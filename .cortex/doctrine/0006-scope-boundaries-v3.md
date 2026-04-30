# 0006 — Scope boundaries (v3, supersedes 0005)

> Cortex is a file-format protocol + reference CLI for per-project memory. Multiple adjacent categories (vector stores, agent frameworks, cloud memory services, knowledge graphs, portfolio tools, observability platforms) solve adjacent problems; Cortex composes with them but does not become them. This entry names the boundaries so future scope drift is catchable. **v3 differs from v2 only on #1** — it narrows the prior framing so Cortex can own a *retrieval interface* (`cortex retrieve`) over its own format without becoming a vector store at the storage layer. Two years of consumer experience (Sentinel especially) showed the prior framing forced every consumer to re-implement the same chunking + retrieval logic against the same format.

**Status:** Accepted
**Date:** 2026-04-29
**Supersedes:** 0005
**Promoted-from:** `.cortex/plans/cortex-retrieve.md` § Doctrine implications + § Doctrine supersede draft (council-reviewed 2026-04-29)
**Load-priority:** always

## Context

Doctrine 0005 #1 said Cortex was "not a vector store. No embeddings, no ANN indexes, no similarity search at the storage layer." That framing was correct for the storage layer and remains correct. What it got wrong was implying the *retrieval* layer was equally out of scope — leaving every consumer (Sentinel, Touchstone, future quartet tools) to re-implement chunking, indexing, hybrid retrieval, and invalidation logic against the same `.cortex/` format. After two years of dogfooding (Sentinel at deployed scale, autumn-mail, autumn-garage itself), the recurring failure mode was: agents past ~100 cycles missing the entry that *would* answer their question because grep needs the exact term the historical entry used.

The resolution: narrow #1 so the storage layer stays exactly as it was — markdown + git + grep, never embeddings *in* `.cortex/` markdown content — but acknowledge that a *retrieval interface* over that storage is something Cortex should own once, instead of N consumers re-doing it. The interface is non-normative (consumers may bypass and build their own); the index it builds is gitignored, derived, recomputable from the markdown source of truth.

Pre-merge council review (3 members synthesized; `journal/2026-04-29-cortex-retrieve-design-council.md`) caught a critical flaw in an earlier framing that would have allowed silent staleness on uncommitted edits, plus pushback that tightened: explicit opt-in for paid embedders, lazy imports preserving the grep floor, and the index declared "hazmat" (see Decision below).

The remaining seven boundaries (database, knowledge graph, portfolio tool, agent framework, AGENTS.md replacement, cloud host, git replacement) are unchanged from v2. This entry does not re-litigate any of them.

## Decision

Cortex explicitly is not:

