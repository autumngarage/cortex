# PR #185 merged — feat(install-brief): dual-artifact follow-up plan

**Date:** 2026-05-06
**Type:** pr-merged
**Trigger:** T1.9
**Cites:** plans/cortex-v1
**Merge-commit:** 1ec40f1
**Branch:** feat/install-followup-plan

> Closes cortex#182: `cortex install-brief --closes` now produces a journal-baseline (append-only, `Refs:` for issue citations, no `[ ]`) and a separate follow-up plan (`Status: active`, `[ ]` per issue), enforcing the layer contract that tracking lives in Plans, not Journals.

## What shipped

- `_dual_artifact_phase5()` helper in `install_brief.py`: when `--closes` is provided, Phase 5 of the brief instructs writing two files — a journal-baseline template and a follow-up plan template.
- Journal-baseline template: `Type: decision`, `Refs: cortex#N` frontmatter, `Cites: plans/cortex-install-followups`, no `[ ]` boxes.
- Follow-up plan template: `Status: active`, `[ ]` per tracked issue, `Cites: journal/` linking back.
- Without `--closes`: single-artifact behavior preserved (backward-compatible).
- `docs/install-pr-templates.md`: dual-artifact convention documented with the layer-contract rationale (`[ ]` in journals are permanent stale claims because Journal is append-only).
- 8 new tests covering dual-artifact shape, `Refs:` in journal template, `[ ]` in plan template, `Status: active`, cross-`Cites:`, single-artifact backward-compat, artifact output lines.

## Closes / advances

- **Plans:** cortex-v1 — closes cortex#182 (staleness slice 3, architectural fix)
- **Doctrine:** none
- **Journal linkage:** none (work done in a single focused session)

## Follow-ups (deferred to future work)

- Optional: doctor check that warns when journal entries contain `^- [ ]` patterns. Deferred per brief — separate concern, separate PR boundary.
