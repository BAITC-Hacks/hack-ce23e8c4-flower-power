import io
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from career_quest import api

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
