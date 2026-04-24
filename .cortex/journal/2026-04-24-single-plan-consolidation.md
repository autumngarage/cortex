# Plans phase-c/d/e → cancelled; consolidated into plans/cortex-v1

**Date:** 2026-04-24
**Type:** plan-transition
**Trigger:** T1.3 (three plan Statuses changed) + T1.1 (diff touches .cortex/plans/ and PLAN.md)
**Cites:** plans/cortex-v1, plans/phase-c-authoring-and-state, plans/phase-d-integration, plans/phase-e-synthesis-and-governance, journal/2026-04-24-case-study-driven-roadmap, journal/2026-04-23-phase-c-reordered, doctrine/0001-why-cortex-exists, ../../principles/documentation-ownership.md, ../../SPEC.md

> The three active phase plans created during the 2026-04-23 reorder (`phase-c-authoring-and-state`, `phase-d-integration`, `phase-e-synthesis-and-governance`) are cancelled and consolidated into a single active plan at [`plans/cortex-v1`](../plans/cortex-v1.md) with phases tracked as `### Phase C / D / E` sub-sections under `## Work items`. Every work item is absorbed verbatim; no scope changes beyond folding in the five case-study-driven follow-ups from this morning's journal entry into their phase homes. Root `PLAN.md` deleted (redundant with the three plan files and with the new consolidated one). Cleanup reason: "so it's easy to stay focused" — user ask.

## Context

Yesterday (2026-04-23) the original Phase C plan was cancelled and reordered into three phase plans along risk lines ([`journal/2026-04-23-phase-c-reordered`](./2026-04-23-phase-c-reordered.md)). That reorder was structurally correct — each phase had a distinct goal, a measurable dogfood gate, and a `Blocked-by:` relation to the previous one. SPEC § 3.4 supports exactly this shape: one Goal-hash per file, one Status per file, `Blocked-by:` as an explicit cross-file relation.

Today's case-study synthesis ([`journal/2026-04-24-case-study-driven-roadmap`](./2026-04-24-case-study-driven-roadmap.md)) produced five new work items spanning multiple phases and then asked where to place them. The act of distributing five items across three plan files plus updating `state.md`'s P0/P1/P2 prose exposed a latent cost: every scope decision on this project has been pinging between four files (PLAN.md + three phase plans) plus state.md, and adding items pushes the ping count higher. The user observation — *"we need one plan file so it's easy to stay focused"* — is the organizational read the case-study session made concrete.

Per [`principles/documentation-ownership.md`](../../principles/documentation-ownership.md), volatile facts should have exactly one canonical owner. PLAN.md's `## Phases` section (lines 86-125) restated each phase plan's work items after saying "Full plan: [link]" — the exact dual-authority anti-pattern that principle names. The three phase plans weren't redundant with *each other* (each had distinct grounding + criteria), but PLAN.md was redundant with *all three* simultaneously.

## Transition

- **From:** 3 plans `active` + 1 root-level `PLAN.md` operating as parallel authority
- **To:** 1 plan `active` ([`plans/cortex-v1`](../plans/cortex-v1.md), Goal-hash `9e961737`, title "Ship Cortex v1.0"); 3 plans `cancelled` with `Promoted-to: plans/cortex-v1`; `PLAN.md` deleted
- **Reason:** focus (one file, one surface for scope decisions) + documentation-ownership hygiene (one canonical source per fact instead of two)

## Outcome against success criteria

This transition does not ship a phase, so the predecessor plans' Success Criteria pass through unchanged into the new plan's `## Success Criteria` section (phases 1, 2, 3 map directly to old phase-c, phase-d, phase-e exit bars). The new plan's Goal-hash (`9e961737`, computed via `cortex.goal_hash.normalize_goal_hash`) is a *different* hash from any of the cancelled plans' hashes — this is intentional: the goal is now "ship v1.0" (the sum), not "ship phase C/D/E" (the parts). SPEC § 4.9 multi-writer-collision detection is preserved because each cancelled plan keeps its original Goal-hash and the new plan's hash is unique.

## Deferred items

No work is deferred by this transition. Every work item and every deferred follow-up from the three cancelled plans maps 1:1 into a location in `plans/cortex-v1`:

