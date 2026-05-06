---
Generated: 2026-05-06T13:15:47-04:00
Generator: cortex refresh-state v0.8.3
Sources:
  - HEAD sha: 8cebe3c71d4f50b62c01658640bfb18e0a89cff9
  - .cortex/plans/*.md (10 files)
  - .cortex/journal/*.md (60 entries, 2026-04-17..2026-05-06)
  - .cortex/doctrine/*.md (7 entries)
  - .cortex/templates/**/*.md (12 templates)
  - docs/case-studies/*.md (1 case studies)
  - SPEC version: 0.5.0
  - pyproject.toml: 0.8.3 + cortex package version: 0.8.3
Corpus: 60 Journal entries, 10 Plans, 7 Doctrine entries, 12 Templates, 1 Case studies
Omitted:
  []
Incomplete:
  - .cortex/journal/2026-05-03-pr-merged-2039.md — shipped journal title contains unresolved template placeholder
  - .cortex/journal/2026-05-03-pr-merged-2057.md — shipped journal title contains unresolved template placeholder
  - .cortex/journal/2026-05-03-pr-merged-2119.md — shipped journal title contains unresolved template placeholder
Conflicts-preserved: []
Spec: 0.5.0
---

# Project State

<!-- cortex:hand -->
## Current work

**One launch roadmap:**
- [`plans/cortex-v1.md`](./plans/cortex-v1.md) (Ship Cortex v1.0) — the master sequence: **shipped readiness tiers → retrieval interface → v0.9.0 three-target dogfood gate → v1.0 ceremony + compatibility**. The next concrete action lives in that plan's `## Pickup pointer` section.
- [`plans/cortex-retrieve.md`](./plans/cortex-retrieve.md) — Cortex-side retrieval sub-plan is shipped and retained for design history. Downstream Sentinel consumption is tracked in autumngarage/sentinel#111, not as a Cortex blocker.

This file (`.cortex/state.md`) and [`plans/cortex-v1.md`](./plans/cortex-v1.md) are the **canonical** answers to "where are we" and "what's next" per [Doctrine 0007](./doctrine/0007-canonical-ownership-of-state-and-plans.md). Repo-root duplicates (ROADMAP.md, STATUS.md, PLAN.md, NEXT.md) are anti-pattern — README links here instead.

**Status by stage:**
- **Readiness tiers through lifecycle are shipped.** Tier 1, Tier 2, Tier 3 Cortex-side, and Tier 4 lifecycle are closed; release details live in the generated `## Shipped recently` section below and the cited release journals.
- **Retrieval interface is Cortex-side shipped.** BM25, semantic, hybrid, and stable JSON output are available from the Cortex CLI. The remaining Sentinel Planner hookup is downstream work in autumngarage/sentinel#111.
- **Release integrity is shipped at v0.8.2.** cortex#107 is closed: the corrective `v0.8.2` tag contains matching package metadata, release.yml verifies tag metadata before Homebrew tap bump, Homebrew upgraded to 0.8.2, and `cortex version` reports 0.8.2 from `/opt/homebrew/bin/cortex`. <!-- cortex:no-stale-check -->
- **v0.9.0 — three-target dogfood gate** is the active next stage. The outward-facing positioning paragraph plus conductor and touchstone installs are complete; next is installing Cortex on **vesper**. Validate fresh-clone session start, bare-repo degradation, retrieval on each corpus, sustained work on the Touchstone-managed targets, and behavioral exit gates. Cortex production readiness must not depend on Sentinel being installed.
- **v1.0.0 — production release.** Ceremony plus pre-1.0 compatibility audit against v0.3 / v0.5 scaffolds, `.cortex/config.toml` schema reference doc, SPEC-to-test traceability matrix, README/PITCH refresh.

**Deferred from v1.0** (full list with revisit conditions in [`plans/cortex-v1.md`](./plans/cortex-v1.md) `## Follow-ups (deferred)`): LLM polish features (`refresh-map`, `refresh-state --enhance`, `cortex next --enhance`); triad-mode infrastructure; Sentinel end-of-cycle hook; Touchstone pre-push `--strict`; interactive per-candidate prompts; doctor audits for runtime triggers (T1.2/T1.6/T1.7); single-authority drift + full claim-trace doctor checks; MCP transport surface; standalone `cortex import-knowledge` command; doctrine-conflict resolution in `cortex promote`; manifest detection edge-case test fixtures; retention/cleanup destructive automation. **Note:** retrieval was previously in this list — promoted to v0.7.0 on 2026-05-02 because grep alone doesn't scale past ~100 entries on real-project corpora.

## Open questions

- (none currently — open questions surface here when work raises a decision that needs deferral or research)
<!-- cortex:end-hand -->

## Active plans

- `cortex-v1` — Ship Cortex v1.0; Goal-hash `9e961737`; 78% complete (36/46 checkboxes)

## Shipped recently

- **2026-04-25** — plans/init-ux-fixes-from-touchstone shipped → Status: shipped (`.cortex/journal/2026-04-25-init-ux-fixes-plan-shipped.md`, Type: plan-transition)
- **2026-04-25** — v0.2.4 and v0.2.5 released — init UX patch series complete (`.cortex/journal/2026-04-25-v0.2.4-and-v0.2.5-released.md`, Type: pr-merged)
- **2026-04-26** — Cortex v0.3.0 released — write-side foundation (`.cortex/journal/2026-04-26-v0.3.0-released.md`, Type: release)
- **2026-04-27** — Mainline landing — Production readiness audit hardening (`.cortex/journal/2026-04-27-production-readiness-audit-hardening.md`, Type: pr-merged)
- **2026-04-28** — Cortex v0.5.0 released — Tier 2 + Tier 3 + partial Tier 4 (`.cortex/journal/2026-04-28-cortex-v050-released-tier-2-tier-3-partial-tier-4.md`, Type: release)
- **2026-04-28** — Cortex v0.5.1 — autumn-mail dogfood polish patch (`.cortex/journal/2026-04-28-cortex-v051-autumn-mail-dogfood-polish-patch.md`, Type: release)
- **2026-05-02** — Cortex v0.6.0 released — Tier 4 closed (real promote writer + 9 doctor invariants + Doctrine 0007) (`.cortex/journal/2026-05-02-v0.6.0-released.md`, Type: release)
- **2026-05-02** — Cortex v0.7.0 released — `cortex retrieve --mode bm25` over FTS5 (Slice S1 of the retrieve interface) (`.cortex/journal/2026-05-02-v0.7.0-released.md`, Type: release)
- **2026-05-03** — Cortex v0.8.0 released — `cortex retrieve` semantic + hybrid (S2) + schema-validator drift fixes (`.cortex/journal/2026-05-03-v0.8.0-released.md`, Type: release)
- **2026-05-04** — PR #109 merged — fix stale cortex state guidance (`.cortex/journal/2026-05-04-pr-merged-0710.md`, Type: pr-merged)
- **2026-05-04** — PR #111 merged — fix release metadata integrity checks (`.cortex/journal/2026-05-04-pr-merged-0728.md`, Type: pr-merged)
- **2026-05-04** — PR #113 merged — docs(journal): record v0.8.2 release (`.cortex/journal/2026-05-04-pr-merged-0737.md`, Type: pr-merged)
- **2026-05-04** — Cortex v0.8.1 released — auto-draft substitution + stale-checkbox detector + append-only false-positive fix (`.cortex/journal/2026-05-04-v0.8.1-released.md`, Type: release)
- **2026-05-04** — Cortex v0.8.2 released — corrective release integrity patch (`.cortex/journal/2026-05-04-v0.8.2-released.md`, Type: release)
- **2026-05-05** — PR #118 merged — docs: add cortex positioning paragraph (`.cortex/journal/2026-05-05-pr-merged-0900.md`, Type: pr-merged)
- **2026-05-05** — PR #122 merged — docs: record conductor cortex install (`.cortex/journal/2026-05-05-pr-merged-0924.md`, Type: pr-merged)
- **2026-05-05** — PR #126 merged — docs: record touchstone cortex install (`.cortex/journal/2026-05-05-pr-merged-0956.md`, Type: pr-merged)
- **2026-05-06** — Cortex v0.8.3 released — installable baseline for vesper dogfood (`.cortex/journal/2026-05-06-cortex-v083-released-installable-baseline-for-vesp.md`, Type: release)
- **2026-05-06** — PR #133 merged — docs(journal): record v0.8.3 release (`.cortex/journal/2026-05-06-pr-merged-0810.md`, Type: pr-merged)
- **2026-05-06** — PR #144 merged — docs(plan): tick v0.9.0 vesper install checkbox (`.cortex/journal/2026-05-06-pr-merged-0835.md`, Type: pr-merged)
- **2026-05-06** — PR #149 merged — docs(journal): record v0.9.0 retrieval validation findings (`.cortex/journal/2026-05-06-pr-merged-1251.md`, Type: pr-merged)
- **2026-05-06** — PR #150 merged — test(acceptance): fresh-clone session-start fixture covers manifest/next/doctor (`.cortex/journal/2026-05-06-pr-merged-1311.md`, Type: pr-merged)

## Stale-now / handle-later

- none
