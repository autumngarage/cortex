from __future__ import annotations

import pytest

from cortex.hosted.ask_ledger import (
    ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
    SOURCE_WEIGHTS,
    AnswerState,
    AskLedgerCandidate,
    AskLedgerQuery,
    AskLedgerValidationError,
    CandidateSource,
    CitedContextPack,
    CitedSourceSpan,
    SourceRank,
    ask_ledger_retrieval_sql,
    build_ask_ledger_context_pack,
    build_cited_context_pack,
    reciprocal_rank_fusion,
    retrieval_trace_insert_sql,
)
from cortex.hosted.scopes import ChangedSurface

TENANT_ID = "11111111-1111-4111-8111-111111111111"
REPO_ID = "22222222-2222-4222-8222-222222222222"
SOURCE_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_DOCUMENT_ID = "44444444-4444-4444-8444-444444444444"
NODE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NODE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
VERSION_A = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
VERSION_B = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
GRAPH_HASH = "a" * 64
SPAN_HASH = "b" * 64


def _span() -> CitedSourceSpan:
    return CitedSourceSpan(
        span_hash=SPAN_HASH,
        excerpt="Use Postgres as the canonical hosted Cortex store.",
        permalink="https://github.com/autumngarage/cortex/blob/main/docs/hosted-ledger.md#L1",
        source_document_id=SOURCE_DOCUMENT_ID,
        source_id=SOURCE_ID,
    )


def _candidate(
    *,
    decision_node_id: str = NODE_A,
    decision_version_id: str = VERSION_A,
    score: float = 2.0,
    cited: bool = True,
) -> AskLedgerCandidate:
    return AskLedgerCandidate(
        decision_node_id=decision_node_id,
        decision_version_id=decision_version_id,
        decision_text="Use Postgres as the canonical hosted Cortex store.",
        score=score,
        reason_codes=("scope:config_key:cortex.hosted.url", "full_text:decision_text"),
        cited_spans=(_span(),) if cited else (),
    )


def test_ask_ledger_query_parameters_include_scope_visibility_and_vector_inputs() -> None:
    scopes = ChangedSurface(config_keys=("CORTEX__HOSTED__URL",)).query_scopes()
    query = AskLedgerQuery(
        tenant_id=TENANT_ID,
        repo_id=REPO_ID,
        query="what did we decide about hosted storage?",
        query_scopes=scopes,
        visible_source_ids=(SOURCE_ID,),
        repo_installation_id="install-123",
        exact_refs=("docs/hosted-ledger.md",),
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )

    params = query.as_sql_parameters()

    assert params["tenant_id"] == TENANT_ID
    assert params["repo_id"] == REPO_ID
    assert params["visible_source_ids"] == [SOURCE_ID]
    assert params["repo_installation_id"] == "install-123"
    assert params["exact_refs"] == ["docs/hosted-ledger.md"]
    assert params["scope_types"] == ["config_key"]
    assert params["normalized_values"] == ["cortex.hosted.url"]
    assert params["reason_codes"] == ["scope:config_key:cortex.hosted.url"]
    assert params["embedding_vector"] == [0.1, 0.2, 0.3]
    assert len(query.query_hash) == 64


def test_ask_ledger_embedding_inputs_are_atomic() -> None:
    with pytest.raises(AskLedgerValidationError, match="provided together"):
        AskLedgerQuery(
            tenant_id=TENANT_ID,
            query="hosted storage",
            visible_source_ids=(SOURCE_ID,),
            embedding_model_id="text-embedding-3-small",
        )


def test_ask_ledger_query_requires_explicit_visible_sources() -> None:
    with pytest.raises(AskLedgerValidationError, match="authorized source"):
        AskLedgerQuery(tenant_id=TENANT_ID, query="hosted storage")


