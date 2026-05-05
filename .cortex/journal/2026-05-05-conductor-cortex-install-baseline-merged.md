# Conductor Cortex install baseline merged

**Date:** 2026-05-05
**Type:** decision
**Trigger:** human-authored
**Cites:** plans/cortex-v1, https://github.com/autumngarage/conductor/pull/178, https://github.com/autumngarage/conductor/issues/175, https://github.com/autumngarage/conductor/issues/176, https://github.com/autumngarage/cortex/issues/119, https://github.com/autumngarage/cortex/issues/120, https://github.com/autumngarage/cortex/issues/121

> The conductor install gate is complete: Cortex is upgraded and audit-configured on conductor, with baseline findings journaled in the conductor repo.

## Context

`plans/cortex-v1.md` requires installing Cortex on conductor before the v0.9.0 three-target dogfood gate can move on to touchstone and vesper. The conductor repo already had an old v0.2.3-era `.cortex/` scaffold, so the work became an upgrade and baseline pass rather than a fresh directory creation.

Conductor PR #178 merged on 2026-05-05. It upgraded the scaffold to SPEC v0.5.0, added `.cortex/config.toml`, regenerated state, fixed stale release claims caught by the audit, and wrote conductor journal `2026-05-05-cortex-install-baseline.md`.

## What we decided

Mark the conductor install item in `plans/cortex-v1.md` complete. The acceptance evidence for this item is:

- `autumngarage/conductor` PR #178 merged cleanly after Conductor review.
- `cortex doctor --audit-instructions` in conductor checks 12 claims and reports all verified.
- `cortex doctor` in conductor reports 0 errors; remaining warnings are explicitly captured in conductor's baseline journal.
- `cortex manifest --budget 8000` and `cortex next` produce a usable session-start surface for conductor's current work.

## Consequences / action items

- [x] `plans/cortex-v1.md` conductor install checkbox is marked complete.
- [x] conductor#174 was fixed by PR #178: stale release claims caught by Cortex audit were updated.
- [ ] conductor#175: `conductor init` should refresh managed delegation block version markers.
- [ ] conductor#176: replace placeholder review priorities in conductor `AGENTS.md`.
- [ ] cortex#119: clarify or fix `[audit-instructions].siblings` syntax.
- [ ] cortex#120: strip markdown backticks from audit-instructions URL claims.
- [ ] cortex#121: `refresh-state` should ignore archived plans consistently.
