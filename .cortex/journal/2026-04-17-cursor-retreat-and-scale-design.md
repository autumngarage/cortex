# Cursor's Memories retreat + Cortex's scale design decisions

**Date:** 2026-04-17
**Type:** decision
**Cites:** plans/vision-sharpening, journal/2026-04-17-vision-critique-round-1

> Deep research on what Cursor actually shipped and pulled back with their Memories feature, plus a design decision on how Cortex handles auto-cleanup / consolidation so it improves with scale rather than decaying into an Obsidian graveyard. Both feed v2 of the vision.

## What Cursor did (primary source notes)

- **2025-01-04** — master feature-request thread ([forum #39109](https://forum.cursor.com/t/persistent-intelligent-project-memory/39109)) opens. Dan Perks (Cursor staff): *"This is definitely something we're interested in… nothing ready to announce right now."*
- **Cursor 0.51 (May 2025)** — Memories introduced in beta. Auto-extracts facts from chats via sidecar model. Project-scoped. **Required disabling privacy mode** — community called this a "Dark Pattern." RichardRourc in [forum #98509](https://forum.cursor.com/t/0-51-memories-feature/98509): *"Some companies do not allow code leakage."*
- **Cursor 1.0 (June 2025)** — Memories officially shipped.
- **Throughout 2025** — community builds workarounds because built-in Memories didn't meet the bar:
  - `.brain/` folder (MEMORY.md / SESSION.md / LOG.md), git-committed, cross-tool. Pitch: *"Cursor in the morning, Claude Code in the afternoon — both read the same `.brain/`."* ([forum #157488](https://forum.cursor.com/t/persistent-memory-for-cursor-that-survives-every-session-brain-folder-approach/157488))
  - Memory Bank framework (six slash-commands with custom modes)
  - Recallium (MCP-based self-hosted)
  - Users describe all of these as *"half-baked and unreliable."*
- **2025-11-22 (Cursor 2.1)** — **Memories removed. Custom Modes removed.** Staff: *"intentionally removed."* Migration path: `/command` Commands and `.cursor/rules/`. No detailed public rationale. ([forum #143744](https://forum.cursor.com/t/custom-modes-and-memories-gone-in-2-1/143744))

## Load-bearing implications for Cortex

1. **Cortex is solving the problem Cursor explicitly retreated from.** 15+ months of feature requests unresolved. The retreat is public evidence the problem is unsolved, not unimportant.
2. **The `.brain/` convention already emerged in the community.** Cortex isn't inventing — it's specifying a pattern users have been hand-building. First-mover on the convention is gone; first-mover on the *discipline* is up for grabs.
3. **Privacy is structural, not a feature.** Cortex is local markdown + git. No cloud sidecar. Privacy-trivial by construction; this is a defensible positive against Cursor's approach.
4. **Auto-writes without human review is what failed.** Cursor tried permissionless auto-extraction and pulled it back. Our design (agent drafts via event triggers → human reviews promotion candidates) is the split Cursor seems to have concluded they couldn't ship cleanly.
5. **Cross-tool compatibility is the stated user want.** MindLink `.brain/` leads with "works across 12 agents." Validates Cortex-as-protocol framing.

## Design decision: auto-cleanup as *consolidate and archive*

The principle: **Cortex improves with scale because the default read surface stays lean regardless of corpus age.** Nothing is ever deleted — everything stays in git. Pruning is a misnomer; Cortex consolidates and archives.

### Tiered retention per layer

| Layer | Mechanic |
|---|---|
| **Doctrine** | Never archived. Superseded entries stay with `superseded-by:` pointer; dropped from default load. Top-K by semantic relevance loads at session start. |
| **Journal** | Hot (0–30d) → Warm (30–365d) → Cold (>365d, moved to `journal/archive/<year>/`). Default load is hot + monthly digests, never warm or cold. |
| **Plans** | Status `active` → hot. `shipped` / `cancelled` → auto-moved to `plans/archive/` after 30d. |
| **Map** | Always regenerated. Old versions are git history. |
| **State** | Always regenerated. "Shipped recently" section auto-ages at 90d. |
| **Procedures** | `cortex doctor` flags dead code references; human moves to `procedures/archive/` if appropriate. |

### Consolidation is the load-bearing mechanic

Monthly (configurable): Cortex proposes a **Journal digest** — a summary of the period's key decisions and learnings, with citations to the originals. Human approves with a keystroke. Digest lives in `journal/` as a special type; originals stay in warm/cold. Agents default to reading digests.

Over years, this produces a navigable history without requiring agents to read thousands of entries.

### Promotion is the other pruning mechanic

Journal → Doctrine promotion is how recurring lessons graduate into the always-loaded layer. Once a lesson is in Doctrine, the original Journal entries stop being load-bearing — they age into warm/cold naturally. **Promotion is not just curation; it's the mechanism that lets Journal grow without drowning the read surface.**

### Scale behavior (the claim)

- Year 1: ~1,000 Journal entries, ~20 Doctrine. Default load: ~5k tokens.
- Year 3: ~4,000 Journal, ~80 Doctrine, monthly digests. Default load: ~5–7k tokens (hot-window bounded, digests replace raw warm, top-K on Doctrine).
- Year 10: ~15,000 Journal, ~300 Doctrine, quarterly digests form a narrative arc. Default load: ~7k tokens. Archive queries retrieve old context on-demand.

### Failure modes the spec must explicitly prevent

1. **Unbounded hot load.** Hard cap on session manifest; semantic retrieval for anything beyond the cap.
2. **Unreviewed promotion candidates piling up.** Surface the queue on every `cortex status` call.
3. **Consolidation skipped.** Digest proposal is automatic; missing monthly digests surface as staleness in `cortex doctor`.

## One-line version for v2

> Cortex is append-only at write and tiered at read. Nothing is deleted; everything consolidates. A project's memory grows richer over time, not noisier.

## UX decisions for v2 (locked)

- **Three human commands:** `cortex status`, `cortex promote <id>`, `cortex regen state`. Everything else is agent-driven via the Protocol.
- **Two read-surfaces:** human prose (browsing six layers) + agent manifest (auto-generated, token-budgeted). `cortex manifest --budget 8000` emits what the agent should load. This is a material spec addition, implying v0.2.0 bump.
- **Projection contract:** `AGENTS.md` imports `@.cortex/state.md` via Claude Code's native import syntax. Convention-based, not mechanical.
- **The Cortex Protocol (new concept):** the set of event triggers that cause an agent to journal, defined in `AGENTS.md` as enumerable, inspectable, overridable rules. This becomes the heart of the vision, not a footnote.

## Next

- [ ] Write v2 of `vision-draft.md` (will be `vision-draft-v2.md` so v1 stays for comparison).
- [ ] Send v2 to codex + gemini for round-2 critique with same pushback framing.
- [ ] Decide README promotion path after round 2 passes.
