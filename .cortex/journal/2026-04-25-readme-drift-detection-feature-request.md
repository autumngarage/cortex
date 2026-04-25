# README drift detection — feature request + UX recommendation

**Date:** 2026-04-25
**Type:** decision
**Trigger:** T2.1 (user phrased a feature request and agreed on UX shape: "one thing i want cortext to do is auto update the readme file or at least be able to ask the user if they want to update" → "okay sounds good" on the recommended UX)
**Cites:** ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md, plans/cortex-v1, journal/2026-04-24-production-release-rerank, journal/2026-04-24-dogfood-target-touchstone, journal/2026-04-24-init-ux-fixes-plan-decision, ../../principles/documentation-ownership.md, doctrine/0005-scope-boundaries-v2

> Captures a feature request raised mid-session ("Cortex should auto-update the README, or at least ask") and the agreed UX shape: **detect always, prompt on demand, never auto-rewrite prose**. Pre-formalization placeholder — to be written up as a plan (`plans/readme-drift-detection.md` or absorbed into [`plans/cortex-v1`](../plans/cortex-v1.md) v0.5.0 scope) after the in-flight v0.2.4 init-UX work lands.

## Context

The 2026-04-24 conductor case study ([`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`](../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md)) established that stale instruction-file claims confidently steer agents wrong. The case-study-driven roadmap synthesis ([`journal/2026-04-24-case-study-driven-roadmap`](./2026-04-24-case-study-driven-roadmap.md)) named `cortex doctor --audit-instructions` (v0.5.0) as the *detection* answer. But the v1.0 path has no *correction* answer — once the audit warns about stale README content, the user has to fix it by hand.

The user raised this gap mid-session: *"one thing i want cortext to do is auto update the readme file or at least be able to ask the user if they want to update."* The right UX framing was discussed and agreed: **detect always, prompt on demand, never auto-rewrite prose.**

This entry captures the feature-request + the agreed UX shape so the design isn't lost between now (mid-session) and the moment a real plan can be filed (after the in-flight `plans/init-ux-fixes-from-touchstone.md` v0.2.4 work ships and the next planning cycle opens).

## What we decided

**Recommended UX: three integrated surfaces, conservative defaults.**

### Surface 1: `cortex doctor --audit-instructions` extended to README

- The v0.5.0 [`plans/cortex-v1`](../plans/cortex-v1.md) work item already audits `CLAUDE.md` / `AGENTS.md` for stale external-artifact claims. **Widen the audited surface set to include `README.md`** with the same primitives (Brew tap exists, PyPI package present, sibling-repo refs valid, version strings match, "planned for X" claims still planned).
- Detection only — surfaces drift as warnings ("README:42 claims version 0.2.3 but pyproject.toml is 0.2.4"; "README:88 says `cortex journal draft` deferred but `--help` shows it shipped"; "README:150 mentions Homebrew tap as planned but `brew tap-info` shows it's live").
- Pure read-side, deterministic, cheap. Scope-bump on the existing v0.5.0 work item — not a new feature.

### Surface 2: `cortex doctor --fix` (new flag)

- Walks each detected drift item and offers per-item Y/n with a **proposed exact-substitution diff** the user approves.
- **Only offers fixes for deterministic substitutions** (version bumps, dead-link removal, "planned for X" → "shipped in X" where the substitution is mechanical). For fuzzy/prose drift, stays as a warning with no autofix offered.
- **The diff preview is non-negotiable.** User always sees the proposed before/after before approving. Even for "obvious" version bumps.
- New scope; could ride along in v0.5.0 or land as v0.5.1. Estimated: ~200-400 LOC + tests.

### Surface 3: Drift surfaced at natural workflow moments — never blocks

- **Bare `cortex` status command** adds one line when drift exists: *"README has 3 stale claims — run `cortex doctor --fix` to review."* Already on the user's path at session start (per current bare `cortex` behavior).
- **Touchstone post-merge hook** (already v0.5.0) emits the same nudge after a merge that touched a version-bearing file (`pyproject.toml`, `__init__.py`, `package.json`, `Cargo.toml`, `Gemfile`).
- Both purely informational, never block. The nudge is "you might want to run `--fix`," not "you must."

