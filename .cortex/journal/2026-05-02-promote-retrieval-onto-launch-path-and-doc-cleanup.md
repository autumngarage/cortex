# Promote `cortex retrieve` onto the launch path; clean up doctrine-drift across README + SPEC + state

**Date:** 2026-05-02
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/cortex-v1, plans/cortex-retrieve, doctrine/0006-scope-boundaries-v3, journal/2026-04-28-codesight-cross-pollination-and-council-review

> Moved `cortex retrieve` (sqlite-vec + fastembed) from "deferred to v1.x" onto the v1.0 launch path as v0.7.0, between Tier 4 close (v0.6.0) and the v0.9.0 three-target dogfood gate; created a top-level `ROADMAP.md` as the single public-facing answer to "when is Cortex ready to launch"; resynced README.md + SPEC.md doctrine references from 0005 (superseded) to 0006 (current).

## Context

Session-start audit surfaced multiple drift points the documentation-ownership principle exists to prevent:

1. **Two active plans, state.md claims one.** `cortex-v1.md` (master roadmap) and `cortex-retrieve.md` (added 2026-04-29) were both `Status: active`, but `.cortex/state.md` `## Current work` still said *"Single active plan: cortex-v1.md"* — Cortex's own state was failing the staleness test Cortex exists to enforce.
2. **README.md:155 cited Doctrine 0005** ("not a vector store, full stop"), three days after Doctrine 0006 narrowed that framing to permit `cortex retrieve` as a non-normative retrieval interface over the unchanged storage layer.
3. **SPEC.md cited Doctrine 0005 in two places** (§ 3.4 Goal-hash rationale, § 9 explicit-non-goals list) with the same staleness.
4. **`autumn-garage/autumn-garage-plan.md` used Cortex's old "Phase B/D/E" labels** that have been superseded by the current Tier-1→Tier-4→v0.7.0→v0.9.0→v1.0 sequence.
5. **Goal asked: a single clear roadmap doc.** The user's framing was "I want one document I can read to know when this launches." `cortex-v1.md` is internal (per-work-item, conductor briefs, council-delta annotations); a public-facing single-page version was missing.

The retrieval question was the load-bearing one. User explicit framing (2026-05-02): *"a small embeddings search would be a huge upgrade over just grep"* — naming retrieval as part of the launch story, not a v1.x parallel. Re-litigation of the prior parking decision is justified because the prior decision conflated `cortex retrieve` (deterministic local-CPU embeddings) with the `--enhance` LLM-polish family (genuinely premature). The two are different on the failure mode that drove the parking: polish-hides-staleness applies to LLM rephrasing of derived layers, not to embedding the markdown source of truth into a gitignored derived index.

## What we decided

**`cortex retrieve` ships as v0.7.0** between v0.6.0 (Tier-4 close) and v0.9.0 (dogfood gate). It is on the launch path, not deferred. Acceptance is dual:

- **Standalone:** fresh-clone test on a real corpus answers within latency budget; bare-repo BM25 fallback engages with a visible notice when sqlite-vec / ONNX fails to load.
- **Sentinel-consumer:** `cortex retrieve --json` returns `{path, score, frontmatter, excerpt}` and Sentinel's Planner role consumes it in at least one real cycle on a >100-entry corpus, surfacing a previously-rejected work item that grep alone would have missed. **This validates the file-contract composition pattern under load** — if the JSON shape is right, future consumers compose without coordination.

**`cortex-retrieve.md` stays as a sub-plan** of `cortex-v1.md` (parallel to how `cortex-v0.3.0.md` is referenced from the master plan). Sequencing lives in the master plan; design + slice details live in the sub-plan; if they disagree, master wins for sequencing, sub-plan wins for design.

**The v0.9.0 dogfood gate is extended** with a new "retrieval validation across the three targets" work item: build the index per target, run ≥10 representative queries the maintainer would actually ask, capture latency. Hybrid mode must surface ≥1 entry grep alone misses on terminology-drift queries per target. Silent fallback (sqlite-vec/ONNX failure with no notice) is a gate failure.

**Doctrine references resynced:** README.md and SPEC.md (§ 3.4, § 9) now cite Doctrine 0006 as the current scope-boundaries doctrine. SPEC.md § 9's "does not maintain a vector store" bullet split into two: "does not store vectors inside the canonical layer" (still true) + "does not maintain a database or knowledge graph." This documents what's already true per Doctrine 0006 — no new normative spec content; no version bump.

**ROADMAP.md created at repo root** as the public-facing single-page launch story. Owns the "what does ready-to-launch mean" definition, the per-stage status table, and the next-action pointer. Other docs (state.md, README) link to it without restating.

**autumn-garage-plan.md flagged stale** with a top-of-file annotation pointing readers to `cortex/ROADMAP.md` for current sequencing. The cross-tool integration content below the annotation is conceptually still valid and not edited; rewriting that doc is a separate piece of work outside Cortex's scope.

## Consequences / action items

- [ ] **v0.6.0 ships next** (no change). Briefs at `briefs/v0.6.0-T2-promote-real-writer.md` + `briefs/v0.6.0-T3-doctor-invariants.md`.
- [ ] **v0.7.0 work begins after v0.6.0 ships.** Sub-plan slices S1 (BM25/FTS5) → S2 (semantic+hybrid via sqlite-vec+fastembed) → S3 (standalone + Sentinel-consumer acceptance proofs). Cross-platform install risk (no aarch64 Linux PyPI wheels for onnxruntime; brew + pip + python@3.11 fragility) must surface as a doctor warning with actionable fix guidance, not a crash.
- [ ] **Sentinel-side coordination needed for v0.7.0 acceptance.** Sentinel's Planner role must consume `cortex retrieve --json` in at least one real cycle on a >100-entry corpus. Track as a coordinated cross-tool work item; the file-contract proof is load-bearing for the v0.9.0 dogfood gate.
- [ ] **`autumn-garage/autumn-garage-plan.md` rewrite or formal supersede.** Annotated stale today; full rewrite or replacement plan is its own work, not blocking Cortex.
- [ ] **Next state.md regen will need to absorb the new hand-block content.** The hand-block markers (`<!-- cortex:hand --> ... <!-- cortex:end-hand -->`) are preserved by `cortex refresh-state` per design; verify on next regen.
- [ ] **`cortex doctor` should now report two active plans, not one.** If it doesn't, the active-plan detection is missing a code path — file as a v0.6.0-adjacent fix.
