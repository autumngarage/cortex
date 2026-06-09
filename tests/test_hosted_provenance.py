from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType
from cortex.hosted.provenance import (
    ProvenanceValidationError,
    SourceDocument,
    SourceSpan,
    content_hash,
    source_document_insert_sql,
    source_span_insert_sql,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
SOURCE_DOCUMENT_ID = "33333333-3333-4333-8333-333333333333"
GRAPH_HASH = "b" * 64


def _document(content: str = "We decided to use Postgres.\nSQLite is a cache.\n") -> SourceDocument:
    return SourceDocument(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        document_type="repo-file",
        external_id="AGENTS.md@abc123",
        permalink="https://github.com/autumngarage/cortex/blob/abc123/AGENTS.md",
        author_ref="github:henrymodisett",
        source_timestamp=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        content=content,
        source_revision="abc123",
        visibility={"repo": "autumngarage/cortex"},
        metadata={"path": "AGENTS.md"},
    )


def test_source_document_hashes_content_and_preserves_metadata() -> None:
    document = _document()

    assert document.content_hash == content_hash(document.content)
    params = document.as_insert_parameters()
    assert params["content_hash"] == document.content_hash
    assert params["document_hash"] == document.document_hash
    assert params["source_revision"] == "abc123"
    assert params["visibility"] == '{"repo":"autumngarage/cortex"}'


def test_source_document_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ProvenanceValidationError, match="timezone-aware"):
        SourceDocument(
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
            document_type="repo-file",
            external_id="AGENTS.md@abc123",
            permalink="https://example.test/AGENTS.md",
            author_ref="github:henrymodisett",
            source_timestamp=datetime(2026, 6, 9, 12, 0),
            content="content",
        )


def test_source_span_extracts_exact_excerpt_and_stable_hash() -> None:
    document = _document()
    span = document.span(start_offset=0, end_offset=len("We decided to use Postgres."))
    same_span = document.span(start_offset=0, end_offset=len("We decided to use Postgres."))

    assert span.excerpt == "We decided to use Postgres."
    assert span.source_document_hash == document.document_hash
    assert span.span_hash == same_span.span_hash


def test_source_span_hash_changes_when_source_snapshot_changes() -> None:
    old_document = _document("We decided to use Postgres.\n")
    new_document = _document("We decided to use MySQL.\n")

    old_span = old_document.span(start_offset=0, end_offset=len("We decided to use Postgres."))
    new_span = new_document.span(start_offset=0, end_offset=len("We decided to use MySQL."))

    assert old_document.content_hash != new_document.content_hash
    assert old_document.document_hash != new_document.document_hash
    assert old_span.span_hash != new_span.span_hash


def test_source_span_rejects_invalid_offsets() -> None:
    document = _document()

    with pytest.raises(ProvenanceValidationError, match="greater than"):
        document.span(start_offset=4, end_offset=4)
    with pytest.raises(ProvenanceValidationError, match="exceeds"):
        document.span(start_offset=0, end_offset=len(document.content) + 1)


def test_direct_source_span_requires_excerpt_length_to_match_offsets() -> None:
    with pytest.raises(ProvenanceValidationError, match="excerpt length"):
        SourceSpan(
            tenant_id=TENANT_ID,
            source_document_hash="a" * 64,
            start_offset=0,
            end_offset=4,
            excerpt="too long",
            permalink="https://example.test",
        )


def test_source_span_insert_parameters_require_document_id() -> None:
    span = _document().span(start_offset=0, end_offset=len("We decided to use Postgres."))

    params = span.as_insert_parameters(source_document_id=SOURCE_DOCUMENT_ID)
    assert params["source_document_id"] == SOURCE_DOCUMENT_ID
    assert params["source_document_hash"] == span.source_document_hash
    assert params["span_hash"] == span.span_hash


def test_source_span_hashes_can_cite_ledger_events() -> None:
    span = _document().span(start_offset=0, end_offset=len("We decided to use Postgres."))

    event = LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.DECISION_CONFIRMED,
        actor=ActorRef(actor_type="github-user", actor_id="henrymodisett"),
        occurred_at=datetime(2026, 6, 9, 12, 5, tzinfo=UTC),
        idempotency_key="confirm-postgres",
        payload={"decision": "Use Postgres as canonical hosted store"},
        source_span_hashes=(span.span_hash,),
        graph_snapshot_hash=GRAPH_HASH,
    )

    assert event.source_span_hashes == (span.span_hash,)


def test_source_document_insert_sql_is_idempotent_without_mutating_snapshot() -> None:
    sql = source_document_insert_sql()

    assert "ON CONFLICT (tenant_id, source_id, external_id, content_hash) DO NOTHING" in sql
    assert "RETURNING source_document_id, content_hash, document_hash" in sql
    assert "UNION ALL" in sql
    assert "UPDATE" not in sql


def test_source_span_insert_sql_is_idempotent_without_mutating_span() -> None:
    sql = source_span_insert_sql()

    assert "ON CONFLICT (tenant_id, span_hash) DO NOTHING" in sql
    assert "RETURNING source_span_id, source_document_hash, span_hash" in sql
    assert "UNION ALL" in sql
    assert "UPDATE" not in sql


def test_source_span_rejects_invalid_document_hash() -> None:
    with pytest.raises(ProvenanceValidationError, match="sha256"):
        SourceSpan(
            tenant_id=TENANT_ID,
            source_document_hash="not-a-hash",
            start_offset=0,
            end_offset=1,
            excerpt="x",
            permalink="https://example.test",
        )
