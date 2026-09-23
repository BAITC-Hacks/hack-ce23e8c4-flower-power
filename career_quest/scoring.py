"""Recommendation scoring: current skill levels, career target, candidate events and their scores.

See the "Recommendation logic" section of ``.agents/project-spec.md``.
"""

import structlog

from career_quest.data import Dataset
from career_quest.models import RoleProfile, SkillGain, next_grade

log = structlog.get_logger(__name__)


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
