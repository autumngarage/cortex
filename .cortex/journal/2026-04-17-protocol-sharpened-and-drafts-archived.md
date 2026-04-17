# Protocol sharpened, templates shipped, vision drafts archived

**Date:** 2026-04-17
**Type:** decision
**Trigger:** T1.1 (diff touches .cortex/protocol.md, SPEC.md, .cortex/doctrine/)
**Cites:** drafts/vision-draft-v3.md, journal/2026-04-17-vision-v3-promoted, journal/2026-04-17-competitive-positioning-and-claude-code-risk, doctrine/0001-0004

> Afternoon/evening session bundled three related improvements before Phase B kickoff: three Protocol/SPEC amendments that resolved real load-bearing gaps, dogfood templates for every Tier 1 trigger, and archival of the vision drafts with supersede banners so new sessions don't mistake them for current guidance.

## Context

Earlier today: vision v3 promoted into canonical artifacts (SPEC v0.2.0-dev, protocol.md v0.1.0, README rewrite, doctrine 0004). State.md enumerated Phase B's open items and handle-later list. Reviewing the freshly-landed artifacts before starting Phase B surfaced four concrete problems:

1. **Protocol § 1 "semantic relevance" contradicts Doctrine 0004 #1 ("not a vector store").** The manifest was specified to load "Top-K Doctrine by semantic relevance to current task," but Cortex storage has no embeddings and at session start there is no "current task" yet. Internal contradiction between the Protocol and the scope boundary.
2. **Protocol § 1 assumes the CLI exists.** A project that imports `@.cortex/protocol.md` into `AGENTS.md` without installing the CLI can't follow the Protocol — the manifest command doesn't exist. This contradicts the "spec is implementable without the CLI" property that makes the distribution-race story viable (see `journal/2026-04-17-competitive-positioning-and-claude-code-risk.md`).
3. **No Tier 1 trigger for "PR merged."** The canonical "this shipped" team-shared event had no Journal entry trigger. T1.3 (plan transition) and T1.8 (commit-message pattern) are near-misses — a PR can merge without a plan change, and commit-pattern matching is fuzzy.
4. **Goal-hash normalization was deferred to Phase B** (state.md open question), which left SPEC § 4.9 with a hand-wave for a field the Protocol references.

Separately:

5. **Templates directory (SPEC § 2, Protocol § 5) declared but not populated.** This repo's own `.cortex/` was not spec-conformant — a dogfood violation.
6. **Vision drafts at repo root unlabeled.** `vision-draft.md`, `vision-draft-v2.md`, `vision-draft-v3.md` were flagged for archival in state.md but still present without supersede markers. Risk: a fresh session reads them as current guidance.
7. **Strategic content in v3 § 8 and § 10.4** (competitive matrix, Claude Code existential risk) not captured in any canonical file. Would be lost on archival.

## What we decided

All seven addressed in one PR on branch `feat/sharpen-protocol-and-archive-drafts`:

### Protocol amendments (`.cortex/protocol.md`)

- **§ 1 rewritten.** Removed "semantic relevance." Default Doctrine loading is `Load-priority: always` pins plus recency by `Date:`. Mid-session retrieval is grep (or `cortex grep` wrapper). Explicit fallback documented for projects without the CLI: `AGENTS.md` imports `@.cortex/protocol.md` + `@.cortex/state.md`; grep covers the rest; `cortex doctor` warns when the fallback is insufficient for corpus size.
- **§ 2 Tier 1: added T1.9 (PR merged to default branch).** Template: `journal/pr-merged.md`. Fires on every merge by default; project-configurable to only fire on architecturally-significant merges.
- **§ 5 templates list** updated to include `journal/pr-merged.md`.

### SPEC amendments (`SPEC.md`)

- **§ 3.1 Doctrine header convention** gains `Load-priority:` field (`always` | `default`). Rationale in a trailing paragraph: `always` pins reserved for load-bearing claims every session needs. `cortex doctor` flags over-pinning.
- **§ 4.9 Goal-hash normalization** concretized: lowercase H1 title, strip non-`[a-z0-9 ]`, collapse whitespace, `sha256[:8]`. Illustrative example included. Explicit non-semantic choice (no embeddings — would require vector store, out of scope per Doctrine 0004).
- **§ 2 directory layout** adds `pr-merged.md` to the templates/journal listing.

