# Cortex

> **A protocol for agents to continuously journal what's happening on a project, and for humans to promote what matters.** The reflective layer of the autumngarage composition — Touchstone is the foundation (universal policy), Sentinel is the loop (autonomous execution), Cortex is the memory (project-local reasoning).

**Status:** CLI v0.2.3 (polish: `cortex init` scan no longer double-prompts on top-level `CLAUDE.md`/`AGENTS.md`, skips toolchain config dirs (`.sentinel/`, `.cortex/`, `.claude/`, `.github/`, `.husky/`, `.circleci/`, `.devcontainer/`), and recognizes `*MIGRATION*.md` as Plan candidates; v0.2.2 added scan-and-absorb so large existing projects drop from ~hours of manual seeding to ~10 minutes interactive; v0.2.1 added the unscoped-LLM/API-constraint warning in `cortex doctor`; v0.2.0 added the Autumn Garage integration — interactive `init`, `doctor` sibling detection, shipped Plan template). [SPEC.md](./SPEC.md) v0.3.1-dev (draft, unchanged from v0.1.0). [`.cortex/protocol.md`](./.cortex/protocol.md) specifies the agent contract. The CLI ships the non-synthesizing commands (`init`, `status`, `doctor`, `manifest`, `grep`, `promote` stub). Deterministic `cortex refresh-state` (plus `cortex journal draft` and `cortex plan spawn`) lands in Phase C; LLM-powered `cortex refresh-map` and `cortex refresh-state --enhance` land in Phase E ([PLAN.md](./PLAN.md); 2026-04-23 reorder recorded in [`.cortex/journal/2026-04-23-phase-c-reordered.md`](./.cortex/journal/2026-04-23-phase-c-reordered.md)).

**New here?** Start with [`docs/PITCH.md`](./docs/PITCH.md) — plain-language one-liner, vision, and day-in-the-life walkthrough.

---

## The problem, in two stories

**The crash.** A multi-hour design session crashes. Every research finding, every decision branch, every piece of iteration: gone. Git is clean. Memory is empty. The next session's honest answer to *"where were we?"* is *"I don't know."*

