# Cortex — Build Plan

> **Status:** active
> **Written:** 2026-04-17
> **Owner:** henrymodisett
> **Spec:** [SPEC.md](./SPEC.md) v0.1.0

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

1. `cortex init` produces a `.cortex/` scaffold that matches [SPEC.md](./SPEC.md) v0.1.0.
2. `cortex refresh-map` regenerates `map.md` from the repo's code + git state in under 60s, using the `claude` CLI for synthesis.
3. `cortex refresh-state` regenerates `state.md` from Sentinel run journals + open Plans + current metrics, surfacing stale-beyond-threshold metrics.
4. `cortex journal draft <type>` emits a draft journal entry from PR context (diff + description + commit messages), ready for human edit.
5. `cortex plan spawn <name>` emits a Plan scaffold citing the grounding Doctrine/State entry.
6. `cortex status` reports freshness per layer and flags spec violations (orphan deferrals, unlinked plans, missing success criteria).
7. Sentinel integration: Sentinel's end-of-cycle hook writes a Journal entry on significant events; Sentinel's scan phase reads Doctrine + State as input.
8. Touchstone integration: Touchstone's PR-merge hook drafts a Journal entry for merges that match an "architecturally significant" trigger.

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

### Phase B — Walking skeleton

Commands that manipulate `.cortex/` structure but don't synthesize.

- [ ] `cortex init` — creates `.cortex/` scaffolding in the CWD per spec v0.3.0-dev (doctrine/, empty map.md + state.md stubs with headers, plans/, journal/, procedures/, templates/, protocol.md, SPEC_VERSION file)
- [ ] `cortex status` — reports freshness per layer (by parsing `Generated:` headers), flags orphan deferrals and unlinked plans (reads and validates, no synthesis)
- [ ] `cortex version` — prints Cortex CLI version and spec version supported
- [ ] `cortex doctor` — health check (valid .cortex? spec version match? required files present?)
- [ ] Tests: structural validation, init idempotence, status detection of each spec-violation class
- [ ] v0.1.0 release, brew tap wired, `brew install autumngarage/cortex/cortex` works

**Exit criterion:** a Cortex-shaped `.cortex/` can be created, inspected, and validated by the CLI with zero LLM calls.

### Phase C — First synthesis: Map

The first command that actually thinks.

- [ ] `cortex refresh-map` — regenerates `map.md` from: directory tree, package metadata (pyproject/package.json/Cargo.toml/go.mod), language-aware boundary detection (Python packages, JS/TS modules), recent git log, and existing Doctrine entries. Uses `claude` CLI for prose synthesis. Writes `Generated:` header with source list.
- [ ] `cortex refresh-state` — regenerates `state.md` from: `.sentinel/runs/*` if present (metrics, phase timings, costs), open Plans' status blocks, recent Journal entries. Falls back to git/PR state if no Sentinel present.
- [ ] Budget handling: CLAMPED_TIMEOUT pattern borrowed from Sentinel
- [ ] Tests against Sentinel's repo (dogfood): regenerate Map, human reviews
- [ ] v0.2.0 release

**Exit criterion:** run it on Sentinel's codebase; the generated `map.md` is within one edit-pass of being useful.

### Phase D — Plans and Journal

The two layers where humans and agents both write.

- [ ] `cortex plan spawn <name>` — creates `plans/<name>.md` with the Required Sections from spec v0.1.0, prompts for grounding citation (Doctrine/State ref), fills in a template skeleton from LLM suggestion
- [ ] `cortex journal draft <type>` — generates a draft journal entry from PR/commit context. Types: decision, incident, migration, reversal, promotion. Human edits before landing.
- [ ] `cortex plan status` — parses checkboxes, reports per-plan completion %, surfaces stalled plans (no updates in N days with open items)
- [ ] Tests for each command against mocked and real repos
- [ ] v0.3.0 release

**Exit criterion:** running on sigint, a month of manual plan-writing could be replaced with `cortex plan spawn` + targeted human edits.

### Phase E — Integration

Cross-tool composition via file contracts (no code coupling).

- [ ] Sentinel: `sentinel work` end-of-cycle optional hook — writes a Journal entry for cycles that shipped a PR or flagged a significant lens finding. Behind `--journal` flag initially; default-on later.
- [ ] Sentinel: scan phase reads `.cortex/doctrine/` + `.cortex/state.md` as additional context when present. Graceful-degrade if absent.
- [ ] Touchstone: optional PR-merge hook (`hooks/cortex-journal.sh`) — drafts a Journal entry when a merged PR matches "significant decision" shape (migration complete, architecture change, revert). Opt-in per project via `.touchstone-config`.
- [ ] Claude Code skill: `cortex-context` — on session start, reads `.cortex/doctrine/` + `state.md` and emits a compact context block for the session.
- [ ] Tests for each integration path
- [ ] v1.0.0 release

**Exit criterion:** Sentinel cycles that ran against a Cortex-enabled project are demonstrably cheaper (fewer tokens on discovery) and the resulting Journal entries are usable without hand-cleanup >80% of the time.

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

**Phase A shipped.** Current P0 is **Phase B — walking-skeleton CLI**, tracked as a full Cortex Plan at [`.cortex/plans/phase-b-walking-skeleton.md`](./.cortex/plans/phase-b-walking-skeleton.md). See also [`.cortex/state.md`](./.cortex/state.md) for the live operational view.

First concrete task in Phase B: scaffold the Python package (`pyproject.toml`, `src/cortex/__init__.py`, `src/cortex/cli.py` entrypoint) and implement `cortex version` + `cortex init`. Tests and release wiring follow. No LLM calls in Phase B — synthesis starts in Phase C.
