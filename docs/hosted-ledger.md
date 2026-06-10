# Hosted ledger substrate

This document owns the first hosted Cortex storage boundary. It does not change
`SPEC.md`, the `.cortex/` file protocol, or the local CLI contract.

## Storage decision

Hosted Cortex uses Postgres as the canonical product database from the first
hosted-shaped build.

SQLite remains allowed only as an explicit rebuildable cache or replay/export
format. The existing `.cortex/.index/chunks.sqlite` retrieval index is still a
local CLI cache; it is not a product decision graph and must not gain separate
core graph semantics.

Deferred until measured pressure exists:

- graph databases;
- external vector databases;
- OpenSearch or Elasticsearch;
- Kafka-shaped event platforms;
- tenant partitioning beyond tenant-scoped relational keys.

## Event invariant

`ledger_events` is the source of truth. Current decision graph tables, scope
indexes, search projections, and retrieval traces are rebuildable projections.

The hosted schema enforces append-only events with update/delete prevention
triggers. Corrections, feedback, supersedes, stale marks, and projection rebuilds
append new events instead of mutating old ones.

Every user-visible answer or PR finding must trace back to:

`tenant + source spans + graph snapshot + retrieval config + model/prompt version`

Missing provenance fails closed. The system should say it does not know rather
than produce an uncited answer.

## Visibility boundary

Hosted retrieval is deny-by-default. `ask_ledger`, `decisions_for_diff`, and raw
embedding projection source queries must receive a non-empty list of source IDs
already authorized for the request. Missing authorization raises a visible
validation error; it must never fall back to a broader tenant or repo search.

Every retrieval query starts from `tenant_id`, `visible_source_ids`, repo scope,
optional repo-install scope, and source/document visibility flags before ranking
or assembling LLM context. The shared visibility CTE excludes sources and
documents marked `deleted`, `revoked`, `slack_channel_excluded`, or
`repo_installation_revoked`. GitHub-backed sources also have to match the
provided repo-installation ID when one is supplied.

The service-layer permission adapter owns translating provider permissions into
`visible_source_ids`: Slack channel membership/exclusions, GitHub repo install
scope, and source revocation all have to be resolved before retrieval. Prompt
packs, retrieval traces, and user-facing logs may contain only cited text that
survived this boundary.

Postgres row-level security is not required for the local Stage 0 proof while a
single trusted service account owns all access. It is required before any
external design-partner or production multi-tenant traffic, because app-layer
authorization is a necessary boundary but not the last line of defense.

## Provenance snapshots

Source documents are immutable snapshots keyed by content hash. Re-ingesting the
same external document with changed content creates a new document snapshot
instead of overwriting the old one. Source spans are derived from a document
content hash plus offsets and excerpt hash, so existing citations survive
re-derivation and source drift can be detected explicitly.

## Executable path: driver, migrations, integration tests

The Postgres driver is an optional extra so the core CLI install stays
driver-free: `pip install 'cortex[hosted]'` (or `uv sync --extra hosted`).
`cortex.hosted.db.connect` owns the connection policy — explicit connect
timeout, `application_name=cortex-hosted`, a session `statement_timeout`,
and Railway-style `?sslmode=require` URLs passed through verbatim, never
stripped. Connection failures raise `HostedDbError` naming what failed
(no driver, bad URL, unreachable, auth).

`cortex.hosted.migrations.apply_schema` is the single migration path for
local Postgres and Railway alike (deploy/predeploy release steps call the
same runner — there is no second path). It verifies pgcrypto/pg_trgm/vector
availability *before* executing the shipped `create_schema_sql()` DDL,
records into `cortex_hosted.schema_migrations`, and reports
applied/already-current with the version number. A missing extension fails
the migration visibly; it never degrades silently.

Run the gated integration tests against a real Postgres:

```bash
DATABASE_URL='postgresql://user:pass@host:5432/db?sslmode=require' \
    uv run --extra hosted pytest tests/test_hosted_db_integration.py -q
```

Without `DATABASE_URL` the suite skips with a message naming this setup.

Railway note (compass project): the target Postgres image must ship the
`pgcrypto`, `pg_trgm`, and `vector` extensions — use a pgvector-enabled
Postgres image/template; `verify_extensions` raises before apply when any
is missing. Full closure of #467 (retrieval traces) and #484 (glob scope
matching) rides on this executable path: their SQL exists as strings and is
proven only when these integration tests run against a live database.

## Stage 0 issue map

- `#460` fixes the canonical storage boundary.
- `#461` fixes the append-only ledger event schema.
- `#462` will deepen source document/span ingestion and stale-source checks.
- `#463` will build the structural scope index.
- `#464` and `#465` will add `ask_ledger` and `decisions_for_diff` retrieval.
- `#468` adds fail-closed tenant/source visibility boundaries before retrieval
  ranking and raw projection text.
