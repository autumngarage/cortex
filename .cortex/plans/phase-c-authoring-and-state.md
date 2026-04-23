---
Status: active
Written: 2026-04-23
Author: human
Goal-hash: 45e97ff9
Updated-by:
  - 2026-04-23T15:00 claude-session-2026-04-23 (created as reordered Phase C; absorbs journal-draft / plan-spawn from old PLAN.md Phase D and deterministic refresh-state from old phase-c-first-synthesis)
Cites: ../../SPEC.md, ../../.cortex/protocol.md, ../../PLAN.md § Phase C, ../doctrine/0001-why-cortex-exists, ../doctrine/0005-scope-boundaries-v2, journal/2026-04-23-phase-c-reordered
---

# Phase C — Authoring and deterministic state

> Make journaling cheap and keep `state.md` current automatically, so a new session (or a session after a crash) can pick up where the last one left off. No LLM dependency in this phase — every command is deterministic, idempotent, and works on a machine that doesn't have `claude` on PATH.

## Why (grounding)

The stated value of Cortex is a rolling idea of what the project is for, where it is now, and what's next, plus a searchable record of what we did and why, so a session can resume after disconnection ([`doctrine/0001-why-cortex-exists`](../doctrine/0001-why-cortex-exists.md)). Phase B shipped the *read* side of that (`cortex manifest`, `cortex grep`, `cortex status`). The *write* side is still manual: journal entries are hand-authored at session boundaries; `state.md` is hand-edited; most session-to-session continuity relies on the author remembering to write things down.

This phase closes the write-side gap with the **deterministic** slice of authoring — template-driven journal drafting with context pre-filled from git/gh, plan scaffolding, and a `refresh-state` that walks plans + recent journal entries and produces the same output twice given the same inputs. LLM-enhanced synthesis (`refresh-map`, `refresh-state --enhance`) is deferred to Phase E ([`plans/phase-e-synthesis-and-governance`](./phase-e-synthesis-and-governance.md)) — it's valuable, but not the feature that makes the system work; it's polish on top of a system that needs to work first.

