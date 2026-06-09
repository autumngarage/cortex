# PRs #296-#305, #476 merged — maintenance window backfill (2026-05-25 → 2026-06-09)

**Date:** 2026-06-09
**Type:** pr-merged
**Trigger:** T1.9 (consolidated backfill — the post-merge hook did not fire for these merges; gap detected 2026-06-09 during the roadmap review)
**Cites:** journal/2026-06-09-roadmap-refinement-and-issue-hygiene
**Merge-commits:** 10d5ae4 (#296), e602921 (#298), 54a9f4e (#299), 9077357 (#300), d91241f (#303), 9308fb7 (#304), 2bb6e0f (#305), 3bea226 (#476)

> Eight maintenance and docs merges landed between 2026-05-25 and 2026-06-09
> with no T1.9 journal record. This consolidated entry closes the gap so
> state regeneration can see the period.

## What shipped

- **#296** — auto-draft pr-merged entry for #295 (journal hygiene; itself
  exempt from T1.9 per the recursion guard, listed for completeness).
- **#298, #299, #300** — conductor integration refreshes to v0.10.32-34
  (vendored integration sync, no Cortex behavior change).
- **#303** — fix: restore post-merge hook after touchstone 2.11.43 sync
  (closed #297, #301, #302). The hook outage this fixed is the proximate
  cause of the missing T1.9 entries in this window.
- **#304** — fix: restore staging wiring after touchstone sync and honor
  config opt-out.
- **#305** — docs: clarify hosted credit pricing (`docs/HOSTED-PRICING.md`).
- **#476** — docs: align cortex memory with hosted plan — adopted
  `plans/hosted-decision-reviewer.md` as the active plan (see
  `journal/2026-06-09-hosted-decision-reviewer-plan-adopted`).

## Closes / advances

- **Issues:** #297, #301, #302 closed by #303.
- **Plans:** #476 activated `plans/hosted-decision-reviewer` and superseded
  `plans/cortex-v1` / `plans/context-integrity-production` as active tracks.

## Lesson

The post-merge hook was broken by a touchstone sync (restored in #303) and
the gap went unnoticed for two weeks while the repo's own protocol requires
T1.9 entries — the memory product's repo drifted on its own memory protocol.
The roadmap review (2026-06-09) added the gap check to its punch list; a
recurring `cortex doctor --audit` run would have surfaced it within a day.

## Triggers fired

- T1.9 (x8, consolidated here as backfill)
