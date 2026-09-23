"""Recommendation scoring: current skill levels, career target, candidate events and their scores.

See the "Recommendation logic" section of ``.agents/project-spec.md``.
"""

import datetime as dt
from collections import Counter
from dataclasses import dataclass

import structlog
from pydantic import BaseModel

from career_quest.data import Dataset, DatasetError
from career_quest.models import ActivityRecord, Employee, Event, RoleProfile, SkillGain, next_grade

log = structlog.get_logger(__name__)

# Contribution of each factor to the score. Gap weights are per level closed, history weights per record,
# duration per hour above LONG_EVENT_HOURS.
WEIGHTS: dict[str, float] = {
    "critical_gap": 3.0,
    "required_gap": 1.5,
    "target_alignment": 1.0,
    "grade_fit": 0.5,
    "goal_alignment": 0.5,
    "history_avoidance_skill": -1.0,
    "history_avoidance_type": -0.5,
    "engagement": 0.5,
    "self_initiated": 0.25,
    "feedback": 0.5,
    "availability": 0.5,
    "duration": -0.05,
}
MIN_FACTORS = 3
GAP_CODES = frozenset({"critical_gap", "required_gap"})
SOON_DAYS = 14
LONG_EVENT_HOURS = 8.0
ENGAGEMENT_CAP = 2
GOOD_FEEDBACK = 4.0
POOR_FEEDBACK = 2.0
RECURRING_EVENT_ID = "EV_036"
NEGATIVE_STATUSES = frozenset({"no_show", "dropped", "declined"})


class Factor(BaseModel):
    """One reason behind a recommendation.

    Attributes:
        code: Factor kind, e.g. ``critical_gap``, ``goal_alignment``, ``history_avoidance``.
        weight: Contribution to the recommendation score (positive or negative).
        detail: Human-readable English fact with numbers.
    """

    code: str
    weight: float
    detail: str


class Recommendation(BaseModel):
    """A development activity suggested to an employee, with the factors behind it.

    Attributes:
        event_id: Recommended event.
        score: Sum of factor weights.
        skill_changes: Skill id -> (current level, level after completion) for every skill the event raises.
        factors: Non-zero factors behind the score, at least ``MIN_FACTORS``.
        next_session: First upcoming session, ``None`` for self-paced events.
    """

    event_id: str
    score: float
    skill_changes: dict[str, tuple[int, int]]
    factors: list[Factor]
    next_session: dt.date | None


@dataclass(frozen=True)
class _Context:
    """Everything about one employee that scoring needs, computed once per ``recommend`` call."""

    employee: Employee
    target: RoleProfile
    skills: dict[str, int]
    history: list[tuple[ActivityRecord, Event]]
    as_of: dt.date


def _apply_gain(level: int, gain: SkillGain) -> int:
    """Raise ``level`` by ``gain.gain`` up to ``gain.max_level``; never lower an already higher level."""
    return max(level, min(level + gain.gain, gain.max_level))


def effective_skills(ds: Dataset, employee_id: str) -> dict[str, int]:
    """Return the employee's current skill levels.

    The profile holds levels from the last review. Gains of events completed strictly after
    ``last_review_date`` (and not after the snapshot date) are applied on top, in date order.
    Skills absent from the profile start at level 0 and appear in the result only once raised.

    Args:
        ds: Dataset to read the profile, events and history from.
        employee_id: Employee identifier.

    Returns:
        Mapping of skill id to level 0–5.
    """
    employee = ds.employee(employee_id)
    levels = dict(employee.skills)
    for record in ds.history_for(employee_id):
        if record.status != "completed":
            continue
        if not employee.last_review_date < record.session_date <= ds.as_of_date:
            continue
        for gain in ds.event(record.event_id).develops_skills:
            levels[gain.skill_id] = _apply_gain(levels.get(gain.skill_id, 0), gain)
    return levels


