# Mainline landing — Production readiness audit hardening

**Date:** 2026-04-27
**Type:** pr-merged
**Trigger:** T1.9
**Cites:** plans/cortex-v1, journal/2026-04-24-production-release-rerank
**Merge-commit:** n/a (direct commit to main)
**Branch:** main

Production-readiness audit fixes landed on main: crash paths now degrade visibly, local/remote validation is wired, and stale roadmap copy reflects the v0.3.0 -> v1.0 release plan.

## What shipped

- `cortex doctor --audit` reports git/audit unavailability as warnings instead of crashing on bare or malformed repos.
- `cortex manifest` reports malformed `.cortex/.index.json` promotion data as unreadable instead of raising.
- `cortex init` scan now surfaces invalid `.discover.toml` and `git check-ignore` failures instead of silently ignoring user configuration or ignore state.
- CI now runs the production gate on pushes and pull requests: ruff, mypy, pytest, and `cortex doctor`.
- Touchstone validation now uses the same project-local `uv` commands.
- README, pitch, protocol packaging, and scaffold templates now describe v0.3.0 shipped status, SPEC v0.4.0-dev, and the v1.0 release path instead of the stale Phase C/D/E framing.

## Closes / advances

- **Plans:** plans/cortex-v1 advanced: production-readiness guardrails, validation gate, and documentation freshness.
- **Doctrine:** none.
- **Journal linkage:** journal/2026-04-24-production-release-rerank remains the roadmap source this work aligns to.

## Follow-ups (deferred to future work)

- None.

## What we'd do differently

The audit pass found that local quality was strong but enforcement was implicit: mypy was red, CI was absent, and several failure modes relied on humans noticing tracebacks or stale generated assumptions. Production readiness needs the checks committed to the repo, not just remembered in an audit transcript.
