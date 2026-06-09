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

## Provenance snapshots

Source documents are immutable snapshots keyed by content hash. Re-ingesting the
same external document with changed content creates a new document snapshot
instead of overwriting the old one. Source spans are derived from a document
content hash plus offsets and excerpt hash, so existing citations survive
re-derivation and source drift can be detected explicitly.

## Stage 0 issue map

- `#460` fixes the canonical storage boundary.
- `#461` fixes the append-only ledger event schema.
- `#462` will deepen source document/span ingestion and stale-source checks.
- `#463` will build the structural scope index.
- `#464` and `#465` will add `ask_ledger` and `decisions_for_diff` retrieval.
