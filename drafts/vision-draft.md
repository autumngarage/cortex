# Cortex — Vision (draft)

> **⚠ ARCHIVED — SUPERSEDED 2026-04-17.** Superseded by draft v2 (`drafts/vision-draft-v2.md`), then v3 (`drafts/vision-draft-v3.md`), then by the canonical artifacts it drove: [README.md](../README.md), [SPEC.md](../SPEC.md) v0.2.0-dev, [`.cortex/protocol.md`](../.cortex/protocol.md), and [`.cortex/doctrine/0004-scope-boundaries.md`](../.cortex/doctrine/0004-scope-boundaries.md). Strategic content not carried into canonical files is preserved in [`journal/2026-04-17-competitive-positioning-and-claude-code-risk.md`](../.cortex/journal/2026-04-17-competitive-positioning-and-claude-code-risk.md). Retained here as reasoning trail; do not treat as current.

---

> **Status:** working draft, not the README. This file exists to be torn apart by `codex` and `gemini`, then folded back into `README.md` / Doctrine / Journal once it survives both critiques.
>
> **Date:** 2026-04-17

---

## 0. The incident that grounds this

Earlier today, a Claude Code session doing multi-hour vision-sharpening work for this project — deep research plus a working draft being critiqued by `codex` and `gemini` in parallel — crashed. Every word, every branch of the discussion, every piece of the critique: gone. `git status` clean on restart. Memory system empty. The next session's first real message was *"sorry we crashed where did we leave off"* — and there was no honest answer.

This is not a war story. It is the simplest possible statement of the problem Cortex exists to solve:

> **Projects accumulate reasoning that has nowhere to live. Chat context evaporates. Tool memory is process-local. Code answers *what*; git history answers *when*. Neither answers *why we chose this*, *what we tried that failed*, or *where we were in the middle of thinking*.**

Every serious software project ends up hand-rolling a set of documents that fill this gap — a thesis, an architecture map, a state doc, a few plans, migration postmortems, policy decisions, a runbook. The practice works, but it breaks in predictable ways: premature-completion declarations, silent staleness in aggregator docs, scattered deferrals that never get revisited, lessons buried in the wrong file.

Cortex's bet is that this practice can become a **spec** — a file-format protocol that names the layers, the write triggers, the authoring modes, and the staleness contract, with a reference CLI that reads, writes, and regenerates against it. The spec is the artifact; the CLI implements it.

---

## 1. What Cortex is, in one paragraph