1. **Not a vector store at the storage layer; owns a retrieval interface as a non-normative reference implementation.** No embeddings, no ANN indexes, no similarity search inside `.cortex/` markdown content — that storage layer stays markdown + git + grep, the load-bearing portability guarantee. **However**, Cortex now owns the `cortex retrieve` retrieval interface, which builds an opt-in derived index at `.cortex/.index/` (gitignored, recomputable). The index is **hazmat**: consumers (Sentinel, Touchstone, future tools) must use the `cortex retrieve` interface; direct queries against `.cortex/.index/` SQLite are unsupported and may break across versions. The interface itself is **non-normative** — part of the Cortex CLI, not part of the Cortex Protocol / SPEC. Custom consumers are free to bypass and implement their own retrieval over `.cortex/` markdown content (the normative format). The grep floor (`cortex grep`) is doctrine: zero-dependency, can't-fail, never imports `sqlite-vec` or ONNX. See `.cortex/plans/cortex-retrieve.md` for the full design and S0–S4 slice plan. The default session manifest (`cortex manifest`) is unchanged — still loads Doctrine by `Load-priority: always` pins plus recency, never by embedding similarity (see `.cortex/protocol.md` § 1).
2. **Not a database.** `.cortex/.index.json` and `.cortex/.index/` are caches, regeneratable from the files. Removing them loses nothing that isn't recoverable from `.cortex/` contents. Git is the durable store.
3. **Not a knowledge graph.** Cross-references between files use typed links (`supersedes`, `implements`, `derives-from`, `grounds-in`, `blocked-by`, `verifies`). Cortex does not construct a graph as a primary artifact. Projects wanting a graph view can build one from the links; graph semantics are not load-bearing for retrieval.
4. **Not a portfolio tool.** One project per `.cortex/`. Cross-project aggregation (the "Lighthouse" conversation from Phase-A discussion) is deliberately out of scope for v0.x. Per-user or per-org baselines could inform a future cross-project story; v0.x is one project at a time.
5. **Not an agent framework.** Cortex has no concept of "an agent." It has *writers* — humans, CLIs, hooks — that follow the Protocol. Any agent framework (Claude Code, Cursor, Aider, Sentinel, custom) can read and write `.cortex/` by meeting the Protocol contract.
6. **Not a replacement for `AGENTS.md` or `CLAUDE.md`.** Those files are the agent-facing entry point for a project. `.cortex/` is the project's memory. `AGENTS.md` imports `@.cortex/protocol.md` and `@.cortex/state.md`; it does not duplicate them. Every Cortex project has both.
7. **Not cloud-hosted.** Local files, git, nothing else. No Cortex Cloud, no API keys stored in Cortex, no network dependencies for the spec itself. (The retrieve interface ships with a CPU-only local default and owns its embedder choice end-to-end — embedding is *not* delegated to Conductor; Conductor's role is LLM routing only. If Cortex later adds direct cloud-embedder adapters, those are Cortex-internal, opt-in, never auto-routed. Synthesis commands that use `claude -p` make network calls; those are implementation details of regeneration, not storage.)
8. **Not a replacement for git.** Git is authoritative for code state. Cortex is authoritative for the *reasoning layer* around the code. Cortex reads git (commits, diffs, log); git does not read Cortex.

## What changed from v2 (load-bearing detail)

**Only #1 narrowed.** Items 2–8 are restated verbatim modulo the parenthetical addition to #7 about retrieve's network behavior (which is itself a non-change: cloud embedders are opt-in, never auto-selected, so Doctrine 0005 #7's "no network dependencies for the spec itself" still holds — the spec is storage, not the optional retrieve interface).

The narrowing in #1 is structurally:

- **Storage** layer: unchanged. Markdown + git + grep. No embeddings *in* canonical content.
- **Retrieval** layer: new. `cortex retrieve` as a non-normative reference interface; `.cortex/.index/` as a gitignored derived index; `cortex grep` preserved untouched as the zero-dep floor.
- **Hazmat boundary**: consumers query through `cortex retrieve`, not the SQLite directly. Index format is internal to Cortex versions.

What this means for downstream consumers:

- **Sentinel** (the primary forcing function): consumes via `cortex retrieve --json --top-k N` mid-cycle, no longer needs to implement its own `src/sentinel/index/` module. The "memory differentiator scales past 100 cycles" promise gets a working substrate.
- **Touchstone**: future hook-script integration possible via the same interface.
- **Custom consumers**: free to bypass `cortex retrieve` entirely and roll their own index. The Cortex Protocol promises the storage layer; the retrieve interface is convenience, not contract.

## Consequences

Same as 0004/0005's Consequences section, reproduced for completeness so the current-authority Doctrine stands alone:

- Adjacent tools (Letta, Mem0, Graphiti, Cursor Rules, Claude Code memory, AGENTS.md) **compose** with Cortex rather than compete with it. A project can use Mem0 as an embedding layer over `.cortex/`; can use Graphiti for retrieval-latency-sensitive agent flows; can use AGENTS.md as the agent-facing entry that imports `.cortex/`. Nothing in this list is an either/or.
- Feature requests that would push Cortex into one of these categories get declined or spun out. **Updated for v3:** *"add a built-in vector index"* → was `declined` in v2; now `declined at the storage layer, accepted at the retrieval layer with the hazmat + non-normative constraints from #1`. *"add per-org aggregation"* → still deferred to the Lighthouse conversation, not added to v0.x.
- The "explicitly not" list is the primary defense against scope creep. Contributors who propose features inconsistent with this Doctrine are expected to argue why the boundary should move — which requires superseding this Doctrine entry with a new one, not silently expanding scope.
- Decisions that *would* move a boundary are load-bearing enough to warrant full Doctrine treatment (new entry that supersedes this one). Example: if a future Cortex version decides the retrieve interface should become normative protocol (every Cortex CLI must implement it), this entry gets superseded by a new one explaining why the boundary moved.

**Procedural note on this supersede.** The supersede was driven by `.cortex/plans/cortex-retrieve.md` (drafted 2026-04-29, council-reviewed same day). The council critique caught a critical bug in the design's invalidation strategy (HEAD-only fast path missed uncommitted edits) and tightened the doctrine framing to declare the index "hazmat" and the interface non-normative. The lesson: doctrine narrowing benefits from same-session council review against a concrete consumer use case (Sentinel's memory differentiator at scale).
