"""Session snapshot import/export compatible with the shared dataset contracts."""

import csv
import io
import json
import tempfile
import zipfile
from pathlib import Path

from career_quest.access import Viewer, require_hr
from career_quest.data import HISTORY_COLUMNS, Dataset, DatasetError, load_dataset, parse_employees, parse_history

FILENAMES = ("employees.json", "events.json", "skills.json", "activity_history.csv")
MAX_FILE_BYTES = 10 * 1024 * 1024


def _check_size(files: dict[str, bytes]) -> None:
    for name, content in files.items():
        if len(content) > MAX_FILE_BYTES:
            raise DatasetError(f"{name}: максимальный размер файла — 10 МБ")
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DatasetError(f"{name}: требуется кодировка UTF-8") from exc


def import_snapshot(viewer: Viewer, files: dict[str, bytes]) -> Dataset:
    """Validate all four uploaded files before replacing a session's dataset."""
    require_hr(viewer)
    if set(files) != set(FILENAMES):
        raise DatasetError("Загрузите employees.json, events.json, skills.json и activity_history.csv")
    _check_size(files)
    try:
        with tempfile.TemporaryDirectory(prefix="career-quest-import-") as directory:
            for name in FILENAMES:
                (Path(directory) / name).write_bytes(files[name])
            return load_dataset(Path(directory))
    except (ValueError, KeyError, TypeError) as exc:
        raise DatasetError(f"Не удалось загрузить набор: {exc}") from exc


def import_additions(viewer: Viewer, dataset: Dataset, employees: bytes, history: bytes) -> Dataset:
    """Atomically merge jury profiles/history; empty optional files are supported."""
    require_hr(viewer)
    _check_size({"employees.json": employees, "activity_history.csv": history})
    profiles = parse_employees(employees) if employees else []
    records = parse_history(history) if history else []
    if not profiles and not records:
        raise DatasetError("Загрузите хотя бы один профиль или запись истории")
    return dataset.with_additions(profiles, records)


def export_snapshot(viewer: Viewer, dataset: Dataset) -> bytes:
    """Export the complete HR snapshot without recalculating or duplicating gains."""
    require_hr(viewer)
    metadata = {"dataset": "Career Quest", "version": "1.0", "as_of_date": dataset.as_of_date.isoformat()}
    documents = {
        "employees.json": {
            "meta": metadata,
            "employees": [e.model_dump(mode="json", by_alias=True) for e in dataset.employees],
        },
        "events.json": {"meta": metadata, "events": [e.model_dump(mode="json", by_alias=True) for e in dataset.events]},
        "skills.json": {
            "meta": metadata,
            "proficiency_scale": dataset.proficiency_scale,
            "skills": [s.model_dump(mode="json", by_alias=True) for s in dataset.skills],
            "role_profiles": [r.model_dump(mode="json", by_alias=True) for r in dataset.role_profiles],
        },
    }
    history = io.StringIO(newline="")
    writer = csv.DictWriter(history, fieldnames=HISTORY_COLUMNS)
    writer.writeheader()
    writer.writerows(row.model_dump(mode="json", by_alias=True) for row in dataset.history)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, document in documents.items():
            archive.writestr(name, json.dumps(document, ensure_ascii=False, indent=2))
        archive.writestr("activity_history.csv", history.getvalue())
    return buffer.getvalue()