**The retreat.** On 2025-11-22, Cursor shipped version 2.1 and removed the Memories feature it had introduced six months earlier. Official rationale: *"intentionally removed."* The [feature-request thread](https://forum.cursor.com/t/persistent-intelligent-project-memory/39109) for persistent project memory has been open since January 2025 and is still unresolved. The community has been hand-building the same three-file `.brain/` convention in every repo ([example](https://forum.cursor.com/t/persistent-memory-for-cursor-that-survives-every-session-brain-folder-approach/157488)): *"Cursor in the morning, Claude Code in the afternoon — both read the same `.brain/`."*

Projects accumulate reasoning that has nowhere to live. Chat context evaporates. Agent memory is machine-local. Vendors have tried and retreated. The pattern is clear; the spec is missing.

---

## What Cortex is

Cortex defines a `.cortex/` directory per project with six layers, and a **Protocol** that tells any agent when to write to them:

| Layer | Question | Contract |
|---|---|---|
| **Doctrine** | Why does this project exist? | Immutable-with-supersede; numbered; never deleted. |
| **Map** | What's here, structurally? | Derived; regenerated from code + git; declares provenance. |
| **State** | Where are we right now? | Derived; regenerated from metrics + recent Journal; declares provenance. |
| **Plans** | What are we doing about it? | Mutable named trails; cite their grounding; measurable success criteria. |
| **Journal** | What happened, what did we learn? | Append-only write-ahead log; one event per file. |
| **Procedures** | How do we do X safely? | Versioned how-tos and interface contracts. |

Every layer has a single authoring mode (Diataxis discipline), a single write trigger, and a single retrieval contract. See [SPEC.md](./SPEC.md) for the full contract per layer.

### The Protocol

The Protocol ([`.cortex/protocol.md`](./.cortex/protocol.md)) is the rule set any agent follows when working on a Cortex-enabled project. Two tiers:

- **Tier 1 — machine-observable triggers.** Deterministic, auditable, enforceable. *Diff touches `.cortex/doctrine/`. Test failed after passing earlier. Plan status changed. File deleted over N lines. Dependency manifest changed. Sentinel cycle ended. Touchstone pre-merge fired on architecturally significant diff (T1.7). Commit message matches pattern. Pull request merged to default branch (T1.9).* When these fire, the agent writes from the template the Protocol specifies — Journal entries for most triggers, a `doctrine/candidate.md` (a Doctrine draft awaiting promotion) for T1.7. Tooling verifies compliance.
- **Tier 2 — advisory heuristics.** Judgment-based and explicitly labeled. *Decision phrasing. Failed attempt that taught something. Surprise about existing code. User says "remember this."* The agent is asked to journal on these; non-compliance is not enforced.

Projects import `.cortex/protocol.md` into `AGENTS.md`. Any agent that reads `AGENTS.md` inherits the Protocol.

### Three invariants the Protocol enforces

1. **Journal is append-only.** Never rewritten; new entry per event.
2. **Doctrine is immutable.** Changes come via new entries with `supersedes:` — the old entry stays.
3. **Generated layers declare provenance.** Every Map / State / digest carries `Generated`, `Generator`, `Sources`, `Corpus`, `Omitted`, `Incomplete`, `Conflicts-preserved`. `Incomplete: []` means "I looked at everything I could." Missing fields fail `cortex doctor`.

---

## UX — one command

> **Status:** v0.2.3 ships status, structural validation, audit, retrieval, an interactive `cortex init` wizard with scan-and-absorb for existing repos (one screen of "here's what I found", per-file Y/n on Doctrine/Plan candidates, taught patterns persist to `.cortex/.discover.toml`), Autumn Garage sibling surfacing in `cortex doctor`, and the unscoped-LLM/API-constraint warning in `cortex doctor`. The fully interactive per-candidate promotion prompts shown below depend on `.cortex/.index.json` being populated, which now lands with **Phase E** (alongside the `cortex promote` writer) per the 2026-04-23 roadmap reorder — Phase C is authoring + deterministic state refresh, Phase D is Sentinel/Touchstone integration, Phase E is LLM synthesis + promotion governance. Track progress in [`.cortex/state.md`](./.cortex/state.md).

What ships today:

```bash
cortex                      # status summary — active plans, journal activity, digest age, queue counts
cortex init                 # scaffold .cortex/ in a project (idempotent); scans for existing
                            # principles/, plans/, decisions/, ROADMAP.md, etc. and offers to
                            # absorb each one (Y/n) into Doctrine or Plans, citing the source
                            # via `Imported-from:` frontmatter — no flag needed
cortex manifest --budget N  # token-budgeted session-start slice per Protocol § 1
cortex grep <pattern>       # frontmatter-aware ripgrep wrapper
cortex doctor               # validate .cortex/ against SPEC
cortex doctor --audit       # check Tier-1 Protocol triggers have matching Journal entries
cortex doctor --audit-digests
cortex promote <id>         # stub pending Phase E .index.json writer + promote writer
cortex version
```

What the full interactive flow will look like once Phase E lands the `.cortex/.index.json` writer and the `cortex promote` end-to-end writer:

```
$ cortex
Cortex — your-project   spec v0.3.1-dev   state: fresh (regenerated 2h ago)

▸ 7 Journal entries since last check
▸ 3 promotion candidates (1 stale, 2 proposed)
▸ March 2026 digest overdue by 8 days

 [1] j-2026-04-17-auth-retry       [trivial]     3 entries on retry backoff
     → Promote to doctrine/0005?  [y/n/view/defer/skip]:

 [2] j-2026-04-16-test-scoping      [editorial]  New pattern; no Doctrine covers
     → Promote to doctrine/0006?  [y/n/view/defer/skip]:

 [3] j-2026-03-22-flaky-ci          [stale, 17d] Re-proposed after 3 new entries
     → Promote to doctrine/0007?  [y/n/view/defer/skip]:

Generate March 2026 digest now?  [y/n]:

Anything else? (enter to exit, or type a request)
```

Everything surfaces at every invocation. You can't miss the queue; you can't miss an overdue digest; you can't miss staleness. For scripting use `cortex --status-only` or `cortex status --json`; the full interactive prompts are Phase C.

---

## Composition with Touchstone and Sentinel

The three tools occupy distinct authority layers:

| Tool | Scope | Authority |
|---|---|---|
| **Touchstone** | Universal (distributed via `touchstone sync`) | Originates engineering standards. Prescriptive. |
| **Sentinel** | Project-local | Executes and reports. Descriptive. |
| **Cortex** | Project-local | Remembers and reasons. Reflective. |

They compose by file contract, never code import:

- **Solo Cortex.** Any agent reading `AGENTS.md` follows the Protocol. Journal grows continuously; humans promote via the `cortex` interactive flow. Invariants are advisory (enforced only on explicit `cortex doctor` runs).
- **With Touchstone.** Pre-push hook runs `cortex doctor --strict`. Invariants are code-enforced. On architecturally significant pre-merge diffs (Protocol T1.7), Touchstone posts a PR comment pre-filled from the `doctrine/candidate.md` template so the author can hand-author a Doctrine candidate if the decision warrants it. Cortex Doctrine `grounds-in:` Touchstone principles where applicable.
- **With Sentinel.** Sentinel reads `.cortex/` (Doctrine + active Plans + recent Journal + digests) for cycle context. End-of-cycle writes a Journal entry. Next cycle reads the previous cycle's Journal. The loop closes.

Solo Cortex is *good notes with conventions*. Triad Cortex is *enforced institutional memory*. Both are useful; the triad is where the loop closes.

---

## Scale — consolidate and archive, never delete

Cortex is append-only at write, **tiered at read**. Nothing is deleted; everything stays in git. The default read surface stays lean regardless of corpus age:

- **Doctrine**: never archived; superseded entries stay with pointer; default session-start loading is `Load-priority: always` pins plus recency (see [`.cortex/protocol.md`](./.cortex/protocol.md) § 1).
- **Journal**: hot (0–30d) → warm (30–365d) → cold (>365d, `journal/archive/<year>/`). Default load is hot + monthly digests.
- **Plans**: auto-moved to `plans/archive/` after 30d in `shipped` or `cancelled` status.
- **Map / State**: always regenerated; old versions are git history.

Monthly: `cortex` proposes a Journal digest — a summary of the period's key decisions with citations to originals. Human approves in one keystroke. Digests obey the seven-field contract plus a depth cap (quarterly digests can cite monthly digests at most one level deep) and audit sampling (`cortex doctor --audit-digests` verifies claims trace back to source entries).

The claim: at year 10, the default manifest is still ~7k tokens. Doctrine grew by promotion; Journal grew by Protocol; digests replaced raw entries in the read surface. **Cortex improves with scale because recurring lessons graduate into always-loaded Doctrine, and the corpus becomes richer evidence for future promotion.**

---

## What Cortex is not

Cortex is deliberately **not** a vector store, a database, a knowledge graph, a portfolio tool, an agent framework, a replacement for `AGENTS.md` / `CLAUDE.md`, or cloud-hosted. See [Doctrine 0005](./.cortex/doctrine/0005-scope-boundaries-v2.md) (supersedes 0004) for the rationale per category. Adjacent tools compose with Cortex; none are replaced by it.

---

## Install

```bash
brew tap autumngarage/cortex
brew install autumngarage/cortex/cortex   # fully qualified — homebrew-core has a different `cortex` (Prometheus storage)
cortex init        # in any project
cortex doctor      # verify the scaffold
```

Or from source with [`uv`](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/autumngarage/cortex
```

### Installing the full autumngarage trio

Cortex stands alone. It also composes with its two siblings — install all three when you want the full loop:

```bash
# Foundation: engineering standards, principles, pre-push Codex review
brew tap autumngarage/touchstone
brew install touchstone

# Loop: autonomous multi-provider agent cycles
brew tap autumngarage/sentinel
brew install sentinel

# Memory: project-local reasoning (this repo)
brew tap autumngarage/cortex
brew install autumngarage/cortex/cortex   # fully qualified; see note above
```

Each tool writes to its own files and reads the others only as best-effort. Nothing breaks if one is missing — Cortex works without Sentinel and without Touchstone; it just can't enforce invariants at push-time (Touchstone) or receive end-of-cycle journal entries (Sentinel). See the [Composition](#composition-with-touchstone-and-sentinel) section above for the file-contract details.

---

## Status and plan

See [PLAN.md](./PLAN.md). Phases A (foundation + spec) and B (walking-skeleton CLI — `init` / `status` / `doctor` / `manifest` / `grep` / `promote` stub + `doctor --audit`) shipped as v0.1.0 on Homebrew; currently on v0.2.3. On 2026-04-23 the remaining roadmap was reordered (full provenance in [`.cortex/journal/2026-04-23-phase-c-reordered.md`](./.cortex/journal/2026-04-23-phase-c-reordered.md)) so that the phase that actually closes the session-pickup gap ships first. **Phase C is now the current P0** — authoring primitives (`cortex journal draft`, `cortex plan spawn`, `cortex plan status`) plus a deterministic `cortex refresh-state` that walks plans + journal with a marker convention for preserving hand-authored prioritization; no LLM dependency, works without `claude` on PATH, byte-identical output on unchanged inputs. Phase D wires those commands into Sentinel and Touchstone hooks so the Journal fills itself from real work events (end-of-cycle, PR merge, architecturally-significant pre-merge via PR comment with the `doctrine/candidate.md` template). Phase E is the capstone — LLM synthesis (`refresh-map`, `refresh-state --enhance`), `.cortex/.index.json` writer, `cortex promote` writer, every remaining SPEC § 4 `cortex doctor` invariant check, and the external dogfood gate on a freshly-cloned Sentinel repo.

The spec at v0.3.1-dev is a draft. See [`docs/PRIOR_ART.md`](./docs/PRIOR_ART.md) for the research synthesis behind the design, and the `.cortex/journal/` directory in this repo for a dogfood trail of the design decisions (especially `2026-04-17-vision-v3-promoted.md` for the full provenance).

---

## License

MIT.