def target_profile(ds: Dataset, employee_id: str) -> RoleProfile | None:
    """Return the role profile the employee is working towards.

    The career goal wins if set. Without a goal, the target is the next grade of the current role;
    a Lead without a goal targets the current profile. A goal that points to an unknown role/grade
    (possible in uploaded data) is logged and ignored.

    Args:
        ds: Dataset to read the profile and role requirements from.
        employee_id: Employee identifier.

    Returns:
        The target profile, or ``None`` if the dataset has no profile for the derived target.
    """
    employee = ds.employee(employee_id)
    goal = employee.career_goal
    if goal is not None:
        try:
            return ds.role_profile(goal.target_role, goal.target_grade)
        except KeyError:
            log.warning("unknown_career_goal", employee_id=employee_id, role=goal.target_role, grade=goal.target_grade)
    grade = next_grade(employee.grade) or employee.grade
    try:
        return ds.role_profile(employee.role, grade)
    except KeyError:
        log.warning("missing_role_profile", employee_id=employee_id, role=employee.role, grade=grade)
        return None


def recommend(ds: Dataset, employee_id: str, limit: int = 3) -> list[Recommendation]:
    """Recommend voluntary development activities for an employee.

    Every candidate event gets a weighted sum of factors (skill gaps towards the target profile,
    alignment with the target and career goal, participation history, availability). Only
    recommendations with a positive score and at least ``MIN_FACTORS`` non-zero factors are returned.

    Args:
        ds: Dataset with the employee, events and history.
        employee_id: Employee identifier.
        limit: Maximum number of recommendations.

    Returns:
        Up to ``limit`` recommendations, best first (ties broken by event id).
    """
    target = target_profile(ds, employee_id)
    if target is None:
        return []
    ctx = _Context(
        employee=ds.employee(employee_id),
        target=target,
        skills=effective_skills(ds, employee_id),
        history=[(record, ds.event(record.event_id)) for record in ds.history_for(employee_id)],
        as_of=ds.as_of_date,
    )
    recommendations = [_score(event, ctx) for event in _candidates(ds, ctx)]
    shown = [
        rec
        for rec in recommendations
        if rec.score > 0 and len(rec.factors) >= MIN_FACTORS and any(f.code in GAP_CODES for f in rec.factors)
    ]
    shown.sort(key=lambda rec: (-rec.score, rec.event_id))
    log.debug("recommended", employee_id=employee_id, candidates=len(recommendations), shown=len(shown[:limit]))
    return shown[:limit]


def complete_activity(ds: Dataset, employee_id: str, event_id: str) -> Dataset:
    """Record that the employee completed an event on the snapshot date.

    Args:
        ds: Current dataset.
        employee_id: Employee identifier.
        event_id: Completed event.

    Returns:
        A new dataset with a ``completed`` self-initiated record dated ``ds.as_of_date``.

    Raises:
        DatasetError: If the employee or event is unknown, the event is mandatory, or it was already
            completed and is not the recurring club.
    """
    try:
        ds.employee(employee_id)
        event = ds.event(event_id)
    except KeyError as exc:
        raise DatasetError(f"unknown employee or event: {exc}") from exc
    if event.mandatory:
        raise DatasetError(f"{event_id} is mandatory and is not tracked as a development activity")
    records = ds.history_for(employee_id)
    if event_id != RECURRING_EVENT_ID and any(r.event_id == event_id and r.status == "completed" for r in records):
        raise DatasetError(f"{event_id} is already completed by {employee_id}")
    taken = {record.record_id for record in ds.history}
    number = 1
    while f"SIM-{employee_id}-{event_id}-{number}" in taken:
        number += 1
    record = ActivityRecord(
        record_id=f"SIM-{employee_id}-{event_id}-{number}",
        employee_id=employee_id,
        event_id=event_id,
        session_date=ds.as_of_date,
        due_date=None,
        status="completed",
        completion_pct=100,
        score=None,
        feedback_rating=None,
        assigned_by="self",
    )
    log.info("activity_completed", employee_id=employee_id, event_id=event_id, record_id=record.record_id)
    return ds.with_additions([], [record])


def _candidates(ds: Dataset, ctx: _Context) -> list[Event]:
    """Return voluntary events the employee can join now and that raise at least one skill."""
    completed = {r.event_id for r, _ in ctx.history if r.status == "completed"} - {RECURRING_EVENT_ID}
    in_progress = {r.event_id for r, _ in ctx.history if r.status == "in_progress"}
    return [
        event
        for event in ds.events
        if event.event_id not in completed | in_progress
        and _is_offered(event, ctx)
        and _is_open(event, ctx)
        and _skill_changes(event, ctx.skills)
    ]