Grounded in [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #7 (not cloud-hosted — `claude -p` is an *optional* enhancement to regeneration, never a hard dependency of the storage layer).

## Success Criteria

This plan is done when the following hold against this repo (dogfood gate):

1. `cortex journal draft <type>` writes `.cortex/journal/YYYY-MM-DD-<slug>.md` from the matching template under `.cortex/templates/journal/`, with frontmatter (`Date`, `Type`, `Trigger`, `Cites`) pre-filled. When invoked inside a git work tree with a current branch, the draft body is pre-populated from the branch's recent commit messages; when `gh pr view` resolves an open PR for the branch, the PR title + body are used instead. Default action opens `$EDITOR`; `--no-edit` writes and exits with the draft path on stdout.
2. `cortex plan spawn <slug>` creates `.cortex/plans/<slug>.md` with full seven-field frontmatter (`Status: active`, `Written`, `Author`, `Goal-hash`, `Updated-by` seeded, `Cites`) and all required sections per SPEC § 3.4 (`## Why (grounding)`, `## Success Criteria`, `## Approach`, `## Work items`, `## Follow-ups (deferred)`). Prompts for `--title` (first argument is the file slug, prompt is the goal statement) and `--grounds-in <ref>` (citation that lands under `## Why (grounding)`); both accept flags to run non-interactively. Goal-hash is computed from the title per § 4.9.
3. `cortex plan status` walks `.cortex/plans/*.md`, parses Work-items checkboxes, and reports per-plan completion percentage; flags any `Status: active` plan whose last `Updated-by:` entry is older than 14 days AND has open checkboxes as stale. `--json` emits machine-readable output.
4. `cortex refresh-state` writes `.cortex/state.md` with a complete seven-field header (`Generated`, `Generator: cortex refresh-state vX.Y.Z`, `Sources`, `Corpus`, `Omitted`, `Incomplete: []`, `Conflicts-preserved`) and a body composed of: (a) an auto-generated Active Plans section (listing each `Status: active` plan with its goal statement and completion %); (b) an auto-generated Shipped Recently section (last N journal entries by Date, newest first, default N=10); (c) an auto-generated Known stale-now section (any active plans past the 14-day threshold). Sections between `<!-- cortex:hand -->` and `<!-- cortex:end-hand -->` markers are preserved verbatim — these hold the human-authored P0/P1/P2 prioritization. Running twice on unchanged inputs produces a byte-identical file.
5. Journal entries written via `cortex journal draft` and state regenerated via `cortex refresh-state` survive `cortex doctor` clean on this repo, including the seven-field contract (SPEC § 4.5) and the derive-from-preconditions rule ([`journal/2026-04-23-derive-success-from-preconditions`](../journal/2026-04-23-derive-success-from-preconditions.md)) — the state file's hand-authored regions are declared complete, never half-maintained.
6. Dogfood gate: for the week following v0.3.0 release, at least 80% of new journal entries on this repo (and any concurrent work on Sentinel/Touchstone) are authored through `cortex journal draft` rather than hand-written. Measured by a one-off count at the Phase C exit commit.

## Approach

**No LLM calls.** Every command in this phase is deterministic — templates + git/gh introspection + frontmatter parsing. The cost is slightly higher per-invocation friction (the draft body isn't as polished as a hypothetical LLM draft), but the benefit is enormous: the command works on a fresh laptop with just `brew install`, the output is the same every run, and diff noise on regenerated files is zero.

**Journal draft types** reuse the templates that already ship in `.cortex/templates/journal/` from Phase B: `decision.md`, `incident.md`, `plan-transition.md`, `sentinel-cycle.md`, `pr-merged.md`. The command's job is filling frontmatter (`Date: today`, `Type: <arg>`, `Trigger: <inferred-or-provided>`) and pre-populating sections from the context that's available without asking the user — which means leaning on `git log`, `git diff --stat`, and `gh pr view --json` (gracefully degrading when `gh` isn't authenticated).

**`refresh-state` uses a marker convention** instead of a full-rewrite so the human-authored priority blocks (the P0/P1/P2 ranking that gives the file its judgment value) survive regeneration. The sections between `<!-- cortex:hand -->` and `<!-- cortex:end-hand -->` are never touched by the refresh; everything outside those markers is regenerated from scratch. This keeps the command deterministic without requiring the synthesis to reproduce human prioritization.

**Spec validation rides along.** Writing `refresh-state` forces the seven-field contract's first real non-stub implementation — if the spec has a bug in that contract, it surfaces here. Any spec amendment discovered during this phase lands in the same PR as the fix, with a journal entry per [`doctrine/0003-spec-is-the-artifact`](../doctrine/0003-spec-is-the-artifact.md).

## Work items

- [ ] **`cortex journal draft <type>`** — writes a journal entry from the matching template, pre-filled from `git log` + `gh pr view` context. Opens `$EDITOR` by default; `--no-edit` writes and exits. Absorbs the "`cortex journal draft <type>`" item from the old PLAN.md Phase D.
- [ ] **`cortex plan spawn <slug>`** — scaffolds a Plan file with seven-field frontmatter and all required sections. Prompts for title and grounding citation; both have corresponding flags. Goal-hash computed from title. Absorbs "`cortex plan spawn <name>`" from old Phase D.
- [ ] **`cortex plan status`** — per-plan completion + staleness report. `--json` for scripting. Absorbs "`cortex plan status`" from old Phase D.
- [ ] **`cortex refresh-state` (deterministic)** — regenerates `.cortex/state.md` from plans + journal with seven-field header and marker-preserved hand-authored sections. Absorbs the non-LLM portion of the "`cortex refresh-state`" work item from old [`phase-c-first-synthesis`](./phase-c-first-synthesis.md) (now cancelled); the LLM enhancement path becomes `--enhance` in Phase E.
- [ ] **Tests** — each command has real-filesystem tests (no mocked subprocess for `gh` or `git`); `refresh-state` has an idempotency test that runs it twice and asserts byte-equal output; `cortex doctor` runs clean on this repo after each refresh in CI.
- [ ] **v0.3.0 release** — tag + GitHub Release + Homebrew formula SHA update, same shape as v0.1.0.

## Follow-ups (deferred)

Nothing deferred at plan creation. Items move here only when scope actually shifts during execution, per SPEC § 4.2.
