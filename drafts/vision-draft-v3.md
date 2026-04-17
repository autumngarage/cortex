# Cortex — Vision (draft v3)

> **⚠ ARCHIVED — PROMOTED 2026-04-17.** This draft was the source material for the canonical artifacts: [README.md](../README.md) (§§ 0, 1, 3.1, 3.3, 5, 6, 7), [SPEC.md](../SPEC.md) v0.2.0-dev (§§ 3.4, 4.5, 4.7, 4.8, 4.9, 5, 9), [`.cortex/protocol.md`](../.cortex/protocol.md) (whole file, from §§ 3.1 + 4), and [`.cortex/doctrine/0004-scope-boundaries.md`](../.cortex/doctrine/0004-scope-boundaries.md) (from § 9). Strategic content not promoted into the spec — the competitive matrix (§ 8) and the Claude Code existential-risk framing (§ 10.4) — is preserved in [`journal/2026-04-17-competitive-positioning-and-claude-code-risk.md`](../.cortex/journal/2026-04-17-competitive-positioning-and-claude-code-risk.md). Full provenance of the promotion is in [`journal/2026-04-17-vision-v3-promoted.md`](../.cortex/journal/2026-04-17-vision-v3-promoted.md). Retained here as the reasoning trail; do not treat as current — canonical artifacts are authoritative.

---

> **Status:** working draft. v1 and v2 preserved at `vision-draft.md` and `vision-draft-v2.md`. This v3 folds in Codex's round-2 critique (5 load-bearing issues), the user's Touchstone-as-universal-baseline clarification, and the one-command UX simplification. Gemini round 2 was unavailable (Google capacity exhaustion); if Gemini capacity returns, v3 can take another critique pass before promotion.
>
> **Date:** 2026-04-17
> **Spec implication:** v0.2.0 bump required. Changes: new § on the Cortex Protocol (with two-tier trigger model); revised § 7 ("does not synthesize without permission" sharpens rather than reverses — Protocol-triggered writes are explicitly invoked); new rules for promotion-queue operation, digest audit invariants, multi-writer Plans, and single-authority-rule for reads.

---

## 0. The two stories that ground this

### The crash

Earlier today, a Claude Code session doing multi-hour vision-sharpening work crashed. Every research finding, every critique from peer agents, every branch of the discussion: gone. Git clean. Memory system empty. The next session's honest answer to *"where were we?"* was *"I don't know."*

### The retreat

