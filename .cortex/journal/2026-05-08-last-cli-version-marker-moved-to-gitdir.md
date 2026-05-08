# last-cli-version marker moved out of the worktree

**Date:** 2026-05-08
**Type:** decision
**Trigger:** T1.1
**Cites:** journal/2026-05-07-v1.3.0-released

> The auto-sync version marker now lives at `<gitdir>/cortex/.last-cli-version` instead of `.cortex/.last-cli-version`, so it stops dirtying the worktree of every Cortex-using project.

## Context

cortex#225 reported that the CLI was writing `.cortex/.last-cli-version` into the working tree on every invocation, but the file was not gitignored. This tripped dirty-tree gates in `touchstone release`, `scripts/codex-review.sh`, `scripts/open-pr.sh`, and the auto-sync push gate. Every cortex-using project hit it. Three remediation options were on the table — write outside the worktree, ship a managed `.cortex/.gitignore`, or have `cortex init` append a line to the project `.gitignore` — and the issue was opinionated toward option 1.

The marker is operational state of the tool, not project memory. Per **derive-don't-persist** in engineering-principles, persisted state belongs only when recomputation is too expensive — which is the case here (we need a stable record of the last CLI version that ran against this store) — but the *location* must reflect ownership: cortex's runtime state has no business in the project's source tree.

## What we decided

Write the marker to `<gitdir>/cortex/.last-cli-version` (resolved via `_git_dir()` in `src/cortex/commands/_auto_sync.py`). Three details that fall out:

- **Worktree-aware resolution.** When `<project>/.git` is a directory we use it directly. When it is a file (linked worktree or submodule), we read the `gitdir:` pointer ourselves rather than shelling out to `git rev-parse --git-dir`. The subprocess-free path avoids polluting tests that mock `subprocess.run`, and avoids one extra fork on every `cortex` invocation.
- **No-git is a clean no-op.** If `.git` is absent (non-git checkout, fresh `cortex init` before `git init`), the marker writes are skipped entirely. Auto-sync continues without complaint — there is nothing meaningful to compare against in that mode.
- **Best-effort migration.** A pre-existing legacy `.cortex/.last-cli-version` is migrated to the new location and the legacy file deleted on the first upgraded run. Migration failures are logged as warnings and do not abort the user's command. The next minor-bump auto-sync detects the same delta either way, so the migration is idempotent.

## Consequences / action items

- [x] `tests/test_sync.py` covers: gitdir-marker write, no-git no-op, atomic write, legacy migration. Runs in CI.
- [x] `.cortex/.gitignore` retains `.last-cli-version` / `.last-cli-version.tmp` lines as defense-in-depth for any project that hasn't yet upgraded — they are inert once the migration runs.
- [x] Documentation in `_auto_sync.py` module docstring + `_git_dir` docstring spells out the gitdir layout so future-me doesn't reinvent it.
