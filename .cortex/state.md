---
Generated: 2026-04-27T22:05:34-04:00
Generator: cortex refresh-state v0.3.0
Sources:
  - HEAD sha: eb1a9821550521e0ed624c22b26395ed2bf48f40
  - .cortex/plans/*.md (9 files)
  - .cortex/journal/*.md (30 entries, 2026-04-17..2026-04-27)
  - .cortex/doctrine/*.md (5 entries)
  - .cortex/templates/**/*.md (11 templates)
  - docs/case-studies/*.md (1 case studies)
  - SPEC version: 0.4.0-dev
  - pyproject.toml + cortex package version: 0.3.0 / 0.3.0
Corpus: 30 Journal entries, 9 Plans, 5 Doctrine entries, 11 Templates, 1 Case studies
Omitted:
  - .cortex/.index.json — absent; promotion queue index ships in a later lifecycle tier
Incomplete:
  []
Conflicts-preserved: []
Spec: 0.4.0-dev
---

# Project State

<!-- cortex:hand -->
## Current work

**Single active plan:**
- [`plans/cortex-v1.md`](./plans/cortex-v1.md) (Ship Cortex v1.0) — the parent plan, sequenced as **feature-readiness tiers** (Tier 1 templated/manual → Tier 2 state self-maintains → Tier 3 automatic-on-merge → Tier 4 lifecycle complete → three-target dogfood gate → v1.0 ceremony). Tier 1 shipped at v0.3.0; **Tier 2 (state self-maintains: deterministic `refresh-state`, `cortex next` MVP, `cortex plan status`) is the next active tier, ships as v0.4.0**. **Dogfood gate target: conductor + touchstone + vesper (simultaneous)** — three different content shapes, three edit cadences, two ecosystems (Python + Swift). Supersedes the earlier touchstone-only target; rationale in this session's reflection — wider evidence, better-designed `[audit-instructions]` config, stronger v1.0 production claim.

- **Tier 1 ✅ shipped at v0.3.0.** `cortex journal draft <type>` keystone (PR #45), `cortex plan spawn <slug>` (PR #46), T1.10 release-event Protocol trigger + `release` journal template + audit (PR #44), `cortex doctor` orphan-deferral check (PR #47), v0.3.0 release (PR #48). Tag `v0.3.0`, [GitHub Release](https://github.com/autumngarage/cortex/releases/tag/v0.3.0), `release.yml` auto-bumped the homebrew-cortex formula. Closure record: [`journal/2026-04-26-v0.3.0-released.md`](./journal/2026-04-26-v0.3.0-released.md).
- **Tier 2 — State self-maintains (v0.4.0).** Deterministic `cortex refresh-state` with `<!-- cortex:hand -->` marker preservation + idempotency; `cortex next` deterministic MVP; `cortex plan status`. Exit bar: refresh-state byte-identical on unchanged inputs; cortex next produces a non-empty ranked list with stable citations; plan status flags any active plan staler than 14 days.
- **Tier 3 — Automatic on merge (v0.5.0). The inflection point.** Touchstone post-merge hook auto-drafts `pr-merged` entries on every default-branch merge; `cortex doctor --audit-instructions` actively catches stale CLAUDE.md / README claims about external artifacts; manifest `Verified:` per-fact surfaces stale derived-fact warnings inline. After this tier, "install Cortex and walk away" is a real claim. Exit bar: ≥ 5 auto-drafted pr-merged entries on this repo; `--audit-instructions` produces unambiguous output (clean exit reports "checked N claims, all verified" — never silent).
- **Tier 4 — Lifecycle complete (v0.6.0).** `.cortex/.index.json` writer + `cortex refresh-index` + `cortex promote <id>` real writer (replacing today's stub) + remaining `cortex doctor` invariant expansions (append-only, immutable-Doctrine, promotion-queue, single-authority, T1.4 audit, claim-trace). Exit bar: end-to-end promotion (journal → doctrine via `cortex promote`) succeeds on this repo's index.
- **Three-target dogfood gate (v0.9.0).** Install Cortex on **conductor**, **touchstone**, and **vesper** simultaneously; sustained period of real work on each using `cortex journal draft` for new entries; `cortex doctor --audit-instructions` exercised against each target's external-artifact claims; ≥ 1 case-study-style journal entry per target capturing surfaced friction. Exit bar: zero crashes on any target; Cortex stays out of Touchstone-managed write paths on every target; user assessment "I'd rather use this than hand-write" on each target. Bug fixes ship as v0.9.x point releases.
- **v1.0.0 — Production release.** SPEC.md freeze (drop `-dev`, bump to 1.0.0); README / PITCH refreshed to tell the production-ready story grounded in the three-target dogfood; Homebrew formula update; GitHub Release covering the full Tier-1 → v1.0 arc.

Deferred from the v1.0 path (parked in [`plans/cortex-v1.md`](./plans/cortex-v1.md) `## Follow-ups (deferred)` with explicit revisit conditions): LLM features (`refresh-map`, `refresh-state --enhance`, `cortex next --enhance`); triad-mode infrastructure (`.cortex/pending/` + `cortex doctrine draft` + T1.7 Touchstone pre-merge hook); Sentinel end-of-cycle hook; Touchstone pre-push `--strict`; interactive per-candidate prompts; doctor audits for runtime-state triggers (T1.2 / T1.6 / T1.7).
<!-- cortex:end-hand -->

## Active plans

- `cortex-v1` — Ship Cortex v1.0; Goal-hash `9e961737`; 21% complete (7/34 checkboxes)

## Shipped recently

- **2026-04-25** — plans/init-ux-fixes-from-touchstone shipped → Status: shipped (`.cortex/journal/2026-04-25-init-ux-fixes-plan-shipped.md`, Type: plan-transition)
- **2026-04-25** — v0.2.4 and v0.2.5 released — init UX patch series complete (`.cortex/journal/2026-04-25-v0.2.4-and-v0.2.5-released.md`, Type: pr-merged)
- **2026-04-26** — Cortex v0.3.0 released — write-side foundation (`.cortex/journal/2026-04-26-v0.3.0-released.md`, Type: release)
- **2026-04-27** — Mainline landing — Production readiness audit hardening (`.cortex/journal/2026-04-27-production-readiness-audit-hardening.md`, Type: pr-merged)

## Stale-now / handle-later

- none
