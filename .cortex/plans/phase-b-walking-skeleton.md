---
Status: active
Written: 2026-04-17
Author: human
Goal-hash: 1f10782a
Updated-by:
  - 2026-04-17T07:00 human (created; targeted v0.1.0)
  - 2026-04-17T23:45 claude-session-2026-04-17 (updated for v0.2.0 scope — Protocol, promotion queue, seven-field metadata contract)
  - 2026-04-17T23:59 claude-session-2026-04-17 (refreshed for v0.3.0-dev scope — adds manifest, grep, expanded doctor checks, T1.9 audit, Goal-hash verification, interactive flow, Load-priority validation)
Cites: ../../SPEC.md, ../../.cortex/protocol.md, ../../PLAN.md § Phase B, doctrine/0003-spec-is-the-artifact, doctrine/0005-scope-boundaries-v2
---

# Phase B — Walking-skeleton CLI

> Ship a Cortex CLI that manipulates `.cortex/` structure without any LLM calls. End-state: `brew install cortex && cortex init` produces a SPEC.md v0.3.0-dev-conformant scaffold (including `protocol.md` + `templates/`) in a fresh repo; `cortex doctor` validates every SPEC.md § 4 rule against it; the interactive `cortex` entrypoint surfaces the promotion queue and overdue digests per the README UX example. No synthesis yet — that's Phase C.

## Why (grounding)

Phase A shipped the spec and the repo. The evening of the same day tightened the Protocol (T1.9, manifest rules, CLI-less fallback) and the SPEC (`Load-priority:` on Doctrine, Goal-hash normalization, seven-field metadata contract) — see `journal/2026-04-17-vision-v3-promoted.md` and `journal/2026-04-17-protocol-sharpened-and-drafts-archived.md`. The result is a spec that is now internally consistent but not yet enforced by tooling. Phase B closes that gap:

- **Protocol Tier 1 triggers are auditable only if `cortex doctor --audit` exists.** Without the audit, the Protocol is a description, not a contract.
- **Promotion-queue operational rules (SPEC § 4.7), single-authority-rule (§ 4.8), multi-writer collisions (§ 4.9), and digest depth-cap/audit-sampling (§ 5.3–5.4)** each need a doctor check to have teeth.
- **The CLI-less fallback story** (Protocol § 1) is the distribution-race floor; `cortex doctor` must warn when a project relies on the fallback against a corpus too large for grep-by-recency to suffice.
- **The interactive `cortex` entrypoint** is the human-facing promise in the README and the one-command UX story. Nothing else surfaces the promotion queue at session cadence.
- **First dogfood:** this very repo's `.cortex/` is the first test subject. `cortex doctor` running clean here is the proof that the spec and the implementation are in sync.

Synthesis stays out of Phase B because it's expensive and variable; a walking skeleton lets us validate every structural rule before any LLM call happens.

## Success Criteria

This plan is done when all of the following hold on a fresh macOS install:

