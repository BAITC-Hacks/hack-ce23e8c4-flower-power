from test_scoring import SENIOR_BACKEND, dataset, make_employee, with_history

from career_quest.data import Dataset
from career_quest.quest import build_quest
from career_quest.scoring import complete_activity

__all__ = ["dataset"]


def test_quest_orders_critical_gaps_first_and_counts_coverage(dataset: Dataset) -> None:
    ds = dataset.with_additions([make_employee(dataset, skills={**SENIOR_BACKEND, "SK_SYSTEM_DESIGN": 2})], [])

    quest = build_quest(ds, "T0001")

    assert quest is not None
    assert (quest.target_role, quest.target_grade) == ("Backend Engineer", "Senior")
    first = quest.milestones[0]
    assert (first.skill_id, first.current, first.required, first.critical, first.done) == (
        "SK_SYSTEM_DESIGN",
        2,
        4,
        True,
        False,
    )
    required_total = sum(SENIOR_BACKEND.values())
    assert quest.coverage == (required_total - 2) / required_total


def test_new_employee_has_no_activity_badges(dataset: Dataset) -> None:
    ds = dataset.with_additions([make_employee(dataset, skills={})], [])

    quest = build_quest(ds, "T0001")

    assert quest is not None
    assert quest.badges == []
    assert quest.growth_points == 0


def test_completion_earns_growth_and_badges(dataset: Dataset) -> None:
    skills = {**SENIOR_BACKEND, "SK_SYSTEM_DESIGN": 3}
    ds = dataset.with_additions([make_employee(dataset, skills=skills)], [])

    quest = build_quest(complete_activity(ds, "T0001", "EV_006"), "T0001")

    assert quest is not None
    assert quest.growth_points == 2  # SK_SYSTEM_DESIGN 3 → 4 and SK_OBSERVABILITY 3 → 4
    assert quest.badges == ["first_step", "self_starter", "grown_since_review", "critical_closed", "halfway", "ready"]


def test_comeback_badge_after_completing_a_skipped_skill(dataset: Dataset) -> None:
    employee = make_employee(dataset, skills={"SK_PUBLIC_SPEAKING": 0})
    ds = with_history(dataset, employee, [("EV_036", "2026-03-11", "no_show"), ("EV_036", "2026-07-08", "completed")])

    quest = build_quest(ds, "T0001")

    assert quest is not None
    assert "comeback" in quest.badges
