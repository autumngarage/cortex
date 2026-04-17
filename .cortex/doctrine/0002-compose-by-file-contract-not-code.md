# 0002 — Compose by file contract, not code

> Cortex integrates with Touchstone and Sentinel through the `.cortex/` filesystem layout, never by code import or shared library. Each tool runs alone; each reads others' outputs if present and degrades gracefully if not. This is the same pattern Sentinel already uses for Touchstone detection.

**Status:** Accepted
**Date:** 2026-04-17

## Context

Three tools with related responsibilities invite coupling. The natural pull is: shared library for provider abstraction, shared schema module for `.cortex/`, shared git helpers. Each seems reasonable in isolation.

But each also breaks the "install independently, useful alone" property the first two tools hold. Touchstone and Sentinel have no code dependency on each other — they communicate through git itself (Sentinel does `git push`; Touchstone's pre-push hook fires if installed). This is a load-bearing property: it's what keeps each tool simple, releasable on its own cadence, and uninstallable without cascading damage.

Adding Cortex threatens this. If Cortex needs LLM synthesis (Map regeneration, Journal drafting), the natural move is to reuse Sentinel's provider abstraction. That's the move to refuse.

## Decision

Cortex composes with Touchstone and Sentinel exclusively through the filesystem layout defined in SPEC.md. Specifically:

- Cortex does not import Sentinel or Touchstone Python/bash code; it does not subprocess into their CLIs for functional output.
- Cortex synthesis (Map, State, Journal drafts) shells out to `claude -p` directly — the same convergent-CLI pattern Sentinel uses. Each tool calls the provider CLI independently; no shared provider layer.
- Cortex *reads* `.sentinel/runs/` and `.sentinel/verifications.jsonl` when present (richer State regeneration). It ignores them when absent.
- Cortex *reads* `.touchstone-config` for project-type hints when present. It does not require it.
- Sentinel and Touchstone may *write* to `.cortex/` via hooks (Sentinel end-of-cycle → Journal entry; Touchstone PR-merge → Journal draft). These are per-project opt-in behaviors, not shared code.

## Consequences

- Installing any one tool is useful. Installing all three compounds value. No tool's install breaks when another is uninstalled.
- Three independent release cadences. Cortex's spec version and CLI version are independent from Sentinel's and Touchstone's.
- Some duplication of shape (each tool has its own way of calling `claude` CLI, writing journals, detecting dependencies). This is acceptable — the cost of convergent patterns is small compared to the cost of shared library coupling across three independent distributions.
- Integration changes are localized. Wiring a new Cortex-writer behavior into Sentinel requires changes in Sentinel; it does not propagate through Cortex's release.
- Test matrix is easier: each tool tests its own behavior with fake `.cortex/` fixtures rather than requiring a running sibling.

## Related

- Sentinel's existing pattern: detects Touchstone via `shutil.which("touchstone")` and prints a recommendation, never blocks. Cortex extends this.
- Unix philosophy: small tools that compose through files, not through linked libraries.
