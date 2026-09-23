import datetime as dt
from pathlib import Path

import pytest

from career_quest.data import Dataset, load_dataset, parse_history
from career_quest.models import CareerGoal, Employee, Grade
from career_quest.scoring import effective_skills, target_profile

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HEADER = "record_id,employee_id,event_id,date,due_date,status,completion_pct,score,feedback_rating,assigned_by\n"
REVIEW_DATE = dt.date(2026, 6, 1)


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATA_DIR)


def make_employee(
    ds: Dataset,
    *,
    skills: dict[str, int],
    grade: Grade = "Middle",
    goal: CareerGoal | None = None,
) -> Employee:
    """Hand-built Backend Engineer reviewed on REVIEW_DATE, based on a real profile."""
    return ds.employee("E0028").model_copy(
        update={
            "employee_id": "T0001",
            "role": "Backend Engineer",
            "grade": grade,
            "career_goal": goal,
            "skills": skills,
            "last_review_date": REVIEW_DATE,
        }
    )


def with_history(ds: Dataset, employee: Employee, rows: list[tuple[str, str, str]]) -> Dataset:
    """Add the employee and history rows given as (event_id, date, status)."""
    csv_rows = "".join(
        f"RT{i:04d},{employee.employee_id},{event_id},{date},,{status},100,,,self\n"
        for i, (event_id, date, status) in enumerate(rows)
    )
    return ds.with_additions([employee], parse_history(HEADER + csv_rows))


def test_only_completions_after_review_are_applied(dataset: Dataset) -> None:
    employee = make_employee(dataset, skills={"SK_PYTHON": 1, "SK_CLOUD": 1, "SK_CONTAINERS": 1, "SK_APP_SECURITY": 1})
    ds = with_history(
        dataset,
        employee,
        [
            ("EV_012", "2026-05-01", "completed"),  # before review: already in the reviewed level
            ("EV_012", REVIEW_DATE.isoformat(), "completed"),  # review day: already assessed
            ("EV_009", "2026-07-01", "dropped"),
            ("EV_010", "2026-07-01", "no_show"),
            ("EV_011", "2026-07-15", "completed"),
        ],
    )

    skills = effective_skills(ds, "T0001")

    assert skills["SK_PYTHON"] == 1
    assert skills["SK_CLOUD"] == 1
    assert skills["SK_CONTAINERS"] == 1
    assert skills["SK_APP_SECURITY"] == 2


def test_gain_is_capped_and_never_lowers_a_level(dataset: Dataset) -> None:
    # EV_005: SK_SYSTEM_DESIGN +1 up to 3, SK_API_DESIGN +1 up to 3.
    employee = make_employee(dataset, skills={"SK_SYSTEM_DESIGN": 3, "SK_API_DESIGN": 4})
    ds = with_history(dataset, employee, [("EV_005", "2026-07-01", "completed")])

    skills = effective_skills(ds, "T0001")

    assert skills["SK_SYSTEM_DESIGN"] == 3
    assert skills["SK_API_DESIGN"] == 4


def test_missing_skill_starts_at_zero(dataset: Dataset) -> None:
    employee = make_employee(dataset, skills={})
    ds = with_history(dataset, employee, [("EV_011", "2026-07-01", "completed")])

    assert effective_skills(ds, "T0001") == {"SK_APP_SECURITY": 1}


@pytest.mark.parametrize(
    ("ev_005_date", "ev_006_date", "expected"),
    [
        ("2026-07-01", "2026-08-01", 4),  # 2 → 3 (EV_005, cap 3) → 4 (EV_006, cap 5)
        ("2026-08-01", "2026-07-01", 3),  # 2 → 3 (EV_006) → 3 (EV_005 cap reached)
    ],
)
def test_gains_are_applied_in_date_order(dataset: Dataset, ev_005_date: str, ev_006_date: str, expected: int) -> None:
    employee = make_employee(dataset, skills={"SK_SYSTEM_DESIGN": 2})
    ds = with_history(dataset, employee, [("EV_006", ev_006_date, "completed"), ("EV_005", ev_005_date, "completed")])

    assert effective_skills(ds, "T0001")["SK_SYSTEM_DESIGN"] == expected


def test_completions_after_snapshot_date_are_ignored(dataset: Dataset) -> None:
    employee = make_employee(dataset, skills={"SK_APP_SECURITY": 1})
    after_snapshot = (dataset.as_of_date + dt.timedelta(days=1)).isoformat()
    ds = with_history(dataset, employee, [("EV_011", after_snapshot, "completed")])

    assert effective_skills(ds, "T0001")["SK_APP_SECURITY"] == 1


def test_target_is_career_goal_even_in_another_role(dataset: Dataset) -> None:
    goal = CareerGoal(target_role="Data Analyst", target_grade="Senior")
    ds = dataset.with_additions([make_employee(dataset, skills={}, goal=goal)], [])

    profile = target_profile(ds, "T0001")

    assert profile is not None
    assert (profile.role, profile.grade) == ("Data Analyst", "Senior")


def test_target_without_goal_is_next_grade(dataset: Dataset) -> None:
    ds = dataset.with_additions([make_employee(dataset, skills={}, grade="Middle")], [])

    profile = target_profile(ds, "T0001")

    assert profile is not None
    assert (profile.role, profile.grade) == ("Backend Engineer", "Senior")


def test_lead_without_goal_targets_current_profile(dataset: Dataset) -> None:
    ds = dataset.with_additions([make_employee(dataset, skills={}, grade="Lead")], [])

    profile = target_profile(ds, "T0001")

    assert profile is not None
    assert (profile.role, profile.grade) == ("Backend Engineer", "Lead")


def test_unknown_goal_falls_back_to_next_grade(dataset: Dataset) -> None:
    goal = CareerGoal(target_role="Astronaut", target_grade="Lead")
    ds = dataset.with_additions([make_employee(dataset, skills={}, grade="Junior", goal=goal)], [])

    profile = target_profile(ds, "T0001")

    assert profile is not None
    assert (profile.role, profile.grade) == ("Backend Engineer", "Middle")