### Explicitly excluded

- **`cortex refresh-readme --enhance` (LLM-driven)** — same defer-to-v1.x park as `refresh-state --enhance` and `cortex next --enhance` ([`journal/2026-04-24-production-release-rerank`](./2026-04-24-production-release-rerank.md) #2 and #7). LLM polish over potentially-stale base is the conductor failure mode in reverse: agent confidently overwrites nuanced human prose with mechanical regeneration. Same risk class as the prose-polish-hides-staleness lesson the case study taught.
- **Auto-write without confirmation** — even for "obvious" deterministic fixes. The diff preview is non-negotiable. The reasoning: README is *project-owned*, not Cortex-owned (per [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) and `principles/documentation-ownership.md`); Cortex shouldn't autonomously write to project-owned files. Asking is cheap; the user is at the keyboard whenever they care.
- **Continuous background watching** — no daemon, no file-watcher, no IDE integration. Drift surfaces at session start (manifest), via explicit `cortex doctor`, or via post-merge hook. That's enough.

## Why this UX vs. the alternatives

| Considered | Rejected because |
|---|---|
| Auto-update README on detection | Conductor failure mode in reverse: confidently overwrites nuanced human prose with mechanical regeneration. README is project-owned. Asking costs near-zero. |
| LLM `refresh-readme` | Same defer-to-v1.x reasoning as `refresh-state --enhance`. Polished-but-wrong > visibly-stale (case study). |
| Block merges on README drift | Friction in the wrong place. Drift detection is informational; merge-blocking belongs to actual contract violations (orphan deferrals, immutable-Doctrine mutation, etc.). |
| Silent `cortex grep`-style detection | Unsurfaced drift = unfixed drift. The bare `cortex` nudge is the load-bearing part. |
| Section-by-section template regeneration with `<!-- cortex:hand -->` markers (like `refresh-state`) | Heavier than needed. README sections don't have the same "always-regenerate this part, never touch that part" structure that `state.md` has. The deterministic-substitution model is simpler and matches actual drift cases. |

## Consequences / action items

- [x] This journal entry captures the request + UX shape so it isn't lost.
- [ ] After [`plans/init-ux-fixes-from-touchstone`](../plans/init-ux-fixes-from-touchstone.md) v0.2.4 ships and the touchstone re-test is clean, formalize this into a plan: either `plans/readme-drift-detection.md` (standalone, ~3 work items) OR scope-bump on [`plans/cortex-v1`](../plans/cortex-v1.md) v0.5.0's `--audit-instructions` work item with a new `--fix` work item alongside.
- [ ] At plan-write time: decide whether the README-audit extension fits inside v0.5.0 or warrants v0.5.1. The Surface 1 widening is small (just include README.md in the audit walk); Surface 2 (`--fix` flag) is larger; Surface 3 (status nudge) is tiny.
- [ ] At plan-write time: confirm that the deterministic-substitution model handles enough real cases on touchstone + cortex's own README to be valuable, or whether some prose-fix cases want the LLM `--enhance` path despite the case-study warning. Lean: stay deterministic-only; if v0.5.0 dogfood surfaces frustration, revisit at v0.6.0.

## What this forecloses

**No autonomy without diff preview, ever.** This entry establishes a posture for any future "Cortex updates project-owned files" feature: the user always sees the proposed change before it lands, even when the change is obvious. The conductor case study proves that "obvious" is exactly when agents (and tools) get it most confidently wrong. Future similar features (auto-update CHANGELOG, auto-add to AGENTS.md command list, etc.) inherit this constraint.

**Detection is the load-bearing primitive; correction is the convenience layer.** If we had to pick only one to ship, it's detection. Correction is a UX win on top of detection — but detection alone is already valuable (the v0.5.0 trust layer was built on this premise). Future scope-decisions in this area should ask "does this make detection more reliable?" before "does this make correction more powerful?"
