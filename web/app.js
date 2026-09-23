"use strict";

const state = {
  session: null,
  employees: [],
  filter: "",
  profile: null,
  profileId: null,
  hr: null,
  lang: "ru",
  explanations: {},
  suggestion: undefined,
  dialogue: [],
  wish: "",
  loginRole: "employee",
  busy: "",
};

const root = document.getElementById("root");

// ---------- helpers ----------
const h = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (value) => `${Math.round(value * 100)}%`;
const date = (iso) => (iso ? iso.split("-").reverse().join(".") : "в любое время");
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
  return many;
};

async function api(path, options = {}) {
  const init = { credentials: "same-origin", ...options };
  if (options.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.json);
  }
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Ошибка ${response.status}`);
  return data;
}

function toast(message, isError = false) {
  document.querySelectorAll(".toast").forEach((node) => node.remove());
  const node = document.createElement("div");
  node.className = `toast${isError ? " error" : ""}`;
  node.textContent = message;
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 6000);
}

function confetti() {
  const layer = document.createElement("div");
  layer.className = "confetti";
  const colors = ["#0e7c5a", "#f5b83d", "#139068", "#ffd98a", "#0a3d2f"];
  for (let i = 0; i < 90; i += 1) {
    const piece = document.createElement("i");
    piece.style.left = `${Math.random() * 100}%`;
    piece.style.background = colors[i % colors.length];
    piece.style.animationDelay = `${Math.random() * 0.6}s`;
    piece.style.transform = `rotate(${Math.random() * 360}deg)`;
    layer.appendChild(piece);
  }
  document.body.appendChild(layer);
  setTimeout(() => layer.remove(), 3200);
}

const spinner = '<span class="spinner"></span>';

// One scale everywhere: the icon shows the absolute level, never whether the goal is met.
const LEVEL_ICONS = [
  [4, "🌸", "высокий уровень (4–5)"],
  [2, "🌿", "средний уровень (2–3)"],
  [0, "🌱", "начальный уровень (0–1)"],
];
const levelIcon = (level) => LEVEL_ICONS.find(([min]) => level >= min)[1];
const STATUS_ICONS = {
  completed: "✓",
  in_progress: "⏳",
  overdue: "⚠",
  dropped: "✕",
  no_show: "✕",
  declined: "✕",
};

// ---------- data loading ----------
async function loadSession() {
  state.session = await api("/api/session");
  if (state.session.viewer) state.employees = await api("/api/employees");
}

async function loadProfile(id) {
  state.explanations = {};
  state.suggestion = undefined;
  state.dialogue = [];
  state.wish = "";
  state.profile = await api(`/api/employees/${encodeURIComponent(id)}`);
  state.profileId = id;
}

function route() {
  const [page, id] = location.hash.replace(/^#\/?/, "").split("/");
  return { page: page || "", id: id ? decodeURIComponent(id) : null };
}

function defaultRoute() {
  const viewer = state.session.viewer;
  if (viewer.role === "hr") return "#/hr";
  const own = viewer.employee_id || (state.employees[0] && state.employees[0].id);
  return `#/employee/${encodeURIComponent(own)}`;
}

async function render() {
  try {
    if (!state.session) await loadSession();
    if (!state.session.viewer) {
      root.innerHTML = loginView();
      return;
    }
    const { page, id } = route();
    if (!page) {
      location.hash = defaultRoute();
      return;
    }
    let content = "";
    if (page === "employee" && id) {
      if (state.profileId !== id || !state.profile) {
        state.suggestion = undefined;
        await loadProfile(id);
      }
      content = employeeView();
    } else if (page === "hr") {
      state.hr = state.hr || (await api("/api/hr"));
      content = hrView();
    } else if (page === "data") {
      content = dataView();
    } else {
      location.hash = defaultRoute();
      return;
    }
    root.innerHTML = `<div class="app">${sidebar(page, id)}<main class="main">${content}</main></div>`;
  } catch (error) {
    root.innerHTML = `<div class="main"><div class="card empty">${h(error.message)}</div></div>`;
  }
}

