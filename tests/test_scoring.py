import datetime as dt
from pathlib import Path

import pytest

from career_quest.data import Dataset, DatasetError, load_dataset, parse_history
from career_quest.models import CareerGoal, Employee, Grade
from career_quest.scoring import (
    MIN_FACTORS,
    RECURRING_EVENT_ID,
    Recommendation,
    complete_activity,
    effective_skills,
    recommend,
    target_profile,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
HEADER = "record_id,employee_id,event_id,date,due_date,status,completion_pct,score,feedback_rating,assigned_by\n"
REVIEW_DATE = dt.date(2026, 6, 1)
# Backend Engineer Senior requirements; trap profiles start from them and open specific gaps.
SENIOR_BACKEND = {
    "SK_PYTHON": 4,
    "SK_SQL": 4,
    "SK_API_DESIGN": 4,
    "SK_SYSTEM_DESIGN": 4,
    "SK_CLOUD": 3,
    "SK_CONTAINERS": 3,
    "SK_CICD": 3,
    "SK_APP_SECURITY": 3,
    "SK_OBSERVABILITY": 3,
    "SK_COMMUNICATION": 3,
    "SK_TEAMWORK": 3,
    "SK_PROBLEM_SOLVING": 4,
    "SK_MENTORING": 3,
    "SK_LEADERSHIP": 2,
    "SK_STAKEHOLDER_MGMT": 2,
    "SK_PUBLIC_SPEAKING": 2,
}


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


def gap_skills(rec: Recommendation) -> set[str]:
    """Skills named in the gap factors of a recommendation."""
    return {
        part.split()[0]
        for factor in rec.factors
        if factor.code in {"critical_gap", "required_gap"}
        for part in factor.detail.split("; ")
    }


def test_trap_skipped_public_speaking_loses_to_critical_system_design(dataset: Dataset) -> None:
    # Brief's example: Public Speaking is the lowest skill, but three such activities were skipped
    # and System Design is critical for Senior.
    skills = {**SENIOR_BACKEND, "SK_SYSTEM_DESIGN": 2, "SK_PUBLIC_SPEAKING": 0}
    employee = make_employee(dataset, skills=skills)
    skipped = [
        ("EV_036", "2026-02-11", "no_show"),
        ("EV_036", "2026-03-11", "no_show"),
        ("EV_036", "2026-04-08", "dropped"),
    ]
    ds = with_history(dataset, employee, skipped)
    assert min(skills, key=skills.__getitem__) == "SK_PUBLIC_SPEAKING"

    recs = recommend(ds, "T0001")

    assert recs
    assert "SK_SYSTEM_DESIGN" in recs[0].skill_changes
    assert any(f.code == "critical_gap" for f in recs[0].factors)
    assert recs[0].event_id != RECURRING_EVENT_ID


def test_skipped_activities_lower_the_score(dataset: Dataset) -> None:
    employee = make_employee(dataset, skills={**SENIOR_BACKEND, "SK_PUBLIC_SPEAKING": 0})
    clean = with_history(dataset, employee, [])
    skipped = with_history(dataset, employee, [("EV_036", "2026-03-11", "no_show")])

    def club_score(ds: Dataset) -> float:
        return next(rec.score for rec in recommend(ds, "T0001", limit=40) if rec.event_id == RECURRING_EVENT_ID)

    assert club_score(skipped) < club_score(clean)


def test_trap_goal_in_another_role_beats_current_role_gaps(dataset: Dataset) -> None:
    # Lowest skill is SK_CLOUD from the current role; the goal Data Analyst Senior needs A/B testing and statistics.
    goal = CareerGoal(target_role="Data Analyst", target_grade="Senior")
    skills = {**SENIOR_BACKEND, "SK_CLOUD": 0, "SK_STATISTICS": 2, "SK_AB_TESTING": 1}
    ds = dataset.with_additions([make_employee(dataset, skills=skills, goal=goal)], [])

    recs = recommend(ds, "T0001")

    assert recs
    assert {"SK_AB_TESTING", "SK_STATISTICS"} & set(recs[0].skill_changes)
    assert {f.code for f in recs[0].factors} >= {"critical_gap", "goal_alignment"}
    assert all("SK_CLOUD" not in rec.skill_changes for rec in recs)


def test_trap_stale_review_does_not_target_an_already_raised_skill(dataset: Dataset) -> None:
    # Reviewed Public Speaking is 0, but two club sessions after the review raised it to the Senior requirement.
    skills = {**SENIOR_BACKEND, "SK_SYSTEM_DESIGN": 3, "SK_PUBLIC_SPEAKING": 0}
    employee = make_employee(dataset, skills=skills)
    ds = with_history(
        dataset,
        employee,
        [(RECURRING_EVENT_ID, "2026-07-08", "completed"), (RECURRING_EVENT_ID, "2026-08-12", "completed")],
    )

    recs = recommend(ds, "T0001")

    assert effective_skills(ds, "T0001")["SK_PUBLIC_SPEAKING"] == 2
    assert recs
    assert all("SK_PUBLIC_SPEAKING" not in gap_skills(rec) for rec in recs)
    assert "SK_SYSTEM_DESIGN" in gap_skills(recs[0])


def test_event_that_closes_no_gap_is_not_recommended(dataset: Dataset) -> None:
    # Every Senior requirement is met: nothing moves the employee towards the target.
    ds = dataset.with_additions([make_employee(dataset, skills=SENIOR_BACKEND)], [])

    assert recommend(ds, "T0001") == []


def test_trap_unmet_prerequisite_is_not_recommended(dataset: Dataset) -> None:
    # EV_006 and EV_007 close the System Design gap best but need level 2; EV_005 has no prerequisites.
    skills = {**SENIOR_BACKEND, "SK_SYSTEM_DESIGN": 1}
    ds = dataset.with_additions([make_employee(dataset, skills=skills)], [])

    recs = recommend(ds, "T0001", limit=40)

    assert recs[0].event_id == "EV_005"
    assert not {"EV_006", "EV_007"} & {rec.event_id for rec in recs}


def test_recurring_club_is_recommended_again_after_completion(dataset: Dataset) -> None:
    # One session raised Public Speaking 0 → 1; Senior still requires 2.
    employee = make_employee(dataset, skills={**SENIOR_BACKEND, "SK_PUBLIC_SPEAKING": 0})
    ds = with_history(dataset, employee, [(RECURRING_EVENT_ID, "2026-07-08", "completed")])

    assert RECURRING_EVENT_ID in {rec.event_id for rec in recommend(ds, "T0001", limit=40)}


def test_recommendations_for_all_employees_follow_the_rules(dataset: Dataset) -> None:
    mandatory = {event.event_id for event in dataset.events if event.mandatory}

    for employee in dataset.employees:
        recs = recommend(dataset, employee.employee_id)
        completed = {r.event_id for r in dataset.history_for(employee.employee_id) if r.status == "completed"}

        assert len(recs) <= 3
        assert [rec.score for rec in recs] == sorted((rec.score for rec in recs), reverse=True)
        for rec in recs:
            assert rec.event_id not in mandatory
            assert rec.event_id not in completed - {RECURRING_EVENT_ID}
            assert len(rec.factors) >= MIN_FACTORS
            assert rec.score > 0
            assert {f.code for f in rec.factors} & {"critical_gap", "required_gap"}
            assert all(after > current for current, after in rec.skill_changes.values())
            assert all(session >= dataset.as_of_date for session in [rec.next_session] if session is not None)


def test_complete_activity_raises_level_and_updates_recommendations(dataset: Dataset) -> None:
    skills = {**SENIOR_BACKEND, "SK_SYSTEM_DESIGN": 1}
    ds = dataset.with_additions([make_employee(dataset, skills=skills)], [])

    updated = complete_activity(ds, "T0001", "EV_005")

    assert effective_skills(updated, "T0001")["SK_SYSTEM_DESIGN"] == 2
    record = updated.history_for("T0001")[-1]
    assert (record.event_id, record.status, record.session_date) == ("EV_005", "completed", dataset.as_of_date)
    rec_ids = {rec.event_id for rec in recommend(updated, "T0001", limit=40)}
    assert "EV_005" not in rec_ids
    assert {"EV_006", "EV_007"} & rec_ids  # prerequisite SK_SYSTEM_DESIGN 2 is now met
    assert effective_skills(ds, "T0001")["SK_SYSTEM_DESIGN"] == 1  # the original dataset is unchanged


def test_recurring_club_counts_each_session_once(dataset: Dataset) -> None:
    # One earlier session after the review and one marked today: two gains. A second click today is rejected.
    ds = with_history(dataset, make_employee(dataset, skills={}), [(RECURRING_EVENT_ID, "2026-07-08", "completed")])

    updated = complete_activity(ds, "T0001", RECURRING_EVENT_ID)

    assert effective_skills(updated, "T0001")["SK_PUBLIC_SPEAKING"] == 2
    with pytest.raises(DatasetError, match="on 2026-10-01"):
        complete_activity(updated, "T0001", RECURRING_EVENT_ID)


def test_duplicate_completion_rows_on_one_day_count_once(dataset: Dataset) -> None:
    employee = make_employee(dataset, skills={})
    rows = [(RECURRING_EVENT_ID, "2026-07-08", "completed"), (RECURRING_EVENT_ID, "2026-07-08", "completed")]

    assert effective_skills(with_history(dataset, employee, rows), "T0001")["SK_PUBLIC_SPEAKING"] == 1


def test_completion_counts_when_review_is_dated_today(dataset: Dataset) -> None:
    employee = make_employee(dataset, skills={"SK_SYSTEM_DESIGN": 1}).model_copy(
        update={"last_review_date": dataset.as_of_date}
    )
    ds = dataset.with_additions([employee], [])

    updated = complete_activity(ds, "T0001", "EV_005")

    assert effective_skills(updated, "T0001")["SK_SYSTEM_DESIGN"] == 2


def test_uploaded_completion_on_review_day_is_not_applied_twice(dataset: Dataset) -> None:
    # A regular record dated on the review day is already part of the reviewed level.
    employee = make_employee(dataset, skills={"SK_SYSTEM_DESIGN": 1})
    ds = with_history(dataset, employee, [("EV_005", REVIEW_DATE.isoformat(), "completed")])

    assert effective_skills(ds, "T0001")["SK_SYSTEM_DESIGN"] == 1


@pytest.mark.parametrize(
    ("event_id", "message"),
    [("EV_999", "unknown"), ("EV_001", "mandatory"), ("EV_005", "already completed")],
)
def test_complete_activity_rejects_invalid_events(dataset: Dataset, event_id: str, message: str) -> None:
    ds = complete_activity(dataset.with_additions([make_employee(dataset, skills={})], []), "T0001", "EV_005")

    with pytest.raises(DatasetError, match=message):
        complete_activity(ds, "T0001", event_id)
