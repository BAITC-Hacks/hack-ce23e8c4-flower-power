"""Derive current skills from reviews and apply idempotent simulated completions."""

import csv
import io
import json
import zipfile
from datetime import date
from uuid import uuid4

from career_quest.models import Activity, ActivityStatus, Dataset, Employee, Event, SkillGain

REPEATABLE_EVENT_ID = "EV_036"


def gained_level(current: int, gain: SkillGain) -> int:
    """Apply the dataset's gain/cap without reducing an already higher level."""
    return max(current, min(5, gain.max_level, current + gain.gain))


def current_skills(employee: Employee, dataset: Dataset) -> dict[str, int]:
    """Replay only completions after the last review, in chronological order."""
    levels = dict(employee.skills)
    events = {item.event_id: item for item in dataset.events}
    rows = sorted(dataset.history, key=lambda row: (row.date, row.record_id))
    for row in rows:
        if row.employee_id != employee.employee_id or row.status != ActivityStatus.COMPLETED:
            continue
        if not employee.last_review_date < row.date <= dataset.meta.as_of_date:
            continue
        for gain in events[row.event_id].develops_skills:
            levels[gain.skill_id] = gained_level(levels.get(gain.skill_id, 0), gain)
    return levels


def available_date(employee: Employee, event: Event, dataset: Dataset) -> date | None:
    """Find the next available session, respecting the recurring-club exception."""
    completed_dates = {
        row.date
        for row in dataset.history
        if row.employee_id == employee.employee_id
        and row.event_id == event.event_id
        and row.status == ActivityStatus.COMPLETED
    }
    if completed_dates and event.event_id != REPEATABLE_EVENT_ID:
        return None
    if event.format == "self_paced":
        return dataset.meta.as_of_date
    return next(
        (
            day
            for day in sorted(event.upcoming_sessions)
            if day >= dataset.meta.as_of_date and day not in completed_dates
        ),
        None,
    )


def eligibility_issues(employee: Employee, event: Event, dataset: Dataset) -> list[str]:
    """Explain audience, mandatory-process and prerequisite exclusions."""
    issues = []
    if event.mandatory:
        issues.append("Обязательное мероприятие не участвует в рекомендациях")
    if employee.role not in event.target_roles or employee.grade not in event.target_grades:
        issues.append("Не соответствует роли или грейду")
    levels = current_skills(employee, dataset)
    for skill_id, minimum in event.prerequisites.items():
        if levels.get(skill_id, 0) < minimum:
            issues.append(f"Для участия нужен {skill_id} ≥ {minimum}")
    if available_date(employee, event, dataset) is None:
        issues.append("Уже завершено или нет доступной сессии")
    return issues


def _with_completion(dataset: Dataset, employee: Employee, event: Event, session_date: date) -> Dataset:
    record = Activity(
        record_id=f"CQ_{uuid4().hex}",
        employee_id=employee.employee_id,
        event_id=event.event_id,
        date=session_date,
        due_date=None,
        status=ActivityStatus.COMPLETED,
        completion_pct=100,
        score=None,
        feedback_rating=None,
        assigned_by="self",
    )
    # A same-day review already incorporates that day's history, so fold this one
    # new completion into its baseline; later completions are replayed normally.
    if session_date == employee.last_review_date:
        levels = dict(employee.skills)
        for gain in event.develops_skills:
            levels[gain.skill_id] = gained_level(levels.get(gain.skill_id, 0), gain)
        employee = employee.model_copy(update={"skills": levels})
    history = [
        row
        for row in dataset.history
        if (row.employee_id, row.event_id, row.date) != (employee.employee_id, event.event_id, session_date)
    ]
    return dataset.model_copy(
        update={
            "meta": dataset.meta.model_copy(update={"as_of_date": max(dataset.meta.as_of_date, session_date)}),
            "employees": [employee if item.employee_id == employee.employee_id else item for item in dataset.employees],
            "history": [*history, record],
        }
    )


def complete_event(dataset: Dataset, employee_id: str, event_id: str, session_date: date) -> Dataset:
    """Simulate a completion; replaying the same employee/event/session is a no-op."""
    employee = next((item for item in dataset.employees if item.employee_id == employee_id), None)
    event = next((item for item in dataset.events if item.event_id == event_id), None)
    if employee is None or event is None:
        raise ValueError("Неизвестный сотрудник или событие")
    if any(
        row.employee_id == employee_id
        and row.event_id == event_id
        and row.status == ActivityStatus.COMPLETED
        and (event_id != REPEATABLE_EVENT_ID or row.date == session_date)
        for row in dataset.history
    ):
        return dataset
    issues = eligibility_issues(employee, event, dataset)
    if issues:
        raise ValueError("; ".join(issues))
    if available_date(employee, event, dataset) != session_date:
        raise ValueError("Выберите ближайшую доступную сессию")
    return _with_completion(dataset, employee, event, session_date)


def export_snapshot(dataset: Dataset) -> bytes:
    """Export all four official-schema files without double-counting derived gains."""
    buffer = io.BytesIO()
    history = io.StringIO(newline="")
    writer = csv.DictWriter(history, fieldnames=list(Activity.model_fields))
    writer.writeheader()
    writer.writerows(row.model_dump(mode="json") for row in dataset.history)
    metadata = dataset.meta.model_dump(mode="json")
    documents = {
        "employees.json": {"meta": metadata, "employees": [row.model_dump(mode="json") for row in dataset.employees]},
        "events.json": {"meta": metadata, "events": [row.model_dump(mode="json") for row in dataset.events]},
        "skills.json": {
            "meta": metadata,
            "proficiency_scale": dataset.proficiency_scale,
            "skills": [row.model_dump(mode="json") for row in dataset.skills],
            "role_profiles": [row.model_dump(mode="json") for row in dataset.role_profiles],
        },
    }
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, document in documents.items():
            archive.writestr(filename, json.dumps(document, ensure_ascii=False, indent=2))
        archive.writestr("activity_history.csv", history.getvalue())
    return buffer.getvalue()
