# Competitive positioning snapshot and Claude Code existential risk

**Date:** 2026-04-17
**Type:** decision
**Trigger:** —   (human-authored; preserved from drafts/vision-draft-v3.md §§ 8, 10 before archival)
**Cites:** doctrine/0004-scope-boundaries, journal/2026-04-17-vision-v3-promoted, drafts/vision-draft-v3.md § 8, drafts/vision-draft-v3.md § 10

> Vision v3 contained two pieces of strategic content that were deliberately kept out of the canonical artifacts (SPEC.md, README.md, .cortex/protocol.md, doctrine/0004) because they are strategy, not spec. Before archiving the drafts, this entry captures both so the reasoning remains findable without grepping archived files.

## Context

V3 of the vision-sharpening work (see `journal/2026-04-17-vision-v3-promoted.md`) landed the Cortex Protocol, the seven-field metadata contract, the promotion queue operational rules, and scope-boundary Doctrine 0004. Two sections of v3 were not promoted:

- **§ 8 — Honest comparison table** (detailed per-tool competitive matrix).
- **§ 10 — Risks (honest)**, specifically § 10.4 on Claude Code native memory as an existential risk.

Both are strategy content: they inform what Cortex *should do about the landscape*, not what `.cortex/` files look like. Canonical spec files are the wrong home for them. A Journal entry is the right home: Journal is the reasoning layer, append-only and timestamped, and can be revisited via grep or digest.

## What we decided

Preserve both pieces here as a single durable snapshot. Future strategic re-assessment (quarterly or on landscape shift) writes a new Journal entry; this one stays as the 2026-04-17 baseline.

### Competitive matrix (v3 § 8, preserved verbatim in substance)

| Adjacent | What they have | Cortex's defensible difference | Risk |
|---|---|---|---|
| **Letta MemFS** | Git-backed MD + frontmatter + pinned `system/` + read-only + `/doctor` + reflection subagents + git sync. **Closest analogue.** | Tier-1 machine-observable triggers (not agent judgment). Promotion queue with operational rules. Multi-writer invariants across independent tools. Cross-tool by spec, not per-agent. | **Medium-high.** Letta can add trigger discipline. Defense: spec adoption first. |
| **Claude Code `CLAUDE.md` + auto-memory** | Hand-written CLAUDE.md + agent-discretion `memory/` + `@path` imports. Built into the IDE. | Team-shared via git, not machine-local. Protocol-triggered, not discretionary. Six layers with enforceable invariants. | **Existential.** Anthropic could ship multi-layer native memory. Defense: Cortex as spec Anthropic converges on. See "Claude Code existential risk" below. |
| **Cursor Memories (retreated 2025-11-22)** | Auto-extract via sidecar; project-scoped; required disabling privacy mode. | Local-markdown, privacy-trivial, cross-tool, Protocol-triggered not extraction-based, human-review queue. | **Low.** Cursor retreated; design space is open. |
| **Cursor `.brain/` community** | 3-file git-committed cross-tool markdown. **Emergent convention.** | Six layers; Protocol; invariants; promotion queue; Map/State regeneration with audit invariants. | **Low — complementary.** The discipline the `.brain/` experiments are missing. |
| **Mem0** | Production memory-as-a-service; LLM extraction; entity linking. | File-first, git-native, human-auditable, team-shared, no cloud dep. | **Low.** Different consumer. |
| **Graphiti / Zep** | Bi-temporal graph; opaque; retrieval-latency-optimized. | Markdown + git + audit trail. | **Low.** Different audience. |
| **LangGraph checkpoints** | State-as-computation snapshots. | Memory-as-reasoning, not state. | **Low.** Orthogonal. |
| **LangSmith / Langfuse** | Observability traces. | Traces are raw material, not memory. Cortex could ingest. | **Low.** Complementary. |
| **AGENTS.md** | Single-file project instructions; multi-vendor standard. | `.cortex/` is memory, not instructions. AGENTS.md imports it. | **Low — complementary.** Every Cortex project has both. |

**The two that matter.** Letta is close enough that our defense is the Tier-1 Protocol + promotion queue + cross-tool multi-writer invariants — not folder structure. Claude Code is the existential variable below.

### Claude Code existential risk (v3 § 10.4, preserved)

> If Anthropic ships multi-layer native memory in Claude Code 3.x (Doctrine + State + Journal equivalents built into the IDE, consumed by the model without user-authored files), Cortex-as-product competes on worse distribution. Cortex-as-spec survives **only if adopted first** — by enough projects and enough peer agents that Anthropic's rational move is to converge on Cortex rather than ship a proprietary parallel.

**What this means for execution:**

1. **Distribution is the race.** Features are secondary. If Cortex is the pattern visible in hundreds of repos' `AGENTS.md` + `.cortex/` before Anthropic ships, convergence is the default. If Anthropic ships first, Cortex is a curio.
2. **Openness is a moat.** Cortex's strength vs. proprietary memory is that *any agent* can participate: Claude Code, Cursor, Aider, Sentinel, humans. Any feature that compromises this (cloud-only, single-vendor API, closed format) kills the moat.
3. **The spec must be implementable without the CLI.** A project that imports `@.cortex/protocol.md` and `@.cortex/state.md` into `AGENTS.md` gets the read contract for free. The CLI adds enforcement and convenience, not necessity. This is why Protocol § 1's fallback path is load-bearing, not optional.
4. **Watch Anthropic's memory-related roadmap signals.** The `memory/` directory pattern introduced in 2025; sub-agent architectures; MCP memory servers. If a multi-layer native-memory feature ships in Claude Code, treat it as a convergence opportunity: propose Cortex as the de facto spec for what they're doing natively.

## Consequences / action items

- [ ] Re-assess competitive landscape quarterly. Write a new Journal entry; cite and supersede-as-latest against this one. First re-assessment due ~2026-07-17.
- [ ] Monitor Letta releases for trigger-discipline features. If Letta ships machine-observable triggers with audit, the defensible-difference claim weakens.
- [ ] Watch Anthropic changelogs for memory-layer features. A multi-layer native memory shipping triggers a strategy rewrite.
- [ ] Prioritize `brew tap autumngarage/cortex` and source-install paths (uv tool install) in Phase B so adoption friction is ~zero.
- [ ] Keep `@.cortex/protocol.md` + `@.cortex/state.md` the recommended AGENTS.md imports so Cortex works even without the CLI — the distribution-race floor.

## What we'd do differently

Nothing in this decision is retroactively corrective — the choice to keep strategy out of the spec was right. What this entry does is ensure the strategy has a durable home before the drafts that contained it are archived. Future archival of working documents should include this kind of provenance sweep: strategic content that lived in a draft goes into a Journal entry before the draft is moved.
