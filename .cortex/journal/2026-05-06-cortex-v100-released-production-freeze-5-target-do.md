# Cortex v1.0.0 released — production freeze + 5-target dogfood pool

**Date:** 2026-05-06
**Type:** release
**Trigger:** T1.10
**Tag:** v1.0.0
**Cites:** plans/cortex-v1, journal/2026-05-06-cortex-v090-released-three-target-dogfood-gate-exi, journal/2026-05-06-pre-10-compatibility-audit-v03-and-v05-forward-com, doctrine/0001-why-cortex-exists, doctrine/0003-spec-is-the-artifact

> Cortex v1.0.0 ships the production freeze: SPEC.md frozen at 1.0.0; CLI 1.0 supports SPEC v0.3, v0.4, v0.5, and v1.0 scaffolds; five-target dogfood pool (conductor, touchstone, vesper, sentinel, vanguard) installed and merged. Homebrew tap auto-bumped; `/opt/homebrew/bin/cortex --version` reports `1.0.0`.

## Artifact

- **Kind:** GitHub Release + Homebrew tap
- **Location:** https://github.com/autumngarage/cortex/releases/tag/v1.0.0; `autumngarage/homebrew-cortex` formula now points at v1.0.0
- **Version:** v1.0.0
- **Tag:** v1.0.0
- **Release notes:** https://github.com/autumngarage/cortex/releases/tag/v1.0.0

## What shipped

The full v0.9.0 → v1.0.0 commit range, organized by the v1.0 ceremony block in `plans/cortex-v1.md`:

**Pre-1.0 ceremony (all closed):**

- **SPEC.md freeze (PR #157)** — bumped 0.5.0 → 1.0.0; § 4.5 required-fields list adds `Spec:`; § 7 changelog entry; "seven" → "eight" required-fields swept repo-wide; `SUPPORTED_SPEC_VERSIONS = ("0.3", "0.4", "0.5", "1.0")` extends rather than replaces, so v1.0 still validates v0.3-v0.5 scaffolds.
- **Documentation refresh + PRIOR_ART (PR #155)** — README "Status & plan" rewritten to v0.9.0+ production-ready framing; PITCH refreshed with three-target dogfood story; new `docs/CASE-STUDIES.md` indexes the conductor incident, vesper stale-URL finding, and v0.9.0 gate exit; PRIOR_ART § 7 adds codesight + Karpathy wiki gist with the composition framing.
- **CLI help-text + error-message polish (PR #158)** — 13 files, +115/-84 across CLI commands and shared validation; `refresh-state` doc-string corrected `cortex:keep` → `cortex:hand` (matches the actual marker convention shipped in v0.4); 523 tests pass post-polish.
- **Doctrine review (PR #156)** — walked all 7 entries; 5 active doctrines hold against v0.9.0 dogfood evidence; 0 supersedes written; review captured in `journal/2026-05-06-v10-doctrine-review-all-7-hold-no-supersedes`.
- **Pre-1.0 compatibility audit (PR #159)** — run on real-world corpora (autumn-mail v0.3.1-dev + cortex itself v0.5.0); outcome: forward-compatible from v0.5 with no migration; v0.3 read-compat directly with optional `cortex migrate-state` helper for upgrading hand-authored `state.md` to v0.4+ marker shape; **no silent breakage** observed.
- **Brew-install smoke test substitution (PR #165)** — replaced the clean-machine VM smoke test with two real-corpus installs of brew-installed cortex 0.9.0: **autumngarage/sentinel#112** (audit clean 15/15) and **outriderintel/vanguard#190** (Railway-deploy distribution shape). Both merged.

**The dogfood pool is now 5 targets**: conductor, touchstone, vesper (henrymodisett), sentinel, vanguard (outriderintel).

**Already-shipped earlier in v1.0 ceremony:**

- `.cortex/config.toml` schema reference (PR #91, v0.6.0 era).
- SPEC-to-test traceability matrix (PR #92, v0.6.0 era).

**Carried into v1.0.x as upstream findings (filed but not blocking):**

- cortex#160 — doctor: `Status: superseded` not in valid plan-status enum (sentinel install).
- cortex#161 — audit-instructions: `github_repos` warns "no release tag" on PaaS-deployed repos with no GitHub releases (vanguard install — Railway-deploy gap).
- cortex#162 — `cortex init` should analyze pre-existing scaffold content rather than making the user discover via doctor errors.
- cortex#163 — `cortex install-brief <target-path>` generator command.
- cortex#164 — merge-pr.sh / Conductor review parity for install PRs across non-cortex sibling repos.

## Downstream docs this changes

- `autumngarage/homebrew-cortex` — formula `url` and `sha256` auto-bumped to v1.0.0 via `release.yml` → `bump-tap` workflow.
- `.cortex/state.md` — regenerated with v1.0.0 as the current released package reality.
- `.cortex/plans/cortex-v1.md` — all v1.0 ceremony items closed; the master sequence completes.
- `README.md` — install path stays canonical (Homebrew tap, no version pin); the Status & plan framing already reads as production-ready as of PR #155.
- `docs/PITCH.md` — already framed for v0.9.0+ in PR #155; v1.0 is the freeze of that framing.
- Five sibling repos (conductor, touchstone, vesper, sentinel, vanguard) — their `[audit-instructions]` will start picking up v1.0 in their `cortex doctor --audit-instructions` runs once they next refresh.

## Compatibility claim (entering v1.0)

- **v1.0 reads v0.3, v0.4, v0.5, and v1.0 scaffolds.** No silent breakage; every divergence from current shape is a visible doctor warning naming the next step.
- **No migration required at install time.** v0.5 corpora are direct forward-compat. v0.3 corpora that want to adopt the v0.4+ marker-preserved `state.md` shape can run the already-shipped `cortex migrate-state --dry-run` / `-y` helper.

## Self-dogfood (continuous from v1.0 onward)

Per Doctrine 0003 (SPEC is the artifact) and the user direction during the compat audit ("you can also self dogfood with this repo, we should always be doing that"): the cortex repo's own `.cortex/` is the always-on compat baseline. Every cortex CLI release runs against the repo that authored it before reaching brew. Pre-merge `touchstone-run.sh validate` + in-session `cortex doctor` constitutes the self-dogfood loop.

## Follow-ups (deferred to future work)

- [ ] **Announce.** Per the v1.0 plan: "Wherever the autumngarage tetrad gets discussed (Sentinel + Touchstone + Cortex + Conductor composition story)." Resolved to: user-driven, not Cortex-CLI-shaped. Once announced, this checkbox in `plans/cortex-v1.md` ticks.
- [ ] **v1.0.x point releases** as cortex#160-164 close. Resolved to: per-issue PRs landing on main; no separate plan needed.

(Per SPEC § 4.2, deferred items resolve to another Plan, Journal entry, or Doctrine entry in the same commit. Both follow-ups resolve to existing durable layers — the plan's announce checkbox and the issue tracker.)
