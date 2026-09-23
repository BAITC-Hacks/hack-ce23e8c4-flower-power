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
from career_quest.coach import GoalSuggestion, set_goal, suggest_goal
from career_quest.data import Dataset, DatasetError, load_dataset
from career_quest.explain import deterministic_explanation, explain, llm_configured
from career_quest.models import CareerGoal, Employee, Language
from career_quest.quest import BADGES, Quest, build_quest, coverage_gain
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
BADGE_LABELS = {
    "first_step": ("🌱", "Первый шаг", "Завершена первая добровольная активность"),
    "self_starter": ("🚀", "По своей инициативе", "Активность выбрана самостоятельно"),
    "comeback": ("🔁", "Возвращение", "Вернулись к навыку, который раньше не получилось развить"),
    "grown_since_review": ("📈", "Рост после оценки", "Навыки выросли после последней оценки"),
    "critical_closed": ("🎯", "Ключевой навык", "Обязательный навык цели на нужном уровне"),
    "halfway": ("⛰️", "Полпути", "Покрыта половина требований цели"),
    "ready": ("🏆", "Готов к цели", "Все обязательные навыки цели на нужном уровне"),
}
MAX_QUEST_STEPS = 6
STYLE = """
<style>
#MainMenu, footer, [data-testid="stDecoration"] {visibility: hidden;}
.block-container {padding-top: 2.2rem; max-width: 1180px;}
h1, h2, h3 {letter-spacing: -0.01em; color: #10261E;}
[data-testid="stSidebar"] {background: #EEF5F1; border-right: 1px solid #DCE8E1;}
[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E3EAE6; border-radius: 16px;
  padding: 14px 18px; box-shadow: 0 1px 2px rgba(16, 40, 30, .05);}
.cq-brand {display: flex; align-items: baseline; gap: 12px; margin-bottom: 18px;}
.cq-brand b {font-size: 26px; color: #0B5D45;}
.cq-brand span {color: #5B6B64; font-size: 15px;}
.cq-hero {background: linear-gradient(135deg, #0E7C5A 0%, #0A4F3B 100%); color: #FFFFFF; border-radius: 22px;
  padding: 26px 30px; display: flex; gap: 30px; align-items: center; margin: 6px 0 22px;
  box-shadow: 0 10px 30px rgba(10, 79, 59, .18);}
.cq-ring {flex: none; width: 128px; height: 128px; border-radius: 50%; display: grid; place-items: center;
  background: conic-gradient(#F5B83D calc(var(--p) * 1%), rgba(255, 255, 255, .16) 0);}
.cq-ring div {width: 100px; height: 100px; border-radius: 50%; background: #0B5A44; display: grid;
  place-items: center; text-align: center; font-size: 26px; font-weight: 700; line-height: 1.1;}
.cq-ring small {display: block; font-size: 11px; font-weight: 400; opacity: .8;}
.cq-path {font-size: 13px; text-transform: uppercase; letter-spacing: .06em; opacity: .75;}
.cq-goal {font-size: 22px; font-weight: 700; margin: 4px 0 2px;}
.cq-note {font-size: 13px; opacity: .75;}
.cq-stats {display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap;}
.cq-stat {background: rgba(255, 255, 255, .12); border-radius: 12px; padding: 8px 14px; font-size: 13px;}
.cq-stat b {display: block; font-size: 20px;}
.cq-steps {display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; margin-bottom: 8px;}
.cq-step {background: #FFFFFF; border: 1px solid #E3EAE6; border-radius: 14px; padding: 12px 14px;}
.cq-step.critical {border-left: 4px solid #F5B83D;}
.cq-step.done {background: #E8F5EE; border-color: #BFE3D0;}
.cq-step b {font-size: 14px; color: #10261E;}
.cq-step small {color: #5B6B64; display: block; margin-top: 2px;}
.cq-bar {height: 6px; background: #E3EAE6; border-radius: 3px; margin-top: 8px; overflow: hidden;}
.cq-bar i {display: block; height: 100%; background: #0E7C5A; border-radius: 3px;}
.cq-badges {display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px;}
.cq-badge {display: flex; gap: 8px; align-items: center; background: #FFFFFF; border: 1px solid #E3EAE6;
  border-radius: 999px; padding: 5px 14px 5px 8px; font-size: 14px;}
.cq-badge.locked {opacity: .45; filter: grayscale(1);}
.cq-badge span {font-size: 20px;}
.cq-rec-top {display: inline-block; background: #F5B83D; color: #3D2A00; font-size: 12px; font-weight: 600;
  border-radius: 999px; padding: 2px 10px; margin-bottom: 6px;}
.cq-plus {display: inline-block; background: #0E7C5A; color: #FFFFFF; font-weight: 600; font-size: 13px;
  border-radius: 999px; padding: 3px 10px; margin: 0 6px 6px 0;}
.cq-rec-title {font-size: 21px; font-weight: 700; color: #10261E; margin-bottom: 6px;}
.cq-chip {display: inline-block; background: #EEF5F1; color: #0B5D45; border-radius: 999px; padding: 3px 10px;
  font-size: 13px; margin: 0 6px 6px 0;}
.cq-gain {display: inline-block; background: #FFF6E0; border: 1px solid #F3DDA3; border-radius: 10px;
  padding: 4px 10px; margin: 2px 6px 6px 0; font-size: 14px;}
</style>
"""


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


