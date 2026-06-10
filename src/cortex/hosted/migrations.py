"""Hosted schema migration runner (cortex#472).

Applies the shipped canonical DDL from
:func:`cortex.hosted.schema.create_schema_sql` — it does not re-author
tables. The DDL is idempotent by construction (``IF NOT EXISTS`` + guarded
``DO`` blocks + ``schema_migrations`` ``ON CONFLICT DO NOTHING``), so "the
migration" and "the schema" cannot drift apart: one source of truth, one
executable path for local Postgres and Railway alike.

Runner contract:

- :func:`verify_extensions` checks pgcrypto/pg_trgm/vector availability
  *before* apply and raises naming any missing extension — a Postgres image
  without them fails the migration visibly, never silently;
- :func:`apply_schema` executes the DDL in one transaction, then verifies
  that ``cortex_hosted.schema_migrations`` actually records
  ``HOSTED_SCHEMA_VERSION`` before reporting success;
- :func:`schema_status` reports the recorded version and table count for
  doctor-style reporting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cortex.hosted.db import HostedConnection
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION, create_schema_sql

# Required by the shipped DDL (CREATE EXTENSION IF NOT EXISTS ...): pgcrypto
# for gen_random_uuid()/digest(), pg_trgm for trigram search indexes, vector
# for the pgvector embeddings projection.
REQUIRED_EXTENSIONS = ("pgcrypto", "pg_trgm", "vector")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class HostedMigrationError(ValueError):
    """Raised when the hosted schema cannot be — or provably was not — applied."""


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of one :func:`apply_schema` run."""

    version: int
    already_current: bool

    def __post_init__(self) -> None:
        if self.version < 1:
            raise HostedMigrationError(f"schema version must be >= 1, got {self.version}")

    def describe(self) -> str:
        if self.already_current:
            return f"hosted schema already current at version {self.version}"
        return f"hosted schema applied at version {self.version}"


@dataclass(frozen=True)
class SchemaStatus:
    """Doctor-style snapshot of the hosted schema in one database."""

    schema: str
    version: int | None
    table_count: int

    def __post_init__(self) -> None:
        _validate_sql_identifier(self.schema)
        if self.version is not None and self.version < 1:
            raise HostedMigrationError(f"schema version must be >= 1, got {self.version}")
        if self.table_count < 0:
            raise HostedMigrationError(f"table_count must be >= 0, got {self.table_count}")

    def describe(self) -> str:
        if self.version is None:
            return (
                f"hosted schema {self.schema!r} is not applied "
                f"(no schema_migrations record; {self.table_count} tables)"
            )
        return (
            f"hosted schema {self.schema!r} at version {self.version} "
            f"({self.table_count} tables)"
        )


def verify_extensions(
    conn: HostedConnection,
    required: tuple[str, ...] = REQUIRED_EXTENSIONS,
) -> tuple[str, ...]:
    """Verify extension availability on the connected Postgres image.

    Runs BEFORE apply. Raises :class:`HostedMigrationError` naming every
    missing extension — this is the Railway-image verification: a target
    Postgres without pgvector (or pgcrypto/pg_trgm) must fail the migration
    visibly instead of degrading. Returns the verified tuple on success.
    """

    if not required:
        raise HostedMigrationError("required extensions must not be empty")
    result = conn.execute(
        "SELECT name FROM pg_available_extensions WHERE name = ANY(%(names)s)",
        {"names": list(required)},
    )
    available = {row[0] for row in result.fetchall()}
    missing = tuple(name for name in required if name not in available)
    if missing:
        raise HostedMigrationError(
            f"missing Postgres extension(s): {', '.join(missing)}; the hosted schema "
            f"requires {', '.join(required)}. Provision a Postgres image that ships "
            "them (on Railway: a pgvector-enabled Postgres image/template) — a "
            "missing extension fails the migration, never degrades silently"
        )
    return required


def apply_schema(conn: HostedConnection, schema: str = "cortex_hosted") -> MigrationResult:
    """Apply the shipped canonical DDL and verify it was recorded.

    The DDL inserts ``HOSTED_SCHEMA_VERSION`` into ``schema_migrations``
    (``ON CONFLICT DO NOTHING``); this runner re-reads the table after apply
    and refuses to report success unless the version is actually recorded.
    Failures roll back, leaving the connection reusable and the database in
    its pre-run state.
    """

    _validate_sql_identifier(schema)
    try:
        verify_extensions(conn)
        recorded_before = _recorded_version(conn, schema)
        if recorded_before is not None and recorded_before > HOSTED_SCHEMA_VERSION:
            raise HostedMigrationError(
                f"database records hosted schema version {recorded_before}, newer than "
                f"this build's HOSTED_SCHEMA_VERSION {HOSTED_SCHEMA_VERSION}; refusing "
                "to apply older DDL over a newer schema"
            )
        conn.execute(create_schema_sql(schema))
        recorded_after = _recorded_version(conn, schema)
        if recorded_after != HOSTED_SCHEMA_VERSION:
            raise HostedMigrationError(
                f"{schema}.schema_migrations records version {recorded_after!r} after "
                f"apply; expected {HOSTED_SCHEMA_VERSION} — refusing to report success"
            )
    except HostedMigrationError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HostedMigrationError(
            f"applying hosted schema version {HOSTED_SCHEMA_VERSION} to {schema!r} "
            f"failed and was rolled back: {exc}"
        ) from exc
    conn.commit()
    return MigrationResult(
        version=HOSTED_SCHEMA_VERSION,
        already_current=recorded_before == HOSTED_SCHEMA_VERSION,
    )


def schema_status(conn: HostedConnection, schema: str = "cortex_hosted") -> SchemaStatus:
    """Report the recorded schema version and table count for doctor output."""

    _validate_sql_identifier(schema)
    version = _recorded_version(conn, schema)
    count_row = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = %(schema)s AND table_type = 'BASE TABLE'",
        {"schema": schema},
    ).fetchone()
    if count_row is None:
        raise HostedMigrationError(
            f"table-count query for schema {schema!r} returned no row; "
            "refusing to report a default"
        )
    return SchemaStatus(schema=schema, version=version, table_count=int(count_row[0]))


def _recorded_version(conn: HostedConnection, schema: str) -> int | None:
    """Read the highest recorded migration version, or None before first apply."""

    exists_row = conn.execute(
        "SELECT to_regclass(%(qualified)s)",
        {"qualified": f"{schema}.schema_migrations"},
    ).fetchone()
    if exists_row is None or exists_row[0] is None:
        return None
    version_row = conn.execute(
        f"SELECT max(version) FROM {schema}.schema_migrations"
    ).fetchone()
    if version_row is None or version_row[0] is None:
        return None
    return int(version_row[0])


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise HostedMigrationError(f"invalid SQL identifier: {name!r}")
