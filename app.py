"""Streamlit employee/HR application against the shared scoring public contracts.

Run with `uv run streamlit run app.py`. The app requires the team's scoring.py.
CQ_DATA_DIR overrides the bundled data directory. CQ_EMPLOYEE_ID,
CQ_EMPLOYEE_PASSWORD and CQ_HR_PASSWORD enable the configured access gate;
without them a clearly labeled demo role switch is shown. State is isolated
per browser session; HR can export a snapshot before closing the session.
"""

import hashlib
import os
from collections import Counter
from pathlib import Path
from typing import cast

import pandas as pd
import streamlit as st
import structlog

from career_quest.access import Viewer, authenticate, can_view_employee, configured_access, require_hr
from career_quest.data import Dataset, DatasetError, load_dataset
from career_quest.explain import deterministic_explanation, explain, llm_configured
from career_quest.models import Employee, Language
from career_quest.scoring import Recommendation, complete_activity, effective_skills, recommend, target_profile
from career_quest.snapshot import FILENAMES, export_snapshot, import_additions, import_snapshot

log = structlog.get_logger(__name__)
DATA_DIR = Path(os.environ.get("CQ_DATA_DIR", str(Path(__file__).resolve().parent / "data")))
STATUS_LABELS = {
    "completed": "Завершено",
    "in_progress": "В процессе",
    "dropped": "Прекращено",
    "no_show": "Пропуск",
    "declined": "Отказ",
    "overdue": "Просрочено",
}
FORMAT_LABELS = {"online": "Онлайн", "offline": "Очно", "self_paced": "В своём темпе"}


def _login() -> Viewer | None:
    try:
        secured = configured_access()
    except ValueError as exc:
        st.error(str(exc))
        return None
    if not secured:
        st.warning("Деморежим: переключение ролей открыто. Не используйте реальные персональные данные.")
        role = st.sidebar.radio("Режим просмотра", ["Сотрудник", "HR"], key="demo_role")
        return Viewer("hr" if role == "HR" else "employee", None, demo=True)
    if "viewer" in st.session_state:
        if st.sidebar.button("Выйти"):
            st.session_state.clear()
            st.rerun()
        return cast(Viewer, st.session_state["viewer"])
    st.subheader("Вход")
    with st.form("login"):
        role = st.selectbox("Роль", ["Сотрудник", "HR"])
        password = st.text_input("Пароль", type="password")
        submitted = st.form_submit_button("Войти", type="primary")
    if submitted:
        viewer = authenticate("hr" if role == "HR" else "employee", password)
        if viewer is not None:
            st.session_state["viewer"] = viewer
            st.rerun()
        st.error("Неверные данные для выбранной роли")
    return None


def _dataset() -> Dataset:
    if "dataset" not in st.session_state:
        try:
            st.session_state["dataset"] = load_dataset(DATA_DIR)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("initial_dataset_failed", error_type=type(exc).__name__)
            st.error("Не удалось прочитать набор данных. Проверьте CQ_DATA_DIR и четыре файла стартового кита.")
            st.stop()
    return cast(Dataset, st.session_state["dataset"])


def _replace_dataset(dataset: Dataset, message: str) -> None:
    st.session_state["dataset"] = dataset
    st.session_state["revision"] = int(st.session_state.get("revision", 0)) + 1
    st.session_state["explanations"] = {}
    st.session_state.pop("hr_report", None)
    st.session_state["notice"] = message
    st.rerun()


def _employee_picker(dataset: Dataset, viewer: Viewer) -> Employee:
    allowed = [employee for employee in dataset.employees if can_view_employee(viewer, employee.employee_id)]
    if not allowed:
        st.error("Для текущей учётной записи профиль не найден")
        st.stop()
    labels = {item.employee_id: f"{item.full_name} · {item.role} · {item.grade}" for item in allowed}
    identifier = st.sidebar.selectbox("Профиль", list(labels), format_func=labels.__getitem__)
    return dataset.employee(identifier)


