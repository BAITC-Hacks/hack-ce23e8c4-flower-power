import json
from typing import Any

import pytest
from test_scoring import dataset, make_employee

from career_quest import coach
from career_quest.data import Dataset
from career_quest.models import CareerGoal
from career_quest.scoring import recommend, target_profile

__all__ = ["dataset"]


@pytest.fixture
def ds(dataset: Dataset, monkeypatch: pytest.MonkeyPatch) -> Dataset:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return dataset.with_additions([make_employee(dataset, skills={}, grade="Middle")], [])


def model_reply(data: dict[str, str]) -> dict[str, Any]:
    text = json.dumps(data)
    return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]}


@pytest.mark.parametrize(
    ("wish", "role", "grade"),
    [
        ("Хочу стать тимлидом в аналитике данных", "Data Analyst", "Lead"),
        ("хочу расти в своей профессии", None, None),
        ("хочу стать сеньором", "Backend Engineer", "Senior"),
        ("перейти в продакт менеджмент", "Product Manager", "Middle"),
        ("I want to become a QA lead", "QA Engineer", "Lead"),
    ],
)
def test_keyword_goal_offline(ds: Dataset, wish: str, role: str | None, grade: str | None) -> None:
    suggestion = coach.suggest_goal(ds, "T0001", wish, "ru")

    if role is None:
        assert suggestion is None
    else:
        assert suggestion is not None
        assert (suggestion.target_role, suggestion.target_grade, suggestion.source) == (role, grade, "keywords")


def test_ai_goal_is_used_when_valid(ds: Dataset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    reply = {"target_role": "Data Analyst", "target_grade": "Senior", "reason": "Хочет работать с данными."}
    monkeypatch.setattr(coach, "_post", lambda _payload: model_reply(reply))

    suggestion = coach.suggest_goal(ds, "T0001", "люблю копаться в цифрах", "ru")

    assert suggestion is not None
    assert (suggestion.target_role, suggestion.target_grade, suggestion.source) == ("Data Analyst", "Senior", "ai")


def test_ai_goal_outside_catalog_falls_back_to_keywords(ds: Dataset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    reply = {"target_role": "Astronaut", "target_grade": "Lead", "reason": "x"}
    monkeypatch.setattr(coach, "_post", lambda _payload: model_reply(reply))

    suggestion = coach.suggest_goal(ds, "T0001", "хочу в аналитику", "ru")

    assert suggestion is not None
    assert (suggestion.target_role, suggestion.source) == ("Data Analyst", "keywords")


def test_ai_no_match_returns_none(ds: Dataset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    reply = {"target_role": coach.NO_MATCH, "target_grade": "Lead", "reason": "x"}
    monkeypatch.setattr(coach, "_post", lambda _payload: model_reply(reply))

    assert coach.suggest_goal(ds, "T0001", "хочу в отпуск", "ru") is None


def test_set_goal_changes_target_and_recommendations(ds: Dataset) -> None:
    before = {rec.event_id for rec in recommend(ds, "T0001")}

    updated = coach.set_goal(ds, "T0001", CareerGoal(target_role="Data Analyst", target_grade="Senior"))

    profile = target_profile(updated, "T0001")
    assert profile is not None
    assert (profile.role, profile.grade) == ("Data Analyst", "Senior")
    assert {rec.event_id for rec in recommend(updated, "T0001")} != before
    assert ds.employee("T0001").career_goal is None


def test_set_goal_rejects_unknown_profile(ds: Dataset) -> None:
    with pytest.raises(KeyError):
        coach.set_goal(ds, "T0001", CareerGoal(target_role="Astronaut", target_grade="Lead"))