def _is_offered(event: Event, ctx: _Context) -> bool:
    """Whether a voluntary event targets the employee's current or target role and grade."""
    roles = {ctx.employee.role, ctx.target.role}
    grades = {ctx.employee.grade, ctx.target.grade}
    return not event.mandatory and bool(roles & set(event.target_roles)) and bool(grades & set(event.target_grades))


def _is_open(event: Event, ctx: _Context) -> bool:
    """Whether the employee meets the prerequisites and the event can still be attended."""
    if any(ctx.skills.get(skill_id, 0) < level for skill_id, level in event.prerequisites.items()):
        return False
    return event.event_format == "self_paced" or _next_session(event, ctx.as_of) is not None


def _next_session(event: Event, as_of: dt.date) -> dt.date | None:
    """Return the first session on or after ``as_of``."""
    return min((day for day in event.upcoming_sessions if day >= as_of), default=None)


def _skill_changes(event: Event, skills: dict[str, int]) -> dict[str, tuple[int, int]]:
    """Return skill id -> (current, after completion) for every skill the event actually raises."""
    changes: dict[str, tuple[int, int]] = {}
    for gain in event.develops_skills:
        current = skills.get(gain.skill_id, 0)
        after = _apply_gain(current, gain)
        if after > current:
            changes[gain.skill_id] = (current, after)
    return changes


def _score(event: Event, ctx: _Context) -> Recommendation:
    """Compute all factors for one candidate event."""
    changes = _skill_changes(event, ctx.skills)
    factors = [
        *_gap_factors(changes, ctx.target),
        *_alignment_factors(event, ctx),
        *_history_factors(event, ctx),
        *_logistics_factors(event, ctx.as_of),
    ]
    factors = [factor for factor in factors if factor.weight != 0]
    return Recommendation(
        event_id=event.event_id,
        score=round(sum(factor.weight for factor in factors), 2),
        skill_changes=changes,
        factors=factors,
        next_session=None if event.event_format == "self_paced" else _next_session(event, ctx.as_of),
    )


def _gap_factors(changes: dict[str, tuple[int, int]], target: RoleProfile) -> list[Factor]:
    """Levels closed towards the target's requirements, split into critical and other required skills."""
    parts: dict[str, list[str]] = {"critical_gap": [], "required_gap": []}
    closed: Counter[str] = Counter()
    for skill_id, (current, after) in changes.items():
        required = target.required_skills.get(skill_id)
        if required is None or current >= required:
            continue
        code = "critical_gap" if skill_id in target.critical_skills else "required_gap"
        closed[code] += min(after, required) - current
        label = " (critical)" if code == "critical_gap" else ""
        parts[code].append(
            f"{skill_id} {current} → {after}, required {required} for {target.role} {target.grade}{label}"
        )
    return [
        Factor(code=code, weight=WEIGHTS[code] * closed[code], detail="; ".join(details))
        for code, details in parts.items()
        if details
    ]


def _alignment_factors(event: Event, ctx: _Context) -> list[Factor]:
    """Whether the event is designed for the target position (or at least the current grade) and serves the goal."""
    target = ctx.target
    if target.role not in event.target_roles or target.grade not in event.target_grades:
        if ctx.employee.grade == target.grade or ctx.employee.grade not in event.target_grades:
            return []
        return [
            Factor(
                code="grade_fit",
                weight=WEIGHTS["grade_fit"],
                detail=f"Designed for the current grade {ctx.employee.grade}, builds the base for {target.grade}",
            )
        ]
    goal = ctx.employee.career_goal
    from_goal = goal is not None and (goal.target_role, goal.target_grade) == (target.role, target.grade)
    if from_goal:
        purpose = "the career goal"
    elif target.grade == ctx.employee.grade:
        purpose = "the current grade"
    else:
        purpose = "the next grade"
    factors = [
        Factor(
            code="target_alignment",
            weight=WEIGHTS["target_alignment"],
            detail=f"Designed for {target.role} {target.grade}, {purpose}",
        )
    ]
    if from_goal:
        factors.append(
            Factor(
                code="goal_alignment",
                weight=WEIGHTS["goal_alignment"],
                detail=f"Serves the career goal {target.role} {target.grade}",
            )
        )
    return factors


