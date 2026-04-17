# Phase B first slice shipped — Python scaffold + `cortex version`

**Date:** 2026-04-17
**Type:** decision
**Trigger:** T1.1 (diff touches `.cortex/plans/phase-b-walking-skeleton.md`) + T1.5 (dependency manifest `pyproject.toml` added)
**Cites:** plans/phase-b-walking-skeleton, journal/2026-04-17-phase-b-plan-refresh, doctrine/0003-spec-is-the-artifact

> First Phase B slice ships the Python package scaffold (`pyproject.toml`, `.python-version`, `src/cortex/`, `tests/`, `uv.lock`) and the `cortex version` command with 5 passing tests. This closes the two "Python project scaffold" and "`cortex version`" items on the Phase B plan Work-items list and gives every subsequent slice something to extend.

## Context

The refreshed Phase B plan (`plans/phase-b-walking-skeleton.md`) enumerates 13 doctor checks, `manifest`, `grep`, interactive flow, init, tests, brew tap, release — collectively a lot. The right first slice is the smallest thing that validates the whole toolchain: package scaffold with `click`, `pytest`, `ruff`, `mypy`; one subcommand (`version`); one test file; uv-managed lockfile. If that works end-to-end, the rest is additive.

## What shipped

- **`pyproject.toml`.** click as runtime dep; pytest, pytest-cov, ruff, mypy in the `dev` group. Hatchling build backend. Entrypoint `cortex = cortex.cli:cli` via `[project.scripts]`. Ruff and mypy configs baked in.
- **`.python-version`** pinning Python 3.12.
- **`src/cortex/__init__.py`.** `__version__ = "0.1.0.dev0"`; `SUPPORTED_SPEC_VERSIONS = ("0.3",)`; `SUPPORTED_PROTOCOL_VERSIONS = ("0.2",)`. Values declared at the package level so they're importable from tests and from future `cortex doctor` spec-version checks.
- **`src/cortex/cli.py`.** Click group with one subcommand (`version`). No-subcommand invocation prints help. The interactive `cortex` flow (per README UX) arrives in a later slice.
- **`tests/test_version.py`.** 5 tests using `click.testing.CliRunner`. No mocked filesystem. All green.
- **`uv.lock`** committed.

Ruff and mypy pass clean. `uv run cortex version` prints the declared versions plus a best-effort install-method label.

## Why this slice, why now

- **End-to-end toolchain validation first.** Before writing `init` or `doctor`, confirm that `uv sync` works, `pytest` runs, `ruff` lints, `mypy` types, and the click entrypoint is wired correctly. Any one of these broken would force rework on later slices.
- **The `SUPPORTED_*` constants live at the package level because every subsequent command reads them.** `cortex doctor` will parse `SPEC_VERSION` and compare against `SUPPORTED_SPEC_VERSIONS`. `cortex init` will write the current-supported spec into the target project's `SPEC_VERSION` file. Putting them in `__init__.py` (not deep in a submodule) makes them the canonical version surface.
- **Tests use `CliRunner`, not `subprocess`.** Faster, deterministic, and per the Phase B plan's "temp-dir fixtures, no mocked filesystem" rule they're still honest: the click entrypoint runs the same code path as `uv run cortex version`.

## Consequences / action items

- [x] Phase B plan `Python project scaffold` and `cortex version` Work items marked done.
- [x] state.md "Shipped recently" updated with this slice.
- [ ] Next slice: `cortex init` — copies `.cortex/protocol.md` + `.cortex/templates/` from package data into target projects. Requires package-data configuration in `pyproject.toml` and tests using `tmp_path`.
- [ ] Slice after: `cortex doctor` structural checks (directory layout, `SPEC_VERSION` parseable, `protocol.md` + `templates/` present). First structural check catches anything `cortex init` misses.

## What we'd do differently

- **Nothing novel.** This was a standard Python package scaffold; no surprises. The only noteworthy decision was choosing `hatchling` over `setuptools` (lighter for this size; matches modern uv conventions). If a contributor argues for `poetry-core` or `pdm-backend` later, the switch is cheap.
- **One minor note:** the mypy config declares a `tests.*` override that's only observed when tests are also type-checked. A stray "unused section(s)" warning surfaces when only `src/` is scanned. Benign; surface noise, not an error.
