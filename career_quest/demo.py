"""Small authored synthetic dataset for reproducible demos without the starter kit."""

from datetime import date

from career_quest.ingestion import validate_dataset
from career_quest.models import Activity, Dataset, Employee, Event, Metadata, RoleProfile, Skill


def _employee(employee_id: str, name: str, skills: dict[str, int]) -> Employee:
    return Employee.model_validate(
        {
            "employee_id": employee_id,
            "full_name": name,
            "department": "Демо-разработка",
            "role": "Backend Engineer",
            "grade": "Middle",
            "manager_id": None,
            "hire_date": "2023-01-01",
            "tenure_months": 45,
            "work_format": "hybrid",
            "preferred_language": "ru",
            "career_goal": {"target_role": "Backend Engineer", "target_grade": "Senior"},
            "skills": skills,
            "last_review_date": "2026-09-01",
        }
    )


def _event(event_id: str, title: str, skill_id: str, *, mandatory: bool = False, max_level: int = 4) -> Event:
    return Event.model_validate(
        {
            "event_id": event_id,
            "title": title,
            "description": "Авторское синтетическое событие для проверки сценария.",
            "type": "workshop",
            "format": "self_paced",
            "duration_hours": 4,
            "mandatory": mandatory,
            "target_roles": ["Backend Engineer"],
            "target_grades": ["Junior", "Middle", "Senior", "Lead"],
            "develops_skills": [] if mandatory else [{"skill_id": skill_id, "gain": 1, "max_level": max_level}],
            "prerequisites": {},
            "upcoming_sessions": [],
        }
    )


def demo_dataset() -> Dataset:
    """Build three clearly fictional profiles in the official interchange schema."""
    skills = [
        Skill(skill_id="SK_SYSTEM_DESIGN", name="System Design", type="hard", category="engineering", description=""),
        Skill(skill_id="SK_PYTHON", name="Python", type="hard", category="engineering", description=""),
        Skill(
            skill_id="SK_PUBLIC_SPEAKING", name="Public Speaking", type="soft", category="communication", description=""
        ),
    ]
    profiles = [
        RoleProfile.model_validate(
            {
                "role": "Backend Engineer",
                "grade": grade,
                "required_skills": {"SK_SYSTEM_DESIGN": level, "SK_PYTHON": level, "SK_PUBLIC_SPEAKING": 2},
                "critical_skills": ["SK_SYSTEM_DESIGN"],
            }
        )
        for grade, level in (("Junior", 1), ("Middle", 2), ("Senior", 4), ("Lead", 5))
    ]
    employees = [
        _employee("DEMO_1", "Демо: сложный профиль", {"SK_SYSTEM_DESIGN": 2, "SK_PYTHON": 3, "SK_PUBLIC_SPEAKING": 0}),
        _employee("DEMO_2", "Демо: цель достигнута", {"SK_SYSTEM_DESIGN": 4, "SK_PYTHON": 4, "SK_PUBLIC_SPEAKING": 2}),
        _employee(
            "DEMO_3", "Демо: рост после оценки", {"SK_SYSTEM_DESIGN": 2, "SK_PYTHON": 3, "SK_PUBLIC_SPEAKING": 2}
        ),
    ]
    return _assemble_demo(employees, skills, profiles)


def _assemble_demo(employees: list[Employee], skills: list[Skill], profiles: list[RoleProfile]) -> Dataset:
    events = [
        _event("DEMO_SYSTEM", "Практикум проектирования систем", "SK_SYSTEM_DESIGN"),
        _event("DEMO_PYTHON", "Практикум Python", "SK_PYTHON"),
        _event("DEMO_REQUIRED", "Обязательный инструктаж", "SK_PYTHON", mandatory=True),
        _event("DEMO_CAP", "Основы проектирования", "SK_SYSTEM_DESIGN", max_level=2),
        _event("EV_036", "Клуб выступлений (демо)", "SK_PUBLIC_SPEAKING").model_copy(
            update={"format": "offline", "upcoming_sessions": [date(2026, 10, 8), date(2026, 10, 22)]}
        ),
    ]
    return validate_dataset(
        Dataset(
            meta=Metadata(dataset="Career Quest — authored demo", version="1.0", as_of_date=date(2026, 10, 1)),
            employees=employees,
            events=events,
            skills=skills,
            role_profiles=profiles,
            proficiency_scale={str(level): str(level) for level in range(6)},
            history=_demo_history(),
        )
    )


def _demo_history() -> list[Activity]:
    history = [
        Activity.model_validate(
            {
                "record_id": f"DEMO_R{index}",
                "employee_id": "DEMO_1",
                "event_id": "EV_036",
                "date": f"2026-0{index + 5}-01",
                "due_date": None,
                "status": "no_show",
                "completion_pct": 0,
                "score": None,
                "feedback_rating": None,
                "assigned_by": "self",
            }
        )
        for index in range(1, 4)
    ]
    history.append(
        Activity.model_validate(
            {
                "record_id": "DEMO_R4",
                "employee_id": "DEMO_3",
                "event_id": "DEMO_PYTHON",
                "date": "2026-09-15",
                "due_date": None,
                "status": "completed",
                "completion_pct": 100,
                "score": None,
                "feedback_rating": None,
                "assigned_by": "self",
            }
        )
    )
    return history