def _hero(employee: Employee, quest: Quest | None) -> None:
    if quest is None:
        st.info("Карьерная цель не определена. Доступны текущие навыки и история участия.")
        return
    percent = round(quest.coverage * 100)
    critical_left = sum(not m.done for m in quest.milestones if m.critical)
    source = "ваша карьерная цель" if employee.career_goal else "следующий уровень"
    st.markdown(
        f"""<div class="cq-hero">
<div class="cq-ring" style="--p: {percent}"><div>{percent}%<small>к цели</small></div></div>
<div>
<div class="cq-path">{escape(_role_label(employee.role, employee.grade))} → {source}</div>
<div class="cq-goal">{escape(_role_label(quest.target_role, quest.target_grade))}</div>
<div class="cq-note">Покрытие требуемых уровней навыков — не вероятность и не гарантия повышения.</div>
<div class="cq-stats">
<div class="cq-stat"><b>{critical_left}</b>обязательных навыков осталось</div>
<div class="cq-stat"><b>+{quest.growth_points}</b>уровней роста после оценки</div>
<div class="cq-stat"><b>{len(quest.badges)} из {len(BADGES)}</b>личных достижений</div>
</div></div></div>""",
        unsafe_allow_html=True,
    )


def _quest(dataset: Dataset, quest: Quest | None) -> None:
    if quest is None:
        return
    st.subheader("Ваш квест")
    open_steps = [m for m in quest.milestones if not m.done][:MAX_QUEST_STEPS]
    if open_steps:
        cards = "".join(
            f'<div class="cq-step{" critical" if m.critical else ""}"><b>{escape(_skill_name(dataset, m.skill_id))}</b>'
            f"<small>{'Обязателен для цели · ' if m.critical else ''}уровень {m.current} из {m.required}</small>"
            f'<div class="cq-bar"><i style="width: {100 * m.current // m.required}%"></i></div></div>'
            for m in open_steps
        )
        st.markdown(f'<div class="cq-steps">{cards}</div>', unsafe_allow_html=True)
        left = sum(not m.done for m in quest.milestones) - len(open_steps)
        if left > 0:
            st.caption(f"И ещё {left} навыков — полный список в саду навыков ниже.")
    else:
        st.success("Все требования цели выполнены.")
    badges = "".join(
        f'<div class="cq-badge{"" if code in quest.badges else " locked"}" title="{escape(description)}">'
        f"<span>{icon}</span>{escape(title)}</div>"
        for code, (icon, title, description) in BADGE_LABELS.items()
    )
    st.markdown(f'<div class="cq-badges">{badges}</div>', unsafe_allow_html=True)
    st.caption("Достижения личные: они не сравниваются с коллегами и не влияют на оценку.")


