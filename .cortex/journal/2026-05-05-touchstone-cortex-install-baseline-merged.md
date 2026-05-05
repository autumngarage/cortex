# Touchstone Cortex install baseline merged

**Date:** 2026-05-05
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/cortex-v1.md, journal/2026-05-05-conductor-cortex-install-baseline-merged.md

> Touchstone is now the second v0.9.0 dogfood target with Cortex installed, audit config committed, and install friction filed instead of hidden.

## Context

The v0.9.0 dogfood gate requires Cortex to run independently on conductor, touchstone, and vesper. Touchstone validates the composition boundary because it owns shared agent workflow files and propagates `principles/`, `scripts/`, `.codex-review.toml`, and `.pre-commit-config.yaml` downstream.

While the install was in progress, Touchstone PR #149 landed first and completed the generic `.cortex/` scaffold. The follow-up install PR #151 therefore stayed smaller: it added audit config, steering imports, Touchstone ownership Doctrine, an install baseline Journal, and `.gitignore` ignores for Cortex runtime artifacts.

## What shipped

- `autumngarage/touchstone` PR #151 merged on 2026-05-05.
- `.cortex/config.toml` now audits Touchstone's Homebrew tap, sibling repo paths, Touchstone root steering files, and the vanguard `CLAUDE.md` Conductor-router cross-link.
- Cortex imports were added to `CLAUDE.md` and `AGENTS.md`.
- Cortex did not modify Touchstone-managed write paths: `principles/`, `scripts/`, `.codex-review.toml`, `.pre-commit-config.yaml`.
- Touchstone's full fast suite passed before merge, and Conductor review was clean.

## Follow-ups filed

- [ ] cortex#123 — README placeholder `~/Repos/my-*` paths are audited as missing sibling repos.
- [ ] cortex#124 — unrelated version strings are compared against every configured GitHub/Homebrew release surface.
- [ ] cortex#125 — config-reference docs still mention removed `gh_release`.
- [ ] touchstone#150 — fast tests pollute or depend on the real `~/.touchstone-projects` registry.

## Consequences

The next install target is vesper. Touchstone audit output is intentionally not treated as clean yet; the warnings are visible and tracked upstream, which is the correct dogfood behavior until cortex#123 and cortex#124 are fixed.
