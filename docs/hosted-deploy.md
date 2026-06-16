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
| `CORTEX_TENANT_ID` / `CORTEX_SOURCE_ID` | worker | dev-only fallback, paired | Static tenant/source fallback for local recovery only. Ignored unless `CORTEX_STATIC_TENANT_FALLBACK=1`; every use logs `worker.static_tenant_fallback_used`. Production review, feedback, staged, and cost telemetry resolves tenant/source from stored GitHub installation bindings. |
| `CORTEX_STATIC_TENANT_FALLBACK` | worker | no (default false) | Explicitly enables the static tenant/source fallback above. Leave unset in hosted dogfood; missing installation bindings should fail or skip visibly instead of sharing a tenant. |
| `CORTEX_WORKER_POLL_SECONDS` | worker | no (default 2.0) | Idle poll interval. |
| `CORTEX_REVIEW_DRY_RUN` | worker | no (default dry-run/on) | Set to `0`/`false`/`no`/`off` to allow enabled repos to receive PR comments. Per-repo rollout still gates before any fetch/model spend. |
| `CORTEX_REVIEW_TOKEN_BUDGET` | worker | no (default 32000) | Per-review decision-pack token budget. Malformed or non-positive values refuse startup. |
| `CORTEX_REACTION_POLL_SECONDS` | worker | no (default 900) | Seconds between scheduled reaction sweeps over recently-reviewed PRs (cortex#393 — reactions have no webhook). `0` disables the sweep. Requires App credentials; each target resolves tenant identity from installation bindings and missing bindings are counted/logged. |
| `CORTEX_STALE_CLAIM_SECONDS` | worker | no (default 1800) | Age after which a `running` claim is presumed crashed and recovered. |
| `CORTEX_JOB_PAYLOAD_PRUNE_GRACE_SECONDS` | worker | no (default 604800) | Grace window before terminal job webhook payloads are replaced by a content-free skeleton plus `body_sha256`. Keep this above the reaction sweep window (48h default) so feedback polling can still derive PR targets; set to `0` only in tests. |
| `CORTEX_APPLY_SCHEMA_ON_START` | worker | no (default false) | When `1`/`true`, the worker runs the migration runner before polling. |
| `RAILWAY_GIT_COMMIT_SHA` | API | provided by Railway | Surfaced by `/version`. |

Config parsing is fail-closed (`ServiceConfig.from_env`): malformed values
(non-integer `PORT`, non-UUID tenant id, blank-but-set secret, unpaired
tenant/source) refuse startup with the variable named. No secret is ever
committed; values live as Railway service variables (policy: #475).

## Schema migration (v7 — historical; live schema is v13 as of 2026-06-16)

The same append-only migration path later applied v8 (`review_cost_records`
cost ledger, PR #559) and v9 (`review_feedback_events` ground-truth corpus,
PR #566), v10 (`review_staged_prs` staged-traffic registry), v11
(`review_rollout_events` per-repo comment rollout), and v12 (reply sentiment
classification can update pending feedback rows); compass runs v12. The v7
step is kept below as the worked example.

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
   (`src/cortex/hosted/schema.py` — 11 as of 2026-06-15).
2. `curl -s https://<domain>/version` → expected package version + commit.
3. Send a test delivery (GitHub App → Advanced → Redeliver, once the
   webhook is flipped active per
   [docs/setup/github-app.md](./setup/github-app.md)) → API logs show
   `webhook.queued`, worker logs show `job.claimed` then `job.succeeded`.
4. Redeliver the same delivery → API logs show `webhook.duplicate`; no
   second job row.

Worker log lines are single-line JSON (`worker.started`, `job.claimed`,
`job.succeeded`, `job.retry_scheduled`, `job.dead_lettered`,
`job.stale_claim_recovered`, `job.payloads_pruned`, `worker.stopped`) —
greppable by `event` key without exposing payload secrets. Hosted logging
fails closed on content-bearing field names such as `payload`, `body`,
`comment_body`, `diff`, `decision_text`, and secret/token/key fields. A
successful stateless review stores counts, ids, rollout/reason codes, and
operator-internal cost telemetry in the job result; it does not persist the
rendered Compass PR comment body.

Terminal job rows keep the raw webhook payload only for the debug grace
window. The worker housekeeping pass then replaces `jobs.payload` with a
content-free skeleton (`event`, delivery GUID, repository full name, PR
number, base/head SHAs, installation id, and `body_sha256`). Queued/running
jobs are never minimized, so retries still have the raw webhook material they
need.

## Per-repo review rollout (cortex#397)

The GitHub App may be installed on all organization repos, but hosted PR
comments are **off by default per repo**. A repo receives comments only after an
operator appends an enable event to `cortex_hosted.review_rollout_events`.
Disabled repos are still acknowledged: the worker completes the
`github.pull_request` job with `reason=review_rollout_disabled`, posts no
comment, and does not construct a GitHub client, fetch decision files, fetch a
diff, or resolve a model.

The worker queries the latest rollout event on every pull-request delivery, so
config changes take effect without redeploy:

```bash
DATABASE_URL='postgresql://...' uv run --extra hosted cortex review-rollout \
  set autumngarage/cortex enabled \
  --actor henry \
  --reason 'dogfood advisory comments'

DATABASE_URL='postgresql://...' uv run --extra hosted cortex review-rollout \
  status autumngarage/cortex
```

To pause a repo:

```bash
DATABASE_URL='postgresql://...' uv run --extra hosted cortex review-rollout \
  set autumngarage/cortex disabled \
  --actor henry \
  --reason 'pause rollout while investigating reviewer behavior'
```

No SQL `UPDATE`/`DELETE` is valid for rollout config. Corrections append later
events; the append-only trigger rejects mutation. `CORTEX_REVIEW_DRY_RUN=false`
only controls posting after a repo is enabled, so a fresh install with no
rollout event remains no-spend/no-comment even when posting is globally allowed.

## Staged demo traffic (cortex#575)

Demo fixtures and walkthrough PRs are a different data regime from organic
work: their findings and feedback must never feed precision metrics, the
promote/auto-demote gates, or the organic-catch validation verdict (#576).
The boundary is the `cortex_hosted.review_staged_prs` registry (schema v10):

- **Convention.** A PR is staged when its **title contains `[cortex-demo]`**
  (case-insensitive) or it carries the **label `cortex-demo-fixture`**. The
  worker detects this on every `github.pull_request` job and appends one
  idempotent registry row (`review.staged_pr` log line). The review itself
  still runs and posts — demos keep working; only the metrics exclude them.
- **Backfill.** Retroactively staging a PR (e.g. the original demo fixture,
  cortex PR #561) is one operator INSERT — never an UPDATE to the
  append-only feedback corpus:

```sql
INSERT INTO cortex_hosted.review_staged_prs
    (tenant_id, repo_full_name, pr_number, reason)
SELECT DISTINCT tenant_id, repo_full_name, pr_number, 'operator-backfill'
FROM cortex_hosted.review_feedback_events
WHERE repo_full_name = 'autumngarage/cortex' AND pr_number = 561
ON CONFLICT ON CONSTRAINT review_staged_prs_pr_unique DO NOTHING;
```

- **Consumption.** Metric queries exclude members by JOIN on
  `(tenant_id, repo_full_name, pr_number)` and report the excluded count —
  exclusion is visible, never silent (`cortex precision-report` does this by
  default; `--include-staged` opts back in for demo walkthroughs).