// ---------- views ----------
function loginView() {
  const demo = state.session.demo;
  const role = (key, icon, title, text) => `
    <button class="role${state.loginRole === key ? " active" : ""}" data-action="pick-role" data-role="${key}">
      <span>${icon}</span><b>${title}</b><small>${text}</small>
    </button>`;
  return `
  <div class="login"><div class="login-card">
    <div class="brand" style="color:var(--ink)"><div class="brand-logo">🌱</div>Career Quest</div>
    <h1>Понятный следующий шаг в развитии</h1>
    <p class="muted">1–3 добровольные активности с объяснением по фактам: навыки, цель, история участия, требования грейда.</p>
    <div class="roles">
      ${role("employee", "🧑‍💻", "Я сотрудник", "Мой путь, шаги и прогресс")}
      ${role("hr", "📊", "Я HR", "Навыки команды и участие")}
    </div>
    <form data-action="login" class="grid">
      ${demo ? "" : '<input class="input" type="password" name="password" placeholder="Пароль" autocomplete="current-password" required>'}
      <button class="btn btn-primary" type="submit" style="justify-content:center">Войти</button>
      ${demo ? '<p class="small muted">Демо-режим: вход без пароля, данные синтетические.</p>' : ""}
    </form>
  </div></div>`;
}

function sidebar(page, id) {
  const viewer = state.session.viewer;
  const hr = viewer.role === "hr";
  const canPick = hr || state.session.demo;
  const nav = [];
  if (hr) nav.push(["#/hr", "📊", "Обзор HR", page === "hr"]);
  nav.push([
    `#/employee/${encodeURIComponent(id || viewer.employee_id || (state.employees[0] && state.employees[0].id) || "")}`,
    "🧭",
    hr ? "Профиль сотрудника" : "Мой путь",
    page === "employee",
  ]);
  if (hr) nav.push(["#/data", "🗂️", "Данные", page === "data"]);
  const filter = state.filter.toLowerCase();
  const people = state.employees
    .filter((e) => !filter || `${e.name} ${e.id} ${e.role}`.toLowerCase().includes(filter))
    .slice(0, 250)
    .map(
      (e) => `<button data-action="open" data-id="${h(e.id)}" class="${e.id === id ? "active" : ""}">
        ${h(e.name)}<small>${h(e.role)}</small></button>`,
    )
    .join("");
  return `
  <aside class="sidebar">
    <div class="brand"><div class="brand-logo">🌱</div><div>Career Quest<small>Halyk · развитие сотрудников</small></div></div>
    <nav class="nav">${nav.map(([href, icon, label, active]) => `<a href="${href}" class="${active ? "active" : ""}">${icon} ${label}</a>`).join("")}</nav>
    ${
      canPick
        ? `<div><div class="side-label">Сотрудники · ${state.employees.length}</div>
      <input class="side-search" data-action="filter" placeholder="Поиск по имени, роли, ID" value="${h(state.filter)}">
      <div class="people">${people}</div></div>`
        : ""
    }
    <div class="side-footer">
      ${state.session.demo ? '<span class="pill warn">Демо-режим</span>' : ""}
      <span class="pill">${state.session.ai ? "🤖 AI настроен · проверяется при запросе" : "Офлайн-режим, без AI"}</span>
      <span>Дата среза: ${date(state.session.as_of_date)}</span>
      <span>Роль: ${hr ? "HR" : "сотрудник"} · <button class="link-button" data-action="logout">выйти</button></span>
    </div>
  </aside>`;
}