Cortex defines a `.cortex/` directory per project containing six layers of Markdown files, each with a single authoring mode, a single write trigger, and a single retrieval contract. **Doctrine** is immutable ADR-style explanation (why this project exists, what we've decided, what would break our thesis). **Map** and **State** are *derived* — regenerated from code, git, metrics, and the Journal, carrying `Generated:` headers with source lists. **Plans** are mutable named efforts with measurable success criteria, grounded to Doctrine or State. **Journal** is an append-only write-ahead log of decisions, incidents, and lessons. **Procedures** are versioned how-tos and interface contracts. Humans, agents, and hooks from sibling tools all write through the same filesystem contract; no code imports, no database, no vector store, no provider abstraction.

---

## 2. What's actually novel

Each individual layer has prior art. ADRs own the immutable-with-supersede discipline for Doctrine (Nygard, 2011). SRE postmortems own the incident shape for Journal. Diataxis owns the authoring-mode-per-doc rule. Memex and Zettelkasten own the trails-between-ideas framing. WAL + checkpoint owns the append-log-plus-derived-view pattern from ext4 and Postgres. MemGPT / Letta own agent-readable Markdown memory with YAML frontmatter.

The novelty is not any single idea. It is the **combination**, and specifically these five load-bearing moves:

1. **Layered, not flat.** Six layers with explicit roles; each with a single authoring mode (Diataxis) and a single write trigger. Letta's MemFS, Claude Code's `memory/`, Obsidian vaults — all flat or two-tier. Flat systems drift; every document becomes a general-purpose dumping ground.

2. **Regenerated, not maintained.** Map and State are *computed from primary sources* on schedule, not hand-kept in sync with code. Every regenerated layer carries a `Generated:` header, a source list, and a freshness threshold. This flips the perennial problem from *"keep the docs in sync"* (lost cause) to *"were the docs regenerated recently enough?"* (decidable). No other prior-art system we found does this for a project-memory store.

3. **Human-first with explicit write gates.** Letta is agent-first and permissive — agents write anywhere. Cortex gates writes per layer: Doctrine is immutable (only new entries, supersede by reference), Journal is append-only (never rewritten), Plans must cite grounding and declare measurable success criteria. The gates exist because the audience is a team, not a single agent. Accountability needs disciplines that permissiveness can't offer.

4. **Cross-layer promotion with bidirectional links.** A recurring Journal observation graduates into Doctrine via a human-reviewed promotion; the old Journal entry keeps the `Promoted-to:` pointer and the new Doctrine entry keeps `Promoted-from:`. Plans that succeed become Procedures. Nothing disappears; everything traces back. Prior art sees lessons migrate ("this is in the wiki now") without preserving the lineage; Cortex keeps it.

5. **Staleness is visible, not tolerated.** Every derived layer's freshness is computable. `cortex status` surfaces stale Maps, stale States, Plans whose Success Criteria reference a State claim that has since changed. The rule is: *silent staleness is a bug*. This is the direct lesson from sigint's 4-day resolution-pipeline death, where `/sig-status` reported "normal" against an empty dataset — documented in `agent/FIX_DATAFLOW_GAPS_PLAN.md`.

---

## 3. Composition with Touchstone and Sentinel

Cortex is the third tool in the autumngarage family. The composition is not *foundation / loop / memory* — that framing is catchy but imprecise. The sharper trichotomy, grounded in what each tool actually does:

| Tool | Concern | What it owns | Authoring mode |
|---|---|---|---|
| **Touchstone** | **Policy** — what good looks like | Principles, scaffolding, hooks, CI glue | Prescriptive |
| **Sentinel** | **Execution** — what the project is doing | Ephemeral run journals, scans, backlog, verifications | Descriptive |
| **Cortex** | **Reasoning** — why we chose this and what we learned | Doctrine, Map, State, Plans, Journal, Procedures | Explanatory + prescriptive |

Policy / execution / reasoning. Reflexes / sensorimotor / cognitive, if you prefer the biology. The older framing (cytoskeleton / metabolism / genome+memory) holds for Touchstone and Sentinel but is imprecise for Cortex — Doctrine is the genome (slow, foundational), Journal is episodic memory (fast, accumulating), Plans are active intention (neither). Don't conflate the three.

The three tools compose **by file contract only**, not code import:

- **Touchstone → Cortex**: Touchstone's pre-merge hook can draft a Journal entry for architecturally significant merges. Optional; gated on `.cortex/` existing. The draft waits for human approval before it's published — Touchstone is a *drafter*, never an *authority*, for Cortex.
- **Sentinel → Cortex**: Sentinel's end-of-cycle hook writes a Journal entry summarizing what shipped, linking back to `.sentinel/runs/<timestamp>.md`. This turns Sentinel's ephemeral output into durable reasoning. Also gated on `.cortex/` existing.
- **Cortex → Sentinel**: Sentinel reads Doctrine as lens-generation context — the lenses ("kill criteria," "non-goals," "load-bearing invariants") are no longer derived from a freeform `CLAUDE.md` but from structured Doctrine entries that cite each other.
- **Cortex → Touchstone**: `cortex doctor` can check that principles/ entries are cited by at least one Doctrine entry, flagging principles the project claims to follow but never grounds.

None of these require a shared library. Each tool reads files the other writes. Each tool degrades gracefully if the other isn't installed — a Cortex-only project works, a Sentinel-only project works, all three together work better.

**The integration debt is real and worth naming.** Today, the Sentinel → Journal and Touchstone → Journal hooks are in the spec but not wired. Without them, Cortex is a disciplined file protocol that humans use by hand, which is valuable but not the full vision. Phase E wires the hooks; Phase E is where the *composition* becomes observable, not just designed.

---

## 4. Adjacent tools: honest comparison

A vision that refuses to name what it's next to is a vision that hasn't thought clearly about its shape. The table:

| Adjacent | What it is | Where Cortex is different | Risk |
|---|---|---|---|
| **Letta (née MemGPT)** | Git-backed Markdown+YAML "MemFS" for stateful agents. | Layer discipline, regeneration, human-first write gates. Letta is permissive; Cortex is gated. | **Highest.** Letta could add layer discipline and catch up. |
| **Claude Code CLAUDE.md + auto-memory** | Two-tier: human-edited project context + agent-accumulated `memory/`. Built into the IDE devs already use. | Six layers with per-layer triggers, regeneration, promotion pipeline. Cortex is a *spec* Claude Code could adopt. | **Existential.** If Anthropic ships multi-layer native memory, Cortex-as-product dies. Cortex-as-spec survives by being adopted first. |
| **Cursor Rules** | `.cursor/rules/*.mdc` — behavior instructions for Cursor agents. | Rules are *prescriptive for agents*; Cortex is *memory about the project*. Complementary, not competing. | Low. |
| **Aider repo-map + CONVENTIONS.md** | Ephemeral tree-sitter repo index + static conventions. | Repo-map is context selection; Cortex's Map is persisted, regenerated, broader. | Low. |
| **Graphiti / Zep** | Bi-temporal knowledge graph, opaque to humans, optimized for agent retrieval latency. | Cortex trades latency for human readability and git-nativeness. Different consumer. | Low — different tradeoff, different audience. |
| **Obsidian / Logseq / Dendron** | Personal PKM with Markdown + linking. Known link-rot problem. | Per-project, team-aware, regeneration surfaces staleness. | Low. |
| **AGENTS.md** | Single-file open standard (August 2025) for "project context for agents." | Discovery vs. memory. `.cortex/` and `AGENTS.md` are complementary. | Low. A serious project should have both. |
| **MetaGPT / ChatDev / A-MEM** | Agent-framework memory — document-centric or Zettelkasten-in-a-graph. | Neither is file-format-first + layered + regenerating + Diataxis-disciplined. | Low. |

Two of these matter enough to name explicitly in the positioning.

**Letta is the closest analogue.** Git-backed, Markdown, dual human/agent authorship. If you squint, Cortex looks like a more opinionated Letta. The honest positioning: Cortex's opinions are the point. Letta lets agents write anywhere because it's built for single-agent workflows where permissiveness is an asset. Cortex's write gates exist because the audience is a team with durable accountability requirements — and because team workflows make permissiveness a silent correctness hazard (sigint's premature-completion declaration is a Letta-shaped failure in an un-Letta-shaped tool).

