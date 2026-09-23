import json
from pathlib import Path

import pytest

from career_quest.data import Dataset, DatasetError, load_dataset, parse_employees, parse_history
from career_quest.models import next_grade

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HEADER = "record_id,employee_id,event_id,date,due_date,status,completion_pct,score,feedback_rating,assigned_by\n"


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA_DIR)


def test_starter_kit_loads_with_documented_sizes(dataset: Dataset) -> None:
    assert len(dataset.skills) == 60
    assert len(dataset.role_profiles) == 32
    assert len(dataset.employees) == 200
    assert len(dataset.events) == 40
    assert len(dataset.history) == 2743
    assert dataset.as_of_date.isoformat() == "2026-10-01"


def test_missing_skill_counts_as_zero(dataset: Dataset) -> None:
    employee = dataset.employee("E0001")
    assert employee.level("SK_DOES_NOT_EXIST") == 0


def test_empty_csv_cells_become_none() -> None:
    records = parse_history(HEADER + "R1,E0001,EV_004,2026-05-01,,completed,100,,,hr\n")
    assert records[0].due_date is None
    assert records[0].score is None
    assert records[0].feedback_rating is None


def test_missing_history_column_is_rejected() -> None:
    with pytest.raises(DatasetError, match="missing columns: assigned_by"):
        parse_history(HEADER.replace(",assigned_by", "") + "R1,E0001,EV_004,2026-05-01,,completed,100,,\n")


def test_additions_merge_new_profile_and_history(dataset: Dataset) -> None:
    base = dataset.employee("E0028").model_dump(mode="json")
    new_profile = parse_employees(f'{{"employees": [{json.dumps(base | {"employee_id": "J0001"})}]}}')
    new_history = parse_history(HEADER + "RJ0001,J0001,EV_004,2026-05-01,,completed,100,,,hr\n")

    merged = dataset.with_additions(new_profile, new_history)

    assert merged.employee("J0001").role == base["role"]
    assert [r.record_id for r in merged.history_for("J0001")] == ["RJ0001"]
    assert len(merged.employees) == len(dataset.employees) + 1


def test_history_for_unknown_employee_is_rejected(dataset: Dataset) -> None:
    orphan = parse_history(HEADER + "RX,NOBODY,EV_004,2026-05-01,,completed,100,,,hr\n")
    with pytest.raises(DatasetError, match="unknown employee NOBODY"):
        dataset.with_additions([], orphan)


def test_next_grade_order() -> None:
    assert next_grade("Middle") == "Senior"
    assert next_grade("Lead") is None
