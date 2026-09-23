import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from career_quest import api
from career_quest.assistant import answer

DEMO_DIR = Path(__file__).resolve().parents[1] / "demo"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for variable in ("CQ_EMPLOYEE_ID", "CQ_EMPLOYEE_PASSWORD", "CQ_HR_PASSWORD", "OPENAI_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    api._SESSIONS.clear()
    with TestClient(api.app) as test_client:
        yield test_client


def login(client: TestClient, role: str = "hr", password: str = "") -> None:
    response = client.post("/api/session", json={"role": role, "password": password})
    assert response.status_code == 200, response.text


def test_web_client_is_served(client: TestClient) -> None:
    page = client.get("/")

    assert page.status_code == 200
    assert "Career Quest" in page.text
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_requires_login_before_data(client: TestClient) -> None:
    assert client.get("/api/session").json()["viewer"] is None
    assert client.get("/api/employees").status_code == 401


def test_profile_has_recommendations_with_three_factors_and_quest(client: TestClient) -> None:
    login(client)

    profile = client.get("/api/employees/E0001").json()

    assert profile["employee"]["id"] == "E0001"
    assert 1 <= len(profile["recommendations"]) <= 3
    assert all(len(rec["factors"]) >= 3 for rec in profile["recommendations"])
    assert profile["quest"]["badges_total"] == 7


def test_complete_updates_progress_and_blocks_repeat_click(client: TestClient) -> None:
    login(client)
    event_id = client.get("/api/employees/E0001").json()["recommendations"][0]["event_id"]

    first = client.post("/api/employees/E0001/complete", json={"event_id": event_id})
    second = client.post("/api/employees/E0001/complete", json={"event_id": event_id})

    assert first.status_code == 200
    assert first.json()["changes"]
    assert second.status_code == 409
    assert event_id not in {r["event_id"] for r in client.get("/api/employees/E0001").json()["recommendations"]}


def test_coach_offline_and_goal_change(client: TestClient) -> None:
    login(client, role="employee")

    suggestion = client.post("/api/employees/E0001/coach", json={"wish": "хочу стать тимлидом в аналитике"}).json()
    chosen = suggestion["suggestion"]
    body = {"target_role": chosen["target_role"], "target_grade": chosen["target_grade"]}
    changed = client.post("/api/employees/E0001/goal", json=body)

    assert (chosen["target_role"], chosen["target_grade"], chosen["source"]) == ("Data Analyst", "Lead", "keywords")
    assert changed.status_code == 200
    assert "Аналитик данных" in client.get("/api/employees/E0001").json()["quest"]["target"]


def test_hr_report_is_forbidden_for_employees(client: TestClient) -> None:
    login(client, role="employee")

    assert client.get("/api/hr").status_code == 403


def test_hr_report_and_trap_upload(client: TestClient) -> None:
    login(client)
    files = {
        "employees_file": ("trap_employees.json", (DEMO_DIR / "trap_employees.json").read_bytes()),
        "history_file": ("trap_history.csv", (DEMO_DIR / "trap_history.csv").read_bytes()),
    }

    report = client.get("/api/hr").json()
    uploaded = client.post("/api/data/additions", files=files)
    trap = client.get("/api/employees/TRAP_1").json()

    assert report["totals"]["employees"] == 200
    assert report["deficits"]
    assert uploaded.status_code == 200
    assert uploaded.json()["employees"] == 203
    assert trap["recommendations"][0]["event_id"] == "EV_006"


def test_export_contains_four_files(client: TestClient) -> None:
    login(client)

    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/data/export").content))

    assert sorted(archive.namelist()) == ["activity_history.csv", "employees.json", "events.json", "skills.json"]


def test_configured_access_limits_employee_to_own_profile(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CQ_EMPLOYEE_ID", "E0005")
    monkeypatch.setenv("CQ_EMPLOYEE_PASSWORD", "employee-pass")
    monkeypatch.setenv("CQ_HR_PASSWORD", "hr-pass")

    assert client.post("/api/session", json={"role": "hr", "password": "wrong"}).status_code == 401
    login(client, role="employee", password="employee-pass")  # noqa: S106 - test secret

    assert [e["id"] for e in client.get("/api/employees").json()] == ["E0005"]
    assert client.get("/api/employees/E0001").status_code == 403
    assert client.get("/api/employees/E0005").status_code == 200


def test_hr_sees_neutral_wording_and_cannot_set_goals(client: TestClient) -> None:
    login(client)

    profile = client.get("/api/employees/E0001").json()
    labels = " ".join(f["label"] for rec in profile["recommendations"] for f in rec["factors"])
    coach = client.post("/api/employees/E0001/coach", json={"wish": "хочу в аналитику"})
    goal = client.post("/api/employees/E0001/goal", json={"target_role": "Data Analyst", "target_grade": "Senior"})

    assert profile["own"] is False
    assert "ваш" not in labels.lower()
    assert " вы " not in f" {labels.lower()} "
    assert coach.status_code == 403
    assert goal.status_code == 403


def test_factor_details_are_translated(client: TestClient) -> None:
    login(client, role="employee")

    factors = [f for rec in client.get("/api/employees/E0001").json()["recommendations"] for f in rec["factors"]]

    assert factors
    for factor in factors:
        assert not any(word in factor["detail_ru"] for word in ("required", "Designed", "earlier", "Takes", "session"))


def test_assistant_repeated_request_is_cached_and_does_not_mutate_goal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(client, role="employee")
    original = answer
    calls: list[str] = []

    def tracked(*args: object, **kwargs: object) -> object:
        calls.append("call")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(api, "answer", tracked)
    body = {"wish": "Каких навыков мне не хватает?", "language": "ru"}
    first = client.post("/api/employees/E0001/assistant", json=body)
    second = client.post("/api/employees/E0001/assistant", json=body)
    assert first.status_code == 200
    assert first.json()["text"]
    assert first.json() == second.json()
    assert calls == ["call"]
    other = client.post("/api/employees/E0002/assistant", json=body)
    assert other.status_code == 200
    assert len(calls) == 2


def test_assistant_rejects_blank_and_oversized_input(client: TestClient) -> None:
    login(client, role="employee")
    for wish in ("   ", "x" * 501):
        assert client.post("/api/employees/E0001/assistant", json={"wish": wish}).status_code == 422


def test_assistant_uses_access_control(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CQ_EMPLOYEE_ID", "E0005")
    monkeypatch.setenv("CQ_EMPLOYEE_PASSWORD", "employee-pass")
    monkeypatch.setenv("CQ_HR_PASSWORD", "hr-pass")
    login(client, role="employee", password="employee-pass")  # noqa: S106 - test secret
    assert client.post("/api/employees/E0001/assistant", json={"wish": "Анализ навыков"}).status_code == 403


def test_explanations_are_cached_per_profile(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    login(client)
    calls: list[str] = []

    def fake_explain(*args: object) -> str:
        del args
        calls.append("call")
        return "Saved explanation"

    monkeypatch.setattr(api, "explain", fake_explain)
    event = client.get("/api/employees/E0001").json()["recommendations"][0]["event_id"]
    for _ in range(2):
        response = client.post("/api/employees/E0001/explain", json={"event_id": event})
        assert response.json()["text"] == "Saved explanation"
    assert calls == ["call"]


def test_assistant_session_limit_prevents_provider_call(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    login(client, role="employee")
    session = next(iter(api._SESSIONS.values()))
    session.ai_calls = 40
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def forbidden_call(*args: object) -> object:
        del args
        raise AssertionError("AI must not run after the session limit")

    monkeypatch.setattr("career_quest.assistant.post", forbidden_call)
    response = client.post("/api/employees/E0001/assistant", json={"wish": "Какие навыки развивать?"})
    assert response.status_code == 200
    assert response.json()["status"] == "limit"
    assert response.json()["source"] == "local"
    assert session.ai_calls == 40


def test_goal_change_clears_old_dialogue_and_keeps_budget(client: TestClient) -> None:
    login(client, role="employee")
    client.post("/api/employees/E0001/assistant", json={"wish": "Какие навыки развивать?"})
    session = next(iter(api._SESSIONS.values()))
    session.ai_calls = 3
    assert session.dialogue
    changed = client.post("/api/employees/E0001/goal", json={"target_role": "Data Analyst", "target_grade": "Senior"})
    assert changed.status_code == 200
    assert not session.dialogue
    assert not session.ai_cache
    assert session.ai_calls == 3


def test_team_lead_dialogue_keeps_unconfirmed_goal_separate(client: TestClient) -> None:
    login(client, role="employee")
    original = client.get("/api/employees/E0001").json()["employee"]["goal"]
    first = client.post("/api/employees/E0001/assistant", json={"wish": "хочу стать тимлидом"}).json()
    followup = client.post("/api/employees/E0001/assistant", json={"wish": "что мне сделать?"}).json()
    assert first["suggestion"]["target_grade"] == "Lead"
    assert followup["suggestion"] == first["suggestion"]
    assert followup["details"] == first["details"]
    assert "EV_" not in followup["text"]
    assert client.get("/api/employees/E0001").json()["employee"]["goal"] == original


def test_activity_chats_are_scoped_to_each_recommendation(client: TestClient) -> None:
    login(client, role="employee")
    recs = client.get("/api/employees/E0001").json()["recommendations"]
    assert len(recs) >= 2
    first, second = recs[:2]
    for rec in (first, second):
        result = client.post(
            "/api/employees/E0001/assistant",
            json={"wish": "Почему мне подходит это занятие?", "event_id": rec["event_id"]},
        )
        assert result.status_code == 200
        assert rec["original_title"] in result.json()["details"]
        other = second if rec == first else first
        assert other["event_id"] not in result.json()["details"]
    session = next(iter(api._SESSIONS.values()))
    assert len(session.dialogue) == 2
    assert all(len(history) == 2 for history in session.dialogue.values())


def test_activity_chat_rejects_unrecommended_event(client: TestClient) -> None:
    login(client, role="employee")
    response = client.post("/api/employees/E0001/assistant", json={"wish": "Почему?", "event_id": "EV_UNKNOWN"})
    assert response.status_code == 404


def test_social_language_switch_preserves_thread_without_using_budget(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(client, "employee")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    for message, language in [("hi", "en"), ("сәлем", "kk"), ("привет", "ru")]:
        response = client.post("/api/employees/E0001/assistant", json={"wish": message})
        assert response.status_code == 200
        assert response.json()["language"] == language
        assert response.json()["status"] == "social"
    session = next(iter(api._SESSIONS.values()))
    assert session.ai_calls == 0
    assert len(session.dialogue) == 1
    assert len(next(iter(session.dialogue.values()))) == 6


def test_employee_list_and_profile_show_current_role_and_target(client: TestClient) -> None:
    login(client)

    people = {e["id"]: e for e in client.get("/api/employees").json()}
    profile = client.get("/api/employees/E0001").json()["employee"]

    assert people["E0001"]["current"] == "Разработчик серверной части · Начинающий"
    assert people["E0001"]["target_kind"] == "goal"
    assert people["E0001"]["target"] == "Разработчик серверной части · Самостоятельный специалист"
    assert (profile["current"], profile["target"], profile["target_kind"]) == (
        people["E0001"]["current"],
        people["E0001"]["target"],
        "goal",
    )
    assert {e["target_kind"] for e in people.values()} <= {"goal", "next", "none"}
