# pr-merged hook: SPEC_VERSION skip + comment-vs-code truthing

**Date:** 2026-05-08
**Type:** decision
**Trigger:** —  (human-authored — fixes for cortex#220 and cortex#204)
**Cites:** journal/2026-05-07-v1.3.1-released.md

> The pr-merged auto-draft hook now silently skips when `.cortex/SPEC_VERSION` is missing (cortex#220), and the contradictory `--no-verify` comment that referenced cortex#204 was rewritten to match the actual feature-branch shipping shape (already landed in PR #200).

## Context

Two issues against `scripts/cortex-pr-merged-hook.sh` were filed in the same hook surface:

- **cortex#220** (2026-05-07, alchemist PR #44): the hook propagates `cortex journal draft pr-merged`'s exit-2 when `.cortex/SPEC_VERSION` is missing. A repo can have a `.cortex/` directory without yet committing to writer paths — the cortex CLI refuses to write to a store of unknown spec version, which is correct, but the hook surfaced the refusal as a noisy WARNING on every merge for that repo.
- **cortex#204** (vanguard PR #217): an older deployed shape of the hook committed directly to local `main`, then failed at branch-protection push time. The `outriderintel/vanguard` deploy hit this; this repo's hook had already moved to feature-branch shipping in PR #200 (cortex#194). The remaining gap was a comment that conductor had drafted earlier in the session asserting the current code does NOT pass `--no-verify`, while the code at the `git commit` call site DOES (intentionally — on the feature branch, where `no-commit-to-branch` is configured for `--branch main --branch master` only). Comment and code disagreed.

Per `principles/audit-weak-points.md`, audited the hook surface and adjacent scripts:

- Searched `scripts/` for other auto-draft callers — none. The hook is invoked only from `scripts/merge-pr.sh:385`. No sibling hooks duplicate the pattern.
- Searched `scripts/` for other `git commit … main` / `git push --no-verify origin main` paths. Only hit: `scripts/release.sh:82,89` — intentional, documented release path with the version-bump commit. Not the same weak-point class.
- `.github/` workflows do not call `cortex journal draft` directly.

The audit's review surface is bounded to the hook itself; no fan-out fixes were needed.

## What we decided

1. **#220 fix:** add a `.cortex/SPEC_VERSION` existence gate immediately after the recursion guard. Missing marker → one informational stderr line (`cortex: .cortex/ exists but SPEC_VERSION missing; skipping auto-draft`) + exit 0. The repo hasn't opted in to writer paths, so silence-with-trace is correct.
2. **#204 audit closeout:** the code already commits on `docs/journal-pr-*` and ships via `gh pr create` + `gh pr merge --auto`; that landed in PR #200. The follow-up was correcting the misleading comment block that contradicted the call site. The `--no-verify` on the feature-branch commit is intentional and documented at the call site (lines 411-415); the new comment makes the bypass scope explicit ("applies only to the feature branch and never to a default-branch commit").
3. **Tests:** added two tests in `tests/test_pr_merged_hook.py` — `test_hook_skips_cleanly_when_spec_version_missing` (writer never invoked, exit 0, informational stderr line, `main` unchanged) and `test_hook_proceeds_when_spec_version_present` (gate is a real lookup, not a constant short-circuit). Updated the `project_repo` fixture to write `.cortex/SPEC_VERSION` so the 16 existing tests continue to simulate a fully-initialized cortex store.

## Consequences / action items

- [x] cortex#220 — `cortex-pr-merged-hook.sh` SPEC_VERSION-missing skip
- [x] cortex#204 — feature-branch shipping already correct; comment now matches code
- [x] Guardrail tests added (writer-not-invoked + writer-invoked variants of the SPEC_VERSION gate)
- [x] Audit complete: bounded to hook + audit-adjacent scripts; no other instances of the weak-point class need fixing in this PR