function employeeView() {
  const { employee, quest, recommendations, own } = state.profile;
  const [top, ...rest] = recommendations;
  return `
    <div class="page-title"><div>
      <h1>${h(employee.name)}</h1>
      <p>${h(employee.department)} · ${h(employee.role_label)} · стаж ${employee.tenure_months} мес.</p>
    </div></div>
    ${heroView(employee, quest, own)}
    ${own ? coachView() : hrGoalNote(employee)}
    <div class="row" style="justify-content:space-between;margin-top:34px">
      <h2 style="margin:0">${own ? "Ваш следующий шаг" : "Рекомендованные шаги сотрудника"}</h2>
      <label class="row small muted">Язык AI-объяснения
        <select class="input" style="width:auto;padding:6px 10px" data-action="lang">
          ${[["ru", "Русский"], ["kk", "Қазақша"], ["en", "English"]]
            .map(([code, label]) => `<option value="${code}" ${state.lang === code ? "selected" : ""}>${label}</option>`)
            .join("")}
        </select></label>
    </div>
    <p class="muted small">${own ? "Участие добровольное. Начните с первого варианта или выберите другой." : "Участие добровольное: решение о прохождении принимает сотрудник."}</p>
    ${
      top
        ? `<div class="grid">${recommendationView(top, true)}</div>
           ${rest.length ? `<div class="grid grid-2" style="margin-top:14px">${rest.map((rec) => recommendationView(rec, false)).join("")}</div>` : ""}`
        : '<div class="card empty">Подходящих шагов сейчас нет: цель может быть достигнута или в каталоге нет доступных занятий.</div>'
    }
    ${questView(quest, own)}
    ${skillsView()}
    ${historyView()}`;
}

function hrGoalNote(employee) {
  return `<section class="card coach"><div class="coach-avatar">🎯</div><div><b>Карьерная цель</b>
    <div class="small muted">${employee.goal ? `Сотрудник указал цель: ${h(employee.goal)}.` : "Сотрудник не указывал цель — ориентир: следующий уровень в текущей роли."}
    Цель выбирает сам сотрудник в своём профиле (AI-коуч доступен только ему).</div></div></section>`;
}

function heroView(employee, quest, own) {
  if (!quest) return '<div class="card">Карьерная цель не определена.</div>';
  const percent = Math.round(quest.coverage * 100);
  const earned = quest.badges.filter((b) => b.earned).length;
  return `
  <section class="hero">
    <div class="ring" style="--p:${percent}"><div class="ring-inner"><div><b>${percent}%</b><span>к цели</span></div></div></div>
    <div>
      <div class="hero-kicker">${h(employee.role_label)} → ${employee.goal ? (own ? "ваша карьерная цель" : "карьерная цель сотрудника") : "следующий уровень"}</div>
      <div class="hero-goal">${h(quest.target)}</div>
      <div class="hero-note">Покрытие требуемых уровней навыков — не вероятность и не гарантия повышения.</div>
      <div class="stats">
        <div class="stat"><b>${quest.critical_left}</b>Обязательные навыки ниже цели</div>
        <div class="stat"><b>+${quest.growth_points}</b>Рост уровней после оценки</div>
        <div class="stat"><b>${earned} из ${quest.badges_total}</b>Личные достижения</div>
      </div>
    </div>
  </section>`;
}

function coachView() {
  const s = state.suggestion;
  let result = "";
  if (s === null) result = '<div class="suggestion">Не нашли подходящую роль в справочнике. Назовите направление или уровень.</div>';
  if (s) {
    result = `<div class="suggestion">
      <div class="small muted">Предлагаемая цель · подобрано ${s.source === "ai" ? "AI" : "по ключевым словам (без AI)"}</div>
      <div style="font-weight:800;font-size:18px;margin:4px 0">${h(s.label)}</div>
      <div class="small">${h(s.reason)}</div>
      <button class="btn btn-primary" style="margin-top:10px" data-action="set-goal">Сделать целью и пересчитать шаги</button>
    </div>`;
  }
  return `
  <section class="card coach">
    <div class="coach-avatar">🤖</div>
    <div>
      <b>Карьерный помощник: разберём ваш следующий шаг</b>
      <div class="small muted">Спросите о навыках, причинах рекомендации, альтернативах или новой цели — на русском, қазақша или English.
      AI получает ваш вопрос, последние сообщения и расчётные факты профиля, без имени. Цель меняется только после подтверждения.</div>
      <div aria-live="polite">${state.dialogue.map(m => `<div class="explain"><b>${m.role === "user" ? "Вы" : "Помощник"}</b><br>${h(m.text)}</div>`).join("")}</div>
      <form data-action="coach">
        <input class="input" name="wish" data-action="wish" value="${h(state.wish)}" maxlength="500" placeholder="Каких навыков мне не хватает? Почему выбран этот курс?" required>
        <button class="btn btn-primary" type="submit">${state.busy === "coach" ? spinner : "Спросить"}</button>
      </form>
      ${result}
    </div>
  </section>`;
}

