# Vision v3 promoted — Protocol shipped, spec bumped to v0.2.0-dev

**Date:** 2026-04-17
**Type:** decision
**Cites:** plans/vision-sharpening, doctrine/0001-why-cortex-exists, doctrine/0002-compose-by-file-contract-not-code, doctrine/0003-spec-is-the-artifact, doctrine/0004-scope-boundaries, journal/2026-04-17-vision-session-lost-to-crash, journal/2026-04-17-vision-critique-round-1, journal/2026-04-17-cursor-retreat-and-scale-design
**Promoted-from:** vision-draft-v3.md

> The vision-sharpening work that started with a crashed session (see `journal/2026-04-17-vision-session-lost-to-crash.md`) ended today with v3 of the vision promoted into the repo's durable artifacts. This entry captures what landed where, what the critique rounds surfaced, and what spec-level decisions were made.

## The path

Three rounds of drafting, two rounds of multi-agent critique (Codex + Gemini), one round-2 Gemini unavailability (Google capacity exhaustion on gemini-3.1-pro-preview and gemini-2.5-pro — noted so future sessions know it's a real thing). The research and critique artifacts:

- Round-1 research: sigint-corpus analysis, autumngarage composition map, public prior art gap-fill. Summarized in `plans/vision-sharpening.md`.
- Round-1 critique: Codex + Gemini, both read-only. Surfaced the projection-authority gap, the human-first-gates friction, the packaging-reads-as-novelty risk, the MVP-collapses-into-NOTES.md risk, the token-tax absence, and the false-freshness risk. Summary at `journal/2026-04-17-vision-critique-round-1.md`.
- User vision clarification: *Cortex is the brain — the agent continuously takes notes of what's happening and why, triggered as changes occur. The human reviews and promotes.* Reframed the draft's gates-as-feature framing into agent-drafts/human-promotes.
- Round-2 research: deeper dive on LLM memory tools (Mem0, Cognee, Letta deep, Claude Code memory deep, Cursor Memories saga, LangGraph, trace-as-memory). Found Letta is 80% of the idea; Cortex's defensible shape is the Protocol + promotion queue + invariants + cross-tool composition.
- Cursor-retreat research: deeper investigation of Cursor's 0.51 Memories introduction (May 2025), 1.0 shipping (June 2025), 2.1 removal (November 22, 2025). Community `.brain/` workaround convention. Empirical validation of the agent-writes-with-human-review split.
- Scale design: tiered retention, consolidate-not-delete, promotion-as-pruning, digest-audit invariants. Summary at `journal/2026-04-17-cursor-retreat-and-scale-design.md`.
- One-command UX: user requirement that the interactive `cortex` invocation be the entire human-facing surface. Partially answers the promotion-queue-as-second-inbox critique by making the queue impossible to ignore.
- Round-2 critique (Codex only, Gemini unavailable): flagged Protocol-still-a-checklist, promotion-queue-becomes-second-inbox, consolidation-reintroduces-false-freshness, uneven-jumps-ahead-claims, Touchstone/Sentinel-authority-leaks. Each answered in v3.

## What landed where

- **`vision-draft-v3.md`** — the final working vision doc. Stays in repo root alongside v1 and v2 for comparison. Candidate for archival after Phase B ships.
- **`README.md`** — rewritten as the public-facing vision. Trimmed from v3 §§ 0, 1, 2, 3, 5, 7. Leads with the two stories (crash + Cursor retreat); names the Protocol as the product; composition with Touchstone and Sentinel clarified under the universal-vs-local authority framing.
- **`.cortex/protocol.md`** — NEW. The concrete Cortex Protocol spec: two-tier triggers (Tier 1 machine-observable, Tier 2 advisory), three invariants (append-only Journal, immutable Doctrine, seven-field metadata contract), template references, project customization rules. Imported into `AGENTS.md`.
- **`SPEC.md`** — bumped to 0.2.0-dev with the following changes:
  - Seven-field metadata contract (`Generated:`, `Generator:`, `Sources:`, `Corpus:`, `Omitted:`, `Incomplete:`, `Conflicts-preserved:`) replaces the two-field version on Map, State, and digests.
  - Plans gain `Author:`, `Goal-hash:`, `Updated-by:` frontmatter to make multi-writer collisions visible.
  - New § 4.7: promotion queue operational rules (candidate states, WIP limit, aging, complexity split).
  - New § 4.8: single authority rule (root agent files may route to `.cortex/` but must not duplicate without `grounds-in:` citation).
  - New § 8: retention and consolidation (tiered hot/warm/cold, monthly/quarterly digests, depth cap, audit sampling).
  - § 7 sharpened: "Does not synthesize without permission" remains; Protocol-triggered writes ARE permission because the Protocol is a declared contract. Silent discretionary writes remain forbidden.
  - Cross-reference to `.cortex/protocol.md` added as the companion artifact.
- **`.cortex/doctrine/0004-scope-boundaries.md`** — NEW. The "explicitly not" list as durable Doctrine: not a vector store, not a database, not a knowledge graph, not a portfolio tool, not an agent framework, not a replacement for AGENTS.md, not cloud-hosted, not a replacement for git. Defense against scope creep; any future boundary move requires a superseding Doctrine entry.
- **This journal entry** — the record of how the vision was sharpened, what the critics flagged, what the user clarified, what landed.

## Spec-level decisions locked in v0.2.0-dev

1. **"The Cortex Protocol"** is the product centerpiece. Named as such. Promotes the trigger set from implementation detail to headline.
2. **Protocol-triggered writes are explicit invocation.** An agent writing to `.cortex/` must be either (a) explicitly invoked by a human, or (b) acting on a declared Tier 1 Protocol trigger. Silent discretionary writes remain forbidden. This sharpens SPEC.md § 7 rather than reversing it.
3. **Seven-field metadata contract** replaces the prior two-field contract on generated layers. Audit trail, not just timestamp.
4. **Promotion queue has operational rules**, not just visibility — WIP limit, candidate aging, explicit state enum. Surfacing at every `cortex` invocation partly mitigates the "second inbox" risk.
5. **Digests have depth cap + audit sampling**, not just `Incomplete:`. Addresses the false-freshness risk that round-2 flagged.
6. **Single authority rule for reads.** Root agent files route to `.cortex/`; they don't duplicate. `cortex doctor` detects drift.
7. **Plans are multi-writer-visible** via `Author:` + `Goal-hash:` + `Updated-by:` fields. Collisions are surfaced, not prevented.

## Solo vs triad — honest framing now in the spec

Solo Cortex is *good notes with conventions*. Triad Cortex (with Touchstone + Sentinel) is *enforced institutional memory*. Invariant enforcement requires Touchstone's pre-push hook; without it, invariants are advisory. The README and SPEC both say this explicitly now, to prevent the overclaim Round-2 flagged.

## What did not ship (deferred)

- **Actual CLI** — Phase B territory. This session shipped the spec + Protocol + Doctrine, not code.
- **Trigger-template files** (`.cortex/templates/*`) — Phase B will scaffold them; the Protocol just references them.
- **Goal-hash normalization** — `§ 7.1` introduces the concept; the exact normalization (tokenization? embedding?) is deferred to Phase B implementation.
- **`cortex doctor` audit cadence** — CI-only? Pre-commit? Periodic? Decision deferred to Phase B.
- **Retry of Gemini round-2 critique** — Google capacity was exhausted during this session; worth attempting when capacity returns, but v3 is defensible enough to proceed without it.
- **Cross-project Doctrine** — out of scope for v0.x; will be reconsidered in v1.

## What we'd do differently

- **Checkpoint research and drafting to disk more aggressively.** The crash that opened this session lost hours of parallel-agent discussion because nothing had been committed. The journal entry at `journal/2026-04-17-vision-session-lost-to-crash.md` made that lesson durable; this session's workflow (research → plan-file update → draft → journal entry at each major phase) is the practical application.
- **Fewer research agents per pass, more focused questions.** Three parallel agents in round-1 worked well; one focused agent in round-2 (after the vision corrected) was sharper. Broad sweeps dilute.
- **Start with the strongest critic prompt.** Round-1's critique prompt asked for "5 sharp critiques"; round-2's sharpened by naming specific v2 sections to challenge. Round-2 critiques were more useful because they were more constrained.
