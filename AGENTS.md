# AGENTS.md

Hackathon repo for team **Flower Power** — HackAlem AI, Halyk Bank track, case **Career Quest**.
Python 3.12, managed with `uv`. Team of two: **Eldana** and **Ayazhan**. Deadline **18:00**, code freeze **17:15**.

Read these before doing anything:

| Document | What it covers |
|---|---|
| [`.agents/project-spec.md`](.agents/project-spec.md) | **What we are building**: task, dataset facts, architecture, module ownership, recommendation logic, contracts |
| [`.agents/guidelines.md`](.agents/guidelines.md) | Coding conventions, ruff/mypy rule set, validation commands |
| [`.agents/git-workflow.md`](.agents/git-workflow.md) | Branching, PRs, conflict resolution |

`git-workflow.md` was written for three people and six hours. Where it disagrees with this file, **this file wins**:
two people, merge to `main` at least **every hour** (14:30, 15:30, 16:30, 17:15), push your branch every 30 minutes.

## Setup

```bash
uv sync
uv run pytest        # must be green before you start
```

## Non-negotiables

- **Pull first**, and every 20–30 minutes after that. After every pull that touches `pyproject.toml` or `uv.lock`, run `uv sync`.
- **Never force-push `main`**, never commit directly on it.
- **Stay inside your owner's files** (table in `project-spec.md`). Touching another owner's file or a shared file
  (`README.md`, `pyproject.toml`, `uv.lock`, `.agents/`, `AGENTS.md`, `CLAUDE.md`) — stop and tell the user first.
- **Do not change the public contracts** in `project-spec.md` without the user's explicit OK: the other person codes against them.
- **No stub or placeholder code** — if it can't be finished, say so instead.
- Before every commit: `uv run ruff format . && uv run ruff check --fix . && uv run mypy . && uv run pytest`

## Talking to the user

- Reply to the user **in Russian**, briefly and concretely. Code, identifiers, docstrings and commit messages in English.
- The user is not a professional developer: when you finish a task, say in 2–3 plain sentences what changed and
  give the exact git commands to commit it.
