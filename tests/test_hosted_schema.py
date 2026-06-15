from __future__ import annotations

import re

import pytest

from cortex.hosted.ledger_events import LedgerEventType
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION, create_schema_sql
from cortex.hosted.scopes import ScopeType


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
    ddl_event_types = {value.strip().strip("'") for value in event_check.group(1).split(",")}

    assert ddl_event_types == {event.value for event in LedgerEventType}


def test_schema_tracks_rebuildable_projections_and_traces() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.decision_nodes" in sql
    assert "repo_id uuid" in sql
    assert "Current graph projection rebuilt from ledger_events" in sql
    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.decision_scopes" in sql
    assert "Structural search projection rebuilt from decision_versions" in sql
    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.retrieval_traces" in sql
    assert "candidate sets, scores, reasons, omitted counts" in sql


def test_schema_scope_type_check_matches_python_enum() -> None:
    sql = create_schema_sql()

    scope_check = re.search(r"scope_type text NOT NULL,\n    scope_value text NOT NULL", sql)
    assert scope_check is not None
    scope_values_check = re.search(r"CHECK \(scope_type IN \(([^)]+)\)\)", sql)
    assert scope_values_check is not None
    ddl_scope_types = {value.strip().strip("'") for value in scope_values_check.group(1).split(",")}

    assert ddl_scope_types == {scope.value for scope in ScopeType}


def test_schema_adds_repo_aware_scope_indexes() -> None:
    sql = create_schema_sql()

    assert "ADD COLUMN IF NOT EXISTS repo_id uuid" in sql
    assert "decision_nodes_repo_fk" in sql
    assert "decision_scopes_repo_fk" in sql
    assert "decision_nodes_tenant_repo_status_idx" in sql
    assert "decision_scopes_tenant_repo_type_value_idx" in sql
    assert "decision_scopes_path_idx" in sql
    assert "decision_scopes_symbol_idx" in sql
    assert "decision_scopes_config_key_idx" in sql


def test_schema_adds_ask_ledger_search_indexes_and_embeddings_projection() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.embeddings" in sql
    assert "item_id uuid NOT NULL" in sql
    assert "embedding vector NOT NULL" in sql
    assert "embedding_dimension integer NOT NULL" in sql
    assert "embeddings_projection_unique" in sql
    assert (
        "UNIQUE (tenant_id, item_type, item_id, embedding_model_id, embedding_dimension, embedding_epoch)"
        in sql
    )
    assert "CHECK (item_type IN ('decision_version', 'source_span'))" in sql
    assert "CHECK (embedding_dimension > 0 AND embedding_dimension = vector_dims(embedding))" in sql
    assert "DROP CONSTRAINT embeddings_item_id_fkey" in sql
    assert "DROP CONSTRAINT embeddings_item_type_check" in sql
    assert "DROP CONSTRAINT IF EXISTS embeddings_dimension_check" in sql
    assert "WHERE embedding_dimension IS DISTINCT FROM vector_dims(embedding)" in sql
    assert "decision_versions_text_fts_idx" in sql
    assert "decision_versions_text_trgm_idx" in sql
    assert "source_spans_excerpt_fts_idx" in sql
    assert "source_spans_excerpt_trgm_idx" in sql
    assert "embeddings_projection_lookup_idx" in sql
    assert "embeddings_model_epoch_dim_idx" in sql
    assert "keyed by item, model, dimension, and epoch" in sql


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
        "CONSTRAINT source_spans_source_document_snapshot_fk\n"
        "    FOREIGN KEY (tenant_id, source_document_id, source_document_hash)\n"
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

    # v11 (cortex#397): the per-repo review rollout event stream.
    assert HOSTED_SCHEMA_VERSION == 11
    assert f"VALUES ({HOSTED_SCHEMA_VERSION})" in sql


def test_schema_models_staged_traffic_registry() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.review_staged_prs" in sql
    assert (
        "CONSTRAINT review_staged_prs_pr_unique\n"
        "        UNIQUE (tenant_id, repo_full_name, pr_number)"
    ) in sql
    assert "CHECK (reason IN ('title-token', 'label', 'operator-backfill'))" in sql
    assert "BEFORE UPDATE ON cortex_hosted.review_staged_prs" in sql
    assert "BEFORE DELETE ON cortex_hosted.review_staged_prs" in sql
    assert "OPERATOR-INTERNAL staged-traffic registry" in sql
    # The version stamp lands only after the staged registry exists.
    assert sql.rfind(f"VALUES ({HOSTED_SCHEMA_VERSION})") > sql.rfind(
        "CREATE TABLE IF NOT EXISTS cortex_hosted.review_staged_prs"
    )


def test_schema_models_review_rollout_events() -> None:
    sql = create_schema_sql()

    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.review_rollout_events" in sql
    assert "CONSTRAINT review_rollout_events_idempotency_key_unique UNIQUE" in sql
    assert "review_rollout_events_repo_time_idx" in sql
    assert "prevent_review_rollout_mutation" in sql
    assert "BEFORE UPDATE ON cortex_hosted.review_rollout_events" in sql
    assert "BEFORE DELETE ON cortex_hosted.review_rollout_events" in sql
    assert "No row for a repo means disabled" in sql
    assert sql.rfind(f"VALUES ({HOSTED_SCHEMA_VERSION})") > sql.rfind(
        "CREATE TABLE IF NOT EXISTS cortex_hosted.review_rollout_events"
    )


def test_schema_migrates_v1_source_provenance_tables() -> None:
    sql = create_schema_sql()

    assert "ADD COLUMN IF NOT EXISTS document_hash text" in sql
    assert "ADD COLUMN IF NOT EXISTS source_revision text" in sql
    assert "WHERE document_hash IS NULL" in sql
    assert "ALTER COLUMN document_hash SET NOT NULL" in sql
    assert "DROP CONSTRAINT source_documents_tenant_id_source_id_external_id_key" in sql
    assert "ADD COLUMN IF NOT EXISTS source_document_hash text" in sql
    assert "WHERE span.source_document_hash IS NULL" in sql
    assert "ALTER COLUMN source_document_hash SET NOT NULL" in sql
    assert "DROP CONSTRAINT source_spans_source_document_id_fkey" in sql
    assert "ADD CONSTRAINT source_spans_source_document_snapshot_fk" in sql
    assert sql.rfind(f"VALUES ({HOSTED_SCHEMA_VERSION})") > sql.rfind(
        "ADD CONSTRAINT source_spans_source_document_snapshot_fk"
    )


def test_schema_constraint_addition_is_idempotent() -> None:
    sql = create_schema_sql()

    assert "IF NOT EXISTS (" in sql
    assert "decision_nodes_current_version_fk" in sql
    assert "ALTER TABLE cortex_hosted.decision_nodes" in sql


def test_schema_indexes_source_visibility_boundary() -> None:
    sql = create_schema_sql()

    assert "sources_tenant_repo_visibility_idx" in sql
    assert "sources_visibility_gin_idx" in sql
    assert "source_documents_tenant_source_visibility_idx" in sql
    assert "source_documents_visibility_gin_idx" in sql
    assert "External source authorization boundary" in sql
    assert "slack_channel_excluded" in sql
    assert "repo_installation_revoked" in sql


def test_schema_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(ValueError, match="invalid SQL identifier"):
        create_schema_sql("cortex; DROP SCHEMA public")