function recommendationView(rec, top) {
  const key = `${state.profileId}:${rec.event_id}:${state.lang}`;
  const explanation = state.explanations[key];
  const factors = rec.factors
    .map(
      (f) => `<li><span class="sign ${f.weight < 0 ? "minus" : "plus"}">${f.weight < 0 ? "−" : "+"}</span>
        <div>${h(f.label)}<small title="${h(f.detail)}">${h(f.detail_ru)}</small></div></li>`,
    )
    .join("");
  const busyExplain = state.busy === `explain:${rec.event_id}`;
  const busyDone = state.busy === `complete:${rec.event_id}`;
  return `
  <article class="card rec${top ? " top" : ""}">
    <div class="row">
      ${top ? '<span class="chip gold">Лучший следующий шаг</span>' : ""}
      ${rec.gain > 0 ? `<span class="chip green">+${pct(rec.gain)} к цели</span>` : ""}
    </div>
    <div class="rec-title" title="${h(rec.original_title)} · ${h(rec.event_id)}">${h(rec.title)}</div>
    <div class="row">
      <span class="chip">${h(rec.format)}</span><span class="chip">${rec.hours} ч</span>
      <span class="chip">Старт: ${date(rec.next_session)}</span>
    </div>
    <div class="gains">${rec.skill_changes.map((c) => `<span class="gain">${h(c.name)} ${c.before} → <b>${c.after}</b></span>`).join("")}</div>
    <div><div class="small muted" style="margin-bottom:8px">Почему этот шаг — ${rec.factors.length} ${plural(rec.factors.length, "фактор", "фактора", "факторов")}</div>
      <ul class="factors">${factors}</ul></div>
    ${explanation ? `<div class="explain">${h(explanation)}</div>` : ""}
    <details><summary>Описание занятия</summary><p class="small">${h(rec.description)}</p>
      <p class="small muted">Оценка подбора: ${rec.score} — сумма вкладов факторов, не вероятность успеха.</p></details>
    <div class="row">
      ${state.session.ai ? `<button class="btn btn-ghost" data-action="explain" data-event="${h(rec.event_id)}" ${explanation ? "disabled" : ""}>${busyExplain ? spinner : "✨"} Объяснить с AI</button>` : ""}
      <button class="btn btn-primary" data-action="complete" data-event="${h(rec.event_id)}" ${rec.completed_today ? "disabled" : ""}>
        ${busyDone ? spinner : "✓"} ${rec.completed_today ? "Отмечено сегодня" : "Отметить как пройдено"}</button>
    </div>
  </article>`;
}

function questView(quest, own) {
  if (!quest) return "";
  const steps = quest.milestones
    .slice(0, 8)
    .map(
      (m) => `<div class="step${m.critical ? " critical" : ""}"><b>${h(m.name)}</b>
        <div class="small muted">${m.critical ? "Обязателен для цели · " : ""}уровень ${m.current} из ${m.required}</div>
        <div class="bar"><i style="width:${Math.round((100 * m.current) / m.required)}%"></i></div></div>`,
    )
    .join("");
  const badges = quest.badges
    .map((b) => `<div class="badge${b.earned ? "" : " locked"}" title="${h(b.description)}"><span>${b.icon}</span>${h(b.title)}</div>`)
    .join("");
  const more = quest.milestones.length - 8;
  return `
    <h2>${own ? "Ваш квест к цели" : "Квест к цели"}</h2>
    <p class="small muted">Навыки, которые ещё ниже уровня цели. Золотая полоса слева — навык обязателен для цели.</p>
    ${steps ? `<div class="grid grid-3">${steps}</div>` : '<div class="card">Все требования цели выполнены 🎉</div>'}
    ${more > 0 ? `<p class="small muted">И ещё ${more} — полный список в разделе «Навыки».</p>` : ""}
    <h2>Достижения</h2>
    <div class="badges">${badges}</div>
    <p class="small muted">Достижения личные: они не сравниваются с коллегами и не влияют на оценку.</p>`;
}

