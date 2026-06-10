# PE-0: the first product experience — loop closed, first contradiction caught

**Date:** 2026-06-10
**Type:** decision
**Trigger:** T2.1 ("the first product experience" — milestone the founder asked to be told about)
**Cites:** plans/hosted-decision-reviewer.md, docs/walkthrough-pe0.md, docs/product/customer-journeys.md

> The complete Stage 0 loop ran on live infrastructure: `cortex derive`
> (332 candidates across cortex/vesper/vanguard/outrider) → human
> confirmation → `cortex push` → `cortex ask` (first cited answer from
> Railway compass) → `cortex review` (first real
> contradicts-prior-decision finding, cited to the CLAUDE.md span, with a
> correct file-contract repair suggestion). The product's thesis
> demonstrated itself on its own constitution.

## What we decided / learned

1. **The invariants police real use correctly.** Both fail-closed refusals
   hit during the run (snapshot-required, confirmed-only answers) were the
   product working; #516 turned them into onboarding (remediation hints).
2. **Vocabulary must be taught, not assumed.** The model invented a
   finding class and a confidence label until the evaluate prompt
   enumerated both; the boundary's refusal of the invented class is what
   surfaced it. Prompt contracts now carry the full vocabulary.
3. **The marquee phrase needed engineering** (#512): "what did we decide
   about X" poisoned its own FTS retrieval; fixed same-day with question
   normalization + OR-softened FTS + a config-version bump.
4. **Dogfood findings became product the same day:** #511-#516 filed in
   the morning, built and merged by afternoon (push/triage/review verbs,
   remediation hints, retrieval + fragment fixes).
5. **Confirmed-only answering is a product feature with UX weight** — the
   confirm ritual is the onboarding moment; journeys doc updated.

## Consequences / action items

- [ ] Stage 0 tail: #322/#326/#338/#367/#368/#373/#374/#376/#339 —
  resolved to: plans/hosted-decision-reviewer.md § Pickup pointer.
- [ ] Gate artifacts: #450 batch replay → #378 grading (LLM-judge
  pre-grades + founder spot-check) → #337 verdict — resolved to: the
  same pickup pointer.
- [ ] Stage 1 frontier (#470/#471/#473/#474/#517) — resolved to:
  tracker #485.