On 2025-11-22, Cursor shipped version 2.1 and **removed the Memories feature** they had introduced just six months earlier. Custom Modes too. Official rationale: *"intentionally removed."* Weeks before the removal, a community member open-sourced [`.brain/`](https://forum.cursor.com/t/persistent-memory-for-cursor-that-survives-every-session-brain-folder-approach/157488) — a three-file git-committed markdown memory structure that *"works across 12 agents from the same memory. Cursor in the morning, Claude Code in the afternoon — both read the same `.brain/`."* The [master feature-request thread](https://forum.cursor.com/t/persistent-intelligent-project-memory/39109) for persistent project memory has been open since January 2025 and is still unresolved today.

These two stories define the problem:

> Projects accumulate reasoning that has nowhere to live. Chat context evaporates. Agent memory is machine-local. The vendor closest to solving it shipped, then retreated. The community is hand-building the same three files in every repo. The pattern is clear; the spec is missing.

Cortex's bet: turn the emergent `.brain/` convention into a spec with teeth. A file-format protocol any agent follows, any team audits, and the failure modes that pushed Cursor to retreat — permissionless auto-writes, cloud-sidecar privacy tax, machine-local silos — are addressed by design.

---

## 1. What Cortex is, in one paragraph

Cortex is **a protocol for agents to continuously journal what's happening on a project, and for humans to promote what matters**. It defines a `.cortex/` directory with six layers of Markdown files, a two-tier trigger set that tells any agent *when* to write (machine-observable events + advisory heuristics), a promotion pipeline that graduates recurring lessons into immutable Doctrine, and a two-surface read model (human prose + token-budgeted agent manifest) that keeps context lean as the corpus grows. Writers are multiple — any Claude Code session, any Sentinel cycle, any Touchstone pre-merge hook, any human. The filesystem is the transport. **The Protocol is the product.**

---

## 2. What we keep (and from whom)

Nothing about the individual layers is novel. The list, attributed:

- **Letta MemFS:** pinned `system/` always in context; YAML frontmatter; read-only flags; `/doctor` subagent; reflection subagents; git as the transport. Closest existing analogue.
- **Cursor Memories (pre-retreat):** sidecar-observer pattern — a second model watches the primary conversation and captures.
- **Cursor `.brain/` community:** three-file primitive (MEMORY / SESSION / LOG); git-committed; cross-tool by design.
- **Claude Code auto-memory:** `MEMORY.md` as index with startup-load cap; topic files on-demand; `@path` imports.
- **AGENTS.md:** single-convention file at root; nested discovery; multi-vendor-neutral. The precedent Cortex follows for its entry point.
- **MemGPT / Letta papers:** virtual-memory paging (hot / warm / cold); reflection for consolidation.
- **Mem0:** entity linking for dedup; extraction as a first-class phase.
- **ADRs (Nygard):** immutable-with-supersede; context-decision-consequences structure.
- **SRE postmortems:** canonical sections for incident-type Journal entries.
- **Diataxis:** one authoring mode per document.
- **WAL + checkpoint:** append-only log plus regenerated projection.
- **sigint manual practice:** empirical bars not calendar dates; falsifiability sections; staged plans with rollback gates; `file::§section:lines` citation format.

---

## 3. What Cortex actually contributes (honest framing)

Round 2 of the critique pushed back on v2's claim of *"five jumps ahead no existing tool makes together."* Individually, most of those are known patterns — read-surface paging is MemGPT-era; reflection loops exist in agent frameworks; promotion workflows exist in knowledge-base curation. The defensible positioning is narrower and stronger:

> **Cortex contributes enforceable project-memory discipline across multiple independent writers** — humans, autonomous agents, and peer tools — **in a format that's auditable, git-native, and vendor-neutral.**

Not novelty of parts. Enforceable discipline of the combination.

Three moves do the work:

### 3.1 The Protocol: two-tier triggers, not one vague list

Round 2 correctly flagged that v2's trigger list mixed machine-observable events with semantic judgments. *"Before a non-trivial change (>50 lines)"* isn't operational — the agent doesn't know diff size before acting. *"On 'we decided' phrasing"* detects wording, not decisions. A single list conflates two different kinds of rule.

v3 splits the Protocol into two tiers:

**Tier 1 — Machine-observable triggers.** Deterministic, verifiable after the fact, enforceable by tooling:

- Diff touches `.cortex/doctrine/`, `.cortex/plans/`, or `principles/`.
- Test command failed after succeeding earlier in the session.
- `Plan.status` field changed.
- File deleted (with the deletion larger than a threshold).
- Dependency manifest changed (package.json, pyproject.toml, Gemfile, etc.).
- Sentinel cycle ended.
- Touchstone pre-merge fired on an architecturally significant diff (heuristic: touches `principles/`, `.cortex/doctrine/`, or SPEC.md).
- Commit with a message matching patterns like `fix: ... regression`, `refactor: ...`, `feat: ... introduces ...`.

These are not judgment calls. A post-session audit can verify: *"Trigger X fired on event Y. Was a Journal entry written?"* If not, non-compliance. Tooling enforcement is possible because the triggers are machine-detectable.

**Tier 2 — Advisory heuristics.** Explicitly labeled as judgment. Agents are asked to journal when they notice these, but non-compliance isn't auditable:

- Decision phrased as *"we decided,"* *"let's,"* *"we chose X over Y."*
- A failed attempt teaching something non-obvious.
- Surprise about existing code ("wait, why is this…").
- User phrasings like *"remember this"* or *"don't forget."*

The separation is the load-bearing move. Tier 1 is the enforceable Protocol; Tier 2 is the agent-side good-citizen behavior. Together they cover the "journal when it matters" claim without pretending all triggers are equally precise.

The trigger set lives in `.cortex/protocol.md`, imported into `AGENTS.md`. Project-customizable. An agent asked *"why did you write this entry?"* cites the trigger. A post-session `cortex doctor --audit` pass verifies Tier 1 compliance.

**This also reconciles with SPEC.md § 7's rule that agents cannot "synthesize without permission."** The rule sharpens, not reverses: *an agent writing to `.cortex/` must either be explicitly invoked by a human, or acting on a declared Tier 1 Protocol trigger.* Silent, discretionary background writes (Letta-style, Claude Code auto-memory-style) remain forbidden. The Protocol IS the permission, because it's a visible, inspectable, versioned contract — not agent judgment.

### 3.2 Promotion as a managed queue, not a second inbox

Round 2 was right that v2's *"one keystroke"* hand-waved the hard part. Real promotion is editorial judgment, not a checkbox. v3 ships an operational promotion queue with explicit state, not just a list:

**Candidate states:**
- `proposed` — fresh candidate, not yet reviewed.
- `approved` — promotion happens on next `cortex` invocation.
- `not-yet` — candidate is real but needs more evidence; re-surfaces in 30d with any new supporting Journal entries attached.
- `duplicate-of:doctrine/0003` — existing Doctrine already covers this; candidate is archived with the pointer.
- `skip-forever` — explicitly rejected; won't re-surface.
- `needs-more-evidence` — same as `not-yet` but with a required-source-count threshold (e.g., "re-propose when 5+ entries cite this pattern").

**Queue health rules:**
- **WIP limit** (default: 10 candidates in `proposed` state). Cortex stops generating new candidates when the limit is hit — forces the human to clear the queue before more come in.
- **Candidate aging.** A candidate in `proposed` for >14 days auto-transitions to `stale-proposed` and surfaces in `cortex doctor` as a warning. This prevents silent queue decay.
- **Promotion debt in the manifest.** The agent manifest declares `Promotion-queue: <n> candidates, <k> stale`. The agent sees the backlog at session start.
- **Review depth is not uniform.** Some candidates are trivial ("these 3 entries say the same thing"). Some are editorial ("does this generalize? supersede?"). The queue marks each candidate's `review-complexity: trivial|editorial` based on recurrence count and supersede signal. Trivial ones get bulk-approve; editorial ones get individual review.

The queue is not a second inbox because you can't ignore it — every `cortex` invocation surfaces it, and stale candidates block new ones.

### 3.3 Generated layers declare corpus, not just sources

Round 2 caught that `Incomplete:` covers only availability failures, not correctness failures — salience errors, collapsed disagreement, wrong causal framing, digest drift. v3 strengthens the metadata contract for generated layers (Map, State, digests):

Every generated file declares seven fields:

- `Generated:` — ISO8601 timestamp.
- `Generator:` — what produced this (`cortex refresh-state`, `cortex digest monthly`, etc.) and its version.
- `Sources:` — list of inputs read, with identifiers (commit SHAs, file paths, date ranges).
- `Corpus:` — total input count (e.g., `Corpus: 47 Journal entries for 2026-03`). Answers *"what did you claim to cover?"*
- `Omitted:` — inputs intentionally excluded, with reason. (`Omitted: journal/2026-03-15-wip-debugging — marked noisy`.)
- `Incomplete:` — inputs attempted but missing, truncated, or unreachable. Non-empty = best-effort.
- `Conflicts-preserved:` — conflicting claims in sources, listed so the summary doesn't silently pick one side.

Digests get two additional rules:

- **Depth cap.** A digest of digests is allowed at most one level (quarterly digest can cite monthly digests). A digest-of-digest-of-digest is forbidden; quarterly digests must also cite at least N raw Journal entries. This bounds drift under repeated consolidation.
- **Audit sampling.** `cortex doctor --audit-digests` picks N random claims in a digest and verifies they can be traced back to at least one source entry. Failures surface as warnings.

`Generated:` alone is a trust invitation. `Generated:` + `Sources:` + `Corpus:` + `Omitted:` + `Incomplete:` + `Conflicts-preserved:` is a verifiable audit trail.

---

## 4. The Cortex Protocol (spec sketch)

Three components, declared in `.cortex/protocol.md` and imported into `AGENTS.md`:

**4.1 Read on session start.** Load `cortex manifest --budget <N>`; treat output as grounding context. The manifest is a slice, not the whole store. State (always), top-K Doctrine by semantic relevance (3–5k), active Plans (2–3k), Journal last-72h + latest digest (1–2k), promotion queue depth (~100t). Graceful degradation to State-only at 32k.

**4.2 Write on triggers.** Tier 1 (§ 3.1) is enforceable. Tier 2 is advisory. Each trigger has a template in `.cortex/templates/` specifying required frontmatter and prose sections. The agent fills the template from conversation context.

**4.3 Respect invariants.** Three enforceable rules:

1. **Journal is append-only.** Never edit an existing Journal entry in place. New entry per event.
2. **Doctrine is immutable.** Changes happen by writing a new entry with `supersedes: 0003` frontmatter. The old entry stays. Promotion queue surfaces stale Doctrine candidates.
3. **Generated layers declare the seven metadata fields** (§ 3.3). Missing fields fail `cortex doctor` validation.

These three invariants are checked by `cortex doctor`. Touchstone's pre-push hook runs `cortex doctor --strict` before push — the enforcement point for projects that have Touchstone installed. Solo Cortex without Touchstone: invariants are advisory (see § 10).

---

## 5. UX — one command

Running `cortex` is the entire interface. No subcommands to remember.

```
$ cortex
Cortex — your-project   spec v0.2.0-dev   state: fresh (regenerated 2h ago)

▸ 7 Journal entries since last check
▸ 3 promotion candidates (1 stale, 2 proposed)
▸ March 2026 digest overdue by 8 days

 [1] j-2026-04-17-auth-retry       [trivial]     3 entries on retry backoff
     → Promote to doctrine/0005?  [y/n/view/defer/skip]:

 [2] j-2026-04-16-test-scoping      [editorial]  New pattern; no existing Doctrine covers
     → Promote to doctrine/0006?  [y/n/view/defer/skip]:

 [3] j-2026-03-22-flaky-ci          [stale, 17d] Re-proposed after 3 new entries
     → Promote to doctrine/0007?  [y/n/view/defer/skip]:

Generate March 2026 digest now?  [y/n]:

Anything else? (enter to exit, or type a request)
```

Everything is surfaced at every invocation. You can't miss the queue; you can't miss an overdue digest; you can't miss staleness in State. Power users can pass flags (`cortex --status-only`, `cortex --promote j-xxx`, `cortex --regen state`, `cortex --audit`) for automation, but the primary surface is `cortex`.

### 5.1 Solo Cortex (no Sentinel, no Touchstone)

You run `claude`. `AGENTS.md` imports `@.cortex/state.md` and the Protocol. Claude loads the manifest (~5k tokens) and is current.

During work, the Protocol tells Claude when to journal. You never ask. Tier 1 triggers produce Journal entries with audit trails; Tier 2 triggers produce agent-judged entries labeled as advisory. You see them as tool-use lines in the transcript.

At the end of the day, you run `cortex`. The interactive flow surfaces new entries, promotion candidates, overdue digests. You spend 30 seconds clearing the queue and move on.

After a crash, next session loads State + recent Journal. The agent picks up mid-sentence. You type *"where were we?"* and it answers with citations.

**Honest caveat:** solo Cortex invariants are enforced only by `cortex doctor` run on-demand. A determined human with edit access can `sed` over a Journal entry. The spec is honest about this: full invariant enforcement requires Touchstone's pre-push hook. Solo Cortex is *"good notes with conventions,"* not *"enforced institutional memory."*

### 5.2 With Touchstone + Sentinel (the triad)

**Touchstone** sits before every merge. Pre-push runs `cortex doctor --strict`. Invariants are now code-enforced. On architecturally significant diffs, Touchstone drafts a Doctrine candidate inline with the PR; you accept or skip when the decision is fresh.

**Sentinel** runs on its cadence. It reads Cortex for context (Doctrine + active Plans + recent Journal + digests) rather than re-deriving from `CLAUDE.md`. Each cycle-end writes a Journal entry summarizing outcome. Next cycle reads the previous cycle's Journal. The reflective store is the continuity.

**Cortex itself** runs no cycle. It's ambient. Touchstone feeds it at merge; Sentinel feeds it at cycle-end; humans feed it at `claude` sessions. `cortex` (the one command) is the human reading and curation interface.

### 5.3 The feel

The project has an accurate, living memory that nobody had to maintain. You run `cortex` daily the way you check test results — read, accept, defer, move on. Over months, Doctrine grows slowly by promotion. Journal grows quickly by Protocol. State stays current by regeneration. Digests replace raw entries in the default read surface as time passes.

You never again open a blank chat and wonder where you left off. The next session starts where the last one ended, even across crashes, even across team members, even across agent tools.

---

## 6. Scale: consolidate and archive, never delete

If Cortex doesn't answer this, it becomes the Obsidian graveyard in six months.

**Principle:** Cortex is append-only at write and **tiered at read**. Nothing is ever lost — everything stays in git. Pruning means moving entries from hot → warm → cold → digest-represented, never removing them.

### 6.1 Tiered retention

| Layer | Mechanic |
|---|---|
| **Doctrine** | Never archived. Superseded entries stay with `superseded-by:` pointer; dropped from default load. Top-K by semantic relevance at session start. |
| **Journal** | Hot (0–30d) → Warm (30–365d) → Cold (>365d, auto-moved to `journal/archive/<year>/`). Default load is hot + monthly digests. |
| **Plans** | `active` → hot. `shipped` / `cancelled` → auto-moved to `plans/archive/` after 30d. |
| **Map** | Always regenerated. Old versions in git history. |
| **State** | Always regenerated. "Shipped recently" section auto-ages at 90d. |
| **Procedures** | `cortex doctor` flags dead code references; human moves to `procedures/archive/`. |

### 6.2 Consolidation with audit invariants

Monthly: Cortex proposes a **Journal digest** — a summary of the period's key decisions, with citations to originals. Human approves in the `cortex` flow. Digest lives in `journal/` as type `digest`. Originals stay in warm/cold.

Digests obey the § 3.3 metadata contract — Sources + Corpus + Omitted + Incomplete + Conflicts-preserved — plus the digest-specific depth cap and `cortex doctor --audit-digests` random sampling. A digest that summarizes 47 entries and can't be verified against any of them fails audit.

### 6.3 Scale behavior (the claim)

- Year 1: ~1k Journal, ~20 Doctrine. Manifest: ~5k tokens.
- Year 3: ~4k Journal, ~80 Doctrine, monthly digests replace raw warm entries. Manifest: ~5–7k tokens.
- Year 10: ~15k Journal, ~300 Doctrine, quarterly digests (depth ≤1) form the narrative arc. Manifest: ~7k tokens. Archive queries retrieve old context on-demand.

### 6.4 Failure modes the spec prevents

1. **Unbounded hot load.** Hard cap on manifest; semantic retrieval beyond.
2. **Unreviewed promotion candidates piling up.** WIP limit + candidate aging + promotion debt in manifest (§ 3.2).
3. **Silent digest drift.** Depth cap + audit sampling + Corpus/Omitted/Conflicts-preserved invariants (§ 3.3).
4. **Consolidation skipped entirely.** Missing monthly digests surface in `cortex` as overdue.

---

## 7. Composition with Touchstone and Sentinel

Cortex is the third tool in autumngarage. The framing sharpens with the user's Touchstone clarification: Touchstone is the **universal-baseline** layer (one source, many projects); Cortex is the **project-local** layer (one project, many writers). Clean authority boundary, no overlap.

| Tool | Scope | Authority |
|---|---|---|
| **Touchstone** | Universal (distributed via `touchstone sync`) | *Originates* standards. Principles, hooks, scripts. Prescriptive. |
| **Sentinel** | Project-local | *Executes* and reports. Runs cycles, ships PRs, writes run journals. Descriptive. |
| **Cortex** | Project-local | *Remembers and reasons*. Captures decisions, consolidates lessons, serves context. Reflective. |

Each tool installs alone and composes via file contract. Seams:

- **Touchstone → Cortex:** pre-merge hook drafts Doctrine candidates; runs `cortex doctor --strict` on push (invariant enforcement). Optional.
- **Sentinel → Cortex:** end-of-cycle writes Journal entries linking `.sentinel/runs/*`. Optional.
- **Cortex → Sentinel:** Sentinel reads `.cortex/doctrine/` + active Plans as lens context.
- **Cortex → Touchstone:** `cortex doctor` verifies that Cortex Doctrine *cites* Touchstone principles where applicable. Touchstone principles are the universal baseline; Doctrine grounds in them without duplicating. `cortex doctor` flags a Doctrine entry that restates a Touchstone principle without `grounds-in: touchstone/principles/engineering-principles.md#no-band-aids` or similar.

### 7.1 The multi-writer Plan collision (new)

Plans are the only mutable layer. Multiple writers (Sentinel, human, Claude session) could create or update Plans for the same goal. v3 adds:

- **`Author:` frontmatter field** on every Plan (`Author: human`, `Author: sentinel-coder-1`, `Author: claude-session-2026-04-17T09:00`).
- **`Goal-hash:` field** — a normalized hash of the Plan's stated goal. If two Plans share a Goal-hash, `cortex doctor` surfaces the collision; resolution is a human decision (merge, supersede, or distinct).
- **`Updated-by:` history** — append-only log of writers who touched the Plan, with timestamps.

Cortex doesn't prevent collisions; it makes them visible.

### 7.2 Single authority rule for reads (new)

To prevent the *"CLAUDE.md drift returns under AGENTS.md branding"* failure Codex flagged:

> **Root agent files (AGENTS.md, CLAUDE.md) may route to `.cortex/` but must not duplicate Cortex claims without a `grounds-in:` citation. `cortex doctor` detects and flags drift — content in AGENTS.md that restates a Doctrine entry without linking back.**

Agents follow the chain: `AGENTS.md` → `@.cortex/state.md` → the Protocol → the manifest. One authoritative source; one path to reach it; drift is detectable.

---

## 8. Honest comparison

| Adjacent | What they have | Cortex's defensible difference | Risk |
|---|---|---|---|
| **Letta MemFS** | Git-backed MD + frontmatter + pinned `system/` + read-only + `/doctor` + reflection subagents + git sync. **Closest analogue.** | Tier-1 machine-observable triggers (not agent judgment). Promotion queue with operational rules. Multi-writer invariants across independent tools. Cross-tool by spec, not per-agent. | **Medium-high.** Letta can add trigger discipline. Defense: spec adoption first. |
| **Claude Code `CLAUDE.md` + auto-memory** | Hand-written CLAUDE.md + agent-discretion `memory/` + `@path` imports. Built into the IDE. | Team-shared via git, not machine-local. Protocol-triggered, not discretionary. Six layers with enforceable invariants. | **Existential.** Anthropic could ship multi-layer native memory. Defense: Cortex as spec Anthropic converges on. |
| **Cursor Memories (retreated 2025-11-22)** | Auto-extract via sidecar; project-scoped; required disabling privacy mode. | Local-markdown, privacy-trivial, cross-tool, Protocol-triggered not extraction-based, human-review queue. | **Low.** Cursor retreated; design space is open. |
| **Cursor `.brain/` community** | 3-file git-committed cross-tool markdown. **Emergent convention.** | Six layers; Protocol; invariants; promotion queue; Map/State regeneration with audit invariants. | **Low — complementary.** The discipline the `.brain/` experiments are missing. |
| **Mem0** | Production memory-as-a-service; LLM extraction; entity linking. | File-first, git-native, human-auditable, team-shared, no cloud dep. | **Low.** Different consumer. |
| **Graphiti / Zep** | Bi-temporal graph; opaque; retrieval-latency-optimized. | Markdown + git + audit trail. | **Low.** Different audience. |
| **LangGraph checkpoints** | State-as-computation snapshots. | Memory-as-reasoning, not state. | **Low.** Orthogonal. |
| **LangSmith / Langfuse** | Observability traces. | Traces are raw material, not memory. Cortex could ingest. | **Low.** Complementary. |
| **AGENTS.md** | Single-file project instructions; multi-vendor standard. | `.cortex/` is memory, not instructions. AGENTS.md imports it. | **Low — complementary.** Every Cortex project has both. |

**The two that matter.** Letta is close enough that our defense is the Tier-1 Protocol + promotion queue + cross-tool multi-writer invariants — not folder structure. Claude Code is the existential variable — if Anthropic ships multi-layer native memory, Cortex-as-product loses; Cortex-as-spec survives if adopted first.

---

## 9. What Cortex is explicitly not

- **Not a vector store.** Markdown + git + grep. Semantic retrieval in the manifest is read-side, not storage.
- **Not a database.** `.cortex/.index.json` is a cache.
- **Not a knowledge graph.** Cross-references between files, no graph.
- **Not a portfolio tool.** One project per `.cortex/`.
- **Not an agent framework.** No "agents" concept. Writers follow the Protocol. Any agent framework can read and write.
- **Not a replacement for `AGENTS.md` or `CLAUDE.md`.** Those are the entry; `.cortex/` is the memory.
- **Not cloud-hosted.** Local files, git, nothing else.

---

## 10. Risks (honest)

1. **Tier 1 trigger set incomplete or wrong on day one.** Phase B dogfood on Sentinel and sigint is where triggers tighten. Expect at least one minor spec bump from that.
2. **Promotion queue still overflows despite WIP limit + aging.** If the corpus generates candidates faster than the team promotes, Doctrine freezes while Journal grows. Mitigation layer 1: WIP limit. Layer 2: `cortex doctor` surfaces promotion debt. Layer 3: the triad provides Touchstone pre-merge drafting so promotion happens at commit time, not as a later chore. Still possible to fail.
3. **Digest quality can't be guaranteed.** § 3.3 + § 6.2 add audit invariants, but LLM synthesis can omit a crucial lesson in ways no audit catches. The hedge: monthly cadence keeps drift bounded; originals are never deleted; a missed lesson in a digest can still be found by searching the raw Journal.
4. **Claude Code native memory.** If Anthropic ships multi-layer memory in Claude Code 3.x, Cortex-as-product competes; Cortex-as-spec has to be adopted first.
5. **Solo Cortex is materially weaker than triad.** The "three tools compose" story depends on all three. Solo Cortex is *good notes with conventions*; triad is *enforced institutional memory*. § 5.1 and § 5.2 are honest about this distinction.
6. **Tier 1 enforcement requires tooling, not spec text.** Without Touchstone's pre-push running `cortex doctor --strict`, invariants are advisory. A project that installs only Cortex gets most of the value but not enforcement.
7. **Plans multi-writer collision is made visible, not prevented.** § 7.1 surfaces collisions; resolution is human. If the human ignores the collision warning, the project has two plans for the same goal and silent drift returns.

---

## 11. Decisions still to confirm

1. **"The Cortex Protocol"** — promoted to product centerpiece. Named as such. OK.
2. **v0.2.0 spec bump.** Required: two read-surfaces, seven-field metadata contract, promotion queue operational rules, single authority rule, multi-writer Plan fields, digest audit invariants. OK.
3. **SPEC.md § 7 sharpens, not reverses.** "Does not synthesize without permission" remains; Protocol-triggered writes ARE permission because the Protocol is a declared contract. OK.

---

## 12. What this draft is still missing

- **The actual `.cortex/protocol.md` file.** §§ 3.1, 4 sketch the trigger set; the real file with templates, frontmatter specs, and test fixtures has to be written before Phase B ships.
- **Digest format schema.** § 3.3 and § 6.2 specify invariants; the actual YAML schema for digest frontmatter is not yet written.
- **Goal-hash normalization.** § 7.1 introduces the concept; what "normalized goal" means (tokenization? embedding?) is not specified.
- **Cross-project Doctrine.** Out of scope for v0.2.0. Deferred to v1.
- **No commitment on cortex-doctor cadence.** Is it CI-only? Pre-commit? Periodic? Decision needed.

---

## 13. Next

- [ ] Retry Gemini round 2 when capacity returns (optional; v3 is more defensible than v2 and may not need it).
- [ ] If Gemini passes or is skipped: promote to README + Doctrine + SPEC.md amendments per § 11. Specifically:
  - Trimmed §§ 1, 2, 3, 5 → `README.md` (public vision).
  - § 3.1 trigger split + § 4 Protocol → new file `.cortex/protocol.md` (Phase B precursor).
  - § 3.3 seven-field metadata contract → amendment to SPEC.md §§ 3.2–3.3 (Map, State) and § 5 (file format conventions).
  - § 3.2 promotion queue rules → new SPEC.md section under § 4 (cross-layer rules).
  - § 6 retention + consolidation → new SPEC.md section.
  - § 7.1 multi-writer Plan fields → amendment to SPEC.md § 3.4 (Plans).
  - § 7.2 single authority rule → amendment to SPEC.md § 4 (cross-layer rules).
  - § 9 "explicitly not" → candidate Doctrine 0004 on scope boundaries.
  - §§ 10–11 risks + open decisions → Journal entry linked from README.
  - Bump SPEC.md version header to 0.2.0-dev; draft changelog entry.
