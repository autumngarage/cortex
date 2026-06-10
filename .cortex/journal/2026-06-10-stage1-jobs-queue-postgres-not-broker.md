# Stage 1 hosted job queue is Postgres SKIP LOCKED, not a broker

**Date:** 2026-06-10
**Type:** decision
**Trigger:** T1.5
**Cites:** plans/hosted-decision-reviewer, journal/2026-06-09-pr-merged-pr477

> The hosted background-job substrate (cortex#471) is a database-backed queue
> (`cortex_hosted.jobs`, schema v7, `FOR UPDATE SKIP LOCKED`) reusing the
> ledger idempotency idiom — no external broker, and the new `cortex-api` /
> `cortex-worker` entrypoints ship as console scripts in `pyproject.toml`.

## Context

cortex#470 (API shell) and #471 (worker) needed a queue so webhooks never do
inline processing. The executable Postgres path from #472 (driver policy,
migration runner) was already live on Railway compass, and #471's issue body
required a documented queue choice plus reuse of the shipped
`derive_idempotency_key` / `ON CONFLICT DO NOTHING` duplicate-delivery
pattern. The `pyproject.toml` diff that fires this trigger adds the two
service entrypoints (`cortex-api = cortex.hosted.api.app:main`,
`cortex-worker = cortex.hosted.worker:main`); no dependency changed — the
API shell is stdlib `http.server` precisely to avoid a framework dependency
at this surface area.

## What we decided

- **Database-backed queue over a broker.** One `cortex_hosted.jobs` table
  claimed via `FOR UPDATE SKIP LOCKED`. A broker (Redis/SQS) would be a
  second stateful service to operate before the first customer exists; the
  Postgres path already carries connection policy, migrations, and backups.
  This queue is the canonical home for ALL hosted job types — Stage 2 PR
  evaluation (#388) and Stage 3 Slack console jobs register handlers on the
  same substrate instead of standing up parallel queues.
- **Delivery GUID as the idempotency key** (`github-delivery:<guid>`),
  `ON CONFLICT (idempotency_key) DO NOTHING` — the ledger idiom, not a
  second invention.
- **Schema v7** adds the jobs table in the same canonical idempotent DDL
  (one migration path), plus the `source.event_received` ledger event type
  so worker stubs record raw webhook arrival append-only.
- **Stdlib HTTP transport** for the API shell; promoting to a framework is
  a deliberate later decision if the surface grows.

## Consequences / action items

- [x] Failure visibility: retries with capped backoff, dead-letter status,
  stale-claim recovery, structured JSON log lines per transition
  (`src/cortex/hosted/worker.py`; tests in `tests/test_hosted_worker.py`)
- [ ] Stage 2 (#386) replaces the static `CORTEX_TENANT_ID`/`CORTEX_SOURCE_ID`
  arrival mapping with installation-based tenant resolution
- [ ] Deploy per `docs/hosted-deploy.md` (v7 migration, then flip the App
  webhook active per `docs/setup/github-app.md`)
