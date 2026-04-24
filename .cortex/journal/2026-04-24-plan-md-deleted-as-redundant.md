# PLAN.md deleted as redundant after plan consolidation

**Date:** 2026-04-24
**Type:** decision
**Trigger:** T1.4 (file deletion exceeding 100 lines — PLAN.md was 149 lines)
**Cites:** journal/2026-04-24-single-plan-consolidation, plans/cortex-v1, ../../principles/documentation-ownership.md

> Documents the T1.4 fire from deleting `PLAN.md` (149 lines) in the same commit as the plan-consolidation transition. The deletion is a separate decision from the plan transition and warrants its own `Type: decision` entry per Protocol § 2.

## Context

The single-plan consolidation ([`journal/2026-04-24-single-plan-consolidation`](./2026-04-24-single-plan-consolidation.md)) collapsed three active phase plans into [`plans/cortex-v1`](../plans/cortex-v1.md). That transition is `Type: plan-transition` (T1.3 — plan status changed). The same commit also deleted root-level `PLAN.md` (149 lines), which fires Protocol Trigger T1.4 (file deletion exceeding 100 lines, default threshold) and expects a `Type: decision` entry — a separate trigger from the plan-status changes.

This entry exists to cover that T1.4 fire so `cortex doctor --audit` stays clean. Codex review on PR #30 caught the gap.

## What we decided

`PLAN.md` at the repo root is **deleted, not archived**. It restated the work items from each phase plan after pointing at them ("Full plan: [link]") — the exact dual-authority anti-pattern that [`principles/documentation-ownership.md`](../../principles/documentation-ownership.md) names. Three phase plans plus PLAN.md plus state.md formed a four-way ping where every scope decision had to update each surface; consolidation removed the phase plans (cancelled with supersede banners pointing at `plans/cortex-v1`) and the same act made PLAN.md redundant with the new single plan.

Why **delete** rather than archive (e.g., move to `drafts/` like the vision drafts):

1. **PLAN.md was never canonical.** It was always a summary of the phase plans — the phase plans had the authoritative content. With the phase plans cancelled (Status: cancelled, Promoted-to: plans/cortex-v1) the supersede chain is durable; PLAN.md added no information that survives the consolidation.

2. **Drafts/ is for content that has standalone historical value.** Vision v1/v2/v3 in `drafts/` document the actual evolution of the project's framing — they're load-bearing for understanding *how* the project's vision sharpened. PLAN.md's content has no equivalent standalone value: every section was either restated in the phase plans (now cancelled with traceable history) or absorbed into `plans/cortex-v1` (`## Why (grounding)`, `## Success Criteria`, `## Approach`, `## Follow-ups (deferred)`).

3. **Git history preserves it.** If anyone needs to see what PLAN.md said, `git show a6d596b:PLAN.md` returns it unchanged. The decision to delete vs. archive is about *which artifacts deserve a path in the working tree*, not about whether the content is preserved.

## Consequences / action items

- [x] `PLAN.md` deleted in the same commit as the plan consolidation (cbcf92d on branch docs/consolidate-plans-into-cortex-v1).
- [x] References to `PLAN.md` updated in `CLAUDE.md`, `AGENTS.md`, `README.md`, `.cortex/state.md`, `.cortex/map.md`, and source comments in `src/cortex/audit.py` + `src/cortex/validation.py` to point at `.cortex/plans/cortex-v1.md`.
- [x] T1.4 fire covered by this `Type: decision` entry.
- [ ] Watch for any external references to `PLAN.md` (e.g., from the `homebrew-cortex` tap README, sentinel/touchstone repos referencing back to cortex). None are known to exist; if any surface, update them to point at `.cortex/plans/cortex-v1.md`.

## What this forecloses

**No more root-level PLAN.md in this project.** Phase boundaries past v1.0 can spawn new plan files inside `.cortex/plans/`, but the root-level PLAN.md pattern is over — every plan from now on lives where SPEC § 3.4 says plans live. If a future `cortex init` template wants to ship a root-level `PLAN.md` for projects that don't (yet) have a `.cortex/`, that is a separate decision and a different file.
