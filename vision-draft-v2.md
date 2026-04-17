# Cortex — Vision (draft v2)

> **Status:** working draft, not the README. v1 at `vision-draft.md` preserved for comparison. This v2 folds in the round-1 critiques from `codex exec` and `gemini`, the user's vision correction ("Cortex is the brain; the agent takes notes as changes happen"), the deeper research on LLM memory tools (Mem0, Letta, Claude Code, Cursor's retreat), and the scale/consolidation design.
>
> **Date:** 2026-04-17
> **Spec implication:** probable v0.2.0 bump (new Protocol section, two read-surfaces, consolidation mechanics).

---

## 0. The two stories that ground this

### The crash

Earlier today, a Claude Code session doing multi-hour vision-sharpening work crashed. Every research finding, every branch of the discussion, every critique from peer agents: gone. Git clean on restart. Memory system empty. Next session's first honest answer was *"I don't know where we left off."*

### The retreat

On 2025-11-22, Cursor shipped version 2.1 and **removed the Memories feature** they had introduced just six months earlier. Custom Modes went too. The official rationale was two words: *"intentionally removed."* Six weeks before the removal, a community member had open-sourced [`.brain/`](https://forum.cursor.com/t/persistent-memory-for-cursor-that-survives-every-session-brain-folder-approach/157488) — a three-file git-committed markdown memory structure that "works across 12 agents from the same memory. Cursor in the morning, Claude Code in the afternoon — both read the same `.brain/`." The [master feature-request thread](https://forum.cursor.com/t/persistent-intelligent-project-memory/39109) for persistent project memory has been open since January 2025 and is still unresolved today.

These two stories define the problem Cortex exists to solve:

> **Projects accumulate reasoning that has nowhere to live. Chat context evaporates. Agent memory is machine-local. The vendor closest to solving it shipped, then retreated. The community is hand-building the same three files in every repo. The pattern is clear; the spec is missing.**

Cortex's bet: turn the emergent `.brain/` convention into a spec with teeth. A file-format protocol any agent follows, any team audits, and the vendor-retreated failure modes (auto-writes with no review, cloud-sidecar privacy tax, machine-local silos) are addressed by design.

---

## 1. What Cortex is, in one paragraph

Cortex is **a protocol for agents to continuously journal what's happening on a project, and for humans to promote what matters**. It defines a `.cortex/` directory with six layers of Markdown files, a set of event triggers that tell any agent *when and how* to write, a promotion pipeline that graduates recurring lessons into immutable Doctrine, and a two-surface read model (human prose + token-budgeted agent manifest) that keeps default context lean as the corpus grows. Writers are multiple — any Claude Code session, any Sentinel cycle, any Touchstone pre-merge hook, any human. The filesystem is the transport. The Protocol is the product.

---

## 2. What we keep (and from whom)

Nothing about the individual layers is novel. The list, attributed:

- **Letta MemFS:** pinned `system/` folder always in context; YAML frontmatter per file; read-only flags; `/doctor` subagent; reflection subagents for consolidation; git as the transport. Closest existing analogue by a wide margin.
- **Cursor Memories (pre-retreat):** the sidecar-observer pattern — a second model watches the primary conversation and captures, so the working agent isn't burdened.
- **Cursor `.brain/` community:** the three-file primitive (MEMORY / SESSION / LOG); git-committed; cross-tool by design; the community-validated shape.
- **Claude Code auto-memory:** `MEMORY.md` as index with a 200-line startup-load cap; topic files on-demand; `@path` import syntax for cross-file references.
- **AGENTS.md:** single-convention file at root; nested discovery; multi-vendor-neutral. The precedent Cortex follows.
- **MemGPT / Letta papers:** virtual-memory paging (hot / warm / cold); reflection for consolidation.
- **Mem0:** entity linking for dedup; extraction as a first-class phase; the multi-level scoping vocabulary (user / session / agent).
- **ADRs (Nygard):** immutable-with-supersede; context-decision-consequences structure.
- **SRE postmortems:** canonical sections for incident-type Journal entries.
- **Diataxis:** one authoring mode per document.
- **WAL + checkpoint:** append-only log plus regenerated projection.
- **sigint manual practice:** empirical bars not calendar dates; falsifiability sections on every thesis; staged plans with rollback gates; precise `file::§section:lines` citation format.

---

## 3. Where we jump ahead

Five moves that no existing tool makes together. These are the load-bearing claims.

### 3.1 The Cortex Protocol: event-triggered journaling, not judgment-based

Letta and Cursor Memories both rely on *agent judgment* about what's memorable. Both have leaked or been retreated from. Cortex instead specifies an **enumerable, inspectable, overridable set of event triggers** in `AGENTS.md`. When any of these fires, the agent writes a Journal entry from a matching template:

- **Before a non-trivial change** (>50 lines or 3+ files touched): one-paragraph intent note.
- **On a failed attempt followed by retry:** what was tried, why it failed, what's next.
- **On a decision phrased as "we decided" / "we chose" / "let's":** decision, alternatives considered, driving constraint.
- **On a stack trace or test failure after passing:** error, hypothesis, fix or next step.
- **On removing an abstraction, file, or dependency:** why it's no longer load-bearing.
- **On session boundary:** State update if any P0 changed.
- **On Sentinel cycle completion** (if present): cycle outcome summary.
- **On Touchstone pre-merge for architecturally significant diffs:** Doctrine candidate draft.

The trigger set lives in `.cortex/protocol.md` and is imported into `AGENTS.md`. It is project-customizable. An agent asked *"why did you write this entry?"* can point at the trigger that fired. A team asked *"why doesn't this project have Journal entries for X?"* can inspect which triggers are disabled.

**This is the specific novelty no existing tool ships.** Letta's agent writes on judgment; Cursor Memories extracted on sidecar judgment; Claude Code's auto-memory writes on Claude's discretion. Cortex writes on protocol. Protocol is inspectable; judgment is not.

### 3.2 Promotion by proposed candidate, not by remembering

Humans will not remember to promote Journal entries to Doctrine. The design has to accept this.

Cortex continuously surfaces **promotion candidates**: *"These 3 Journal entries last week reference 'we decided X because Y' — promote to Doctrine?"* The review is one keystroke per candidate. Candidates are ranked by recurrence (lesson stated in 3+ entries), by supersede signal (new entry marks an old Doctrine as stale), or by explicit flag (agent wrote `promote: candidate` in frontmatter).

**This is the split Cursor seems to have concluded they couldn't ship cleanly.** Auto-writes with no review were fragile in production. Cortex decouples: writes happen continuously (cheap, agent-driven); promotions happen on a queue (rare, human-gated). The queue stays ahead of the corpus; the human stays ahead of the queue.

### 3.3 False-freshness guards by construction

Codex's sharpest round-1 critique: *"A timestamp proves recency, not correctness. An LLM can omit a critical failed attempt, collapse conflicting sources into a false priority, or produce a clean summary from incomplete inputs. That failure is worse than stale docs because the freshness header invites trust."*

Cortex answers this at the protocol level. Every generated layer (Map, State, digests) carries *three* metadata fields, not one:

- `Generated:` — ISO8601 timestamp.
- `Sources:` — list of inputs read, with row counts / commit SHAs.
- `Incomplete:` — inputs attempted but missing, truncated, or unreachable. Non-empty means: *consumer should treat with caution; the regeneration is best-effort*.

A regeneration with an unreachable git commit fails loud — `Incomplete:` is populated, `cortex doctor` surfaces it, the projection flags itself as partial. `Generated:` alone is a trust invitation; `Generated:` + `Incomplete:` is an audit trail.

### 3.4 Two read-surfaces, not one

Gemini's sharpest round-1 critique: *"Cortex forces a brute-force filesystem read of potentially thousands of words of Markdown before the agent can even begin reasoning."*

The fix: Cortex ships **two read-surfaces** from the same files.

- **Human read-surface:** the six layers, browsable, diffable, grep-able. This is the audit trail.
- **Agent read-surface:** an auto-generated session manifest with a token budget. `cortex manifest --budget 8000` emits: State (always, ~1.5k), top-K Doctrine by semantic relevance (~3k), active Plans (~2k), Journal last-72h + monthly digest (~1.5k). Graceful degradation to State-only for 32k windows.

The agent loads the manifest, not the whole directory. The manifest is cheap to recompute (it's a retrieval query, not a regeneration). The human never looks at the manifest; they read the source layers.

### 3.5 Loop closure with Sentinel

No other memory tool has a loop partner. Mem0 sits alongside single-agent apps; Letta's memory belongs to one agent; Cursor's was per-project-per-machine. **Cortex is the only memory system designed for an autonomous writer + a reflective store + an enforcement tool + a human curator seat, composed by file.**

Concretely:

- **Sentinel** runs a cycle; ASSESS reads `.cortex/` (Doctrine + active Plans + recent Journal) instead of re-deriving from `CLAUDE.md`; DELEGATE ships work; end-of-cycle writes a Journal entry: *"Cycle-2026-04-17-1430 shipped PR #42; grounded in Plan `fix-auth-retry`; learned X."*
- **Touchstone** sits before every merge. On architecturally significant diffs, it drafts a Doctrine candidate inline with the PR. Human accepts or skips at commit time, when the decision is fresh.
- **Cortex** runs no cycle itself. It's ambient. Multiple writers feed it; the CLI is the human's curator seat.

This loop makes the memory *lived*, not filed. A year in, Doctrine reflects actual experience, not aspirational design. Journal reflects what happened. Plans reflect what's next. State reflects now. No vendor can do this alone because no vendor owns both the autonomous writer and the memory store.

---

## 4. The Cortex Protocol (spec sketch, implementation pending)

The Protocol has three components, all in `AGENTS.md` or imported from there.

**4.1 Read on session start.** Load `cortex manifest --budget <N>` and treat the output as grounding context. The manifest is a slice, not the whole store.

**4.2 Write on event triggers.** The trigger set in § 3.1. Each trigger has a template (filename pattern, frontmatter required, prose sections expected). Templates live in `.cortex/templates/` and are customizable per project.

**4.3 Respect invariants.** Three that the agent-facing protocol enforces and the CLI verifies:

1. **Journal is append-only.** Never edit an existing Journal entry in place. New entry per event.
2. **Doctrine is immutable.** Changes happen by writing a new entry with `supersedes: 0003` frontmatter. The old entry stays. Promotion queue surfaces stale Doctrine candidates.
3. **Generated layers declare incompleteness.** `Incomplete:` is mandatory if any input was missing. Empty `Incomplete:` means "I looked at everything I could."

These are the enforceable project-memory invariants Codex argued were the real product — not the taxonomy. The Protocol is the product; the filesystem is the transport.

---

## 5. UX

### 5.1 Solo Cortex (no Sentinel, no Touchstone)

**Day start.** You open a terminal in project X, run `claude`. `AGENTS.md` imports `@.cortex/state.md` and the Cortex Protocol. Claude loads the session manifest — ~5k tokens — and is current.

**During work.** The Protocol tells Claude when to journal. You never ask. On a non-trivial change, a one-paragraph entry lands. On a surprising decision, a decision entry lands with the constraint that drove it. On a failed approach, the miss is logged before the retry. You see these as tool-use lines in the transcript; nothing gets in your way.

**End of session.** `cortex status` shows: 7 Journal entries today; State regenerated at 14:22; 2 promotion candidates. You approve one (`cortex promote j-2026-04-17-auth-retry → doctrine/0005`), skip one. Done.

**After a crash.** Next session loads State + recent Journal. The agent picks up mid-sentence. You type *"where were we?"* and it answers with citations.

**Three human commands, total:** `cortex status`, `cortex promote <id>`, `cortex regen state`. Everything else is agent-driven via the Protocol.

### 5.2 With Touchstone + Sentinel (the triad)

**Touchstone** sits before every merge. When a commit touches architecture, principles, or `.cortex/doctrine/`, its pre-push hook runs: *"This PR looks architectural. Draft a Doctrine candidate?"* The candidate is proposed inline with the diff; you accept or skip at commit time. Promotion happens when the decision is fresh, not as a later chore.

**Sentinel** runs on its own cadence (cron, manual, or demand). It reads Cortex for context (Doctrine + active Plans + recent Journal) rather than re-deriving from `CLAUDE.md`. At end of cycle it writes a Journal entry summarizing outcome. Next cycle reads previous cycle's Journal. The reflective store is the continuity.

**Cortex itself** runs no cycle. It's ambient. Touchstone feeds it at merge; Sentinel feeds it at cycle; humans feed it at `claude` sessions. The CLI is the human's reading and curation interface. Three commands, same as solo mode.

### 5.3 The feel

The project has an accurate, living memory that nobody had to maintain. You check in on it the way you check test results — read the status, accept the candidates, move on. Over months, Doctrine grows slowly by promotion. Journal grows quickly by Protocol. State stays current by regeneration. Digests replace raw Journal in the default read surface as time passes.

The claim: you never again open a blank chat and wonder where you left off. The next session starts where the last one ended, even across crashes, even across team members, even across agent tools.

---

## 6. Scale: consolidate and archive, never delete

If Cortex doesn't answer this, it becomes the Obsidian graveyard in six months and everyone says *"yeah, great for a quarter, then we stopped using it."*

**The principle:** Cortex is append-only at write and **tiered at read**. Nothing is ever lost — everything stays in git. "Pruning" in Cortex means moving entries from hot → warm → cold → digest-represented, never removing them.

### 6.1 Tiered retention per layer

| Layer | Mechanic |
|---|---|
| **Doctrine** | Never archived. Superseded entries stay with `superseded-by:` pointer; dropped from default load. Top-K by semantic relevance at session start. |
| **Journal** | Hot (0–30d) → Warm (30–365d) → Cold (>365d, auto-moved to `journal/archive/<year>/`). Default load is hot + monthly digests, not warm/cold. |
| **Plans** | `active` → hot. `shipped` / `cancelled` → auto-moved to `plans/archive/` after 30d. |
| **Map** | Always regenerated. Old versions are git history. |
| **State** | Always regenerated. "Shipped recently" section auto-ages at 90d. |
| **Procedures** | `cortex doctor` flags dead code references; human moves to `procedures/archive/` if appropriate. |

### 6.2 Consolidation is the scale mechanic

Monthly (configurable): Cortex proposes a **Journal digest** — a summary of the period's key decisions and learnings, with citations to the originals. Human approves with a keystroke. The digest lives in `journal/` as type `digest`; the originals stay in warm/cold. Agents default to reading digests in the manifest.

Over years, digests form a navigable history without requiring agents to read thousands of entries. Year 3's "Q2 2025 — how we rebuilt auth" digest is readable in 200 lines; the 40 original entries are still in `journal/archive/2025/Q2/` for anyone who needs detail.

### 6.3 Scale behavior (the claim)

- Year 1: ~1k Journal entries, ~20 Doctrine. Manifest: ~5k tokens.
- Year 3: ~4k Journal, ~80 Doctrine, monthly digests replace raw warm entries. Manifest: ~5–7k tokens.
- Year 10: ~15k Journal, ~300 Doctrine, quarterly digests form the narrative arc. Manifest: ~7k tokens. Archive queries retrieve old context on-demand.

### 6.4 Failure modes the spec must prevent

1. **Unbounded hot load.** Hard cap on manifest; semantic retrieval for anything beyond.
2. **Unreviewed promotion candidates piling up.** `cortex status` surfaces the queue every call.
3. **Consolidation skipped.** Missing monthly digests surface as staleness in `cortex doctor`.

**Cortex improves with scale because the default read surface stays lean, recurring lessons graduate into always-loaded Doctrine, and the corpus becomes richer evidence for future promotion.** The system that starts as "better notes" becomes "the most accurate institutional memory on the project" precisely because scale makes the signal stronger.

---

## 7. Composition with Touchstone and Sentinel

Cortex is the third tool in autumngarage. The framing from round 1 (*policy / execution / reasoning*) survives the critiques but needs one tightening: the clean trichotomy isn't about *what* each tool does, it's about *authority*.

| Tool | Authority |
|---|---|
| **Touchstone** | *Originates* engineering standards. Principles, hooks, scripts. Prescriptive. |
| **Sentinel** | *Executes* and reports. Runs cycles, ships PRs, writes run journals. Descriptive. |
| **Cortex** | *Remembers and reasons*. Captures decisions, consolidates lessons, serves context. Reflective. |

Each tool installs alone and composes via file contract. The seams:

- **Touchstone → Cortex:** pre-merge hook drafts Doctrine candidates; human reviews at commit. Optional; gated on `.cortex/` existing.
- **Sentinel → Cortex:** end-of-cycle writes Journal entries linking to `.sentinel/runs/*`. Optional; gated on `.cortex/` existing.
- **Cortex → Sentinel:** Sentinel reads `.cortex/doctrine/` + active Plans as lens context, not freeform `CLAUDE.md`.
- **Cortex → Touchstone:** `cortex doctor` checks that `principles/` entries are cited by at least one Doctrine entry.

The three tools compose by reading files the other writes, with graceful degrade if any is absent. None imports another's code. This pattern is already validated (Sentinel has detected Touchstone via `shutil.which` for months); Cortex is the first tool that *multiple others write to*, and the append-only / immutable / generated-with-incomplete invariants are specifically designed for that multi-writer case.

---

## 8. Honest comparison

| Adjacent | What they have | Where Cortex differs | Risk |
|---|---|---|---|
| **Letta MemFS** | Git-backed MD + frontmatter + pinned `system/` + read-only + `/doctor` + reflection subagents + git sync. **Closest analogue.** | Event-triggered Protocol (not agent judgment). Promotion queue (not permissive writes). Human-scale and team-scale, not single-agent. | **Medium-high.** Letta can add layer discipline. Defense: protocol adoption first. |
| **Claude Code `CLAUDE.md` + auto-memory** | Hand-written `CLAUDE.md` + agent-discretion `memory/` + `@path` imports. Built into the IDE devs already use. | Not machine-local; team-shared via git. Not discretionary; protocol-triggered. Structured into six layers with enforceable invariants. | **Existential.** Anthropic could ship multi-layer native memory. Defense: Cortex as spec Anthropic would converge on, not compete with. |
| **Cursor Memories (retreated 2025-11-22)** | Auto-extract via sidecar, project-scoped, required disabling privacy mode. | Local-markdown, privacy-trivial, cross-tool, event-triggered not extraction-based, human-review-gated. | **Low.** Cursor already retreated; the design space is open. |
| **Cursor `.brain/` community convention** | MEMORY.md + SESSION.md + LOG.md, git-committed, cross-tool. **Emergent convention.** | Six layers not three; enumerated Protocol; invariants; promotion pipeline; Map/State regeneration. | **Low — complementary.** We're the discipline the `.brain/` experiments are missing. |
| **Mem0** | Production memory-as-a-service; LLM extraction at boundaries; entity linking dedup. | File-first, git-native, human-auditable, team-shared. No cloud dep, no privacy-mode conflict. | **Low.** Different consumer (enterprise agent apps vs. dev teams). |
| **Graphiti / Zep** | Bi-temporal graph, opaque, optimized for agent retrieval latency. | Markdown, git-native, human-auditable. Different tradeoff (audit vs. latency). | **Low.** Different audience. |
| **LangGraph checkpoints** | State-as-computation snapshots; DB-backed. | Memory-as-reasoning, not memory-as-state. Different model. | **Low.** Orthogonal. |
| **LangSmith / Langfuse** | Observability traces. | Observability isn't memory. Cortex could ingest traces via Sentinel. | **Low.** Complementary; traces are raw material, not memory. |
| **AGENTS.md** | Single-file project instructions; open multi-vendor standard. | `.cortex/` is memory, not instructions. Cortex's `AGENTS.md` imports `@.cortex/state.md` — they compose. | **Low — complementary.** Every Cortex project has both. |

**The two that matter.** Letta is close enough that our defense has to be the Protocol (event-triggered, enumerable, overridable) and the promotion pipeline, not the folder structure. Claude Code's native memory is the existential variable — if Anthropic ships multi-layer memory, Cortex-as-product dies; Cortex-as-spec survives if adopted first.

---

## 9. What Cortex is explicitly not

- **Not a vector store.** Markdown + git + grep. Semantic retrieval for the manifest is a read-side concern, not storage.
- **Not a database.** `.cortex/.index.json` is a cache, not a source of truth.
- **Not a knowledge graph.** Cross-references between files, no graph.
- **Not a portfolio tool.** One project per `.cortex/`. Cross-project Doctrine is a v1 target, not v0.1.
- **Not an agent framework.** No "agents" concept. Writers (humans, CLIs, hooks) follow the Protocol. Any agent framework can read and write.
- **Not a replacement for `CLAUDE.md` or `AGENTS.md`.** `AGENTS.md` is the project's public interface; `.cortex/` is its memory. Every Cortex project has both.
- **Not cloud-hosted.** Local files, git, nothing else. No "Cortex Cloud" to worry about.

---

## 10. Risks (the honest ones)

1. **The Protocol trigger set is under-specified.** "Before a non-trivial change" is less ambiguous than "when you judge something memorable," but still has edge cases. Phase B dogfood on Sentinel's repo is where the trigger set gets tightened.
2. **Promotion queue depth is a UX variable.** If candidates accumulate faster than humans promote, Doctrine lags Journal. `cortex doctor` surfacing the queue is necessary but not sufficient.
3. **Digest quality depends on LLM synthesis.** If monthly digests omit a critical lesson, the warm-archive move loses signal. Codex's false-freshness risk applies to digests too — they must declare `Incomplete:` for any input that was truncated.
4. **Claude Code native memory is the existential variable.** If Anthropic ships a six-layer memory protocol in Claude Code 3.x, Cortex-as-standalone-product loses its reason. Cortex-as-spec survives by being adopted first. This is a distribution race, not a features race.
5. **Three-tool composition requires adoption of three tools.** A user who only wants Cortex gets most of the value; the triad is where the loop closes. If Touchstone and Sentinel don't gain adoption, Cortex works but underperforms its promise.
6. **The trigger set is enforced by convention, not code.** A tired human with edit access can violate append-only. The Touchstone pre-commit hook is the enforcement mechanism; without Touchstone, invariants are advisory.

---

## 11. Decisions still to confirm

Two items carried from round 1, surfaced here for explicit sign-off before promotion to `README.md`:

1. **Naming "the Cortex Protocol."** This draft promotes the trigger set from implementation detail to product centerpiece. OK?
2. **Two read-surfaces implies v0.2.0.** The manifest + budget concept is not in SPEC.md v0.1.0. If it lands, the spec bumps minor. OK?

---

## 12. What this draft is not yet

- **No Protocol spec file.** §§ 3.1 and 4 sketch the trigger set; the actual `.cortex/protocol.md` has to be written before implementation starts. That's the Phase B precursor.
- **No commitment on trigger enforcement.** Who validates "the agent followed the Protocol on this session"? A CLI post-check? A Touchstone hook? A Sentinel report? Spec-level decision, not drafted here.
- **No digest template.** The monthly-digest format needs a concrete schema so regeneration is deterministic.
- **No cross-project story.** Shared Doctrine between projects is a v1 target and intentionally absent here.

---

## 13. Next

- [ ] Send v2 to `codex exec -s read-only` and `gemini --approval-mode plan` for round-2 critique with the pushback prompt updated to challenge the *Protocol*, the *promotion queue*, the *false-freshness guards*, the *consolidation mechanics*, and the *composition seams* specifically.
- [ ] Fold round-2 critiques into v3 (or decide v2 passes).
- [ ] Once v2 or v3 passes, promote:
  - Trimmed §§ 1, 2, 3, 5.1 → `README.md` (public vision).
  - § 3.1 trigger set → `.cortex/protocol.md` (new file, Phase B precursor).
  - § 4 invariants → amendment to SPEC.md §§ 2 and 5.
  - § 6 retention + consolidation → new SPEC.md section, bumps to v0.2.0.
  - § 9 "explicitly not" → candidate Doctrine 0004 on scope boundaries.
  - Sections 10–11 (risks, open decisions) → Journal entry linked from README.
