import json
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from career_quest import explain as explanation
from career_quest.data import load_dataset
from career_quest.models import Employee
from career_quest.scoring import Factor, Recommendation


@pytest.fixture
def employee() -> Employee:
    return load_dataset(Path(__file__).resolve().parents[1] / "data").employee("E0001")


@pytest.fixture
def rec() -> Recommendation:
    return Recommendation(
        event_id="EV_005",
        score=4.5,
        skill_changes={"SK_SYSTEM_DESIGN": (2, 3)},
        next_session=None,
        factors=[
            Factor(
                code="critical_gap",
                weight=3,
                detail="SK_SYSTEM_DESIGN 2 → 3, required 4 for Backend Engineer Senior (critical)",
            ),
            Factor(
                code="history_avoidance",
                weight=-1,
                detail="3 earlier activities on the same skills not finished (no_show ×3)",
            ),
            Factor(code="goal_alignment", weight=0.5, detail="Serves the career goal Backend Engineer Senior"),
        ],
    )


def valid_statements() -> list[dict[str, object]]:
    return [
        {"factor_index": 0, "text": "SK_SYSTEM_DESIGN вырастет с 2 до 3, при требовании 4 для Senior."},
        {"factor_index": 1, "text": "Ранее были пропущены 3 активности по этим навыкам."},
        {"factor_index": 2, "text": "Активность соответствует цели Backend Engineer Senior."},
    ]


def mock_response(monkeypatch: pytest.MonkeyPatch, body: dict[str, object]) -> MagicMock:
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(body).encode()
    monkeypatch.setattr(urllib.request, "build_opener", MagicMock(return_value=opener))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-a-real-secret")
    return opener


def envelope(statements: list[dict[str, object]]) -> dict[str, object]:
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning"},
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps({"statements": statements})}]},
        ],
    }


def test_without_key_no_request_is_made(
    rec: Recommendation, employee: Employee, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    network = MagicMock(side_effect=AssertionError("network must not be used"))
    monkeypatch.setattr(urllib.request, "build_opener", network)
    text = explanation.explain(rec, employee, "ru")
    assert "без LLM" in text
    assert all(factor.detail in text for factor in rec.factors)
    network.assert_not_called()


def test_openai_request_and_grounded_response(
    rec: Recommendation,
    employee: Employee,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = mock_response(monkeypatch, envelope(valid_statements()))
    text = explanation.explain(rec, employee, "ru")
    assert text.startswith("AI-объяснение")
    assert "[1] SK_SYSTEM_DESIGN вырастет с 2 до 3" in text
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://api.openai.com/v1/responses"
    payload = json.loads(request.data)
    assert payload["store"] is False
    assert payload["text"]["format"]["strict"] is True
    assert employee.full_name not in request.data.decode()
    assert employee.employee_id not in request.data.decode()
    assert opener.open.call_args.kwargs["timeout"] == 8


@pytest.mark.parametrize(
    "bad_text",
    [
        "SK_SYSTEM_DESIGN вырастет с 2 до 5, при требовании 4 для Senior.",
        "SK_PYTHON вырастет с 2 до 3, при требовании 4 для Senior.",
        "SK_SYSTEM_DESIGN вырастет с 2 до 3, при требовании 4 для Lead.",
    ],
)
def test_changed_facts_trigger_fallback(
    bad_text: str,
    rec: Recommendation,
    employee: Employee,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements = valid_statements()
    statements[0]["text"] = bad_text
    mock_response(monkeypatch, envelope(statements))
    assert explanation.explain(rec, employee, "ru").endswith(explanation.deterministic_explanation(rec, employee, "ru"))


@pytest.mark.parametrize("index", [0, 99])
def test_duplicate_or_unknown_factor_triggers_fallback(
    index: int,
    rec: Recommendation,
    employee: Employee,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statements = valid_statements()
    statements[1]["factor_index"] = index
    mock_response(monkeypatch, envelope(statements))
    assert "без LLM" in explanation.explain(rec, employee, "ru")


@pytest.mark.parametrize(
    "body",
    [
        {"status": "incomplete", "output": []},
        {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
        {"status": "completed", "output": []},
    ],
)
def test_incomplete_refused_or_empty_response_falls_back(
    body: dict[str, object],
    rec: Recommendation,
    employee: Employee,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response(monkeypatch, body)
    assert "без LLM" in explanation.explain(rec, employee, "ru")


def test_timeout_falls_back(rec: Recommendation, employee: Employee, monkeypatch: pytest.MonkeyPatch) -> None:
    opener = mock_response(monkeypatch, {})
    opener.open.side_effect = TimeoutError("timed out")
    assert "без LLM" in explanation.explain(rec, employee, "ru")


def test_negative_factor_cannot_be_hidden(
    rec: Recommendation,
    employee: Employee,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = rec.model_copy(update={"factors": [*rec.factors, Factor(code="availability", weight=1, detail="Self-paced")]})
    statements = valid_statements()
    statements[1] = {"factor_index": 3, "text": "Можно начать в удобное время."}
    mock_response(monkeypatch, envelope(statements))
    assert "без LLM" in explanation.explain(rec, employee, "ru")
