# Decision — open Phase C plan and refresh build-plan docs

**Date:** 2026-04-18
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/phase-c-first-synthesis, plans/phase-b-walking-skeleton, PLAN.md, .cortex/state.md

> Same-commit companion to the Phase B plan-transition: documents the decisions made in this commit that touched `.cortex/plans/`, `.cortex/state.md`, and `PLAN.md` — so T1.1 has a matching Journal record distinct from the T1.3 plan-transition entry.

## Context

The Phase B exit commit does three things in one unit: transitions `plans/phase-b-walking-skeleton` from `active` to `shipped` (T1.3, recorded in `journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md`), opens a new `plans/phase-c-first-synthesis` (T1.1), and refreshes `.cortex/state.md` + `PLAN.md` to describe the new priority layout (T1.1).

SPEC § 3.5 says "one event per file" for Journal; the plan-transition and the new-plan-plus-doc-refresh are different events that happen to share a commit, so they get separate entries.

## What happened / what we decided

- **Opened `plans/phase-c-first-synthesis`** with frontmatter, grounding citations to `doctrine/0003-spec-is-the-artifact` and `doctrine/0005-scope-boundaries-v2`, 10 measurable success criteria, and a work-item list that enumerates each deferred item from the Phase B exit journal.
- **Refactored `.cortex/state.md`** so Phase B moves to a "Closed" block preserving the shipped checklist, Phase C is the new P0 with a link to the plan file, Phase D and Phase E are P1/P2, and Open Questions is retitled "Phase C kickoff" with the specific questions that actually need answering now.
- **Refreshed `PLAN.md`'s "Where to start next session" section** to say Phases A+B shipped, Phase C is P0, and the deferred-item pointers route to the new Phase C plan.
- **Confirmed no separate `refresh-index` command yet** — that decision is deferred into the Phase C plan's first slice.

## Consequences / action items

- [x] `plans/phase-c-first-synthesis.md` exists and validates against `cortex doctor`.
- [x] Every Phase B deferred item has a concrete Phase C work-item owner (no orphan deferrals under SPEC § 4.2).
- [x] `state.md` sources/corpus count reflects the 1 active Plan (phase-c-first-synthesis).
- [ ] First Phase C slice (probably the `.cortex/.index.json` writer) opens a focused PR that starts checking boxes against `plans/phase-c-first-synthesis`.
