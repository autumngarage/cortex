# Phase B — Walking-skeleton CLI

> Ship a Cortex CLI that manipulates `.cortex/` structure without any LLM calls. End-state: `brew install cortex && cortex init` produces a spec-conformant scaffold in a fresh repo, and `cortex doctor` validates it. No synthesis yet — that's Phase C.

**Status:** active (not started; Phase A just shipped 2026-04-17)
**Written:** 2026-04-17
**Owner:** unassigned (next session)
**Cites:** [`../../SPEC.md`](../../SPEC.md), [`../../PLAN.md`](../../PLAN.md) Phase B, doctrine/[0003-spec-is-the-artifact](../doctrine/0003-spec-is-the-artifact.md)

## Why (grounding)

Phase A shipped the spec and the repo. To turn the spec into a usable protocol, we need a CLI that reads and writes `.cortex/` per SPEC.md v0.1.0 — but we should ship that machinery *before* we ship any LLM-driven synthesis. Reasons: (a) synthesis is the expensive, variable part; a walking skeleton lets us validate the structural parts in isolation; (b) `cortex doctor` is the enforcement mechanism for the cross-layer rules in SPEC.md §4 — we need it to catch violations early as soon as humans start writing `.cortex/` by hand; (c) getting the brew tap and release flow working on a small surface reduces risk when we add heavier commands later.

## Success Criteria

This plan is done when all of the following hold on a fresh macOS install:

1. `brew tap autumngarage/cortex && brew install cortex` succeeds.
2. In an empty git repo: `cortex init` creates `.cortex/SPEC_VERSION`, `.cortex/doctrine/`, `.cortex/plans/`, `.cortex/journal/`, `.cortex/procedures/`, and stubs for `map.md` and `state.md` with proper `Generated:` headers and "(pending Phase C synthesis)" placeholders.
3. `cortex doctor` on that fresh `.cortex/` returns zero and prints "spec v0.1.0 conformant."
4. `cortex status` reports per-layer freshness from parsed `Generated:` headers.
5. `cortex doctor` detects and reports each of these seeded violations: orphan deferral in a Plan, missing Success Criteria, unknown spec major version in `SPEC_VERSION`.
6. All tests pass (`uv run pytest`) — temp-dir fixtures, no mocked filesystem.
7. A git-tagged v0.1.0 release exists at `github.com/autumngarage/cortex`, with the Homebrew formula at `autumngarage/homebrew-cortex` pointing at it with the correct SHA.

## Approach

Python CLI built on `click` (matches Sentinel's stack), src-layout package under `src/cortex/`. Entrypoint via `pyproject.toml`'s `[project.scripts]`. Distribution: `uv tool install .` for source, Homebrew tap for `brew`.

The CLI's dispatch mirrors Touchstone's pattern — a thin `bin/cortex` equivalent that subcommands off to per-command modules under `src/cortex/commands/`. No daemon, no background work, no config file at this phase.

Spec validation (`cortex doctor`) is implemented as a set of pure-function checks, each keyed to a SPEC.md §4 rule. Checks return `(ok, violations)` tuples; the CLI formats them.

Brew formula mirrors the Touchstone formula's structure: `url` points at a tagged GitHub release tarball, `sha256` captured at release time, `depends_on "gh"` and `depends_on "git"`.

## Work items

- [ ] **Python project scaffold** — `pyproject.toml` with `click`, `pytest`, `ruff`, `mypy` as dev deps; `src/cortex/__init__.py` with `__version__`; `src/cortex/cli.py` click entrypoint; `uv.lock` committed.
- [ ] **`cortex version`** — prints CLI version, spec versions supported (reads `SUPPORTED_SPEC_VERSIONS` constant), install method (brew vs. source).
- [ ] **`cortex init`** — scaffolds `.cortex/` per SPEC.md §2 in CWD. Idempotent (refuses to overwrite existing `.cortex/SPEC_VERSION` unless `--force`).
- [ ] **`cortex status`** — reads `.cortex/` in CWD, parses `Generated:` headers and plan statuses, prints a compact freshness table.
- [ ] **`cortex doctor`** — runs all SPEC.md §4 validation rules, exits non-zero on any violation. Structured as one check per rule so new rules are easy to add.
- [ ] **Tests** — `tests/test_init.py`, `tests/test_status.py`, `tests/test_doctor.py`. Each uses `tmp_path` pytest fixtures, seeds a sample `.cortex/`, runs commands, asserts on output. No mocked filesystem.
- [ ] **Ruff + mypy configuration** — match Sentinel's `pyproject.toml` settings; add to `touchstone-run.sh validate` flow.
- [ ] **`autumngarage/homebrew-cortex` tap repo** — create via `gh repo create`, seed with placeholder `Formula/cortex.rb`. Formula populated at release.
- [ ] **v0.1.0 release** — tag v0.1.0 on main, `gh release create`, compute tarball SHA, update tap formula, push tap. Verify `brew install` on a clean state.
- [ ] **Release verification** — run `cortex init` on a fresh temp repo using the brew-installed binary; `cortex doctor` returns clean.

## Follow-ups (deferred to Phase C)

- Map and State regeneration (requires `claude` CLI integration — Phase C's entire scope).
- `cortex plan spawn`, `cortex journal draft` (Phase D, requires synthesis).
- Sentinel / Touchstone integration hooks (Phase E, requires stable synthesis).
- Auto-update check (lift Touchstone's `lib/auto-update.sh` pattern later; not needed at v0.1.0).
- Spec-version migration tooling (Cortex will eventually need a `cortex migrate-spec` command when v0.1.0 → v0.2.0 ships, but the first spec bump is the first time we need it).

## Known limitations at exit

- `cortex doctor` can only check structural rules (layer presence, header format, checkbox syntax, orphan deferrals). It cannot validate semantic rules (e.g., "is this Success Criterion actually measurable?") until Phase C has synthesis.
- Without Map/State regeneration, every `.cortex/` has to hand-author those files or leave them as stubs. This is acceptable for Phase B's scope — the protocol supports it.
- No cross-project state; this plan is single-project-scope by design (portfolio views are explicitly out of scope per Doctrine 0001's companion decision).
