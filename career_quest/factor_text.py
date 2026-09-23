"""Russian rendering of factor details for the web client.

``Factor.detail`` stays English (it is the public contract and the LLM input). This module maps the
fixed detail templates written by ``scoring.py`` to Russian with human skill names; anything it does
not recognise is returned unchanged, so no fact is ever lost.
"""

import re
from collections.abc import Callable

from career_quest.labels import GRADE_LABELS, ROLE_LABELS, STATUS_LABELS
from career_quest.scoring import Factor

EVENT_TYPE_LABELS = {
    "course": "курс",
    "workshop": "практикум",
    "mentoring": "наставничество",
    "certification": "сертификация",
    "meetup": "клуб",
    "compliance": "обязательное обучение",
    "onboarding": "адаптация",
}
PURPOSES = {
    "the career goal": "карьерная цель",
    "the current grade": "текущий уровень",
    "the next grade": "следующий уровень",
}
GRADES = "Junior|Middle|Senior|Lead"
GAP = re.compile(rf"^(\w+) (\d) → (\d), required (\d) for (.+) ({GRADES})( \(critical\))?$")
TARGET = re.compile(rf"^Designed for (.+) ({GRADES}), (.+)$")
GRADE_FIT = re.compile(rf"^Designed for the current grade ({GRADES}), builds the base for ({GRADES})$")
GOAL = re.compile(rf"^Serves the career goal (.+) ({GRADES})$")
AVOID = re.compile(r"^(\d+) earlier activit(?:y|ies) (on the same skills|of type (\w+)) not finished \((.+)\)$")
ENGAGE = re.compile(r"^(\d+) similar activit(?:y|ies) completed before, (\d+) of them self-initiated$")
FEEDBACK = re.compile(r"^Average own rating of (\d+) similar activit(?:y|ies): ([\d.]+) of 5$")
SESSION = re.compile(r"^Next session (\d{4})-(\d{2})-(\d{2}), in (\d+) days?$")
DURATION = re.compile(r"^Takes ([\d.]+) hours, ([\d.]+) above ([\d.]+)$")


def russian_detail(factor: Factor, skill_name: Callable[[str], str]) -> str:
    """Return the factor detail in Russian, or the original text if the template is unknown.

    Args:
        factor: Factor computed by ``scoring.recommend``.
        skill_name: Maps a skill id to its display name.

    Returns:
        Russian text with the same numbers as the original detail.
    """
    parts = [_part(part, skill_name) for part in factor.detail.split("; ")]
    return "; ".join(parts)


def _role(role: str, grade: str) -> str:
    return f"{ROLE_LABELS.get(role, role)} · {GRADE_LABELS.get(grade, grade)}"


def _part(text: str, skill_name: Callable[[str], str]) -> str:
    for pattern, render in _RENDERERS:
        match = pattern.match(text)
        if match:
            return render(match, skill_name)
    if text == "Self-paced, can start any time":
        return "В своём темпе — можно начать сразу"
    return text


def _gap(m: re.Match[str], skill_name: Callable[[str], str]) -> str:
    critical = " (обязательный навык)" if m[7] else ""
    return f"{skill_name(m[1])}: {m[2]} → {m[3]}, для цели нужно {m[4]}{critical}"


def _target(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    return f"Занятие рассчитано на уровень «{_role(m[1], m[2])}» — {PURPOSES.get(m[3], m[3])}"


def _grade_fit(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    return f"Рассчитано на текущий уровень «{GRADE_LABELS[m[1]]}», база для уровня «{GRADE_LABELS[m[2]]}»"


def _goal(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    return f"Карьерная цель: {_role(m[1], m[2])}"


def _avoid(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    scope = "по тем же навыкам" if m[3] is None else f"того же типа ({EVENT_TYPE_LABELS.get(m[3], m[3])})"
    statuses = ", ".join(_status(item) for item in m[4].split(", "))
    return f"Не завершено похожих занятий {scope}: {m[1]} ({statuses})"


def _status(item: str) -> str:
    status, _, count = item.partition(" ×")
    return f"{STATUS_LABELS.get(status, status).lower()} ×{count}"


def _engage(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    return f"Похожих занятий завершено: {m[1]}, из них по своей инициативе: {m[2]}"


def _feedback(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    return f"Средняя оценка похожих занятий: {m[2]} из 5 (оценок: {m[1]})"


def _session(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    return f"Ближайшая сессия {m[3]}.{m[2]}.{m[1]}, через {m[4]} дн."


def _duration(m: re.Match[str], _skill_name: Callable[[str], str]) -> str:
    return f"Длительность {m[1]} ч — на {m[2]} ч больше обычных {m[3]} ч"


_RENDERERS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str], Callable[[str], str]], str]], ...] = (
    (GAP, _gap),
    (GRADE_FIT, _grade_fit),
    (TARGET, _target),
    (GOAL, _goal),
    (AVOID, _avoid),
    (ENGAGE, _engage),
    (FEEDBACK, _feedback),
    (SESSION, _session),
    (DURATION, _duration),
)
