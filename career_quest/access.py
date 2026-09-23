"""Small server-side access gate for demo and configured employee/HR sessions."""

import hmac
import os
from dataclasses import dataclass
from typing import Literal

Role = Literal["employee", "hr"]


@dataclass(frozen=True)
class Viewer:
    """Authenticated role and optional employee scope."""

    role: Role
    employee_id: str | None
    demo: bool


def configured_access() -> bool:
    """Require all three access variables together; partial setup is an error."""
    names = ("CQ_EMPLOYEE_ID", "CQ_EMPLOYEE_PASSWORD", "CQ_HR_PASSWORD")
    configured = [bool(os.environ.get(name)) for name in names]
    if any(configured) and not all(configured):
        raise ValueError("Задайте вместе CQ_EMPLOYEE_ID, CQ_EMPLOYEE_PASSWORD и CQ_HR_PASSWORD")
    return all(configured)


def authenticate(role: Role, password: str) -> Viewer | None:
    """Compare configured secrets and bind employee sessions to one profile."""
    if not configured_access():
        return None
    name = "CQ_HR_PASSWORD" if role == "hr" else "CQ_EMPLOYEE_PASSWORD"
    if not password or not hmac.compare_digest(password.encode(), os.environ[name].encode()):
        return None
    employee_id = os.environ["CQ_EMPLOYEE_ID"] if role == "employee" else None
    return Viewer(role, employee_id, demo=False)


def can_view_employee(viewer: Viewer, employee_id: str) -> bool:
    """Allow HR/demo access or the employee's own profile only."""
    return viewer.demo or viewer.role == "hr" or viewer.employee_id == employee_id


def require_hr(viewer: Viewer) -> None:
    """Reject dataset imports and organization-wide exports outside the HR role."""
    if viewer.role != "hr":
        raise PermissionError("Действие доступно только HR")
