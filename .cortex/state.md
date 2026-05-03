---
Generated: 2026-05-02T16:48:02-07:00
Generator: cortex refresh-state v0.5.1
Sources:
  - HEAD sha: be8891383e0791482f182c78c540eeb3521c88b5
  - .cortex/plans/*.md (10 files)
  - .cortex/journal/*.md (37 entries, 2026-04-17..2026-05-02)
  - .cortex/doctrine/*.md (7 entries)
  - .cortex/templates/**/*.md (11 templates)
  - docs/case-studies/*.md (1 case studies)
  - SPEC version: 0.5.0
  - pyproject.toml: 0.5.1 + cortex package version: 0.5.1
Corpus: 37 Journal entries, 10 Plans, 7 Doctrine entries, 11 Templates, 1 Case studies
Omitted:
  []
Incomplete:
  []
Conflicts-preserved: []
Spec: 0.5.0
---

# Project State

<!-- cortex:hand -->
## Current work

**One launch roadmap:**
- [`plans/cortex-v1.md`](./plans/cortex-v1.md) (Ship Cortex v1.0) — the master sequence: **Tier 1 → Tier 4 → v0.7.0 retrieve → v0.9.0 three-target dogfood gate → v1.0 ceremony + compatibility**. The next concrete action lives in that plan's `## Pickup pointer` section.
- [`plans/cortex-retrieve.md`](./plans/cortex-retrieve.md) — sub-plan for v0.7.0 (sqlite-vec + fastembed retrieval interface). Moved onto the launch path 2026-05-02. Status: active. Design + slice details live in this sub-plan; sequencing lives in the master plan.

This file (`.cortex/state.md`) and [`plans/cortex-v1.md`](./plans/cortex-v1.md) are the **canonical** answers to "where are we" and "what's next" per [Doctrine 0007](./doctrine/0007-canonical-ownership-of-state-and-plans.md). Repo-root duplicates (ROADMAP.md, STATUS.md, PLAN.md, NEXT.md) are anti-pattern — README links here instead.

**Status by stage:**
- **Tier 1 ✅ shipped at v0.3.0.**
- **Tier 2 ✅ shipped at v0.5.0.**
- **Tier 3 ✅ shipped at v0.5.0 (Cortex side).** Touchstone post-merge hook is a v0.9.0 item.
- **Tier 4 ✅ shipped at v0.6.0** (2026-05-02). Real `cortex promote <id>` writer + 9 doctor invariant checks (incl. canonical-ownership warning per Doctrine 0007). Tag `v0.6.0` + GitHub Release published. Closure: [`journal/2026-05-02-v0.6.0-released`](./journal/2026-05-02-v0.6.0-released.md).
- **v0.7.0 — retrieval interface (S1 ✅ shipped at v0.7.0; S2 + S3 in flight).** S1 = `cortex retrieve --mode bm25` over FTS5 — shipped 2026-05-02 (PR #83 + tag v0.7.0). S2 = semantic + hybrid via sqlite-vec + fastembed — brief at `briefs/v0.7.0-S2-retrieve-semantic-hybrid.md`, dispatched via codex. S3 = Sentinel-consumer acceptance proof — cross-repo work in `~/repos/sentinel`. Closure (S1): [`journal/2026-05-02-v0.7.0-released`](./journal/2026-05-02-v0.7.0-released.md).
- **v0.9.0 — three-target dogfood gate** (next active stage after v0.8.0 ships). Install on **conductor + touchstone + vesper** with overlapping active use. Touchstone post-merge hook canary on Cortex first (cross-repo work in `~/repos/touchstone`, brief at `briefs/v0.9.0-touchstone-post-merge-hook.md`), then validate across targets. Plus fresh-clone acceptance test per target, bare-repo degradation fixture, retrieval validation per target, behavioral exit gates.
- **v1.0.0 — production release.** Ceremony plus pre-1.0 compatibility audit against v0.3 / v0.5 scaffolds, `.cortex/config.toml` schema reference doc, SPEC-to-test traceability matrix, README/PITCH refresh.

**Deferred from v1.0** (full list with revisit conditions in [`plans/cortex-v1.md`](./plans/cortex-v1.md) `## Follow-ups (deferred)`): LLM polish features (`refresh-map`, `refresh-state --enhance`, `cortex next --enhance`); triad-mode infrastructure; Sentinel end-of-cycle hook; Touchstone pre-push `--strict`; interactive per-candidate prompts; doctor audits for runtime triggers (T1.2/T1.6/T1.7); single-authority drift + full claim-trace doctor checks; MCP transport surface; standalone `cortex import-knowledge` command; doctrine-conflict resolution in `cortex promote`; manifest detection edge-case test fixtures; retention/cleanup destructive automation. **Note:** retrieval was previously in this list — promoted to v0.7.0 on 2026-05-02 because grep alone doesn't scale past ~100 entries on real-project corpora.

## Open questions

- (none currently — open questions surface here when work raises a decision that needs deferral or research)
<!-- cortex:end-hand -->

## Active plans

- `cortex-retrieve` — `cortex retrieve` — semantic retrieval as an opt-in derived layer; Goal-hash `b57f6355`; 100% complete (0/0 checkboxes)
- `cortex-v1` — Ship Cortex v1.0; Goal-hash `9e961737`; 29% complete (14/49 checkboxes)

## Shipped recently

- **2026-04-25** — plans/init-ux-fixes-from-touchstone shipped → Status: shipped (`.cortex/journal/2026-04-25-init-ux-fixes-plan-shipped.md`, Type: plan-transition)
- **2026-04-25** — v0.2.4 and v0.2.5 released — init UX patch series complete (`.cortex/journal/2026-04-25-v0.2.4-and-v0.2.5-released.md`, Type: pr-merged)
- **2026-04-26** — Cortex v0.3.0 released — write-side foundation (`.cortex/journal/2026-04-26-v0.3.0-released.md`, Type: release)
- **2026-04-27** — Mainline landing — Production readiness audit hardening (`.cortex/journal/2026-04-27-production-readiness-audit-hardening.md`, Type: pr-merged)
- **2026-04-28** — Cortex v0.5.0 released — Tier 2 + Tier 3 + partial Tier 4 (`.cortex/journal/2026-04-28-cortex-v050-released-tier-2-tier-3-partial-tier-4.md`, Type: release)
- **2026-04-28** — Cortex v0.5.1 — autumn-mail dogfood polish patch (`.cortex/journal/2026-04-28-cortex-v051-autumn-mail-dogfood-polish-patch.md`, Type: release)

## Stale-now / handle-later

- none
