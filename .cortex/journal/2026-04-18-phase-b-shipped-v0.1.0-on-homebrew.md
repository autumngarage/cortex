# Plan phase-b-walking-skeleton — active → shipped

**Date:** 2026-04-18
**Type:** plan-transition
**Trigger:** T1.3
**Cites:** plans/phase-b-walking-skeleton, plans/phase-c-first-synthesis, https://github.com/autumngarage/cortex/releases/tag/v0.1.0, https://github.com/autumngarage/homebrew-cortex

> Phase B shipped as Cortex v0.1.0 on Homebrew; 10 of 15 success criteria met as written, 5 explicitly scope-shifted to Phase C with same-commit resolutions to [`plans/phase-c-first-synthesis`](../plans/phase-c-first-synthesis.md).

## Context

The Phase B plan targeted a walking-skeleton CLI that scaffolds and validates `.cortex/` without any LLM calls, so later phases have something to extend and the Protocol's Tier-1 triggers have a `cortex doctor --audit` to enforce them. Seven slices shipped over 2026-04-17/18 (Python scaffold + `version`, `init`, `doctor` basic, `manifest`, `grep`, `doctor --audit` + `--audit-digests`, `status` + `promote` stub) and v0.1.0 tagged + the `autumngarage/homebrew-cortex` tap published today closed out distribution.

## Transition

- **From:** active
- **To:** shipped
- **Reason:** 10 of 15 success criteria met as written; the remaining 5 are scope shifts to Phase C (they require either `.cortex/.index.json` to be populated by the refresh commands or the interactive promotion flow that depends on it). Scope shifts are recorded explicitly below with same-commit resolutions per SPEC § 4.2.

## Outcome against success criteria

Each of the Phase B plan's numbered success criteria, quoted and marked.

1. *"`brew tap autumngarage/cortex && brew install autumngarage/cortex/cortex` succeeds…"* — **Met.** Verified end-to-end on macOS; `cortex version` prints `0.1.0` with install method `homebrew`.
2. *"In an empty git repo: `cortex init` creates `.cortex/SPEC_VERSION` (`0.3.1-dev`), copies `.cortex/protocol.md` and the full `.cortex/templates/` tree, scaffolds `doctrine/`, `plans/`, `journal/`, `procedures/`, and stubs `map.md` + `state.md` with seven-field `Generated:` headers…"* — **Met.** Covered by `tests/test_init.py` + manual verification at release time.
3. *"`cortex doctor` on that fresh `.cortex/` prints \"spec v0.3.1 conformant\" and exits 0."* — **Met in substance.** Exits 0 with the message "cortex doctor: .cortex/ looks healthy …" rather than the exact quoted wording; the quoted string was aspirational and never implemented. Wording change, not a scope change — logged for reference.
4. *"`cortex doctor` on this repo's `.cortex/` also exits 0. Dogfood gate."* — **Met.** Clean throughout the session and clean against the installed Homebrew binary at v0.1.0.
5. *"`cortex doctor` detects and reports each seeded violation: orphan deferral in a Plan; missing Success Criteria; unknown spec major version in `SPEC_VERSION`; Doctrine entry without `Load-priority:`; Plan with a `Goal-hash:` that doesn't match SPEC § 4.9 normalization; two Plans with colliding `Goal-hash:` values; Journal entry edited in place (append-only violation); Doctrine entry modified with Status still `Accepted`; root-file (`AGENTS.md`/`CLAUDE.md`) content duplicating Doctrine without `grounds-in:`."* — **Partially met (scope shift).** Missing Success Criteria, unknown SPEC_VERSION, Doctrine without Load-priority, Goal-hash mismatch, Goal-hash collision all ship and have regression tests. Orphan-deferral detection, append-only-violation detection on Journal, Status-mutation detection on Doctrine, and single-authority-rule drift against root agent files are scope-shifted to Phase C's `cortex doctor` expansion — **resolved to** `plans/phase-c-first-synthesis` work items "Orphan-deferral detection in `cortex doctor`" and "Single-authority-rule drift detection in `cortex doctor`".
6. *"`cortex doctor --audit` detects a missing Journal entry for each Tier 1 trigger fired in the git-log window (T1.1–T1.9). Seeded test: commit a dependency-manifest change (T1.5) without a journal entry → doctor flags it."* — **Partially met.** T1.1, T1.5, T1.8, T1.9 are classified and matched; T1.2, T1.3, T1.4, T1.6, T1.7 are deferred because they need runtime session state or per-commit diff parsing. Non-orphan deferral: these are named in `cortex/audit.py` docstring and already tracked implicitly by the `EXPECTED_TYPE` map. No new plan item required beyond the existing Phase C doctor-expansion work.
7. *"`cortex doctor --audit-digests` picks N random claims from a seeded digest and reports claim→source-entry verification pass/fail."* — **Partially met.** Samples bulleted claims from body (not frontmatter), skips fenced content, warns when >50% lack `journal/...` citations. Full random-sampling with SPEC § 5.4 claim-trace verification is scope-shifted to the Phase C doctor-expansion work items.
8. *"`cortex manifest --budget 8000` on this repo emits a budgeted session-start slice: full `state.md`, all `Load-priority: always` Doctrine, active Plans, last-72h Journal + latest digest (if present), promotion-queue depth summary. Output is valid Markdown."* — **Met.** Plus graceful degradation at <2k (state-only) and widened Journal window at ≥15k.
9. *"`cortex grep <pattern>` returns matches from `.cortex/` with frontmatter-aware highlighting (entry title, Date, Type surfaced per match). Falls back to ripgrep output on flag."* — **Met.** Shell out to `rg --json`, group by file, prepend metadata summary. Fence-aware; handles leading-dash patterns.
10. *"Interactive `cortex` (no subcommand) prints the README-example output: status line + Journal counts since last check + promotion candidates with `[trivial]`/`[editorial]`/`[stale]` tags and y/n/view/defer/skip prompts + overdue-digest prompt + \"Anything else?\" tail. Works against this repo's `.cortex/`."* — **Scope shift.** Bare `cortex` prints the non-interactive status summary (active plans, journal counts, digest age + overdue flag, queue counts). The per-candidate prompt loop and "Generate digest?" prompt require `.cortex/.index.json` to be populated — **resolved to** `plans/phase-c-first-synthesis` work item "Interactive per-candidate prompts in bare `cortex`".
11. *"`cortex --status-only` emits the status line alone for scripting."* — **Met.** Top-level `--status-only` flag + `cortex status` subcommand with `--json`.
12. *"`cortex --promote <candidate-id>` performs a flag-style promotion end-to-end…"* — **Scope shift.** Ships as a validated stub (exits 3 with a "not yet implemented" note when a candidate id is found) because `.index.json` is not yet populated by a refresh command. **Resolved to** `plans/phase-c-first-synthesis` work item "`cortex promote` writer".
13. *"`cortex version` prints CLI version, supported SPEC versions (reads `SUPPORTED_SPEC_VERSIONS`), supported Protocol versions, install method."* — **Met.** Covered by `tests/test_version.py`.
14. *"All tests pass (`uv run pytest`) — temp-dir fixtures, no mocked filesystem."* — **Met.** 111 tests green at the v0.1.0 tag; audit and init tests use real `git init` / real temp dirs; grep tests monkeypatch `subprocess.run` only so ripgrep doesn't need to be on the sandbox PATH (fixture is explicit).
15. *"A git-tagged v0.1.0 release exists at `github.com/autumngarage/cortex`, with the Homebrew formula at `autumngarage/homebrew-cortex` pointing at it with the correct SHA. CLI v0.1.0 targets spec v0.3.1-dev (versions are independent per Doctrine 0003)."* — **Met.** Tag pushed, release published, formula SHA `b21afe5421bf7f6ba9d46326340ebdb482187f0a4925f022334aacecbfdeb1b9` verified against the GitHub-served tarball, `brew install autumngarage/cortex/cortex` succeeds.

