---
Status: shipped
Written: 2026-04-25
Shipped: 2026-04-26
Author: claude-session-2026-04-25
Goal-hash: 08364e1a
Updated-by:
  - 2026-04-25T19:00 claude-session-2026-04-25 (spawned from plans/cortex-v1.md ### v0.3.0 sub-section as a session-scoped sub-plan; same pattern as plans/init-ux-fixes-from-touchstone — sub-plan exists to drive a focused multi-PR session, parent plan tracks the v0.3.0 → v1.0.0 arc)
  - 2026-04-26T00:05 claude-session-2026-04-25 (work-item PRs #44-#47 merged; release-prep PR #48 opened — version bump + uv.lock + README + state.md. Plan stays Status: active until the release artifact (tag + GitHub Release + Homebrew tap bump via release.yml) actually lands; on artifact landing this plan flips to Status: shipped with Promoted-to journal/<date>-v0.3.0-released)
  - 2026-04-26T01:15 claude-session-2026-04-25 (Status: active → shipped — PR #48 merged; tag v0.3.0 pushed; GitHub Release published; release.yml fired and auto-bumped the homebrew-cortex formula. Closure recorded in journal/2026-04-26-v0.3.0-released)
Promoted-to: journal/2026-04-26-v0.3.0-released
Cites: ../../SPEC.md, ../../.cortex/protocol.md, plans/cortex-v1, ../doctrine/0001-why-cortex-exists, ../doctrine/0003-spec-is-the-artifact, ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md, journal/2026-04-26-v0.3.0-released
---

# Ship Cortex v0.3.0 Write-Side Foundation

> **Shipped 2026-04-26.** All five PRs landed (#44 T1.10 + release template, #45 `cortex journal draft`, #46 `cortex plan spawn`, #47 orphan-deferral check, #48 v0.3.0 release prep). Tag `v0.3.0` pushed; [GitHub Release](https://github.com/autumngarage/cortex/releases/tag/v0.3.0) published; `release.yml` auto-bumped the `autumngarage/homebrew-cortex` formula. Closure recorded in [`journal/2026-04-26-v0.3.0-released`](../journal/2026-04-26-v0.3.0-released.md). v0.4.0 (read-side foundation: deterministic `refresh-state`, `cortex next` MVP, `cortex plan status`) is the next active sub-section of [`plans/cortex-v1`](./cortex-v1.md).

## Why (grounding)

The session-pickup gap is what Cortex exists to close ([`doctrine/0001-why-cortex-exists`](../doctrine/0001-why-cortex-exists.md)). v0.3.0 is the first release on the production-rerank path that gives the user any new authoring surface — every release after this assumes `cortex journal draft <type>` exists (v0.5.0's Touchstone post-merge hook calls it; v0.9.0's external-dogfood gate is "≥ 80 % of new journal entries authored via the draft command for a week"). So the v0.3.0 keystone is `journal draft`; everything else in the release is small enough to ride alongside.

The conductor case study ([`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`](../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md)) is the design test case for the T1.10 release-event amendment — a stale "tap planned for v0.1.0; not yet wired" claim survived eight releases because no release event ever entered the journal. T1.10 + `journal/release.md` template + audit expansion makes that *class* of incident catchable in v0.3.0; v0.5.0's `--audit-instructions` will catch the *consequence* (downstream-doc claims that contradict reality).

## Success Criteria

This plan is done when v0.3.0 is tagged, released, and on Homebrew, with all five PRs landed. Specifically:

1. **`cortex journal draft <type>` works on this repo.** Running `cortex journal draft decision` opens `$EDITOR` with the decision template substituted with today's `**Date:**` and (when `--title` is passed) a rewritten H1, plus an HTML-comment context block appended at the bottom containing the latest 5 `git log` subjects and `gh pr view` output for the active branch. `--no-edit` writes the draft directly to `.cortex/journal/<filename>` and prints the path on stdout. `gh` integration degrades cleanly: missing `gh`, failing `gh auth status`, or no PR for the current branch each substitute a one-line note into the context block instead of blocking.
2. **`release` journal template ships under `.cortex/templates/journal/release.md` and the bundled copy in `src/cortex/_data/templates/journal/release.md`** with fields per [`plans/cortex-v1.md`](./cortex-v1.md) v0.3.0 work item: artifact kind, artifact location, release version, release-notes link, downstream-docs-this-changes list. `tests/test_data_sync.py` keeps the two copies in sync.
3. **T1.10 ships in `.cortex/protocol.md` § 2** as the new Tier-1 trigger for "Pull request released as a tagged distribution artifact (Homebrew tap, PyPI, Docker, GitHub Release)" with `journal/release.md` as its template. Protocol version bumps to 0.2.1.
4. **SPEC.md bumps to v0.4.0-dev** with the T1.10 mention (minor under § 7's pre-1.0 exception). `SUPPORTED_SPEC_VERSIONS` and `SUPPORTED_PROTOCOL_VERSIONS` in `src/cortex/__init__.py` accept the new versions.
5. **`cortex doctor --audit` fires T1.10 against `git tag --list --sort=-creatordate`** for the audit window and matches each tag against a `Type: release` Journal entry within 72h. Unmatched fires warn but never error (matches the existing T1.x first-slice convention). On this repo, the audit produces ~10 historical T1.10 unmatched fires (one per existing tag); these are documented as expected and parked alongside the T1.9 retrofit follow-up.
6. **`cortex plan spawn <slug>`** scaffolds a Plan file under `.cortex/plans/<slug>.md` with seven-field frontmatter (Status active, Written today, Author seeded, Goal-hash computed from `--title`, Updated-by seeded, Cites empty placeholder) and the five required sections per SPEC § 3.4 (`## Why (grounding)`, `## Success Criteria`, `## Approach`, `## Work items`, `## Follow-ups (deferred)`). Refuses to overwrite existing files.
7. **`cortex doctor` orphan-deferral check** scans every active Plan's `## Follow-ups (deferred)` section and warns when any item lacks a citation to another Plan, Journal entry, or Doctrine entry per SPEC § 4.2. Errors under `--strict` if the doctor command supports `--strict`; otherwise stays a warning. Passes on this repo's existing plans with no waivers.
8. **Tests** — every new command has tests against real temp git repos (no mocked filesystem); `--audit` T1.10 test creates a temp git repo with synthetic tags + journal entries; orphan-deferral check tests use synthetic plan fixtures (good + missing-citation + present-citation cases).
9. **v0.3.0 released.** Version bumped in `src/cortex/__init__.py` (0.2.7 → 0.3.0) and `pyproject.toml`, `uv.lock` regenerated, tag `v0.3.0` pushed, `gh release create v0.3.0 --generate-notes`, Homebrew formula auto-updated by the existing `.github/workflows/release.yml` reusable workflow. Hand-authored `release` journal entry on this repo's `.cortex/` records the release.

## Approach

**Smallest opening PR first.** PR 1 ships `release` template + T1.10 Protocol/SPEC amendments + audit expansion as a single small PR. Pure additive — no new commands, no behavioral changes to existing code paths beyond audit.py classifier expansion. Gets the SPEC text live so the keystone PR can use the template.

**Keystone PR second.** PR 2 ships `cortex journal draft <type>` once the templates are present. This is the largest PR of the night: new module, new subcommand, git/gh integration, $EDITOR handling. Everything else in v0.3.0 sits downstream of this command being live.

**Two small PRs, then release.** PR 3 (`cortex plan spawn`) and PR 4 (orphan-deferral check) are independent of each other; either can land in either order. PR 5 is the release ritual.

**Parent plan stays single-source-of-truth.** This sub-plan exists for tonight's session focus; on landing, every checkbox here also closes a checkbox in [`plans/cortex-v1.md`](./cortex-v1.md) v0.3.0 work items. On landing, this plan is `Status: shipped` with `Promoted-to: journal/<release-entry>`, mirroring how `plans/init-ux-fixes-from-touchstone.md` closed.

**No re-architecture.** New code goes in new modules under `src/cortex/commands/` and `src/cortex/` flat. Existing modules (audit.py, validation.py, doctor.py) get small additive changes. Tests follow the existing pattern: real `git init` temp repos, no mocks, real subprocess calls when feasible.

**T1.10 audit window for tags uses `git tag --list --sort=-creatordate` not `git log`.** Tags are ref objects, not commits — annotated tags carry their own date. The audit walks tags in the window, looks up the tagged commit's date for the 72h window, and matches against `Type: release` journal entries. This is parallel to T1.9's commit walk, not the same path.

**`cortex journal draft` template-resolution degrades.** If a custom template lives at `.cortex/templates/journal/<type>.md`, use it. Otherwise fall back to bundled `src/cortex/_data/templates/journal/<type>.md`. If neither exists, error with the list of available types.

## Work items

### PR 1 — `release` journal template + T1.10 Protocol/SPEC amendment + audit expansion

- [x] **Add `.cortex/templates/journal/release.md`** with the seven-field structure. Required fields: `# <title>`, `**Date:**`, `**Type:** release`, `**Trigger:** T1.10`, `**Cites:**`, blockquote summary, `## Artifact` (kind, location, version, link), `## Release notes`, `## Downstream docs this changes` (CLAUDE.md, README.md, etc. — list of files that reference the artifact location).
- [x] **Mirror to `src/cortex/_data/templates/journal/release.md`** so `cortex init` ships it. Confirm `tests/test_data_sync.py` covers this new file (it scans both trees automatically based on existing pattern, but verify).
- [x] **Add T1.10 to `.cortex/protocol.md` § 2** as `T1.10 | Pull request released as a tagged distribution artifact (Homebrew tap, PyPI release, Docker push, GitHub Release) | journal/release.md`. Bump Protocol version to 0.2.1 and update its SPEC.md compatibility line.
- [x] **Bump `SPEC.md` to v0.4.0-dev** with a § 7 entry explaining what changed (T1.10 added). Update SPEC version line at top of file.
- [x] **Update `src/cortex/__init__.py`** — `SUPPORTED_SPEC_VERSIONS` accepts "0.3" already (matches major; no change needed unless we want explicit 0.3.2 acceptance); `SUPPORTED_PROTOCOL_VERSIONS` add "0.2" (already there) — confirm both are still major-only matches.
- [x] **Mirror `.cortex/protocol.md` to `src/cortex/_data/protocol.md`** if init bundles protocol.md (it does — confirmed in cortex init scaffold).
- [x] **Extend `src/cortex/audit.py`** — add `Trigger.T1_10`, `EXPECTED_TYPE[T1_10] = "release"`, new `_load_tags(project_root, since_days)` helper that runs `git tag --list --sort=-creatordate` and resolves each tag's commit date, new audit pass that fires T1.10 per tag and matches against journal entries with `Type: release` within the existing JOURNAL_MATCH_WINDOW_HOURS. Add tests.
- [x] **Add tests** — `tests/test_audit_t110.py`: real `git init` temp repo, `git tag` two tags within window, journal entries (one matched, one unmatched), assert audit fires T1.10 for both and matches the right one. `tests/test_data_sync.py` pickup verified.
- [x] **Open PR.**

### PR 2 — `cortex journal draft <type>` (keystone)

- [x] **Create `src/cortex/commands/journal.py`** with `journal` group and `draft` subcommand. Args: `type` (positional, e.g. `decision`, `incident`, `release`, `pr-merged`, `plan-transition`, `sentinel-cycle`); `--no-edit` (skip `$EDITOR`, print path); `--slug <text>` (override the auto-generated filename slug); `--path <project-root>` (default `.`).
- [x] **Template resolution** — first `<root>/.cortex/templates/journal/<type>.md`, then bundled `src/cortex/_data/templates/journal/<type>.md`. Error with list of available types if neither found.
- [x] **Pre-fill from context.** Default fill substitutes:
  - `**Date:**` → today's ISO date
  - `**Type:**` → the requested type (override the template's literal field if present)
  - `<title>` placeholder → derived from the latest commit subject (fallback: "TODO")
  - `<one-sentence summary>` placeholder → leave empty, let the user write
  - `## Context` → for `decision` / `incident`, append commented-out context: most recent 5 commit subjects (one per line) and, when `gh` is installed and authenticated, `gh pr view` for the current branch's PR (or "no open PR for this branch")
- [x] **Filename pattern** — `YYYY-MM-DD-<slug>.md`. Slug derives from the title via the same lowercase-strip-collapse normalization used by `goal_hash.normalize_goal_hash`, but limited to 50 characters. If `<slug>` is provided via `--slug`, use it verbatim (still limit to 50 chars and strip non-`[a-z0-9-]`).
- [x] **`gh` degradation.** When `gh` is not on PATH or `gh auth status` fails, skip the PR-context step and write `_(gh PR context unavailable: gh not installed or not authenticated)_` as a comment in the body. Never block.
- [x] **`$EDITOR` handling.** Default: write to a temp file, run `$EDITOR <tempfile>` (fall back to `vi` then `nano` then bail), then on editor exit move the file to `.cortex/journal/<filename>`. `--no-edit` writes directly to `.cortex/journal/` and prints the path.
- [x] **Wire `journal` group into `cli.py`.** Subcommand groups already exist (`status`, `manifest`, `grep`, `doctor`, `init`, `promote`, `version`).
- [x] **Tests** — `tests/test_journal_draft.py`: real temp git repo, real `.cortex/templates/journal/decision.md` (copied from this repo's bundle), `cortex journal draft decision --no-edit` produces a file with today's date and the latest commit's subject as title; `--slug custom-slug` honors the slug; missing template type produces a clear error; gh-missing fallback works.
- [x] **Open PR.**

### PR 3 — `cortex plan spawn <slug>`

- [x] **Create `src/cortex/commands/plan.py`** with `plan` group and `spawn` subcommand. Args: `slug` (positional, becomes filename); `--title <text>` (drives Goal-hash and the `# <title>` line — required); `--cites <text>` (optional, comma-separated initial Cites entries); `--path <project-root>`.
- [x] **Scaffolding** — write `.cortex/plans/<slug>.md` with frontmatter:
  - `Status: active`
  - `Written: <today>`
  - `Author: <auto-detected>` — e.g. `claude-session-<iso>` if `$CORTEX_SESSION_ID` set, else `human`
  - `Goal-hash: <computed>` via `cortex.goal_hash.normalize_goal_hash(title)`
  - `Updated-by:` with one seeded line — `<iso> <author> (created via cortex plan spawn)`
  - `Cites:` empty or comma-separated from `--cites`
- [x] **Body sections** — empty placeholders for `## Why (grounding)`, `## Success Criteria`, `## Approach`, `## Work items`, `## Follow-ups (deferred)`. Each section has a short `_(fill in)_` italic placeholder so `cortex doctor` doesn't immediately fail on missing required sections.
- [x] **Refuse to overwrite.** If `.cortex/plans/<slug>.md` exists, error and exit 1 with a message naming the existing file.
- [x] **Wire into `cli.py`.**
- [x] **Tests** — `tests/test_plan_spawn.py`: scaffolded file passes `cortex doctor` plan checks (frontmatter complete, Goal-hash matches title, all required sections present); slug collision raises; `--cites` populates correctly.
- [x] **Open PR.**

### PR 4 — `cortex doctor` orphan-deferral check

- [x] **Locate** the doctor module that walks plan files (`src/cortex/validation.py` or `src/cortex/commands/doctor.py`). Add a new check `_check_orphan_deferrals(plan_path)` that:
  - Locates the `## Follow-ups (deferred)` section.
  - For each `- ` bullet item in that section, verifies the bullet text contains either `journal/<...>` or `plans/<...>` (case-insensitive) — an explicit citation to where the deferral is resolved.
  - Bullets without such a citation produce a warning of the form `<plan>:<line>: Follow-ups (deferred) item lacks resolution citation per SPEC § 4.2: "<first-50-chars>".`
- [x] **Run only on `Status: active` plans** to avoid noise from shipped/cancelled plans (which may have un-cited items that have since been resolved organically).
- [x] **Surface as warning by default; error under `--strict`** if doctor supports `--strict`. Match the existing convention.
- [x] **Tests** — `tests/test_doctor_orphan_deferral.py`: synthetic plan with all-cited items (clean), one with an uncited item (warns), one with a non-active status (skipped). Assert warning text format.
- [x] **Verify on this repo.** `cortex doctor` should pass on `plans/cortex-v1.md` (every Follow-up cites a journal entry) and on this plan once it lands.
- [x] **Open PR.**

### PR 5 — v0.3.0 release

- [x] Bump `__version__` in `src/cortex/__init__.py` from `0.2.7` → `0.3.0`.
- [x] Bump `version` in `pyproject.toml` to `0.3.0`.
- [x] `uv lock` regen.
- [x] Update `.cortex/state.md` `## Shipped recently` to record v0.3.0 (release-prep entry).
- [x] In the parent [`plans/cortex-v1.md`](./cortex-v1.md), check off the v0.3.0 Work-item PRs and add an Updated-by line.
- [x] Open the release-prep PR (#48).
- [x] (post-merge) Tag `v0.3.0`, push tag, `gh release create v0.3.0 --generate-notes`. The `release.yml` workflow fires on the `release-published` event and bumps the Homebrew formula's `url` + `sha256` automatically.
- [x] (post-tag) Hand-author the `release` Journal entry via `cortex journal draft release` (dogfooded the keystone) — supplies the matching `Type: release / Trigger: T1.10` entry the doctor audit looks for. Entry: [`journal/2026-04-26-v0.3.0-released`](../journal/2026-04-26-v0.3.0-released.md).
- [x] (post-release-entry) Mark this plan `Status: shipped` with `Promoted-to: journal/2026-04-26-v0.3.0-released` (this commit).

## Follow-ups (deferred)

Each item resolves to [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) (the parent rerank that already accepted these as out-of-scope for v0.3.0) or to a successor plan/journal entry.

- **Retrofit historical T1.10 fires** — running `cortex doctor --audit` after PR 1 lands will surface ~10 unmatched T1.10 fires (one per existing Cortex tag from v0.1.0 to v0.2.7). Mirrors the T1.9 historical-fires situation that was parked at item #5 in [`journal/2026-04-24-v1-followups-parked`](../journal/2026-04-24-v1-followups-parked.md). Decision: same park, same revisit condition (only retrofit if/when historical entries become load-bearing for any synthesis); resolves to that journal entry. No new entry needed.
- **`cortex plan spawn` interactive title prompt** — first cut takes `--title` as a flag. Prompted entry (`prompt_toolkit` or click prompt) is parked to the v1.x interactive-flow follow-up at item #6 in [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md).
- **`cortex journal draft` template-customization story** — projects can already drop a custom template in `.cortex/templates/journal/<type>.md` and `journal draft` resolves it first. A future-proofing concern (per-project template fields, validation against the bundled template's required-field list) is not in v0.3.0 scope; resolves to [`plans/cortex-v1.md`](./cortex-v1.md) `## Follow-ups (deferred)` (mutability of templates is implicit in SPEC § 5).
- **End-to-end Homebrew verification on a fresh terminal** — `brew upgrade autumngarage/cortex/cortex` should report `0.3.0` once the tap formula bump propagates. The release-published-event fired and `release.yml` ran, but verifying on a fresh shell rounds out the deploy. Captured for next-session attention; resolves to [`journal/2026-04-26-v0.3.0-released`](../journal/2026-04-26-v0.3.0-released.md) (the same release entry calls out the verification as routine post-deploy hygiene rather than a v0.3.0 success criterion).
