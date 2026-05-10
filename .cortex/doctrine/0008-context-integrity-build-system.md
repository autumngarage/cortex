# 0008 — Cortex owns context integrity, not generic memory-bank scope

> Cortex is the context build system for AI-assisted work: it turns durable project memory into bounded, cited, verifiable context for agents. The durable product boundary is context integrity — source capture, generated surfaces, invalidation, budget discipline, and CI-style checks — not becoming a general agent framework, proprietary memory service, or opaque RAG product.

**Status:** Accepted
**Date:** 2026-05-09
**Promoted-from:** `.cortex/journal/2026-05-09-context-build-system-vision.md`
**Load-priority:** always

## Context

Cortex already had the pieces of this product shape: Doctrine and Journal as primary sources, generated State and Map with provenance, `cortex manifest --budget <N>` as the session-start context compiler, `cortex grep` / `cortex retrieve` as the deeper lookup loop, and `cortex doctor` as the invariant checker. The public language still leaned on "project memory," which is true but too soft. It invites comparison to memory banks, vendor memories, agent frameworks, personal knowledge bases, and vector databases.

The competitive analysis from the earlier vision plan showed the same pattern. AGENTS.md owns instructions and project discovery. Claude Code, Cursor, Windsurf, and similar tools own local assistant affordances. Aider, Sourcegraph, Continue, and repo-map tools own code-context selection. Letta, Mem0, Zep/Graphiti, and related systems own long-running agent memory or graph/RAG memory. The Cortex opening is narrower and more useful: git-native context integrity for software projects, where the question is not "can an agent remember something?" but "can the next agent prove it is using the right project context, within budget, with stale or incomplete inputs surfaced?"

Recent dogfood made this concrete. Stale external installation claims steered agents wrong even after the real Homebrew path had shipped. The fix was not more memory volume; it was source ownership, generated-state freshness, audit rules, and visible diagnostics.

## Decision

Cortex positions and builds as a **context build system**:

1. **Primary sources are explicit.** Doctrine, Plans, Journal, Procedures, git history, and optional sibling-tool outputs are inputs. They are not an amorphous memory pool.
2. **Generated context is an artifact.** State, Map, manifests, retrieve indexes, and future production reports are compiled from sources and must declare their provenance.
3. **Invalidation is product behavior.** Sources hashes, generated headers, incomplete-input reporting, audit triggers, and doctor checks are part of the core user value, not internal maintenance.
4. **Budgeting is a first-class contract.** Session-start context should be bounded and explain what was included or omitted. Deeper lookup should happen through grep/retrieve, not by loading the whole corpus.
5. **Verification is the trust boundary.** `cortex doctor` is Context CI. A production-ready Cortex installation should be able to fail a workflow when context is stale, uncited, malformed, over budget, or missing required handoff evidence.

The product should keep saying "project memory" when that helps users understand the surface, but internal planning and roadmap choices should prefer "context integrity" as the sharper boundary.

## Trust boundaries and anti-goals

Humans remain the authority for source memory. LLMs may draft Journal entries, propose Doctrine candidates, summarize Plans, or prepare handoff facts, but they do not bypass append-only Journal rules, immutable Doctrine rules, generated-artifact provenance, or doctor gates.

Verification means Cortex can check consistency, freshness, provenance, budget-fit, and policy compliance. It does **not** mean Cortex proves every claim factually true, eliminates hallucinations, or guarantees the correctness of the agent's final code change.

Cortex must avoid these product expansions:

- a general autonomous agent framework;
- a hosted memory cloud;
- a replacement for vector databases or RAG systems in every use case;
- a personal knowledge-management system;
- a hidden source of truth that lives outside git;
- a semantic retrieval layer that silently outranks cited source files.

## Consequences

- README and pitch language should lead with context integrity / context build system, then explain memory as the source material.
- Work on token-budget instrumentation, journal handoff facts, source-PR journal staging, and usage telemetry is on the production path because these are context-integrity controls.
- Work on semantic top-up, LLM-polished summaries, and richer retrieval remains valuable only after telemetry proves the deterministic context path is insufficient.
- Cortex should not grow into a general task runner, autonomous agent framework, cloud memory service, or mandatory embedding/RAG stack. Those tools can consume or feed Cortex through file contracts.
- The best production proof is not a flashy UI. It is a project where a fresh agent can run the Cortex context loop, cite the right facts, and fail visibly when its context is stale or incomplete.

## Bounds

This doctrine does not change the SPEC. It sharpens product strategy and roadmap priority. Existing layer invariants still govern writes:

- Journal remains append-only.
- Doctrine remains immutable-with-supersede.
- generated layers remain provenance-bearing.
- Cortex still degrades on bare repos without Touchstone or Sentinel.

If future work changes those contracts, it must land as a SPEC change with the required version bump.