function skillsView() {
  const levels = state.profile.levels;
  const cards = state.profile.skills
    .map((s) => {
      const dots = [1, 2, 3, 4, 5]
        .map((n) => `<span class="dot${n <= s.current ? " on" : ""}${s.required === n ? " target" : ""}"></span>`)
        .join("");
      const met = s.required !== null && s.current >= s.required;
      const goal = s.required === null ? "" : met ? ' · <span class="ok">✓ цель достигнута</span>' : ` · цель ${s.required}`;
      return `<div class="card skill"><div><b title="${h(s.original)}">${levelIcon(s.current)} ${h(s.name)}</b>
        <div class="small muted">${h(levels[s.current])} · уровень ${s.current}${goal}${s.critical ? " · обязателен" : ""}</div></div>
        <div class="dots" title="Уровень ${s.current} из 5">${dots}</div></div>`;
    })
    .join("");
  const legend = LEVEL_ICONS.map(([, icon, text]) => `<span class="chip">${icon} ${text}</span>`).join("");
  return `<h2>Навыки</h2>
    <div class="legend">${legend}<span class="chip"><span class="dot on"></span> уровень из 5</span>
      <span class="chip"><span class="dot target"></span> уровень, нужный для цели</span><span class="chip"><span class="ok">✓</span> цель по навыку достигнута</span></div>
    <div class="grid grid-2">${cards}</div>`;
}

function historyView() {
  const rows = state.profile.history
    .map(
      (r) => `<tr><td>${date(r.date)}</td><td>${h(r.title)}</td>
      <td><span class="status ${h(r.status)}">${STATUS_ICONS[r.status] || ""} ${h(r.status_label)}</span></td><td>${r.completion_pct}%</td></tr>`,
    )
    .join("");
  const legend = `<div class="legend">
    <span class="status completed">✓ завершено</span><span class="status in_progress">⏳ идёт сейчас</span>
    <span class="status overdue">⚠ просрочено</span><span class="status no_show">✕ пропуск, отказ или прекращено</span></div>`;
  return `<h2>История участия</h2>${legend}
    <div class="card" style="padding:6px 10px">${
      rows
        ? `<table><thead><tr><th>Дата</th><th>Активность</th><th>Статус</th><th>Прогресс</th></tr></thead><tbody>${rows}</tbody></table>`
        : '<div class="empty">Истории участия пока нет</div>'
    }</div>`;
}

