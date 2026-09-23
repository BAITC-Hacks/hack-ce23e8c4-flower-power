import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from career_quest.access import Viewer
from career_quest.scoring import effective_skills
from career_quest.snapshot import import_additions

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> AppTest:
    for variable in ("CQ_EMPLOYEE_ID", "CQ_EMPLOYEE_PASSWORD", "CQ_HR_PASSWORD", "OPENAI_API_KEY", "CQ_DATA_DIR"):
        monkeypatch.delenv(variable, raising=False)
    return AppTest.from_file(str(APP), default_timeout=10)


def test_actual_scoring_completion_updates_profile(app: AppTest) -> None:
    app.run()
    assert not app.exception
    dataset = app.session_state["dataset"]
    employee_id = dataset.employees[0].employee_id
    before = effective_skills(dataset, employee_id)
    next(button for button in app.button if button.label == "Смоделировать завершение").click().run()
    assert not app.exception
    after = effective_skills(app.session_state["dataset"], employee_id)
    assert before != after
    assert any("Прогресс обновлён" in message.value for message in app.success)


def test_hr_reports_and_import_forms_render(app: AppTest) -> None:
    app.run()
    app.sidebar.radio[0].set_value("HR").run()
    assert not app.exception
    assert any(header.value == "Обзор развития команды" for header in app.header)
    app.sidebar.radio[1].set_value("Данные").run()
    assert not app.exception
    assert any(header.value == "Данные и проверка жюри" for header in app.header)
    assert any(button.label == "Добавить профили и историю" for button in app.button)


def test_employee_login_limits_profile_and_logout_clears_data(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CQ_EMPLOYEE_ID", "E0028")
    monkeypatch.setenv("CQ_EMPLOYEE_PASSWORD", "test-employee")
    monkeypatch.setenv("CQ_HR_PASSWORD", "test-hr")
    app.run()
    assert not app.exception
    assert not app.dataframe
    app.text_input[0].set_value("test-employee")
    next(button for button in app.button if button.label == "Войти").click().run()
    assert not app.exception
    assert len(app.sidebar.selectbox[0].options) == 1
    assert "Обзор HR" not in app.sidebar.radio[0].options
    next(button for button in app.button if button.label == "Выйти").click().run()
    assert not app.dataframe


def test_wrong_hr_password_does_not_open_reports(app: AppTest, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CQ_EMPLOYEE_ID", "E0028")
    monkeypatch.setenv("CQ_EMPLOYEE_PASSWORD", "test-employee")
    monkeypatch.setenv("CQ_HR_PASSWORD", "test-hr")
    app.run()
    app.selectbox[0].set_value("HR")
    app.text_input[0].set_value("wrong-password")
    next(button for button in app.button if button.label == "Войти").click().run()
    assert not app.exception
    assert not app.dataframe
    assert any("Неверные данные" in message.value for message in app.error)


def test_three_jury_profiles_are_selectable_after_import(app: AppTest) -> None:
    app.run()
    dataset = app.session_state["dataset"]
    base = dataset.employees[0].model_dump(mode="json")
    profiles = [{**base, "employee_id": f"JURY_{index}", "full_name": f"Jury profile {index}"} for index in range(3)]
    raw = json.dumps({"employees": profiles}).encode()
    history = (
        b"record_id,employee_id,event_id,date,due_date,status,completion_pct,score,feedback_rating,assigned_by\n"
        b"JURY_R0,JURY_0,EV_036,2026-09-20,,no_show,0,,,self\n"
        b"JURY_R1,JURY_1,EV_036,2026-09-20,,no_show,0,,,self\n"
        b"JURY_R2,JURY_2,EV_036,2026-09-20,,no_show,0,,,self\n"
    )
    updated = import_additions(Viewer("hr", None, demo=True), dataset, raw, history)
    app.session_state["dataset"] = updated
    app.run()
    assert not app.exception
    assert len(app.sidebar.selectbox[0].options) == 203
    app.sidebar.selectbox[0].set_value("JURY_0").run()
    assert not app.exception
    assert any(header.value == "Jury profile 0" for header in app.header)


def test_club_completion_button_is_disabled_after_one_record_today(app: AppTest) -> None:
    app.run()
    identifier = app.session_state["dataset"].employees[0].employee_id
    button = app.button(key=f"complete_{identifier}_EV_036")
    button.click().run()
    assert not app.exception
    buttons = [button for button in app.button if button.key == f"complete_{identifier}_EV_036"]
    assert not buttons or buttons[0].disabled
    records = [
        record
        for record in app.session_state["dataset"].history_for(identifier)
        if record.event_id == "EV_036" and record.session_date == app.session_state["dataset"].as_of_date
    ]
    assert len(records) == 1
