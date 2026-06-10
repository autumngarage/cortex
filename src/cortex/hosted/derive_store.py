"""Local SQLite event store for `cortex derive` — a rebuildable replay export.

Storage boundary (issue #350): Postgres remains the only canonical hosted
store. This module persists `LedgerEvent` envelopes to SQLite strictly under
the approved ``local-replay-export`` role from `cortex.hosted.storage`, and
every open validates that role through ``validate_rebuildable_cache_store``
so the boundary stays a raised error, not a convention.

Derive-don't-persist provenance:

- **Source of truth:** the repo sources `cortex derive` walks, plus the
  `LedgerEvent` envelope in `cortex.hosted.ledger_events`.
- **Invalidation trigger:** any change to those sources or to the extractor
  set; the store carries no state that cannot be recomputed from them.
- **Rebuild path:** delete the database file and re-run `cortex derive`;
  stable idempotency keys reproduce the identical event set.
- **Reconciliation check:** ``export_events()`` round-trips rows back into
  the exact ``LedgerEvent.as_insert_parameters()`` parameter dicts, so a
  later stage loads them into the hosted Postgres ledger without
  translation (proven by tests).

The table mirrors ``LedgerEvent.as_insert_parameters()`` column-for-column;
there is no derive-private event schema.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from cortex.hosted.ledger_events import EVENT_SCHEMA_VERSION, LedgerEvent
from cortex.hosted.storage import validate_rebuildable_cache_store

DERIVE_STORE_SQLITE_ROLE = "local-replay-export"
# Lives under `.cortex/.index/`, which `.cortex/.gitignore` already excludes —
# the store is derived state and must never be committed.
DERIVE_STORE_RELATIVE_PATH = Path(".cortex") / ".index" / "derive-events.sqlite"

# Mirrors LedgerEvent.as_insert_parameters() key order exactly. A test asserts
# this stays in lockstep with the envelope so the export needs no translation.
DERIVE_STORE_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "source_id",
    "event_type",
    "event_version",
    "actor_type",
    "actor_id",
    "occurred_at",
    "idempotency_key",
    "source_event_external_id",
    "source_span_hashes",
    "graph_snapshot_hash",
    "model_id",
    "prompt_version",
    "payload",
    "metadata",
    "previous_event_hash",
    "event_hash",
)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ledger_events (
    tenant_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_version INTEGER NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    source_event_external_id TEXT,
    source_span_hashes TEXT NOT NULL,
    graph_snapshot_hash TEXT,
    model_id TEXT,
    prompt_version TEXT,
    payload TEXT NOT NULL,
    metadata TEXT NOT NULL,
    previous_event_hash TEXT,
    event_hash TEXT NOT NULL,
    UNIQUE (tenant_id, idempotency_key)
)
""".strip()

_INSERT_SQL = (
    f"INSERT OR IGNORE INTO ledger_events ({', '.join(DERIVE_STORE_COLUMNS)}) "
    f"VALUES ({', '.join(':' + column for column in DERIVE_STORE_COLUMNS)})"
)

_SELECT_SQL = (
    f"SELECT {', '.join(DERIVE_STORE_COLUMNS)} FROM ledger_events "
    "ORDER BY occurred_at, event_hash"
)


class DeriveStoreError(ValueError):
    """Raised when the local derive event store cannot uphold replay invariants."""


@dataclass(frozen=True)
class AppendOutcome:
    """Counts from one append batch: new rows vs idempotent duplicates."""

    inserted: int
    ignored: int


def derive_store_path(project_root: Path) -> Path:
    """Return the canonical derive store location inside a project."""

    return project_root / DERIVE_STORE_RELATIVE_PATH


