"""Personal career quest: milestones towards the target profile and self-only badges.

Gamification here is strictly personal: no rankings, no comparison with colleagues and nothing
about mandatory activities. Every value is derived from the same data as the recommendations.
"""

from pydantic import BaseModel

from career_quest.data import Dataset
from career_quest.models import ActivityRecord
from career_quest.scoring import NEGATIVE_STATUSES, effective_skills, target_profile


class Milestone(BaseModel):
    """One skill requirement of the target profile.

    Attributes:
        skill_id: Skill identifier.
        current: Current effective level.
        required: Level required by the target profile.
        critical: Whether the target lists the skill as critical.
    """

    skill_id: str
    current: int
    required: int
    critical: bool

    @property
    def done(self) -> bool:
        """Whether the requirement is met."""
        return self.current >= self.required


class Quest(BaseModel):
    """Progress of one employee towards the target profile.

    Attributes:
        target_role: Target role.
        target_grade: Target grade.
        milestones: Requirements, critical first, then the largest remaining gaps.
        coverage: Share of required levels reached, 0–1.
        growth_points: Skill levels gained after the last review.
        badges: Codes of earned badges, in the order of ``BADGES``.
    """

    target_role: str
    target_grade: str
    milestones: list[Milestone]
    coverage: float
    growth_points: int
    badges: list[str]


# Badge codes in display order; the UI owns titles and descriptions.
BADGES = ("first_step", "self_starter", "comeback", "grown_since_review", "critical_closed", "halfway", "ready")
HALFWAY = 0.5


def build_quest(ds: Dataset, employee_id: str) -> Quest | None:
    """Build the employee's quest towards the target profile.

    Args:
        ds: Dataset with the employee, events and history.
        employee_id: Employee identifier.

    Returns:
        The quest, or ``None`` if the employee has no target profile.
    """
    target = target_profile(ds, employee_id)
    if target is None:
        return None
    levels = effective_skills(ds, employee_id)
    milestones = [
        Milestone(
            skill_id=skill_id,
            current=levels.get(skill_id, 0),
            required=required,
            critical=skill_id in target.critical_skills,
        )
        for skill_id, required in target.required_skills.items()
    ]
    milestones.sort(key=lambda m: (m.done, not m.critical, m.current - m.required, m.skill_id))
    required_total = sum(m.required for m in milestones)
    reached = sum(min(m.current, m.required) for m in milestones)
    reviewed = ds.employee(employee_id).skills
    growth = sum(max(0, level - reviewed.get(skill_id, 0)) for skill_id, level in levels.items())
    quest = Quest(
        target_role=target.role,
        target_grade=target.grade,
        milestones=milestones,
        coverage=reached / required_total if required_total else 1.0,
        growth_points=growth,
        badges=[],
    )
    earned = _earned_badges(ds, ds.history_for(employee_id), quest)
    return quest.model_copy(update={"badges": [code for code in BADGES if code in earned]})


def _earned_badges(ds: Dataset, history: list[ActivityRecord], quest: Quest) -> set[str]:
    """Return codes of badges earned from the employee's own voluntary history and progress."""
    voluntary = [r for r in history if not ds.event(r.event_id).mandatory]
    completed = [r for r in voluntary if r.status == "completed"]
    earned: set[str] = set()
    if completed:
        earned.add("first_step")
    if any(r.assigned_by == "self" for r in completed):
        earned.add("self_starter")
    if _came_back(ds, voluntary):
        earned.add("comeback")
    if quest.growth_points > 0:
        earned.add("grown_since_review")
    critical = [m for m in quest.milestones if m.critical]
    if any(m.done for m in critical):
        earned.add("critical_closed")
    if quest.coverage >= HALFWAY:
        earned.add("halfway")
    if critical and all(m.done for m in critical):
        earned.add("ready")
    return earned


def _came_back(ds: Dataset, history: list[ActivityRecord]) -> bool:
    """Whether a skill once skipped or dropped was later developed by a completed activity."""
    skipped: set[str] = set()
    for record in history:
        skills = {gain.skill_id for gain in ds.event(record.event_id).develops_skills}
        if record.status in NEGATIVE_STATUSES:
            skipped |= skills
        elif record.status == "completed" and skills & skipped:
            return True
    return False


def coverage_gain(quest: Quest, skill_changes: dict[str, tuple[int, int]]) -> float:
    """Return how much ``quest.coverage`` grows if the given skill changes happen.

    Args:
        quest: Current quest.
        skill_changes: Skill id -> (current level, level after completion), as in a recommendation.

    Returns:
        Coverage increase, 0–1.
    """
    required = {m.skill_id: m.required for m in quest.milestones}
    total = sum(required.values())
    if not total:
        return 0.0
    closed = sum(
        max(0, min(after, required[skill_id]) - min(before, required[skill_id]))
        for skill_id, (before, after) in skill_changes.items()
        if skill_id in required
    )
    return closed / total
