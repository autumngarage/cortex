# NNNN — The default manifest may add a deterministic, opt-in, evidence-gated semantic top-up tier after pins + recency

> The `cortex manifest` default load MAY fill *remaining* budget — after `Load-priority: always` pins and `Date:` recency are satisfied — with a deterministic semantic-similarity top-up tier, but only behind an explicit opt-in and only once telemetry shows recency-only is missing relevant entries on real corpora. This narrows Doctrine 0006 #1's "never by embedding similarity" clause for the manifest; it does not touch the storage layer or the grep floor.

**Status:** Proposed
**Date:** 2026-05-25
**Promoted-from:** - (direct authoring; proposed in response to cortex#234)
**Cites:** doctrine/0006-scope-boundaries-v3, doctrine/0008-context-integrity-build-system, .cortex/protocol.md § 1, .cortex/plans/context-integrity-production.md, cortex#234, cortex#235
**Grounds-in:** principles/engineering-principles.md#derive-dont-persist
**Load-priority:** default

## Context

cortex#234 argues that the manifest's recency heuristic is a proxy for relevance, not relevance itself: on a high-volume journal, the most pertinent six-month-old Doctrine entry loses budget to three recent PR-merged digests. The request is a deterministic semantic top-up tier *after* Load-priority pins + recency.

The standing rule it bumps against is Doctrine 0006 #1, which closes with: *"The default session manifest (`cortex manifest`) is unchanged — still loads Doctrine by `Load-priority: always` pins plus recency, never by embedding similarity."* Protocol § 1 states the same ("No semantic retrieval at session start"). Both trace to the conductor case study scar: ranked/derived layers **hide staleness** behind authoritative-looking output, so the manifest was kept transparent and deterministic-by-construction.

Two facts make #234 worth a doctrine review rather than a flat decline:
1. Semantic *similarity over a fixed index* is deterministic given its inputs — it is a score, not LLM-generated prose. The 0006 scar was specifically about *polished prose hiding staleness*; a similarity score is a different object than the thing that burned us.
2. Doctrine 0008 reframes Cortex as a context build system ("Context CI"), whose job is *the right slice* for the task — and the `context-integrity-production` plan already lists "evidence-gated semantic top-up (#234)" alongside usage telemetry (#235). The strategic intent exists; what is missing is evidence and a transparency guarantee.

What is NOT yet established: that recency-only actually fails on real corpora. #234 asserts it ("worse on dogfood targets with high-volume journals") but cites no measurement. The conductor scar is concrete; this pain is so far hypothetical. Cortex's own bar is "dogfood as the readiness bar" and "evidence-gated."

## Decision

We will permit — **not require** — the default `cortex manifest` to add a semantic top-up tier, subject to all of these boundaries (the boundaries are the decision):

1. **Order is fixed and pins/recency win.** Selection stays: (1) `Load-priority: always` pins, (2) most-recent-N by `Date:`, then (3) semantic top-up fills *only remaining* budget. The top-up never displaces a pin or a recency-selected entry, so the deterministic floor an agent sees today is a strict subset of what it sees with the tier on.
2. **Deterministic and reproducible.** The top-up ranks against the existing gitignored derived index (Doctrine 0006 #1 hazmat boundary) with a fixed embedder and fixed tie-break; same inputs → same slice. No LLM call, no prose generation, in the manifest path.
3. **Opt-in, off by default.** Gated behind explicit project config (e.g. `[manifest].semantic_topup = true`). With it absent or false, the manifest is byte-for-byte today's behavior. The grep floor and `cortex manifest` without the index present are unchanged.
4. **Evidence-gated before promotion.** This candidate is **not** promoted to ratified Doctrine — and no code ships — until usage telemetry (cortex#235) demonstrates, on a real high-volume corpus, that recency-only omits entries a task needed. Absent that evidence, the right state is "proposed, deferred."
5. **Transparency is mandatory (the anti-scar guarantee).** When the tier contributes entries, the manifest header MUST mark which entries were semantically selected vs pin/recency selected, and the manifest's `--show-budget`/`--json` diagnostics MUST expose the tier's contribution. Silent semantic selection is the exact failure 0006 guarded against and is forbidden.

What falls **outside** this decision and is unchanged: the storage layer (markdown + git + grep, no embeddings in canonical content), the `cortex grep` zero-dependency floor, the `cortex retrieve` interface's non-normative/hazmat status, and Protocol § 1's default for projects that do not opt in.

On promotion this entry supersedes Doctrine 0006 by narrowing only #1's final "the default session manifest is unchanged … never by embedding similarity" sentence; 0006's items 2–8 and the storage-layer framing of #1 are carried verbatim.

## Consequences

- **What becomes easier:** the manifest can become *the right slice* (Doctrine 0008's promise) on large corpora instead of *a recent slice*; downstream agents stop missing the old-but-relevant entry that recency buried.
- **What becomes harder:** the manifest acquires a second, index-dependent selection path that must stay deterministic and transparent; reviewers must verify the tier never displaces pins/recency and always discloses its picks. The evidence gate adds a measurement prerequisite before any code lands.
- **What this forecloses:** it does not reopen embeddings *in* storage, does not make `cortex retrieve` normative, and does not permit a non-deterministic or undisclosed semantic selection anywhere in the session-start path. A future request for "semantic-first manifest selection" (ahead of recency) would need to supersede *this* entry.

---

<!--
Promotion checklist (remove before promoting to doctrine/):

- [ ] Telemetry evidence (cortex#235) cited showing recency-only misses relevant entries on a real corpus — this is the evidence gate in Decision #4.
- [ ] `Supersedes: 0006` set, and 0006's Status flipped to `Superseded-by <this-nnnn>` on promotion (narrowing only #1's manifest clause; carry 2–8 verbatim).
- [ ] Doesn't duplicate an existing Doctrine entry (cortex doctor --dup-check).
- [ ] Transparency guarantee (Decision #5) has a passing test before code ships: manifest marks semantically-selected entries and exposes the tier in --json/--show-budget.
- [ ] Falsifier: supersede this entry if telemetry after rollout shows the semantic tier degrades slice quality or reintroduces staleness-hiding versus recency-only.
-->