def _coach(dataset: Dataset, employee: Employee, viewer: Viewer) -> None:
    suggestions = cast(dict[str, GoalSuggestion | None], st.session_state.setdefault("coach", {}))
    with st.container(border=True):
        st.markdown("**🤖 AI-коуч: кем вы хотите стать?**")
        st.caption(
            "Напишите своими словами — на русском, қазақша или English. "
            "Коуч выберет цель только из справочника ролей банка; в AI уходят лишь ваш текст и текущая роль."
        )
        with st.form(f"coach_{employee.employee_id}", border=False):
            wish = st.text_input("Ваша цель", placeholder="Например: хочу через год стать тимлидом в аналитике")
            asked = st.form_submit_button("Подобрать цель")
        if asked:
            with st.spinner("Коуч подбирает цель…"):
                suggestions[employee.employee_id] = suggest_goal(dataset, employee.employee_id, wish, "ru")
        if employee.employee_id not in suggestions:
            return
        suggestion = suggestions[employee.employee_id]
        if suggestion is None:
            st.info("Не нашли подходящую роль в справочнике. Попробуйте назвать направление или уровень.")
            return
        source = "AI" if suggestion.source == "ai" else "по ключевым словам (без AI)"
        st.markdown(
            f"Предлагаемая цель: **{escape(_role_label(suggestion.target_role, suggestion.target_grade))}**  \n"
            f"{escape(suggestion.reason)} · _подобрано {source}_"
        )
        if st.button("Сделать целью и пересчитать шаги", key=f"set_goal_{employee.employee_id}", type="primary"):
            if not can_view_employee(viewer, employee.employee_id):
                st.error("Нет доступа к этому профилю")
                return
            goal = CareerGoal(target_role=suggestion.target_role, target_grade=suggestion.target_grade)
            suggestions.pop(employee.employee_id)
            _replace_dataset(
                set_goal(dataset, employee.employee_id, goal),
                f"Цель обновлена: {_role_label(goal.target_role, goal.target_grade)}. Шаги пересчитаны.",
            )


def _trajectory(dataset: Dataset, employee: Employee) -> None:
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
    old_quest = build_quest(dataset, employee.employee_id)
    new_quest = build_quest(updated, employee.employee_id)
    earned = [code for code in (new_quest.badges if new_quest else []) if not old_quest or code not in old_quest.badges]
    if earned:
        message += " 🏅 Новое достижение: " + ", ".join(BADGE_LABELS[code][1] for code in earned) + "."
        st.session_state["celebrate"] = True
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
    dataset: Dataset,
    employee: Employee,
    rec: Recommendation,
    viewer: Viewer,
    language: Language,
    *,
    quest: Quest | None,
    top: bool = False,
) -> None:
    event = dataset.event(rec.event_id)
    with st.container(border=True):
        session = rec.next_session.strftime("%d.%m.%Y") if rec.next_session else "в любое время"
        chips = [FORMAT_LABELS[event.event_format], f"{event.duration_hours:g} ч", f"Старт: {session}"]
        gain = coverage_gain(quest, rec.skill_changes) if quest else 0.0
        progress = f'<span class="cq-plus">+{gain:.0%} к цели</span>' if gain > 0 else ""
        gains = "".join(
            f'<span class="cq-gain">{_hint(_skill_name(dataset, key), f"{dataset.skill(key).name} · {key}")}'
            f" {before} → <b>{after}</b></span>"
            for key, (before, after) in rec.skill_changes.items()
        )
        title = _hint(EVENT_LABELS.get(event.title, event.title), f"{event.title} · {event.event_id}")
        badge = '<div class="cq-rec-top">Лучший следующий шаг</div>' if top else ""
        st.markdown(
            f'{badge}<div class="cq-rec-title">{title}</div>{progress}'
            + "".join(f'<span class="cq-chip">{escape(chip)}</span>' for chip in chips)
            + f"<div>{gains}</div>",
            unsafe_allow_html=True,
        )
        st.write("Почему этот шаг подходит")
        _explanation(rec, employee, language)
        with st.expander("Описание занятия и демонстрация завершения"):
            st.text(f"{event.title} · {event.event_id}")
            st.text(event.description)
            st.caption("Тестовое завершение изменяет только текущую сессию. Записи в учебной системе не создаются.")
            _completion_button(dataset, employee, rec, viewer)


def _recommendations(dataset: Dataset, employee: Employee, viewer: Viewer, quest: Quest | None) -> None:
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
    _recommendation_card(dataset, employee, candidates[0], viewer, language, quest=quest, top=True)
    if len(candidates) > 1:
        with st.expander(f"Другие варианты · {len(candidates) - 1}"):
            for rec in candidates[1:]:
                _recommendation_card(dataset, employee, rec, viewer, language, quest=quest)


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
    quest = build_quest(dataset, employee.employee_id)
    _hero(employee, quest)
    _coach(dataset, employee, viewer)
    _recommendations(dataset, employee, viewer, quest)
    _quest(dataset, quest)
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
    st.markdown(STYLE, unsafe_allow_html=True)
    st.markdown(
        '<div class="cq-brand"><b>🌱 Career Quest</b>'
        "<span>Понятный следующий шаг в профессиональном развитии</span></div>",
        unsafe_allow_html=True,
    )
    viewer = _login()
    if viewer is None:
        return
    dataset = _dataset()
    st.sidebar.caption(f"Дата среза: {dataset.as_of_date:%d.%m.%Y}")
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    if st.session_state.pop("celebrate", False):
        st.balloons()
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
