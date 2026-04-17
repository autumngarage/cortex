---
Generated: 2026-04-17T23:55:00-04:00
Generator: hand-authored (regeneration infrastructure ships in Phase C)
Sources:
  - HEAD of branch feat/sharpen-protocol-and-archive-drafts
  - .cortex/doctrine/ (4 entries: 0001–0004, all Load-priority: always)
  - .cortex/plans/ (1 active: phase-b-walking-skeleton; vision-sharpening shipped)
  - .cortex/journal/ (9 entries for 2026-04-17)
  - .cortex/templates/ (8 files shipped today)
  - SPEC.md v0.2.0-dev
  - PLAN.md phase-A-complete, phase-B-pending
Corpus: 4 Doctrine, 1 active Plan, 9 Journal entries, 8 Templates
Omitted: []
Incomplete:
  - Map regeneration (Phase C); no Map layer yet
  - Automated metric aggregation (Phase C); State is hand-authored
  - Sentinel run journals (no integration yet; Phase E)
Conflicts-preserved: []
Spec: 0.2.0-dev
---

# Project State

> Vision v3 promoted this afternoon; protocol sharpened and dogfood templates shipped this evening. Phase B (walking-skeleton CLI) is the single open priority. The spec is now internally consistent and implementable without the CLI — the distribution-race floor is durable.

## P0 — Phase B: walking-skeleton CLI + Protocol implementation

Build the CLI structure and non-synthesizing commands so there's something to `brew install`, so later phases have something to extend, and so the Protocol's Tier 1 triggers have a `cortex doctor --audit` to enforce them.

Full plan: [`plans/phase-b-walking-skeleton.md`](./plans/phase-b-walking-skeleton.md) (needs an update pass to reflect new commands below).

**Success signal:** `brew tap autumngarage/cortex && brew install cortex && cortex init` works in a fresh repo and produces a SPEC-v0.2.0-conformant `.cortex/` scaffold including `.cortex/protocol.md` and `.cortex/templates/`, validated by `cortex doctor`.

- [ ] Python package scaffold (`pyproject.toml`, `src/cortex/`, `uv`-managed)
- [ ] `cortex` (interactive entry point) — status + promotion queue + digest prompts (per README example)
- [ ] `cortex init` — scaffolds `.cortex/` per SPEC.md v0.2.0, copying this repo's `.cortex/protocol.md` and `.cortex/templates/` into the target project
- [ ] `cortex manifest --budget <N>` — token-budgeted session-start slice per Protocol § 1; default recency-based Doctrine loading plus `Load-priority: always` pins
- [ ] `cortex grep <pattern>` — frontmatter-aware wrapper over ripgrep; primary mid-session retrieval path per Protocol § 1
- [ ] `cortex --status-only` — equivalent of status summary, for scripting
- [ ] `cortex doctor` — validates `.cortex/` structure against SPEC.md; checks the seven-field metadata contract; validates promotion-queue invariants; flags orphan deferrals, unlinked plans, single-authority-rule violations; verifies `Goal-hash:` fields via SPEC § 4.9 normalization
- [ ] `cortex doctor --audit` — verifies Tier 1 Protocol triggers (T1.1–T1.9) produced entries during the git session window
- [ ] `cortex doctor --audit-digests` — random-sample claim verification on digests
- [ ] `cortex doctor` warning when the CLI-less fallback manifest (per Protocol § 1) is used against a corpus exceeding default thresholds
- [ ] `cortex --promote <id>` — flag-style promotion (interactive flow is the default)
- [ ] `cortex version` — prints CLI version + supported spec + protocol versions
- [ ] Tests for each command (temp-dir fixtures, no mocked filesystem)
- [ ] `autumngarage/homebrew-cortex` tap repo created
- [ ] v0.2.0 release via Homebrew formula pointing at the source tarball

## P1 — Phase C: first synthesis (`cortex refresh-map`, `cortex refresh-state`)

Gated on P0. Not started. Will use the `claude` CLI directly (no SDK, no provider layer). Must emit the seven-field metadata contract per SPEC.md § 4.5.

## P2 — Integration with Sentinel and Touchstone (Phase E)