| Cancelled-plan location | New home in `plans/cortex-v1` |
|---|---|
| `phase-c-authoring-and-state` § Work items | `### Phase C — Authoring and deterministic state` |
| `phase-c-authoring-and-state` § Follow-ups (deferred) | absorbed into cortex-v1 `## Follow-ups (deferred)` |
| `phase-c-authoring-and-state` T1.7-deferral note | absorbed into the `## Follow-ups (deferred)` § T1.7 deferral note (actually into the Phase D work-item list as an explicit bullet; see plan) |
| `phase-d-integration` § Work items | `### Phase D — Composition integrations` |
| `phase-d-integration` § Follow-ups (deferred) | absorbed into cortex-v1 `## Follow-ups (deferred)` |
| `phase-e-synthesis-and-governance` § Work items | `### Phase E — Synthesis and governance` |
| `phase-e-synthesis-and-governance` § Follow-ups (deferred) | absorbed into cortex-v1 `## Follow-ups (deferred)` |
| Case-study items #1 + #2 (from journal/2026-04-24-case-study-driven-roadmap) | `### Phase C` as work items |
| Case-study items #3, #4, #5 | `### Phase E` as work items |
| PLAN.md § Why (grounding) | cortex-v1 `## Why (grounding)` (rewritten) |
| PLAN.md § Success Criteria | cortex-v1 `## Success Criteria` (restated per phase) |
| PLAN.md § Approach | cortex-v1 `## Approach` (rewritten) |
| PLAN.md § Known Limitations | cortex-v1 `## Follow-ups (deferred)` |
| PLAN.md § Follow-ups (deferred) | cortex-v1 `## Follow-ups (deferred)` |
| PLAN.md § Where to start next session | state.md § Current work (already) |

Per SPEC § 4.2 — every deferred item resolves to another plan or journal entry in the same commit. No orphans.

## Consequences / action items

- [x] `plans/cortex-v1.md` created with seven-field frontmatter, Goal-hash `9e961737`, all required sections, Phase C/D/E sub-sections under `## Work items`, and the five case-study follow-ups in their phase homes.
- [x] `plans/phase-c-authoring-and-state.md` → Status: cancelled, Cancelled: 2026-04-24, Promoted-to: plans/cortex-v1. Supersede banner added to the body; original scope preserved for historical reference.
- [x] `plans/phase-d-integration.md` → same treatment.
- [x] `plans/phase-e-synthesis-and-governance.md` → same treatment.
- [x] `PLAN.md` at repo root deleted.
- [x] `CLAUDE.md` references to `PLAN.md` updated to point at `.cortex/plans/cortex-v1.md` (three places: memory-discipline rule, Key Files table, State & Config list).
- [x] `AGENTS.md` § High-scrutiny paths updated similarly.
- [x] `README.md` two references updated (Status paragraph and `## Status and plan` section).
- [x] `.cortex/map.md` stub updated to reference the single plan.
- [x] `.cortex/state.md` collapsed from P0/P1/P2 + Case-study-driven-follow-ups into a single `## Current work` section pointing at `plans/cortex-v1`.
- [ ] Verify `cortex doctor` runs clean after the full restructure; fix any dangling references surfaced.
- [ ] Retrospective check at Phase C exit: did collapsing to one plan file actually improve focus, or did we lose something by giving up per-phase Status transitions? Adjust if the evidence disagrees with today's reasoning.

## What this forecloses

**The phase-plan-per-phase pattern is over for this project.** Future phase boundaries (past v1.0) can be tracked as new plan files when appropriate, but within v1.0's trajectory they live as sub-sections. If a SPEC amendment is ever needed to formalize "one plan can cover multiple phase-like sub-goals," this journal entry is the reference. None is needed today — SPEC § 3.4's contract (one Goal-hash, one Status, one H1) is still satisfied by a plan whose work items are internally sub-sectioned.

**Per-phase `Blocked-by:` relations become implicit.** The old `phase-d-integration` had `Blocked-by: phase-c-authoring-and-state`; the old `phase-e` had `Blocked-by: phase-d`. The consolidated plan's `### Phase D` and `### Phase E` sub-sections document "blocked on Phase C / D" in prose instead. This is a real information loss for tooling that walks Blocked-by chains, but that tooling does not yet exist — adding it back (via section-level metadata) is a future enhancement if and when it proves useful, not something to reconstruct preemptively.

**The user observation that drove this — "so it's easy to stay focused" — is a design principle worth naming.** Documentation-ownership hygiene stops you from duplicating facts; focus-hygiene stops you from fragmenting a single concern across multiple surfaces *even when each surface is internally consistent*. Three well-written plan files can still fragment one concern. This entry is the working example.
