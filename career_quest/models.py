"""Validated models matching the Career Quest starter kit version 1.0."""

import datetime as dt
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Level = Annotated[int, Field(strict=True, ge=0, le=5)]
Identifier = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]
Grade = Literal["Junior", "Middle", "Senior", "Lead"]


class Record(BaseModel):
    """Reject unexpected fields so imported information is never silently lost."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class Metadata(Record):
    """Dataset identity and simulation date supplied by the starter kit."""

    dataset: str
    version: str
    as_of_date: dt.date


class CareerGoal(Record):
    """Employee's chosen role and grade."""

    target_role: str
    target_grade: Grade


class Employee(Record):
    """Skill levels recorded at the employee's last review."""

    employee_id: Identifier
    full_name: str
    department: str
    role: str
    grade: Grade
    manager_id: Identifier | None
    hire_date: dt.date
    tenure_months: Annotated[int, Field(strict=True, ge=0)]
    work_format: Literal["office", "hybrid", "remote"]
    preferred_language: Literal["kk", "ru", "en"]
    career_goal: CareerGoal | None
    skills: dict[Identifier, Level]
    last_review_date: dt.date


class Skill(Record):
    """Catalog entry for one skill."""

    skill_id: Identifier
    name: str
    type: Literal["hard", "soft"]
    category: str
    description: str


class RoleProfile(Record):
    """Required and critical skills for a role/grade combination."""

    role: str
    grade: Grade
    required_skills: dict[Identifier, Level]
    critical_skills: list[Identifier]


class SkillGain(Record):
    """Skill improvement and the activity's development ceiling."""

    skill_id: Identifier
    gain: Annotated[int, Field(strict=True, ge=1, le=5)]
    max_level: Level


class Event(Record):
    """Activity with audience, prerequisites and scheduled sessions."""

    event_id: Identifier
    title: str
    description: str
    type: str
    format: Literal["online", "offline", "self_paced"]
    duration_hours: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    mandatory: bool
    target_roles: list[str]
    target_grades: list[Grade]
    develops_skills: list[SkillGain]
    prerequisites: dict[Identifier, Level]
    upcoming_sessions: list[dt.date]


class ActivityStatus(StrEnum):
    """Participation outcomes from the starter kit."""

    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    DROPPED = "dropped"
    NO_SHOW = "no_show"
    DECLINED = "declined"
    OVERDUE = "overdue"


class Activity(Record):
    """One CSV participation record."""

    record_id: Identifier
    employee_id: Identifier
    event_id: Identifier
    date: dt.date
    due_date: dt.date | None
    status: ActivityStatus
    completion_pct: Annotated[int, Field(ge=0, le=100)]
    score: Annotated[int, Field(ge=0, le=100)] | None
    feedback_rating: Annotated[int, Field(ge=1, le=5)] | None
    assigned_by: Literal["self", "manager", "hr"]

    @field_validator("due_date", "score", "feedback_rating", mode="before")
    @classmethod
    def empty_as_none(cls, value: object) -> object:
        """Treat empty optional CSV cells as null."""
        return None if value == "" else value

    @model_validator(mode="after")
    def check_completion(self) -> Self:
        """Reject contradictory status/progress pairs."""
        if self.status == ActivityStatus.COMPLETED and self.completion_pct != 100:
            raise ValueError("completed требует completion_pct=100")
        if self.status in {ActivityStatus.NO_SHOW, ActivityStatus.DECLINED} and self.completion_pct != 0:
            raise ValueError("no_show/declined требуют completion_pct=0")
        if self.status in {ActivityStatus.IN_PROGRESS, ActivityStatus.OVERDUE, ActivityStatus.DROPPED}:
            minimum = 5 if self.status == ActivityStatus.DROPPED else 0
            if not minimum <= self.completion_pct <= 95:
                raise ValueError("Противоречивый прогресс незавершённого события")
        return self


class EmployeeFile(Record):
    """Employee JSON document."""

    meta: Metadata
    employees: list[Employee]


class EventFile(Record):
    """Event JSON document."""

    meta: Metadata
    events: list[Event]


class SkillFile(Record):
    """Skill catalog and career requirements JSON document."""

    meta: Metadata
    proficiency_scale: dict[str, str]
    skills: list[Skill]
    role_profiles: list[RoleProfile]


class Dataset(Record):
    """Validated snapshot; gains since last review are derived from history."""

    meta: Metadata
    employees: list[Employee]
    events: list[Event]
    skills: list[Skill]
    role_profiles: list[RoleProfile]
    proficiency_scale: dict[str, str]
    history: list[Activity]
