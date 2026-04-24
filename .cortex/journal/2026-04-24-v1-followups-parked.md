# Items parked for v1.x+ — explicit deferrals from plans/cortex-v1

**Date:** 2026-04-24
**Type:** decision
**Trigger:** T1.1 (diff touches .cortex/plans/) + T2.1 (deferral phrasing)
**Cites:** plans/cortex-v1, journal/2026-04-24-single-plan-consolidation, journal/2026-04-24-case-study-driven-roadmap, journal/2026-04-23-phase-c-reordered, doctrine/0005-scope-boundaries-v2, ../../SPEC.md § 4.2

> Records the items in [`plans/cortex-v1`](../plans/cortex-v1.md) `## Follow-ups (deferred)` that have no in-tree resolution target — so SPEC § 4.2's "no orphan deferrals" rule is satisfied with this journal entry as the resolution.

## Context

SPEC § 4.2: *"Any item moved out of a Plan's scope must resolve to another Plan or a Journal entry within the same commit. No orphan deferrals."* The consolidation that produced [`plans/cortex-v1`](../plans/cortex-v1.md) ([`journal/2026-04-24-single-plan-consolidation`](./2026-04-24-single-plan-consolidation.md)) absorbed every active work item from the three cancelled phase plans, but the union of their `## Follow-ups (deferred)` sections includes several items that pointed at *future* work — "v1.1+", "v1.x", "follow-up" — without naming a successor plan or journal that exists today.

Codex review on PR #30 flagged this as the orphan-deferral pattern SPEC § 4.2 prohibits. The fix is one of: (a) write a real successor plan for each parked item, or (b) write one journal entry that names them as parked-for-after-v1.0 and have each item cite it. (a) is premature — these items are explicitly post-v1.0 and forecasting their plan shapes today would invent scope. (b) keeps the discipline (a durable in-tree resolution target) without inventing scope.

This entry is option (b).

## What we decided

The following items are **deliberately deferred until after Cortex v1.0 ships**. Each item in [`plans/cortex-v1`](../plans/cortex-v1.md) `## Follow-ups (deferred)` cites this entry as its resolution target (the place that records *why* it's parked and *when* to revisit). Re-evaluate during v1.0 retrospective; promote to a real plan only when the v1.0 evidence base supports prioritizing it.

### Parked items (v1.x+)

1. **Cross-repo journal import.** Opt-in sibling-repo release-event mirroring (e.g., `homebrew-<project>` release → journal entry in `<project>`). Depends on T1.10 landing first (case-study item #2 in Phase C). Revisit when `--audit-instructions` (Phase E case-study item #3) is dogfooded — that command will surface the cross-repo trust gap and tell us whether import is the right shape vs. on-demand verification.

2. **Promotion enforcement automation.** v1.0 has manual promotion (human decides via `cortex promote <id>`); automated Journal-to-Doctrine graduation gate is a v1.x concern. Revisit once `.index.json` writer (Phase E) has produced ≥ 30 days of real promotion-queue data on this repo to inform what "automatic enough" means.

3. **Cortex-as-protocol separation.** If a second implementation of the protocol appears (e.g., a JS reader, a Go writer), extract `SPEC.md` to its own `autumngarage/cortex-spec` repo so the spec versions independently of any one implementation. Not needed at one implementation. Revisit when a second implementation is proposed.

4. **Single-writer assumption.** Two humans or two agents writing `.cortex/` concurrently will conflict on the same file. Append-only Journal helps (each write is a new file); a full CRDT-ish merge story for derived layers (state.md, map.md) and for in-place Plan/Doctrine edits is a v1.x+ concern. Revisit when the first concurrent-write conflict occurs in practice.

5. **Retrofit historical T1.9 journal entries.** `cortex doctor --audit` flagged ~14 unmatched T1.9 fires on this repo (the `pr-merged` template shipped after most of those merges). Backfill or mark "pre-template" is a follow-up. Not on the v1.0 path because the audit warnings are loud-but-non-blocking and cluttering the audit output to fix history-pre-template adds noise, not signal. Revisit if/when historical entries become load-bearing for any synthesis (`refresh-state --enhance` or `refresh-map`).

6. **Embedding / semantic retrieval.** Already cited [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #1 in `plans/cortex-v1`. Listed here for completeness — the Doctrine entry IS the resolution target; this journal does not duplicate that resolution.

7. **Portfolio view (Lighthouse).** Already cited [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md). Listed here for completeness; same caveat as #6.

## Consequences / action items

- [x] [`plans/cortex-v1`](../plans/cortex-v1.md) `## Follow-ups (deferred)` updated to cite this entry on each formerly-orphan item (items 1–5 above; items 6–7 already had Doctrine resolutions).
- [ ] At v1.0 retrospective: walk this list, decide for each whether evidence supports promoting to a real plan or extending the park.
- [ ] If any parked item gains urgency before v1.0 ships, write a successor plan and update [`plans/cortex-v1`](../plans/cortex-v1.md) to cite the new plan instead of this entry.

## What this forecloses

This journal entry **is not a plan** — it does not commit to building any of the parked items. It is the in-tree resolution target SPEC § 4.2 requires for items deferred *out of v1.0 scope*. Treating it as a plan (e.g., counting items off, promising delivery) would re-introduce the scope-creep problem the consolidation just solved.

It also **does not foreclose on early promotion.** If during Phase D dogfood the cross-repo import gap becomes the bottleneck, item 1 graduates to a real plan and `plans/cortex-v1` updates to point at the new plan instead of this entry. The park is the default, not the contract.
