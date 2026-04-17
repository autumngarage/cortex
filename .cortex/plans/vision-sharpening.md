---
Status: shipped
Written: 2026-04-17
Shipped: 2026-04-17
Author: human
Goal-hash: sharpen-cortex-vision-2026-04-17
Updated-by:
  - 2026-04-17T10:00 human (created after crash — second attempt at vision sharpening)
  - 2026-04-17T14:00 claude-session-2026-04-17 (three research summaries added after round-1 explorer agents)
  - 2026-04-17T17:00 claude-session-2026-04-17 (round-1 critique outcomes, Cursor-retreat research, user clarifications)
  - 2026-04-17T23:45 claude-session-2026-04-17 (marked shipped; v3 promoted to README/SPEC/Doctrine 0004/protocol.md/journal)
Promoted-to: README.md, SPEC.md v0.2.0-dev, .cortex/protocol.md, doctrine/0004-scope-boundaries, journal/2026-04-17-vision-v3-promoted
Cites: doctrine/0001-why-cortex-exists, doctrine/0003-spec-is-the-artifact, journal/2026-04-17-vision-session-lost-to-crash
---

# Plan: sharpen Cortex's vision

> The first attempt at this plan was lost in a Claude Code crash (see journal). This second attempt is checkpointed at each phase so it survives the next one. The goal is a sharpened vision that (a) names what Cortex uniquely does, (b) survives direct comparison with Letta, Claude Code's own memory, AGENTS.md, Zep/Graphiti, and the PKM lineage, and (c) describes the seam with Touchstone and Sentinel crisply enough that the three tools compose without overlap.

## Success criteria

- A `vision-draft.md` in the repo that passes two independent critiques (`codex exec`, `gemini`) without either agent identifying a load-bearing claim that's overclaimed, wrong, or already done better elsewhere.
- A decision, post-critique, about which parts of the vision land in `README.md`, which become new Doctrine entries, and which are filed as open questions.
- A Journal entry recording the multi-agent critique round (Codex said / Gemini said / we decided), so future sessions can re-grind the thinking without re-doing it.

## Research checkpoints (2026-04-17)

Three parallel Explore agents returned structured reports. Full outputs preserved in the conversation and in task-output files; compressed findings below for survival-through-crash.

### (R1) Sigint manual docs as evidence

Read as a body of work — 15 files under `~/Repos/sigint/agent/` plus `CODEX_AUTOFIX_DISABLED.md`. Key findings:

- **Six emergent document types** evolved by hand: Thesis, Architecture, Plan, Tactical-roadmap, Post-mortem, Reference. Each has an unwritten trigger, update rhythm, and authoring mode. These map closely onto Cortex's six layers but the shape isn't identical — e.g. sigint has *both* `NEXT_PHASE.md` (tactical roadmap) and plan docs; Cortex collapses them into State + Plans.
- **Patterns that worked** (must survive into Cortex unchanged):
  - "Empirical bar, not calendar date" — goals are falsifiable gates, not deadlines (`NEXT_PHASE.md:3-4`).
  - "What Would Break the Thesis" section — every claim has a falsification condition (`INVESTMENT_THESIS.md:150-174`).
  - Staged execution with rollback gates — `OPTIONS_UNBLOCK_PLAN` stages 0–4, each independently revertable.
  - Forensic post-mortems that add AST guardrails, not just prose (`COLLECTOR_MIGRATION.md:22-26`).
  - `filepath :: § Section (lines X-Y)` citation format — unambiguous.
  - "Known limitations shipped in this PR" sections — deferrals tracked inline, not scattered.
- **Patterns that broke, visibly in the prose** (these are why Cortex exists):
  - Premature-completion declaration then 2-day silent prod failure (collector migration, Apr 5 → Apr 7).
  - Stale aggregator (`/sig-status`) reporting "normal" while the resolution pipeline was dead for 4 days.
  - Two competing plan docs for the same effort (`OPTIONS_PIPELINE_PLAN` vs `OPTIONS_UNBLOCK_PLAN`) — supersede relationship implicit, not explicit.
  - LOTR → functional rename half-migrated, old names still in code/tables.
  - 6 deferrals scattered into one PR's "known limitations" section — transparent but evidence of decision fatigue.
- **Gaps** — doc types that should exist but don't in sigint: standalone forensic RCAs (lessons embedded in fix plans and thus tied to their lifecycle); decision journals at the parameter level (no record of *why* `target_delta=0.12` was chosen); deprecation index; calibration-learning narrative; ops runbook.
- **Tightening candidates** for Cortex: standardize status field format across layers, formalize deferral tracking with issue-linked tags, enforce the precise citation format, distinguish "open questions for the reader" from "missing context the author doesn't have."

