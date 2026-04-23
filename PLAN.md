# Cortex — Build Plan

> **Status:** active
> **Written:** 2026-04-17
> **Owner:** henrymodisett
> **Spec:** [SPEC.md](./SPEC.md) v0.3.1-dev (current draft)

This plan tracks Cortex from empty repo to first useful release. Each phase ships a coherent slice; nothing is half-done.

---

## Why (grounding)

Manual prior art: sigint's `NEXT_PHASE.md`, `INVESTMENT_THESIS.md`, `*_PLAN.md` set, `COLLECTOR_MIGRATION.md`, and policy docs (`CODEX_AUTOFIX_DISABLED.md`, `API_INTEGRATION.md`) have been maintained by hand for months. Pains observed:

- **Premature-completion declarations** (`COLLECTOR_MIGRATION.md` shipped "complete" Apr 5 but tests weren't running the actual code; fix landed Apr 7 with an AST guardrail)
- **Silent data-flow failures hidden by stale aggregators** (`FIX_DATAFLOW_GAPS_PLAN.md`: 4 days of resolution-pipeline dead while `/sig-status` reported normal)
- **Deferrals scattered without a consolidated queue** (6+ deferred items across plans, tracked by grep)
- **Hard-won lessons buried in CLAUDE.md instead of surfaced near risky code**
- **Plans don't link to the metric they claim to fix** — "did it work?" requires manual digging

Cortex automates the layer taxonomy, enforces the cross-layer rules, and regenerates the Map/State layers so staleness is surfaced not tolerated.

---

## Success Criteria

Cortex v1.0 is done when, on sigint or sentinel:

1. `cortex init` produces a `.cortex/` scaffold that matches [SPEC.md](./SPEC.md) v0.3.1-dev.
2. `cortex refresh-map` regenerates `map.md` from the repo's code + git state in under 60s, using the `claude` CLI for synthesis.
3. `cortex refresh-state` regenerates `state.md` from Sentinel run journals + open Plans + current metrics, surfacing stale-beyond-threshold metrics.
4. `cortex journal draft <type>` emits a draft journal entry from PR context (diff + description + commit messages), ready for human edit.
5. `cortex plan spawn <name>` emits a Plan scaffold citing the grounding Doctrine/State entry.
6. `cortex status` reports freshness per layer and flags spec violations (orphan deferrals, unlinked plans, missing success criteria).
7. Sentinel integration: Sentinel's end-of-cycle hook writes a Journal entry (via `cortex journal draft --type sentinel-cycle`) on significant events; Sentinel's scan phase reads Doctrine + State as input.
8. Touchstone integration: post-merge hook (T1.9) drafts a `Type: pr-merged` Journal entry for every default-branch merge via `cortex journal draft --type pr-merged`; pre-merge hook (T1.7) on architecturally-significant diffs renders the `doctrine/candidate.md` template pre-filled from PR context as a PR comment for the author to hand-author a Doctrine candidate from; pre-push hook runs `cortex doctor --strict`.

**Out of scope for v1.0:** multi-repo / portfolio views (that's the Lighthouse discussion, deliberately deferred); promotion enforcement (the human gates promotions); embedding / semantic search over Cortex content (may come as v1.1).

---

## Approach

Python CLI, same distribution model as Sentinel (uv tool install + brew via `autumngarage/homebrew-cortex`). Synthesis via the `claude` CLI — no SDK, no stored keys, convergent-CLI pattern Sentinel already proves. File I/O-heavy, LLM-light — Cortex's job is mostly structure and regeneration, not raw generation.

**Dogfood target:** Sentinel's repo first (known code, checkable outputs). Touchstone second. Sigint third (the project this tool exists to replace manual work on).

---

## Phases

### Phase A — Foundation ✅ (complete, 2026-04-17)

Ship the repo and the spec, not the tool. Nothing calls an LLM yet.

- [x] Repo `autumngarage/cortex` created
- [x] Bootstrapped with `touchstone new --type python` (dogfoods Touchstone)
- [x] [SPEC.md](./SPEC.md) v0.1.0 — the file-format protocol
- [x] This build plan
- [x] README.md — story, composition, install-pending posture
- [x] `docs/PRIOR_ART.md` — research synthesis backing the spec's design rules
- [x] `CLAUDE.md` / `AGENTS.md` tailored for this project
- [x] Dogfood `.cortex/` inside this repo — Doctrine 0001–0003 + one Journal entry
- [x] Initial commit, pushed to `autumngarage/cortex` main

**Exit criterion met:** spec is readable standalone, plan doc captures Phase B work, repo is on GitHub with the dogfood `.cortex/` validating the spec against a real project.

### Phase B — Walking skeleton *(shipped as v0.1.0 on 2026-04-18)*

Commands that manipulate `.cortex/` structure but don't synthesize. Every line item below shipped and is covered by tests; exit provenance is in [`.cortex/journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md`](./.cortex/journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md).

- [x] `cortex init` — creates `.cortex/` scaffolding per spec v0.3.1-dev
- [x] `cortex status` / bare `cortex` / `--status-only` / `cortex status --json` — structural summary (active plans, journal activity, digest age with overdue flag, promotion-queue counts)
- [x] `cortex version` — prints CLI version, supported SPEC + Protocol versions, install method
- [x] `cortex doctor` — structural validation (scaffold, seven-field metadata, Doctrine / Plan / Journal frontmatter + sections, Goal-hash recomputation)
- [x] `cortex doctor --audit` + `--audit-digests` — first-slice Tier-1 coverage (T1.1 / T1.5 / T1.8 / T1.9) + digest-claim citation sampling
- [x] `cortex manifest --budget N` — token-budgeted session-start slice per Protocol § 1
- [x] `cortex grep <pattern>` — frontmatter-aware `rg --json` wrapper
- [x] `cortex promote <id>` — stub (validates `.index.json` shape; full writer is Phase C)
- [x] Tests: 111 green, temp-dir fixtures, audit tests use real `git init` temp repos
- [x] v0.1.0 release + `autumngarage/homebrew-cortex` tap live; `brew install autumngarage/cortex/cortex` works on macOS

**Exit criterion met:** a Cortex-shaped `.cortex/` can be created, inspected, and validated by the CLI with zero LLM calls; distribution is live.

### Phase C — Authoring and deterministic state *(active, v0.3.0 target)*

Full plan: [`.cortex/plans/phase-c-authoring-and-state.md`](./.cortex/plans/phase-c-authoring-and-state.md). Make journaling cheap and `state.md` self-current so a new or post-crash session picks up where the last one left off. No LLM dependency — every command is deterministic, idempotent, and works on a machine without `claude` installed. Reordered from the original "first synthesis" framing because session-pickup value is unblocked by deterministic writes, not by LLM synthesis; the original Phase C plan ([`.cortex/plans/phase-c-first-synthesis.md`](./.cortex/plans/phase-c-first-synthesis.md)) is cancelled and its items redistributed across new Phase C / D / E, with rationale in [`.cortex/journal/2026-04-23-phase-c-reordered.md`](./.cortex/journal/2026-04-23-phase-c-reordered.md).

- [ ] `cortex journal draft <type>` — pre-fills a journal template from `git log` + `gh pr view` context
- [ ] `cortex plan spawn <slug>` — scaffolds a Plan file with seven-field frontmatter + all required sections + computed Goal-hash
- [ ] `cortex plan status` — per-plan completion + staleness report; `--json` for scripting
- [ ] `cortex refresh-state` (deterministic) — regenerates `state.md` with seven-field header; hand-authored sections between `<!-- cortex:hand -->` markers survive regeneration; byte-identical output on unchanged inputs
- [ ] Tests (real filesystem, real git, no mocked subprocess) + idempotency test on refresh-state
- [ ] v0.3.0 release

**Exit criterion:** for a week after v0.3.0, ≥80 % of new journal entries on this repo are authored via `cortex journal draft` rather than hand-written, and `state.md` can be rebuilt from plans+journal without human edits to the auto-generated sections.

### Phase D — Composition integrations *(blocked on C, v0.4.0 target)*

Full plan: [`.cortex/plans/phase-d-integration.md`](./.cortex/plans/phase-d-integration.md). Sentinel and Touchstone use Phase C's `cortex journal draft` to write to `.cortex/` on real work events — end-of-cycle (T1.6), PR merge (T1.9). For architecturally-significant pre-merge (T1.7), the Protocol names `doctrine/candidate.md` as the template; Phase D renders it pre-filled as a PR comment (no new storage layer until Phase E ships the SPEC amendment + `cortex promote` writer). This is the phase where the composition story (Touchstone = standards, Sentinel = loop, Cortex = memory) starts compounding: the Journal fills itself as a byproduct of normal work instead of requiring the author to remember to record things.

- [ ] Sentinel end-of-cycle hook (in `autumngarage/sentinel` repo) → `cortex journal draft --type sentinel-cycle`
- [ ] Touchstone post-merge hook (in `autumngarage/touchstone`) → `cortex journal draft --type pr-merged`
- [ ] Touchstone pre-merge hook on architecturally-significant diffs → render `doctrine/candidate.md` template filled from PR context, post as PR comment
- [ ] Touchstone pre-push hook → `cortex doctor --strict` (fail-loud gate)
- [ ] Graceful-degradation tests for every integration (Cortex missing, Cortex present but not opted in, Cortex present + opted in)
- [ ] v0.4.0 release

**Exit criterion:** a week of PRs on this repo produces ≥ 5 auto-drafted `pr-merged` journal entries; ≥ 1 Sentinel cycle on this repo produces an auto-drafted cycle entry.

### Phase E — Synthesis and governance *(blocked on D, v1.0.0 target)*

Full plan: [`.cortex/plans/phase-e-synthesis-and-governance.md`](./.cortex/plans/phase-e-synthesis-and-governance.md). The capstone: LLM synthesis layered over the deterministic core; `.cortex/.index.json` writer end-to-end; `cortex promote` writer; every remaining SPEC § 4 cross-layer rule becomes an enforceable `cortex doctor` check. All work items from the cancelled Phase C plan land here (see that plan's Promoted-to for the mapping). The external dogfood gate on Sentinel's repo also moves here — it's where prompt design actually gets exercised, after the authoring / integration loops have already worked on this repo.

- [ ] `.cortex/.index.json` writer + `cortex refresh-index`
- [ ] `cortex refresh-map` — LLM synthesis with seven-field header
- [ ] `cortex refresh-state --enhance` — LLM prose polish over the Phase C deterministic core
- [ ] `cortex promote <id>` — end-to-end promotion (writes Doctrine entry, updates `.index.json`, emits `Type: promotion` Journal entry)
- [ ] `cortex doctor` expansions — orphan-deferral (§ 4.2), append-only violation (§ 3.5), immutable-Doctrine mutation (§ 3.1), promotion-queue invariants (§ 4.7), single-authority-rule drift (§ 4.8), CLI-less-fallback warning (Protocol § 1), Tier-1 audit expansion to T1.2/T1.3/T1.4/T1.6/T1.7, full § 5.4 claim-trace in `--audit-digests`
- [ ] Interactive per-candidate prompts in bare `cortex` (depends on `.index.json` writer)
- [ ] External dogfood gate on a freshly-cloned Sentinel repo
- [ ] v1.0.0 release — SPEC.md frozen at the shipping version

**Exit criterion:** running `cortex refresh-map && cortex refresh-state --enhance && cortex doctor --strict` on a freshly-cloned Sentinel repo produces non-trivial Map/State content + clean exit; SPEC.md has no open `-dev` suffix.

---

## Known Limitations (to be addressed in v1.x)

- **Promotion is manual** — v1.0 does not enforce the Journal-to-Doctrine graduation gate; humans decide and move entries. Automating this is v1.x.
- **No embedding/semantic search** — queries are grep + filename + header parsing. Good enough for one project; doesn't scale to cross-project unless Lighthouse happens (deferred).
- **Single-writer assumption** — two humans or two agents writing `.cortex/` concurrently will conflict. Fine for now (one user, one project at a time); CRDT-ish append-only patterns already help on the Journal specifically.

---

## Follow-ups (deferred)

- **Cortex-as-protocol separation**: if/when Cortex grows other implementations (e.g., a JS reader), extract SPEC.md to its own `autumngarage/cortex-spec` repo. Not needed at one implementation.
- **Portfolio view (Lighthouse)**: `cortex across` that aggregates state.md freshness and blocked priorities across `~/.touchstone-projects`. Explicitly out of scope per the "one project at a time" principle.
- **Embeddings + semantic retrieval**: once `.cortex/` is large enough that grep stops working, add a local embedding index.

---

## Where to start next session

**Phases A and B shipped.** Cortex v0.1.0 is on Homebrew via [`autumngarage/homebrew-cortex`](https://github.com/autumngarage/homebrew-cortex); the full shipped-plan record is in [`.cortex/journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md`](./.cortex/journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md).

**Current P0 is the reordered Phase C — authoring and deterministic state.** Tracked at [`.cortex/plans/phase-c-authoring-and-state.md`](./.cortex/plans/phase-c-authoring-and-state.md) (created 2026-04-23 after a roadmap audit revealed the old Phase C bundled three risk classes and led with LLM synthesis before the features that actually close the session-pickup gap). Ship `cortex journal draft`, `cortex plan spawn`, `cortex plan status`, and a deterministic `cortex refresh-state` — no LLM calls, no `claude` dependency at runtime. Phase D ([`.cortex/plans/phase-d-integration.md`](./.cortex/plans/phase-d-integration.md)) wires those commands into Sentinel and Touchstone hooks; Phase E ([`.cortex/plans/phase-e-synthesis-and-governance.md`](./.cortex/plans/phase-e-synthesis-and-governance.md)) layers LLM synthesis + the remaining SPEC § 4 enforcement. Full rationale for the reorder in [`.cortex/journal/2026-04-23-phase-c-reordered.md`](./.cortex/journal/2026-04-23-phase-c-reordered.md); the old plan ([`.cortex/plans/phase-c-first-synthesis.md`](./.cortex/plans/phase-c-first-synthesis.md)) is marked cancelled with Promoted-to links to the three successor plans.
