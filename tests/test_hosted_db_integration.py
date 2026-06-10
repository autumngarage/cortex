"""Integration tests for the first executable hosted SQL path (cortex#472).

These run only when ``DATABASE_URL`` points at a real Postgres provisioned
with the pgcrypto, pg_trgm, and vector extensions (the Railway compass
Postgres, or a local pgvector-enabled image) and the ``hosted`` extra is
installed::

    DATABASE_URL='postgresql://user:pass@host:5432/db?sslmode=require' \\
        uv run --extra hosted pytest tests/test_hosted_db_integration.py -q

The suite applies the shipped DDL twice (idempotency), verifies the
extensions, executes the first real ledger write through
``ledger_event_insert_sql`` + ``as_insert_parameters``, and proves the
append-only trigger fires. Rows created here are tagged with per-run UUIDs;
``ledger_events`` is append-only by design, so they are left in place.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from cortex.hosted.db import (
    DEFAULT_STATEMENT_TIMEOUT_MS,
    HOSTED_APPLICATION_NAME,
    HostedConnection,
    connect,
)
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    ledger_event_insert_sql,
)
from cortex.hosted.migrations import (
    REQUIRED_EXTENSIONS,
    apply_schema,
    schema_status,
    verify_extensions,
)
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION

DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "set DATABASE_URL to a Postgres with pgcrypto/pg_trgm/vector "
        "(e.g. the Railway compass Postgres) to run the hosted integration tests"
    ),
)

# Base tables in the shipped v6 DDL; later schema versions may add more.
MIN_EXPECTED_TABLE_COUNT = 14


@pytest.fixture()
def conn() -> Iterator[HostedConnection]:
    connection = connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_connection_policy_is_visible_in_the_live_session(conn: HostedConnection) -> None:
    app_name = conn.execute("SELECT current_setting('application_name')").fetchone()[0]
    assert app_name == HOSTED_APPLICATION_NAME
    timeout = conn.execute("SELECT current_setting('statement_timeout')").fetchone()[0]
    # Postgres renders 30000ms as "30s"; accept either spelling of the policy value.
    assert timeout in {f"{DEFAULT_STATEMENT_TIMEOUT_MS}ms", "30s"}


def test_required_extensions_are_available_on_the_image(conn: HostedConnection) -> None:
    assert verify_extensions(conn) == REQUIRED_EXTENSIONS


def test_apply_schema_twice_second_run_is_already_current(conn: HostedConnection) -> None:
    first = apply_schema(conn)
    assert first.version == HOSTED_SCHEMA_VERSION

    second = apply_schema(conn)
    assert second.version == HOSTED_SCHEMA_VERSION
    assert second.already_current is True
    assert second.describe() == (
        f"hosted schema already current at version {HOSTED_SCHEMA_VERSION}"
    )

    status = schema_status(conn)
    assert status.version == HOSTED_SCHEMA_VERSION
    assert status.table_count >= MIN_EXPECTED_TABLE_COUNT


def test_first_executed_ledger_write_round_trips_and_is_append_only(
    conn: HostedConnection,
) -> None:
    apply_schema(conn)
    run_id = uuid.uuid4().hex[:12]

    tenant_row = conn.execute(
        "INSERT INTO cortex_hosted.tenants (slug, display_name) "
        "VALUES (%(slug)s, %(display_name)s) RETURNING tenant_id",
        {"slug": f"it-{run_id}", "display_name": f"integration run {run_id}"},
    ).fetchone()
    tenant_id = str(tenant_row[0])

    source_row = conn.execute(
        "INSERT INTO cortex_hosted.sources (tenant_id, source_type, external_id) "
        "VALUES (%(tenant_id)s, %(source_type)s, %(external_id)s) RETURNING source_id",
        {"tenant_id": tenant_id, "source_type": "github_pr", "external_id": f"it-{run_id}"},
    ).fetchone()
    source_id = str(source_row[0])

    event = LedgerEvent(
        tenant_id=tenant_id,
        source_id=source_id,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=ActorRef(actor_type="integration-test", actor_id="cortex#472"),
        occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key=f"it-{run_id}",
        payload={"candidate": "first executed hosted ledger write"},
    )

    inserted = conn.execute(ledger_event_insert_sql(), event.as_insert_parameters()).fetchone()
    assert inserted is not None
    event_id, returned_hash = inserted
    assert returned_hash == event.event_hash

    fetched = conn.execute(
        "SELECT event_type, idempotency_key, payload, event_hash "
        "FROM cortex_hosted.ledger_events WHERE event_id = %(event_id)s",
        {"event_id": event_id},
    ).fetchone()
    assert fetched is not None
    assert fetched[0] == LedgerEventType.CANDIDATE_PROPOSED.value
    assert fetched[1] == f"it-{run_id}"
    assert fetched[2] == {"candidate": "first executed hosted ledger write"}
    assert fetched[3] == event.event_hash

    # Idempotent retry: the same idempotency key inserts nothing new.
    retry = conn.execute(ledger_event_insert_sql(), event.as_insert_parameters()).fetchone()
    assert retry is None

    conn.commit()

    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.DatabaseError, match="append-only"):
        conn.execute(
            "UPDATE cortex_hosted.ledger_events SET actor_id = 'mutant' "
            "WHERE event_id = %(event_id)s",
            {"event_id": event_id},
        )
    conn.rollback()
