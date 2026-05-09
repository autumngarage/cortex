```text
  ____           _
 / ___|___  _ __| |_ _____  __
| |   / _ \| '__| __/ _ \ \/ /
| |__| (_) | |  | ||  __/>  <
 \____\___/|_|   \__\___/_/\_\
```

> *Project memory for AI-assisted work.*
>
> by **[Autumn Garage](https://github.com/autumngarage/autumn-garage)** · alongside [Touchstone](https://github.com/autumngarage/touchstone) · [Sentinel](https://github.com/autumngarage/sentinel) · [Conductor](https://github.com/autumngarage/conductor) · [Alchemist](https://github.com/autumngarage/alchemist) — Cortex is a protocol for agent project memory that treats your exact git repo as the memory store.

# Cortex

> **Cortex is a protocol for agent project memory that treats your exact git repo as the memory store.** Instead of introducing a new database, daemon, or vector index, it defines a directory of structured Markdown files (`.cortex/`) that agents evolve alongside code. It is grepable, diffable, and auditable with existing tools, adding the missing agent memory convention without replacing your workspace.

**Status:** v0.9.0 released 2026-05-06 — production-ready. Latest release notes: [GitHub Releases](https://github.com/autumngarage/cortex/releases). [`SPEC.md`](./SPEC.md) v0.5.0; [`.cortex/protocol.md`](./.cortex/protocol.md) v0.3.0.

**For "where are we now" and "what's next" — read [`.cortex/state.md`](./.cortex/state.md) (current state) and [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) (the one master launch plan).** Those are the canonical sources, kept current by Cortex itself; this README does not restate them. Eating our own dog food: a single canonical owner per fact is [Doctrine 0007](./.cortex/doctrine/0007-canonical-ownership-of-state-and-plans.md), and `cortex doctor` warns when repo-root files duplicate `.cortex/` content.

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

> **Status:** v0.3.0 ships status, structural validation, audit, retrieval, an interactive `cortex init` wizard with scan-and-absorb for existing repos, Autumn Garage sibling surfacing in `cortex doctor`, the unscoped-LLM/API-constraint warning, write-side authoring via `cortex journal draft <type>` and `cortex plan spawn <slug>`, release-event audit coverage, and orphan-deferral validation. The fully interactive per-candidate promotion prompts shown below depend on `.cortex/.index.json` being populated, which lands with **v0.6.0** (the lifecycle layer alongside the `cortex promote` real writer) per the 2026-04-24 production-release rerank — v0.4.0 is read-side foundation (`refresh-state` + `cortex next` + `plan status`), v0.5.0 is trust + automation (`--audit-instructions` + Touchstone post-merge), v0.6.0 is lifecycle (.index.json + promote + remaining doctor invariants), v0.9.0 is the external dogfood gate. Track progress in [`.cortex/state.md`](./.cortex/state.md).

What ships today:

```bash
cortex                      # status summary — active plans, journal activity, digest age, queue counts
cortex init                 # scaffold .cortex/ in a project (idempotent); scans for existing
                            # principles/, plans/, decisions/, ROADMAP.md, etc. and offers to
                            # absorb each one (Y/n) into Doctrine or Plans, citing the source
                            # via `Imported-from:` frontmatter — no flag needed
cortex manifest --budget N  # token-budgeted session-start slice per Protocol § 1
cortex grep <pattern>       # frontmatter-aware ripgrep wrapper; see docs/grep.md
cortex update               # bring this repo's .cortex/ up to date in one step
cortex update --check       # verify generated layers are current without writing files
cortex refresh-state        # regenerate .cortex/state.md
cortex refresh-index        # rebuild .cortex/.index.json
cortex doctor               # validate .cortex/ against SPEC
cortex doctor --audit       # check Tier-1 Protocol triggers have matching Journal entries
cortex doctor --audit-digests
cortex promote <id>         # stub pending v0.6.0 .index.json writer + promote writer
cortex sync                 # deprecated alias for cortex update
cortex version
```

See [`docs/grep.md`](./docs/grep.md) for `cortex grep --frontmatter` filter syntax and examples.

External tools can seed their own default Doctrine without making Cortex opinionated: `cortex init --seed-from <dir>` copies one-level Markdown packs into `.cortex/doctrine/`, preserving bytes and frontmatter exactly. Numbered pack files keep their requested `NNNN-` prefix; unnumbered files are assigned from `0100` upward using their H1 slug. By default Cortex aborts before copying if a destination Doctrine entry or requested number already exists; `--merge skip-existing` makes pack installs idempotent for cases like Sentinel's planned baseline Doctrine pack.

What the full interactive flow will look like once v0.6.0 ships the `.cortex/.index.json` writer and the `cortex promote` end-to-end writer (the per-candidate prompt UX itself is deferred from the v1.0 path to v1.x — see [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)` #6):

```
$ cortex
Cortex — your-project   spec v0.5.0   state: fresh (regenerated 2h ago)

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

Everything surfaces at every invocation. You can't miss the queue; you can't miss an overdue digest; you can't miss staleness. For scripting use `cortex --status-only` or `cortex status --json`; the full interactive prompts are deferred from the v1.0 path (they depend on `.cortex/.index.json` having months of real promotion candidates — the v0.6.0 writer ships first, the per-candidate UX revisits at v1.x once data exists; see [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)` #6).

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
- **With Touchstone.** The post-merge hook (auto-drafts `pr-merged` journal entries via `cortex journal draft`) ships in **v0.5.0**. The `cortex doctor --strict` pre-push gate is deferred from the v1.0 path to v1.x. On architecturally-significant pre-merge diffs (Protocol T1.7), Touchstone invokes `cortex doctrine draft` to create a durable Doctrine candidate the author reviews and promotes — this is **deferred from v1.0 to v1.x** along with the SPEC amendment that defines the `.cortex/pending/` staging layer (the durable-write requirement for T1.7's Tier-1 "auditable" contract is real, but a SPEC change for narrow triad-mode audience warrants its own dedicated cycle; see [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)` #3). Cortex Doctrine `grounds-in:` Touchstone principles where applicable.
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

Cortex is deliberately **not** a database, a knowledge graph, a portfolio tool, an agent framework, a replacement for `AGENTS.md` / `CLAUDE.md`, or cloud-hosted. On vector storage specifically: the canonical store stays markdown + git + grep — no embeddings live inside `.cortex/` content — but Cortex owns an opt-in retrieval interface (`cortex retrieve`) over a gitignored derived index for projects whose corpora outgrow recency-by-grep. See [Doctrine 0006](./.cortex/doctrine/0006-scope-boundaries-v3.md) (supersedes 0005) for the rationale per category and the storage-vs-retrieval split. Adjacent tools compose with Cortex; none are replaced by it.

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

## Documentation

- **[`SPEC.md`](./SPEC.md)** — The normative specification for the `.cortex/` file format and protocol.
- **[`docs/config-reference.md`](./docs/config-reference.md)** — Per-project `.cortex/config.toml` schema reference (every key, type, default, worked example).
- **[`docs/spec-conformance.md`](./docs/spec-conformance.md)** — The SPEC-to-test traceability matrix, proving CLI conformance.
- **[`docs/PITCH.md`](./docs/PITCH.md)** — A plain-language overview of Cortex.
- **[`docs/CASE-STUDIES.md`](./docs/CASE-STUDIES.md)** — Documented case studies: the conductor incident and the three-target v0.9.0 dogfood gate.
- **[`docs/install-pr-templates.md`](./docs/install-pr-templates.md)** — Reusable copy and checklist for Cortex install PRs on sibling projects.
- **[`docs/PRIOR_ART.md`](./docs/PRIOR_ART.md)** — Research and influences.

---

## Status and plan

**Production-ready (v0.9.0, released 2026-05-06).** Three reference installs in the wild: `conductor`, `touchstone`, and `vesper` install Cortex via the Homebrew tap. Nine dogfood-surfaced bugs filed and closed in v0.9.0; CI fixtures are now permanent for fresh-clone acceptance and bare-repo degradation. See [`docs/CASE-STUDIES.md`](./docs/CASE-STUDIES.md) for the gate evidence.

For "where are we now" and "what's next": [`.cortex/state.md`](./.cortex/state.md) is the canonical current state; [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) is the master launch sequence. README deliberately keeps only this pointer per [Doctrine 0007](./.cortex/doctrine/0007-canonical-ownership-of-state-and-plans.md) — repo-root files that restate `.cortex/` content are anti-pattern.

LLM-additive features (`cortex refresh-map`, `cortex refresh-state --enhance`, `cortex next --enhance`) and triad-mode infrastructure (`.cortex/pending/` + `cortex doctrine draft` + T1.7 Touchstone pre-merge hook) are deliberately **deferred from the v1.0 path** to v1.x. See [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)` for the full deferral list with revisit conditions per item.

---

## License

MIT.
