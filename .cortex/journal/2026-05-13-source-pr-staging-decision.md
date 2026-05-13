# We chose source-PR staging as the default T1.9 authoring path

**Date:** 2026-05-13
**Type:** decision
**Trigger:** T2.1
**Cites:** doctrine/candidate.md, plans/context-integrity-production.md, protocol.md §2 T1.9, https://github.com/autumngarage/cortex/issues/207

> We decided to stage `pr-merged` journal entries on source PRs and keep post-merge automation in verifier mode.

## Context

Issue #207 requested an ADR/Doctrine comparison of three paths for T1.9 (`pr-merged`) entries: status quo post-merge writer, pre-merge source-PR staging, and hybrid fallback. Dogfood history showed recurring meta churn from follow-up journal PRs plus quality drift toward metadata restatement instead of author-time rationale.

The doctrine candidate now records the comparison and recommendation in `.cortex/doctrine/candidate.md`, including the concrete repo measurements used in this decision.

## What we decided

We chose **pre-merge source-PR staging** as Cortex’s encouraged first-party path.

- Primary writing happens before merge (`cortex journal stage --type pr-merged --pr <n>` in follow-up implementation work).
- `open-pr.sh` / `merge-pr.sh` become the enforcement path for staged presence.
- `cortex-pr-merged-hook.sh` shifts from writer to verifier so T1.9 remains auditable without generating a second PR by default.

Hybrid fallback was rejected as default because it creates two long-lived writer paths for one trigger.

## Consequences / action items

- [ ] Implement the staged-authoring command surface and script wiring in a follow-up implementation PR (tracked by issue #207).
- [x] Mark plan item #207 as design-decided with citation to `doctrine/candidate.md`.
