from __future__ import annotations

import pytest

from cortex.hosted.embeddings import (
    DEFAULT_HNSW_EF_SEARCH,
    DEFAULT_HNSW_MIN_ROWS,
    DEFAULT_VECTOR_INDEX_CONFIG,
    DEFAULT_VECTOR_RECALL_FLOOR,
    HOSTED_VECTOR_INDEX_CONFIG_VERSION,
    EmbeddingItemType,
    EmbeddingProjectionRow,
    HostedEmbeddingValidationError,
    VectorIndexConfig,
    VectorRecallSample,
    approximate_vector_search_sql,
    embedding_delete_orphans_sql,
    embedding_hnsw_index_sql,
    embedding_projection_counts_sql,
    embedding_projection_source_sql,
    embedding_upsert_sql,
    evaluate_vector_recall,
    exact_vector_search_sql,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
REPO_ID = "22222222-2222-4222-8222-222222222222"
ITEM_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
OTHER_ITEM_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
THIRD_ITEM_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
ITEM_HASH = "a" * 64


def test_embedding_projection_row_is_dimension_keyed_and_replayable() -> None:
    row = EmbeddingProjectionRow(
        tenant_id=TENANT_ID,
        repo_id=REPO_ID,
        item_type=EmbeddingItemType.DECISION_VERSION,
        item_id=ITEM_ID,
        item_hash=ITEM_HASH,
        text="Use cited hosted decision versions as embedding inputs.",
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
        metadata={"source": "test"},
    )

    params = row.as_upsert_parameters()

    assert row.embedding_dimension == 3
    assert len(row.projection_hash) == 64
    assert params["item_type"] == "decision_version"
    assert params["embedding_dimension"] == 3
    assert params["embedding_vector"] == [0.1, 0.2, 0.3]
    assert params["metadata"] == '{"source":"test"}'


def test_embedding_projection_row_rejects_non_rebuildable_inputs() -> None:
    with pytest.raises(HostedEmbeddingValidationError, match="embedding_vector"):
        EmbeddingProjectionRow(
            tenant_id=TENANT_ID,
            item_type=EmbeddingItemType.SOURCE_SPAN,
            item_id=ITEM_ID,
            item_hash=ITEM_HASH,
            text="Source span excerpt.",
            embedding_model_id="text-embedding-3-small",
            embedding_epoch="2026-06-09",
            embedding_vector=(),
        )

    with pytest.raises(HostedEmbeddingValidationError, match="sha256"):
        EmbeddingProjectionRow(
            tenant_id=TENANT_ID,
            item_type=EmbeddingItemType.SOURCE_SPAN,
            item_id=ITEM_ID,
            item_hash="not-a-hash",
            text="Source span excerpt.",
            embedding_model_id="text-embedding-3-small",
            embedding_epoch="2026-06-09",
            embedding_vector=(0.1,),
        )


def test_embedding_projection_source_sql_rebuilds_from_versions_and_source_spans() -> None:
    sql = embedding_projection_source_sql()

    assert "visible_sources AS" in sql
    assert "visible_docs AS" in sql
    assert "source.source_id = ANY(%(visible_source_ids)s::uuid[])" in sql
    assert "%(visible_source_ids)s::uuid[] IS NULL" not in sql
    assert "%(repo_installation_id)s::text" in sql
    assert "slack_channel_excluded" in sql
    assert "revoked" in sql
    assert "deleted" in sql
    assert "projection_sources AS" in sql
    assert "'decision_version'::text AS item_type" in sql
    assert "version.decision_text AS text" in sql
    assert "JOIN visible_docs AS visible_doc" in sql
    assert "'source_span'::text AS item_type" in sql
    assert "span.excerpt AS text" in sql
    assert "source.repo_id" in sql
    assert "embedding_dimension = %(embedding_dimension)s::integer" in sql
    assert "embedding.item_hash <> source.item_hash" in sql
    assert "%(item_types)s::text[]" in sql


def test_embedding_projection_counts_and_orphan_delete_sql_are_source_of_truth_based() -> None:
    counts_sql = embedding_projection_counts_sql()
    delete_sql = embedding_delete_orphans_sql()

    assert "missing_count" in counts_sql
    assert "stale_count" in counts_sql
    assert "orphan_count" in counts_sql
    assert "filtered_sources" in counts_sql
    assert "target_embeddings" in counts_sql
    assert "visible_sources AS" not in counts_sql
    assert "DELETE FROM cortex_hosted.embeddings AS embedding" in delete_sql
    assert "NOT EXISTS (" in delete_sql
    assert "projection_sources AS" in delete_sql
    assert "visible_sources AS" not in delete_sql


def test_embedding_upsert_sql_keys_projection_by_dimension() -> None:
    sql = embedding_upsert_sql()

    assert "INSERT INTO cortex_hosted.embeddings" in sql
    assert "embedding_dimension" in sql
    assert "%(embedding_vector)s::vector" in sql
    assert "ON CONFLICT ON CONSTRAINT embeddings_projection_unique" in sql
    assert "RETURNING" in sql


def test_hnsw_index_sql_uses_versioned_threshold_and_settings() -> None:
    config = VectorIndexConfig(
        min_rows_for_hnsw=2_500,
        hnsw_m=32,
        hnsw_ef_construction=128,
        hnsw_ef_search=80,
    )
    sql = embedding_hnsw_index_sql(embedding_dimension=3, config=config)

    assert "embedding_row_count >= 2500" in sql
    assert "embedding_dimension = 3" in sql
    assert "embeddings_vector_cosine_hnsw_dim_3_idx" in sql
    assert "USING hnsw ((embedding::vector(3)) vector_cosine_ops)" in sql
    assert "WITH (m = 32, ef_construction = 128)" in sql
    assert "efs80" in config.config_version
    assert DEFAULT_VECTOR_INDEX_CONFIG.config_version == HOSTED_VECTOR_INDEX_CONFIG_VERSION
    assert f"min{DEFAULT_HNSW_MIN_ROWS}" in HOSTED_VECTOR_INDEX_CONFIG_VERSION


def test_vector_search_sql_separates_exact_baseline_from_approximate_search() -> None:
    exact = exact_vector_search_sql(embedding_dimension=3)
    approximate = approximate_vector_search_sql(embedding_dimension=3)

    assert "set_config('enable_indexscan', 'off', true)" in exact
    assert "set_config('enable_bitmapscan', 'off', true)" in exact
    assert "hnsw.ef_search" not in exact
    assert f"set_config('hnsw.ef_search', '{DEFAULT_HNSW_EF_SEARCH}', true)" in approximate
    assert "SET LOCAL" not in exact
    assert "SET LOCAL" not in approximate
    assert exact.count(";") == 1
    assert approximate.count(";") == 1
    assert "CROSS JOIN cortex_hosted.embeddings AS embedding" in approximate
    assert "embedding.embedding::vector(3) <=> %(embedding_vector)s::vector(3)" in approximate
    assert "embedding.embedding_dimension = 3" in approximate
    assert "ORDER BY distance ASC" in approximate


def test_vector_recall_report_detects_approximate_regression_and_records_settings() -> None:
    good = VectorRecallSample(
        sample_id="query:hosted-storage",
        exact_item_ids=(ITEM_ID, OTHER_ITEM_ID),
        approximate_item_ids=(ITEM_ID, OTHER_ITEM_ID),
        k=2,
    )
    bad = VectorRecallSample(
        sample_id="query:visibility",
        exact_item_ids=(ITEM_ID, OTHER_ITEM_ID),
        approximate_item_ids=(ITEM_ID, THIRD_ITEM_ID),
        k=2,
    )

    report = evaluate_vector_recall(
        retrieval_config_version="ask-ledger-test",
        samples=(good, bad),
        minimum_required_recall=DEFAULT_VECTOR_RECALL_FLOOR,
    )
    payload = report.as_trace_payload()

    assert report.average_recall == pytest.approx(0.75)
    assert report.worst_recall == pytest.approx(0.5)
    assert not report.passed
    assert [sample.sample_id for sample in report.failed_samples] == ["query:visibility"]
    assert payload["vector_index_config"] == DEFAULT_VECTOR_INDEX_CONFIG.as_trace_payload()
    assert "pgvector-hnsw-v1" in str(payload["vector_index_config"])


def test_vector_recall_sample_rejects_samples_without_exact_baseline() -> None:
    with pytest.raises(HostedEmbeddingValidationError, match="exact_item_ids"):
        VectorRecallSample(
            sample_id="query:empty",
            exact_item_ids=(),
            approximate_item_ids=(),
            k=10,
        )


def test_embedding_sql_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(HostedEmbeddingValidationError, match="invalid SQL identifier"):
        embedding_projection_source_sql("bad;drop")
