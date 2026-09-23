"""Loading, merging and validating the Career Quest dataset.

The jury uploads extra employee profiles and history records in the same format as the
starter kit, so every loader accepts raw file contents as well as paths.
"""

import csv
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from career_quest.models import ActivityRecord, Employee, Event, Grade, RoleProfile, Skill

log = structlog.get_logger(__name__)

HISTORY_COLUMNS = (
    "record_id",
    "employee_id",
    "event_id",
    "date",
    "due_date",
    "status",
    "completion_pct",
    "score",
    "feedback_rating",
    "assigned_by",
)
MAX_REPORTED_PROBLEMS = 20


class DatasetError(ValueError):
    """Raised when input files are malformed or reference unknown entities."""


class Dataset:
    """In-memory dataset with id-based lookups.

    Attributes:
        as_of_date: Snapshot date that the dataset treats as "today".
        proficiency_scale: Meaning of skill levels 0–5.
        skills: Skill catalog.
        role_profiles: Requirements per (role, grade).
        employees: Employee profiles.
        events: Development activities.
        history: Participation log sorted by date, employee_id, event_id.
    """

    def __init__(
        self,
        *,
        as_of_date: dt.date,
        proficiency_scale: dict[int, str],
        skills: list[Skill],
        role_profiles: list[RoleProfile],
        employees: list[Employee],
        events: list[Event],
        history: list[ActivityRecord],
    ) -> None:
        """Build lookup indexes and validate cross-file references.

        Raises:
            DatasetError: If any reference points to an unknown entity.
        """
        self.as_of_date = as_of_date
        self.proficiency_scale = proficiency_scale
        self.skills = skills
        self.role_profiles = role_profiles
        self.employees = employees
        self.events = events
        self.history = sorted(history, key=lambda r: (r.session_date, r.employee_id, r.event_id))

        self._skills = {s.skill_id: s for s in skills}
        self._profiles = {(p.role, p.grade): p for p in role_profiles}
        self._employees = {e.employee_id: e for e in employees}
        self._events = {e.event_id: e for e in events}
        self._history_by_employee: dict[str, list[ActivityRecord]] = {}
        for record in self.history:
            self._history_by_employee.setdefault(record.employee_id, []).append(record)

        _raise_if_problems(self._reference_problems())

    def skill(self, skill_id: str) -> Skill:
        """Return a skill by id."""
        return self._skills[skill_id]

    def employee(self, employee_id: str) -> Employee:
        """Return an employee by id."""
        return self._employees[employee_id]

    def event(self, event_id: str) -> Event:
        """Return an event by id."""
        return self._events[event_id]

    def role_profile(self, role: str, grade: Grade) -> RoleProfile:
        """Return the requirements for a role at a grade."""
        return self._profiles[(role, grade)]

    def history_for(self, employee_id: str) -> list[ActivityRecord]:
        """Return one employee's participation records in chronological order."""
        return list(self._history_by_employee.get(employee_id, []))

    def with_additions(self, employees: list[Employee], history: list[ActivityRecord]) -> "Dataset":
        """Return a new dataset with extra profiles and history merged in.

        Profiles and records whose ids already exist replace the existing ones.

        Args:
            employees: Additional or updated employee profiles.
            history: Additional or updated history records.

        Returns:
            A new, validated ``Dataset``.

        Raises:
            DatasetError: If the merged data contains broken references.
        """
        merged_employees = {e.employee_id: e for e in self.employees}
        merged_history = {r.record_id: r for r in self.history}
        replaced = sum(e.employee_id in merged_employees for e in employees)
        merged_employees.update({e.employee_id: e for e in employees})
        merged_history.update({r.record_id: r for r in history})
        log.info("dataset_merged", added_employees=len(employees), replaced_employees=replaced, records=len(history))
        return Dataset(
            as_of_date=self.as_of_date,
            proficiency_scale=self.proficiency_scale,
            skills=self.skills,
            role_profiles=self.role_profiles,
            employees=list(merged_employees.values()),
            events=self.events,
            history=list(merged_history.values()),
        )

    def _reference_problems(self) -> list[str]:
        problems: list[str] = []
        for employee in self.employees:
            if (employee.role, employee.grade) not in self._profiles:
                problems.append(f"{employee.employee_id}: unknown role/grade {employee.role}/{employee.grade}")
            problems.extend(
                f"{employee.employee_id}: unknown skill {skill_id}"
                for skill_id in employee.skills
                if skill_id not in self._skills
            )
        for record in self.history:
            if record.employee_id not in self._employees:
                problems.append(f"{record.record_id}: unknown employee {record.employee_id}")
            if record.event_id not in self._events:
                problems.append(f"{record.record_id}: unknown event {record.event_id}")
        return problems