function hrView() {
  const { totals, deficits, no_steps: noSteps, participation } = state.hr;
  const max = Math.max(1, ...deficits.map((d) => d.count));
  const bars = deficits
    .map(
      (d) => `<div class="hbar"><span>${h(d.name)}</span>
      <div class="track"><div class="fill${d.critical ? " crit" : ""}" style="width:${(100 * d.count) / max}%"></div></div><b>${d.count}</b></div>`,
    )
    .join("");
  const people = noSteps
    .map((p) => `<tr><td><a href="#/employee/${encodeURIComponent(p.id)}">${h(p.name)}</a></td><td>${h(p.role)}</td><td class="muted">${h(p.id)}</td></tr>`)
    .join("");
  const events = participation
    .map(
      (e) => `<tr><td>${h(e.title)}</td><td>${e.participants}</td><td>${e.completed} из ${e.records}</td>
      <td style="min-width:140px"><div class="bar" style="margin:0"><i style="width:${pct(e.rate)}"></i></div><span class="small muted">${pct(e.rate)}</span></td></tr>`,
    )
    .join("");
  return `
    <div class="page-title"><div><h1>Обзор развития команды</h1>
      <p>Агрегаты по всем сотрудникам. Без рейтингов людей: только навыки, шаги и участие.</p></div>
      <button class="btn btn-ghost" data-action="refresh-hr">↻ Пересчитать</button></div>
    <div class="grid grid-3">
      <div class="card metric"><b>${totals.employees}</b><span>сотрудников</span></div>
      <div class="card metric"><b>${totals.events}</b><span>добровольных активностей</span></div>
      <div class="card metric"><b>${totals.records}</b><span>записей участия</span></div>
    </div>
    <h2>Какие навыки отстают чаще всего</h2>
    <div class="card">${bars}<p class="small muted">Число сотрудников ниже уровня, нужного для их цели. Золотой оттенок — навык обязателен хотя бы для одной цели.</p></div>
    <h2>Нет подходящего следующего шага · ${noSteps.length}</h2>
    <div class="card" style="padding:6px 10px">${people ? `<table><thead><tr><th>Сотрудник</th><th>Роль</th><th>ID</th></tr></thead><tbody>${people}</tbody></table>` : '<div class="empty">У всех есть следующий шаг</div>'}</div>
    <p class="small muted">Это не оценка вовлечённости: цель может быть достигнута или в каталоге нет подходящих занятий.</p>
    <h2>Участие в добровольных активностях</h2>
    <div class="card" style="padding:6px 10px"><table><thead><tr><th>Активность</th><th>Участников</th><th>Завершений</th><th>Доля завершений</th></tr></thead><tbody>${events}</tbody></table></div>`;
}

function dataView() {
  return `
    <div class="page-title"><div><h1>Данные и проверка жюри</h1>
      <p>Изменения хранятся на сервере в вашей сессии. Скачайте снимок, чтобы сохранить результат.</p></div>
      <a class="btn btn-ghost" href="/api/data/export">⬇ Скачать снимок (ZIP)</a></div>
    <div class="grid grid-2">
      <form class="card upload" data-action="additions">
        <b>Добавить проверочные профили и историю</b>
        <span class="small muted">Файлы в формате датасета. Совпадающие ID заменяются, каталог событий и навыков сохраняется.</span>
        <label class="small">employees.json <input class="input" type="file" name="employees_file" accept=".json"></label>
        <label class="small">activity_history.csv <input class="input" type="file" name="history_file" accept=".csv"></label>
        <button class="btn btn-primary" type="submit">${state.busy === "additions" ? spinner : ""} Загрузить и пересчитать</button>
      </form>
      <form class="card upload" data-action="replace">
        <b>Заменить весь набор данных</b>
        <span class="small muted">Четыре файла: employees.json, events.json, skills.json, activity_history.csv. Прогресс этой сессии будет заменён.</span>
        <input class="input" type="file" name="files" accept=".json,.csv" multiple required>
        <button class="btn btn-ghost" type="submit">${state.busy === "replace" ? spinner : ""} Заменить набор</button>
      </form>
    </div>`;
}

// ---------- actions ----------
async function withBusy(key, action) {
  if (state.busy) return;
  state.busy = key;
  await render();
  try {
    await action();
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.busy = "";
    await render();
  }
}

async function refreshAfterDataChange() {
  state.employees = await api("/api/employees");
  state.hr = null;
  state.profile = null;
  state.explanations = {};
}

