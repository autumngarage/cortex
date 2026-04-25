# plans/init-ux-fixes-from-touchstone shipped → Status: shipped

**Date:** 2026-04-25
**Type:** plan-transition
**Trigger:** T1.3 (plan Status: active → shipped) + T1.5 (dep manifest changed: cortex 0.2.4 → 0.2.5)
**Cites:** plans/init-ux-fixes-from-touchstone, journal/2026-04-24-init-ux-fixes-plan-decision, journal/2026-04-25-v0.2.4-touchstone-re-test-clean, ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md, plans/cortex-v1

> [`plans/init-ux-fixes-from-touchstone`](../plans/init-ux-fixes-from-touchstone.md) (Goal-hash `db2ef686`) closes today as `Status: shipped` after v0.2.5 verified clean on touchstone via Homebrew install. All five Slices done; both Sev-1 + four Sev-2/3/4 fixes shipped across two patch releases (v0.2.4 + v0.2.5) and verified live on the dogfood target. v0.3.0 work on [`plans/cortex-v1`](../plans/cortex-v1.md) resumes next.

## Transition

- **From:** `Status: active` since 2026-04-24 (filed from yesterday's touchstone dogfood UX test)
- **To:** `Status: shipped` 2026-04-25; `Promoted-to: journal/2026-04-25-init-ux-fixes-plan-shipped` (this entry)
- **Reason:** All five Slices complete + both releases shipped + re-test on touchstone clean against brew-installed v0.2.5

## Outcome against success criteria

The plan's `## Success Criteria` named seven measurable signals. Every one passes against the brew-installed v0.2.5 on touchstone:

| # | Criterion | Result |
|---|---|---|
| 1 | No shallow Doctrine entries imported from Touchstone-managed paths | ✓ — six `principles/*.md` files surface as `touchstone_managed`, zero seeded as Doctrine |
| 2 | `cortex init` does not damage CLAUDE.md / AGENTS.md document outline | ✓ — imports append at end-of-file; no `### sub-heading` reparenting |
| 3 | `principles/README.md` (and any `**/README.md` under a Touchstone-managed dir) filtered from Doctrine | ✓ — `meta_doc` reclassification in v0.2.5 + `touchstone_managed` skip in v0.2.4 (double-coverage on touchstone) |
| 4 | `0001` stays reserved for human-authored "why X exists" entry; auto-imports start at 0100 | ✓ — `_AUTO_IMPORT_DOCTRINE_FLOOR = 100` in `init_seeders.py`; verified on non-Touchstone fixture |
| 5 | Idempotent re-run | ✓ — re-running `cortex init` on touchstone produces no NEW Doctrine entries; existing imports detected, idempotent skip path triggered |
| 6 | Tests for each fix run against real `git init`'d temp repos | ✓ — 218 tests passing across `test_init_touchstone_managed.py` (11 tests) + `test_init_slice_3_cosmetic.py` (4 tests) + updated `test_init_interactive.py`; no mocked filesystem |
| 7 | v0.2.4 release shipped | ✓ — tag, GitHub Release, Homebrew formula update at `9eb4eea`; verified `brew upgrade` works |

Plus: v0.2.5 release (covering Slices 2-3) shipped — tag, [GitHub Release](https://github.com/autumngarage/cortex/releases/tag/v0.2.5), Homebrew formula update at `c5ad634`. `brew upgrade` from v0.2.4 → v0.2.5 verified locally.

## Slices completed

| Slice | Scope | PR | Status |
|---|---|---|---|
| Slice 1 — Sev-1 must-ship | Touchstone-managed Doctrine skip + end-of-file import placement | #34 | shipped (v0.2.4) |
| Slice 4 — v0.2.4 release ritual | Version bump + tag + GitHub Release + Homebrew formula | #35 | shipped (v0.2.4) |
| Slice 5 — Re-test on touchstone (after v0.2.4) | Verified Sev-1 fixes hold | (no PR — test only) | shipped 2026-04-25 (early morning) |
| Slice 2 — Sev-2 ride-along | README filter + 0100-numbering | #37 (bundled with Slice 3) | shipped (v0.2.5) |
| Slice 3 — Sev-3 / Sev-4 cosmetic | state.md sources + inline location ref + Next-steps numbering + `--status-only --path` + Phase C terminology in scaffolds | #37 (bundled with Slice 2) | shipped (v0.2.5) |
| Slice 4-bis — v0.2.5 release ritual | Version bump 0.2.4 → 0.2.5 + tag + GitHub Release + Homebrew formula | #38 | shipped (v0.2.5) |
| Slice 5-bis — Re-test on touchstone (after v0.2.5, brew install) | Verified all fixes hold under brew-installed binary | (no PR — test only) | shipped 2026-04-25 (this entry) |

Two ride-along observations from the closure:

1. **Slices 2 + 3 bundled cleanly** in PR #37 (730 diff lines, near the 600-line "split if exceeds" threshold the plan named — but Codex review caught zero findings on first iteration before timing out at 5 min). The split-or-bundle decision the plan deferred to scope-time landed as bundle.
2. **Plan-internal contradictions caught by Codex** during the planning PR cycle (PR #33 took 5 review rounds for the plan itself) prevented several real bugs from shipping. Pattern worth keeping: when a plan is delegatable to Codex, the same Codex catches plan-internal inconsistencies during review.

## Deferred items resolution

The plan's `## Follow-ups (deferred)` section had four items, all parked in [`journal/2026-04-24-init-ux-fixes-plan-decision`](./2026-04-24-init-ux-fixes-plan-decision.md). Status of each at plan close:

1. **Phase C terminology in scaffolded outputs** — *resolved during Slice 3* (rode along in v0.2.5 as planned). No longer parked.
2. **`cortex init --dry-run`** — still parked. v0.2.5's fixes (README filter, 0100-numbering) reduced the original argument's force; revisit at v0.3.0 kickoff if conditions hold.
3. **`cortex doctor --audit-instructions` extension to "is Cortex pulling its weight?"** — still parked. Revisit at v0.5.0 exit-bar review.
4. **Stub-Doctrine detection in `cortex doctor`** — still parked. Revisit during v0.6.0 invariant expansions.

Items 2-4 stay parked per their original conditions in [`journal/2026-04-24-init-ux-fixes-plan-decision`](./2026-04-24-init-ux-fixes-plan-decision.md); no new park-list journal needed since they roll forward unchanged.

## Consequences / action items

- [x] [`plans/init-ux-fixes-from-touchstone`](../plans/init-ux-fixes-from-touchstone.md) frontmatter: `Status: shipped`, `Promoted-to: journal/2026-04-25-init-ux-fixes-plan-shipped`, all `[ ]` work items marked `[x]`, new `Updated-by` line.
- [x] `.cortex/state.md` `## Current work` updated: only [`plans/cortex-v1`](../plans/cortex-v1.md) remains active (P0); `plans/init-ux-fixes-from-touchstone` moves to "Shipped recently".
- [x] Touchstone working tree at `~/Repos/touchstone` cleaned up (the v0.2.5 install was a re-test only; rolled back so the actual cortex-on-touchstone install can be a deliberate next decision rather than a test artifact).
- [ ] Decide next move on cortex repo: resume v0.3.0 work on [`plans/cortex-v1`](../plans/cortex-v1.md) (the production-release path), OR defer to a touchstone-side PR that commits cortex-on-touchstone for real (the cortex-on-touchstone install we've been testing in throwaway form). User picks.
- [ ] At v0.3.0 kickoff: decide whether `cortex init --dry-run` (parked item #2) gets pulled forward into v0.3.0 scope or stays parked.

## What this forecloses

**The "real UX test" pattern is now ratified as production discipline.** Yesterday's pre-v0.9.0 install on touchstone (running v0.2.3 against the dogfood target before the formal gate) caught two Sev-1 bugs in ~30 minutes that would have undermined the v0.9.0 official gate weeks from now. The plan-then-fix-then-re-test cycle worked as designed: file plan from real evidence (yesterday), implement against the plan (today), re-test on the same target, ship. Future minor releases on the v0.3.0 → v0.6.0 path should bake in a "fresh install on touchstone" pass alongside their release-ritual slice — not a ship-blocker, but a cheap signal source.

**The Slice 1 / Slice 2-3 split landed as the right granularity.** v0.2.4 shipped Sev-1 same-day; v0.2.5 followed within hours bundling Sev-2/3/4. Two small focused releases cost less reviewer time (one Codex-review round each, both clean) than one larger release would have. Sev-1 + Sev-2/3/4 in one PR was probably also viable (Codex didn't error out on the 730-line v0.2.5 PR's diff, just timed out at the 5-min limit), but the split kept release notes legible and made the v0.2.4 → v0.2.5 progression easier to narrate. Pattern worth reusing: split patch-series releases by Sev tier when the touch points permit.

**`plans/cortex-v1` v0.3.0 work resumes from a stronger baseline.** The init UX bugs are gone from v0.2.5, so v0.3.0's first PRs (`release` template + T1.10 + `cortex journal draft`) build on an install path that already proved clean on touchstone. The next "real UX test" against touchstone happens after v0.3.0's keystone PRs land — and this time the test exercises the new write-side commands, not init.
