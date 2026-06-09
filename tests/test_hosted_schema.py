from __future__ import annotations

import re

import pytest

from cortex.hosted.ledger_events import LedgerEventType
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION, create_schema_sql


def test_schema_declares_postgres_extensions() -> None:
    sql = create_schema_sql()

    assert "CREATE EXTENSION IF NOT EXISTS pgcrypto;" in sql
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm;" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql


def test_schema_has_append_only_ledger_events_table() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.ledger_events" in sql
    assert "UNIQUE (tenant_id, idempotency_key)" in sql
    assert "prevent_ledger_event_mutation" in sql
    assert "BEFORE UPDATE ON cortex_hosted.ledger_events" in sql
    assert "BEFORE DELETE ON cortex_hosted.ledger_events" in sql
    assert "ledger_events is append-only" in sql


def test_schema_event_type_check_matches_python_enum() -> None:
    sql = create_schema_sql()

    event_check = re.search(r"event_type text NOT NULL CHECK \(event_type IN \(([^)]+)\)\)", sql)
    assert event_check is not None
    ddl_event_types = {
        value.strip().strip("'")
        for value in event_check.group(1).split(",")
    }

    assert ddl_event_types == {event.value for event in LedgerEventType}


def test_schema_tracks_rebuildable_projections_and_traces() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.decision_nodes" in sql
    assert "Current graph projection rebuilt from ledger_events" in sql
    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.decision_scopes" in sql
    assert "Structural search projection rebuilt from decision_versions" in sql
    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.retrieval_traces" in sql
    assert "candidate sets, scores, reasons, omitted counts" in sql


def test_schema_models_source_documents_as_immutable_snapshots() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.source_documents" in sql
    assert "document_hash text NOT NULL" in sql
    assert "source_revision text" in sql
    assert "UNIQUE (tenant_id, document_hash)" in sql
    assert "UNIQUE (tenant_id, source_document_id, document_hash)" in sql
    assert "UNIQUE (tenant_id, source_id, external_id, content_hash)" in sql
    assert "BEFORE UPDATE ON cortex_hosted.source_documents" in sql
    assert "BEFORE DELETE ON cortex_hosted.source_documents" in sql
    assert "Immutable source snapshots keyed by content hash" in sql


def test_schema_models_citable_source_spans() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.source_spans" in sql
    assert "source_document_id uuid NOT NULL" in sql
    assert "source_document_hash text NOT NULL" in sql
    assert (
        "FOREIGN KEY (tenant_id, source_document_id, source_document_hash)\n"
        "        REFERENCES cortex_hosted.source_documents (\n"
        "            tenant_id,\n"
        "            source_document_id,\n"
        "            document_hash\n"
        "        )"
    ) in sql
    assert "UNIQUE (tenant_id, span_hash)" in sql
    assert "BEFORE UPDATE ON cortex_hosted.source_spans" in sql
    assert "BEFORE DELETE ON cortex_hosted.source_spans" in sql
    assert "Citable source excerpts derived from immutable source document snapshots" in sql


def test_schema_records_version() -> None:
    sql = create_schema_sql()

    assert HOSTED_SCHEMA_VERSION == 1
    assert f"VALUES ({HOSTED_SCHEMA_VERSION})" in sql


def test_schema_constraint_addition_is_idempotent() -> None:
    sql = create_schema_sql()

    assert "IF NOT EXISTS (" in sql
    assert "decision_nodes_current_version_fk" in sql
    assert "ALTER TABLE cortex_hosted.decision_nodes" in sql


def test_schema_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(ValueError, match="invalid SQL identifier"):
        create_schema_sql("cortex; DROP SCHEMA public")