const actions = {
  "pick-role": (el) => {
    state.loginRole = el.dataset.role;
    render();
  },
  open: (el) => {
    location.hash = `#/employee/${encodeURIComponent(el.dataset.id)}`;
  },
  logout: async () => {
    await api("/api/session", { method: "DELETE" });
    Object.assign(state, { session: null, employees: [], profile: null, profileId: null, hr: null, explanations: {}, dialogue: [], suggestion: undefined, wish: "" });
    location.hash = "";
    render();
  },
  "refresh-hr": () =>
    withBusy("hr", async () => {
      state.hr = null;
    }),
  explain: (el) => {
    const employeeId = state.profileId, eventId = el.dataset.event, language = state.lang;
    return withBusy(`explain:${eventId}`, async () => {
      const result = await api(`/api/employees/${encodeURIComponent(employeeId)}/explain`, {
        json: { event_id: eventId, language },
      });
      if (state.profileId === employeeId) state.explanations[`${employeeId}:${eventId}:${language}`] = result.text;
    });
  },
  complete: (el) =>
    withBusy(`complete:${el.dataset.event}`, async () => {
      const result = await api(`/api/employees/${encodeURIComponent(state.profileId)}/complete`, {
        json: { event_id: el.dataset.event },
      });
      await loadProfile(state.profileId);
      state.hr = null;
      const changes = result.changes.map((c) => `${c.name}: ${c.before} → ${c.after}`).join("; ");
      let message = `Прогресс обновлён. ${changes || "Уровни навыков не изменились."}`;
      if (result.new_badges.length) {
        message += ` 🏅 Новое достижение: ${result.new_badges.map((b) => `${b.icon} ${b.title}`).join(", ")}`;
        confetti();
      }
      toast(message);
    }),
  "set-goal": () =>
    withBusy("goal", async () => {
      const s = state.suggestion;
      await api(`/api/employees/${encodeURIComponent(state.profileId)}/goal`, {
        json: { target_role: s.target_role, target_grade: s.target_grade },
      });
      await loadProfile(state.profileId);
      state.suggestion = undefined;
      state.hr = null;
      toast(`Цель обновлена: ${s.label}. Шаги пересчитаны.`);
    }),
};

const forms = {
  login: (form) =>
    withBusy("login", async () => {
      const password = form.password ? form.password.value : "";
      await api("/api/session", { json: { role: state.loginRole, password } });
      state.session = null;
      await loadSession();
      location.hash = defaultRoute();
    }),
  coach: (form) => {
    const wish = form.wish.value.trim();
    const employeeId = state.profileId;
    const language = state.lang;
    if (!wish) return;
    return withBusy("coach", async () => {
      const result = await api(`/api/employees/${encodeURIComponent(employeeId)}/assistant`, { json: { wish, language } });
      if (state.profileId !== employeeId) return;
      state.dialogue.push({ role: "user", text: wish }, { role: "assistant", text: result.text });
      state.dialogue = state.dialogue.slice(-6);
      state.wish = "";
      state.suggestion = result.suggestion || undefined;
    });
  },
  additions: (form) => {
    const body = new FormData(form);
    return withBusy("additions", async () => {
      const result = await api("/api/data/additions", { method: "POST", body });
      await refreshAfterDataChange();
      toast(`Загружено. Сотрудников: ${result.employees}, записей: ${result.records}. Рекомендации пересчитаны.`);
    });
  },
  replace: (form) => {
    const body = new FormData(form);
    return withBusy("replace", async () => {
      const result = await api("/api/data/replace", { method: "POST", body });
      await refreshAfterDataChange();
      toast(`Новый набор загружен. Сотрудников: ${result.employees}.`);
    });
  },
};

document.addEventListener("click", (event) => {
  const el = event.target.closest("[data-action]");
  if (el && el.tagName !== "FORM" && el.tagName !== "INPUT" && el.tagName !== "SELECT" && actions[el.dataset.action]) {
    event.preventDefault();
    actions[el.dataset.action](el);
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target.closest("form[data-action]");
  if (form && forms[form.dataset.action]) {
    event.preventDefault();
    forms[form.dataset.action](form);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.dataset.action === "lang") {
    state.lang = event.target.value;
    render();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.dataset.action === "wish") state.wish = event.target.value;
  if (event.target.dataset.action === "filter") {
    state.filter = event.target.value;
    const position = event.target.selectionStart;
    render().then(() => {
      const input = document.querySelector('[data-action="filter"]');
      if (input) {
        input.focus();
        input.setSelectionRange(position, position);
      }
    });
  }
});

window.addEventListener("hashchange", render);
render();
