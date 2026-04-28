---
Generated: 2026-04-28T14:18:38-04:00
Generator: cortex refresh-state v0.5.1
Sources:
  - HEAD sha: 35655994858b94f55de41ae74f1da8c39f17cd32
  - .cortex/plans/*.md (9 files)
  - .cortex/journal/*.md (33 entries, 2026-04-17..2026-04-28)
  - .cortex/doctrine/*.md (5 entries)
  - .cortex/templates/**/*.md (11 templates)
  - docs/case-studies/*.md (1 case studies)
  - SPEC version: 0.5.0
  - pyproject.toml: 0.5.1 + cortex package version: 0.5.1
Corpus: 33 Journal entries, 9 Plans, 5 Doctrine entries, 11 Templates, 1 Case studies
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

**Single active plan:**
- [`plans/cortex-v1.md`](./plans/cortex-v1.md) (Ship Cortex v1.0) — sequenced as **feature-readiness tiers** (Tier 1 templated/manual → Tier 2 state self-maintains → Tier 3 automatic-on-merge → Tier 4 lifecycle complete → v0.9.0 three-target dogfood gate → v1.0 ceremony + compatibility). **The very next concrete action lives in the plan's `## Pickup pointer` section** — read that block first on session start.

- **Tier 1 ✅ shipped at v0.3.0.** `cortex journal draft <type>` (PR #45), `cortex plan spawn <slug>` (PR #46), T1.10 release-event trigger + audit (PR #44), `cortex doctor` orphan-deferral check (PR #47). Closure: [`journal/2026-04-26-v0.3.0-released.md`](./journal/2026-04-26-v0.3.0-released.md).
- **Tier 2 ✅ shipped at v0.5.0.** Deterministic `cortex refresh-state` with `<!-- cortex:hand -->` marker preservation + idempotency under `CORTEX_DETERMINISTIC=1`; `cortex next` deterministic MVP (PR #58, v0.5.1 PR #68 added template-placeholder filtering); `cortex plan status` (PR #57). Closure: [`journal/2026-04-28-cortex-v050-released-tier-2-tier-3-partial-tier-4.md`](./journal/2026-04-28-cortex-v050-released-tier-2-tier-3-partial-tier-4.md).
- **Tier 3 ✅ shipped at v0.5.0 (Cortex side).** `cortex doctor --audit-instructions` (PR #56); per-fact `Verified:` per SPEC § 4.3.1 (PR #55). The Touchstone post-merge hook is a v0.9.0 work item per the council resolution — see [`journal/2026-04-28-codesight-cross-pollination-and-council-review.md`](./journal/2026-04-28-codesight-cross-pollination-and-council-review.md).
- **Tier 4 🟡 partial at v0.5.0; v0.6.0 closes the tier — the next active tier.** `.cortex/.index.json` writer + `cortex refresh-index` shipped (PR #59); briefs ready at `briefs/v0.6.0-T2-promote-real-writer.md` (real `cortex promote` writer) and `briefs/v0.6.0-T3-doctor-invariants.md` (doctor invariant expansions — **edit before dispatch** to reflect council-trimmed scope: keep append-only Journal + immutable-Doctrine + T1.4 + promotion-queue + CLI-less-fallback; **add** generated-layer contract validation + `.cortex/config.toml` schema validation + SPEC § 5.1 retention visibility; **defer** single-authority drift + full claim-trace to v1.x).
- **v0.9.0 — three-target dogfood gate.** Install on **conductor + touchstone + vesper** with overlapping active use. First v0.9.0 work item: Touchstone post-merge hook canary on Cortex first, then validate across the three targets. Plus fresh-clone session-start acceptance test per target, bare-repo degradation fixture, behavioral exit gates (no subjective vibe-checks). Plan body has the full ordered checklist.
- **v1.0.0 — production release.** Ceremony plus three council-added compatibility/conformance work items: pre-1.0 compatibility audit against v0.3 / v0.5 scaffolds (file-format protocol blocker), `.cortex/config.toml` schema reference doc, SPEC-to-test traceability matrix (per Doctrine 0003).

**Deferred from v1.0** (full list with revisit conditions in [`plans/cortex-v1.md`](./plans/cortex-v1.md) `## Follow-ups (deferred)`): LLM features (`refresh-map`, `refresh-state --enhance`, `cortex next --enhance` — including DeepSeek's interesting freshness-gating proposal preserved as a deferred note); triad-mode infrastructure; Sentinel end-of-cycle hook; Touchstone pre-push `--strict`; interactive per-candidate prompts; doctor audits for runtime triggers (T1.2/T1.6/T1.7); single-authority drift + full claim-trace doctor checks (council de-scope); MCP transport surface; standalone `cortex import-knowledge` command; doctrine-conflict resolution in `cortex promote`; manifest detection edge-case test fixtures; retention/cleanup destructive automation; version-update nudge.

## Open questions

- (none currently — open questions surface here when work raises a decision that needs deferral or research)
<!-- cortex:end-hand -->

## Active plans

- `cortex-v1` — Ship Cortex v1.0; Goal-hash `9e961737`; 35% complete (14/40 checkboxes)

## Shipped recently

- **2026-04-25** — plans/init-ux-fixes-from-touchstone shipped → Status: shipped (`.cortex/journal/2026-04-25-init-ux-fixes-plan-shipped.md`, Type: plan-transition)
- **2026-04-25** — v0.2.4 and v0.2.5 released — init UX patch series complete (`.cortex/journal/2026-04-25-v0.2.4-and-v0.2.5-released.md`, Type: pr-merged)
- **2026-04-26** — Cortex v0.3.0 released — write-side foundation (`.cortex/journal/2026-04-26-v0.3.0-released.md`, Type: release)
- **2026-04-27** — Mainline landing — Production readiness audit hardening (`.cortex/journal/2026-04-27-production-readiness-audit-hardening.md`, Type: pr-merged)
- **2026-04-28** — Cortex v0.5.0 released — Tier 2 + Tier 3 + partial Tier 4 (`.cortex/journal/2026-04-28-cortex-v050-released-tier-2-tier-3-partial-tier-4.md`, Type: release)
- **2026-04-28** — Cortex v0.5.1 — autumn-mail dogfood polish patch (`.cortex/journal/2026-04-28-cortex-v051-autumn-mail-dogfood-polish-patch.md`, Type: release)

## Stale-now / handle-later

- none