**Claude Code's own memory is the existential question.** CLAUDE.md + `memory/` is a working, built-in, integrated solution. If it grows a layer-and-promotion discipline in the next year, the standalone Cortex format loses its reason to exist. Cortex's defense has to be *either* (a) the spec is adopted broadly enough that Anthropic converges on it, or (b) the spec is demonstrably better for teams than anything bundled into a single-vendor tool. We should not pretend this risk isn't there.

---

## 5. What Cortex is explicitly not

- **Not a vector store.** No embeddings, no ANN search, no similarity retrieval. Markdown, git, grep. If you want embeddings, run them over `.cortex/` — we won't stop you.
- **Not a database.** No schema migrations, no SQL, no JSON Lines binary log. `.cortex/.index.json` is a cache, not a source of truth.
- **Not a knowledge graph.** We have cross-references between files. We do not build a graph. If you want Graphiti, use Graphiti — it solves a different problem (agent retrieval at scale).
- **Not a portfolio tool.** One project at a time. The Lighthouse conversation — cross-project state — is explicitly out of scope. Cortex is `.cortex/` in a single repo, period.
- **Not an agent framework.** Cortex has no notion of "an agent." It has writers (humans, CLIs, hooks). Any agent framework (Claude Code, Cursor, Sentinel, Aider) can read and write a `.cortex/`.
- **Not a replacement for CLAUDE.md or AGENTS.md.** `AGENTS.md` is the project's public-facing "here's what this is for agents" declaration. `.cortex/` is the project's internal memory. A serious project has both and they don't overlap.
- **Not opinionated about how plans are generated.** Humans write plans. Agents write plans. Sentinel may spawn plans. Cortex cares about the file contract, not the authorship model.

---

## 6. Why this ships (as a spec, not a product)

The decision to lead with a spec and an open protocol is a distribution choice, not a technical one. Three arguments:

