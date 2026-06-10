"""Tests for the local SQLite derive event store (issue #350).

Invariants under test:

- The store is SQLite under the approved ``local-replay-export`` role only.
- The table mirrors ``LedgerEvent.as_insert_parameters()`` column-for-column.
- ``(tenant_id, idempotency_key)`` is UNIQUE with INSERT OR IGNORE semantics;
  a same-key/different-hash collision raises instead of silently dropping.
- ``export_events()`` round-trips rows back into the exact parameter dicts.
- Deleting the file and re-appending the same events reproduces the same
  event-hash set (rebuildable export).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cortex.hosted.derive_store import (
    DERIVE_STORE_COLUMNS,
    DERIVE_STORE_RELATIVE_PATH,
    DERIVE_STORE_SQLITE_ROLE,
    DeriveEventStore,
    DeriveStoreError,
    derive_store_path,
)
from cortex.hosted.ledger_events import (
    EVENT_SCHEMA_VERSION,
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)
from cortex.hosted.storage import REBUILDABLE_SQLITE_CACHE_ROLES

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"


def _candidate_event(
    *,
    external_id: str = "CLAUDE.md",
    summary: str = "Use Postgres",
    occurred_at: datetime | None = None,
) -> LedgerEvent:
    payload = {"external_id": external_id, "summary": summary}
    return LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=ActorRef(actor_type="derive", actor_id="cortex-derive"),
        occurred_at=occurred_at or datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key=derive_idempotency_key(
            source_id=SOURCE_ID,
            event_type=LedgerEventType.CANDIDATE_PROPOSED,
            source_event_external_id=external_id,
            payload=payload,
        ),
        payload=payload,
    )


def _store(tmp_path: Path) -> DeriveEventStore:
    return DeriveEventStore(tmp_path / "derive-events.sqlite")


def test_store_role_is_the_approved_local_replay_export_role() -> None:
    assert DERIVE_STORE_SQLITE_ROLE == "local-replay-export"
    assert DERIVE_STORE_SQLITE_ROLE in REBUILDABLE_SQLITE_CACHE_ROLES


def test_store_path_is_the_gitignored_index_location(tmp_path: Path) -> None:
    expected_relative = Path(".cortex") / ".index" / "derive-events.sqlite"
    assert expected_relative == DERIVE_STORE_RELATIVE_PATH
    assert derive_store_path(tmp_path) == tmp_path / DERIVE_STORE_RELATIVE_PATH


def test_columns_mirror_ledger_event_insert_parameters_exactly() -> None:
    event = _candidate_event()
    assert tuple(event.as_insert_parameters().keys()) == DERIVE_STORE_COLUMNS


def test_table_enforces_unique_tenant_and_idempotency_key(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        store.append_events([_candidate_event()])
        table_sql = sqlite3.connect(store.db_path).execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ledger_events'"
        ).fetchone()[0]
    assert "UNIQUE (tenant_id, idempotency_key)" in table_sql


def test_append_is_idempotent_on_duplicate_delivery(tmp_path: Path) -> None:
    event = _candidate_event()
    with _store(tmp_path) as store:
        first = store.append_events([event])
        second = store.append_events([event])
        assert (first.inserted, first.ignored) == (1, 0)
        assert (second.inserted, second.ignored) == (0, 1)
        assert store.event_hashes() == frozenset({event.event_hash})


def test_same_key_different_hash_collision_raises_instead_of_dropping(
    tmp_path: Path,
) -> None:
    original = _candidate_event()
    # Same idempotency key, different immutable material => different hash.
    imposter = LedgerEvent(
        tenant_id=original.tenant_id,
        source_id=original.source_id,
        event_type=original.event_type,
        actor=ActorRef(actor_type="derive", actor_id="someone-else"),
        occurred_at=original.occurred_at,
        idempotency_key=original.idempotency_key,
        payload={"summary": "A different decision entirely"},
    )
    assert imposter.event_hash != original.event_hash
    with _store(tmp_path) as store:
        store.append_events([original])
        with pytest.raises(DeriveStoreError, match="idempotency key collision"):
            store.append_events([imposter])
        # The failed batch rolled back; the original row is intact.
        assert store.event_hashes() == frozenset({original.event_hash})


def test_export_round_trips_insert_parameters_exactly(tmp_path: Path) -> None:
    events = [
        _candidate_event(external_id="CLAUDE.md", summary="Use Postgres"),
        _candidate_event(
            external_id="docs/adr/0001-foo.md",
            summary="Adopt ADRs",
            occurred_at=datetime(2026, 6, 8, 9, 30, tzinfo=UTC),
        ),
    ]
    with _store(tmp_path) as store:
        store.append_events(events)
        exported = store.export_events()
    expected = sorted(
        (event.as_insert_parameters() for event in events),
        key=lambda parameters: (parameters["occurred_at"].isoformat(), parameters["event_hash"]),
    )
    assert list(exported) == expected


def test_delete_and_rebuild_reproduces_identical_event_hashes(tmp_path: Path) -> None:
    events = [
        _candidate_event(external_id="CLAUDE.md"),
        _candidate_event(external_id="AGENTS.md", summary="Agents follow protocol"),
    ]
    db_path = tmp_path / "derive-events.sqlite"
    with DeriveEventStore(db_path) as store:
        store.append_events(events)
        first_hashes = store.event_hashes()
        first_export = store.export_events()
    db_path.unlink()
    with DeriveEventStore(db_path) as store:
        store.append_events(events)
        assert store.event_hashes() == first_hashes
        assert store.export_events() == first_export


def test_schema_version_mismatch_fails_closed_with_rebuild_path(tmp_path: Path) -> None:
    db_path = tmp_path / "derive-events.sqlite"
    DeriveEventStore(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(DeriveStoreError, match="schema version 99"):
        DeriveEventStore(db_path)


def test_non_sqlite_file_surfaces_a_visible_error(tmp_path: Path) -> None:
    db_path = tmp_path / "derive-events.sqlite"
    db_path.write_bytes(b"this is not a sqlite database, padded to 16+ bytes")
    with pytest.raises(DeriveStoreError, match=str(db_path)):
        DeriveEventStore(db_path)


def test_fresh_store_records_event_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "derive-events.sqlite"
    DeriveEventStore(db_path).close()
    stored = sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0]
    assert stored == EVENT_SCHEMA_VERSION
