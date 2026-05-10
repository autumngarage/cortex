# Token-budget instrumentation shipped for agent workflows

**Date:** 2026-05-09
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/context-integrity-production, doctrine/0008-context-integrity-build-system, https://github.com/autumngarage/cortex/issues/244

> Cortex now makes agent context budget consumption visible in normal manifest output and warns when generated Journal drafts exceed the reviewable handoff target.

## Context

Issue #244 was the first production slice from the context-integrity roadmap. Cortex already had a budgeted manifest implementation and JSON diagnostics, but the normal agent-facing output did not make consumed budget, word count, over-budget state, or total omitted entries obvious. Journal drafts also had no size hint, so an auto-generated entry could become the new oversized normal without any visible warning.

Conductor was asked for a second-opinion slice boundary. Its useful recommendation was to keep the work deterministic and small: extend manifest metadata, add a static journal draft validator, and avoid live LLM or semantic-provider dependencies.

## What we decided

Ship deterministic budget instrumentation now:

- `cortex manifest` shows estimated tokens, estimated words, budget status, and omitted-entry count in the default header.
- `cortex manifest --json` exposes top-level `used_words`, `omitted_count`, `over_budget`, and `over_budget_tokens`, and per-section `used_words`.
- `cortex journal draft` warns above 1200 estimated tokens and names `--allow-large` as the explicit acknowledgment path.
- agent-facing docs name the budget targets: 8k tokens for normal coding startup, 4k for delegation, and ~1200 for generated Journal entries.

## Consequences / action items

- [x] Add regression tests for default manifest budget visibility and JSON metadata.
- [x] Add regression tests for oversized Journal draft warning and `--allow-large` acknowledgment.
- [x] Update `.cortex/protocol.md`, `README.md`, and `docs/retrieve.md` with budget targets.
- [x] Mark #244 complete in [context-integrity-production](../plans/context-integrity-production.md).
