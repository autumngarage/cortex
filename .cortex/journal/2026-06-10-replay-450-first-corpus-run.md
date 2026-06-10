# First #450 corpus replay — retrieval vs evaluator failure, separated

**Date:** 2026-06-10
**Type:** decision
**Trigger:** T2.1 (Stage 0 gate evidence; the run the #337 report grades)
**Cites:** plans/hosted-decision-reviewer.md, docs/eval/replay-450-2026-06-10.json, docs/walkthrough-pe0.md

> The live batch replay over the 6-fixture real-history corpus (claude
> CLI as evaluator) produced the master plan's required decomposition:
> retrieval failures separated from evaluator failures, with zero
> hallucinated findings.

## Results

- **True negatives: 2/2 clean** — no findings on the compliant fixtures
  (the no-spam bar).
- **Retrieval-stage misses: 3** — exactly the fixtures whose decisions
  never reach the structural-emulation pack (the baselined 0.5 presence
  debt; #367's work, visible in `diagnostics.impossible_expected_findings`
  ... recorded per-stage in the report).
- **Substantive catch: 1** — spec-version-drift: the model found the SAME
  decision with the SAME cited spans and a correct account of the real
  historical drift, classified as contradicts-prior-decision where the
  fixture expected the (now-shadow) omitted-load-bearing-constraint class.
  Grader counts it missed+unexpected; #525 adds class-divergent-match and
  shadow-expectation grading.
- **Hallucinations: 0.**

## What this means for the gate

The gradeable surface for #378 is currently one emission (by inspection:
substance-correct). The honest blocker on a meaningful 70%-bar sample is
corpus size and retrieval presence, not evaluator quality — the #337
report should weigh: grow the corpus (simlab + #339 sibling repos) and
raise presence (#367) before treating the percentage as load-bearing.

## Consequences / action items

- [x] Replay report committed — resolved to:
  `docs/eval/replay-450-2026-06-10.json`.
- [ ] Grader refinements — resolved to: cortex#525.
- [ ] Corpus growth before the percentage is load-bearing — resolved to:
  cortex#339 + the simlab scenario packs (#521, shipped).