1. `brew tap autumngarage/cortex && brew install cortex` succeeds.
2. In an empty git repo: `cortex init` creates `.cortex/SPEC_VERSION` (`0.3.0-dev`), copies `.cortex/protocol.md` and the full `.cortex/templates/` tree, scaffolds `doctrine/`, `plans/`, `journal/`, `procedures/`, and stubs `map.md` + `state.md` with seven-field `Generated:` headers in `(pending Phase C synthesis)` state.
3. `cortex doctor` on that fresh `.cortex/` prints "spec v0.3.0 conformant" and exits 0.
4. `cortex doctor` on **this repo's** `.cortex/` also exits 0. Dogfood gate.
5. `cortex doctor` detects and reports each seeded violation: orphan deferral in a Plan; missing Success Criteria; unknown spec major version in `SPEC_VERSION`; Doctrine entry without `Load-priority:`; Plan with a `Goal-hash:` that doesn't match SPEC § 4.9 normalization; two Plans with colliding `Goal-hash:` values; Journal entry edited in place (append-only violation); Doctrine entry modified with Status still `Accepted`; root-file (`AGENTS.md`/`CLAUDE.md`) content duplicating Doctrine without `grounds-in:`.
6. `cortex doctor --audit` detects a missing Journal entry for each Tier 1 trigger fired in the git-log window (T1.1–T1.9). Seeded test: commit a dependency-manifest change (T1.5) without a journal entry → doctor flags it.
7. `cortex doctor --audit-digests` picks N random claims from a seeded digest and reports claim→source-entry verification pass/fail.
8. `cortex manifest --budget 8000` on this repo emits a budgeted session-start slice: full `state.md`, all `Load-priority: always` Doctrine, active Plans, last-72h Journal + latest digest (if present), promotion-queue depth summary. Output is valid Markdown.
9. `cortex grep <pattern>` returns matches from `.cortex/` with frontmatter-aware highlighting (entry title, Date, Type surfaced per match). Falls back to ripgrep output on flag.
10. Interactive `cortex` (no subcommand) prints the README-example output: status line + Journal counts since last check + promotion candidates with `[trivial]`/`[editorial]`/`[stale]` tags and y/n/view/defer/skip prompts + overdue-digest prompt + "Anything else?" tail. Works against this repo's `.cortex/`.
11. `cortex --status-only` emits the status line alone for scripting.
12. `cortex --promote <candidate-id>` performs a flag-style promotion end-to-end (adds a new Doctrine entry from the `doctrine/candidate.md` template, sets `Promoted-from:` on the new entry and `Promoted-to:` on the source Journal entry).
13. `cortex version` prints CLI version, supported SPEC versions (reads `SUPPORTED_SPEC_VERSIONS`), supported Protocol versions, install method.
14. All tests pass (`uv run pytest`) — temp-dir fixtures, no mocked filesystem.
15. A git-tagged v0.1.0 release exists at `github.com/autumngarage/cortex`, with the Homebrew formula at `autumngarage/homebrew-cortex` pointing at it with the correct SHA. CLI v0.1.0 targets spec v0.3.0-dev (versions are independent per Doctrine 0003).

## Approach

