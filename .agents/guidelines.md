# Coding Guidelines

This file provides coding and git guidelines for AI coding agents.

**Stack:** Python, managed with `uv`. Linting and formatting with `ruff` are mandatory.

> **Environment:** Python 3.12 (pinned in `.python-version`), dependencies and tool
> config in `pyproject.toml`, lockfile `uv.lock` committed. Run `uv sync` once after
> cloning. The ruff rule set below is wired into `[tool.ruff.lint]` -- keep the two in sync.

## Mandatory Agent Rules

**This is a production-grade system, not a playground. These rules are non-negotiable.**

1. **No dull or boilerplate implementations.** Every piece of code must be purposeful,
   correct, and production-ready from the first commit. Do not write stub implementations,
   placeholder logic, TODO-only functions, or "fill this in later" scaffolding. If a
   feature cannot be fully implemented, say so explicitly and propose a plan rather than
   shipping skeleton code.

2. **Always consult this file before building anything.** Before writing any code, read
   this file in full to understand conventions, architecture constraints, and critical
   implementation notes. Code that contradicts this file will be rejected.

3. **No silent assumptions.** If a requirement is unclear, ask for clarification rather
   than guessing. Wrong guesses that reach production cost more to fix than a one-question
   pause.

4. **Correctness over cleverness.** Prefer clear, reviewable code. Avoid overly clever
   one-liners, premature abstractions, or patterns that require extensive mental overhead
   to follow.

## Code Conventions

**Python:** PEP 8 enforced by `ruff` (line length 120). Use `str | None` not
`Optional[str]`. Use `list[T]`, `dict[K, V]`. All public functions require complete
type annotations. `mypy` strict mode — zero errors in CI. Max 50 lines per function,
max 3 nesting levels. Max McCabe complexity 10 (enforced by `C901`). Google-style
docstrings on all public classes/functions. Absolute imports only. Import order:
standard lib → third-party → local.

**Import rules:**

- All imports must be at the top of the file, in three groups separated by blank lines:
  1. Standard library
  2. Third-party (pandas, fastapi, structlog, etc.)
  3. Local project packages
- No function-level imports. No lazy imports. No `# noqa: E402`.
- Use `if TYPE_CHECKING:` guards only for imports used exclusively in type annotations
  that would create circular dependencies. Never use `TYPE_CHECKING` guards for imports
  needed at runtime (e.g., Pydantic model fields, `datetime` in model annotations).
- Circular dependency = architecture bug. If two modules need each other, restructure
  with protocols, extract shared interfaces, or move the dependency to a lower layer.
  Do not work around it with lazy imports.

**Banned imports:**

- `from typing import Optional` — use `X | None` instead
- `import logging` — use `structlog` instead

**Git:** Conventional Commits — `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`. Branch
naming: `feat/feature-name`, `fix/bug-description`, `chore/tooling-change`,
`docs/what-changed`. Branch and merge via PR — never commit directly on `main`, and
never force-push it. See [`git-workflow.md`](git-workflow.md) for the full team process.

**Logging:** Use `structlog` (not stdlib `logging`). Always bind relevant context:

```python
log = structlog.get_logger(__name__).bind(entity_id=item.id)
log.warning("missing_field", field_name="title")
```

**Error handling:** Never use bare `except Exception: pass`. Always log the exception:

```python
except Exception as exc:
    log.warning("operation_failed", error=str(exc))
```

**Path handling:** Use `pathlib.Path` instead of `os.path`. Use `Path.open()` instead of
`open()`. Use `Path.exists()` instead of `os.path.exists()`.

## Ruff Rule Set

The following ruff rules are enforced (configured in `pyproject.toml`):

| Rule | Purpose |
|---|---|
| `E` | pycodestyle errors |
| `F` | pyflakes |
| `I` | isort (import sorting) |
| `N` | pep8-naming |
| `UP` | pyupgrade |
| `B` | flake8-bugbear |
| `A` | flake8-builtins |
| `SIM` | flake8-simplify |
| `S` | flake8-bandit (security) |
| `C4` | flake8-comprehensions |
| `PT` | flake8-pytest-style |
| `RET` | flake8-return |
| `ARG` | flake8-unused-arguments |
| `PTH` | flake8-use-pathlib |
| `ICN` | flake8-import-conventions |
| `PERF` | perflint |
| `C901` | McCabe complexity |
| `RUF` | Ruff-specific rules |
| `D` | pydocstyle (Google-style docstrings) |

Ignored rules (with justification):

| Rule | Reason |
|---|---|
| `RUF001` | Ambiguous Cyrillic chars — false positives in Russian-language strings |
| `RUF002` | Ambiguous chars in docstrings — intentional math/typography (× − – →) |
| `RUF003` | Ambiguous chars in comments — intentional math/typography (× − – →) |
| `S324` | sha1/md5 hashing — used for cache keys, not security |
| `TC` | Type-checking import guards — deferred; `strict = true` breaks Pydantic models |

## Project Layout

Python 3.12. Currently a **non-package project** (`package = false`) -- there is no
importable package yet, because the project scope is not defined. When code lands:

- Add the package (and a `[build-system]`, dropping `package = false` if it should be
  installable), then set `known-first-party` under `[tool.ruff.lint.isort]`.
- Add `tests/` and set `testpaths = ["tests"]` under `[tool.pytest.ini_options]`.
- If the project becomes a `uv` workspace, run `mypy` once per member instead of once
  at the root.

**Until then, two of the four checks in step 1 fail for lack of code, not for lack of
correctness:** `mypy .` exits 2 (`no .py[i] files`) and `pytest` exits 5 (no tests
collected). Both must go green with the first real module and its test. Do not silence
them with stub modules or empty test files -- see Mandatory Agent Rule 1.

## Validation & Git Workflow

**After every code change, before considering the task done:**

1. **Validate the project is runnable:**
   ```bash
   uv run ruff format --check .      # formatting must match
   uv run ruff check .               # must pass with zero errors
   uv run mypy .                     # mypy strict — zero errors
   uv run pytest                     # all tests must pass
   ```
   With a `uv` workspace, run `mypy` once per member rather than once at the root.

2. **Format before committing:**
   ```bash
   uv run ruff format .              # auto-format
   uv run ruff check --fix .         # auto-fix safe violations
   ```

3. **Commit and push every change:**
   - Stage only relevant files (never `git add -A` blindly)
   - Use Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
   - Push to remote: `git push`
   - Do not leave uncommitted changes after completing a task

4. **Review after every change:**
    - Review the code diff and check that it adheres to the code style and guidelines
      of this project as described in `.agents/guidelines.md`.
    - Review relevant project docs and update them to stay accurate given the changes.

These steps are **non-negotiable** — a task is not complete until the project passes
all checks and the commit is pushed.