### (R2) Autumngarage composition seams

Read Touchstone and Sentinel repos. Key findings:

- **Touchstone owns policy**: principles/, scripts/, `.touchstone-config`, pre-commit hooks. Immutable after bootstrap (updates via `touchstone sync`). Does NOT write project memory, state, or decisions.
- **Sentinel owns execution**: `.sentinel/runs/*`, `.sentinel/scans/*`, `.sentinel/backlog.md`, `.sentinel/verifications.jsonl`. Philosophy: *derive, don't persist.* Reads `README.md`, `CLAUDE.md`, `AGENTS.md`, principles/ fresh each cycle. Explicitly rejects a persisted goals file as a "second source of truth."
- **Cortex owns reasoning** (planned): Doctrine, Map, State, Plans, Journal, Procedures. Reads own layers + `.sentinel/runs/` + `git log`.
- **Composition already works** via file contract today — Sentinel detects Touchstone via `shutil.which("touchstone")` and reads `.touchstone-config`. Cortex will be the first with *multiple writers* to one layer (Cortex CLI, Sentinel hooks, Touchstone hooks, humans, Claude Code).
- **Sharper framing than "foundation / loop / memory":** **policy / execution / reasoning**. Or: reflexes / sensorimotor / cognitive. The biological framing (cytoskeleton / metabolism / genome) holds for Touchstone and Sentinel but is imprecise for Cortex — Doctrine = genome, Journal = episodic memory, Plans = active intention. Don't conflate.
- **Real overlap risks**:
  - Sentinel derives from `CLAUDE.md` each cycle; Cortex Doctrine claims to be the reasoning source of truth. If Doctrine is updated but `CLAUDE.md` is not, the next Sentinel cycle derives from stale `CLAUDE.md`. Need a mitigation (spec addition? documented convention?).
  - Plans are Cortex's only mutable layer. If both a human and `sentinel-coder` create plans for the same effort, they collide. Need an `Author:` field and a collision-detection story.
- **Load-bearing integration debt**: spec mentions but does not wire (a) Sentinel end-of-cycle → Journal entry, (b) Touchstone pre-merge → Doctrine draft, (c) Cortex Doctrine → Sentinel lens-generation context. Without these, Cortex is useful but underutilized. These are Phase E targets; the vision should be honest that Phase E is where "the three tools compose" becomes real, not Phase B.

### (R3) Public prior art gap-fill

Read Letta, Cursor Rules, Aider repo-map, Claude Code CLAUDE.md + auto-memory, Graphiti/Zep, Obsidian/Logseq/Dendron, recent agent-memory papers (Reflexion, MetaGPT, ChatDev, A-MEM), AGENTS.md. Key findings:

- **Letta MemFS is 80% of the idea.** Git-backed, Markdown + YAML, dual human/agent authorship, progressive disclosure. Differences: Letta is *agent-first and permissive*; Cortex is *human-first with layered write gates*. Letta has no regeneration, no Diataxis discipline, no promotion gates. The closest analogue by a wide margin — the vision **must** name it honestly, not pretend it doesn't exist.
- **Claude Code CLAUDE.md + auto-memory is the existential threat.** Two-tier (CLAUDE.md + `memory/`) built into the IDE developers already use. If Anthropic ships multi-layer memory natively, Cortex-as-standalone-spec dies. Defense: speed to adoption. If Cortex is the de facto standard first, Anthropic would converge on it. This is a distribution race, not a features race.
- **AGENTS.md is complementary, not competing.** August 2025 standard for "project discovery for agents" (OpenAI, Google, Cursor, Factory, Sourcegraph). Single file at root. `.cortex/` is project *memory*, not project *discovery*. A project should have both. Vision should say this explicitly.
- **Cursor Rules, Aider repo-map, Obsidian/Dendron** — different use cases. Rules = behavior instructions. Repo-map = ephemeral context filter. Obsidian = personal PKM with known link-rot problem. None threaten Cortex's positioning.
- **Graphiti/Zep** is a different tradeoff — opaque graph optimized for retrieval latency (agent-at-scale). Cortex trades query latency for human readability and git-nativeness (human-in-the-loop teams). Both can coexist; they serve different consumers.
- **Recent papers** (MetaGPT, ChatDev, A-MEM) cluster around document-centric or graph-centric memory. None propose Cortex's mix: file-centric + regenerating + layered + Diataxis-disciplined.
- **Where Cortex is genuinely novel** — not the individual layers (each has prior art) but the *combination*:
  1. Six-layer stack with per-layer authoring-mode discipline (Diataxis).
  2. **Regeneration as the key insight** — Map and State are *computed from primary sources* (code, git, Journal) on schedule, not hand-maintained. Flips the problem from "keep docs in sync" to "were docs regenerated?"
  3. Human-first write gates (Journal append-only, Doctrine immutable-with-supersede, Plans require grounding + success criteria).
  4. Cross-layer promotion (Journal → Doctrine) with bidirectional linking.
  5. Staleness surfaced, not hidden — `Generated:` headers with source lists and thresholds.
