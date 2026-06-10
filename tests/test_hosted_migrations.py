"""Unit tests for the hosted schema migration runner (cortex#472).

No driver required: a scripted fake connection satisfies the
``HostedConnection`` protocol so the apply/verify/status flows and the
failure taxonomy are covered without a live Postgres. The live flows run in
``tests/test_hosted_db_integration.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from cortex.hosted.migrations import (
    REQUIRED_EXTENSIONS,
    HostedMigrationError,
    MigrationResult,
    SchemaStatus,
    apply_schema,
    schema_status,
    verify_extensions,
)
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION

# Base tables in the shipped v6 DDL; the fake reports this after a
# successful apply so status assertions have a concrete shape.
EXPECTED_TABLE_COUNT = 14


class FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeConnection:
    """Scripted stand-in for a psycopg connection (HostedConnection protocol)."""

    def __init__(
        self,
        *,
        available_extensions: tuple[str, ...] = REQUIRED_EXTENSIONS,
        recorded_version: int | None = None,
        table_count: int = 0,
        fail_apply: Exception | None = None,
        record_on_apply: bool = True,
    ) -> None:
        self.available_extensions = set(available_extensions)
        self.recorded_version = recorded_version
        self.table_count = table_count
        self.fail_apply = fail_apply
        self.record_on_apply = record_on_apply
        self.executed: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> FakeResult:
        self.executed.append(query)
        if "pg_available_extensions" in query:
            assert params is not None
            names = params["names"]
            return FakeResult([(name,) for name in names if name in self.available_extensions])
        if "to_regclass" in query:
            assert params is not None
            exists = self.recorded_version is not None
            return FakeResult([(params["qualified"] if exists else None,)])
        if "max(version)" in query:
            return FakeResult([(self.recorded_version,)])
        if "information_schema.tables" in query:
            return FakeResult([(self.table_count,)])
        if query.lstrip().startswith("CREATE EXTENSION"):
            if self.fail_apply is not None:
                raise self.fail_apply
            if self.record_on_apply:
                self.recorded_version = max(self.recorded_version or 0, HOSTED_SCHEMA_VERSION)
                self.table_count = EXPECTED_TABLE_COUNT
            return FakeResult([])
        raise AssertionError(f"unexpected query: {query[:80]!r}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass

    def ddl_runs(self) -> int:
        return sum(1 for query in self.executed if query.lstrip().startswith("CREATE EXTENSION"))


def test_apply_schema_on_a_fresh_database_records_the_shipped_version() -> None:
    conn = FakeConnection()
    result = apply_schema(conn)
    assert result.version == HOSTED_SCHEMA_VERSION
    assert result.already_current is False
    assert conn.commits == 1
    assert conn.rollbacks == 0
    assert conn.ddl_runs() == 1
    assert result.describe() == f"hosted schema applied at version {HOSTED_SCHEMA_VERSION}"


def test_extensions_are_verified_before_any_ddl_executes() -> None:
    """Invariant: the availability probe always precedes the schema script."""

    conn = FakeConnection()
    apply_schema(conn)
    probe_index = next(
        index for index, query in enumerate(conn.executed) if "pg_available_extensions" in query
    )
    ddl_index = next(
        index
        for index, query in enumerate(conn.executed)
        if query.lstrip().startswith("CREATE EXTENSION")
    )
    assert probe_index < ddl_index


def test_second_apply_reports_already_current_with_the_version_number() -> None:
    conn = FakeConnection()
    apply_schema(conn)
    second = apply_schema(conn)
    assert second.version == HOSTED_SCHEMA_VERSION
    assert second.already_current is True
    assert second.describe() == (
        f"hosted schema already current at version {HOSTED_SCHEMA_VERSION}"
    )


def test_missing_extension_fails_visibly_before_apply() -> None:
    conn = FakeConnection(available_extensions=("pgcrypto", "pg_trgm"))
    with pytest.raises(HostedMigrationError, match="vector"):
        apply_schema(conn)
    assert conn.ddl_runs() == 0
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_apply_refuses_to_run_older_ddl_over_a_newer_schema() -> None:
    conn = FakeConnection(
        recorded_version=HOSTED_SCHEMA_VERSION + 1, table_count=EXPECTED_TABLE_COUNT
    )
    with pytest.raises(HostedMigrationError, match="newer than"):
        apply_schema(conn)
    assert conn.ddl_runs() == 0
    assert conn.commits == 0


def test_apply_wraps_driver_failures_and_rolls_back() -> None:
    boom = RuntimeError("server exploded mid-DDL")
    conn = FakeConnection(fail_apply=boom)
    with pytest.raises(HostedMigrationError, match="rolled back") as excinfo:
        apply_schema(conn)
    assert excinfo.value.__cause__ is boom
    assert conn.rollbacks == 1
    assert conn.commits == 0


def test_apply_fails_closed_when_the_version_is_not_recorded() -> None:
    """The runner refuses to report success it cannot read back."""

    conn = FakeConnection(record_on_apply=False)
    with pytest.raises(HostedMigrationError, match="refusing to report success"):
        apply_schema(conn)
    assert conn.rollbacks == 1
    assert conn.commits == 0


@pytest.mark.parametrize("bad_schema", ["bad-name", "1bad", "drop table;--", ""])
def test_invalid_schema_identifiers_are_rejected(bad_schema: str) -> None:
    conn = FakeConnection()
    with pytest.raises(HostedMigrationError, match="invalid SQL identifier"):
        apply_schema(conn, schema=bad_schema)
    with pytest.raises(HostedMigrationError, match="invalid SQL identifier"):
        schema_status(conn, schema=bad_schema)
    assert conn.executed == []


def test_verify_extensions_returns_the_required_tuple_when_available() -> None:
    conn = FakeConnection()
    assert verify_extensions(conn) == REQUIRED_EXTENSIONS


def test_verify_extensions_names_every_missing_extension() -> None:
    conn = FakeConnection(available_extensions=())
    with pytest.raises(HostedMigrationError, match="pgcrypto, pg_trgm, vector"):
        verify_extensions(conn)


def test_verify_extensions_rejects_an_empty_requirement_list() -> None:
    with pytest.raises(HostedMigrationError, match="must not be empty"):
        verify_extensions(FakeConnection(), required=())


def test_schema_status_before_first_apply() -> None:
    conn = FakeConnection()
    status = schema_status(conn)
    assert status.version is None
    assert status.table_count == 0
    assert "not applied" in status.describe()


def test_schema_status_after_apply_reports_version_and_table_count() -> None:
    conn = FakeConnection()
    apply_schema(conn)
    status = schema_status(conn)
    assert status == SchemaStatus(
        schema="cortex_hosted",
        version=HOSTED_SCHEMA_VERSION,
        table_count=EXPECTED_TABLE_COUNT,
    )
    assert str(HOSTED_SCHEMA_VERSION) in status.describe()


def test_migration_result_rejects_nonsense_versions() -> None:
    with pytest.raises(HostedMigrationError, match=">= 1"):
        MigrationResult(version=0, already_current=False)


def test_schema_status_rejects_negative_table_counts() -> None:
    with pytest.raises(HostedMigrationError, match="table_count"):
        SchemaStatus(schema="cortex_hosted", version=None, table_count=-1)
