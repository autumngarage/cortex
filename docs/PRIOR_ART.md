# Prior Art Informing the Cortex Spec

> The spec's design rules are not original. Each comes from a prior tradition that solved part of the problem. This doc cites the sources, briefly names what Cortex borrows, and calls out what remains novel.

---

## 1. Sigint's manual practice (direct prior art)

Internal evidence, `~/Repos/sigint`. Over ~6 months, the author evolved five document types by hand:

- **`INVESTMENT_THESIS.md`** — stable doctrine (why the project exists, kill criteria)
- **`SYSTEM_ARCHITECTURE.md`** — structural map (cluster layout, package boundaries)
- **`NEXT_PHASE.md`** — volatile state (daily-updated metrics, P0–P4 priorities, shipped history)
- **`*_PLAN.md` set** (`ANTI_CHASE_PLAN`, `FIX_DATAFLOW_GAPS_PLAN`, `NEW_COLLECTORS_PLAN`) — active efforts with per-PR lifecycle
- **`COLLECTOR_MIGRATION.md`, `CODEX_AUTOFIX_DISABLED.md`** — journal entries (completed-with-postmortem, policy decisions)
- **`RESEARCH_API.md`, `API_INTEGRATION.md`** — procedures (interfaces, checklists)

Observed pains drove Cortex's cross-layer rules:
- **Premature-completion declarations** (`COLLECTOR_MIGRATION.md` was declared complete Apr 5; tests weren't running the real code; fix landed Apr 7 with an AST guardrail). → **Plans must define measurable success criteria.**
- **Silent data-flow failures hidden by stale aggregators** (`FIX_DATAFLOW_GAPS_PLAN`: 4 days of dead resolution pipeline while `/sig-status` reported normal). → **Regenerated layers carry `Generated:` headers and source lists; staleness is surfaced.**
- **Deferrals scattered with no consolidated queue.** → **Deferred items must resolve to another plan or a journal entry in the same commit.**
- **Lessons buried in CLAUDE.md rather than surfaced near risky code.** → **Promotion pipeline (Journal → Doctrine) with bidirectional links.**
- **Plans didn't link to the metric they claim to fix.** → **Plans cite grounding (Doctrine entry, State priority, or Journal item).**

---

## 2. Engineering decision-record and design-doc culture

### Architecture Decision Records (Nygard, 2011)
- [Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
- [adr.github.io](https://adr.github.io/)

**Borrowed:** the five canonical sections (Context, Decision, Consequences, Status), and critically, the **append-only, never-delete rule** — a superseded ADR stays in the repo marked "Superseded by ADR-XYZ." Preserved reasoning prevents silent reversal. Cortex's Doctrine layer inherits this directly.

### RFC / design-doc culture (Google, Amazon, Stripe)
- [Pragmatic Engineer: RFCs and Design Docs](https://blog.pragmaticengineer.com/rfcs-and-design-docs/)

**Borrowed:** non-goals as a first-class section in Plans; the trigger is "architecturally significant" (borrowed into Doctrine's trigger contract).

### Postmortems (Google SRE)
- [SRE Book: Postmortem Culture](https://sre.google/sre-book/postmortem-culture/)

**Borrowed:** objective triggers + subjective escape hatch; blamelessness via naming roles not people; canonical sections (summary, impact, timeline, action items, what-went-well / what-went-poorly). Cortex's Journal entries of type `incident` follow this shape.

### Diataxis framework (Procida)
- [diataxis.fr](https://diataxis.fr)

**Borrowed:** the "one authoring mode per document" discipline. Mixing modes (tutorial + reference + explanation in one doc) makes all of them worse. Each Cortex layer has a single mode:
- Doctrine = Explanation
- Procedures = How-to
- Map / State = Reference
- Plans = Memex trail (outside Diataxis; see below)
- Journal = timestamped facts (outside Diataxis)

**Not borrowed:** Diataxis has no time axis. Plans and Journal needed other sources.

---

## 3. Personal Knowledge Management

### Zettelkasten (Luhmann via Ahrens)
- [Atomicity guide](https://zettelkasten.de/atomicity/guide/)
- [Ahrens summary](https://www.ernestchiang.com/en/posts/2025/sonke-ahrens-how-to-take-smart-notes/)

**Borrowed:** atomicity (one idea per note → one decision per Doctrine or Journal entry); **promotion pipeline** (Ahrens: fleeting → literature → permanent → project). Cortex uses a simpler version: Journal → Doctrine is promotion; Plans → Procedures is promotion. Promotion is a deliberate act with bidirectional links.

**Not borrowed:** "let structure emerge from links." That works for humans with context; it fails for agents and at scale. Cortex uses typed links (`supersedes`, `implements`, `blocked-by`, `derives-from`, `verifies`) instead of free-form association.

### Memex (Vannevar Bush, 1945)
- [As We May Think (MIT PDF)](https://web.mit.edu/sts.035/www/PDFs/think.pdf)
- [Memex on Wikipedia](https://en.wikipedia.org/wiki/Memex)

**Borrowed:** named associative trails. A Cortex Plan is exactly this — a named, structured path through Doctrine/State entries toward a concrete completion. The trail is the artifact, not the individual links.

### Obsidian / Logseq / Roam (contemporary)

**Failure mode observed in practice:** link rot and stale "current" notes. Vaults become graveyards because nothing distinguishes "this used to be true" from "this is true." Cortex's regenerating Map/State layers are the mitigation — the "current" view is always derived and timestamped, never drifted-from-source by accretion.

---

## 4. LLM agent memory research

### MemGPT (Packer et al. 2023)
- [arXiv:2310.08560](https://arxiv.org/abs/2310.08560)

**Borrowed:** the virtual-memory framing — main context (hot, paged in) vs. external context (cold, on disk). For Cortex: State is always hot; Doctrine and Plans are paged in on relevance; Journal and Procedures are retrieved on demand. An agent reading `.cortex/` at session start should load State + top Doctrine + active Plans, not everything.

### Voyager (Wang et al. 2023)
- [arXiv:2305.16291](https://arxiv.org/abs/2305.16291)

**Borrowed:** the verified skill library. A skill is a named, executable, retrievable routine — exactly Procedures in Cortex. Grown incrementally, retrieved by similarity, promoted only when verified.

### Agent memory surveys (2024-2025)
- [Memory Mechanism of LLM Agents (ACM TOIS)](https://dl.acm.org/doi/10.1145/3748302)
- [From Storage to Experience](https://www.preprints.org/manuscript/202601.0618)

**Borrowed:** the three-to-four-type taxonomy (episodic, semantic, procedural, + working/core) direct from cognitive science. Cortex layers map cleanly:
- Doctrine = semantic
- Procedures = procedural
- Map / State = working/core
- Plans (active) = episodic (active)
- Journal = episodic (archived)

This taxonomy is what makes the retrieval contracts in [SPEC.md](../SPEC.md) coherent.

---

## 5. Systems design principles

### Write-ahead log + checkpoint (ext4 JBD2, PostgreSQL)
- [Journaling file system](https://en.wikipedia.org/wiki/Journaling_file_system)

**Borrowed directly:** Journal is WAL, Map (and State) are checkpoints. Intent is journaled before mutation; periodic checkpoint folds committed intent into the main state; crash recovery replays the tail. The parallel to Cortex is exact: Journal entries are individually valid even if Map is missing; Map regenerates from recent Journal + Doctrine + code.

### Content-addressable storage (git object model)
- [Git Internals: Objects](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)

**Borrowed:** immutable content, mutable refs. Doctrine / Journal entries are immutable content (hashable, deduplicated conceptually); Map and State are refs (named pointers into a specific generation). Regeneration is cheap because prior versions never get destroyed — git already keeps them.

### Rich Hickey, "The Value of Values"
- [Transcript](https://github.com/matthiasn/talk-transcripts/blob/master/Hickey_Rich/ValueOfValues.md)

**Borrowed:** a fact is a value-plus-time, never a place. Journal entries are values (timestamped facts that never mutate); Map / State are places (mutable views derived from values). Don't let the two get confused.

### CRDTs
- [Wikipedia](https://en.wikipedia.org/wiki/Conflict-free_replicated_data_type)

**Borrowed narrowly:** monotonic append semantics. If two agents append to the Journal concurrently, convergence is trivial because appends commute. Other layers assume single-writer for now; multi-writer CRDT semantics are deferred until needed.

---

## 6. What's novel

Each tradition solved part of the problem; Cortex's distinctive contribution is **the regenerating Current-State layer**. None of the prior traditions provided this because none had cheap machine regeneration available:

- ADRs / RFCs don't have a "what's true right now" layer at all.
- Zettelkasten / Obsidian have *a* current layer but it drifts from source because regeneration is too expensive for humans.
- WAL + checkpoint has regeneration but only over its own append-log, not over a heterogeneous source (code + git + journals).
- Agent memory research has the retrieval shape but assumes the content already exists.

The insight: **an agent can regenerate the Map weekly; a human could not.** That flips the stale-docs problem. The Map should be *expected* to be regenerated on a schedule or on significant Journal churn, not hand-maintained. That's the novel piece — and it's only practical because LLMs made cheap regeneration from primary sources possible.

---

## 7. Summary table

| Cortex layer | Mechanical source | Authoring mode | Agent memory type |
|---|---|---|---|
| Doctrine | ADR immutable-with-supersede | Explanation (Diataxis) | Semantic |
| Map | WAL checkpoint (git refs) | Reference (Diataxis) | Working |
| State | WAL checkpoint | Reference (Diataxis) | Working/core |
| Plans | Memex named trails | — (trail, not doc-type) | Episodic (active) |
| Journal | WAL (ext4/Postgres) + ADR append-only | — (timestamped facts) | Episodic (archived) |
| Procedures | Voyager skill library | How-to (Diataxis) | Procedural |

No single tradition gave us all six. The spec is a deliberate assembly.
