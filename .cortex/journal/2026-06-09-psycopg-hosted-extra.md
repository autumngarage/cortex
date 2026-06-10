# Add psycopg as the optional `hosted` extra — the first executable SQL dependency

**Date:** 2026-06-09
**Type:** decision
**Trigger:** T1.5
**Cites:** plans/hosted-decision-reviewer, journal/2026-06-09-pr-merged-pr477, docs/hosted-ledger.md

> psycopg ships as an optional `cortex[hosted]` extra so the core CLI install stays driver-free while the hosted substrate gains its first executable SQL path (cortex#472).

## Context

The hosted Postgres substrate landed as SQL strings (PRs #477–#483, schema
version 6 in `src/cortex/hosted/schema.py`): no driver, no migration runner,
all 86+ hosted tests string assertions. Issue #472 adds the first code that
executes SQL against a real Postgres — a connection policy
(`src/cortex/hosted/db.py`), a migration runner that applies the shipped
`create_schema_sql()` DDL (`src/cortex/hosted/migrations.py`), and
DATABASE_URL-gated integration tests. That requires a driver dependency, and
the dependency choice is a packaging boundary: Cortex-core is a leaf in the
quartet runtime DAG, the brew formula stays dependency-light, and `cortex
init`/`doctor`/`manifest` users must never pay for a Postgres driver.

## What we decided

`psycopg[binary]>=3.1,<4` is declared as an **optional extra** named
`hosted` in `pyproject.toml` — mirroring the existing `semantic` extra
pattern — not a core dependency. `cortex.hosted.db` imports psycopg lazily
inside `connect()` and raises a visible `HostedDbError` naming
`pip install 'cortex[hosted]'` when the driver is absent (same shape as the
`cortex[semantic]` probe in `retrieve/embeddings.py`). Alternatives weighed:
a core dependency (rejected — violates the standalone-boundary rule and
bloats the brew formula for local-only users) and a separate package
(rejected — premature; the hosted code already lives in this repo behind the
`cortex.hosted` namespace).

## Consequences / action items

- [x] Lazy import with visible install hint in `src/cortex/hosted/db.py`
- [x] `HostedDbError`/`HostedMigrationError` registered in the degradation
      taxonomy as `fail_closed_refusal` (guardrail test enforces this)
- [ ] Railway compass Postgres run of `tests/test_hosted_db_integration.py`
      to verify pgcrypto/pg_trgm/vector on the live image (full closure of
      #472, unblocking #467/#484 closure evidence)
