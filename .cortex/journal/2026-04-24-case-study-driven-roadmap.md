# Top-5 next items, grounded in the conductor stale-CLAUDE.md case study

**Date:** 2026-04-24
**Type:** decision
**Trigger:** T2.1 (user phrased a prioritization decision: "compile the top 5 next things to work on")
**Cites:** ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md, plans/phase-c-authoring-and-state, plans/phase-e-synthesis-and-governance, plans/phase-d-integration, ../../SPEC.md, ../protocol.md, doctrine/0001-why-cortex-exists, ../../principles/documentation-ownership.md, ../../principles/engineering-principles.md

> A case study landed in `docs/case-studies/` today describing an incident where an agent on the `conductor` repo confidently told the user the Homebrew tap was "planned for v0.1.0 but deferred" — when in fact it had shipped eight releases earlier. The stale claim lived in `CLAUDE.md` and the `README.md` "Deferred" list, both loaded into every session, with no journal trace of the tap going live. The incident exposes five structural Cortex concerns (Documentation Ownership, Derive-don't-persist, missing release-event trigger, no across-the-fourth-wall audit, manifest trusts inputs blindly). This entry captures the top-5 work items that incident argues for, so the synthesis survives to next session.

## Context

The full incident is durable under `docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`. Summary relevant here:

- **What broke.** An agent answered "why do we use pip instead of homebrew" with "Homebrew isn't wired up yet." In reality `autumngarage/homebrew-conductor` had been live since somewhere around v0.2.1, with six release commits spanning v0.2.1 → v0.3.3. The agent only noticed after the user pushed back and an `ls ~/Repos/homebrew-conductor` was run.
- **Why the agent believed the wrong thing.** Two load-bearing files inside `conductor` — `CLAUDE.md` line 65 ("Homebrew formula via `autumngarage/homebrew-conductor` tap (planned for v0.1.0; not yet wired)") and `README.md` Deferred list ("Brew tap for `brew install autumngarage/conductor/conductor`") — both contained a prose claim that stopped being true months earlier. No journal entry recorded the tap shipping; no doctrine pointed at it as canonical; `.cortex/state.md` in that project was still the scaffolded placeholder.
- **What this tells us about Cortex.** The session-pickup contract (manifest loads state.md and doctrine pins as ground truth) only holds if the ground-truth layer stays synced with reality. Nothing in today's Cortex forces that sync when reality lives in a sibling repo or a distribution channel outside `.cortex/`.

The case study itself names four affordances (release-event trigger, instruction-file audit, cross-repo journal import, manifest provenance); this entry picks five priorities that combine those with the existing phase roadmap and one meta-item the user added in conversation today ("I also want Cortex to be doing this automatically").

## What we decided

Top 5 next items, in priority order, with phase homes:

### 1. Finish Phase C (v0.3.0) as planned — add one scope expansion

Already P0 per [`plans/phase-c-authoring-and-state`](../plans/phase-c-authoring-and-state.md). The case study reinforces Phase C's premise: the session-pickup gap is what caused the conductor incident, because when homebrew-conductor shipped there was no write-side machinery to record it in `conductor/.cortex/journal/`. Phase C's `cortex journal draft <type>` is exactly that machinery.

**Scope expansion worth taking on now:** add `release` as a `journal draft` type, with a `journal/release.md` template covering the fields that would have closed this incident (artifact kind — tap / PyPI / Docker / tag; artifact location; release version; link to release notes; "install path this changes" field that names the downstream docs to refresh). Template ships in `.cortex/templates/journal/release.md`. Small addition, case-study-direct, doesn't block the existing plan's exit criteria.

Phase C does **not** absorb T1.10 (see item 2); adding the type as a draftable template is separable from wiring it as a Tier-1 trigger with audit coverage.

### 2. T1.10 release-event trigger — Protocol + SPEC amendment

Add `T1.10: Release / distribution artifact shipped (tag pushed, brew formula updated, PyPI release, Docker image tagged)` to Protocol § 2 with `journal/release.md` as its template. Minor Protocol bump (0.2.0 → 0.2.1). SPEC.md likely needs a corresponding minor bump so `Protocol version` accepted by v0.3.x includes the new trigger. Auditability via `cortex doctor --audit` expansion: for each release event in the window (detected via `git tag --list --sort=-creatordate` within the window, plus optional sibling-repo watching from item 5 when it ships), expect a matching `Type: release` journal entry within 72 h.

Direct case-study remediation. Low cost once the wording is right. Unblocks the natural workflow where every release event leaves a durable audit trail that `refresh-state` and `refresh-map` can both consume.

Belongs in: its own small amendment PR, ideally landing before or during Phase C implementation so the template Phase C ships is already aware of the trigger it backs.

### 3. `cortex doctor --audit-instructions` — the across-the-fourth-wall audit

The biggest lever the case study identifies and the most ambitious item on this list. Scan `CLAUDE.md`, `AGENTS.md`, `README.md`, and similar agent-loaded prose for claims about external artifacts and verify each against the real world:

- Filesystem siblings (`~/Repos/homebrew-<project>` exists? has commits?)
- Published releases (`gh release list` for the expected tap / main repo)
- PyPI presence (`pip show` or simple HTTPS HEAD on the package index)
- Brew tap formulae (`brew tap-info`)
- URL liveness for external links claimed as canonical sources

Needs project-level configuration (`.cortex/config.toml` eventually, or a `cortex:check` frontmatter block in the files being audited) that names the source-of-truth repos/packages for each project. Reports contradictions as warnings, escalates to errors under `--strict` (which Touchstone's pre-push hook would use).

**Phase home: Phase E.** This is synthesis + governance territory and depends on configuration primitives (`.cortex/config.toml`) that we haven't yet designed. Design should start from this case study — file a dedicated plan at Phase E kickoff: `plans/phase-e-fourth-wall-audit.md` (or fold into `phase-e-synthesis-and-governance` as an explicit work item). Cite this journal entry and the case study from that plan.

Two deliberate non-goals, carried directly from the case study's "What this case study is NOT":

- The audit does not police narrative/principles prose. CLAUDE.md legitimately holds judgment and voice; only claims about external artifacts are in scope.
- The audit does not require a release-per-commit hygiene; infrequent release events in small projects are fine if each one leaves a durable journal entry.

### 4. `cortex next` — automate this exact compilation

The user's meta-ask from today's session: "I also want Cortex to be doing this automatically." The top-5 compilation I produced for the user today should be a Cortex command, not a chat answer.

**Deterministic MVP** (fits in or directly after Phase C, no LLM dependency):

- Walk `.cortex/state.md` for P0 / P1 / P2 section headings; extract their plan-file pointers and summarize.
- Walk `state.md` "Open questions" section; list unresolved items.
- Walk `.cortex/plans/*.md` with `Status: active`, count open checkboxes, flag stale plans (reuses `cortex plan status` from Phase C).
- Walk `docs/case-studies/*.md` (when the directory exists) newer than N days (default 30); each new case study becomes a "recent evidence" item with a pointer to `cortex doctor --audit-instructions` once item 3 ships.
- Output: a ranked list with stable citations to each source.

**LLM-enhanced layer** (Phase E, as `cortex next --enhance` or similar): prose synthesis across the deterministic signals, tone calibration, and phase-home recommendations. Deterministic first, LLM additive — matches the pattern already established for `refresh-state` by [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #7 ("LLM is opt-in enhancement, not a default").

Phase home for the MVP: extend Phase C scope, or pull into an immediate follow-up plan. Leaning toward **immediate follow-up plan** rather than Phase C scope creep — Phase C's exit criteria are already well-defined, and layering a new command onto the same release risks blurring the ship criterion. Tentatively `plans/phase-c-followup-cortex-next.md` or bundled into Phase D kickoff.

### 5. Manifest provenance — per-fact `Verified:` metadata

The case study's "Session manifest trusts its inputs" critique. Extend SPEC § 4.3 so derived facts inside `state.md` and `doctrine/*.md` can carry a `Verified: <date>` tag on individual bullets (not just the whole file's `Generated:` timestamp):

```markdown
- Install path: `brew install autumngarage/cortex/cortex` *(Verified: 2026-04-24 against `autumngarage/homebrew-cortex` HEAD)*
```

`cortex manifest` surfaces stale `Verified:` timestamps as warnings inline in the manifest output, so an agent reading the session manifest sees "this fact was last verified 180 days ago" next to the fact itself. Agents inherit skepticism instead of trust by default.

Smaller lift than item 3. Phase home: Phase E, alongside the audit. The two work together — `audit-instructions` is the active check; `Verified:` is the passive freshness signal.

## Consequences / action items

- [x] This journal entry authored.
- [ ] Update [`.cortex/state.md`](../state.md) to surface these five items so next session's `cortex manifest` loads them: add a pointer under `## Shipped recently`, refresh `Sources:` and `Corpus:` counts, add a new `## Case-study-driven follow-ups (2026-04-24)` section with the five items and their phase homes.
- [ ] Decide whether to update [`plans/phase-c-authoring-and-state.md`](../plans/phase-c-authoring-and-state.md) work items to include the `release` journal type + template in this plan's scope, or spin it off into a follow-up plan. Leaning add-to-Phase-C because the template is template-only and doesn't cost the phase's "no LLM dependency" promise. Confirm with Henry before amending.
- [ ] Draft the T1.10 Protocol amendment (item 2) as the first concrete case-study follow-up PR. Small, scoped, lands in its own commit independent of Phase C progress.
- [ ] File [`plans/phase-e-fourth-wall-audit.md`](../plans/) (or expand existing Phase E plan) to name item 3 as a first-class work item with this case study cited as the motivating incident.
- [ ] Decide placement for `cortex next` (item 4): Phase C extension vs. follow-up plan. Prefer follow-up plan to keep Phase C exit clean.

## What this forecloses

The reorder journal on 2026-04-23 named LLM-always as not-Cortex's-posture and made governance a v1.0.0 concern. This entry is consistent with that: every one of the five items above has a deterministic path (even the "audit-instructions" external check is a set of subprocess calls plus grep, not an LLM prompt), with LLM synthesis as the opt-in enhancement layer. The case study's lessons do not reopen the "is every refresh an LLM call?" question; they reinforce the "deterministic first, LLM additive" answer by showing that the most valuable next feature (`audit-instructions`) is itself deterministic.

This entry does **not** foreclose on cross-repo journal import (the case study's fourth affordance). It's omitted from the top-5 because it depends on item 2 (T1.10 has to exist before sibling-repo release events have anywhere to land) and on unresolved design questions (opt-in shape? who owns the watcher process? what happens on network failure?). Expected to resurface as a Phase E+ item once the release-trigger + audit-instructions slices have been dogfooded on this repo and Conductor.

<!--
Optional flags (remove the lines that don't apply):
**failed-approach:** true   # T2.2 — journal a dead-end that taught something
**investigation:** true      # T2.3 — surprise-about-existing-code hypothesis
**inferred-invariant:** true # T2.5 — a constraint the agent is relying on
-->
