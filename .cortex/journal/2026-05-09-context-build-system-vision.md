# Cortex commits to context integrity as the product line

**Date:** 2026-05-09
**Type:** decision
**Trigger:** T2.4
**Cites:** doctrine/0008-context-integrity-build-system, plans/context-integrity-production, plans/vision-sharpening, docs/PITCH.md, README.md

> Cortex should be built and described as a context build system: source capture, generated context, invalidation, budget discipline, and CI-style verification for AI agents.

## Context

The user asked for a creative and critical production-readiness plan after release work and competitive analysis across Autumn Garage tools and external prior art. The existing `vision-sharpening` plan already showed that generic "AI memory" is crowded: AGENTS.md owns instructions, vendor memories own local tool affordances, Letta/Mem0/Zep-like products own agent memory stores, and code-context tools own code selection.

The stronger product claim emerged from Cortex's own dogfood. The failures that mattered were not "the agent lacked a bigger memory." They were stale generated state, stale external claims, missing handoff evidence, uncited summaries, and ambiguous ownership of what a fresh agent should load. Those are context integrity failures.

Conductor was delegated a second-opinion research critique for this framing during the session. The prompt asked it to identify weak claims, missing competitors, better framing, and execution risks. It pushed the plan to make "context build system" the primary category, define "context integrity" narrowly, avoid overclaims about hallucination/correctness/adoption, add anti-goals and trust boundaries, and make artifact contracts plus diagnostics explicit.

## What we decided

We will describe and build Cortex as **Context CI for AI-assisted work**:

- Project memory is the source material.
- `cortex manifest`, `cortex grep`, and `cortex retrieve` are the context build/read path.
- generated State/Map/index outputs are build artifacts with provenance and invalidation.
- `cortex doctor` is the verification gate.
- production readiness means the agent can prove it is using bounded, cited, fresh-enough context, and fails visibly when it cannot.

This narrows roadmap priority. Token budget instrumentation (#244), facts-file journal handoff (#243), source-PR journal staging (#207), and local lookup telemetry (#235) move onto the production path. Semantic manifest top-up (#234) remains evidence-gated until usage data shows deterministic manifest + grep/retrieve is insufficient.

The old framing being retired is "Cortex is a memory bank with better discipline." That was understandable but incomplete. It underweighted the product behavior that actually prevented failures in dogfood: stale-state detection, source ownership, generated provenance, token-budget control, and reviewable handoffs.

Moved up:

- #244 token-budget instrumentation, because budget-fit is part of context integrity.
- #243 facts-file journal handoff, because agent/Conductor summaries need narrow authority and visible provenance.
- #207 source-PR journal staging, because memory should ship with the change that creates it.
- #235 lookup telemetry, because retrieval and semantic top-up should be decided from real usage.

Moved down or out:

- generic memory-bank UX that does not improve provenance, freshness, or reviewability;
- agent-framework ambitions that make Cortex own execution rather than context inputs;
- semantic top-up (#234) as a default path before usage data and provenance rules justify it;
- hosted memory or cloud sync surfaces that would weaken the local-first git contract.

## Consequences / action items

- [x] Record durable Doctrine: [Doctrine 0008](../doctrine/0008-context-integrity-build-system.md).
- [x] Record execution plan: [context-integrity-production](../plans/context-integrity-production.md).
- [x] Update public framing in [README.md](../../README.md) and [docs/PITCH.md](../../docs/PITCH.md).
- [x] Fold Conductor critique into the doctrine, plan, README, and pitch.
