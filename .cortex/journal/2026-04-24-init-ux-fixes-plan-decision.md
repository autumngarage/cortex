# Why a separate v0.2.4 patch plan + parked-follow-ups list

**Date:** 2026-04-24
**Type:** decision
**Trigger:** T1.1 (diff touches .cortex/plans/) + T2.1 (user phrased a decision: "lets make a plan to improve and then delegate the coding to codex")
**Cites:** plans/init-ux-fixes-from-touchstone, plans/cortex-v1, journal/2026-04-24-dogfood-target-touchstone, journal/2026-04-24-production-release-rerank, journal/2026-04-24-v1-followups-parked, ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md, doctrine/0001-why-cortex-exists, ../../SPEC.md § 4.2

> Records the decision to file [`plans/init-ux-fixes-from-touchstone`](../plans/init-ux-fixes-from-touchstone.md) as a separate v0.2.4 patch plan rather than adding the work to `plans/cortex-v1.md`. Also serves as the in-tree resolution target for that plan's `## Follow-ups (deferred)` items per SPEC § 4.2.

## Context

The 2026-04-24 touchstone dogfood UX test ran `cortex init -y --path ~/Repos/touchstone` and surfaced two Sev-1 bugs (shallow-Doctrine manifest contamination; CLAUDE.md insertion-placement breaks document outline) plus several smaller bugs. The user's response: *"lets make a plan to improve and then delegate the coding to codex."*

Two structural decisions had to be made before the plan could land:

1. **New plan file vs. cortex-v1 work items.** The bugs are clearly v0.2.x patch material (no new commands, no SPEC text, no Protocol bumps), not v0.3.0+ feature work. They block v1.0-path progress because the v0.9.0 dogfood gate's "install on touchstone, no surprises" exit bar is undermined by install-time bugs that the gate would re-surface. Adding them to `cortex-v1.md` as a v0.2.4 sub-section under `## Work items` would mix patch-release work with feature-release work in one file — fighting the consolidation rationale ([`journal/2026-04-24-single-plan-consolidation`](./2026-04-24-single-plan-consolidation.md)) which collapsed three plans into one *because they shared a release trajectory*. The v0.2.4 patches don't share that trajectory; they're a parallel pre-v0.3.0 ship.

2. **Where the v0.2.4 plan's deferred items resolve.** The plan defers four items (state.md Phase C terminology cleanup; `cortex init --dry-run`; `--audit-instructions` extension to "is Cortex pulling its weight"; stub-Doctrine detection in `cortex doctor`). SPEC § 4.2 requires each to resolve to another Plan or Journal entry in the same commit. The v0.2.4 plan's first draft cited cortex-v1 work items that don't actually exist there — a real orphan-deferral violation Codex caught on review.

## What we decided

**Decision 1: New plan file (`plans/init-ux-fixes-from-touchstone.md`).** Reasoning:

- **Different release trajectory.** v0.2.4 is a patch; v0.3.0 → v1.0.0 is a feature sequence. Mixing them would force every scope decision in either to weigh against the other; separating them lets v0.2.4 ship and close cleanly.
- **Different audience.** v0.2.4 is delegatable to Codex (each work item names files + tests + acceptance assertions). cortex-v1 work items are higher-level and assume judgment calls. The plan files want different shapes.
- **Different lifetime.** v0.2.4's plan closes when the v0.2.4 release ships and the touchstone re-test is clean. cortex-v1 is open until v1.0.0. Separate Status transitions match separate timelines.
- **The 2026-04-24 single-plan-consolidation rationale doesn't apply.** That consolidation collapsed plans that *shared a release trajectory* (Phase C/D/E all heading to v1.0). v0.2.4 doesn't share v1.0's trajectory; it ships first as a patch, then v0.3.0+ resumes. Two plans with different trajectories is correct; two plans with the same trajectory is the bug the consolidation fixed.

**Decision 2: This journal entry resolves the v0.2.4 plan's parked follow-ups.** Reasoning:

- SPEC § 4.2 requires in-tree resolution. The v0.2.4 plan's first draft cited cortex-v1 work items that don't exist there — the `Phase C terminology` follow-up has no matching v0.3.0 work item; `--dry-run` is not in v0.3.0's named scope; the `audit-instructions` "pulling weight" extension is broader than v0.5.0's named scope; stub-Doctrine detection is not in v0.6.0's invariant list. Cleanest fix: this entry parks all four follow-ups with explicit revisit conditions, identical to the [`journal/2026-04-24-v1-followups-parked`](./2026-04-24-v1-followups-parked.md) pattern but scoped to the v0.2.4 plan rather than cortex-v1.
- Co-locating the T1.1 decision artifact and the parked-follow-ups list in one journal entry keeps both close to the source — anyone reading this entry sees both why the plan exists *and* what it deferred.

### Parked v0.2.4 follow-ups (each cited from `plans/init-ux-fixes-from-touchstone` `## Follow-ups (deferred)`)

