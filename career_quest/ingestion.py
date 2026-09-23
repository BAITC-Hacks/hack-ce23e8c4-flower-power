"""Load official JSON/CSV files atomically and validate cross-file references."""

import csv
import io
from pathlib import Path

from pydantic import BaseModel, ValidationError

from career_quest.models import Activity, Dataset, EmployeeFile, EventFile, SkillFile

CSV_FIELDS = tuple(Activity.model_fields)
MAX_FILE_BYTES = 10 * 1024 * 1024


def _decode(raw: bytes, filename: str) -> str:
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"{filename}: максимальный размер файла — 10 МБ")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{filename}: требуется кодировка UTF-8") from exc


def _read_json[Model: BaseModel](raw: bytes, model: type[Model], filename: str) -> Model:
    try:
        return model.model_validate_json(_decode(raw, filename))
    except ValidationError as exc:
        raise ValueError(f"{filename}: {exc}") from exc


def read_history(raw: bytes) -> list[Activity]:
    """Parse the official CSV header and report the row of a validation error."""
    reader = csv.DictReader(io.StringIO(_decode(raw, "activity_history.csv")), strict=True)
    try:
        if reader.fieldnames is None or set(reader.fieldnames) != set(CSV_FIELDS):
            raise ValueError(f"activity_history.csv: нужны столбцы {','.join(CSV_FIELDS)}")
        if len(reader.fieldnames) != len(CSV_FIELDS):
            raise ValueError("activity_history.csv: повторяющиеся столбцы")
        return [Activity.model_validate(row) for row in reader]
    except (ValidationError, csv.Error) as exc:
        raise ValueError(f"activity_history.csv, строка {reader.line_num}: {exc}") from exc


def _require_unique(values: list[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"Повторяющиеся идентификаторы: {label}")


def _validate_catalog(dataset: Dataset) -> None:
    skills = {item.skill_id for item in dataset.skills}
    for event in dataset.events:
        gained = [gain.skill_id for gain in event.develops_skills]
        _require_unique(gained, f"навыки события {event.event_id}")
        unknown = (set(gained) | set(event.prerequisites)) - skills
        if unknown:
            raise ValueError(f"{event.event_id}: неизвестные навыки {sorted(unknown)}")
    for profile in dataset.role_profiles:
        if set(profile.required_skills) - skills:
            raise ValueError(f"{profile.role}/{profile.grade}: неизвестные навыки")
        if set(profile.critical_skills) - set(profile.required_skills):
            raise ValueError(f"{profile.role}/{profile.grade}: критические навыки отсутствуют в требованиях")


def _validate_employees(dataset: Dataset) -> None:
    skills = {item.skill_id for item in dataset.skills}
    employees = {item.employee_id for item in dataset.employees}
    profiles = {(item.role, item.grade) for item in dataset.role_profiles}
    for employee in dataset.employees:
        if set(employee.skills) - skills:
            raise ValueError(f"{employee.employee_id}: неизвестные навыки {sorted(set(employee.skills) - skills)}")
        if (employee.role, employee.grade) not in profiles:
            raise ValueError(f"{employee.employee_id}: нет требований для роли и грейда")
        if employee.manager_id is not None and employee.manager_id not in employees:
            raise ValueError(f"{employee.employee_id}: неизвестный руководитель {employee.manager_id}")
        goal = employee.career_goal
        if goal is not None and (goal.target_role, goal.target_grade) not in profiles:
            raise ValueError(f"{employee.employee_id}: нет требований для карьерной цели")
        if not employee.hire_date <= employee.last_review_date <= dataset.meta.as_of_date:
            raise ValueError(f"{employee.employee_id}: некорректные даты найма/оценки относительно даты среза")


def validate_dataset(dataset: Dataset) -> Dataset:
    """Reject broken references or identifiers before exposing a snapshot to users."""
    if not dataset.employees or not dataset.skills or not dataset.events or not dataset.role_profiles:
        raise ValueError("Профили, события, навыки и требования не должны быть пустыми")
    _require_unique([item.employee_id for item in dataset.employees], "сотрудники")
    _require_unique([item.event_id for item in dataset.events], "события")
    _require_unique([item.skill_id for item in dataset.skills], "навыки")
    _require_unique([f"{item.role}/{item.grade}" for item in dataset.role_profiles], "роли и грейды")
    _require_unique([item.record_id for item in dataset.history], "история")
    _validate_catalog(dataset)
    _validate_employees(dataset)
    employees = {item.employee_id: item for item in dataset.employees}
    events = {item.event_id for item in dataset.events}
    participation_keys = []
    for row in dataset.history:
        if row.employee_id not in employees or row.event_id not in events:
            raise ValueError(f"{row.record_id}: неизвестный сотрудник/событие {row.employee_id}/{row.event_id}")
        if not employees[row.employee_id].hire_date <= row.date <= dataset.meta.as_of_date:
            raise ValueError(f"{row.record_id}: дата истории вне периода работы/среза")
        participation_keys.append(f"{row.employee_id}/{row.event_id}/{row.date}")
    _require_unique(participation_keys, "участия сотрудника в одной сессии")
    return dataset


def load_dataset(employees: bytes, events: bytes, skills: bytes, history: bytes) -> Dataset:
    """Load all four starter-kit files as one validated snapshot."""
    employee_file = _read_json(employees, EmployeeFile, "employees.json")
    event_file = _read_json(events, EventFile, "events.json")
    skill_file = _read_json(skills, SkillFile, "skills.json")
    if not employee_file.meta == event_file.meta == skill_file.meta:
        raise ValueError("Метаданные и дата среза трёх JSON-файлов должны совпадать")
    return validate_dataset(
        Dataset(
            meta=employee_file.meta,
            employees=employee_file.employees,
            events=event_file.events,
            skills=skill_file.skills,
            role_profiles=skill_file.role_profiles,
            proficiency_scale=skill_file.proficiency_scale,
            history=read_history(history),
        )
    )


def load_directory(directory: Path) -> Dataset:
    """Load the four official filenames from a local directory."""
    return load_dataset(
        (directory / "employees.json").read_bytes(),
        (directory / "events.json").read_bytes(),
        (directory / "skills.json").read_bytes(),
        (directory / "activity_history.csv").read_bytes(),
    )


def merge_profiles(dataset: Dataset, employees: bytes, history: bytes) -> Dataset:
    """Upsert additional jury profiles and history, validating the merged snapshot."""
    incoming = _read_json(employees, EmployeeFile, "employees.json")
    if incoming.meta != dataset.meta:
        raise ValueError("Дополнительные профили должны иметь те же метаданные и дату среза")
    _require_unique([item.employee_id for item in incoming.employees], "дополнительные профили")
    records = read_history(history)
    _require_unique([item.record_id for item in records], "дополнительная история")
    merged_employees = {item.employee_id: item for item in dataset.employees}
    merged_employees.update({item.employee_id: item for item in incoming.employees})
    merged_history = {item.record_id: item for item in dataset.history}
    for row in records:
        if row.record_id in merged_history and row != merged_history[row.record_id]:
            raise ValueError(f"{row.record_id}: идентификатор уже занят другой записью истории")
        merged_history[row.record_id] = row
    return validate_dataset(
        dataset.model_copy(
            update={"employees": list(merged_employees.values()), "history": list(merged_history.values())}
        )
    )
