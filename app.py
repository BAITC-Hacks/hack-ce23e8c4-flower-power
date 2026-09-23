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
from html import escape
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


LEVEL_LABELS = {
    0: "Ещё не знаком",
    1: "Знаю основы",
    2: "Решаю типовые задачи с поддержкой",
    3: "Работаю самостоятельно",
    4: "Решаю сложные задачи и помогаю другим",
    5: "Задаю стандарты",
}
ROLE_LABELS = {
    "Backend Engineer": "Разработчик серверной части",
    "Frontend Engineer": "Разработчик интерфейсов",
    "Data Analyst": "Аналитик данных",
    "QA Engineer": "Инженер по качеству",
    "Product Manager": "Менеджер продукта",
    "HR Business Partner": "HR-партнёр",
    "Sales Manager": "Менеджер по продажам",
    "Customer Support Specialist": "Специалист поддержки",
}
GRADE_LABELS = {
    "Junior": "Начинающий",
    "Middle": "Самостоятельный специалист",
    "Senior": "Старший специалист",
    "Lead": "Ведущий специалист",
}
SKILL_LABELS = {
    "Python": "Программирование на Python",
    "Java": "Программирование на Java",
    "SQL": "Запросы к данным (SQL)",
    "API Design": "Проектирование API",
    "System Design": "Проектирование систем",
    "Cloud Platforms": "Облачные платформы",
    "Containers & Orchestration": "Контейнеры и управление ими",
    "CI/CD": "Автоматизация сборки и выпуска",
    "Application Security": "Безопасность приложений",
    "Observability": "Мониторинг работы систем",
    "JavaScript": "Программирование на JavaScript",
    "TypeScript": "Программирование на TypeScript",
    "React": "Интерфейсы на React",
    "HTML & CSS": "Вёрстка страниц",
    "Web Performance": "Быстродействие сайтов",
    "Web Accessibility": "Доступность интерфейсов",
    "Test Design": "Проектирование тестов",
    "Test Automation": "Автоматизация тестирования",
    "API Testing": "Тестирование API",
    "Load Testing": "Нагрузочное тестирование",
    "Statistics": "Статистика",
    "A/B Testing": "A/B-тестирование",
    "Data Visualization": "Визуализация данных",
    "BI Tools": "Инструменты бизнес-аналитики",
    "Data Modeling": "Моделирование данных",
    "Machine Learning Fundamentals": "Основы машинного обучения",
    "Product Discovery": "Поиск потребностей пользователей",
    "Roadmapping & Prioritization": "Планирование и приоритеты",
    "Product Analytics": "Продуктовая аналитика",
    "UX Research": "Исследование опыта пользователей",
    "Requirements Writing": "Описание требований",
    "Agile Practices": "Гибкие методы работы",
    "Project Management": "Управление проектами",
    "Talent Acquisition": "Подбор сотрудников",
    "Employee Relations": "Взаимоотношения с сотрудниками",
    "Labor Law": "Трудовое право",
    "HR Analytics": "HR-аналитика",
    "Learning Program Design": "Разработка учебных программ",
    "Compensation & Benefits": "Оплата труда и льготы",
    "Prospecting": "Поиск клиентов",
    "Negotiation": "Переговоры",
    "CRM Systems": "Системы работы с клиентами (CRM)",
    "Account Management": "Развитие отношений с клиентами",
    "Product Knowledge": "Знание продукта",
    "Customer Service": "Клиентский сервис",
    "Technical Troubleshooting": "Решение технических проблем",
    "Communication": "Общение",
    "Public Speaking": "Публичные выступления",
    "Written Communication": "Письменное общение",
    "Stakeholder Management": "Работа с заинтересованными сторонами",
    "Leadership": "Лидерство",
    "Mentoring": "Наставничество",
    "Feedback": "Обратная связь",
    "Conflict Resolution": "Разрешение конфликтов",
    "Teamwork": "Командная работа",
    "Emotional Intelligence": "Эмоциональный интеллект",
    "Problem Solving": "Решение проблем",
    "Critical Thinking": "Критическое мышление",
    "Time Management": "Управление временем",
    "Adaptability": "Адаптивность",
}
EVENT_LABELS = {
    "Information Security Awareness": "Информационная безопасность",
    "Personal Data Protection": "Защита персональных данных",
    "Code of Conduct & Workplace Safety": "Кодекс поведения и безопасность труда",
    "New Employee Onboarding": "Знакомство с компанией",
    "System Design Fundamentals": "Основы проектирования систем",
    "Designing High-Load Systems": "Проектирование высоконагруженных систем",
    "Architecture Review Circle": "Клуб разбора архитектуры",
    "Business Writing & Documentation": "Деловая переписка и документация",
    "Cloud Certification Prep": "Подготовка к сертификации по облачным технологиям",
    "Kubernetes in Practice": "Kubernetes на практике",
    "Secure Coding Workshop": "Практикум по безопасной разработке",
    "Advanced Python": "Продвинутый Python",
    "TypeScript in Depth": "Углублённый TypeScript",
    "Web Performance Deep Dive": "Углублённая оптимизация сайтов",
    "Web Performance Fundamentals": "Основы быстродействия сайтов",
    "Accessible Interfaces": "Доступные интерфейсы",
    "React Patterns & State Management": "Подходы к разработке на React и управление состоянием",
    "Test Automation Bootcamp": "Интенсив по автоматизации тестирования",
    "API & Performance Testing Workshop": "Практикум по API и нагрузочному тестированию",
    "Applied Statistics for Analysts": "Прикладная статистика для аналитиков",
    "A/B Testing Workshop": "Практикум по A/B-тестированию",
    "SQL & BI for Analytics": "SQL и бизнес-аналитика",
    "Data Storytelling & Visualization": "Как рассказывать истории с помощью данных",
    "Machine Learning for Analysts": "Машинное обучение для аналитиков",
    "Dimensional Data Modeling": "Многомерное моделирование данных",
    "Product Discovery Lab": "Лаборатория исследования потребностей пользователей",
    "Roadmapping & Agile Planning": "Планирование продукта и гибкие методы работы",
    "Labor Law & Employee Relations": "Трудовое право и отношения с сотрудниками",
    "People Analytics & Total Rewards": "HR-аналитика и система вознаграждений",
    "Structured Interviewing": "Структурированные собеседования",
    "Designing Learning Programs": "Разработка учебных программ",
    "Negotiation Masterclass": "Мастер-класс по переговорам",
    "Consultative Selling & Prospecting": "Консультативные продажи и поиск клиентов",
    "Handling Difficult Conversations": "Как вести сложные разговоры",
    "Technical Troubleshooting Academy": "Решение технических проблем",
    "Public Speaking Club": "Клуб публичных выступлений",
    "Mentor Track": "Программа наставничества",
    "Leadership Foundations": "Основы лидерства",
    "Time & Priority Management": "Управление временем и приоритетами",
    "Structured Problem Solving": "Системный подход к решению проблем",
}
FACTOR_LABELS = {
    "critical_gap": "Развивает навык, обязательный для карьерной цели.",
    "required_gap": "Сокращает расстояние до нужного уровня навыков.",
    "grade_fit": "Подходит вашему текущему профессиональному уровню.",
    "target_alignment": "Подходит роли и уровню вашей карьерной цели.",
    "goal_alignment": "Связан с указанной вами карьерной целью.",
    "history_avoidance": "Похожие занятия вы ранее пропускали, отклоняли или не завершали — это снижает приоритет.",
    "engagement": "Учтён ваш опыт завершения похожих занятий.",
    "feedback": "Учтены ваши оценки похожих занятий.",
    "availability": "Учтено, когда можно начать занятие.",
    "duration": "Учтено время на занятие: длительные активности получают меньший приоритет.",
}


