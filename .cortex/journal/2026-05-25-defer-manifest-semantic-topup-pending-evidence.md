# Stage manifest semantic-top-up as a Proposed doctrine candidate; defer implementation pending evidence

**Date:** 2026-05-25
**Type:** decision
**Trigger:** T1.1
**Cites:** doctrine/candidate-manifest-semantic-topup, doctrine/0006-scope-boundaries-v3, doctrine/0008-context-integrity-build-system, plans/context-integrity-production, cortex#234, cortex#235

> cortex#234 (semantic top-up in the default manifest) conflicts with Doctrine 0006 #1 / Protocol § 1; rather than implement against a load-bearing invariant or decline outright, we stage a Proposed doctrine candidate that fixes the boundaries and gate, and ship no code until telemetry proves recency-only is insufficient.

## Context

cortex#234 asks the default `cortex manifest` to add a semantic-similarity top-up after `Load-priority` pins + recency, because recency is a relevance proxy that buries old-but-pertinent entries on high-volume journals. Doctrine 0006 #1 and Protocol § 1 forbid embedding-similarity selection in the session-start manifest, tracing to the conductor case-study scar (ranked/derived output hides staleness). The issue was declined twice by the alchemist bot ("conductor produced no diff"). The `context-integrity-production` plan already names "evidence-gated semantic top-up (#234)" with usage telemetry (#235), so the intent exists — but the pain is asserted, not measured, and the change crosses an immutable-doctrine boundary.

## What we decided

We will neither implement #234 now nor close it. We stage `.cortex/doctrine/candidate-manifest-semantic-topup.md` (Status: Proposed, supersedes 0006 on promotion) that narrows *only* 0006 #1's manifest clause and pins five boundaries: pins/recency always win and are never displaced; deterministic + reproducible; opt-in and off by default; **promotion and code both gated on telemetry (#235) evidence that recency-only misses needed entries on a real corpus**; and mandatory transparency (the manifest must mark and expose semantically-selected entries — the explicit anti-scar guarantee). Storage layer, the grep floor, and `cortex retrieve`'s non-normative status are untouched. Implementing now (against the invariant) and closing outright (discarding recorded intent) were both rejected in favor of spec-before-implementation.

## Consequences / action items

- [ ] Gather manifest-selection telemetry (cortex#235) on a high-volume corpus; promote the candidate only if recency-only is shown to miss relevant entries — else keep deferred.
- [ ] On promotion: set `Supersedes: 0006`, flip 0006 to `Superseded-by`, carry 0006 items 2–8 verbatim, and land the transparency test before any manifest code (per the candidate's promotion checklist).
