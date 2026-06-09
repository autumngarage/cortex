from __future__ import annotations

import pytest

from cortex.hosted.decisions_for_diff import (
    DECISIONS_FOR_DIFF_RETRIEVAL_CONFIG_VERSION,
    MAX_DECISIONS_FOR_DIFF_LIMIT,
    DecisionsForDiffCandidate,
    DecisionsForDiffQuery,
    DecisionsForDiffValidationError,
    build_decisions_for_diff_candidate_pack,
    decisions_for_diff_retrieval_sql,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
REPO_ID = "22222222-2222-4222-8222-222222222222"
SOURCE_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_DOCUMENT_ID = "44444444-4444-4444-8444-444444444444"
NODE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NODE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NODE_C = "99999999-9999-4999-8999-999999999999"
VERSION_A = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
VERSION_B = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
VERSION_C = "88888888-8888-4888-8888-888888888888"
GRAPH_HASH = "a" * 64
SPAN_HASH = "b" * 64


def _span_payload() -> dict[str, str]:
    return {
        "span_hash": SPAN_HASH,
        "excerpt": "Confirmed hosted retrieval decisions must stay cited.",
        "permalink": "https://github.com/autumngarage/cortex/pull/480/files#diff",
        "source_document_id": SOURCE_DOCUMENT_ID,
        "source_id": SOURCE_ID,
    }


def _row(
    *,
    decision_node_id: str = NODE_A,
    decision_version_id: str = VERSION_A,
    status: str = "confirmed",
    score: float = 4.2,
    reason_codes: list[str] | None = None,
    candidate_pool_size: int = 12,
    graph_node_count: int = 120,
) -> dict[str, object]:
    return {
        "candidate_pool_size": candidate_pool_size,
        "cited_spans": [_span_payload()],
        "decision_node_id": decision_node_id,
        "decision_text": "Keep hosted retrieval cited and bounded.",
        "decision_version_id": decision_version_id,
        "fused_score": score,
        "graph_node_count": graph_node_count,
        "reason_codes": reason_codes or ["scope:path:src/cortex/hosted/schema.py"],
        "status": status,
    }


def test_query_from_diff_metadata_parses_surface_and_sql_parameters() -> None:
    query = DecisionsForDiffQuery.from_diff_metadata(
        tenant_id=TENANT_ID,
        repo_id=REPO_ID,
        changed_paths=("./src//cortex\\hosted/schema.py",),
        symbols=("cortex.hosted.create_schema_sql",),
        imports=("FastAPI_Users",),
        config_keys=("CORTEX__HOSTED__URL",),
        issue_refs=("https://github.com/autumngarage/cortex/issues/465",),
        owners=("@Platform-Team",),
        services=("Hosted_API",),
        visible_source_ids=(SOURCE_ID,),
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )

    params = query.as_sql_parameters()

    assert params["tenant_id"] == TENANT_ID
    assert params["repo_id"] == REPO_ID
    assert params["visible_source_ids"] == [SOURCE_ID]
    assert params["statuses"] == ["candidate", "confirmed"]
    assert params["scope_types"] == [
        "path",
        "symbol",
        "package",
        "config_key",
        "owner",
        "service",
        "issue_ref",
    ]
    assert params["normalized_values"] == [
        "src/cortex/hosted/schema.py",
        "cortex.hosted.create_schema_sql",
        "fastapi-users",
        "cortex.hosted.url",
        "platform-team",
        "hosted-api",
        "#465",
    ]
    assert "src/cortex/hosted/schema.py" in str(params["query"])
    assert params["embedding_vector"] == [0.1, 0.2, 0.3]
    assert len(query.query_hash) == 64


def test_query_requires_surface_or_diff_text_and_caps_evaluator_budget() -> None:
    with pytest.raises(DecisionsForDiffValidationError, match="changed surface or diff_text"):
        DecisionsForDiffQuery(tenant_id=TENANT_ID)

    with pytest.raises(DecisionsForDiffValidationError, match="between 1 and"):
        DecisionsForDiffQuery(
            tenant_id=TENANT_ID,
            diff_text="touch hosted retrieval",
            limit=MAX_DECISIONS_FOR_DIFF_LIMIT + 1,
        )


def test_query_hash_changes_for_diff_text_visibility_and_embeddings() -> None:
    base = DecisionsForDiffQuery(
        tenant_id=TENANT_ID,
        diff_text="touch hosted retrieval",
        visible_source_ids=(SOURCE_ID,),
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )
    changed_text = DecisionsForDiffQuery(
        tenant_id=TENANT_ID,
        diff_text="touch reviewer retrieval",
        visible_source_ids=(SOURCE_ID,),
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )
    changed_visibility = DecisionsForDiffQuery(
        tenant_id=TENANT_ID,
        diff_text="touch hosted retrieval",
        visible_source_ids=("55555555-5555-4555-8555-555555555555",),
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )
    changed_embedding = DecisionsForDiffQuery(
        tenant_id=TENANT_ID,
        diff_text="touch hosted retrieval",
        visible_source_ids=(SOURCE_ID,),
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.4),
    )

    assert base.query_hash != changed_text.query_hash
    assert base.query_hash != changed_visibility.query_hash
    assert base.query_hash != changed_embedding.query_hash


