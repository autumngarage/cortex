# Cortex v1.2.0 released — staleness family closed (issue-refs audit, generator drift, install dual-artifact)

**Date:** 2026-05-06
**Type:** release
**Trigger:** T1.10
**Tag:** v1.2.0
**Cites:** plans/cortex-v1, journal/2026-05-06-cortex-v110-released-root-cause-fixes-sources-hash, doctrine/0001-why-cortex-exists

> v1.2.0 closes the v1.1.0 staleness-family root-cause fixes with three more checks/changes (cortex#181/#182/#183). Plus a self-corrective chore: SPEC 1.1.0 was authoritatively shipped in v1.1.0 but the `-dev` suffix and `SUPPORTED_SPEC_VERSIONS` weren't synced; v1.2.0 corrects the drift.

## Artifact

- **Kind:** GitHub Release + Homebrew tap
- **Location:** https://github.com/autumngarage/cortex/releases/tag/v1.2.0; `autumngarage/homebrew-cortex` formula now points at v1.2.0
- **Version:** v1.2.0
- **Tag:** v1.2.0
- **Release notes:** https://github.com/autumngarage/cortex/releases/tag/v1.2.0

## What shipped

Three root-cause fixes from the staleness-family swarm (cortex#181/#182/#183), plus a SPEC-1.1 finalization chore:

### Fix #181 — `--audit-issue-refs` doctor check (PR #187)

**Surface:** Vesper's install-baseline journal carried `[ ] Track cortex#141` boxes referencing closed issues. Append-only Journal can't flip the boxes — they're permanent stale claims.

**Fundamental fix:** New `check_stale_issue_references` doctor check walks every `.cortex/` markdown file, extracts `[\s\]\s+.*#(\d+)` patterns, queries GitHub issue state, warns when `[ ]` references a `closed` issue. Runs under `--audit-issue-refs` flag (or folded into `--audit-instructions`); cache to `.cortex/.cache/issue-state.json` (24h TTL); per-line `<!-- watch -->` opt-out.

Conductor review caught a real `.cortex/.gitignore` gap during merge (cache directory not ignored — would have appeared as untracked file after first run); fixed before merge.

### Fix #183 — Generator-version drift check (PR #184)

**Surface:** Vesper's state.md `Generator: cortex refresh-state v0.8.3`; brew binary at v1.1.0. Three minor versions of silent drift.

**Fundamental fix:** New `check_generator_version_drift` plain-doctor check reads `Generator:` field, parses version, compares against `cortex.__version__`, warns on major/minor delta (silent on patch). Surfaces "you should refresh" exactly when refresh would pick up new layer fields.

### Fix #182 — Install-pattern dual-artifact (PR #185)

**Surface:** Install briefs put `[ ]` tracking checkboxes inside append-only journal entries — a layer violation that creates the stale claims #181 catches operationally.

**Fundamental fix:** `cortex install-brief --closes N,N,N` now generates **two** artifacts: an append-only journal-baseline (no `[ ]` boxes; references via `Refs:` only) AND a mutable `plans/cortex-install-followups.md` (where the `[ ]` tracking lives). Journal cites the plan; plan owns the trackable work. Installs without `--closes` keep the existing single-artifact shape.

### Chore — SPEC 1.1.0 finalization (PR #188)

**Surface:** SPEC.md said `1.1.0-dev` while CLI v1.1.0 was authoritatively shipping SPEC 1.1.0 features (`Sources-hash:`, `superseded` plan status). `SUPPORTED_SPEC_VERSIONS` didn't claim `1.1`. Per CLAUDE.md: "Each release must also declare which spec version it supports."

**Fix:** Drop `-dev` from SPEC.md (v1.1.0-dev → v1.1.0), extend `SUPPORTED_SPEC_VERSIONS` to `("0.3", "0.4", "0.5", "1.0", "1.1")`, bump `SPEC_VERSION_LITERAL` 1.0.0 → 1.1.0, rewrite the § 7 changelog entry from "v1.1.0-dev adds X" to "v1.1.0 adds X (Sources-hash + superseded)."

## Downstream docs this changes

- `autumngarage/homebrew-cortex` — formula auto-bumped to v1.2.0.
- `.cortex/state.md` — refreshed pre-release.
- `SPEC.md` — frozen at 1.1.0 (drift corrected).
- `docs/config-reference.md` — already documents `[doctrine.append-only]` (PR #176, v1.1.0); v1.2.0 added documentation for `[audit-instructions].self_repo` and the cache config.
- Five sibling install repos (conductor, touchstone, vesper, sentinel, vanguard) — `cortex doctor --audit-issue-refs` on each will now surface stale `[ ]` references in their install-baseline journals. Vesper's was the canonical fixture; expect 2-3 warnings on first run there.

## Self-dogfood signals from this release

1. **The trailer convention round-trips correctly.** All three swarm PRs (#184, #185, #187) auto-closed their issues on merge via the `Closes-issue: #N` trailers — no manual cleanup needed for the first time in this session's pattern.
2. **Conductor review is paying its rent.** PR #187 had a real bug (missing `.cache/` gitignore entry) caught and fixed before merge. Same as PR #170's silent-failure catch in v1.1.0. Two real bugs caught by the review gate across the staleness work.
3. **Conductor team's response loop on conductor#210/#211 was visible.** Both issues filed during this session were CLOSED by the time we ran the staleness swarm. The improved stall diagnostic ("Detected 12 other live claude processes…") shipped during the same window. Two follow-up issues filed: conductor#230 (`--retry-on-stall` default) and conductor#231 (orphan-process cleanup) — neither blocks Cortex.

## Compatibility claim (entering v1.2.0)

- v1.2 reads v0.3, v0.4, v0.5, v1.0, and v1.1 scaffolds. No new SPEC bumps in v1.2 itself — all of v1.2's changes are CLI behavior additions.
- Existing v1.1 scaffolds (`Sources-hash:` present, `superseded` accepted) are unchanged-compatible.
- v1.2's `cortex install-brief --closes` produces a different output shape than v1.1's; existing `--closes`-less invocations are unchanged.

## Follow-ups (deferred to future work)

- [ ] **Migration of existing install-baseline journals' `[ ]` boxes** on conductor / touchstone / vesper / sentinel / vanguard. Resolved to: per-target follow-up entries (NOT in-place edits — Journal is append-only). The `--audit-issue-refs` check makes them visible; the migration is just authoring follow-up entries acknowledging closure.
- [ ] **Optional doctor check for `[ ]` patterns inside journal entries** (named in cortex#182's body as a deferred "stretch goal"). Would prevent future layer violations regardless of staleness. Resolved to: a future cortex#NNN issue once the dual-artifact pattern is exercised across an install or two.
- [ ] **Three-tier severity reclassification audit** for doctor checks (the deeper fix from cortex#174). Still deferred. Resolved to: cortex#174's "deeper fix" framing.

(Per SPEC § 4.2, deferred items resolve to another Plan, Journal entry, or Doctrine entry in the same commit. All three resolutions point to existing durable layers.)
