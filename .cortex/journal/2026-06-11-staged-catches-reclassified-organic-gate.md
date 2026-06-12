# Both catches to date were staged — reclassified, and the organic-catch gate is now the validation bar

**Date:** 2026-06-11
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/hosted-decision-reviewer, journal/2026-06-11-compass-review-live-and-flywheel, journal/2026-06-11-roadmap-coverage-audit-reconciliation, docs/walkthrough-pe0.md

> The two catches our memory describes as "real" — the PE-0 first catch and the Stage 2 live catch on PR #561 — were both deliberately planted demonstrations. This entry reclassifies them as staged (mechanism proof, not product validation), defines the organic-catch bar with an explicit falsification arm (#576), and protects the ground-truth corpus from fixture contamination (#575).

## Context

Asked directly whether usage has validated the product idea, we checked the
evidence behind our own records. `journal/2026-06-11-compass-review-live-and-flywheel`
says Compass Review "caught a real decision contradiction on PR #561"; PR #561
is titled "chore: Compass Review catch demo fixture (DO NOT MERGE)" and the
operator's own reply on the thread says "this was a deliberate demo fixture."
The PE-0 walkthrough's § 7 catch ran on a hand-built `contradiction.diff`
constructed to violate the compose-by-file-contract decision. Per § 4.1 the
earlier entries stay unchanged; this entry revises the conclusion.

This is the premature-completion-declaration failure mode (sigint's
`COLLECTOR_MIGRATION.md`, already a SPEC design input) occurring inside
Cortex's own memory layer — a product about decision integrity overselling
its evidence to itself. The same week's roadmap-coverage audit found the
mirror pattern (bundle PRs closing issues with unshipped ACs); the common
root is celebratory language outrunning verification.

## What we decided

1. **Reclassify.** Both catches are *staged demonstrations*: they prove the
   mechanism (accurate citations, advisory invariants, confirmed-status
   evidence gate, viable cost ~0.3–2.7¢/review) and nothing about the idea.
2. **Define the validation bar** (#576): an **organic catch** is a
   `contradicts-prior-decision` finding on a PR not created to demonstrate
   Cortex, citation hand-verified, human response captured. Staged demos
   explicitly cannot satisfy #337/#451 (gate-thread comments posted).
3. **Name the falsification arm:** ~4 weeks of live posting across the 6
   dogfood repos (recall un-crippled since #563) with zero organic catches is
   a recorded pivot/persevere signal — silence is data, not a postponement.
4. **Protect the corpus** (#575): `review_feedback_events` currently contains
   only fixture-derived entries; staged traffic gets tagged (append-only) and
   excluded from #395 precision metrics by default, so promote/auto-demote
   (#413/#415) never trains on planted ground truth.
5. **Re-rank the gates:** #378 hand-grading → #337 report → #451 sign-off
   move from "Stage 0 tail, lower priority" to the validation bar, with #378
   grading live Stage 2 traffic. Path-to-first-customer now reads: no
   design-partner outreach on staged-demo evidence alone. This explicitly
   supersedes the plan's original Stage 0 rule "Do not host or build webhooks
   before this passes" — that rule was already overtaken by events (hosted
   core and the live reviewer shipped 2026-06-10/11 ahead of any gate
   verdict); the re-rank records the supersession instead of leaving the
   dead rule standing. (The merge-gate reviewer caught the dead rule and the
   Wave 9 / P1 / P5 inconsistencies; its plan fix is adopted in this PR.)
6. **Guardrail the sibling rot pattern** (#577): scheduled deterministic
   tracker-checklist reconciliation, so the audit's one-time cleanup has a
   standing mechanism.

## Consequences / action items

- [ ] #575 staged-traffic tagging lands before #395 reads the corpus
- [ ] #576 organic-catch gate: validation event journaled, or falsification
      confronted at window end
- [ ] #378 hand-grading runs over live Stage 2 findings
- [ ] #577 scheduled tracker reconciliation
- [x] Gate-thread comments on #337/#451; tracker #446 updated; plan + state
      reclassified in this PR

**Lesson:** verification language is load-bearing memory. "Real catch" written
for a fixture cost nothing today and would have cost a design-partner pitch
built on it later. The fix is the same one Cortex sells: classify the
evidence at write time (staged vs organic), and make the gate read the
classification, not the adjective.
