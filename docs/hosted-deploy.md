# Hosted deploy — API shell + worker on Railway

**Date:** 2026-06-10
**Owns:** the deployment shape of the Stage 1 hosted services (cortex#470
API shell, cortex#471 worker), their start commands, environment variables,
and the schema-migration step.
**Does not own:** the GitHub App registration fields
([docs/setup/github-app.md](./setup/github-app.md)), the service topology
rationale (#469), environment/secret management policy (#475), backup and
restore drills (#473), or the substrate architecture
([docs/hosted-architecture.md](./hosted-architecture.md)).

## Services

Two Railway services deploy from this repo on the `compass` project,
sharing one Postgres (the `DATABASE_URL` they both receive):

| Service | Entrypoint | Start command | Role |
|---|---|---|---|
| `cortex` (API) | `cortex-api` (`cortex.hosted.api.app:main`) | `uv run --frozen --extra hosted cortex-api` | Webhook receiver + health/version endpoints. Public domain `https://cortex-production-61d7.up.railway.app`. |
| `cortex-worker` | `cortex-worker` (`cortex.hosted.worker:main`) | `uv run --frozen --extra hosted cortex-worker` | Polls the `cortex_hosted.jobs` queue; no public networking. |

[`railway.toml`](../railway.toml) is the canonical config for the API
service (build, start command, `/healthz` healthcheck, restart policy).
Railway config-as-code applies per service, so the worker service points at
the same repo and overrides only its start command in the service settings.

The API does **no inline processing**: `POST /webhooks/github` verifies the
HMAC signature, persists one idempotent job row (delivery GUID as the
idempotency key — the ledger `ON CONFLICT DO NOTHING` idiom), and answers
202. The worker claims jobs with `FOR UPDATE SKIP LOCKED`, dispatches by
job type through a handler registry, retries with capped exponential
backoff, and dead-letters exhausted jobs with the error text persisted on
the row. This queue is the canonical substrate for every future hosted job
type (Stage 2 PR evaluation #388, Stage 3 Slack console jobs) — new job
types register a handler; no schema change.

## Endpoints

- `GET /healthz` — liveness + DB round trip. Reports the recorded hosted
  schema version. Degrades to a JSON body naming the failure
  (`database_url_missing`, `connect_failed`, `schema_version_mismatch`,
  `schema_status_failed`) — never a crash, never DSN details in the
  response.
- `GET /version` — package version, supported hosted schema version,
  commit SHA (from `RAILWAY_GIT_COMMIT_SHA`).
- `POST /webhooks/github` — 202 queued / 202 duplicate; 401 on signature
  mismatch or missing signature; 400 on malformed headers/body; 503 when
  the secret or database is not configured (visible refusal, never a
  silent drop).

## Environment variables

| Variable | Service | Required | Meaning |
|---|---|---|---|
| `DATABASE_URL` | both | API: optional (degrades) · worker: required | Postgres DSN (Railway-style, `?sslmode=require` honored verbatim). |
| `GITHUB_WEBHOOK_SECRET` | API | optional (webhook 503s without it) | HMAC-SHA256 secret from the App registration. |
| `PORT` | API | provided by Railway | Listen port (default 8080). |
| `CORTEX_API_HOST` | API | no (default `0.0.0.0`) | Bind address. |
| `CORTEX_TENANT_ID` / `CORTEX_SOURCE_ID` | worker | optional, paired | Static tenant/source mapping for recording raw webhook arrivals as `source.event_received` ledger events. Unset: jobs are still handled; the result names the unrecorded arrival. Dogfood-only: real installation-based resolution is #572 (#386 shipped the installation-auth half; this static mapping is the residual). |
| `CORTEX_WORKER_POLL_SECONDS` | worker | no (default 2.0) | Idle poll interval. |
| `CORTEX_STALE_CLAIM_SECONDS` | worker | no (default 1800) | Age after which a `running` claim is presumed crashed and recovered. |
| `CORTEX_APPLY_SCHEMA_ON_START` | worker | no (default false) | When `1`/`true`, the worker runs the migration runner before polling. |
| `RAILWAY_GIT_COMMIT_SHA` | API | provided by Railway | Surfaced by `/version`. |

Config parsing is fail-closed (`ServiceConfig.from_env`): malformed values
(non-integer `PORT`, non-UUID tenant id, blank-but-set secret, unpaired
tenant/source) refuse startup with the variable named. No secret is ever
committed; values live as Railway service variables (policy: #475).

## Schema migration (v7 — historical; live schema is v9 as of 2026-06-11)

The same append-only migration path later applied v8 (`review_cost_records`
cost ledger, PR #559) and v9 (`review_feedback_events` ground-truth corpus,
PR #566); compass runs v9. The v7 step is kept below as the worked example.

Schema v7 adds the `cortex_hosted.jobs` table and refreshes the
`ledger_events.event_type` CHECK for the new `source.event_received` event
type. The migration is the same append-only, idempotent DDL path as v1–v6
(`cortex.hosted.migrations.apply_schema`). Apply it either by:

- setting `CORTEX_APPLY_SCHEMA_ON_START=1` on the worker for one deploy
  (then unsetting it), or
- running once from a trusted shell:

```bash
DATABASE_URL='postgresql://...' uv run --extra hosted python -c \
  "from cortex.hosted.db import connect; from cortex.hosted.migrations import apply_schema; \
import os; print(apply_schema(connect(os.environ['DATABASE_URL'])).describe())"
```

Rollback shape: the DDL only adds — existing tables, rows, and the
append-only triggers are untouched, so a bad deploy is recovered by
redeploying the previous build; the v7 objects are inert under v6 code.

## Deploy verification

1. `curl -s https://<domain>/healthz` → `"status": "ok"` and
   `"schema_version"` equal to the deployed build's `HOSTED_SCHEMA_VERSION`
   (`src/cortex/hosted/schema.py` — 9 as of 2026-06-11).
2. `curl -s https://<domain>/version` → expected package version + commit.
3. Send a test delivery (GitHub App → Advanced → Redeliver, once the
   webhook is flipped active per
   [docs/setup/github-app.md](./setup/github-app.md)) → API logs show
   `webhook.queued`, worker logs show `job.claimed` then `job.succeeded`.
4. Redeliver the same delivery → API logs show `webhook.duplicate`; no
   second job row.

Worker log lines are single-line JSON (`worker.started`, `job.claimed`,
`job.succeeded`, `job.retry_scheduled`, `job.dead_lettered`,
`job.stale_claim_recovered`, `worker.stopped`) — greppable by `event` key
without exposing payload secrets.