def _skill_table(dataset: Dataset, employee: Employee) -> pd.DataFrame:
    levels = effective_skills(dataset, employee.employee_id)
    target = target_profile(dataset, employee.employee_id)
    if target is None:
        return pd.DataFrame(
            [{"Навык": dataset.skill(identifier).name, "Уровень": level} for identifier, level in levels.items()]
        )
    return pd.DataFrame(
        [
            {
                "Навык": dataset.skill(identifier).name,
                "Сейчас": levels.get(identifier, 0),
                "Цель": required,
                "Осталось": max(0, required - levels.get(identifier, 0)),
                "Критический": "Да" if identifier in target.critical_skills else "Нет",
            }
            for identifier, required in target.required_skills.items()
        ]
    )


def _trajectory(dataset: Dataset, employee: Employee) -> None:
    target = target_profile(dataset, employee.employee_id)
    levels = effective_skills(dataset, employee.employee_id)
    st.subheader("Траектория развития")
    if target is None:
        st.info("Карьерная цель не определена. Доступны текущие навыки и история участия.")
    else:
        st.write(f"{employee.role} / {employee.grade} → {target.role} / {target.grade}")
        required = sum(target.required_skills.values())
        reached = sum(min(levels.get(key, 0), value) for key, value in target.required_skills.items())
        coverage = reached / required if required else 1.0
        remaining = sum(levels.get(key, 0) < target.required_skills[key] for key in target.critical_skills)
        first, second = st.columns(2)
        first.metric("Покрытие требований", f"{coverage:.0%}")
        second.metric("Критических навыков с дефицитом", remaining)
        st.progress(coverage)
        st.caption("Покрытие уровней навыков из датасета. Это не вероятность и не гарантия повышения.")
    st.dataframe(_skill_table(dataset, employee), hide_index=True, width="stretch")


def _explanation(rec: Recommendation, employee: Employee, language: Language) -> None:
    identity = f"{employee.employee_id}:{employee.role}:{employee.grade}:{language}:{rec.model_dump_json()}"
    key = hashlib.sha256(identity.encode()).hexdigest()
    explanations = cast(dict[str, str], st.session_state.setdefault("explanations", {}))
    if llm_configured() and st.button("Упорядочить аргументы с AI", key=f"ai_{key}"):
        with st.spinner("AI выбирает порядок аргументов…"):
            explanations[key] = explain(rec, employee, language)
    text = explanations.get(key, deterministic_explanation(rec, employee, language))
    st.text(text)
    with st.expander("Факторы и вклад в оценку"):
        st.dataframe(
            pd.DataFrame(
                [{"Фактор": factor.code, "Вклад": factor.weight, "Факт": factor.detail} for factor in rec.factors]
            ),
            hide_index=True,
            width="stretch",
        )


def _complete(dataset: Dataset, employee: Employee, rec: Recommendation, viewer: Viewer) -> None:
    if not can_view_employee(viewer, employee.employee_id):
        st.error("Нет доступа к этому профилю")
        return
    try:
        before = effective_skills(dataset, employee.employee_id)
        updated = complete_activity(dataset, employee.employee_id, rec.event_id)
        after = effective_skills(updated, employee.employee_id)
    except (ValueError, KeyError) as exc:
        log.warning("completion_failed", event_id=rec.event_id, error_type=type(exc).__name__)
        st.error(f"Не удалось завершить активность: {exc}")
        return
    changes = [
        f"{dataset.skill(key).name}: {before.get(key, 0)} → {value}"
        for key, value in after.items()
        if value != before.get(key, 0)
    ]
    message = "Прогресс обновлён. " + ("; ".join(changes) if changes else "Уровни навыков не изменились.")
    _replace_dataset(updated, message)


