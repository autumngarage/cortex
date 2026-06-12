# Roadmap-coverage audit: residuals re-tracked, trackers synced, the close-time AC gap named

**Date:** 2026-06-11
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/hosted-decision-reviewer, journal/2026-06-11-compass-review-live-and-flywheel, journal/2026-06-10-pr-merged-pr518

> A 33-agent roadmap-coverage audit (7-finder sweep + adversarial verification per candidate) found the core roadmap durably tracked but confirmed 12 gaps; the residuals are now re-tracked as issues #568–#573, the tracker checklists are synced to live state, and the failure pattern behind the worst three gaps gets a guardrail (#571).

## Context

With Stage 2 live, the operator asked whether everything we should be building
is tracked on the roadmap. A multi-agent audit swept the active plan, state.md
+ June journal, all tracker/issue state, code-level TODOs, the docs, the
Obsidian master plan, and recent merged-PR bodies (127 raw findings → 28
deduped candidates → each adversarially verified against GitHub). Result: 12
confirmed gaps, 13 refuted (tracking existed), 76 tracked-ok confirmations.

The three worst gaps shared one mechanism: **a bundle PR carries `Closes #N`
for an umbrella issue whose acceptance criteria did not all ship, and the
residual silently loses its tracking.** Instances this week: #547 closed by
PR #559 (cost telemetry shipped; the cascade ladder/config/BYOK/regression-gate
ACs did not), #390 closed by PR #552 (inline anchoring — AC 2 — did not ship;
the bot posts one aggregate PR-level comment), #512 closed by PR #518 (FTS leg
shipped; embeddings-table population deferred only in code comments).

Secondary patterns: tracker checklists rotted after the 2026-06-09/10 bulk
closes (65 closed-but-unticked boxes on #445 alone; #485 omitted all 10
Stage-1 security issues); the 2026-06-11 state capture listed closed issues as
remaining (the Wave 8/9 tail) while omitting open ones (#325, #451); and an
open operator decision (keep live-posting on 6 repos vs scope down) was
recorded in the journal but invisible in state.md's `## Open questions`.

## What we decided

1. **Residual work gets first-class issues, not plan-line footnotes.** Filed
   #568 (cascade economics, ex-#547), #569 (inline anchoring, ex-#390 AC 2),
   #570 (embeddings population, ex-#512), #572 (installation→tenant
   resolution, ex-#386 — previously "tracked" only by a plan line citing the
   closed issue), #573 (install-brief paas_repos swap, ex-#161).
2. **The pattern gets a guardrail, not just instance fixes** (#571): the
   open-pr/merge path should fail when a PR closes an issue with unchecked
   ACs unless the body carries an explicit `Residual:` acknowledgment —
   the close-time analogue of `issue-claim-check`.
3. **Tracker checklists were synced to live issue state** (8 parallel
   reconciliation agents; #444/#445/#446/#447/#449/#455/#485 edited, #448
   already clean), with residual cross-references on the #547/#390/#512
   lines and dated reconciliation footers.
4. **Plan + state corrected:** Stage 0 tail now lists the verified-open set
   (#322, #325, #378→#337→#451); #538/#539 citations un-swapped; the
   `#530-#544` range tightened to #530–#540 + #543/#544 (it swept in PR
   numbers); cascade section repointed #547→#568; the keep-live-vs-scope-down
   decision is now a real `## Open questions` entry.
5. **Stale operational docs refreshed** (hosted-deploy verification → current
   `HOSTED_SCHEMA_VERSION`; github-app setup record → the real "Compass
   Review" registration; hosted-architecture caveat marked historical).

Checked and deliberately left alone: the `TODO(cortex#358 seam)` in
`lane_assignment.py` — `assign_lane` genuinely has no callers and #357 (one
extractor) is still open, so "deliberately not yet wired" remains accurate.
Known low-priority untracked tail (audit passthrough, unverified): the
SPEC-conformance "GAP — no parking entry" rows from the v1.0 ceremony.

## Consequences / action items

- [ ] #571 guardrail implementation (close-time AC audit in open-pr.sh + CI)
- [x] Residual issues filed: #568, #569, #570, #572, #573
- [x] Tracker checklists synced (reconciliation footers dated 2026-06-11)
- [x] Plan/state/docs drift corrected in this PR
- [ ] Operator decision: keep live-posting on all 6 repos vs scope down
      (state.md `## Open questions`; mechanism is #397)

**Lesson (the meta-pattern):** the flywheel's own delivery loop needs the same
"no silent staleness" discipline Cortex sells. Bundle PRs + bulk closes are
efficient but they decay tracking surfaces in days, not months — visibility
(`Generated:`-style reconciliation stamps on trackers) plus a deterministic
close-time check is the same medicine the SPEC prescribes for derived layers.
