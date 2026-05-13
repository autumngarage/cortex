```text
  ____           _
 / ___|___  _ __| |_ _____  __
| |   / _ \| '__| __/ _ \ \/ /
| |__| (_) | |  | ||  __/>  <
 \____\___/|_|   \__\___/_/\_\
```

[![Release](https://img.shields.io/github/v/release/autumngarage/cortex?label=release&color=informational)](https://github.com/autumngarage/cortex/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Homebrew](https://img.shields.io/badge/brew-autumngarage%2Fcortex-orange)](https://github.com/autumngarage/homebrew-cortex)

> **Git-native context build system for AI agents.**
>
> *The spine* of the **[Autumn Garage](https://github.com/autumngarage/autumn-garage)** quartet, alongside [Touchstone](https://github.com/autumngarage/touchstone) · [Sentinel](https://github.com/autumngarage/sentinel) · [Conductor](https://github.com/autumngarage/conductor).

# Cortex

Cortex treats project memory as source code. Primary facts live in structured Markdown under `.cortex/`, generated context surfaces declare their inputs, and `cortex doctor` verifies the invariants before stale or uncited context quietly steers an agent. No new database, no daemon, no mandatory vector index — the memory store stays grepable, diffable, and auditable with existing tools.

**New here?** Start with [`docs/PITCH.md`](./docs/PITCH.md) — plain-language one-liner, vision, and day-in-the-life walkthrough. For "where are we now" and "what's next," read [`.cortex/state.md`](./.cortex/state.md) and [`.cortex/plans/`](./.cortex/plans/) — those are the canonical sources, kept current by Cortex itself.

## The problem, in two stories

**The crash.** A multi-hour design session crashes. Every research finding, every decision branch, every piece of iteration: gone. Git is clean. Memory is empty. The next session's honest answer to *"where were we?"* is *"I don't know."*

**The retreat.** On 2025-11-22, Cursor shipped version 2.1 and removed the Memories feature it had introduced six months earlier. Official rationale: *"intentionally removed."* The [feature-request thread](https://forum.cursor.com/t/persistent-intelligent-project-memory/39109) for persistent project memory has been open since January 2025 and is still unresolved. The community has been hand-building the same three-file `.brain/` convention in every repo: *"Cursor in the morning, Claude Code in the afternoon — both read the same `.brain/`."*

Projects accumulate reasoning that has nowhere to live. Chat context evaporates. Agent memory is machine-local. Vendors have tried and retreated. The pattern is clear; the spec was missing.

## Install

```bash
brew install autumngarage/cortex/cortex   # fully qualified — homebrew-core has a different `cortex`
cortex init        # scaffold .cortex/ in any project
cortex doctor      # verify the scaffold
```

Or from source with [`uv`](https://github.com/astral-sh/uv):

```bash
uv tool install git+https://github.com/autumngarage/cortex
```

## Quickstart

```bash
cd ~/Repos/my-project
cortex init                  # scaffolds .cortex/, scans for existing principles/plans/decisions/, offers to absorb each
git add .cortex/
git commit -m "chore: initialize cortex memory"
cortex manifest --budget 8000   # token-budgeted session-start slice for an agent
cortex doctor                # validate against the spec
```

After that, agents read the bounded manifest at session start, write Journal entries as work produces durable lessons, and CI/review can reject stale generated context before it steers a follow-up session.

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

### Context build system

Cortex is not just a folder of notes. It is the build, budget, and verification layer for agent context. **Reusable thesis:** Cortex compiles human-authored project memory in Markdown + git into bounded, auditable context for agents.

**Context integrity** means five narrow things: freshness, provenance, budget-fit, policy compliance, and reviewability. It does not mean Cortex proves every fact true or guarantees the agent's final output is correct.

| Class | Examples | Contract |
|---|---|---|
| **Source artifacts** | Doctrine, Plans, Journal, Procedures, git history | Human-authoritative or append-only inputs; LLMs may propose edits but do not bypass gates. |
| **Derived artifacts** | State, Map, manifests, retrieve indexes, reports | Generated from source inputs; declare source files, source hashes, omissions, and incomplete inputs. |
| **Integrity checks** | `cortex doctor`, audit checks, budget checks | Fail visibly on stale, malformed, uncited, over-budget, or policy-violating context. |

- **Build artifacts:** State, Map, manifest slices, retrieve indexes, and future production reports are generated from source inputs.
- **Invalidation:** generated files carry `Generated`, `Sources`, `Sources-hash`, `Incomplete`, and `Conflicts-preserved` metadata so stale context is visible.
- **Budgeting:** session-start context is compiled through `cortex manifest --budget <N>`; deeper lookup goes through `cortex grep` and `cortex retrieve` instead of dumping the corpus into the model.
- **Verification:** `cortex doctor` is the context CI gate.

The durable product decision is [Doctrine 0008](./.cortex/doctrine/0008-context-integrity-build-system.md).

### The Protocol

The Protocol ([`.cortex/protocol.md`](./.cortex/protocol.md)) is the rule set any agent follows when working on a Cortex-enabled project. Two tiers:

- **Tier 1 — machine-observable triggers.** Deterministic, auditable, enforceable. *Diff touches `.cortex/doctrine/`. Test failed after passing earlier. Plan status changed. File deleted over N lines. Dependency manifest changed. Sentinel cycle ended. Touchstone pre-merge fired on architecturally significant diff. Commit message matches pattern. Pull request merged to default branch.* When these fire, the agent writes from the template the Protocol specifies. Tooling verifies compliance.
- **Tier 2 — advisory heuristics.** Judgment-based and explicitly labeled. *Decision phrasing. Failed attempt that taught something. Surprise about existing code. User says "remember this."* The agent is asked to journal; non-compliance is not enforced.

Projects import `.cortex/protocol.md` into `AGENTS.md`. Any agent that reads `AGENTS.md` inherits the Protocol.

### Three invariants the Protocol enforces

1. **Journal is append-only.** Never rewritten; new entry per event.
2. **Doctrine is immutable.** Changes come via new entries with `supersedes:` — the old entry stays.
3. **Generated layers declare provenance.** Every Map / State / digest carries `Generated`, `Generator`, `Sources`, `Corpus`, `Omitted`, `Incomplete`, `Conflicts-preserved`. `Incomplete: []` means "I looked at everything I could." Missing fields fail `cortex doctor`.

## Commands

```bash
cortex                      # status summary — active plans, journal activity, digest age, queue counts
cortex init                 # scaffold .cortex/ in a project; scans for existing principles/plans/decisions/
                            # and offers to absorb each into Doctrine or Plans, citing the source
cortex manifest --budget N  # token-budgeted session-start slice per Protocol § 1
cortex manifest --show-budget   # per-section estimates; normal coding startup target is 8k tokens
cortex grep <pattern>       # frontmatter-aware ripgrep wrapper; see docs/grep.md
cortex retrieve <query>     # ranked lookup over the derived index; --for-agent emits compact citations
cortex journal draft <type> # scaffold Journal entries; warns above ~1200 estimated tokens unless --allow-large
cortex update               # bring this repo's .cortex/ up to date in one step
cortex update --check       # verify generated layers are current without writing files
cortex refresh-state        # regenerate .cortex/state.md
cortex refresh-index        # rebuild .cortex/.index.json
cortex doctor               # validate .cortex/ against SPEC
cortex doctor --audit       # check Tier-1 Protocol triggers have matching Journal entries
cortex promote <id>         # promote a Journal entry to Doctrine
cortex version
```

See [`docs/grep.md`](./docs/grep.md) for `cortex grep --frontmatter` filter syntax and [`docs/retrieve.md`](./docs/retrieve.md) for citation-first `cortex retrieve --for-agent` output.

External tools can seed their own default Doctrine without making Cortex opinionated: `cortex init --seed-from <dir>` copies one-level Markdown packs into `.cortex/doctrine/`, preserving bytes and frontmatter exactly.

## Scale — consolidate and archive, never delete

Cortex is append-only at write, **tiered at read**. Nothing is deleted; everything stays in git. The default read surface stays lean regardless of corpus age:

- **Doctrine** — never archived; superseded entries stay with pointer.
- **Journal** — hot (0–30d) → warm (30–365d) → cold (>365d, `journal/archive/<year>/`). Default load is hot + monthly digests.
- **Plans** — auto-moved to `plans/archive/` after 30d in `shipped` or `cancelled` status.
- **Map / State** — always regenerated; old versions are git history.

Monthly, `cortex` proposes a Journal digest — a summary of the period's key decisions with citations to originals. **Cortex improves with scale because recurring lessons graduate into always-loaded Doctrine, and the corpus becomes richer evidence for future promotion.**

## What Cortex is not

Cortex is deliberately **not** a database, a knowledge graph, a portfolio tool, an agent framework, a replacement for `AGENTS.md` / `CLAUDE.md`, or cloud-hosted. On vector storage specifically: the canonical store stays markdown + git + grep — no embeddings live inside `.cortex/` content — but Cortex owns an opt-in retrieval interface (`cortex retrieve`) over a gitignored derived index for projects whose corpora outgrow recency-by-grep. See [Doctrine 0006](./.cortex/doctrine/0006-scope-boundaries-v3.md).

## The quartet

Cortex is the spine that holds project memory across tools and time:

- **[Touchstone](https://github.com/autumngarage/touchstone)** — scaffolding + pre-push AI review gate. *The ground.*
- **Cortex** *(this tool)* — portable file-format protocol for project memory. *The spine.*
- **[Sentinel](https://github.com/autumngarage/sentinel)** — autonomous assess→plan→delegate→review loop. *The hands.*
- **[Conductor](https://github.com/autumngarage/conductor)** — capability-aware router across LLM providers. *The voice.*

Each tool installs independently and composes through **file contracts, never code imports**:

- **Solo Cortex.** Any agent reading `AGENTS.md` follows the Protocol. Journal grows continuously; humans promote candidates via `cortex refresh-index` + `cortex promote <id>`.
- **With Touchstone.** The post-merge hook auto-drafts `pr-merged` journal entries via `cortex journal draft`. Architecturally-significant pre-merge diffs (T1.7) are scoped to drop a `doctrine/candidate.md` draft for the author to review and promote; the `cortex doctrine draft` command and triad-mode enforcement are deferred from v1.0 — see [`plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)`.
- **With Sentinel.** Sentinel reads `.cortex/` (Doctrine + active Plans + recent Journal + digests) for cycle context. End-of-cycle writes a Journal entry. Next cycle reads the previous cycle's Journal. The loop closes.

Solo Cortex is *good notes with conventions*. Triad Cortex is *enforced institutional memory*. Both are useful; the triad is where the loop closes. See [autumn-garage](https://github.com/autumngarage/autumn-garage) for the coordination repo.

## Status

Production-ready and shipped via Homebrew. Three reference installs in the wild: `conductor`, `touchstone`, and `vesper` install Cortex via the Homebrew tap. Latest release: [GitHub Releases](https://github.com/autumngarage/cortex/releases). Run `cortex version` to see the installed build.

For "where are we now" and "what's next": [`.cortex/state.md`](./.cortex/state.md) is the canonical current state; [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) is the master launch sequence; [`.cortex/plans/context-integrity-production.md`](./.cortex/plans/context-integrity-production.md) owns the context-integrity roadmap. README deliberately keeps only this pointer per [Doctrine 0007](./.cortex/doctrine/0007-canonical-ownership-of-state-and-plans.md) — repo-root files that restate `.cortex/` content are anti-pattern.

## Documentation

- [`SPEC.md`](./SPEC.md) — The normative specification for the `.cortex/` file format and protocol.
- [`docs/PITCH.md`](./docs/PITCH.md) — Plain-language overview of Cortex.
- [`docs/config-reference.md`](./docs/config-reference.md) — Per-project `.cortex/config.toml` schema reference.
- [`docs/grep.md`](./docs/grep.md) — `cortex grep --frontmatter` filter syntax and examples.
- [`docs/retrieve.md`](./docs/retrieve.md) — Citation-first `cortex retrieve --for-agent` output.
- [`docs/spec-conformance.md`](./docs/spec-conformance.md) — SPEC-to-test traceability matrix.
- [`docs/CASE-STUDIES.md`](./docs/CASE-STUDIES.md) — The conductor incident and the v0.9.0 dogfood gate.
- [`docs/PRIOR_ART.md`](./docs/PRIOR_ART.md) — Research and influences.
- [GitHub Releases](https://github.com/autumngarage/cortex/releases) — release notes for every version.

## License

MIT
