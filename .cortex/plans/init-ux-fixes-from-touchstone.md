---
Status: active
Written: 2026-04-24
Author: claude-session-2026-04-24
Goal-hash: db2ef686
Updated-by:
  - 2026-04-24T22:50 claude-session-2026-04-24 (created from the touchstone dogfood UX test; scoped as a v0.2.4 patch series so the Sev-1 / Sev-2 bugs that make Cortex regressive on Touchstone-style projects don't ride along into v0.3.0+)
  - 2026-04-24T23:05 claude-session-2026-04-24 (added journal/2026-04-24-init-ux-fixes-plan-decision as the in-tree resolution target for the four ## Follow-ups (deferred) items per SPEC § 4.2 + Codex review on PR #33; also folded Sev-3 #6 (Phase C terminology in scaffolded outputs) into Slice 3 ride-along scope)
Cites: ../../SPEC.md, ../../.cortex/protocol.md, plans/cortex-v1, journal/2026-04-24-dogfood-target-touchstone, journal/2026-04-24-production-release-rerank, journal/2026-04-24-init-ux-fixes-plan-decision, ../doctrine/0001-why-cortex-exists, ../doctrine/0005-scope-boundaries-v2, ../../principles/documentation-ownership.md
---

# Cortex Init UX Fixes from Touchstone Dogfood

> v0.2.4 patch series fixing bugs surfaced by running `cortex init -y --path ~/Repos/touchstone` on 2026-04-24. The two Sev-1 bugs make Cortex *regressive* on projects that already have working `@path` imports in CLAUDE.md/AGENTS.md — fixing them is a precondition for the v0.9.0 dogfood gate (currently targeting touchstone) producing meaningful evidence rather than evidence of bugs that should have been caught earlier.

## Why (grounding)

The 2026-04-24 production-release-rerank ([`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md)) named touchstone as the v0.9.0 dogfood target ([`journal/2026-04-24-dogfood-target-touchstone`](../journal/2026-04-24-dogfood-target-touchstone.md)). A pre-v0.9.0 "real UX test" — running `cortex init -y --path ~/Repos/touchstone` from `cortex` v0.2.3 (Homebrew) and reviewing the result — surfaced two Sev-1 UX bugs and several smaller ones. The Sev-1 bugs change the install from "additive" to "regressive" on projects that already have working `@principles/*.md` imports in CLAUDE.md/AGENTS.md (which is every Touchstone-managed project, by construction).

The bugs:

- **Sev-1: Shallow-Doctrine manifest contamination.** `cortex init` auto-imports every file under `principles/` as a Doctrine entry. Each entry is a stub pointing back at the source (`See [principles/X.md] for the full content.`) — not the actual doctrine. `cortex manifest` then loads these stubs into the Doctrine slot at session start, displacing other Doctrine content within the budget. Net effect: agents get *less* useful context after install on a project that already had `@principles/X.md` imports working in CLAUDE.md.

- **Sev-1: CLAUDE.md insertion placement breaks document outline.** Init inserts new `## Current state (read this first)` and `## Cortex Protocol` sections at the position immediately after the last `@`-import line — but that position is *inside* an existing top-level section. Result: any `### sub-heading` that originally followed the insertion point gets reparented to `## Cortex Protocol` from a markdown TOC perspective. Concrete example on touchstone: `### Never commit on main` (a Git-Workflow sub-section) became visually a sub-section of `## Cortex Protocol`.

- **Sev-2: `principles/README.md` absorbed as `doctrine/0001-readme.md`.** The scanner treats anything in `principles/` as a Doctrine candidate without filtering meta-docs. Doctrine title is derived from the file's H1 (`# Engineering Principles` because that's the README's H1) — wrong file, wrong title.

- **Sev-2: 0001 collision.** Init's printed Next Steps tell the user to *"Author doctrine/0001-why-`<project>`-exists.md"* — but absorption already took 0001 for the bogus README.

- **Sev-3 / Sev-4 follow-ups** (smaller; bundled if cheap). State.md Sources undercount; "1 unscoped constraint" output should inline the file:line ref; printed Next Steps shows step 1 then step 3 (missing step 2); top-level `cortex --status-only --path X` doesn't accept `--path` (only the subcommand form does).

Full evidence and severity reasoning: this morning's dogfood-test conversation. A case study under `docs/case-studies/` will land alongside the first PR in this series with the full reproduction + screenshots-of-output.

## Success Criteria

This plan is done when **`cortex init -y --path ~/Repos/touchstone`, after rolling back the current Touchstone install, produces no Sev-1 / Sev-2 findings**. Specifically:

1. **No shallow Doctrine entries imported from Touchstone-managed paths.** `cortex init` detects the Touchstone integration (already does — see "Touchstone signals: ✓" output) and skips `principles/`-prefixed files when the project is Touchstone-managed. Skipped files surface in scan output as "Detected Touchstone-managed; already imported via @-imports in CLAUDE.md/AGENTS.md — skipped Doctrine import." `cortex manifest` on the resulting `.cortex/` produces a Doctrine slot that contains real Doctrine (project-authored), not pointer-stubs.
2. **`cortex init` does not damage CLAUDE.md / AGENTS.md document outline.** New `##` imports are appended at the end of the file (after a blank line if the file does not end with one), preserving every existing `## ` and `### ` parent/child relationship. Validated by markdown TOC tools (e.g., `markdownlint MD028` and a doctest that walks the resulting outline).
3. **`principles/README.md` (and any `**/README.md` under a Touchstone-managed dir) is filtered from Doctrine candidates** with a "skipped: meta-doc filename pattern" note in scan output.
4. **0001 stays reserved** for the human-authored "why X exists" entry. Auto-imported Doctrine starts at 0100 (or another non-overlapping range) so the printed Next Steps "Author doctrine/0001-why-`<project>`-exists.md" guidance never collides.
5. **Idempotent re-run.** Running `cortex init` twice produces identical output (already true; verify still true after the fixes).
6. **Tests for each fix**, run against real `git init`'d temp repos with synthetic touchstone-style structure (no mocking the filesystem; real `principles/` dir with synthetic content; assert on the resulting `.cortex/` shape).
7. **Sev-3/Sev-4 fixes ship if cheap** (single-file, < 30 LOC each); otherwise defer to v0.3.0 with a citation to this plan.
8. **v0.2.4 release** — version bump in `__init__.py` + `pyproject.toml`, tag, GitHub Release, Homebrew formula update.

## Approach

**Patch release, not feature release.** v0.2.4 is a bug-fix release. No new commands, no new SPEC text, no Protocol bumps. Pure fixes to the install path. This plan exists *outside* the v0.3.0 → v1.0.0 sequence in [`plans/cortex-v1.md`](./cortex-v1.md) — v0.2.4 ships first; v0.3.0 work resumes after.

**Delegated implementation via Codex.** This plan is written for `codex exec --full-auto` to implement against. Each work item names files to change, test files to add, and acceptance assertions. Codex implements; review happens via the standard merge-pr.sh + Codex-review hook flow.

**Sev-1 fixes ship together; Sev-2 / Sev-3 / Sev-4 ride along if they fit one PR cleanly.** If the bundle exceeds ~600 diff lines (the threshold where Codex-review tends to fragment), split into v0.2.4 (Sev-1 only) and v0.2.5 (Sev-2 + smaller).

**No re-architecture.** The fixes are local edits in `src/cortex/init.py` (or whatever the discovery/import module is named) and the test harness. No restructuring of the absorb pipeline, no new SPEC contracts, no new commands. Hold the design line.

**Touchstone-detection is the load-bearing primitive.** `cortex init` already detects Touchstone integration (the "Touchstone signals: ✓ ..." line in scan output). Sev-1 #1's fix piggybacks on that same detection: when Touchstone is detected, `principles/` is owned by Touchstone (synced from upstream), so it should not be absorbed as Cortex Doctrine. This keeps the fix minimal and respects existing architecture.

**Acceptance test runs `cortex init` on a real fixture, not a mocked one.** Per Cortex's existing test convention (real filesystem, real git in temp dirs).

## Work items

### Slice 1 — Sev-1 fixes (v0.2.4 must-ship)

- [ ] **Fix #1: Don't auto-import Touchstone-managed paths as Doctrine.**
  - Where: `src/cortex/init.py` (or the discovery module — identify in the first commit). Locate the loop that classifies `principles/*.md` as Doctrine candidates.
  - Add condition: when the project's Touchstone integration is detected (any of `.touchstone-config`, `.touchstone-manifest`, `.touchstone-version` present at project root — same signals already surfaced in the "Touchstone signals: ✓" scan output line), skip files under `principles/` from Doctrine candidate classification.
  - Skip surfaces in scan output as a new "Detected Touchstone-managed; skipped from Doctrine import (already imported via @path in CLAUDE.md/AGENTS.md)" line listing each skipped file.
  - Acceptance: `cortex init -y --path <touchstone-fixture>` produces zero `.cortex/doctrine/000N-*.md` entries derived from `principles/*.md`. Existing scan output for the rest of the absorb (map references, reference-only, unknown patterns) unchanged.
  - Test: `tests/test_init_touchstone_managed.py` — fixture sets up a temp repo with `.touchstone-config` + `.touchstone-version` files and a `principles/foo.md`. Asserts post-init that `(temp/.cortex/doctrine/).iterdir()` returns no entry whose `Imported-from:` frontmatter equals `principles/foo.md`.

- [ ] **Fix #2: Append CLAUDE.md / AGENTS.md imports at end of file, not in the middle.**
  - Where: `src/cortex/init.py`, the function(s) that handle `--add-imports-claude` / `--add-imports-agents`.
  - Change: replace the current placement heuristic (which inserts after the last `@`-import line) with strict append-to-end. Ensure exactly one trailing newline before the appended block; ensure exactly one blank line between the appended block and any existing content; ensure the appended block ends with a single trailing newline.
  - Acceptance: on the touchstone fixture (CLAUDE.md ending with `## Release & Distribution` body), the resulting CLAUDE.md has the two new `## ` sections after the existing `## Release & Distribution` body, with no existing `### ` sub-heading reparented.
  - Test: `tests/test_init_imports_placement.py` — fixture provides a CLAUDE.md with `## A`, `## B`, `### B.1` structure. After init, asserts that `### B.1` still has `## B` as its nearest preceding `## ` heading (i.e., did not get reparented to `## Cortex Protocol`).
  - Idempotency test: running init twice does not double-append.

### Slice 2 — Sev-2 fixes (ride-along; ship in same PR if scope allows)

- [ ] **Fix #3: Filter `**/README.md` from Doctrine candidates by default.**
  - Where: same discovery module. Add a default-skip filter for any path matching `**/README.md` in the Doctrine candidate scanner. Surface in scan output as "Skipped (meta-doc filename): <path>".
  - Override: existing `.cortex/.discover.toml` `[[pattern]]` entries with `category = "doctrine"` should still win for explicit user instruction. The default-skip is only a default.
  - Acceptance: on touchstone fixture, no `doctrine/000N-readme.md` is created from any README.md.
  - Test: `tests/test_init_readme_filter.py` — fixture has `principles/README.md`, asserts no Doctrine entry imports it post-init. Add a second fixture with an explicit `.discover.toml` override that opts the README in; assert it IS imported in that case.

- [ ] **Fix #4: Reserve doctrine/0001 for the human-authored "why X exists" entry; auto-imported Doctrine starts at 0100.**
  - Where: same discovery module's numbering logic.
  - Change: when allocating numeric IDs for auto-imported Doctrine entries, start at 0100 (not 0001). Human-authored entries that the user creates with `cortex doctrine draft` (when that ships in v1.x) or by hand will use 0001-0099.
  - Acceptance: on a **non-Touchstone fixture** (project with `principles/foo.md` + `principles/bar.md` but NO `.touchstone-config` / `.touchstone-version` — so Fix #1's skip rule does not apply and absorption proceeds), the first auto-imported Doctrine becomes `doctrine/0100-foo.md`, not `doctrine/0001-foo.md`. (On the Touchstone fixture, Fix #1 ensures zero principles imports — that test exercises Fix #1, not Fix #4. The two fixtures are distinct on purpose.)
  - Test: `tests/test_init_doctrine_numbering.py` — non-Touchstone fixture with two principle files, asserts they are imported as 0100 and 0101 (or higher), not 0001 and 0002.
  - Update Next-Steps text to drop any line that suggests authoring `0001` if `0100+` already exists; it can stay otherwise.

### Slice 3 — Sev-3 / Sev-4 (cosmetic; ship if cheap, else defer to v0.3.0)

- [ ] **Fix #5: state.md Sources should include all files that informed any layer.**
  - Where: same module that writes the scaffolded state.md.
  - Change: when listing Sources, include not just the scan-discovered files (CHANGELOG.md, README.md, hooks/README.md) but also every file whose content was imported into Doctrine. Format: keep the list short — "principles/*.md (6 files imported as Doctrine 0100-0105)" is acceptable.
  - Acceptance: on touchstone fixture, state.md Sources lists at least 9 entries (the 3 scan-discovered + the 6 doctrine sources).
  - Test: extend `test_init_touchstone_managed.py`.

- [ ] **Fix #6: "1 unscoped constraint" output inlines the file:line ref.**
  - Where: the scan-output formatter.
  - Change: instead of "CLAUDE.md/AGENTS.md unscoped constraints: 1 (run `cortex doctor` after init for per-line detail)", print "CLAUDE.md/AGENTS.md unscoped constraints: 1 (AGENTS.md:35 — run `cortex doctor` for full detail)". Reuse the same parser the doctor uses.
  - Acceptance: on touchstone fixture, scan output names the line.
  - Test: extend `test_init_touchstone_managed.py`.

- [ ] **Fix #7: Next steps numbering bug ("step 1, step 3" — missing step 2).**
  - Where: the scan-output writer for the "Next steps:" block.
  - Change: investigate. Likely a conditional that drops step 2 when no Plan candidates were found, but doesn't renumber. Renumber so steps are always 1, 2, 3, ... in sequence.
  - Acceptance: on touchstone fixture, scan output's Next steps numbering is contiguous.
  - Test: snapshot test on the scan output structure.

- [ ] **Fix #8: `cortex --status-only --path X` accepts `--path`.**
  - Where: top-level CLI flag definition.
  - Change: top-level `--status-only` should accept the same `--path` option that `cortex status` subcommand does.
  - Acceptance: `cortex --status-only --path /tmp/foo` works the same as `cortex status --path /tmp/foo`.
  - Test: `tests/test_cli_top_level_flags.py`.

### Slice 4 — Release ritual

- [ ] Bump version in `src/cortex/__init__.py` to **0.2.4** (regardless of which slices landed in the release-cutting PR — v0.2.4 is the next patch). If Slice 1 + ride-alongs all shipped together, v0.2.4 covers them all. If only Slice 1 shipped and Slices 2/3 land in a follow-up PR after v0.2.4 is tagged, that follow-up release becomes v0.2.5. The version-bump PR includes only the slices being released.
- [ ] Bump version in `pyproject.toml` to match.
- [ ] Tag, GitHub Release with notes covering each fix slice that landed in this release.
- [ ] Update Homebrew formula `url` + `sha256` in `autumngarage/homebrew-cortex`.
- [ ] Add a `release` journal entry to `.cortex/journal/` once the v0.3.0 `release` template lands; for v0.2.4 itself, write a hand-authored journal entry of `Type: pr-merged` for the release PR.

### Slice 5 — Re-test on touchstone

- [ ] After v0.2.4 lands on Homebrew, roll back the current touchstone install (`cd ~/Repos/touchstone && rm -rf .cortex && git checkout CLAUDE.md AGENTS.md .gitignore`) and re-run `cortex init -y --path ~/Repos/touchstone`.
- [ ] Verify each Success Criterion (1-7 above) on the re-installed result.
- [ ] Capture the re-test as a journal entry on cortex's `.cortex/`. If clean, this entry is what closes this plan (Status: shipped, Promoted-to: that journal entry).
- [ ] If the re-test surfaces new bugs, scope them as v0.2.5+ patch series in a successor plan; do not let scope creep extend this plan.

## Follow-ups (deferred)

Each item resolves to [`journal/2026-04-24-init-ux-fixes-plan-decision`](../journal/2026-04-24-init-ux-fixes-plan-decision.md) (the same-commit decision journal that records the plan's filing rationale and parks these items with explicit revisit conditions). Per SPEC § 4.2, no orphan deferrals.

- **Sev-3 #6 (state.md / map.md Phase C terminology in scaffolded outputs)** — Generator strings shipped in `cortex init` v0.2.3 reference "Phase C" instead of v0.4.0 / v1.x. **Decision: ride along in v0.2.4** (Slice 3 — fold into the same scaffolding-template touch as Sev-3 #5). Parked entry #1 in [`journal/2026-04-24-init-ux-fixes-plan-decision`](../journal/2026-04-24-init-ux-fixes-plan-decision.md) — no separate revisit needed; ships in this plan.
- **`cortex init --dry-run` flag** — preview-mode for the absorb scan. Sev-2 fixes (README filter + 0100-numbering reservation) reduce the original argument's force. Parked entry #2 in [`journal/2026-04-24-init-ux-fixes-plan-decision`](../journal/2026-04-24-init-ux-fixes-plan-decision.md); revisit at v0.3.0 kickoff if conditions hold.
- **`--audit-instructions` extension to "is Cortex pulling its weight?"** — broader audit shape than v0.5.0's external-claim scope; would warrant its own command or a config-toggled mode. Parked entry #3 in [`journal/2026-04-24-init-ux-fixes-plan-decision`](../journal/2026-04-24-init-ux-fixes-plan-decision.md); revisit at v0.5.0 exit-bar review.
- **Stub-Doctrine detection in `cortex doctor`** — flag Doctrine entries whose body is solely a "See `<path>` for the full content" pattern. Requires a new SPEC § 3.1 `Status:` enum value plus a doctor invariant — too large for v0.2.4. Parked entry #4 in [`journal/2026-04-24-init-ux-fixes-plan-decision`](../journal/2026-04-24-init-ux-fixes-plan-decision.md); revisit during v0.6.0 invariant expansions if conditions hold.
