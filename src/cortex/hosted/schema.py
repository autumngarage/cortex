"""Versioned Postgres schema for the hosted decision ledger."""

from __future__ import annotations

import re

from cortex.hosted.ledger_events import LedgerEventType
from cortex.hosted.scopes import ScopeType

HOSTED_SCHEMA_VERSION = 5
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def create_schema_sql(schema: str = "cortex_hosted") -> str:
    """Return the hosted Postgres schema DDL.

    The DDL is intentionally Postgres-specific. Local SQLite retrieve indexes
    remain rebuildable caches and do not implement hosted graph semantics.
    """

    _validate_sql_identifier(schema)
    event_values = ", ".join(f"'{event.value}'" for event in LedgerEventType)
    scope_values = ", ".join(f"'{scope.value}'" for scope in ScopeType)
    return f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.tenants (
    tenant_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug text NOT NULL UNIQUE,
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (slug <> ''),
    CHECK (display_name <> '')
);

CREATE TABLE IF NOT EXISTS {schema}.repos (
    repo_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    provider text NOT NULL,
    owner text NOT NULL,
    name text NOT NULL,
    external_id text NOT NULL,
    default_branch text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider, external_id),
    CHECK (provider <> ''),
    CHECK (owner <> ''),
    CHECK (name <> ''),
    CHECK (external_id <> '')
);

CREATE TABLE IF NOT EXISTS {schema}.sources (
    source_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    repo_id uuid REFERENCES {schema}.repos (repo_id),
    source_type text NOT NULL,
    external_id text NOT NULL,
    visibility jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_type, external_id),
    CHECK (source_type <> ''),
    CHECK (external_id <> ''),
    CHECK (jsonb_typeof(visibility) = 'object')
);