1. **File formats survive tool turnover.** The CLI is a reference implementation; anyone can build another. Cursor, Claude Code, and Aider all converged on `AGENTS.md` without anyone owning it. `.cortex/` should be able to be read and written by any tool that wants to compose with it.
2. **Spec-first prevents feature creep from eating the invariants.** If the `Journal is append-only` rule lives in code, some future PR quietly relaxes it. If it lives in a versioned SPEC.md, relaxing it is a visible breaking change.
3. **A spec + reference CLI is the only form of this that wouldn't be cannibalized by Claude Code shipping built-in memory.** Built-in memory solves the use case for the single tool; the spec survives because it's tool-neutral. The long game is adoption, not download counts.

Phase B ships the walking-skeleton CLI. Phase C adds the first synthesis command (`refresh-map`, shelling out to `claude -p` directly — same convergent-CLI pattern Sentinel uses, no SDK, no provider layer). Phase D adds Plan and Journal authoring helpers. Phase E wires integration with Touchstone and Sentinel and dogfoods on sigint's repo — sigint is where the manual practice was invented, so sigint is where Cortex proves it replaces the manual practice without losing the fidelity.

---

## 7. The honest risks

1. **Six layers may be too many.** If the practical minimum for a useful Cortex is Doctrine + Journal, the other four are baroque. Phase B–D must pressure-test whether Map, State, Plans, and Procedures each pay their weight on real repos. If one of them doesn't, collapse it.
2. **Regeneration costs money.** Every `refresh-map` is a `claude -p` call. On a large codebase, that's minutes and dollars. If regeneration is expensive enough that people turn it off, staleness returns by the back door.
3. **Write gates require tooling to enforce.** Append-only Journal, immutable Doctrine — these are spec rules. Without a pre-commit hook that enforces them, a tired human can just `sed` over an entry. The Touchstone pre-commit integration is the enforcement mechanism, and it's in Phase E.
4. **Claude Code native memory.** Discussed above. Not reiterating.
5. **Adoption requires someone other than the author to try it.** The sigint dogfood proves the spec against one user's practice. The real test is whether a developer who didn't invent these rules finds them more useful than confusing.
6. **Sentinel can derive goals from a stale `CLAUDE.md` if Doctrine is updated and `CLAUDE.md` isn't.** Spec gap. Options: (a) Doctrine must be mirrored into `CLAUDE.md` (couples the files), (b) `CLAUDE.md` must cite Doctrine instead of duplicating (looser, closer to current practice), (c) Phase E problem only. Leaning (b), to resolve in the first Doctrine-backed Sentinel cycle.

---

## 8. The smallest version that proves the idea

A Cortex MVP that *just works* has three files in `.cortex/`:

- `doctrine/0001-why-this-project-exists.md` — the thesis, with `## What would break this` section.
- `journal/<date>-<slug>.md` for each decision or lesson, append-only.
- `state.md` — a derived snapshot, regenerated weekly.

That's it. Three files, two layers plus State, all the rest of the spec is optional until a project wants it. The CLI makes the regen one command. The claim this MVP makes is: *a thesis, a write-ahead log of lessons, and a derived state view, all in files, is already more durable than what most teams have today.* Everything else in the spec — Procedures, Plans, Map, the promotion pipeline, the integrations — earns its keep by solving problems that surface only at higher scale.

Leading with the MVP in the README is the right way to teach the spec. The full spec shows up for those who want it.

---

## 9. What to do with this draft

Send to `codex exec` and `gemini` with a direct pushback prompt: *"Tear this apart. Where is the positioning wrong, overclaimed, or already done better by someone else? Where does the composition story leak? What are we not seeing?"*

Fold critiques into a second draft. Decide:

- What lands in `README.md` as the public vision (trimmed version of §§ 1, 2, 3, 8).
- What becomes Doctrine (§ 5 "explicitly not" — candidate for a new Doctrine entry on scope boundaries; § 3 composition framing — update Doctrine 0002).
- What becomes a Journal entry (§ 0 incident, if not already captured; § 7 risks, if we want them in the durable record).
- What stays as open questions (§§ 7.1, 7.6).

Then commit, push, close this plan as shipped with the links to where the ideas landed.
