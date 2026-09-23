"""JSON API and static web client for Career Quest.

Run with ``uv run uvicorn career_quest.api:app``. Every browser session gets its own copy of the
dataset on the server (progress survives page reloads while the server runs). Access rules come from
``career_quest.access``: without ``CQ_*`` variables a labeled demo role switch is available.
"""

import os
import secrets
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from fastapi import Cookie, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from career_quest.access import Viewer, authenticate, can_view_employee, configured_access, require_hr
from career_quest.coach import set_goal, suggest_goal
from career_quest.data import Dataset, DatasetError, load_dataset
from career_quest.explain import explain, llm_configured
from career_quest.labels import (
    BADGE_LABELS,
    EVENT_LABELS,
    FACTOR_LABELS,
    FORMAT_LABELS,
    GRADE_LABELS,
    LEVEL_LABELS,
    ROLE_LABELS,
    SKILL_LABELS,
    STATUS_LABELS,
)
from career_quest.models import CareerGoal, Employee, Grade, Language
from career_quest.quest import BADGES, Quest, build_quest, coverage_gain
from career_quest.scoring import Recommendation, complete_activity, effective_skills, recommend, target_profile
from career_quest.snapshot import FILENAMES, export_snapshot, import_additions, import_snapshot

log = structlog.get_logger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("CQ_DATA_DIR", str(ROOT / "data")))
WEB_DIR = ROOT / "web"
COOKIE = "cq_session"
Role = Literal["employee", "hr"]


@dataclass
class _Session:
    viewer: Viewer | None
    dataset: Dataset


_BASE: dict[str, Dataset] = {}
_SESSIONS: dict[str, _Session] = {}

app = FastAPI(title="Career Quest", docs_url="/api/docs", openapi_url="/api/openapi.json")


class LoginBody(BaseModel):
    """Role and password; the password is ignored in demo mode."""

    role: Role
    password: str = ""


class WishBody(BaseModel):
    """Free-text career wish for the coach."""

    wish: str


class GoalBody(BaseModel):
    """Career goal chosen by the employee."""

    target_role: str
    target_grade: Grade


class EventBody(BaseModel):
    """Event selected on a recommendation card."""

    event_id: str
    language: Language = "ru"


@app.exception_handler(PermissionError)
async def _forbidden(_request: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc) or "Нет доступа"})


