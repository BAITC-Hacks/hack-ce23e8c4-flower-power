"""Pydantic models mirroring the Career Quest dataset schema (see data/README.md)."""

import datetime as dt
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Grade = Literal["Junior", "Middle", "Senior", "Lead"]
GRADE_ORDER: tuple[Grade, ...] = ("Junior", "Middle", "Senior", "Lead")

SkillType = Literal["hard", "soft"]
EventType = Literal["compliance", "onboarding", "course", "workshop", "mentoring", "certification", "meetup"]
EventFormat = Literal["online", "offline", "self_paced"]
WorkFormat = Literal["office", "hybrid", "remote"]
Language = Literal["kk", "ru", "en"]
Status = Literal["completed", "in_progress", "dropped", "no_show", "declined", "overdue"]
AssignedBy = Literal["self", "manager", "hr"]

SkillLevel = Annotated[int, Field(ge=0, le=5)]


def next_grade(grade: Grade) -> Grade | None:
    """Return the grade that follows ``grade``, or ``None`` for the top grade.

    Args:
        grade: Current grade.

    Returns:
        The next grade in ``GRADE_ORDER`` or ``None`` if ``grade`` is ``Lead``.
    """
    index = GRADE_ORDER.index(grade)
    return GRADE_ORDER[index + 1] if index + 1 < len(GRADE_ORDER) else None


class _Record(BaseModel):
    """Immutable base model; unknown fields are ignored so extended datasets still load."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class Skill(_Record):
    """A skill from the catalog in ``skills.json``."""

    skill_id: str
    name: str
    skill_type: SkillType = Field(alias="type")
    category: str
    description: str


class RoleProfile(_Record):
    """Skill requirements for one (role, grade) pair."""

    role: str
    grade: Grade
    required_skills: dict[str, SkillLevel]
    critical_skills: list[str]


class CareerGoal(_Record):
    """Target position an employee has set for themselves."""

    target_role: str
    target_grade: Grade


class Employee(_Record):
    """Employee profile from ``employees.json``."""

    employee_id: str
    full_name: str
    department: str
    role: str
    grade: Grade
    manager_id: str | None
    hire_date: dt.date
    tenure_months: int = Field(ge=0)
    work_format: WorkFormat
    preferred_language: Language
    career_goal: CareerGoal | None
    skills: dict[str, SkillLevel]
    last_review_date: dt.date

    def level(self, skill_id: str) -> int:
        """Return the assessed level of a skill; skills absent from the profile are level 0.

        Args:
            skill_id: Skill identifier, e.g. ``SK_SYSTEM_DESIGN``.

        Returns:
            Level from 0 to 5.
        """
        return self.skills.get(skill_id, 0)


class SkillGain(_Record):
    """Effect of completing an event on one skill."""

    skill_id: str
    gain: int = Field(ge=1)
    max_level: SkillLevel


class Event(_Record):
    """Development activity from ``events.json``."""

    event_id: str
    title: str
    description: str
    event_type: EventType = Field(alias="type")
    event_format: EventFormat = Field(alias="format")
    duration_hours: float = Field(gt=0)
    mandatory: bool
    target_roles: list[str]
    target_grades: list[Grade]
    develops_skills: list[SkillGain]
    prerequisites: dict[str, SkillLevel]
    upcoming_sessions: list[dt.date]


class ActivityRecord(_Record):
    """One row of ``activity_history.csv``: one employee's participation in one event."""

    record_id: str
    employee_id: str
    event_id: str
    session_date: dt.date = Field(alias="date")
    due_date: dt.date | None
    status: Status
    completion_pct: int = Field(ge=0, le=100)
    score: int | None = Field(ge=0, le=100)
    feedback_rating: int | None = Field(ge=1, le=5)
    assigned_by: AssignedBy

    @field_validator("due_date", "score", "feedback_rating", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """Treat empty CSV cells as missing values."""
        return None if value == "" else value