def test_query_hash_includes_visibility_limit_config_and_embedding_inputs() -> None:
    base = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
        limit=3,
        retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )
    changed_visibility = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=("55555555-5555-4555-8555-555555555555",),
        limit=3,
        retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )
    changed_limit = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
        limit=10,
        retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.3),
    )
    changed_embedding = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
        limit=3,
        retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
        embedding_model_id="text-embedding-3-small",
        embedding_epoch="2026-06-09",
        embedding_vector=(0.1, 0.2, 0.4),
    )

    assert base.query_hash != changed_visibility.query_hash
    assert base.query_hash != changed_limit.query_hash
    assert base.query_hash != changed_embedding.query_hash


def test_rank_fusion_keeps_reason_codes_and_weights_exact_above_vector() -> None:
    fused = reciprocal_rank_fusion(
        (
            SourceRank(NODE_A, CandidateSource.VECTOR, 1, "vector:decision_embedding"),
            SourceRank(NODE_B, CandidateSource.EXACT, 1, "exact:ref"),
        )
    )

    assert SOURCE_WEIGHTS[CandidateSource.EXACT] > SOURCE_WEIGHTS[CandidateSource.VECTOR]
    assert fused[0].decision_node_id == NODE_B
    assert fused[0].reason_codes == ("exact:ref",)
    assert fused[1].reason_codes == ("vector:decision_embedding",)


def test_context_pack_returns_cited_candidates_and_omitted_counts() -> None:
    query = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
    )
    pack = build_cited_context_pack(
        query_hash=query.query_hash,
        retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
        graph_snapshot_hash=GRAPH_HASH,
        candidates=(
            _candidate(decision_node_id=NODE_A, decision_version_id=VERSION_A, score=10.0),
            _candidate(decision_node_id=NODE_B, decision_version_id=VERSION_B, score=9.0),
            _candidate(decision_node_id=NODE_B, decision_version_id=VERSION_B, score=8.0, cited=False),
        ),
        limit=1,
    )

    assert pack.answer_state is AnswerState.READY
    assert [candidate.decision_node_id for candidate in pack.candidates] == [NODE_A]
    assert pack.omitted_counts == {"missing_citations": 1, "over_limit": 1}
    assert len(pack.candidate_set_hash) == 64


def test_ask_ledger_context_pack_from_rows_returns_bounded_cited_context() -> None:
    query = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
        limit=1,
    )
    pack = build_ask_ledger_context_pack(
        query=query,
        graph_snapshot_hash=GRAPH_HASH,
        rows=(
            {
                "decision_node_id": NODE_A,
                "decision_version_id": VERSION_A,
                "decision_text": "Use Postgres as the canonical hosted Cortex store.",
                "fused_score": 4.2,
                "reason_codes": ["full_text:decision_text"],
                "cited_spans": [_span().as_payload()],
            },
            {
                "decision_node_id": NODE_B,
                "decision_version_id": VERSION_B,
                "decision_text": "Keep hosted retrieval cited.",
                "fused_score": 3.1,
                "reason_codes": ["trigram:decision_text"],
                "cited_spans": [_span().as_payload()],
            },
        ),
    )

    assert pack.answer_state is AnswerState.READY
    assert [candidate.decision_node_id for candidate in pack.candidates] == [NODE_A]
    assert pack.omitted_counts == {"missing_citations": 0, "over_limit": 1}


def test_ask_ledger_context_pack_from_rows_rejects_malformed_payloads() -> None:
    query = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
    )

    with pytest.raises(AskLedgerValidationError, match="reason_codes must be a string array"):
        build_ask_ledger_context_pack(
            query=query,
            graph_snapshot_hash=GRAPH_HASH,
            rows=(
                {
                    "decision_node_id": NODE_A,
                    "decision_version_id": VERSION_A,
                    "decision_text": "Use Postgres as the canonical hosted Cortex store.",
                    "fused_score": 4.2,
                    "reason_codes": "full_text:decision_text",
                    "cited_spans": [_span().as_payload()],
                },
            ),
        )


