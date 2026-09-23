# Git Workflow

Team of three, one hackathon repo, one shared `main`, **six hours total**. The goal of
this document is that nobody ever loses work and nobody spends any of those six hours
untangling history.

At this timescale an hour lost to a broken `main` is a sixth of the event. The rules
below are deliberately cheap to follow.

## Related documents

- **[`guidelines.md`](guidelines.md)** — coding conventions, the ruff/mypy/pytest rule
  set, and the commit message and branch naming conventions this document assumes.
  Read it before you write code; read this before you push it.

## The six rules

1. **Pull before you do anything. Then keep pulling.** Before starting a task, before
   creating a branch, before pushing, and roughly **every 20–30 minutes** while you
   work. A conflict caught 10 minutes after it appears is a one-line fix; the same
   conflict caught three hours later is a merge from hell.

   ```bash
   git pull                # on main; rebases, see setup below
   git fetch origin        # on a branch; then rebase when convenient
   ```

2. **Never force-push `main`.** Not with `--force`, not with `--force-with-lease`.
3. **Never commit directly on `main`.** Branch, then merge.
4. **Rebase onto `origin/main` before you open or merge a PR.** Conflicts are yours to
   resolve on your branch, not everyone's to discover on `main`.
5. **Push every 20–30 minutes.** Unpushed work is invisible to the other two, and
   invisible work is work that gets duplicated. At six hours you cannot afford to
   build the same thing twice.
6. **One task, one branch, one hour maximum.** If a branch has been alive longer than
   an hour, it is too big — merge what works and branch again for the rest.

## Why rule 2 exists

This already happened here. `main` was force-pushed early on: commit `7019dbb`
("Add LABUBU heading to README") was rewritten into `1ce34ca`. Anyone who had already
pulled `7019dbb` ended up with a local `main` pointing at a commit that no longer
existed upstream, diverged from the real `main`, and every later push from that clone
was rejected. It cost a rebase and a conflict resolution to recover.

Force-pushing a shared branch does not just change history — it silently invalidates
every teammate's clone. If you think you need it, ask in the team chat first.

## Branch model

```
main                 always green, always deployable, protected by convention
 ├── feat/<thing>    new functionality
 ├── fix/<thing>     bug fixes
 ├── chore/<thing>   tooling, deps, CI
 └── docs/<thing>    documentation only
```

**Name branches after the work, not after yourself.** This repo currently has a branch
called `Sula`, stale since its author moved on. Nobody else can tell what is in it, or
whether it is safe to delete. `feat/lead-scoring` answers both questions instantly.

Delete your branch after it merges. GitHub offers a button for this; use it.

## The loop

Set this once per clone, so every pull rebases instead of creating surprise merge
commits:

```bash
git config pull.rebase true
```

Then, per task:

```bash
# 1. PULL FIRST -- always, before anything else
git checkout main
git pull                       # rebases, per the config above

# 2. Branch for the task
git checkout -b feat/short-description

# 3. Work. Commit in small, complete steps.
uv run ruff format .
uv run ruff check --fix .
git add <specific files>       # never `git add -A`
git commit -m "feat: add lead scoring endpoint"

# 4. Every 20-30 min: pull main's new work into your branch
git fetch origin
git rebase origin/main         # resolve conflicts here, on your own branch

# 5. Push -- also every 20-30 min, not just when finished
git push -u origin feat/short-description

# 6. Open the PR as soon as the branch exists, even if unfinished
gh pr create --fill --draft    # drop --draft when ready for review
```

Steps 4 and 5 are not "end of task" steps. Run them on a timer. Opening the PR early
(step 6) is free and lets the other two see what you are building before it lands.

Rebasing **your own unmerged branch** is safe and expected — that is not what rule 2
forbids. Rule 2 is about `main` and any branch someone else has based work on.

## Reviews

Three people and six hours, so keep review cheap:

- **One approval merges.** Do not wait for both others.
- **Ten-minute review SLA.** If nobody has reviewed within ten minutes, merge it
  anyway and say so in chat. At this timescale a stalled PR costs the team more than
  an unreviewed one.
- **Keep PRs small enough to review in five minutes.** A PR touching 40 files will not
  get a real review during a hackathon — it will get a rubber stamp, which is worse
  than no review at all.

Squash-merge so `main` stays one commit per logical change:

```bash
gh pr merge --squash --delete-branch
```

The squash commit message must still follow Conventional Commits.

## The last 45 minutes

Freeze. From then on:

- **Bug fixes only.** No refactors, no new dependencies, no renames.
- **Merge or abandon** every open branch — nothing half-merged at the deadline.
- **Pull and verify on a clean clone** before you call it done:

  ```bash
  git clone <repo> /tmp/final-check && cd /tmp/final-check
  uv sync && uv run ruff check . && uv run pytest
  ```

  "It works on my machine" has lost more hackathons than bad code.
## Staying out of each other's way

Most conflicts are an organisational problem, not a git problem.

**Split by file, not by layer.** If two people both "work on the API", they will both
edit the same router file. Agree on module ownership at the start of each session and
write it in the team chat: one person owns ingestion, one owns scoring, one owns the
API surface.

**Shared files need a heads-up.** In this repo those are `README.md`, `pyproject.toml`,
`uv.lock`, and anything in `.agents/`. Say in chat before you touch one. `README.md`
has already conflicted once, because two people appended to the end of the same file.

**Append in your own section.** When several people must edit one document, give each
section a single owner rather than all editing the bottom of the file.

## Resolving conflicts

**`uv.lock` — never hand-edit, never hand-merge.** It is generated. Take either side
wholesale and regenerate:

```bash
git checkout --theirs uv.lock   # or --ours; the choice does not matter
uv lock                         # regenerate from pyproject.toml
git add uv.lock
```

**`pyproject.toml` — almost always "keep both".** Two people adding two different
dependencies is not a real conflict; merge both lines, then run `uv lock`.

**Source files — resolve, then re-run the checks.** A resolved conflict that no longer
compiles is a broken `main`:

```bash
uv run ruff check .
uv run pytest
```

**If a rebase goes wrong, you can always back out:**

```bash
git rebase --abort
```

And if you have already lost a commit, it is almost certainly still there:

```bash
git reflog                      # find the hash
git branch recovered <hash>
```

## Merge checklist

Before you merge to `main`:

- [ ] Rebased onto current `origin/main`
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run ruff check .` passes
- [ ] `uv run mypy .` passes *(will pass once the first module exists)*
- [ ] `uv run pytest` passes *(will pass once the first test exists)*
- [ ] Commit messages follow Conventional Commits
- [ ] Code follows [`guidelines.md`](guidelines.md)
- [ ] One teammate approved, or the ten-minute SLA elapsed
- [ ] Branch deleted after merge