def _hint(label: str, original: str) -> str:
    return f'<abbr tabindex="0" title="{escape(original, quote=True)}">{escape(label)}</abbr>'


def _skill_name(dataset: Dataset, identifier: str) -> str:
    original = dataset.skill(identifier).name
    return SKILL_LABELS.get(original, original)


def _role_label(role: str, grade: str) -> str:
    return f"{ROLE_LABELS.get(role, role)} · {GRADE_LABELS.get(grade, grade)}"


def _level_hint(level: int) -> str:
    return _hint(LEVEL_LABELS[level], f"Уровень {level} из 5")


def _garden(dataset: Dataset, employee: Employee) -> None:
    target = target_profile(dataset, employee.employee_id)
    levels = effective_skills(dataset, employee.employee_id)
    st.subheader("Ваш сад навыков")
    st.caption("Каждый цветок — навык. Рост отражает уровень, а цветение — достижение цели по этому навыку.")
    identifiers = list(target.required_skills) if target else list(levels)
    columns = st.columns(3)
    for index, identifier in enumerate(identifiers):
        current = levels.get(identifier, 0)
        required = target.required_skills[identifier] if target else None
        icon = "🌸" if required is not None and current >= required else ("🌿" if current >= 2 else "🌱")
        original = f"{dataset.skill(identifier).name} · {identifier} · сейчас {current}/5"
        if required is not None:
            original += f" · цель {required}/5"
        with columns[index % 3].container(border=True):
            st.markdown(f"{icon} **{_hint(_skill_name(dataset, identifier), original)}**", unsafe_allow_html=True)
            st.markdown(f"Сейчас: {_level_hint(current)}", unsafe_allow_html=True)
            if required is not None:
                st.markdown(f"Цель: {_level_hint(required)}", unsafe_allow_html=True)
                if current >= required:
                    st.caption("✓ Цель по навыку достигнута")
                elif target and identifier in target.critical_skills:
                    st.caption("Приоритет: обязателен для цели")
    with st.expander("Все исходные названия, коды и уровни"):
        st.caption("Подсказки доступны при наведении на подчёркнутый текст. На телефоне используйте эту таблицу.")
        st.dataframe(_skill_table(dataset, employee), hide_index=True, width="stretch")
        for level, label in LEVEL_LABELS.items():
            st.write(f"{level} — {label}")


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
    st.session_state.pop("hr_report", None)
    st.session_state["notice"] = message
    st.rerun()


