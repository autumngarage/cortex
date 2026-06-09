# Roadmap refinement: milestones become stage authority; backlog reconciled against shipped substrate

**Date:** 2026-06-09
**Type:** decision
**Trigger:** T1.1 (diff touches `.cortex/plans/hosted-decision-reviewer.md`)
**Cites:** plans/hosted-decision-reviewer.md, journal/2026-06-09-hosted-decision-reviewer-plan-adopted, journal/2026-06-09-pr-merged-pr477

> A full review of the 12 plan files and 164 open product issues (multi-agent,
> adversarially verified: 52 confirmed findings of 74 checked) refined the
> existing roadmap rather than rebuilding it. Milestones are now the single
> stage authority; the retired `stage-N` label numbering is deleted; the
> backlog is reconciled against the #477-#483 substrate wave; the active plan
> carries wave ordering and per-stage exit gates.

## Context

The issue breakdown (#306-#475) and the active plan were generated hours
before the substrate wave (PRs #477-#483) landed and were never reconciled.
Consequences found by review: ten open issues described already-shipped work;
a dozen more had stale premises that would cause an agent to rebuild shipped
code; the `stage-N` labels encoded the retired `cortex_roadmap.md` numbering
while milestones followed the canonical `cortex_master_plan.md` ordering; the
plan file and `state.md` understated the issue range as "#444-#475"; and no
T1.9 journal entries existed for any merge since 2026-05-25 (post-merge hook
outage, fixed in #303). An autonomous dispatcher (autumn-alchemist) had
documented near-collisions on the stale issues, stopped only by provider
billing errors, and retries on future ticks.

## What we decided

1. **Milestones are the single stage authority**, per `cortex_master_plan.md`
   (canonical 2026-06-09): Stage 0 local proof → Stage 1 hosted Railway core →
   Stage 2 GitHub reviewer → Stage 3 Slack ledger console. The `stage-0/1/2/3`
   labels (retired numbering, half-applied) were deleted, trackers and dogfood
   issues retitled to functional names, and stale "Roadmap fit" body lines
   rewritten. Where the Obsidian companion docs conflict on Slack-vs-GitHub
   ordering, the master plan's build order wins.
2. **The backlog reflects shipped reality.** Twelve issues closed with
   evidence (10 shipped-by-substrate: #306, #311, #312, #317, #321, #324,
   #364, #365, #366, #383; #361 consolidated into #358; #264 complete-with-
   handoff). Residuals were cut to new issues rather than left implicit
   (#484 glob scope matching). ~60 issue bodies rescoped to cite the shipped
   modules so no agent rebuilds them.
3. **Dispatch is gated.** All open product issues carry `alchemist-skip`
   until their wave is current and their body is dispatch-ready. This
   converts the dispatcher race into a controlled gate.
4. **The active plan owns wave ordering and exit gates.** Stage 0 is ten
   dependency-respecting waves with the #337 report as the gate artifact
   (>=70% hand-graded advisory bar); Stage 1-3 each carry waves and gates;
   Future buckets name activation conditions (#454 spine gate). Tracker #485
   created for the previously tracker-less Stage 1.
5. **Milestone moves:** #379 → Future-blocking (Wilson math), #380 → Stage 2
   (override classification after feedback capture), #399/#400 → GTM (pricing
   deferral), #458 → Stage 0 (Hermes research, sequenced #456 → #457 → #458),
   #454 → Stage 3 (spine gate), #384 → Stage 0 (publisher verification filed
   early as the wall-clock long pole).
6. **Self-conformance fixes:** `.cortex/SPEC_VERSION` bumped 0.5.0 → 1.1.0 to
   match the spec the scaffold actually tracks; T1.9 backfill entries written
   for the unjournaled 2026-05-25 → 2026-06-09 window.

## Consequences / action items

- [x] GitHub hygiene applied (closes, moves, retitles, label deletion,
  tracker #485, skip-labels) — resolved to: this entry + the issue audit
  trail on GitHub.
- [x] Plan file refined with waves/gates — resolved to:
  `plans/hosted-decision-reviewer.md`.
- [ ] Build proceeds at Stage 0 Wave 1 (#310, #332, #344, #327, #363) —
  resolved to: `plans/hosted-decision-reviewer.md` § Pickup pointer.
- [ ] The Obsidian `cortex_master_plan.md` "Canonical Sources" line still
  says issues "#444-#468 own the detailed task breakdown" (true span:
  #306-#475+) — resolved to: external-vault fix noted in the same session;
  outside repo PR flow.
