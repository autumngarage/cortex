# `cortex init` shipped — second Phase B slice

**Date:** 2026-04-17
**Type:** decision
**Trigger:** T1.1 (diff touches `.cortex/plans/phase-b-walking-skeleton.md`) + T1.5 (dependency manifest `pyproject.toml` gains package-data config)
**Cites:** plans/phase-b-walking-skeleton, journal/2026-04-17-phase-b-scaffold-shipped, journal/2026-04-17-phase-b-plan-refresh

> `cortex init` now scaffolds a SPEC-v0.3.1-dev-conformant `.cortex/` directory in any target project: SPEC_VERSION, protocol.md, full templates/ tree, doctrine/plans/journal/procedures/ subdirs, and seven-field map.md + state.md stubs. Idempotent. `--force` escape hatch that never deletes user content. 10 tests for init + 2 sync tests for the `_data/` bundle, all green.

## Context

The refreshed Phase B plan's second Work item. With the scaffold + `cortex version` landed in PR #5, the natural next slice is the command that gives the CLI its first user-visible job: scaffolding a new project.

## Design decisions worth recording

- **Package-data bundling via hatchling `force-include`.** `pyproject.toml` maps `src/cortex/_data` → `cortex/_data` at wheel-build time. For editable installs (`uv sync`), the data lives under `src/cortex/_data/` on disk and is accessed via `importlib.resources.files("cortex._data")`. Same code path in dev and in a wheel.
- **`.cortex/` is the single source of truth; `src/cortex/_data/` is a copy.** The copies are a build-time concession (wheels can't reach outside the package). Drift risk is real. Mitigated by `tests/test_data_sync.py` which reads both and fails if they differ — enforced in the same test suite as the behavior tests.
- **`--force` preserves user content.** The initial instinct was "wipe and rewrite." But doctrine/plans/journal/procedures/ are the whole point of Cortex; a `--force` that blows them away would be a landmine. Scope of `--force`: rewrite scaffold files (SPEC_VERSION, protocol.md, templates/, map.md/state.md stubs) only. Existing `0001-*.md` doctrine entries, journal entries, etc. are untouched either way.
- **`--path` defaults to CWD via `default=Path.cwd`.** Passing the callable (not `Path.cwd()`) means click resolves it per-invocation, so tests can pass `--path tmp_path` without global-state games.
- **Map.md and state.md seven-field stubs use `Incomplete: [all sources]` explicitly.** This is the whole point of the `Incomplete:` field — a scaffolded derived layer that hasn't been regenerated yet. Consumers see `Incomplete: non-empty` and know to treat the file as a placeholder.
- **No Doctrine 0001 seed.** The earlier plan suggested seeding `0001 — Why this project exists`. Dropped on reflection: the entry's title and content are project-specific; a generic stub would be wrong-looking enough that users would delete it immediately. Templates under `doctrine/candidate.md` cover the shape; users author their first Doctrine entry by hand.

## Consequences / action items

- [x] Phase B plan's `cortex init` Work item marked done.
- [x] state.md Shipped-recently updated.
- [x] Tests: 12 new tests (10 for init, 2 for data sync). Cumulative suite now at 17 green.
- [ ] Next slice: `cortex doctor` structural checks. The CLI can now scaffold; doctor is what proves the scaffold is conformant. This repo's own `.cortex/` becomes the second integration test subject (first was a fresh tmp_path).
- [ ] Deferred: `cortex init --git` (auto-`git init` + stage the scaffold). Not in the plan. File a journal note if a user asks.

## What we'd do differently

- **`force-include` path works for wheels but not for sdists by default.** The sdist would not include `src/cortex/_data/` unless we also declare it in `[tool.hatch.build.targets.sdist]`. Existing sdist config lists `src/cortex`, `README.md`, `SPEC.md`, `LICENSE` — which includes `src/cortex/_data/` transitively because it's under `src/cortex/`. So: incidentally covered. Worth a test if we start publishing to PyPI. Deferred.
- **The `tests/test_data_sync.py` pattern is generalizable.** Any package that bundles human-maintained assets as package data probably wants an equivalent sync test. Could promote to a Procedure (SPEC § 3.6) once the pattern repeats in another codebase.