1. **state.md / map.md Phase C terminology in scaffolded outputs.** The `Generator:` strings shipped in `cortex init` v0.2.3 reference "Phase C" instead of v0.4.0 (refresh-state) / v1.x (refresh-map per the production rerank). The fix is a one-line string change in the init scaffolded-template strings. **Decision: ride along in v0.2.4** — Codex can fold this into the same patch series as the Sev-3 fixes (state.md Sources undercount) since they touch the same scaffolding code. *Revisit conditions: none — ship in v0.2.4.* (Cortex-v1 does not need a corresponding work item; this is a v0.2.4 implementation detail.)

2. **`cortex init --dry-run`** — preview-mode for the install scan-and-absorb decisions before any file is written. Sev-2 bugs (README false-positive, 0001 collision) argue for a preview affordance, but the v0.2.4 fixes (filtering README, starting auto-imports at 0100) reduce that argument's force. *Revisit conditions: post-v0.2.4 re-test on touchstone surfaces a new class of init UX bug that --dry-run would have caught; OR a real external user (not the author) requests it; OR more than one v0.2.x patch ships fixing init-time false positives, suggesting the preview affordance has compounding value.* Re-evaluate at v0.3.0 kickoff; if revisit conditions hold, write a small `plans/init-dry-run.md` plan and ship as v0.3.x.

3. **`cortex doctor --audit-instructions` extension to "is Cortex pulling its weight on this project?"** — current cortex-v1 v0.5.0 scope is external-artifact-claim auditing (Brew tap exists, PyPI package exists, etc.). The Sev-1 #1 bug (shallow Doctrine in manifest) suggests a broader audit shape: detect when `.cortex/` content is structurally redundant with what CLAUDE.md / AGENTS.md already import via `@path` directives and warn. Different shape from external-claim auditing; would warrant its own command (`cortex doctor --audit-value` or similar) or a config-toggled mode of `--audit-instructions`. *Revisit conditions: v0.5.0 ships and `--audit-instructions` works on touchstone; in the same week's dogfood usage, agents demonstrably trust shallow `.cortex/` content over richer CLAUDE.md `@path` imports.* Re-evaluate at v0.5.0 exit-bar review.

4. **Stub-Doctrine detection in `cortex doctor`** — detect Doctrine entries whose body is solely a "See `<path>` for the full content" pattern with no other prose, and flag as `Status: shallow` (excluded from manifest by default). Requires a new `Status:` enum value in SPEC.md and a new doctor invariant. *Revisit conditions: post-v0.2.4 re-test on touchstone produces zero shallow Doctrine entries (the v0.2.4 fixes succeed) AND another project's install surfaces shallow Doctrine that v0.2.4's filters missed.* Re-evaluate during v0.6.0 doctor invariant expansions; if needed, write a SPEC § 3.1 amendment + doctor check together as one unit (same shape as v1.x's `.cortex/pending/` + doctrine-draft + T1.7 unit).

## Consequences / action items

- [x] [`plans/init-ux-fixes-from-touchstone`](../plans/init-ux-fixes-from-touchstone.md) created with seven-field frontmatter, Goal-hash `db2ef686`.
- [x] [`plans/init-ux-fixes-from-touchstone`](../plans/init-ux-fixes-from-touchstone.md) `## Follow-ups (deferred)` cites this journal entry on each of the four items above.
- [x] state.md updated to list two active plans (init-ux-fixes-from-touchstone P0; cortex-v1 P1).
- [ ] Hand off to Codex: `codex exec --full-auto` against the plan's Slice 1 (Sev-1 fixes) as the first PR.
- [ ] After Slice 1 merges + v0.2.4 ships: roll back current touchstone install and re-run `cortex init -y --path ~/Repos/touchstone`; verify Success Criteria 1–7.
- [ ] If re-test clean: close `plans/init-ux-fixes-from-touchstone` (Status: shipped, Promoted-to: a re-test journal entry); resume cortex-v1 v0.3.0 work.
- [ ] If re-test surfaces new bugs: write `plans/init-ux-fixes-v2.md` (or v0.2.5 patch plan), do not extend this plan.

## What this forecloses

**Two-plan parallelism is now in this project's pattern library.** The single-plan consolidation on 2026-04-24 collapsed three plans into one for a single trajectory. This entry establishes the inverse: when a piece of work has a *different* trajectory from the active main-line plan (different release target, different audience, different ship gate), separate plan files are correct. The criterion is shared trajectory, not file count. Future similar decisions cite both this entry (when to split) and `journal/2026-04-24-single-plan-consolidation` (when to merge).

**Deferred-follow-up resolution via co-located decision journal becomes the second pattern.** Previously [`journal/2026-04-24-v1-followups-parked`](./2026-04-24-v1-followups-parked.md) was a standalone "park list" journal that the cortex-v1 plan cited from its `## Follow-ups (deferred)`. This entry combines the T1.1 decision artifact (why the plan exists) with the park-list (what the plan deferred) in one journal — saving a file and keeping both close to source. Future small-plan decisions can use the same combined shape; large-plan decisions (with many follow-ups) probably still warrant a separate park-list journal.