def test_candidate_pack_from_rows_caps_candidates_and_records_growth_metrics() -> None:
    query = DecisionsForDiffQuery(tenant_id=TENANT_ID, diff_text="hosted retrieval", limit=2)
    pack = build_decisions_for_diff_candidate_pack(
        query=query,
        graph_snapshot_hash=GRAPH_HASH,
        rows=(
            _row(decision_node_id=NODE_A, decision_version_id=VERSION_A, score=5.0),
            _row(decision_node_id=NODE_B, decision_version_id=VERSION_B, score=4.0),
            _row(decision_node_id=NODE_C, decision_version_id=VERSION_C, score=3.0),
        ),
    )

    assert [candidate.decision_node_id for candidate in pack.candidates] == [NODE_A, NODE_B]
    assert pack.omitted_counts == {"over_limit": 10}
    assert pack.candidate_pool_size == 12
    assert pack.graph_node_count == 120
    assert pack.candidate_growth_ratio == pytest.approx(0.1)
    assert len(pack.candidate_set_hash) == 64


def test_candidate_pack_rejects_uncited_or_malformed_rows() -> None:
    query = DecisionsForDiffQuery(tenant_id=TENANT_ID, diff_text="hosted retrieval")

    with pytest.raises(DecisionsForDiffValidationError, match="must include citations"):
        build_decisions_for_diff_candidate_pack(
            query=query,
            graph_snapshot_hash=GRAPH_HASH,
            rows=(_row() | {"cited_spans": []},),
        )

    with pytest.raises(DecisionsForDiffValidationError, match="reason_codes must be a string array"):
        build_decisions_for_diff_candidate_pack(
            query=query,
            graph_snapshot_hash=GRAPH_HASH,
            rows=(_row() | {"reason_codes": "scope:path:src/cortex/hosted/schema.py"},),
        )

    with pytest.raises(DecisionsForDiffValidationError, match="candidate_pool_size"):
        build_decisions_for_diff_candidate_pack(
            query=query,
            graph_snapshot_hash=GRAPH_HASH,
            rows=(
                _row(decision_node_id=NODE_A, decision_version_id=VERSION_A, candidate_pool_size=1),
                _row(decision_node_id=NODE_B, decision_version_id=VERSION_B, candidate_pool_size=1),
            ),
        )


def test_candidate_trace_records_replay_fields_and_metrics() -> None:
    query = DecisionsForDiffQuery(tenant_id=TENANT_ID, diff_text="hosted retrieval", limit=1)
    pack = build_decisions_for_diff_candidate_pack(
        query=query,
        graph_snapshot_hash=GRAPH_HASH,
        rows=(
            _row(decision_node_id=NODE_A, decision_version_id=VERSION_A),
            _row(decision_node_id=NODE_B, decision_version_id=VERSION_B),
        ),
    )
    trace = pack.as_trace()
    params = trace.as_insert_parameters(tenant_id=TENANT_ID)

    assert params["query_kind"] == "decisions_for_diff"
    assert params["retrieval_config_version"] == DECISIONS_FOR_DIFF_RETRIEVAL_CONFIG_VERSION
    assert "pgvector-hnsw-v1" in str(params["retrieval_config_version"])
    assert params["query_input_hash"] == query.query_hash
    assert params["candidate_set_hash"] == pack.candidate_set_hash
    assert '"candidate_pool_size":12' in str(params["omitted_counts"])
    assert '"graph_node_count":120' in str(params["omitted_counts"])
    assert "scope:path:src/cortex/hosted/schema.py" in str(params["reason_codes"])


def test_decisions_for_diff_retrieval_sql_includes_hybrid_sources_and_metrics() -> None:
    sql = decisions_for_diff_retrieval_sql()

    assert "WITH query_scopes AS" in sql
    assert "lexical_query AS" in sql
    assert "visible_docs AS" in sql
    assert "%(visible_source_ids)s::uuid[]" in sql
    assert "version.decision_version_id = node.current_version_id" in sql
    assert "node.status = ANY(%(statuses)s::text[])" in sql
    assert "scope_candidates AS" in sql
    assert "fts_candidates AS" in sql
    assert "trigram_candidates AS" in sql
    assert "vector_candidates AS" in sql
    assert "graph_candidates AS" in sql
    assert "mentioned_with" in sql
    assert "embedding.embedding <=> %(embedding_vector)s::vector" in sql
    assert "embedding.embedding_dimension = vector_dims(%(embedding_vector)s::vector)" in sql
    assert "websearch_to_tsquery('english', query.query_text)" in sql
    assert "candidate_pool_size" in sql
    assert "graph_node_count" in sql
    assert "LIMIT %(limit)s" in sql
    assert "bounded.decision_node_id::text AS decision_node_id" in sql
    assert "bounded.decision_version_id::text AS decision_version_id" in sql


def test_decisions_for_diff_sql_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(DecisionsForDiffValidationError, match="invalid SQL identifier"):
        decisions_for_diff_retrieval_sql("bad;drop")


def test_candidate_payload_preserves_status_citations_and_text() -> None:
    candidate = DecisionsForDiffCandidate(
        decision_node_id=NODE_A,
        decision_version_id=VERSION_A,
        status="candidate",
        decision_text="Evaluate this candidate decision.",
        score=1.0,
        reason_codes=("scope:path:src/cortex/hosted/schema.py",),
        cited_spans=build_decisions_for_diff_candidate_pack(
            query=DecisionsForDiffQuery(tenant_id=TENANT_ID, diff_text="hosted retrieval"),
            graph_snapshot_hash=GRAPH_HASH,
            rows=(_row(status="candidate"),),
        ).candidates[0].cited_spans,
    )

    payload = candidate.as_context_payload()

    assert payload["status"] == "candidate"
    assert payload["decision_text"] == "Evaluate this candidate decision."
    assert payload["citations"] == [_span_payload()]