def _raise_if_problems(problems: list[str]) -> None:
    if not problems:
        return
    shown = "; ".join(problems[:MAX_REPORTED_PROBLEMS])
    more = len(problems) - MAX_REPORTED_PROBLEMS
    suffix = f" (and {more} more)" if more > 0 else ""
    raise DatasetError(f"{len(problems)} reference problem(s): {shown}{suffix}")


def _decode(raw: str | bytes) -> str:
    return raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw


def _json_list(raw: str | bytes, key: str) -> list[Any]:
    """Parse a dataset JSON file and return the list stored under ``key``."""
    try:
        payload = json.loads(_decode(raw))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"invalid JSON: {exc}") from exc
    items = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise DatasetError(f"expected an object with a '{key}' list")
    return items


def parse_employees(raw: str | bytes) -> list[Employee]:
    """Parse the contents of an ``employees.json`` file.

    Args:
        raw: File contents in the starter-kit format ``{"meta": ..., "employees": [...]}``.

    Returns:
        Validated employee profiles.

    Raises:
        DatasetError: If the file is not valid JSON or a profile fails validation.
    """
    try:
        return [Employee.model_validate(item) for item in _json_list(raw, "employees")]
    except ValidationError as exc:
        raise DatasetError(f"invalid employee profile: {exc}") from exc


def parse_history(raw: str | bytes) -> list[ActivityRecord]:
    """Parse the contents of an ``activity_history.csv`` file.

    Args:
        raw: CSV contents with the starter-kit header.

    Returns:
        Validated history records.

    Raises:
        DatasetError: If columns are missing or a row fails validation.
    """
    reader = csv.DictReader(io.StringIO(_decode(raw)))
    missing = [c for c in HISTORY_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise DatasetError(f"history CSV is missing columns: {', '.join(missing)}")
    records: list[ActivityRecord] = []
    for line_number, row in enumerate(reader, start=2):
        try:
            records.append(ActivityRecord.model_validate(row))
        except ValidationError as exc:
            raise DatasetError(f"history CSV line {line_number}: {exc}") from exc
    return records


def load_dataset(data_dir: Path) -> Dataset:
    """Load the four starter-kit files from a directory.

    Args:
        data_dir: Directory containing ``skills.json``, ``employees.json``, ``events.json``
            and ``activity_history.csv``.

    Returns:
        A validated ``Dataset``.

    Raises:
        DatasetError: If a file is malformed or references are broken.
    """
    skills_payload = json.loads((data_dir / "skills.json").read_text(encoding="utf-8"))
    try:
        skills = [Skill.model_validate(item) for item in skills_payload["skills"]]
        profiles = [RoleProfile.model_validate(item) for item in skills_payload["role_profiles"]]
        events = [Event.model_validate(item) for item in _json_list((data_dir / "events.json").read_bytes(), "events")]
    except (ValidationError, KeyError) as exc:
        raise DatasetError(f"invalid catalog file: {exc}") from exc
    dataset = Dataset(
        as_of_date=dt.date.fromisoformat(skills_payload["meta"]["as_of_date"]),
        proficiency_scale={int(level): text for level, text in skills_payload["proficiency_scale"].items()},
        skills=skills,
        role_profiles=profiles,
        employees=parse_employees((data_dir / "employees.json").read_bytes()),
        events=events,
        history=parse_history((data_dir / "activity_history.csv").read_bytes()),
    )
    log.info("dataset_loaded", employees=len(dataset.employees), events=len(events), records=len(dataset.history))
    return dataset
