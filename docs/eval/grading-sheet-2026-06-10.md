# Stage 0 grading sheet — the #378 pass

**Run:** docs/eval/replay-450-v2-2026-06-10.json (14-fixture corpus, live claude CLI, grader v2)
**Your job:** one checkbox per row. `useful` = you would want this comment on a real PR. ~20 minutes.
**The bar (#378):** >= 70% of *emitted* findings graded useful.

## Pre-graded by construction (matched expectations) — verify-only

- [ ] **consolidated-journal-entries-001** — 1 matched, 0 class-divergent. Default: useful. Override only if the citation reads wrong.
- [ ] **journal-entry-deletion-001** — 1 matched, 0 class-divergent. Default: useful. Override only if the citation reads wrong.
- [ ] **simlab-chatty-startup-worker-threads-001** — 1 matched, 0 class-divergent. Default: useful. Override only if the citation reads wrong.
- [ ] **simlab-clean-shop-retry-fixed-delay-001** — 1 matched, 0 class-divergent. Default: useful. Override only if the citation reads wrong.
- [ ] **simlab-legacy-migration-runbook-anchor-001** — 0 matched, 1 class-divergent. Default: useful. Override only if the citation reads wrong.
- [ ] **spec-version-drift-001** — 0 matched, 1 class-divergent. Default: useful. Override only if the citation reads wrong.
- [ ] **standalone-boundary-violation-synthetic-001** — 1 matched, 0 class-divergent. Default: useful. Override only if the citation reads wrong.

## The judgment calls (unexpected emissions) — read each

### consolidated-journal-entries-001
- class: `contradicts-prior-decision` vs decision `pr-merged-entry-per-merge`
- summary: New journal entry 2026-06-09-pr-merged-hosted-substrate-wave.md consolidates seven default-branch merges (#477-#483) into one pr-merged entry with a plural 'Merge-commits:' field listing seven shas ('T1.9 (x7, consolidated here as backfill)'), conflicting with the confirmed decision requiring exactly one pr-merged entry per merge with a singular Merge-commit sha.
- [ ] useful  /  [ ] not useful (spam)  /  [ ] fixture label wrong (file follow-up)

### outrider-contract-version-omitted-001
- class: `contradicts-prior-decision` vs decision `outrider-api-version-boundaries`
- summary: The diff makes semantic changes to the /v1/agents/{name} response — n_proposals/n_resolved/brier are rescoped from all agent_predictions rows to current-model_version rows only, monthly_history gains a model_version filter, calibration-state lookup is rescoped from the hardcoded '<agent>_v1' cohort to current_model_version() ('<agent>_v2'), and realized-PnL proposal selection is newly filtered by payload model_version — but it neither bumps AGENT_DETAIL_RESPONSE_VERSION nor ships the docs/CONTRACT.md compatibility/migration update in the same PR (the docstring even claims the methodology 'is documented under docs/CONTRACT.md', which the diff leaves untouched). This silently rescopes which model_version cohort backs the public response, violating the confirmed versioned-boundary decision.
- [ ] useful  /  [ ] not useful (spam)  /  [ ] fixture label wrong (file follow-up)

### touchstone-managed-principles-001
- class: `contradicts-prior-decision` vs decision `principles-touchstone-managed`
- summary: The diff hand-edits four files under principles/ in this repo (modifies principles/README.md and principles/git-workflow.md, adds principles/agent-swarms.md and principles/file-upstream-bugs.md), conflicting with the confirmed decision that principles/ and scripts/ are Touchstone-managed and change only via touchstone update sync. Nothing in the diff indicates it is the output of a touchstone sync; new content also cross-references scripts/spawn-worktree.sh and scripts/setup-worktree-local.sh, which are likewise Touchstone-owned surfaces.
- [ ] useful  /  [ ] not useful (spam)  /  [ ] fixture label wrong (file follow-up)

### vesper-lucide-icon-vocabulary-001
- class: `contradicts-prior-decision` vs decision `vesper-design-system-tokens`
- summary: The confirmed decision requires all icons to go through LucideImage/LucideLabel and explicitly forbids Image(systemName:) — SF Symbols are not part of the icon vocabulary. The diff replaces LucideImage with Image(systemName:) at multiple sites (info.circle, checkmark, plus.diamond, wifi.slash, cursorarrow, chevron.left.forwardslash.chevron.right in AIInvitePopover.swift and WindowWatchButton.swift) and adds a test ('AI picker generic chrome uses SF Symbols') that locks in the violation. It also moves AIInviteRow off the ButtonStyles.swift helper (vesperPopoverRowStyle) onto raw .buttonStyle(.borderless), bypassing the decision that button styles come from ButtonStyles.swift.
- [ ] useful  /  [ ] not useful (spam)  /  [ ] fixture label wrong (file follow-up)

### vesper-workspace-sheet-tokens-respected-001
- class: `contradicts-prior-decision` vs decision `vesper-design-system-tokens`
- summary: NewWorkspaceSheet.swift adds Cancel and Create buttons using plain SwiftUI Button with default styling instead of the button styles from ButtonStyles.swift required by the design-system decision; the adjacent Choose button correctly applies .vesperGhostButtonStyle(), making the omission on Cancel/Create inconsistent with the confirmed rule that all Vesper UI uses ButtonStyles.swift. Icon usage complies (LucideImage/LucideLabel only, no Image(systemName:)) and spacing/typography/theme tokens come from DesignSystem.swift.
- [ ] useful  /  [ ] not useful (spam)  /  [ ] fixture label wrong (file follow-up)

## Misses (no grading needed — already counted against)

- outrider-contract-version-omitted-001: missed_shadow 1 (shadow class — correctly not emitted; counts toward shadow-precision data, not this bar)
- vesper-lucide-icon-vocabulary-001: missed 1 (vesper near-miss: caught the violation, cited the sibling decision — dedup/granularity follow-up)

## Arithmetic (fill after grading)

- emitted findings graded useful: __ / 12 (5 matched + 2 class-divergent + 5 unexpected)
- bar: >= 70% → verdict feeds docs/eval + the #337 report
