# Project spec — Career Quest

## The task (from the organisers' brief)

Web app with an AI layer that, from an employee's profile, history and next-grade requirements, recommends
**1–3 development activities with a justification**, updates skill progress when an activity is completed,
and gives HR a view of lagging skills. **The core is the quality and explainability of the recommendation**,
not the UI. Gamification is optional and out of scope.

Must-have (the jury checks each one):

1. Open any employee → role, grade, skills, completed activities, available next steps.
2. AI recommends 1–3 relevant activities.
3. Each justification rests on **at least three factors** (grade, skill gaps, participation history, next-level requirements).
4. Mark an activity done → skill progress and trajectory move.
5. HR view: which skills lag most often, who has no recommended step, participation per activity.

The jury loads **three trap profiles** (plus their history) in the dataset format, built so that single-factor rules fail.
Example: lowest skill is Public Speaking, but the employee skipped such activities three times, and System Design is critical
for the next grade. "Pick the lowest skill" must lose on such a profile.

Hard constraints:

- No single-field rule presented as AI. No public performance rankings of employees.
- No mechanics around mandatory processes. Voluntary participation only — never pressure.
- Privacy: an employee sees only their own data; HR sees aggregates and lists. Role separation employee/HR.
- Latency: UI < 2 s, AI recommendation < 10 s. One-command launch.

Scoring weights: fit & working (25), technical implementation (25), **README & reproducibility (25)**, value (15), originality (10).

## Dataset (`data/`, full description in `data/README.md`)

- "Today" is the snapshot date **2026-10-01** (`Dataset.as_of_date`), never the real clock.
- `role_profiles`: `required_skills` (min level per skill) and **`critical_skills`** (must meet the requirement to hold the grade —
  the key promotion factor). Requirements never decrease with grade. Grade order: Junior → Middle → Senior → Lead.
- Employee `skills` reflect the **last review** (`last_review_date`). Activities completed after that date are **not yet included** —
  their gains must be applied to get current levels. Missing skill = level 0.
- `career_goal` (`{target_role, target_grade}` or null) is set for 134 of 200 employees; the target role can differ from the current one.
- Events: `mandatory: true` events (4 of 40) are **never recommended**. `develops_skills[]` = `{skill_id, gain, max_level}`:
  completion raises the level by `gain`, capped at `max_level`. `prerequisites` = minimum levels to join.
  `upcoming_sessions` empty for `self_paced` (available any time).
- History statuses: `completed`, `in_progress`, `dropped`, `no_show`, `declined`, `overdue`. An event is not repeated after
  `completed`, except `EV_036` (recurring club). `assigned_by`: `self` | `manager` | `hr`.

## Architecture and ownership

| Module | Owner | Status |
|---|---|---|
| `career_quest/models.py` — Pydantic models for the dataset | Eldana | done |
| `career_quest/data.py` — loading, validation, merging extra profiles/history | Eldana | done |
| `career_quest/scoring.py` — effective skills, candidate filter, scoring, progress update | Eldana | next |
| `tests/test_data.py`, `tests/test_scoring.py` (incl. trap profiles) | Eldana | |
| `README.md` | Eldana | |
| `career_quest/explain.py` — LLM justification from computed factors | Ayazhan | |
| `career_quest/api.py` + `web/` — FastAPI server and web client: employee view, HR view, role switch, file upload (replaced the Streamlit `app.py`) | Eldana, Ayazhan | done |
| `Dockerfile`, `docker-compose.yml` | Ayazhan | |
| `pyproject.toml`, `uv.lock` | shared — announce before editing | |

## Public contracts

Existing (`career_quest/data.py`):

```python
load_dataset(data_dir: Path) -> Dataset
parse_employees(raw: str | bytes) -> list[Employee]          # jury upload of employees.json
parse_history(raw: str | bytes) -> list[ActivityRecord]     # jury upload of activity_history.csv
Dataset.with_additions(employees, history) -> Dataset       # returns a new validated Dataset
Dataset.employee(id) / .event(id) / .role_profile(role, grade) / .history_for(employee_id)
DatasetError                                                  # show its message to the user, do not crash
```

To be implemented in `career_quest/scoring.py` (UI and explain code against these):

```python
class Factor(BaseModel):          # one reason behind a recommendation
    code: str                     # e.g. "critical_gap", "goal_alignment", "history_avoidance"
    weight: float                 # contribution to the score (+/-)
    detail: str                   # human-readable English fact with numbers, e.g. "SK_SYSTEM_DESIGN 2 → required 4 for Senior (critical)"

class Recommendation(BaseModel):
    event_id: str
    score: float
    skill_changes: dict[str, tuple[int, int]]   # skill_id -> (current level, level after completion)
    factors: list[Factor]                       # at least 3
    next_session: date | None                   # None = self-paced

effective_skills(ds: Dataset, employee_id: str) -> dict[str, int]
target_profile(ds: Dataset, employee_id: str) -> RoleProfile | None
recommend(ds: Dataset, employee_id: str, limit: int = 3) -> list[Recommendation]
complete_activity(ds: Dataset, employee_id: str, event_id: str) -> Dataset   # appends a `completed` record dated as_of_date
```

`explain.py` (Ayazhan): `explain(rec: Recommendation, employee: Employee, language: Language) -> str`.
The LLM receives only the computed factors and must not invent facts. If the LLM call fails or times out,
return a deterministic text built from the same factors — the app must work without the network.
API key from an environment variable; never commit keys.

## Recommendation logic (scoring.py)

1. **Effective skills** = reviewed levels + gains of events completed after `last_review_date`, capped by `max_level`, in date order.
2. **Target** = `career_goal` if set; otherwise the next grade of the current role; for a Lead without a goal — the current profile.
3. **Candidates**: not mandatory; employee role (or target role) in `target_roles`; current or target grade in `target_grades`;
   prerequisites met by effective skills; not already completed (except `EV_036`); raises at least one skill.
4. **Score** — a weighted sum of factors, weights in one constant dict at the top of the module:
   - critical gap closure: how much the event closes gaps on the target's `critical_skills` (strongest factor);
   - other required gaps closed for the target grade;
   - goal alignment (event serves the career goal role/grade);
   - history avoidance: past `no_show` / `dropped` / `declined` on events of the same type or the same skills lowers the score;
   - engagement: past `self`-initiated completions and good `feedback_rating` on similar events raise it;
   - availability: self-paced or a session soon; heavy `duration_hours` slightly lowers it.
5. Return the top `limit` with every non-zero factor. Fewer than 3 factors → the recommendation is not shown.
6. Tests must include hand-built trap profiles (like the brief's example) where "lowest skill" loses.
