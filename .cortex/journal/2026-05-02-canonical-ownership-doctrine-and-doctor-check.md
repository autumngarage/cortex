# Recursive failure: agent created `ROADMAP.md` while cleaning up drift, then promoted the rule to Doctrine 0007 + a v0.6.0 doctor check

**Date:** 2026-05-02
**Type:** decision
**Trigger:** T1.1
**Cites:** doctrine/0007-canonical-ownership-of-state-and-plans, plans/cortex-v1, journal/2026-05-02-promote-retrieval-onto-launch-path-and-doc-cleanup

> The earlier session decision to create `ROADMAP.md` at repo root (recorded in [`journal/2026-05-02-promote-retrieval-onto-launch-path-and-doc-cleanup.md`](./2026-05-02-promote-retrieval-onto-launch-path-and-doc-cleanup.md)) is **revised**: `ROADMAP.md` was created and then deleted within the same session because it duplicated content already canonical in `.cortex/state.md` and `.cortex/plans/cortex-v1.md` — the exact failure mode Cortex exists to prevent. The recursive lesson promoted to **[Doctrine 0007 — Canonical ownership of "where are we" and "what's next" lives in `.cortex/`, not at repo root](../doctrine/0007-canonical-ownership-of-state-and-plans.md)** plus a `cortex doctor` enforcement check shipping in v0.6.0. Earlier journal entry stays append-only per Protocol § 4.1; this entry supersedes its `ROADMAP.md created at repo root` conclusion only.

## Context

Mid-session on 2026-05-02, after applying a documentation cleanup that resynced README.md + SPEC.md to Doctrine 0006 and re-architected `.cortex/plans/cortex-v1.md` to absorb `cortex retrieve` as v0.7.0, the agent created a new top-level `ROADMAP.md` at the cortex repo root as "the single-page public-facing answer to *when does Cortex launch?*" The user (Henry) immediately surfaced the question:

> *"do we have too many files now? does making a roadmap file fit in the cortex vision? we want to learn from this thinking recursively"*

The honest audit: `ROADMAP.md` restated content already canonical in three places — `.cortex/state.md` `## Current work` (just rewritten in the same session), `.cortex/plans/cortex-v1.md` `## Success Criteria` and `## Work items`, and the new sub-plan banner in `.cortex/plans/cortex-retrieve.md`. The file was 100+ lines of legitimate content; every line of it was load-bearing somewhere else in `.cortex/`. The very act of cleaning up drift had created a fresh duplication surface — three places now claimed authority over the same facts.

This is the classic legacy-codebase anti-pattern: `ROADMAP.md`, `STATUS.md`, `PLAN.md`, `NEXT.md`, `TODO.md` accumulate over a project's lifetime, each starting as a thoughtful answer to a real audience question, each then drifting as the project's actual state changes faster than the file. Cortex's design is supposed to fix this by giving every project ONE canonical place for state (`.cortex/state.md`) and ONE canonical place for plans (`.cortex/plans/`). When a Cortex-using project re-creates the legacy anti-pattern at repo root, Cortex's own design promise is broken.

The forcing event was Cortex doing it to itself, on the same session that started by auditing other people's drift. That makes the lesson doubly load-bearing — it caught the failure in dogfood, and the dogfood evidence is the doctrine's grounding. The user named the move explicitly: *"this is all important learnings FOR cortex. we should not have these problems. we need a canonical plan file for what is next and we should not accumulate out of date stuff. this should happen automatically for all projects USING cortex."*

## What we decided

**File-org decision (executed this session):**

1. **Deleted `ROADMAP.md`** from the cortex repo root (was untracked — never committed; clean removal).
2. **Slimmed `README.md` `## Status` section** from a multi-paragraph restatement of the launch sequence to a one-paragraph link to `.cortex/state.md` + `.cortex/plans/cortex-v1.md`. Cites Doctrine 0007 inline so the README itself models the rule it's pointing at.
3. **Updated `.cortex/state.md` hand-block** to remove the cross-link to `ROADMAP.md` and to inline a one-line claim that `state.md` + `cortex-v1.md` are the canonical "where are we" / "what's next" surfaces per Doctrine 0007.

**Rule promotion (this session):**