def _recommendations(dataset: Dataset, employee: Employee, viewer: Viewer) -> None:
    st.subheader("Следующие шаги")
    language = cast(Language, st.selectbox("Язык заголовков объяснения", ["ru", "kk", "en"]))
    st.caption(
        "Факты показаны на языке датасета. Подбор активностей выполняет расчётный модуль; LLM упорядочивает объяснение."
    )
    candidates = recommend(dataset, employee.employee_id)
    if not candidates:
        st.info(
            "Подходящих шагов сейчас нет: проверьте цель, достигнутые уровни, требования участия и доступные сессии."
        )
    for rec in candidates:
        event = dataset.event(rec.event_id)
        with st.container(border=True):
            st.subheader(event.title)
            session = rec.next_session.isoformat() if rec.next_session else "доступно в любое время"
            st.caption(f"{FORMAT_LABELS[event.event_format]} · {event.duration_hours:g} ч · {session}")
            st.write(event.description)
            st.write(
                "Ожидаемое изменение: "
                + "; ".join(
                    f"{dataset.skill(key).name}: {before} → {after}"
                    for key, (before, after) in rec.skill_changes.items()
                )
            )
            _explanation(rec, employee, language)
            if st.button("Смоделировать завершение", key=f"complete_{employee.employee_id}_{rec.event_id}"):
                _complete(dataset, employee, rec, viewer)
    st.caption("Завершение моделируется в текущей сессии приложения, без подтверждения из учебной системы.")


def _history(dataset: Dataset, employee: Employee) -> None:
    rows = dataset.history_for(employee.employee_id)
    with st.expander(f"История участия · {len(rows)} записей"):
        if not rows:
            st.info("Истории участия пока нет")
            return
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Дата": row.session_date,
                        "Активность": dataset.event(row.event_id).title,
                        "Статус": STATUS_LABELS[row.status],
                        "Прогресс, %": row.completion_pct,
                    }
                    for row in reversed(rows)
                ]
            ),
            hide_index=True,
            width="stretch",
        )


def _employee_view(dataset: Dataset, viewer: Viewer) -> None:
    employee = _employee_picker(dataset, viewer)
    st.header(employee.full_name)
    st.caption(f"{employee.department} · {employee.role} · {employee.grade} · стаж {employee.tenure_months} мес.")
    _trajectory(dataset, employee)
    _recommendations(dataset, employee, viewer)
    _history(dataset, employee)


def _hr_metrics(dataset: Dataset) -> tuple[pd.DataFrame, pd.DataFrame]:
    deficits: Counter[str] = Counter()
    critical: Counter[str] = Counter()
    no_steps = []
    for employee in dataset.employees:
        levels = effective_skills(dataset, employee.employee_id)
        target = target_profile(dataset, employee.employee_id)
        if target:
            for identifier, required in target.required_skills.items():
                if levels.get(identifier, 0) < required:
                    deficits[identifier] += 1
                    critical[identifier] += identifier in target.critical_skills
        if not recommend(dataset, employee.employee_id):
            no_steps.append({"Сотрудник": employee.full_name, "ID": employee.employee_id, "Роль": employee.role})
    rows = [
        {"Навык": dataset.skill(key).name, "Сотрудников с дефицитом": count, "Из них критический": critical[key]}
        for key, count in deficits.most_common()
    ]
    return pd.DataFrame(rows), pd.DataFrame(no_steps)


def _participation(dataset: Dataset) -> pd.DataFrame:
    rows = []
    for event in dataset.events:
        if event.mandatory:
            continue
        records = [record for record in dataset.history if record.event_id == event.event_id]
        completed = sum(record.status == "completed" for record in records)
        rows.append(
            {
                "Активность": event.title,
                "Участников": len({record.employee_id for record in records}),
                "Записей участия": len(records),
                "Завершений": completed,
                "Доля завершений": completed / len(records) if records else 0.0,
            }
        )
    return pd.DataFrame(rows)