Python CLI built on `click` (matches Sentinel's stack), src-layout package under `src/cortex/`. Entrypoint via `pyproject.toml`'s `[project.scripts]`. Distribution: `uv tool install .` for source; Homebrew tap for `brew`.

The CLI's dispatch mirrors Touchstone's pattern — a thin `src/cortex/cli.py` that routes to per-command modules under `src/cortex/commands/`. No daemon, no background work, no project-level config file at this phase.

Spec validation (`cortex doctor`) is implemented as a set of pure-function checks, each keyed to a SPEC.md § 4 / § 5 rule. Each check returns `(check_id, ok, violations)`. The CLI formats and exits with an aggregated code. Adding a new rule means adding a new check module — no cross-cutting changes.

`cortex init` **copies this repo's `.cortex/protocol.md` and `.cortex/templates/` verbatim into the target project.** This is the single source of truth for protocol text that every Cortex project starts from; projects then customize per Protocol § 6. Shipped via Python package data (MANIFEST.in / pyproject package-data).

`cortex manifest` reads `.cortex/` structure, selects content per Protocol § 1 default allocation (state.md full, Load-priority pins + recency Doctrine, active Plans, recent Journal, queue summary), and renders to stdout as a single Markdown document with a seven-field metadata header. No LLM calls.

`cortex grep` wraps ripgrep with `--glob '.cortex/**/*.md'` and post-processes matches to surface the entry's frontmatter header (title, Date, Type) alongside the hit.

Interactive `cortex` (no args) is a prompt-toolkit (or click-prompt) loop over the promotion queue and overdue-digest list, state-machine driven so keyboard input (`y/n/view/defer/skip`) advances deterministically.

Brew formula mirrors Touchstone's: `url` points at a tagged GitHub release tarball, `sha256` captured at release, `depends_on "git"`, `depends_on "ripgrep"` (for `cortex grep`).

## Work items

### Scaffold + plumbing

- [ ] **Python project scaffold** — `pyproject.toml` with `click`, `prompt_toolkit` (or click-prompt), `pytest`, `ruff`, `mypy` as dev deps; `src/cortex/__init__.py` with `__version__`; `src/cortex/cli.py` click entrypoint; package data for `.cortex/protocol.md` + `.cortex/templates/`; `uv.lock` committed.
- [ ] **Ruff + mypy configuration** — match Sentinel's `pyproject.toml` settings; add to `touchstone-run.sh validate` flow.
- [ ] **`cortex version`** — prints CLI version, `SUPPORTED_SPEC_VERSIONS` (currently `['0.3']` — accepts any `0.3.x` including pre-release qualifiers like `-dev`), `SUPPORTED_PROTOCOL_VERSIONS` (currently `['0.2']`), install method. Matching rule: major.minor match; the CLI warns on unknown major and accepts any minor/patch within a supported major.

### Init + structural commands

- [ ] **`cortex init`** — scaffolds `.cortex/` per SPEC.md § 2. Copies `protocol.md` + full `templates/` tree from package data. Idempotent (refuses to overwrite existing `.cortex/SPEC_VERSION` unless `--force`). Stubs `map.md`/`state.md` with seven-field `(pending)` headers. Seeds one Doctrine 0001 stub (`# 0001 — Why this project exists`).
- [ ] **`cortex status` / `cortex --status-only`** — parses `Generated:` timestamps and plan statuses; prints compact freshness table + promotion-queue depth.
- [ ] **`cortex grep <pattern>`** — frontmatter-aware ripgrep wrapper over `.cortex/**/*.md`.
- [ ] **`cortex manifest --budget <N>`** — emits session-start slice per Protocol § 1 defaults; seven-field metadata header; Markdown output. `--format json` for programmatic consumers.

### Doctor checks (one module per rule)

- [ ] **Structural** — SPEC § 2 directory layout; SPEC_VERSION presence + parseable; `protocol.md` + `templates/` present.
- [ ] **Seven-field metadata** — SPEC § 4.5 on Map, State, and any `Type: digest` Journal entries.
- [ ] **Plan grounding** — SPEC § 4.1 every Plan's `Why (grounding)` cites Doctrine/State/Journal.
- [ ] **Deferral tracking** — SPEC § 4.2 no orphan deferrals.
- [ ] **Measurable success criteria** — SPEC § 4.3 Plan Success Criteria names a signal.
- [ ] **Typed-link checks** — SPEC § 4.6 supersede/promoted-from/grounds-in presence on entries that need them.
- [ ] **Promotion-queue invariants** — SPEC § 4.7 WIP limit, candidate aging, state enum validity.
- [ ] **Single authority rule** — SPEC § 4.8 detect Cortex-claim duplication in `AGENTS.md`/`CLAUDE.md`/`.cursor/rules/*` without `grounds-in:` back-citation.
- [ ] **Goal-hash verification** — SPEC § 4.9 recompute each Plan's `Goal-hash:` from its H1 title; flag mismatches and collisions.
- [ ] **Load-priority validation** — SPEC § 3.1 every Doctrine entry has `Load-priority:` (`always` or `default`); warn if `always` set exceeds default Doctrine budget.
- [ ] **Append-only Journal** — git-log walks for in-place edits of `journal/*.md` files (SPEC § 3.5); warn.
- [ ] **Immutable Doctrine** — git-log walks for in-place content edits of `doctrine/*.md` files whose Status is `Accepted` and whose Status field is not the only changed line (the only mutable field per SPEC § 3.1).
- [ ] **CLI-less fallback warning** — detect `AGENTS.md` imports of `@.cortex/state.md` without `cortex` CLI presence; warn if corpus >20 Doctrine or >100 Journal.

### Audits (require git-log walks)

- [ ] **`cortex doctor --audit`** — walks git log for the session window (default: `HEAD~N..HEAD`, configurable; N defaults to 20); for each qualifying Tier 1 event (T1.1–T1.9) verifies a corresponding Journal entry was written with matching `Trigger:` frontmatter. Missing = warning (solo) or error (triad).
- [ ] **`cortex doctor --audit-digests`** — picks N random claims from each digest, verifies each traces to at least one source entry named in `Sources:`. N default = 5.
- [ ] **`cortex doctor --strict`** — aggregates all checks with errors (not warnings) as exit-nonzero. Target for Touchstone pre-push integration in Phase E.

### Interactive entry + promotion

- [ ] **Interactive `cortex` (no subcommand)** — matches README UX block: status line, counts, candidate list with `[trivial]`/`[editorial]`/`[stale]` tags, y/n/view/defer/skip prompts, overdue-digest prompt, free-form tail. State persists to `.cortex/.index.json` promotion-queue section.
- [ ] **`cortex --promote <id>`** — scripted equivalent of the interactive flow's promotion step. Writes a **new** Doctrine entry from `doctrine/candidate.md` with `Promoted-from: journal/<date>-<slug>`. Optionally writes a new Journal entry with `Type: promotion` citing both the source and the new Doctrine entry (records the promotion *event* without editing the source). Updates `.cortex/.index.json` to cache the reverse lookup (source Journal → promoted-to Doctrine) — derived, regeneratable. **Never writes `Promoted-to:` into an existing Journal entry** — that would violate SPEC § 3.5 append-only Journal. The canonical promotion link is `Promoted-from:` on the new Doctrine entry; reverse traversal is a read-side concern.

### Distribution

- [ ] **Tests** — `tests/test_<command>.py` per command. `tmp_path` fixtures seed sample `.cortex/` trees (conformant, one-violation-per-test). No mocked filesystem. Test `cortex doctor` against this repo's `.cortex/` as an integration check.
- [ ] **`autumngarage/homebrew-cortex` tap repo** — create via `gh repo create`, seed with placeholder `Formula/cortex.rb`. Formula populated at release.
- [ ] **v0.1.0 release** — tag v0.1.0 on main; `gh release create`; compute tarball SHA; update tap formula; push tap. Verify `brew install` on a clean state.
- [ ] **Release verification** — run `cortex init` on a fresh temp repo using the brew-installed binary; `cortex doctor` returns clean. Run `cortex doctor` on this repo; clean.

## Follow-ups (deferred)

All items below resolve to either a named future plan, a journal entry, or an explicit out-of-scope reason — per SPEC § 4.2.

- **Map and State regeneration** → Phase C plan (to be written once Phase B ships). Requires `claude` CLI integration.
- **`cortex plan spawn`, `cortex journal draft`** → Phase D plan. Requires synthesis.
- **Sentinel / Touchstone integration hooks** → Phase E plan. Requires stable synthesis + triad composition design.
- **Auto-update check** → deferred to post-v0.1.0; journal note to be filed when the first out-of-band CLI bug appears and manual `brew upgrade` becomes a friction point.
- **Spec-version migration tooling (`cortex migrate-spec`)** → deferred to the first spec-major-bump trigger event. Until then, single-major-version assumption is fine; the CLI warns on unknown major versions per SPEC § 7 already.
- **`cortex grep` semantic mode** → out of scope. Semantic retrieval is an optional external layer per Doctrine 0005 #1; adding it inside Cortex would violate the boundary.

## Known limitations at exit

- `cortex doctor` validates structural and metadata rules. It cannot validate semantic rules (e.g., "is this Success Criterion *actually* measurable?") until synthesis lands in Phase C.
- Without Map/State regeneration, every project's `map.md`/`state.md` must be hand-authored or remain stubs at `cortex init`. The seven-field metadata contract supports this via `Incomplete:` declarations — acceptable for Phase B.
- The interactive `cortex` flow's terminal rendering (pager, color, keybindings) is the biggest unknown. Worst case: collapse to a simpler one-prompt-at-a-time form.
- `cortex doctor --audit` depends on consistent Tier 1 `Trigger:` labeling on Journal entries. Older Journal entries (pre-v0.2.0 Protocol) may lack the field and should be exempted by date.
- No cross-project state; this plan is single-project by design (Doctrine 0005 #4).
