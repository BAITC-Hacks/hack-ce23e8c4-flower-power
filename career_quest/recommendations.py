"""Explainable multi-factor baseline, explicitly separate from an AI model."""

from dataclasses import dataclass
from datetime import date

from career_quest.models import ActivityStatus, Dataset, Employee, Event, RoleProfile
from career_quest.progress import available_date, current_skills, eligibility_issues, gained_level

GRADE_ORDER = ("Junior", "Middle", "Senior", "Lead")


@dataclass(frozen=True)
class Gap:
    """Current assessed/replayed level and target requirement."""

    skill_id: str
    name: str
    current: int
    required: int
    critical: bool

    @property
    def deficit(self) -> int:
        """Return the non-negative remaining skill gap."""
        return max(0, self.required - self.current)


@dataclass(frozen=True)
class Recommendation:
    """One candidate and the factors behind its baseline score."""

    event: Event
    session_date: date
    score: float
    coverage: float
    completed_similar: int
    avoided_similar: int
    reasons: tuple[str, ...]


def target_profile(employee: Employee, dataset: Dataset) -> RoleProfile | None:
    """Use the explicit career goal, otherwise the next grade of the current role."""
    goal = employee.career_goal
    role = goal.target_role if goal else employee.role
    grade: str
    if goal:
        grade = goal.target_grade
    elif employee.grade == GRADE_ORDER[-1]:
        return None
    else:
        grade = GRADE_ORDER[GRADE_ORDER.index(employee.grade) + 1]
    return next((item for item in dataset.role_profiles if item.role == role and item.grade == grade), None)


def skill_gaps(employee: Employee, dataset: Dataset) -> list[Gap]:
    """Compare effective skills to the goal; missing skills are zero per the kit."""
    profile = target_profile(employee, dataset)
    if profile is None:
        return []
    levels = current_skills(employee, dataset)
    names = {skill.skill_id: skill.name for skill in dataset.skills}
    return [
        Gap(skill_id, names[skill_id], levels.get(skill_id, 0), level, skill_id in profile.critical_skills)
        for skill_id, level in profile.required_skills.items()
    ]


def readiness(employee: Employee, dataset: Dataset) -> float | None:
    """Compute weighted skill coverage, not a probability or guarantee of promotion."""
    gaps = skill_gaps(employee, dataset)
    total = sum(gap.required * (2 if gap.critical else 1) for gap in gaps)
    if not total:
        return None
    reached = sum(min(gap.current, gap.required) * (2 if gap.critical else 1) for gap in gaps)
    return reached / total


def _history_counts(employee: Employee, event: Event, dataset: Dataset) -> tuple[int, int]:
    skills = {gain.skill_id for gain in event.develops_skills}
    similar = {
        item.event_id
        for item in dataset.events
        if not item.mandatory and skills.intersection(gain.skill_id for gain in item.develops_skills)
    }
    rows = [row for row in dataset.history if row.employee_id == employee.employee_id and row.event_id in similar]
    completed = sum(row.status == ActivityStatus.COMPLETED for row in rows)
    avoided = sum(
        row.status in {ActivityStatus.NO_SHOW, ActivityStatus.DECLINED, ActivityStatus.DROPPED} for row in rows
    )
    return completed, avoided


def _coverage(event: Event, gaps: list[Gap]) -> tuple[float, list[str]]:
    gains = {gain.skill_id: gain for gain in event.develops_skills}
    total = sum(gap.deficit * (2 if gap.critical else 1) for gap in gaps)
    closed = 0
    reasons = []
    for gap in gaps:
        if gap.skill_id not in gains or not gap.deficit:
            continue
        after = gained_level(gap.current, gains[gap.skill_id])
        improvement = min(after, gap.required) - gap.current
        if improvement > 0:
            closed += improvement * (2 if gap.critical else 1)
            label = "Критический навык" if gap.critical else "Навык"
            reasons.append(
                f"{label} {gap.name}: сейчас {gap.current}, требуется {gap.required}, после активности {after}."
            )
    return (closed / total if total else 0), reasons


def _score(employee: Employee, event: Event, dataset: Dataset, gaps: list[Gap]) -> Recommendation | None:
    coverage, reasons = _coverage(event, gaps)
    day = available_date(employee, event, dataset)
    if coverage == 0 or day is None:
        return None
    completed, avoided = _history_counts(employee, event, dataset)
    format_penalty = 5 if employee.work_format == "remote" and event.format == "offline" else 0
    score = 100 * coverage + 3 * min(completed, 3) - 4 * min(avoided, 3) - format_penalty
    reasons.append(f"Активности по тем же навыкам: завершено {completed}, пропущено/отклонено/прекращено {avoided}.")
    reasons.append(f"Закрывает {coverage:.0%} взвешенного дефицита; критические навыки имеют вес 2.")
    if format_penalty:
        reasons.append("Очный формат при удалённой работе: −5 баллов; участие остаётся доступным.")
    return Recommendation(event, day, round(score, 2), coverage, completed, avoided, tuple(reasons))


def recommend(employee: Employee, dataset: Dataset, limit: int = 3) -> list[Recommendation]:
    """Rank available voluntary activities by critical gaps, history and format."""
    if not 1 <= limit <= 3:
        raise ValueError("Количество рекомендаций должно быть от 1 до 3")
    gaps = skill_gaps(employee, dataset)
    candidates = []
    for event in dataset.events:
        if eligibility_issues(employee, event, dataset):
            continue
        candidate = _score(employee, event, dataset, gaps)
        if candidate is not None:
            candidates.append(candidate)
    return sorted(candidates, key=lambda item: (-item.score, item.session_date, item.event.event_id))[:limit]
