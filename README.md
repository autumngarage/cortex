# Cortex

> **A protocol for agents to continuously journal what's happening on a project, and for humans to promote what matters.** The reflective layer of the autumngarage composition — Touchstone is the foundation (universal policy), Sentinel is the loop (autonomous execution), Cortex is the memory (project-local reasoning).

**Status:** v0.3.0 shipped 2026-04-26 (five PRs #44–#48 merged; tag `v0.3.0` pushed; [GitHub Release](https://github.com/autumngarage/cortex/releases/tag/v0.3.0) published; `release.yml` auto-bumped the Homebrew tap formula on the `release-published` event). v0.3.0 ships `cortex journal draft <type>` (the keystone authoring command — pre-fills date and `--title` H1, appends auto-context from `git log` + `gh pr view`, refuses to write to incompatible stores per SPEC § 7), `cortex plan spawn <slug>` (scaffolds an active Plan with computed Goal-hash and seven-field frontmatter), the new T1.10 release-event Protocol trigger + `journal/release.md` template + tag-walk in `cortex doctor --audit`, and SPEC § 4.2 orphan-deferral enforcement in `cortex doctor` (warns on active-Plan bullets that lack a `plans/<slug>`, `journal/<date>-<slug>`, or `doctrine/<nnnn>-<slug>` citation pointing at a real file). Earlier v0.2.x: v0.2.6/v0.2.7 added Homebrew tap auto-deploy + `scripts/release.sh` release driver; v0.2.4/v0.2.5 fixed init UX bugs surfaced by the touchstone dogfood (Touchstone-managed Doctrine skip + end-of-file `@<path>` import placement + README filter + 0100-doctrine-floor + accurate state.md Sources + inline file:line ref + contiguous Next-steps numbering + top-level `--status-only --path` + scaffolded-template terminology cleanup; full closure in [`.cortex/journal/2026-04-25-init-ux-fixes-plan-shipped.md`](./.cortex/journal/2026-04-25-init-ux-fixes-plan-shipped.md)); v0.2.3 polished `cortex init` scan; v0.2.2 added scan-and-absorb; v0.2.1 added the unscoped-LLM/API-constraint warning; v0.2.0 added the Autumn Garage integration. [SPEC.md](./SPEC.md) v0.4.0-dev (draft — adds T1.10 release trigger + § 4.2 Doctrine-resolution clarification, additive minor per § 7). [`.cortex/protocol.md`](./.cortex/protocol.md) v0.2.1 specifies the agent contract. The CLI ships the non-synthesizing commands (`init`, `status`, `doctor`, `manifest`, `grep`, `promote` stub) plus the v0.3.0 write-side: `journal draft`, `plan spawn`. The remaining roadmap to v1.0 is sequenced as six release-driven sub-sections in [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) (single active plan after v0.3.0 closure — see `.cortex/state.md` `## Current work` for canonical priority): **v0.3.0 (shipped 2026-04-26)** — write-side authoring (`cortex journal draft`, `cortex plan spawn`, `release` template + T1.10 audit, orphan-deferral doctor check); **v0.4.0** — read-side (deterministic `cortex refresh-state` + `cortex next` MVP + `cortex plan status`); **v0.5.0** — trust + automation (Touchstone post-merge hook + `cortex doctor --audit-instructions` + Manifest `Verified:`); **v0.6.0** — lifecycle (`.cortex/.index.json` writer + `cortex promote` real writer + remaining doctor invariants); **v0.9.0** — external dogfood gate; **v1.0.0** — ceremonial freeze. Production-rerank rationale in [`.cortex/journal/2026-04-24-production-release-rerank.md`](./.cortex/journal/2026-04-24-production-release-rerank.md). LLM features (`refresh-map`, `refresh-state --enhance`, `cortex next --enhance`) deferred from the v1.0 path to v1.x.

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
cortex grep <pattern>       # frontmatter-aware ripgrep wrapper
cortex doctor               # validate .cortex/ against SPEC
cortex doctor --audit       # check Tier-1 Protocol triggers have matching Journal entries
cortex doctor --audit-digests
cortex promote <id>         # stub pending v0.6.0 .index.json writer + promote writer
cortex version
```

What the full interactive flow will look like once v0.6.0 ships the `.cortex/.index.json` writer and the `cortex promote` end-to-end writer (the per-candidate prompt UX itself is deferred from the v1.0 path to v1.x — see [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)` #6):

```
$ cortex
Cortex — your-project   spec v0.4.0-dev   state: fresh (regenerated 2h ago)

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

Single active plan (see [`.cortex/state.md`](./.cortex/state.md) `## Current work` for the canonical surface): [`.cortex/plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) — Ship Cortex v1.0 (six release-driven sub-sections; v0.3.0 shipped 2026-04-26, v0.4.0 read-side foundation is next). The session-scoped sub-plan [`cortex-v0.3.0`](./.cortex/plans/cortex-v0.3.0.md) closed `Status: shipped` with full PR sequence and post-tag closure recorded in [`journal/2026-04-26-v0.3.0-released`](./.cortex/journal/2026-04-26-v0.3.0-released.md). The v0.2.4 → v0.2.5 init UX patch series ([`init-ux-fixes-from-touchstone.md`](./.cortex/plans/init-ux-fixes-from-touchstone.md)) shipped 2026-04-25. Phases A (foundation + spec) and B (walking-skeleton CLI — `init` / `status` / `doctor` / `manifest` / `grep` / `promote` stub + `doctor --audit`) shipped as v0.1.0 on Homebrew; currently on v0.3.0. The remaining roadmap to v1.0 is sequenced as **six release-driven sub-sections** under a single forcing function: install Cortex on a real project, work for a week, no surprises (full rationale in [`.cortex/journal/2026-04-24-production-release-rerank.md`](./.cortex/journal/2026-04-24-production-release-rerank.md), supersedes the 2026-04-23 phase reorder for sequencing decisions). The **v0.9.0 dogfood target is touchstone** (`autumngarage/touchstone`, locked 2026-04-24 per [`.cortex/journal/2026-04-24-dogfood-target-touchstone.md`](./.cortex/journal/2026-04-24-dogfood-target-touchstone.md)) — chosen over conductor (the case study subject) because composition validation between sibling autumngarage tools is a stronger v1.0 gate than verifying a single known incident; the conductor case study ([`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`](./docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md)) still informs trust-layer design but the v0.9.0 test exercises the *class* of fix on a sibling tool rather than the specific incident.

**v0.3.0** ships the write-side foundation: `cortex journal draft <type>`, `cortex plan spawn`, the `release` journal type + T1.10 release-event Protocol/SPEC amendment + audit (case-study items #1 + #2; smallest opening PR), and a `cortex doctor` orphan-deferral check. **v0.4.0** ships the read-side foundation: deterministic `cortex refresh-state` (with `<!-- cortex:hand -->` marker preservation + idempotency), `cortex next` deterministic MVP (case-study item #4), and `cortex plan status`. **v0.5.0** ships the trust + automation layer: Touchstone post-merge hook (auto-drafts `pr-merged`), `cortex doctor --audit-instructions` (case-study item #3 — the across-the-fourth-wall claim audit), and Manifest `Verified:` per-fact (case-study item #5). **v0.6.0** ships the lifecycle layer: `.cortex/.index.json` writer + `cortex refresh-index`, `cortex promote <id>` real writer, and the remaining `cortex doctor` invariant expansions (append-only, immutable-Doctrine, promotion-queue, single-authority, T1.4 audit, claim-trace). **v0.9.0** is the engineering release-gate — Cortex installed on touchstone; one week of real work; zero crashes; Cortex stays out of Touchstone-managed write paths; user assessment "I'd rather use this than hand-write" — with bug fixes shipping as v0.9.x point releases. **v1.0.0** is ceremonial: SPEC.md freeze (drop `-dev`, bump to 1.0.0), README / PITCH refreshed, Homebrew formula update, GitHub Release covering the full arc.

LLM-additive features (`cortex refresh-map`, `cortex refresh-state --enhance`, `cortex next --enhance`) and triad-mode infrastructure (`.cortex/pending/` + `cortex doctrine draft` + T1.7 Touchstone pre-merge hook) are deliberately **deferred from the v1.0 path** to v1.x — the conductor case study evidence is that prose polish hides staleness, so synthesis ships only after the trust layer (`--audit-instructions`, `Verified:`) has dogfooded. See [`plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)` for the full deferral list with revisit conditions per item.

The spec at v0.4.0-dev is a draft. See [`docs/PRIOR_ART.md`](./docs/PRIOR_ART.md) for the research synthesis behind the design, and the `.cortex/journal/` directory in this repo for a dogfood trail of the design decisions (especially `2026-04-17-vision-v3-promoted.md` for the full provenance).

---

## License

MIT.