CREATE TABLE IF NOT EXISTS {schema}.source_documents (
    source_document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    source_id uuid NOT NULL REFERENCES {schema}.sources (source_id),
    document_type text NOT NULL,
    external_id text NOT NULL,
    permalink text NOT NULL,
    author_ref text NOT NULL,
    source_timestamp timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    content_hash text NOT NULL,
    document_hash text NOT NULL,
    source_revision text,
    visibility jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    CONSTRAINT source_documents_snapshot_unique UNIQUE (tenant_id, document_hash),
    CONSTRAINT source_documents_id_hash_unique UNIQUE (tenant_id, source_document_id, document_hash),
    CONSTRAINT source_documents_external_content_unique UNIQUE (tenant_id, source_id, external_id, content_hash),
    CHECK (document_type <> ''),
    CHECK (external_id <> ''),
    CHECK (permalink <> ''),
    CHECK (author_ref <> ''),
    CHECK (content_hash ~ '^[a-f0-9]{{64}}$'),
    CONSTRAINT source_documents_document_hash_check CHECK (document_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (jsonb_typeof(visibility) = 'object'),
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE TABLE IF NOT EXISTS {schema}.source_spans (
    source_span_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    source_document_id uuid NOT NULL,
    source_document_hash text NOT NULL,
    span_hash text NOT NULL,
    start_offset integer NOT NULL,
    end_offset integer NOT NULL,
    excerpt text NOT NULL,
    permalink text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, span_hash),
    CONSTRAINT source_spans_source_document_hash_check CHECK (source_document_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (span_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (start_offset >= 0),
    CHECK (end_offset > start_offset),
    CHECK (excerpt <> ''),
    CHECK (permalink <> ''),
    CONSTRAINT source_spans_source_document_snapshot_fk
    FOREIGN KEY (tenant_id, source_document_id, source_document_hash)
        REFERENCES {schema}.source_documents (
            tenant_id,
            source_document_id,
            document_hash
        )
);

DO $$
BEGIN
    ALTER TABLE {schema}.source_documents
        ADD COLUMN IF NOT EXISTS document_hash text;

    ALTER TABLE {schema}.source_documents
        ADD COLUMN IF NOT EXISTS source_revision text;

    UPDATE {schema}.source_documents
    SET document_hash = encode(
        digest(
            '{{"content_hash":' || to_json(content_hash)::text ||
            ',"external_id":' || to_json(external_id)::text ||
            ',"source_id":' || to_json(source_id::text)::text ||
            ',"tenant_id":' || to_json(tenant_id::text)::text ||
            '}}',
            'sha256'
        ),
        'hex'
    )
    WHERE document_hash IS NULL;

    ALTER TABLE {schema}.source_documents
        ALTER COLUMN document_hash SET NOT NULL;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_documents_tenant_id_source_id_external_id_key'
          AND conrelid = '{schema}.source_documents'::regclass
    ) THEN
        ALTER TABLE {schema}.source_documents
            DROP CONSTRAINT source_documents_tenant_id_source_id_external_id_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_documents_snapshot_unique'
          AND conrelid = '{schema}.source_documents'::regclass
    ) THEN
        ALTER TABLE {schema}.source_documents
            ADD CONSTRAINT source_documents_snapshot_unique
            UNIQUE (tenant_id, document_hash);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_documents_id_hash_unique'
          AND conrelid = '{schema}.source_documents'::regclass
    ) THEN
        ALTER TABLE {schema}.source_documents
            ADD CONSTRAINT source_documents_id_hash_unique
            UNIQUE (tenant_id, source_document_id, document_hash);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_documents_external_content_unique'
          AND conrelid = '{schema}.source_documents'::regclass
    ) THEN
        ALTER TABLE {schema}.source_documents
            ADD CONSTRAINT source_documents_external_content_unique
            UNIQUE (tenant_id, source_id, external_id, content_hash);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_documents_document_hash_check'
          AND conrelid = '{schema}.source_documents'::regclass
    ) THEN
        ALTER TABLE {schema}.source_documents
            ADD CONSTRAINT source_documents_document_hash_check
            CHECK (document_hash ~ '^[a-f0-9]{{64}}$');
    END IF;

    ALTER TABLE {schema}.source_spans
        ADD COLUMN IF NOT EXISTS source_document_hash text;

    UPDATE {schema}.source_spans AS span
    SET source_document_hash = doc.document_hash
    FROM {schema}.source_documents AS doc
    WHERE span.source_document_hash IS NULL
      AND span.tenant_id = doc.tenant_id
      AND span.source_document_id = doc.source_document_id;

    ALTER TABLE {schema}.source_spans
        ALTER COLUMN source_document_hash SET NOT NULL;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_spans_source_document_id_fkey'
          AND conrelid = '{schema}.source_spans'::regclass
    ) THEN
        ALTER TABLE {schema}.source_spans
            DROP CONSTRAINT source_spans_source_document_id_fkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_spans_source_document_hash_check'
          AND conrelid = '{schema}.source_spans'::regclass
    ) THEN
        ALTER TABLE {schema}.source_spans
            ADD CONSTRAINT source_spans_source_document_hash_check
            CHECK (source_document_hash ~ '^[a-f0-9]{{64}}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'source_spans_source_document_snapshot_fk'
          AND conrelid = '{schema}.source_spans'::regclass
    ) THEN
        ALTER TABLE {schema}.source_spans
            ADD CONSTRAINT source_spans_source_document_snapshot_fk
            FOREIGN KEY (tenant_id, source_document_id, source_document_hash)
            REFERENCES {schema}.source_documents (
                tenant_id,
                source_document_id,
                document_hash
            );
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS {schema}.ledger_events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    source_id uuid NOT NULL REFERENCES {schema}.sources (source_id),
    event_type text NOT NULL CHECK (event_type IN ({event_values})),
    event_version integer NOT NULL DEFAULT 1,
    actor_type text NOT NULL,
    actor_id text NOT NULL,
    occurred_at timestamptz NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    idempotency_key text NOT NULL,
    source_event_external_id text,
    source_span_hashes text[] NOT NULL DEFAULT ARRAY[]::text[],
    graph_snapshot_hash text,
    model_id text,
    prompt_version text,
    payload jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    previous_event_hash text,
    event_hash text NOT NULL,
    UNIQUE (tenant_id, idempotency_key),
    CHECK (event_version >= 1),
    CHECK (actor_type <> ''),
    CHECK (actor_id <> ''),
    CHECK (idempotency_key <> ''),
    CHECK (jsonb_typeof(payload) = 'object'),
    CHECK (jsonb_typeof(metadata) = 'object'),
    CHECK (graph_snapshot_hash IS NULL OR graph_snapshot_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (previous_event_hash IS NULL OR previous_event_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (event_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (
        (model_id IS NULL AND prompt_version IS NULL)
        OR (model_id IS NOT NULL AND prompt_version IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS {schema}.graph_snapshots (
    graph_snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    graph_snapshot_hash text NOT NULL,
    schema_version integer NOT NULL,
    retrieval_config_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    source_event_id uuid NOT NULL REFERENCES {schema}.ledger_events (event_id),
    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    UNIQUE (tenant_id, graph_snapshot_hash),
    CHECK (graph_snapshot_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (schema_version >= 1),
    CHECK (retrieval_config_version <> ''),
    CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE TABLE IF NOT EXISTS {schema}.decision_nodes (
    decision_node_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    repo_id uuid,
    current_version_id uuid,
    status text NOT NULL,
    confidence text NOT NULL,
    latest_event_id uuid NOT NULL REFERENCES {schema}.ledger_events (event_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('candidate', 'confirmed', 'rejected', 'superseded', 'stale')),
    CHECK (confidence <> '')
);

CREATE TABLE IF NOT EXISTS {schema}.decision_versions (
    decision_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    decision_node_id uuid NOT NULL REFERENCES {schema}.decision_nodes (decision_node_id),
    source_event_id uuid NOT NULL REFERENCES {schema}.ledger_events (event_id),
    decision_text text NOT NULL,
    source_span_hashes text[] NOT NULL,
    scope jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    decided_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (decision_text <> ''),
    CHECK (cardinality(source_span_hashes) > 0),
    CHECK (jsonb_typeof(scope) = 'object')
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'decision_nodes_current_version_fk'
          AND conrelid = '{schema}.decision_nodes'::regclass
    ) THEN
        ALTER TABLE {schema}.decision_nodes
            ADD CONSTRAINT decision_nodes_current_version_fk
            FOREIGN KEY (current_version_id)
            REFERENCES {schema}.decision_versions (decision_version_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS {schema}.decision_edges (
    decision_edge_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    from_node_id uuid NOT NULL REFERENCES {schema}.decision_nodes (decision_node_id),
    to_node_id uuid NOT NULL REFERENCES {schema}.decision_nodes (decision_node_id),
    edge_type text NOT NULL,
    source_event_id uuid NOT NULL REFERENCES {schema}.ledger_events (event_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, from_node_id, to_node_id, edge_type),
    CHECK (edge_type IN ('supersedes', 'duplicates', 'refines', 'contradicts', 'derived_from', 'mentioned_with')),
    CHECK (from_node_id <> to_node_id)
);

CREATE TABLE IF NOT EXISTS {schema}.decision_scopes (
    decision_scope_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    repo_id uuid,
    decision_node_id uuid NOT NULL REFERENCES {schema}.decision_nodes (decision_node_id),
    scope_type text NOT NULL,
    scope_value text NOT NULL,
    normalized_value text NOT NULL,
    source_event_id uuid NOT NULL REFERENCES {schema}.ledger_events (event_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, decision_node_id, scope_type, normalized_value),
    CHECK (scope_type IN ({scope_values})),
    CHECK (scope_value <> ''),
    CHECK (normalized_value <> '')
);

DO $$
BEGIN
    ALTER TABLE {schema}.decision_nodes
        ADD COLUMN IF NOT EXISTS repo_id uuid;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'decision_nodes_repo_fk'
          AND conrelid = '{schema}.decision_nodes'::regclass
    ) THEN
        ALTER TABLE {schema}.decision_nodes
            ADD CONSTRAINT decision_nodes_repo_fk
            FOREIGN KEY (repo_id)
            REFERENCES {schema}.repos (repo_id);
    END IF;

    ALTER TABLE {schema}.decision_scopes
        ADD COLUMN IF NOT EXISTS repo_id uuid;

    UPDATE {schema}.decision_scopes AS scope
    SET repo_id = node.repo_id
    FROM {schema}.decision_nodes AS node
    WHERE scope.repo_id IS NULL
      AND scope.tenant_id = node.tenant_id
      AND scope.decision_node_id = node.decision_node_id
      AND node.repo_id IS NOT NULL;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'decision_scopes_repo_fk'
          AND conrelid = '{schema}.decision_scopes'::regclass
    ) THEN
        ALTER TABLE {schema}.decision_scopes
            ADD CONSTRAINT decision_scopes_repo_fk
            FOREIGN KEY (repo_id)
            REFERENCES {schema}.repos (repo_id);
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS {schema}.retrieval_traces (
    retrieval_trace_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    graph_snapshot_hash text NOT NULL,
    retrieval_config_version text NOT NULL,
    query_kind text NOT NULL,
    query_input_hash text NOT NULL,
    candidate_set_hash text NOT NULL,
    candidates jsonb NOT NULL,
    omitted_counts jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    reason_codes jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (graph_snapshot_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (retrieval_config_version <> ''),
    CHECK (query_kind IN ('ask_ledger', 'decisions_for_diff', 'propose_decision')),
    CHECK (query_input_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (candidate_set_hash ~ '^[a-f0-9]{{64}}$'),
    CHECK (jsonb_typeof(candidates) = 'array'),
    CHECK (jsonb_typeof(omitted_counts) = 'object'),
    CHECK (jsonb_typeof(reason_codes) = 'object')
);

CREATE TABLE IF NOT EXISTS {schema}.embeddings (
    embedding_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES {schema}.tenants (tenant_id),
    repo_id uuid REFERENCES {schema}.repos (repo_id),
    item_type text NOT NULL,
    item_id uuid NOT NULL,
    item_hash text NOT NULL,
    embedding_model_id text NOT NULL,
    embedding_dimension integer NOT NULL,
    embedding_epoch text NOT NULL,
    embedding vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    CONSTRAINT embeddings_projection_unique
        UNIQUE (tenant_id, item_type, item_id, embedding_model_id, embedding_dimension, embedding_epoch),
    CONSTRAINT embeddings_item_type_supported_check
        CHECK (item_type IN ('decision_version', 'source_span')),
    CONSTRAINT embeddings_item_hash_check CHECK (item_hash ~ '^[a-f0-9]{{64}}$'),
    CONSTRAINT embeddings_dimension_check CHECK (embedding_dimension > 0),
    CHECK (embedding_model_id <> ''),
    CHECK (embedding_epoch <> ''),
    CHECK (jsonb_typeof(metadata) = 'object')
);

DO $$
DECLARE
    old_unique record;
BEGIN
    ALTER TABLE {schema}.embeddings
        ADD COLUMN IF NOT EXISTS embedding_dimension integer;

    UPDATE {schema}.embeddings
    SET embedding_dimension = vector_dims(embedding)
    WHERE embedding_dimension IS NULL;

    ALTER TABLE {schema}.embeddings
        ALTER COLUMN embedding_dimension SET NOT NULL;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'embeddings_item_id_fkey'
          AND conrelid = '{schema}.embeddings'::regclass
    ) THEN
        ALTER TABLE {schema}.embeddings
            DROP CONSTRAINT embeddings_item_id_fkey;
    END IF;

    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'embeddings_item_type_check'
          AND conrelid = '{schema}.embeddings'::regclass
    ) THEN
        ALTER TABLE {schema}.embeddings
            DROP CONSTRAINT embeddings_item_type_check;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'embeddings_item_type_supported_check'
          AND conrelid = '{schema}.embeddings'::regclass
    ) THEN
        ALTER TABLE {schema}.embeddings
            ADD CONSTRAINT embeddings_item_type_supported_check
            CHECK (item_type IN ('decision_version', 'source_span'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'embeddings_dimension_check'
          AND conrelid = '{schema}.embeddings'::regclass
    ) THEN
        ALTER TABLE {schema}.embeddings
            ADD CONSTRAINT embeddings_dimension_check
            CHECK (embedding_dimension > 0);
    END IF;

    FOR old_unique IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = '{schema}.embeddings'::regclass
          AND contype = 'u'
          AND pg_get_constraintdef(oid) = 'UNIQUE (tenant_id, item_type, item_id, embedding_model_id, embedding_epoch)'
    LOOP
        EXECUTE format(
            'ALTER TABLE {schema}.embeddings DROP CONSTRAINT %I',
            old_unique.conname
        );
    END LOOP;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'embeddings_projection_unique'
          AND conrelid = '{schema}.embeddings'::regclass
    ) THEN
        ALTER TABLE {schema}.embeddings
            ADD CONSTRAINT embeddings_projection_unique
            UNIQUE (
                tenant_id,
                item_type,
                item_id,
                embedding_model_id,
                embedding_dimension,
                embedding_epoch
            );
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS ledger_events_tenant_time_idx
    ON {schema}.ledger_events (tenant_id, occurred_at, event_id);

CREATE INDEX IF NOT EXISTS ledger_events_source_idx
    ON {schema}.ledger_events (tenant_id, source_id, source_event_external_id);

CREATE INDEX IF NOT EXISTS decision_nodes_status_idx
    ON {schema}.decision_nodes (tenant_id, status, updated_at);

CREATE INDEX IF NOT EXISTS decision_nodes_tenant_repo_status_idx
    ON {schema}.decision_nodes (tenant_id, repo_id, status, updated_at);

CREATE INDEX IF NOT EXISTS decision_edges_from_idx
    ON {schema}.decision_edges (tenant_id, from_node_id, edge_type);

CREATE INDEX IF NOT EXISTS decision_edges_to_idx
    ON {schema}.decision_edges (tenant_id, to_node_id, edge_type);

CREATE INDEX IF NOT EXISTS decision_scopes_lookup_idx
    ON {schema}.decision_scopes (tenant_id, scope_type, normalized_value);

CREATE INDEX IF NOT EXISTS decision_scopes_tenant_repo_type_value_idx
    ON {schema}.decision_scopes (tenant_id, repo_id, scope_type, normalized_value);

CREATE INDEX IF NOT EXISTS decision_scopes_path_idx
    ON {schema}.decision_scopes (tenant_id, repo_id, normalized_value)
    WHERE scope_type = 'path';

CREATE INDEX IF NOT EXISTS decision_scopes_symbol_idx
    ON {schema}.decision_scopes (tenant_id, repo_id, normalized_value)
    WHERE scope_type = 'symbol';

CREATE INDEX IF NOT EXISTS decision_scopes_config_key_idx
    ON {schema}.decision_scopes (tenant_id, repo_id, normalized_value)
    WHERE scope_type = 'config_key';

CREATE INDEX IF NOT EXISTS source_spans_hash_idx
    ON {schema}.source_spans (tenant_id, span_hash);

CREATE INDEX IF NOT EXISTS decision_versions_text_fts_idx
    ON {schema}.decision_versions
    USING gin (to_tsvector('english', decision_text));

CREATE INDEX IF NOT EXISTS decision_versions_text_trgm_idx
    ON {schema}.decision_versions
    USING gin (decision_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS source_spans_excerpt_fts_idx
    ON {schema}.source_spans
    USING gin (to_tsvector('english', excerpt));

CREATE INDEX IF NOT EXISTS source_spans_excerpt_trgm_idx
    ON {schema}.source_spans
    USING gin (excerpt gin_trgm_ops);

CREATE INDEX IF NOT EXISTS source_documents_metadata_gin_idx
    ON {schema}.source_documents USING gin (metadata);

CREATE INDEX IF NOT EXISTS retrieval_traces_tenant_kind_idx
    ON {schema}.retrieval_traces (tenant_id, query_kind, created_at);

CREATE INDEX IF NOT EXISTS embeddings_projection_lookup_idx
    ON {schema}.embeddings (
        tenant_id,
        item_type,
        item_id,
        embedding_model_id,
        embedding_dimension,
        embedding_epoch
    );

CREATE INDEX IF NOT EXISTS embeddings_model_epoch_dim_idx
    ON {schema}.embeddings (
        tenant_id,
        embedding_model_id,
        embedding_epoch,
        embedding_dimension,
        item_type
    );

CREATE OR REPLACE FUNCTION {schema}.prevent_ledger_event_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'ledger_events is append-only; attempted %', TG_OP
        USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.prevent_provenance_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'source provenance is immutable; attempted %', TG_OP
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS ledger_events_no_update ON {schema}.ledger_events;
CREATE TRIGGER ledger_events_no_update
    BEFORE UPDATE ON {schema}.ledger_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.prevent_ledger_event_mutation();

DROP TRIGGER IF EXISTS ledger_events_no_delete ON {schema}.ledger_events;
CREATE TRIGGER ledger_events_no_delete
    BEFORE DELETE ON {schema}.ledger_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.prevent_ledger_event_mutation();

DROP TRIGGER IF EXISTS source_documents_no_update ON {schema}.source_documents;
CREATE TRIGGER source_documents_no_update
    BEFORE UPDATE ON {schema}.source_documents
    FOR EACH ROW EXECUTE FUNCTION {schema}.prevent_provenance_mutation();

DROP TRIGGER IF EXISTS source_documents_no_delete ON {schema}.source_documents;
CREATE TRIGGER source_documents_no_delete
    BEFORE DELETE ON {schema}.source_documents
    FOR EACH ROW EXECUTE FUNCTION {schema}.prevent_provenance_mutation();

DROP TRIGGER IF EXISTS source_spans_no_update ON {schema}.source_spans;
CREATE TRIGGER source_spans_no_update
    BEFORE UPDATE ON {schema}.source_spans
    FOR EACH ROW EXECUTE FUNCTION {schema}.prevent_provenance_mutation();

DROP TRIGGER IF EXISTS source_spans_no_delete ON {schema}.source_spans;
CREATE TRIGGER source_spans_no_delete
    BEFORE DELETE ON {schema}.source_spans
    FOR EACH ROW EXECUTE FUNCTION {schema}.prevent_provenance_mutation();

COMMENT ON TABLE {schema}.ledger_events IS
    'Canonical append-only hosted Cortex ledger. Mutations are forbidden; corrections append new events.';

COMMENT ON TABLE {schema}.decision_nodes IS
    'Current graph projection rebuilt from ledger_events; not a source of truth.';

COMMENT ON TABLE {schema}.decision_versions IS
    'Immutable decision projection snapshots rebuilt from ledger_events and source spans.';

COMMENT ON TABLE {schema}.source_documents IS
    'Immutable source snapshots keyed by content hash so source drift does not overwrite citations.';

COMMENT ON TABLE {schema}.source_spans IS
    'Citable source excerpts derived from immutable source document snapshots.';

COMMENT ON TABLE {schema}.decision_scopes IS
    'Structural search projection rebuilt from decision_versions and ledger_events.';

COMMENT ON TABLE {schema}.retrieval_traces IS
    'Replay/debug projection recording candidate sets, scores, reasons, omitted counts, and config versions.';

COMMENT ON TABLE {schema}.embeddings IS
    'Rebuildable vector-search projection keyed by item, model, dimension, and epoch; ledger_events remains source of truth.';

INSERT INTO {schema}.schema_migrations (version)
VALUES ({HOSTED_SCHEMA_VERSION})
ON CONFLICT (version) DO NOTHING;
""".strip()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