Gated on P0–D. Critical integrations: Sentinel end-of-cycle → Journal entry (Trigger T1.6); Touchstone pre-merge → Doctrine candidate draft (Trigger T1.7); Touchstone post-merge → T1.9 `journal/pr-merged.md` entry; Touchstone pre-push → `cortex doctor --strict` (the invariant-enforcement story from SPEC.md § 9 and README). Without these, Cortex is useful but not *enforced*.

---

## Shipped recently

- **2026-04-17 (evening)** — **Protocol sharpened, templates shipped, drafts archived.** Three Protocol/SPEC amendments resolved live contradictions and hand-waves: Protocol § 1 rewritten to use `Load-priority: always` + recency (removing the "semantic relevance" contradiction with Doctrine 0004); Protocol § 1 fallback specified for CLI-less projects; T1.9 (PR merged) added to Tier 1; SPEC § 4.9 Goal-hash normalization concretized (lowercase title → sha256[:8]); SPEC § 3.1 Doctrine gains `Load-priority:` field; all four existing Doctrine entries backfilled. Eight templates shipped under `.cortex/templates/` (five journal, one doctrine, two digest). Vision drafts v1/v2/v3 moved to `drafts/` with supersede banners. Strategic content preserved in [`journal/2026-04-17-competitive-positioning-and-claude-code-risk.md`](./journal/2026-04-17-competitive-positioning-and-claude-code-risk.md). Full details in [`journal/2026-04-17-protocol-sharpened-and-drafts-archived.md`](./journal/2026-04-17-protocol-sharpened-and-drafts-archived.md).
- **2026-04-17 (afternoon)** — **Vision v3 promoted.** Cortex Protocol shipped as `.cortex/protocol.md` (two-tier triggers, three invariants, template references). SPEC.md bumped to v0.2.0-dev with seven-field metadata contract, promotion queue operational rules, single authority rule for reads, multi-writer Plan visibility, retention and consolidation section. Doctrine 0004 (scope boundaries) landed. README rewritten. Full provenance in [`journal/2026-04-17-vision-v3-promoted.md`](./journal/2026-04-17-vision-v3-promoted.md).
- **2026-04-17 (morning)** — Phase A complete. Repo bootstrapped, SPEC.md v0.1.0 drafted, PLAN.md + README.md + PRIOR_ART.md + CLAUDE.md + AGENTS.md written, dogfood `.cortex/` populated with three Doctrine entries and one Journal entry. See [`journal/2026-04-17-spec-v0.1.0-drafted.md`](./journal/2026-04-17-spec-v0.1.0-drafted.md).

## Open questions (Phase B kickoff)

- **Python project structure:** src-layout vs. flat? Lean toward `src/cortex/` (matches Sentinel). Confirm.
- **Testing framework:** pytest (matches Sentinel). Agreed; decide `typer.testing.CliRunner` vs. shell-out.
- **Brew formula placement:** `autumngarage/homebrew-cortex` tap needs creating before v0.2.0 release.
- **`cortex doctor` cadence:** CI-only? Pre-commit? Periodic? Decide in Phase B.
- **Interactive-flow UX:** terminal rendering of the prompt-per-candidate flow; pager interaction; keybindings. Sketch in Phase B.
- **Phase B plan refresh:** `plans/phase-b-walking-skeleton.md` predates today's Protocol/SPEC amendments. Update pass needed to add `cortex manifest`, `cortex grep`, T1.9 handling, `Goal-hash:` verification, and templates-shipped-with-init before Phase B opens.

## Known stale-now / handle-later

- **Spec freshness:** SPEC.md v0.2.0-dev is draft and has not yet been validated against a real external project. Expect at least one amendment (minor bump) during Phase C–D dogfood on Sentinel's repo.
- **Gemini round-2 critique is missing.** Google capacity was exhausted during v2 → v3 iteration; v3 went to promotion on Codex critique + user direction alone. Re-running Gemini when capacity returns is optional; v3 is defensible without it.
- **No Map layer in this repo's own `.cortex/` yet.** Map requires regeneration, which Phase C provides.
- **Competitive landscape re-assessment due ~2026-07-17** (quarterly cadence set in [`journal/2026-04-17-competitive-positioning-and-claude-code-risk.md`](./journal/2026-04-17-competitive-positioning-and-claude-code-risk.md)). Watch-items: Letta trigger-discipline features, Anthropic memory-roadmap signals.