def test_context_pack_fails_closed_when_no_cited_support_exists() -> None:
    query = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
    )
    pack = build_cited_context_pack(
        query_hash=query.query_hash,
        retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
        graph_snapshot_hash=GRAPH_HASH,
        candidates=(_candidate(cited=False),),
        limit=3,
    )

    assert pack.answer_state is AnswerState.NO_ANSWER
    assert pack.no_answer_reason == "no_cited_support"
    assert pack.candidates == ()
    assert pack.omitted_counts == {"missing_citations": 1, "over_limit": 0}


def test_ready_context_pack_rejects_uncited_candidates() -> None:
    query = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
    )

    with pytest.raises(AskLedgerValidationError, match="must include citations"):
        CitedContextPack(
            query_hash=query.query_hash,
            retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
            graph_snapshot_hash=GRAPH_HASH,
            candidates=(_candidate(cited=False),),
        )


def test_retrieval_trace_records_candidates_scores_reasons_and_versions() -> None:
    query = AskLedgerQuery(
        tenant_id=TENANT_ID,
        query="hosted storage",
        visible_source_ids=(SOURCE_ID,),
    )
    pack = build_cited_context_pack(
        query_hash=query.query_hash,
        retrieval_config_version=ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
        graph_snapshot_hash=GRAPH_HASH,
        candidates=(_candidate(),),
        limit=3,
    )
    trace = pack.as_trace()
    params = trace.as_insert_parameters(tenant_id=TENANT_ID)

    assert params["retrieval_config_version"] == ASK_LEDGER_RETRIEVAL_CONFIG_VERSION
    assert "pgvector-hnsw-v1" in str(params["retrieval_config_version"])
    assert params["query_kind"] == "ask_ledger"
    assert params["query_input_hash"] == query.query_hash
    assert params["candidate_set_hash"] == pack.candidate_set_hash
    assert '"score":2.0' in str(params["candidates"])
    assert "scope:config_key:cortex.hosted.url" in str(params["reason_codes"])


def test_ask_ledger_retrieval_sql_includes_hybrid_sources_visibility_and_citations() -> None:
    sql = ask_ledger_retrieval_sql()

    assert "WITH query_scopes AS" in sql
    assert "visible_docs AS" in sql
    assert "%(visible_source_ids)s::uuid[]" in sql
    assert "%(visible_source_ids)s::uuid[] IS NULL" not in sql
    assert "%(repo_installation_id)s::text" in sql
    assert "slack_channel_excluded" in sql
    assert "revoked" in sql
    assert "deleted" in sql
    assert "version.decision_version_id = node.current_version_id" in sql
    assert "base_versions AS" in sql
    assert "JOIN visible_docs AS visible_doc" in sql
    assert "exact_candidates AS" in sql
    assert "scope_candidates AS" in sql
    assert "fts_candidates AS" in sql
    assert "trigram_candidates AS" in sql
    assert "vector_candidates AS" in sql
    assert "graph_candidates AS" in sql
    assert "cited_fused AS" in sql
    assert "WHERE EXISTS (" in sql
    assert "embedding.embedding <=> %(embedding_vector)s::vector" in sql
    assert "embedding.embedding_dimension = vector_dims(%(embedding_vector)s::vector)" in sql
    assert "websearch_to_tsquery('english', %(query)s)" in sql
    assert "similarity(version.decision_text, %(query)s)" in sql
    assert "jsonb_agg(" in sql
    assert "source_document_id" in sql
    assert "source_id" in sql
    assert "bounded.decision_node_id::text AS decision_node_id" in sql
    assert "bounded.decision_version_id::text AS decision_version_id" in sql


def test_retrieval_trace_insert_sql_persists_replay_fields() -> None:
    sql = retrieval_trace_insert_sql()

    assert "INSERT INTO cortex_hosted.retrieval_traces" in sql
    assert "graph_snapshot_hash" in sql
    assert "retrieval_config_version" in sql
    assert "candidate_set_hash" in sql
    assert "omitted_counts" in sql
    assert "reason_codes" in sql
    assert "RETURNING retrieval_trace_id" in sql


def test_ask_ledger_sql_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(AskLedgerValidationError, match="invalid SQL identifier"):
        ask_ledger_retrieval_sql("bad;drop")
