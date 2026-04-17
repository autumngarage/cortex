# Phase B plan refreshed for v0.3.1-dev scope

**Date:** 2026-04-18
**Type:** decision
**Trigger:** T1.1 (diff touches `.cortex/plans/phase-b-walking-skeleton.md`, `SPEC.md`, and related canonical files)
**Cites:** plans/phase-b-walking-skeleton, doctrine/0003-spec-is-the-artifact, doctrine/0005-scope-boundaries-v2, journal/2026-04-17-protocol-sharpened-and-drafts-archived, journal/2026-04-17-vision-v3-promoted

> The Phase B walking-skeleton plan was refreshed to cover the v0.3.0/v0.3.1-dev scope (manifest, grep, expanded doctor checks, T1.9 audit, Goal-hash verification, Load-priority, interactive flow, § 4.4 promotion-link enforcement). The spec bumped to 0.3.1-dev in the same PR as a clarification patch resolving an internal § 4.4/§ 3.5 contradiction. This entry records the refresh decisions and serves as the SPEC § 4.2 resolution target for deferred Follow-ups in the plan.

## Context

Phase A shipped the spec and repo on 2026-04-17. Vision v3 was promoted later that day. The Protocol was sharpened the same evening (see `journal/2026-04-17-protocol-sharpened-and-drafts-archived.md`). The Phase B plan — authored before those amendments — had fallen behind the spec. The refresh PR (`chore/refresh-phase-b-plan`) brings the plan up to v0.3.1-dev's command surface and doctor check catalog. This entry is written during the PR cycle; the PR's merge is not yet recorded here because Journal is append-only and a pre-merge claim that "this merged" could become false.

## Key decisions captured in the plan

1. **`cortex init` ships `.cortex/protocol.md` + full `.cortex/templates/`** as Python package data copied verbatim into target projects. This repo is the single source of truth for protocol text; projects customize per Protocol § 6.
2. **`cortex manifest --budget <N>` is first-class.** Not deferred to later phases — the Protocol § 1 read contract depends on it. Default allocations match Protocol § 1 table.
3. **`cortex grep` is a frontmatter-aware ripgrep wrapper.** Primary mid-session retrieval path. Semantic search stays out of Cortex per Doctrine 0005 #1.
4. **`cortex doctor` is modular: one pure-function check per SPEC § 4 rule.** New rules = new check module, no cross-cutting changes. Catalog includes structural, seven-field metadata, plan grounding, deferral tracking, success-criteria, typed-link (with new § 4.4 `Promoted-to:` scope enforcement), promotion-queue, single-authority, Goal-hash verification, Load-priority (scoped to Accepted Doctrine only), append-only Journal, immutable Doctrine (evaluated against parent-commit Status, not current), and CLI-less fallback warnings.
5. **`cortex doctor --audit` walks git log `HEAD~N..HEAD` (N=20 default)** verifying Tier 1 triggers (T1.1–T1.9) produced Journal entries. `--audit-digests` picks N random claims and traces each to a source entry.
6. **Dogfood gate: `cortex doctor` must exit 0 on this repo's own `.cortex/`, not just on a fresh init scaffold.** This is how we prevent spec-drift between the canonical doc and the implementation.
7. **Interactive `cortex` is the primary surface** per the README UX example. `--status-only` and `--promote <id>` for scripting.
8. **CLI v0.1.0 targets spec v0.3.1-dev.** Per Doctrine 0003, CLI and spec versions are independent; first CLI release carries the PLAN.md-numbered tag (v0.1.0), not the spec tag (v0.3.1-dev).

## Deferred Follow-ups — § 4.2 resolution

Per SPEC § 4.2, every deferral from a Plan must resolve to another Plan or a Journal entry in the same commit. The refresh PR's Follow-ups section defers these items; each resolves to this Journal entry, which carries the deferral's context and the triggering condition for future resolution:

- **Map and State regeneration** → Phase C. When Phase B ships (v0.1.0 CLI release targeting spec v0.3.1-dev), a `plans/phase-c-first-synthesis.md` will be authored. Until then, Map/State are hand-authored or stubs per SPEC § 3.2/§ 3.3 (the `Incomplete:` field makes this explicit). Trigger: Phase B exit criteria met.
- **`cortex plan spawn`, `cortex journal draft`** → Phase D. Authoring helpers that take a synthesis round. A `plans/phase-d-authoring-helpers.md` will be authored when Phase C exits. Trigger: first stable synthesis commands in Phase C.
- **Sentinel / Touchstone integration hooks** → Phase E. The triad composition seam. A `plans/phase-e-triad-integration.md` will be authored when Phase D exits. Trigger: authoring helpers stable; time to wire the three tools together.
- **Auto-update check** → out-of-scope for v0.1.0. Will become relevant only if the first out-of-band CLI bug makes manual `brew upgrade` friction-heavy. Resolution at that point: a new Journal entry proposing the auto-update approach, promoted to a Plan if warranted. Trigger: first reported out-of-band bug.
- **`cortex migrate-spec`** → out-of-scope until the first spec-major-bump event (`1.0.0` or later). The pre-1.0 exception in SPEC § 7 lets minor/patch bumps land without migration tooling; only a major bump requires it. Trigger: proposing a `1.0.0` release.
- **`cortex grep` semantic mode** → permanently out-of-scope per Doctrine 0005 #1 (Cortex is not a vector store). This is not a deferral — it's a declined feature. Removed from the plan's Follow-ups on reflection; noted here for completeness. Trigger: none (would require superseding Doctrine 0005).

## Consequences / action items

- [x] Phase B plan reflects the full v0.3.1-dev command surface.
- [x] SPEC § 4.4 clarified to resolve the § 3.5 append-only contradiction; § 4.6 typed-links list tightened.
- [x] `cortex doctor` check catalog covers every enforceable SPEC § 4 / § 5 rule.
- [ ] Phase C plan authored when Phase B ships (trigger: CLI v0.1.0 released).
- [ ] Phase D plan authored when Phase C ships.
- [ ] Phase E plan authored when Phase D ships.

## What we'd do differently

- **Codex pre-merge review caught 20+ issues across 5 review iterations on this plan-refresh PR.** Without it, every single one would have shipped as drift between the spec text and either the plan or itself. The cumulative-edit pattern (make a change; something else in the spec now contradicts; review catches it; fix that; now a third thing contradicts; repeat) is the exact failure mode Cortex is trying to solve for the *agent + project* — and the review loop is what keeps the spec coherent for now, pending Phase B's `cortex doctor --strict` taking over.
- **Spec-amendment PRs should write a deferral-resolution Journal entry as their first commit, not their last.** The § 4.2 rule was the single issue Codex found that didn't surface until round 5. Having the Journal entry in place before authoring the plan's Follow-ups section would have caught it earlier.