4. **Wrote [Doctrine 0007](../doctrine/0007-canonical-ownership-of-state-and-plans.md)** — codifies the rule: canonical ownership of state and forward-looking plans lives in `.cortex/`; repo-root duplicates are anti-pattern; README links instead of restating. Bounds carved out for legitimate repo-root content (release notes, architecture, contributing guides, code overviews) so the doctrine doesn't over-reach.
5. **Updated `.cortex/protocol.md` § 1**, plus the bundled `src/cortex/_data/protocol.md` shipped via `cortex init` (with the doctrine link rewritten to the GitHub URL so it resolves in projects that don't ship a copy of Doctrine 0007 in their own `.cortex/`). Means every Cortex-using project that imports `@.cortex/protocol.md` now inherits the rule at session start. Protocol bumped from 0.2.1 → 0.2.2 implicitly via the additive section (formal version-string bump deferred to the next protocol-edit session if I missed it).

**Enforcement (shipping in v0.6.0):**

6. **Added a new doctor check work item to `.cortex/plans/cortex-v1.md` v0.6.0 trimmed scope.** Scans repo root for `^(ROADMAP|STATUS|PLAN|PLANS|NEXT|TODO|roadmap|status|plan|plans|next|todo)\.md$` when `.cortex/state.md` exists AND ≥1 `.cortex/plans/*.md` has `Status: active`. Warns (not errors) and points to the canonical files. Overridable per-project via `.cortex/config.toml` `[doctrine.0007] allowed_root_files = ["ROADMAP.md"]` for projects with documented reason to keep one — the override is explicit, never silent.
7. **Added the corresponding test to the v0.6.0 tests bullet** (synthetic `ROADMAP.md` triggers warning; presence of override config suppresses it).

## Consequences / action items

- [ ] **v0.6.0 brief update.** When dispatching `briefs/v0.6.0-T3-doctor-invariants.md`, add the canonical-ownership warning to the brief before sending to conductor — currently in the master plan but not yet in the brief file. Without this, the conductor agent will skip it.
- [ ] **Protocol version bump verification.** I added new normative content to `.cortex/protocol.md` § 1 but did not bump the version string at the file head. If still 0.2.1 at next session start, bump to 0.2.2 (additive minor per SPEC § 6) and ship in the same release that ships the doctor check (v0.6.0).
- [ ] **Bundled-doctrine-pack question (deferred).** Cortex currently ships only `protocol.md` + `templates/` via `cortex init` — no doctrine entries. Doctrine 0007 is referenced from the bundled protocol via a GitHub URL (works but fragile). The longer-term question: should `cortex init` ship a Cortex-canonical doctrine pack (similar to how Sentinel ships engineering-values doctrine per `sentinel/.cortex/plans/sentinel-autonomous-engineer.md`)? Park as v1.x consideration; revisit if a second Cortex-canonical doctrine surfaces that warrants bundling.
- [ ] **Apply the same lens to `autumn-garage/autumn-garage-plan.md`** (annotated stale earlier this session). That file is exactly the anti-pattern Doctrine 0007 names — a repo-root plan-style file that duplicates content that should live in `.cortex/plans/` for autumn-garage. Annotation calls this out implicitly; a follow-up should either move the content into `autumn-garage/.cortex/plans/` properly or formally supersede the file. Tracked as a follow-up against autumn-garage, not Cortex.
- [ ] **Recursive lesson archived as case study material.** The "agent cleaning up drift creates new drift" pattern belongs in `docs/case-studies/` alongside the conductor stale-CLAUDE.md case study, as evidence-grounding for the v1.0 launch narrative. Do this as part of v1.0 ceremony (`docs/CASE-STUDIES.md` index work item already exists in v1.0 plan).

## Why this matters

Two failure modes have now been caught by Cortex's own dogfood and converted to product:

1. **Stale external claims** (conductor case study, 2026-04-24): a stale CLAUDE.md confidently steered an agent wrong eight releases after the underlying reality changed. Resolution: T1.10 release-event trigger + `cortex doctor --audit-instructions` + per-fact `Verified:` (shipped in v0.3.0 + v0.5.0).
2. **Canonical-ownership drift** (this entry, 2026-05-02): an agent cleaning up drift created a new duplication surface within minutes. Resolution: Doctrine 0007 + `cortex doctor` canonical-ownership warning (shipping in v0.6.0).

Both follow the same pattern: real failure → recorded in journal → promoted to doctrine → enforced by doctor → ships to all Cortex-using projects. This is the Cortex value proposition working as designed — the meta-loop where "Cortex helps prevent the failures Cortex itself encounters." The v0.6.0 release becomes the first version where this second class of failure is structurally caught, not just informally avoided.