def _history_factors(event: Event, ctx: _Context) -> list[Factor]:
    """Factors from the employee's own records on events with the same skills or of the same type."""
    skills = {gain.skill_id for gain in event.develops_skills}
    same_skill = [r for r, past in ctx.history if skills & {g.skill_id for g in past.develops_skills}]
    same_type = [r for r, past in ctx.history if past.event_type == event.event_type and r not in same_skill]
    factors = [
        _avoidance_factor(same_skill, same_type, event.event_type),
        _engagement_factor(same_skill + same_type),
        _feedback_factor(same_skill + same_type),
    ]
    return [factor for factor in factors if factor is not None]


def _avoidance_factor(
    same_skill: list[ActivityRecord], same_type: list[ActivityRecord], event_type: str
) -> Factor | None:
    """Past no-shows, drop-outs and declines on events with the same skills (stronger) or of the same type."""
    weight = 0.0
    details: list[str] = []
    for records, weight_key, scope in (
        (same_skill, "history_avoidance_skill", "on the same skills"),
        (same_type, "history_avoidance_type", f"of type {event_type}"),
    ):
        negative = Counter(r.status for r in records if r.status in NEGATIVE_STATUSES)
        total = sum(negative.values())
        if total:
            weight += WEIGHTS[weight_key] * total
            statuses = ", ".join(f"{status} ×{count}" for status, count in sorted(negative.items()))
            details.append(
                f"{_count(total, 'earlier activity', 'earlier activities')} {scope} not finished ({statuses})"
            )
    if not details:
        return None
    return Factor(code="history_avoidance", weight=weight, detail="; ".join(details))


def _engagement_factor(records: list[ActivityRecord]) -> Factor | None:
    """Past completions of similar events, with a bonus if any of them was self-initiated."""
    completed = [r for r in records if r.status == "completed"]
    if not completed:
        return None
    self_initiated = sum(r.assigned_by == "self" for r in completed)
    weight = WEIGHTS["engagement"] * min(len(completed), ENGAGEMENT_CAP)
    if self_initiated:
        weight += WEIGHTS["self_initiated"]
    return Factor(
        code="engagement",
        weight=weight,
        detail=f"{_count(len(completed), 'similar activity', 'similar activities')} completed before, "
        f"{self_initiated} of them self-initiated",
    )


def _feedback_factor(records: list[ActivityRecord]) -> Factor | None:
    """Average feedback the employee gave to similar events, if clearly good or poor."""
    ratings = [r.feedback_rating for r in records if r.feedback_rating is not None]
    if not ratings:
        return None
    average = sum(ratings) / len(ratings)
    if POOR_FEEDBACK < average < GOOD_FEEDBACK:
        return None
    sign = 1 if average >= GOOD_FEEDBACK else -1
    rated = _count(len(ratings), "similar activity", "similar activities")
    return Factor(
        code="feedback",
        weight=sign * WEIGHTS["feedback"],
        detail=f"Average own rating of {rated}: {average:.1f} of 5",
    )


def _logistics_factors(event: Event, as_of: dt.date) -> list[Factor]:
    """Availability (self-paced or a session soon) and the time the event takes."""
    factors: list[Factor] = []
    session = _next_session(event, as_of)
    if event.event_format == "self_paced":
        factors.append(
            Factor(code="availability", weight=WEIGHTS["availability"], detail="Self-paced, can start any time")
        )
    elif session is not None and (session - as_of).days <= SOON_DAYS:
        days = _count((session - as_of).days, "day", "days")
        factors.append(
            Factor(code="availability", weight=WEIGHTS["availability"], detail=f"Next session {session}, in {days}")
        )
    extra_hours = event.duration_hours - LONG_EVENT_HOURS
    if extra_hours > 0:
        factors.append(
            Factor(
                code="duration",
                weight=round(WEIGHTS["duration"] * extra_hours, 2),
                detail=f"Takes {event.duration_hours:g} hours, {extra_hours:g} above {LONG_EVENT_HOURS:g}",
            )
        )
    return factors


def _count(number: int, singular: str, plural: str) -> str:
    """Format a count with the right noun form, e.g. ``1 similar activity``."""
    return f"{number} {singular if number == 1 else plural}"
