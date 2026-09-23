# AGENTS.md

Hackathon repo for team **la(rp)-peace**. Python 3.12, managed with `uv`.

Read these before doing anything:

| Document | What it covers |
|---|---|
| [`.agents/guidelines.md`](.agents/guidelines.md) | Coding conventions, ruff/mypy rule set, validation commands |
| [`.agents/git-workflow.md`](.agents/git-workflow.md) | Branching, PRs, conflict resolution, six-hour cadence |

## Setup

```bash
uv sync
```

## Non-negotiables

- **Pull first**, and every 20–30 minutes after that.
- **Never force-push `main`**, never commit directly on it.
- **No stub or placeholder code** — if it can't be finished, say so instead.
- Before every commit: `uv run ruff format . && uv run ruff check --fix .`

Everything else is in the two documents above.