@app.exception_handler(DatasetError)
async def _bad_data(_request: Request, exc: DatasetError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _base_dataset() -> Dataset:
    if "base" not in _BASE:
        _BASE["base"] = load_dataset(DATA_DIR)
    return _BASE["base"]


def _session(response: Response, token: str | None) -> _Session:
    if token and token in _SESSIONS:
        return _SESSIONS[token]
    token = secrets.token_urlsafe(24)
    _SESSIONS[token] = _Session(viewer=None, dataset=_base_dataset())
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax")
    return _SESSIONS[token]


def _viewer(session: _Session) -> Viewer:
    if session.viewer is None:
        raise HTTPException(status_code=401, detail="Войдите в систему")
    return session.viewer


def _employee_for(session: _Session, employee_id: str) -> Employee:
    viewer = _viewer(session)
    if not can_view_employee(viewer, employee_id):
        raise PermissionError("Нет доступа к этому профилю")
    try:
        return session.dataset.employee(employee_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Сотрудник не найден") from exc


def _role_label(role: str, grade: str) -> str:
    return f"{ROLE_LABELS.get(role, role)} · {GRADE_LABELS.get(grade, grade)}"


def _skill_name(ds: Dataset, skill_id: str) -> str:
    original = ds.skill(skill_id).name
    return SKILL_LABELS.get(original, original)


SessionCookie = Annotated[str | None, Cookie(alias=COOKIE)]


@app.get("/api/session")
def session_info(response: Response, cq_session: SessionCookie = None) -> dict[str, Any]:
    """Return the current viewer and app mode."""
    session = _session(response, cq_session)
    viewer = session.viewer
    return {
        "demo": not configured_access(),
        "ai": llm_configured(),
        "as_of_date": session.dataset.as_of_date.isoformat(),
        "viewer": None if viewer is None else {"role": viewer.role, "employee_id": viewer.employee_id},
    }


@app.post("/api/session")
def login(body: LoginBody, response: Response, cq_session: SessionCookie = None) -> dict[str, Any]:
    """Open a session: role switch in demo mode, password check otherwise."""
    session = _session(response, cq_session)
    if configured_access():
        viewer = authenticate(body.role, body.password)
        if viewer is None:
            raise HTTPException(status_code=401, detail="Неверные данные для выбранной роли")
    else:
        viewer = Viewer(body.role, None, demo=True)
    session.viewer = viewer
    return {"role": viewer.role, "employee_id": viewer.employee_id}


@app.delete("/api/session")
def logout(response: Response, cq_session: SessionCookie = None) -> dict[str, bool]:
    """Forget the session and its data."""
    if cq_session:
        _SESSIONS.pop(cq_session, None)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/employees")
def employees(response: Response, cq_session: SessionCookie = None) -> list[dict[str, str]]:
    """List profiles the viewer may open."""
    session = _session(response, cq_session)
    viewer = _viewer(session)
    return [
        {"id": e.employee_id, "name": e.full_name, "role": _role_label(e.role, e.grade), "department": e.department}
        for e in session.dataset.employees
        if can_view_employee(viewer, e.employee_id)
    ]


@app.get("/api/employees/{employee_id}")
def profile(employee_id: str, response: Response, cq_session: SessionCookie = None) -> dict[str, Any]:
    """Return everything the employee page shows."""
    session = _session(response, cq_session)
    employee = _employee_for(session, employee_id)
    ds = session.dataset
    quest = build_quest(ds, employee_id)
    return {
        "employee": _employee_payload(employee),
        "quest": _quest_payload(ds, quest),
        "recommendations": [_recommendation_payload(ds, employee, rec, quest) for rec in recommend(ds, employee_id)],
        "skills": _skills_payload(ds, employee),
        "history": [
            {
                "date": r.session_date.isoformat(),
                "title": _event_title(ds, r.event_id),
                "status": r.status,
                "status_label": STATUS_LABELS[r.status],
                "completion_pct": r.completion_pct,
            }
            for r in reversed(ds.history_for(employee_id))
        ],
        "levels": LEVEL_LABELS,
    }


def _employee_payload(employee: Employee) -> dict[str, Any]:
    goal = employee.career_goal
    return {
        "id": employee.employee_id,
        "name": employee.full_name,
        "department": employee.department,
        "role_label": _role_label(employee.role, employee.grade),
        "tenure_months": employee.tenure_months,
        "language": employee.preferred_language,
        "goal": None if goal is None else _role_label(goal.target_role, goal.target_grade),
    }


def _quest_payload(ds: Dataset, quest: Quest | None) -> dict[str, Any] | None:
    if quest is None:
        return None
    return {
        "target": _role_label(quest.target_role, quest.target_grade),
        "coverage": quest.coverage,
        "growth_points": quest.growth_points,
        "critical_left": sum(not m.done for m in quest.milestones if m.critical),
        "milestones": [
            {"name": _skill_name(ds, m.skill_id), "current": m.current, "required": m.required, "critical": m.critical}
            for m in quest.milestones
            if not m.done
        ],
        "badges": [
            {"icon": icon, "title": title, "description": text, "earned": code in quest.badges}
            for code, (icon, title, text) in BADGE_LABELS.items()
        ],
        "badges_total": len(BADGES),
    }


def _event_title(ds: Dataset, event_id: str) -> str:
    title = ds.event(event_id).title
    return EVENT_LABELS.get(title, title)


def _recommendation_payload(
    ds: Dataset, employee: Employee, rec: Recommendation, quest: Quest | None
) -> dict[str, Any]:
    event = ds.event(rec.event_id)
    done_today = any(
        r.event_id == rec.event_id and r.status == "completed" and r.session_date == ds.as_of_date
        for r in ds.history_for(employee.employee_id)
    )
    return {
        "event_id": rec.event_id,
        "title": _event_title(ds, rec.event_id),
        "original_title": event.title,
        "description": event.description,
        "format": FORMAT_LABELS[event.event_format],
        "hours": event.duration_hours,
        "next_session": rec.next_session.isoformat() if rec.next_session else None,
        "score": rec.score,
        "gain": coverage_gain(quest, rec.skill_changes) if quest else 0.0,
        "skill_changes": [
            {"name": _skill_name(ds, key), "before": before, "after": after}
            for key, (before, after) in rec.skill_changes.items()
        ],
        "factors": [
            {"code": f.code, "weight": f.weight, "detail": f.detail, "label": FACTOR_LABELS.get(f.code, f.code)}
            for f in rec.factors
        ],
        "completed_today": done_today,
    }


def _skills_payload(ds: Dataset, employee: Employee) -> list[dict[str, Any]]:
    levels = effective_skills(ds, employee.employee_id)
    target = target_profile(ds, employee.employee_id)
    required = target.required_skills if target else {}
    ids = list(required) + [key for key in levels if key not in required]
    return [
        {
            "name": _skill_name(ds, key),
            "original": ds.skill(key).name,
            "current": levels.get(key, 0),
            "required": required.get(key),
            "critical": bool(target and key in target.critical_skills),
        }
        for key in ids
    ]


@app.post("/api/employees/{employee_id}/complete")
def complete(employee_id: str, body: EventBody, response: Response, cq_session: SessionCookie = None) -> dict[str, Any]:
    """Mark a recommended activity completed and report what changed."""
    session = _session(response, cq_session)
    _employee_for(session, employee_id)
    ds = session.dataset
    before, old_quest = effective_skills(ds, employee_id), build_quest(ds, employee_id)
    try:
        updated = complete_activity(ds, employee_id, body.event_id)
    except DatasetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.dataset = updated
    after, new_quest = effective_skills(updated, employee_id), build_quest(updated, employee_id)
    earned = [c for c in (new_quest.badges if new_quest else []) if not old_quest or c not in old_quest.badges]
    return {
        "changes": [
            {"name": _skill_name(ds, key), "before": before.get(key, 0), "after": value}
            for key, value in after.items()
            if value != before.get(key, 0)
        ],
        "new_badges": [{"icon": BADGE_LABELS[c][0], "title": BADGE_LABELS[c][1]} for c in earned],
    }


@app.post("/api/employees/{employee_id}/explain")
def explanation(
    employee_id: str, body: EventBody, response: Response, cq_session: SessionCookie = None
) -> dict[str, str]:
    """Explain one recommendation with the LLM, or with the computed factors offline."""
    session = _session(response, cq_session)
    employee = _employee_for(session, employee_id)
    rec = next((r for r in recommend(session.dataset, employee_id) if r.event_id == body.event_id), None)
    if rec is None:
        raise HTTPException(status_code=404, detail="Рекомендация не найдена")
    return {"text": explain(rec, employee, body.language)}


@app.post("/api/employees/{employee_id}/coach")
def coach(employee_id: str, body: WishBody, response: Response, cq_session: SessionCookie = None) -> dict[str, Any]:
    """Turn a free-text wish into a catalog career goal."""
    session = _session(response, cq_session)
    _employee_for(session, employee_id)
    suggestion = suggest_goal(session.dataset, employee_id, body.wish, "ru")
    if suggestion is None:
        return {"suggestion": None}
    return {
        "suggestion": {
            **suggestion.model_dump(),
            "label": _role_label(suggestion.target_role, suggestion.target_grade),
        }
    }


@app.post("/api/employees/{employee_id}/goal")
def goal(employee_id: str, body: GoalBody, response: Response, cq_session: SessionCookie = None) -> dict[str, str]:
    """Set the employee's career goal; recommendations are recomputed on the next read."""
    session = _session(response, cq_session)
    _employee_for(session, employee_id)
    try:
        session.dataset = set_goal(session.dataset, employee_id, CareerGoal(**body.model_dump()))
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="Такой роли нет в справочнике") from exc
    return {"goal": _role_label(body.target_role, body.target_grade)}


@app.get("/api/hr")
def hr_report(response: Response, cq_session: SessionCookie = None) -> dict[str, Any]:
    """Aggregates for HR: lagging skills, people without a next step, participation per activity."""
    session = _session(response, cq_session)
    require_hr(_viewer(session))
    ds = session.dataset
    deficits: Counter[str] = Counter()
    critical: Counter[str] = Counter()
    no_steps = []
    for employee in ds.employees:
        levels = effective_skills(ds, employee.employee_id)
        target = target_profile(ds, employee.employee_id)
        for key, required in (target.required_skills if target else {}).items():
            if levels.get(key, 0) < required:
                deficits[key] += 1
                critical[key] += bool(target and key in target.critical_skills)
        if not recommend(ds, employee.employee_id):
            no_steps.append({"id": employee.employee_id, "name": employee.full_name, "role": employee.role})
    return {
        "totals": {
            "employees": len(ds.employees),
            "events": sum(not e.mandatory for e in ds.events),
            "records": len(ds.history),
        },
        "deficits": [
            {"name": _skill_name(ds, key), "count": count, "critical": critical[key]}
            for key, count in deficits.most_common(15)
        ],
        "no_steps": no_steps,
        "participation": _participation(ds),
    }


def _participation(ds: Dataset) -> list[dict[str, Any]]:
    by_event: dict[str, list[str]] = {}
    for record in ds.history:
        by_event.setdefault(record.event_id, []).append(record.status)
    people: dict[str, set[str]] = {}
    for record in ds.history:
        people.setdefault(record.event_id, set()).add(record.employee_id)
    rows = []
    for event in ds.events:
        if event.mandatory:
            continue
        statuses = by_event.get(event.event_id, [])
        completed = statuses.count("completed")
        rows.append(
            {
                "title": _event_title(ds, event.event_id),
                "participants": len(people.get(event.event_id, set())),
                "records": len(statuses),
                "completed": completed,
                "rate": completed / len(statuses) if statuses else 0.0,
            }
        )
    return sorted(rows, key=lambda row: -row["participants"])


@app.post("/api/data/additions")
async def add_data(
    response: Response,
    employees_file: Annotated[UploadFile | None, File()] = None,
    history_file: Annotated[UploadFile | None, File()] = None,
    cq_session: SessionCookie = None,
) -> dict[str, Any]:
    """Merge jury profiles and history into this session's dataset."""
    session = _session(response, cq_session)
    viewer = _viewer(session)
    raw_employees = await employees_file.read() if employees_file else b""
    raw_history = await history_file.read() if history_file else b""
    session.dataset = import_additions(viewer, session.dataset, raw_employees, raw_history)
    return {"employees": len(session.dataset.employees), "records": len(session.dataset.history)}


@app.post("/api/data/replace")
async def replace_data(
    response: Response, files: Annotated[list[UploadFile], File()], cq_session: SessionCookie = None
) -> dict[str, Any]:
    """Replace this session's dataset with four uploaded files."""
    session = _session(response, cq_session)
    viewer = _viewer(session)
    uploads = {file.filename or "": await file.read() for file in files}
    session.dataset = import_snapshot(viewer, {name: uploads[name] for name in FILENAMES if name in uploads})
    return {"employees": len(session.dataset.employees), "records": len(session.dataset.history)}


@app.get("/api/data/export")
def export_data(response: Response, cq_session: SessionCookie = None) -> Response:
    """Download this session's dataset (with simulated progress) as a ZIP of the four files."""
    session = _session(response, cq_session)
    payload = export_snapshot(_viewer(session), session.dataset)
    headers = {"Content-Disposition": 'attachment; filename="career-quest-snapshot.zip"'}
    return Response(content=payload, media_type="application/zip", headers=headers)


@app.get("/api/health")
def health() -> dict[str, str]:
    """Liveness probe for Docker."""
    return {"status": "ok"}


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