def _hr_view(dataset: Dataset, viewer: Viewer) -> None:
    require_hr(viewer)
    st.header("Обзор развития команды")
    first, second, third = st.columns(3)
    first.metric("Сотрудников", len(dataset.employees))
    second.metric("Добровольных активностей", sum(not event.mandatory for event in dataset.events))
    third.metric("Записей участия", len(dataset.history))
    if "hr_report" not in st.session_state:
        with st.spinner("Считаем дефициты и доступные шаги…"):
            st.session_state["hr_report"] = _hr_metrics(dataset)
    deficits, no_steps = cast(tuple[pd.DataFrame, pd.DataFrame], st.session_state["hr_report"])
    st.subheader("Дефициты навыков относительно карьерных целей")
    st.dataframe(deficits, hide_index=True, width="stretch")
    st.subheader("Нет подходящего следующего шага")
    st.caption("Это не оценка вовлечённости: цель может быть достигнута или в каталоге нет подходящих событий.")
    st.dataframe(no_steps, hide_index=True, width="stretch")
    st.subheader("Участие в добровольных активностях")
    st.dataframe(_participation(dataset), hide_index=True, width="stretch")


def _additions_form(dataset: Dataset, viewer: Viewer) -> None:
    with st.form("jury_additions"):
        st.write("Добавить проверочные профили и историю жюри")
        st.caption("Совпадающие ID заменяются по правилам загрузчика. Каталог событий и навыков сохраняется.")
        employees = st.file_uploader("Дополнительные employees.json", type="json")
        history = st.file_uploader("Дополнительная activity_history.csv", type="csv")
        submitted = st.form_submit_button("Добавить профили и историю", type="primary")
    if submitted:
        try:
            updated = import_additions(
                viewer, dataset, employees.getvalue() if employees else b"", history.getvalue() if history else b""
            )
        except (DatasetError, ValueError, TypeError) as exc:
            st.error(str(exc))
        else:
            _replace_dataset(updated, "Профили и история загружены. Рекомендации пересчитаны.")


def _full_import_form(viewer: Viewer) -> None:
    with st.form("full_dataset"):
        st.write("Заменить весь набор данных")
        uploads = {
            name: st.file_uploader(name, type="csv" if name.endswith("csv") else "json", key=f"full_{name}")
            for name in FILENAMES
        }
        confirmed = st.checkbox("Заменить текущий набор и несохранённый прогресс этой сессии")
        submitted = st.form_submit_button("Загрузить четыре файла")
    if submitted:
        if not confirmed:
            st.error("Подтвердите замену текущего набора")
            return
        try:
            files = {name: upload.getvalue() for name, upload in uploads.items() if upload is not None}
            updated = import_snapshot(viewer, files)
        except (DatasetError, ValueError, OSError) as exc:
            st.error(str(exc))
        else:
            _replace_dataset(updated, "Новый набор данных загружен")


def _data_view(dataset: Dataset, viewer: Viewer) -> None:
    require_hr(viewer)
    st.header("Данные и проверка жюри")
    st.info("Изменения живут в текущей сессии браузера. Скачайте снимок, чтобы сохранить результат.")
    st.download_button(
        "Скачать снимок данных (ZIP)",
        export_snapshot(viewer, dataset),
        file_name="career-quest-snapshot.zip",
        mime="application/zip",
    )
    st.caption("Для восстановления распакуйте архив и загрузите его четыре файла ниже.")
    _additions_form(dataset, viewer)
    with st.expander("Импорт полного набора"):
        _full_import_form(viewer)


def main() -> None:
    """Run the role-scoped Streamlit application using shared data/scoring services."""
    st.set_page_config(page_title="Career Quest", page_icon="🌱", layout="wide")
    st.title("🌱 Career Quest")
    st.caption("Понятный следующий шаг в профессиональном развитии")
    viewer = _login()
    if viewer is None:
        return
    dataset = _dataset()
    st.sidebar.caption(f"Дата среза: {dataset.as_of_date:%d.%m.%Y}")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    pages = ["Мой профиль"] if viewer.role == "employee" else ["Обзор HR", "Профиль сотрудника", "Данные"]
    page = st.sidebar.radio("Раздел", pages)
    if page == "Обзор HR":
        _hr_view(dataset, viewer)
    elif page == "Данные":
        _data_view(dataset, viewer)
    else:
        _employee_view(dataset, viewer)


if __name__ == "__main__":
    main()