### Doctrine (`.cortex/doctrine/000{1,2,3,4}`)

All four existing entries backfilled with `Load-priority: always`. At 4 entries they fit comfortably under the 3k manifest budget; as Doctrine grows, some will become `default`.

### Templates (`.cortex/templates/`)

Eight files shipped for every template Protocol § 5 references:

- `journal/decision.md`, `journal/incident.md`, `journal/plan-transition.md`, `journal/sentinel-cycle.md`, `journal/pr-merged.md`
- `doctrine/candidate.md`
- `digest/monthly.md`, `digest/quarterly.md`

Each template has required frontmatter and section scaffolds with `{{ ... }}` placeholders. Kept terse — these are scaffolds, not essays.

### Strategic content captured

New journal entry `2026-04-17-competitive-positioning-and-claude-code-risk.md` preserves v3 § 8 (competitive matrix: Letta, Claude Code, Cursor, Mem0, Graphiti, LangGraph, LangSmith, AGENTS.md, `.brain/`) and § 10.4 (Claude Code existential risk framing) before archival. Also names the quarterly re-assessment cadence and watch-items for Letta and Anthropic memory-roadmap signals.

### Drafts archived

`vision-draft.md`, `vision-draft-v2.md`, `vision-draft-v3.md` moved to `drafts/` via `git mv`. Each gains a supersede banner at the top linking to the canonical artifact(s) that replaced its content and to the competitive-positioning journal entry for the un-promoted strategic content.

Doctrine 0004's `Promoted-from:` field updated to point at the new path (`drafts/vision-draft-v3.md § 9`).

## Consequences / action items

- [x] Protocol § 1 no longer contradicts Doctrine 0004. Semantic retrieval is explicitly a read-side optional layer outside the Protocol.
- [x] Spec is now implementable without the CLI — the distribution-race floor is durable.
- [x] Merge events produce durable Journal entries via T1.9; the audit story now covers ratification, not just in-flight work.
- [x] `Goal-hash:` has a concrete, reproducible normalization that `cortex doctor` can verify without ambiguity.
- [x] This repo's `.cortex/` is now spec-conformant (templates populated, Doctrine `Load-priority:` fields present).
- [x] Strategic content preserved at a grep-findable location before archival.
- [x] Drafts are labeled — a fresh session reading `drafts/vision-draft-v3.md` sees the supersede banner first.
- [ ] Phase B plan (`.cortex/plans/phase-b-walking-skeleton.md`) needs an update: add `cortex manifest`, `cortex grep`, `cortex doctor --verify-goal-hash`, the templates-shipped-with-init requirement. Deferred to next session when we formally open Phase B.
- [ ] First external-project dogfood (Sentinel or Touchstone) will stress-test the new T1.9 trigger and the manifest fallback path. Expect at least one follow-up spec bump.

## Codex pre-merge review round (same PR)

Pre-merge Codex review on branch `feat/sharpen-protocol-and-archive-drafts` caught seven concrete issues on the first iteration; all fixed in the follow-up commit before the PR landed:

1. **Version bumps missing.** Layer contract + validation rule changes required a SPEC bump and a Protocol bump. Fixed: SPEC 0.2.0-dev → **0.3.0-dev** (`.cortex/SPEC_VERSION` updated; all example frontmatter `Spec: 0.2.0` → `Spec: 0.3.0`); Protocol 0.1.0 → **0.2.0**. README, state.md, PLAN.md, phase-b plan all updated accordingly.
2. **SPEC § 5.1 retention table stale.** Still said "Top-K by semantic relevance loads at session start." Rewrote to `Load-priority: always` pins + recency; cross-referenced Protocol § 1 and Doctrine 0004.
3. **SPEC § 5.5 failure-modes list stale.** "Semantic retrieval beyond" → "grep (or an optional external index) beyond."
4. **Doctrine 0004 #1 not updated.** Still claimed `cortex manifest` does semantic top-K. This was the actual contradiction the PR claimed to fix. Rewrote to state the manifest uses recency + `Load-priority` pins; semantic retrieval is an optional external layer.
5. **Goal-hash example didn't match the spec.** SPEC § 4.9 showed `Goal-hash: 6f2d9a1c` but `sha256("sharpen cortexs vision")[:8]` is `1cc12b25`. Fixed and verified with Python.
6. **`phase-b-walking-skeleton.md` Goal-hash was still a slug** (`phase-b-walking-skeleton-cli-v02`). Recomputed: `1f10782a`.
7. **`vision-sharpening.md` Goal-hash was still a slug** (`sharpen-cortex-vision-2026-04-17`). Recomputed: `adf7ee92`.

