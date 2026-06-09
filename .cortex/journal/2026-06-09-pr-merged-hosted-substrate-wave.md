# PRs #477-#483 merged — hosted decision-ledger substrate wave (backfill)

**Date:** 2026-06-09
**Type:** pr-merged
**Trigger:** T1.9 (consolidated backfill — written 2026-06-09T17:40 for seven merges earlier the same day; the post-merge hook did not fire on this wave)
**Cites:** plans/hosted-decision-reviewer.md, journal/2026-06-09-hosted-decision-reviewer-plan-adopted
**Merge-commits:** 8f1f1ce (#477), 4c01d87 (#478), f1e52b2 (#479), 3cf4054 (#480), 300daf3 (#481), 4599cff (#482), 17f23e3 (#483)

> Seven PRs landed the Stage 0 Postgres-shaped substrate for the hosted
> decision reviewer in a single wave, closing issues #460-#466 and #468.
> This consolidated entry backfills the missing per-merge T1.9 records.

## What shipped

| PR | Closed | Substance |
|---|---|---|
| #477 | #460, #461 | `src/cortex/hosted/schema.py`, `ledger_events.py`, `storage.py` — Postgres as canonical store; append-only `ledger_events` with no-update/no-delete triggers, idempotency keys, event-hash chaining; decision graph tables (`HOSTED_SCHEMA_VERSION = 6`); fail-closed `FINDING_EMITTED` replay material |
| #478 | #462 | `provenance.py` — `SourceDocument`/`SourceSpan` with content/document/span hashes, `source_event_id` FKs, immutability triggers |
| #479 | #463 | `scopes.py` — 9-type scope model + `decision_scopes` index + 5 indexes |
| #480 | #464 | `ask_ledger.py` — hybrid cited retrieval; `AnswerState.NO_ANSWER` / `no_cited_support` fail-closed answer packs |
| #481 | #465 | `decisions_for_diff.py` — structural scope candidates + bounded hybrid RRF fusion capped at 30 with omitted counts |
| #482 | #466 | `embeddings.py` — pgvector projection keyed by model + epoch, recall checks |
| #483 | #468 | `visibility.py` — tenant/source visibility deny flags enforced in retrieval |

## Caveat recorded for the roadmap

The substrate is non-executing: SQL strings and dataclasses with
string-assertion tests (86 of them); no Postgres driver, migration runner, or
CLI wiring. The first executable SQL path is issue #472 (Stage 1); #467 and
#484 require it for full closure.

## Closes / advances

- **Plans:** advances `plans/hosted-decision-reviewer` — the "Stage 0
  substrate" work item is now ticked with this wave as evidence.
- **Issues:** closed #460-#466, #468 at merge time; ten further issues were
  closed 2026-06-09 as shipped-by-this-wave after review (#306, #311, #312,
  #317, #321, #324, #364, #365, #366, #383 — see
  `journal/2026-06-09-roadmap-refinement-and-issue-hygiene`).

## Triggers fired

- T1.9 (x7, consolidated here as backfill)
- T1.5 fired on none (no dependency manifest changes in the wave)