## Deferred items

Each deferral resolves to a concrete work item in [`plans/phase-c-first-synthesis`](../plans/phase-c-first-synthesis.md), created in this same commit per SPEC § 4.2 (no orphan deferrals).

- **Orphan-deferral detection** — resolved to: `plans/phase-c-first-synthesis` → "Orphan-deferral detection in `cortex doctor`".
- **Append-only-violation detection on Journal** — resolved to: `plans/phase-c-first-synthesis` → "Append-only-violation detection on Journal in `cortex doctor`".
- **Doctrine immutability / Status-mutation detection** — resolved to: `plans/phase-c-first-synthesis` → "Immutable-Doctrine / Status-mutation detection in `cortex doctor`".
- **Single-authority-rule drift detection** — resolved to: `plans/phase-c-first-synthesis` → "Single-authority-rule drift detection in `cortex doctor`".
- **Promotion-queue invariants** (SPEC § 4.7 WIP limit, candidate aging) — resolved to: `plans/phase-c-first-synthesis` → "Promotion-queue invariants in `cortex doctor`".
- **CLI-less-fallback warning** (Protocol § 1) — resolved to: `plans/phase-c-first-synthesis` → "CLI-less-fallback warning in `cortex doctor`".
- **Full interactive per-candidate promotion flow** (criterion 10) — resolved to: `plans/phase-c-first-synthesis` → "Interactive per-candidate prompts in bare `cortex`".
- **`cortex promote` end-to-end writer** (criterion 12) — resolved to: `plans/phase-c-first-synthesis` → "`cortex promote` writer".
- **Expanded T1.2/T1.3/T1.4/T1.6/T1.7 classification in `cortex doctor --audit`** (criterion 6) — resolved to: the existing Phase C doctor-expansion work (tracked inline in `cortex/audit.py` docstring as the known-deferred set; rolled up under the Phase C plan's `cortex doctor` expansion work rather than carving a separate line item).
- **Full SPEC § 5.4 random-sample claim-trace for `--audit-digests`** (criterion 7) — same rollup as above; not a standalone line item.