Lesson embedded: **when adding a validation rule, audit every existing artifact against the new rule in the same PR** — Codex caught two plans that would have failed `cortex doctor --verify-goal-hash` on their first run. This is an application of the audit-weak-points principle (`principles/audit-weak-points.md`) to spec amendments: find all instances of the pattern, not just the one you added.

### Codex round 2 (same PR, second iteration)

After the first fix commit, Codex found five more issues — including one that was the exact invariant the PR claimed to be reinforcing:

1. **Doctrine 0004 content changed in place.** The first fix commit edited 0004 #1's body text. Doctrine is immutable-with-supersede ([SPEC § 3.1](../../SPEC.md) + Protocol § 4.2). Correct response: revert 0004 to its on-main state, write **Doctrine 0005** (`doctrine/0005-scope-boundaries-v2.md`) that supersedes 0004 with the corrected #1 language, flip 0004's Status to `Superseded-by 0005`, and update Protocol/SPEC/README references to point at 0005. This is the disciplined supersede path; the PR now demonstrates it rather than bypassing it.
2. **SPEC § 3.5 Journal `Type:` enum didn't include `pr-merged`.** The new `journal/pr-merged.md` template uses `Type: pr-merged` but a spec-aware validator would reject it. Fixed by adding `pr-merged` to the enum.
3. **State.md corpus count wrong.** Said 9 Journal entries; actual count is 8. Generated-layer provenance (even hand-authored during Phase A/B) must match reality or the seven-field contract is a lie. Fixed corpus count and doctrine count (5 now: 0001–0003, 0004 superseded, 0005 active).
4. **PLAN.md and `phase-b-walking-skeleton.md` still referenced spec v0.1.0.** The Phase B plan instructed `cortex init` to scaffold spec v0.1.0, and the plan's success criteria required "spec v0.1.0 conformant." Both contradicted the bumped SPEC. Fixed to v0.3.0-dev / v0.3.0.
5. **CLI release-tag churn.** First fix bumped state.md's "v0.2.0 release" → "v0.3.0 release" to match the spec. But PLAN.md separately tracks CLI releases (Phase B → v0.1.0, Phase C → v0.2.0, etc.) independent of spec version, per Doctrine 0003. Reverted state.md to "v0.1.0 release (targets spec v0.3.0-dev)" to match PLAN.md's CLI progression and to reinforce that CLI and spec versioning are explicitly independent.

The invariant-violation catch (#1) is the important one: even the PR shipping the sharpened Protocol violated the invariants the Protocol enforces. This is exactly what `cortex doctor --strict` will catch in the triad configuration (Protocol § 1 + SPEC § 3.1 + SPEC § 4.2). Until then, Codex pre-merge review is the enforcement layer — and it works.

## What we'd do differently

- **The Doctrine 0004 / Protocol § 1 contradiction was visible in v3 before promotion** — "not a vector store" and "semantic relevance to current task" co-existed in the same document and neither reviewer (Codex round 2, user read-through) caught it. Future critique prompts should explicitly ask *"does any rule in this document contradict the scope boundaries?"* That's a cheap high-value check.
- **Shipping the templates-directory as part of v0.2.0 promotion would have been better** than needing a same-day follow-up PR. Lesson: when the spec references a directory of files, populating at least one stub of each is part of the promotion, not a later-phase task.
- **Pre-merge code review caught seven issues the author missed.** The Codex round produced a better spec in one review iteration than the entire drafting session produced. This is direct evidence for the Phase E value proposition — Touchstone pre-merge Codex review is not ceremony, it's load-bearing quality. When Cortex integrates with Touchstone (Phase E, T1.7), Doctrine candidates drafted inline at review time will ride this same mechanism. See [journal/2026-04-17-competitive-positioning-and-claude-code-risk.md](./2026-04-17-competitive-positioning-and-claude-code-risk.md) for why "enforced institutional memory" depends on this seam.