def _employee_picker(dataset: Dataset, viewer: Viewer) -> Employee:
    allowed = [employee for employee in dataset.employees if can_view_employee(viewer, employee.employee_id)]
    if not allowed:
        st.error("Для текущей учётной записи профиль не найден")
        st.stop()
    labels = {item.employee_id: f"{item.full_name} · {_role_label(item.role, item.grade)}" for item in allowed}
    identifier = st.sidebar.selectbox("Профиль", list(labels), format_func=labels.__getitem__)
    return dataset.employee(identifier)


def _skill_table(dataset: Dataset, employee: Employee) -> pd.DataFrame:
    levels = effective_skills(dataset, employee.employee_id)
    target = target_profile(dataset, employee.employee_id)
    if target is None:
        return pd.DataFrame(
            [
                {
                    "Навык": _skill_name(dataset, identifier),
                    "Исходное название": dataset.skill(identifier).name,
                    "Код": identifier,
                    "Уровень": level,
                }
                for identifier, level in levels.items()
            ]
        )
    return pd.DataFrame(
        [
            {
                "Навык": _skill_name(dataset, identifier),
                "Исходное название": dataset.skill(identifier).name,
                "Код": identifier,
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
        st.markdown(
            _hint(_role_label(employee.role, employee.grade), f"{employee.role} / {employee.grade}")
            + " → "
            + _hint(_role_label(target.role, target.grade), f"{target.role} / {target.grade}"),
            unsafe_allow_html=True,
        )
        required = sum(target.required_skills.values())
        reached = sum(min(levels.get(key, 0), value) for key, value in target.required_skills.items())
        coverage = reached / required if required else 1.0
        remaining = sum(levels.get(key, 0) < target.required_skills[key] for key in target.critical_skills)
        first, second = st.columns(2)
        first.metric(
            "Прогресс к цели по навыкам",
            f"{coverage:.0%}",
            help="Сумма достигнутых уровней, ограниченных требованиями, делится на сумму требуемых уровней.",
        )
        second.metric(
            "Обязательных навыков нужно подтянуть",
            remaining,
            help="Количество навыков из critical_skills, по которым текущий уровень ниже целевого.",
        )
        st.progress(coverage)
        st.caption("Покрытие уровней навыков из датасета. Это не вероятность и не гарантия повышения.")
    _garden(dataset, employee)


def _explanation(rec: Recommendation, employee: Employee, language: Language) -> None:
    identity = f"{employee.employee_id}:{employee.role}:{employee.grade}:{language}:{rec.model_dump_json()}"
    key = hashlib.sha256(identity.encode()).hexdigest()
    explanations = cast(dict[str, str], st.session_state.setdefault("explanations", {}))
    if llm_configured() and st.button("Объяснить с AI", key=f"ai_{key}", disabled=key in explanations):
        with st.spinner("AI объясняет рекомендацию…"):
            explanations[key] = explain(rec, employee, language)
        st.rerun()
    if key in explanations:
        st.text(explanations[key])
        st.caption("Объяснение сохранено для этой рекомендации в текущей сессии.")
    else:
        for factor in rec.factors:
            st.write("• " + FACTOR_LABELS.get(factor.code, factor.detail))
    with st.expander("Как рассчитано: исходные факторы и числа"):
        st.caption(f"Оценка подбора: {rec.score:g}. Это сумма вкладов факторов, не процент успеха.")
        st.text(deterministic_explanation(rec, employee, language))
        st.dataframe(
            pd.DataFrame(
                [
                    {"№": index + 1, "Фактор": factor.code, "Вклад": factor.weight, "Факт": factor.detail}
                    for index, factor in enumerate(rec.factors)
                ]
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
        f"{_skill_name(dataset, key)}: {LEVEL_LABELS[before.get(key, 0)]} → {LEVEL_LABELS[value]}"
        for key, value in after.items()
        if value != before.get(key, 0)
    ]
    message = "Прогресс обновлён. " + ("; ".join(changes) if changes else "Уровни навыков не изменились.")
    _replace_dataset(updated, message)


def _completion_button(dataset: Dataset, employee: Employee, rec: Recommendation, viewer: Viewer) -> None:
    recorded_today = any(
        record.event_id == rec.event_id and record.status == "completed" and record.session_date == dataset.as_of_date
        for record in dataset.history_for(employee.employee_id)
    )
    if st.button(
        "Смоделировать завершение", key=f"complete_{employee.employee_id}_{rec.event_id}", disabled=recorded_today
    ):
        _complete(dataset, employee, rec, viewer)
    if recorded_today:
        st.caption("Завершение этой активности уже записано на дату текущего среза.")


def _recommendation_card(
    dataset: Dataset, employee: Employee, rec: Recommendation, viewer: Viewer, language: Language
) -> None:
    event = dataset.event(rec.event_id)
    with st.container(border=True):
        st.markdown(
            "### " + _hint(EVENT_LABELS.get(event.title, event.title), f"{event.title} · {event.event_id}"),
            unsafe_allow_html=True,
        )
        session = rec.next_session.strftime("%d.%m.%Y") if rec.next_session else "можно начать в любое время"
        st.caption(f"{FORMAT_LABELS[event.event_format]} · {event.duration_hours:g} ч · {session}")
        st.write("После завершения вы сможете:")
        for key, (before, after) in rec.skill_changes.items():
            original = f"{dataset.skill(key).name} · {key} · {before} → {after}"
            st.markdown(
                _hint(_skill_name(dataset, key), original) + ": " + _level_hint(before) + " → " + _level_hint(after),
                unsafe_allow_html=True,
            )
        st.write("Почему этот шаг подходит")
        _explanation(rec, employee, language)
        with st.expander("Описание занятия и демонстрация завершения"):
            st.text(f"{event.title} · {event.event_id}")
            st.text(event.description)
            st.caption("Тестовое завершение изменяет только текущую сессию. Записи в учебной системе не создаются.")
            _completion_button(dataset, employee, rec, viewer)


def _recommendations(dataset: Dataset, employee: Employee, viewer: Viewer) -> None:
    st.subheader("Ваш следующий шаг")
    st.caption("Начните с первого варианта или посмотрите другие: участие добровольное.")
    language = cast(
        Language,
        st.selectbox(
            "Язык AI-объяснения",
            ["ru", "kk", "en"],
            format_func={"ru": "Русский", "kk": "Қазақша", "en": "English"}.__getitem__,
        ),
    )
    candidates = recommend(dataset, employee.employee_id)
    if not candidates:
        st.info("Подходящих шагов сейчас нет: цель может быть достигнута или в каталоге нет подходящих занятий.")
        return
    _recommendation_card(dataset, employee, candidates[0], viewer, language)
    if len(candidates) > 1:
        with st.expander(f"Другие варианты · {len(candidates) - 1}"):
            for rec in candidates[1:]:
                _recommendation_card(dataset, employee, rec, viewer, language)


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
                        "Активность": EVENT_LABELS.get(
                            dataset.event(row.event_id).title, dataset.event(row.event_id).title
                        ),
                        "Исходное название": dataset.event(row.event_id).title,
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
    st.caption(
        f"{employee.department} · {_role_label(employee.role, employee.grade)} · стаж {employee.tenure_months} мес."
    )
    _recommendations(dataset, employee, viewer)
    _trajectory(dataset, employee)
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
        {
            "Навык": _skill_name(dataset, key),
            "Исходное название": dataset.skill(key).name,
            "Нужно развить": count,
            "Обязателен для цели": critical[key],
        }
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
                "Активность": EVENT_LABELS.get(event.title, event.title),
                "Исходное название": event.title,
                "Участников": len({record.employee_id for record in records}),
                "Записей участия": len(records),
                "Завершений": completed,
                "Завершено, %": 100 * completed / len(records) if records else 0.0,
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
    st.caption("Процент завершений — завершённые записи / все записи участия, включая повторные посещения.")
    st.dataframe(
        _participation(dataset),
        hide_index=True,
        width="stretch",
        column_config={"Завершено, %": st.column_config.NumberColumn(format="%.1f %%")},
    )


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
    with st.expander("Настройка AI"):
        st.write("Задайте OPENAI_API_KEY в окружении перед запуском. Не добавляйте ключ в Git.")
        st.code("uv run streamlit run app.py", language="bash")
        st.caption("Модель по умолчанию: gpt-4.1-mini. Изменить: OPENAI_MODEL.")
        st.caption("В OpenAI уходят только факторы рекомендации, без имени и полной истории сотрудника.")
        st.caption("При ошибке модели показываем исходные расчётные факторы.")
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
