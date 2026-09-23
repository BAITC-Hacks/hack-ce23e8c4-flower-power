import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from career_quest import assistant
from career_quest.data import Dataset, load_dataset
from career_quest.llm import AIUnavailableError, output_text


@pytest.fixture
def ds(monkeypatch: pytest.MonkeyPatch) -> Dataset:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return load_dataset(Path(__file__).resolve().parents[1] / "data")


def envelope(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(decision)}]}],
    }


def test_offline_analysis_returns_current_facts_without_network(ds: Dataset, monkeypatch: pytest.MonkeyPatch) -> None:
    network = MagicMock(side_effect=AssertionError("No network permitted"))
    monkeypatch.setattr(assistant, "post", network)
    reply = assistant.answer(ds, "E0001", "Каких навыков мне не хватает?")
    assert reply.source == "local"
    assert reply.status == "missing_key"
    assert reply.intent == "gaps"
    assert reply.text.strip()
    network.assert_not_called()


def test_followup_and_mixed_language_are_sent_with_scoped_evidence(
    ds: Dataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    evidence = assistant.context(ds, "E0001")
    event = evidence["recommendations"][0]["event_id"]
    mock = MagicMock(
        return_value=envelope({"intent": "explain", "skill_ids": [], "event_ids": [event], "goal_key": ""})
    )
    monkeypatch.setattr(assistant, "post", mock)
    history = [{"role": "assistant", "text": f"Обсуждаем {event}"}]
    reply = assistant.answer(ds, "E0001", "А почему именно он, түсіндірші?", "kk", history)
    payload = mock.call_args.args[0]
    data = json.loads(payload["input"])
    assert data["dialogue"] == history
    assert data["language"] == "kk"
    assert ds.employee("E0001").full_name not in payload["input"]
    assert "employee_id" not in data["evidence"]
    assert reply.source == "ai"
    assert event in reply.text
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 300


@pytest.mark.parametrize("intent", ["clarify", "out_of_scope"])
def test_scope_and_ambiguity_never_apply_goal(ds: Dataset, monkeypatch: pytest.MonkeyPatch, intent: str) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        assistant, "post", lambda _: envelope({"intent": intent, "skill_ids": [], "event_ids": [], "goal_key": ""})
    )
    original = ds.employee("E0001").career_goal
    reply = assistant.answer(ds, "E0001", "Продажи или аналитика? А ещё расскажи анекдот")
    assert reply.intent == intent
    assert reply.suggestion is None
    assert ds.employee("E0001").career_goal == original
    assert reply.text


@pytest.mark.parametrize(
    "body",
    [
        {"status": "incomplete", "output": []},
        {"status": "completed", "output": []},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
        {"status": "completed", "output": None},
    ],
)
def test_empty_incomplete_and_refused_responses_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(AIUnavailableError):
        output_text(body)


def test_hallucinated_event_yields_visible_fallback(ds: Dataset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        assistant,
        "post",
        lambda _: envelope({"intent": "explain", "skill_ids": [], "event_ids": ["EV_INVENTED"], "goal_key": ""}),
    )
    reply = assistant.answer(ds, "E0001", "Объясни рекомендацию")
    assert reply.source == "local"
    assert reply.status == "invalid_evidence"
    assert "EV_INVENTED" not in reply.text


def test_quota_error_is_visible_without_provider_body(ds: Dataset, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(assistant, "post", MagicMock(side_effect=AIUnavailableError("quota")))
    reply = assistant.answer(ds, "E0001", "Какие навыки развить?")
    assert reply.status == "quota"
    assert "лимит" in reply.text
    assert reply.intent == "gaps"