class DeriveEventStore:
    """Append-only local mirror of hosted ledger events, keyed for replay.

    Rows are unique on ``(tenant_id, idempotency_key)`` with INSERT OR IGNORE
    semantics — the same conflict contract as the hosted Postgres insert in
    ``ledger_event_insert_sql`` — so re-running derive over unchanged inputs
    is a no-op and deleting the file is always recoverable.
    """

    def __init__(self, db_path: Path) -> None:
        validate_rebuildable_cache_store("sqlite", role=DERIVE_STORE_SQLITE_ROLE)
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        try:
            self._ensure_schema()
        except sqlite3.DatabaseError as exc:
            self._conn.close()
            raise DeriveStoreError(
                f"cannot open derive event store at {db_path}: {exc}"
            ) from exc

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _ensure_schema(self) -> None:
        row = self._conn.execute("PRAGMA user_version").fetchone()
        stored_version = int(row[0])
        if stored_version == 0:
            self._conn.execute(_CREATE_TABLE_SQL)
            # PRAGMA cannot take bound parameters; EVENT_SCHEMA_VERSION is a
            # trusted module constant, not user input.
            self._conn.execute(f"PRAGMA user_version = {int(EVENT_SCHEMA_VERSION)}")
            self._conn.commit()
            return
        if stored_version != EVENT_SCHEMA_VERSION:
            raise DeriveStoreError(
                f"derive event store at {self._db_path} has schema version "
                f"{stored_version}, expected {EVENT_SCHEMA_VERSION}; the store is "
                "a rebuildable export — delete the file and re-run `cortex derive`"
            )
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def append_events(self, events: Iterable[LedgerEvent]) -> AppendOutcome:
        """Append events idempotently; raise on same-key/different-hash collisions.

        A duplicate delivery (same tenant, same idempotency key, same event
        hash) is ignored and counted. A collision (same key, *different* event
        hash) means two distinct events claimed one replay slot; that is never
        ignorable, so the whole batch rolls back and the error names both
        hashes.
        """

        inserted = 0
        ignored = 0
        try:
            for event in events:
                row = _event_row(event)
                cursor = self._conn.execute(_INSERT_SQL, row)
                if cursor.rowcount == 1:
                    inserted += 1
                    continue
                existing = self._conn.execute(
                    "SELECT event_hash FROM ledger_events "
                    "WHERE tenant_id = ? AND idempotency_key = ?",
                    (event.tenant_id, event.idempotency_key),
                ).fetchone()
                if existing is None:
                    raise DeriveStoreError(
                        f"insert of event {event.event_hash} was ignored but no "
                        f"stored row matches idempotency key {event.idempotency_key!r}"
                    )
                if existing[0] != row["event_hash"]:
                    raise DeriveStoreError(
                        f"idempotency key collision for {event.idempotency_key!r}: "
                        f"stored event hash {existing[0]} != new event hash "
                        f"{row['event_hash']}; refusing to silently drop a distinct event"
                    )
                ignored += 1
        except (DeriveStoreError, sqlite3.DatabaseError):
            self._conn.rollback()
            raise
        self._conn.commit()
        return AppendOutcome(inserted=inserted, ignored=ignored)

    def export_events(self) -> tuple[dict[str, Any], ...]:
        """Return stored rows as `LedgerEvent.as_insert_parameters()` dicts.

        Ordered by ``(occurred_at, event_hash)`` so the export is stable
        regardless of insertion order. The dicts round-trip byte-for-byte into
        the hosted Postgres insert parameters.
        """

        rows = self._conn.execute(_SELECT_SQL).fetchall()
        return tuple(_row_to_insert_parameters(row) for row in rows)

    def event_hashes(self) -> frozenset[str]:
        rows = self._conn.execute("SELECT event_hash FROM ledger_events").fetchall()
        return frozenset(row[0] for row in rows)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DeriveEventStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _event_row(event: LedgerEvent) -> dict[str, Any]:
    """Flatten insert parameters into SQLite-storable scalars."""

    parameters = event.as_insert_parameters()
    row = dict(parameters)
    occurred_at = parameters["occurred_at"]
    row["occurred_at"] = occurred_at.isoformat()
    row["source_span_hashes"] = json.dumps(
        parameters["source_span_hashes"], separators=(",", ":")
    )
    return row


def _row_to_insert_parameters(row: tuple[Any, ...]) -> dict[str, Any]:
    """Reconstruct the exact `as_insert_parameters()` dict from a stored row."""

    parameters: dict[str, Any] = dict(zip(DERIVE_STORE_COLUMNS, row, strict=True))
    parameters["occurred_at"] = datetime.fromisoformat(parameters["occurred_at"])
    parameters["source_span_hashes"] = json.loads(parameters["source_span_hashes"])
    return parameters
