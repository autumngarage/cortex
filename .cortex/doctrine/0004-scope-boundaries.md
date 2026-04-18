# 0004 — Scope boundaries: what Cortex deliberately is not

> Cortex is a file-format protocol + reference CLI for per-project memory. Multiple adjacent categories (vector stores, agent frameworks, cloud memory services, knowledge graphs, portfolio tools, observability platforms) solve adjacent problems; Cortex composes with them but does not become them. This entry names the boundaries so future scope drift is catchable.

**Status:** Superseded-by 0005
**Date:** 2026-04-17
**Load-priority:** default
**Promoted-from:** vision-draft-v3.md § 9

## Context

Project memory for AI-assisted teams is a crowded design space. Letta, Cursor Memories (retreated), Mem0, Graphiti/Zep, LangChain/LangGraph, Claude Code auto-memory, Obsidian/Dendron, MetaGPT, AGENTS.md, and ~dozen MCP memory servers all claim some piece of it. The first vision draft was vulnerable to *"isn't this just X?"* pushback from peer-agent critics precisely because Cortex's shape touched many of them.

The resolution isn't to contest each adjacent claim. It's to state clearly what Cortex *is not* doing, so the scope stays defensible and each adjacent tool can compose with Cortex instead of being compared to it.

## Decision

Cortex explicitly is not:

1. **Not a vector store.** No embeddings, no ANN indexes, no similarity search at the storage layer. Markdown + git + grep. Semantic retrieval used by `cortex manifest` for top-K Doctrine is a read-side concern, built over the file store; it is not the file store. If a project wants deep semantic search, it can index `.cortex/` externally — that's not Cortex's job.
2. **Not a database.** `.cortex/.index.json` is a cache, regeneratable from the files. Removing `.index.json` loses nothing that isn't recoverable from `.cortex/` contents. Git is the durable store.
3. **Not a knowledge graph.** Cross-references between files use typed links (`supersedes`, `implements`, `derives-from`, `grounds-in`, `blocked-by`, `verifies`). Cortex does not construct a graph as a primary artifact. Projects wanting a graph view can build one from the links; graph semantics are not load-bearing for retrieval.
4. **Not a portfolio tool.** One project per `.cortex/`. Cross-project aggregation (the "Lighthouse" conversation from Phase-A discussion) is deliberately out of scope for v0.x. Per-user or per-org baselines could inform a future cross-project story; v0.x is one project at a time.
5. **Not an agent framework.** Cortex has no concept of "an agent." It has *writers* — humans, CLIs, hooks — that follow the Protocol. Any agent framework (Claude Code, Cursor, Aider, Sentinel, custom) can read and write `.cortex/` by meeting the Protocol contract.
6. **Not a replacement for `AGENTS.md` or `CLAUDE.md`.** Those files are the agent-facing entry point for a project. `.cortex/` is the project's memory. `AGENTS.md` imports `@.cortex/protocol.md` and `@.cortex/state.md`; it does not duplicate them. Every Cortex project has both.
7. **Not cloud-hosted.** Local files, git, nothing else. No Cortex Cloud, no API keys stored in Cortex, no network dependencies for the spec itself. (Synthesis commands that use `claude -p` or similar make network calls; those are implementation details of regeneration, not storage.)
8. **Not a replacement for git.** Git is authoritative for code state. Cortex is authoritative for the *reasoning layer* around the code. Cortex reads git (commits, diffs, log); git does not read Cortex.

## Consequences

- Adjacent tools (Letta, Mem0, Graphiti, Cursor Rules, Claude Code memory, AGENTS.md) **compose** with Cortex rather than compete with it. A project can use Mem0 as an embedding layer over `.cortex/`; can use Graphiti for retrieval-latency-sensitive agent flows; can use AGENTS.md as the agent-facing entry that imports `.cortex/`. Nothing in this list is an either/or.
- Feature requests that would push Cortex into one of these categories get declined or spun out. Example: *"add a built-in vector index"* → declined; external indexing is fine, storing vectors inside `.cortex/` is not. Example: *"add per-org aggregation"* → deferred to the Lighthouse conversation, not added to v0.x.
- The "explicitly not" list is the primary defense against scope creep. Contributors who propose features inconsistent with this Doctrine are expected to argue why the boundary should move — which requires superseding this Doctrine entry with a new one, not silently expanding scope.
- Decisions that *would* move a boundary are load-bearing enough to warrant full Doctrine treatment (new entry that supersedes this one). Example: if a future Cortex version decides cross-project baseline Doctrine is in scope, this entry gets superseded by a new one explaining why the boundary moved.