- **Where Cortex might be re-inventing the wheel** (honest risks to name in the vision, not hide):
  - Letta matures its discipline → Cortex becomes a formalization of Letta.
  - Claude Code ships multi-layer native memory → Cortex becomes obsolete unless already adopted.
  - AGENTS.md + one memory file wins for simplicity → Cortex's six layers look baroque.

## Next steps

- [ ] (task #4) Draft `vision-draft.md` at repo root as a scratch working artifact — not committed as permanent, lives outside `.cortex/` because it's not a spec-compliant layer.
- [ ] (task #5) `codex exec` critique of the draft with explicit pushback prompt.
- [ ] (task #6) `gemini` critique of the draft with the same prompt.
- [ ] (task #7) Fold critiques, decide what lands in `README.md`, what becomes Doctrine, what becomes Journal. Write Journal entry recording the critique round.

## Open questions to resolve during drafting

- **How hard to lean on the crash story?** It's the most concrete "why Cortex" example we have, but it's a self-promoting anecdote. Candidate for Doctrine 0004 or just a Journal entry cited from README.
- **Should the vision re-frame from six layers to three concerns?** R2 suggests *policy / execution / reasoning* is sharper than *foundation / loop / memory*. But "six layers" is what the spec actually defines — don't let the vision narrative drift from the spec.
- **How to handle the Claude Code existential threat in public?** Saying "Anthropic might ship this natively" out loud is honest but potentially demoralizing. Alternative: frame Cortex as a *spec* (protocol), which is adoption-independent — if Anthropic adopts it, we win; if not, we win by being open and composable.
- **Does the Sentinel-reads-stale-CLAUDE.md risk need a spec amendment?** The current spec does not address drift between Doctrine and CLAUDE.md. Either (a) spec says Doctrine must be mirrored to CLAUDE.md (coupling), (b) spec says CLAUDE.md should cite Doctrine (looser), (c) Phase E problem only.

## Deferred from this plan

None yet. All items from the research synthesis are in scope for the draft.

---

## Update — 2026-04-17 afternoon

**Round 1 critique from Codex and Gemini complete.** Journal entry at `journal/2026-04-17-vision-critique-round-1.md` captures both. Four structural questions surfaced. User has now clarified two of them, which reframes the work:

- **Core vision correction.** Cortex is "the brain" — an agent-driven continuous note-taker for the team. The agent captures what's happening and why *as it happens*, triggered by change events. The human's role is to review and promote, not to remember and write. This is explicitly what the draft underweighted. Gemini's "human-first gates are bureaucracy" critique lands for the draft-as-written but dissolves under the corrected framing.
- **Projection contract.** Simple. `CLAUDE.md` / `AGENTS.md` instructs the agent to follow the Cortex journaling protocol (read `.cortex/` on start, journal continuously during work, obey append-only Journal and immutable-Doctrine rules). It's a convention like AGENTS.md itself, not a mechanical integration.

**Still open.** Map (derived-on-read vs. persisted) and read economics (what loads at session start under a context budget).

**Research pass #2 in flight.** Gap-fills that round 1 missed or under-covered, specifically under the agent-driven-continuous-note-taking framing:
- Mem0 (production LLM memory platform — major gap in round 1)
- Cognee (open-source agent memory framework)
- Letta — specifically *how* agents self-edit MemFS (reflection subagents, /doctor, core memory paging)
- Claude Code's `memory/` consolidation ("dreaming") and how it's paged
- Cursor Memories (late-2025 feature)
- LangChain memory modules (historical) + LangGraph checkpointing
- Trace-as-memory: LangSmith, Langfuse, Helicone — do continuous agent traces already solve what Cortex wants?
- MIRIX, A-MEM, Letta paper — autonomous agent memory surveys
- The specific pattern of "AGENTS.md-style routing to a memory subdirectory"

Expected output: (a) whether agent-driven continuous journaling is already solved by an existing tool, (b) what specifically Cortex's layering + human-review promotion adds that none of them does, (c) what the read-budget story needs to look like.
