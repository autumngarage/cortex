from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    LedgerEventValidationError,
    derive_idempotency_key,
    ledger_event_insert_sql,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
SPAN_HASH = "a" * 64
GRAPH_HASH = "b" * 64


def _actor() -> ActorRef:
    return ActorRef(actor_type="github-user", actor_id="henrymodisett")


def test_finding_event_requires_citations_snapshot_and_model_versions() -> None:
    event = LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.FINDING_EMITTED,
        actor=_actor(),
        occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key="retry-safe-key",
        payload={"finding_id": "F-1", "summary": "Prior decision contradicted"},
        source_span_hashes=(SPAN_HASH,),
        graph_snapshot_hash=GRAPH_HASH,
        model_id="model-a",
        prompt_version="review-v1",
    )

    assert event.event_hash
    assert event.as_insert_parameters()["event_hash"] == event.event_hash
    assert event.as_insert_parameters()["source_span_hashes"] == [SPAN_HASH]


def test_finding_event_fails_closed_without_source_span() -> None:
    with pytest.raises(LedgerEventValidationError, match="source span hash"):
        LedgerEvent(
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
            event_type=LedgerEventType.FINDING_EMITTED,
            actor=_actor(),
            occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
            idempotency_key="retry-safe-key",
            payload={"finding_id": "F-1"},
            graph_snapshot_hash=GRAPH_HASH,
            model_id="model-a",
            prompt_version="review-v1",
        )


def test_model_id_and_prompt_version_are_atomic() -> None:
    with pytest.raises(LedgerEventValidationError, match="provided together"):
        LedgerEvent(
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
            event_type=LedgerEventType.CANDIDATE_PROPOSED,
            actor=_actor(),
            occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
            idempotency_key="retry-safe-key",
            payload={"candidate": "Use Postgres"},
            model_id="model-a",
        )


def test_occurred_at_must_be_timezone_aware() -> None:
    with pytest.raises(LedgerEventValidationError, match="timezone-aware"):
        LedgerEvent(
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
            event_type=LedgerEventType.CANDIDATE_PROPOSED,
            actor=_actor(),
            occurred_at=datetime(2026, 6, 9, 12, 0),
            idempotency_key="retry-safe-key",
            payload={"candidate": "Use Postgres"},
        )


def test_event_hash_is_stable_across_payload_key_order() -> None:
    left = LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=_actor(),
        occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key="same-key",
        payload={"b": 2, "a": 1},
    )
    right = LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=_actor(),
        occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key="same-key",
        payload={"a": 1, "b": 2},
    )

    assert left.event_hash == right.event_hash


def test_derive_idempotency_key_is_retry_stable() -> None:
    first = derive_idempotency_key(
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        source_event_external_id="github-delivery-1",
        payload={"candidate": "Use Postgres"},
    )
    second = derive_idempotency_key(
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        source_event_external_id="github-delivery-1",
        payload={"candidate": "Use Postgres"},
    )

    assert first == second
    assert len(first) == 64


def test_insert_sql_is_idempotent_append_statement() -> None:
    sql = ledger_event_insert_sql()

    assert "INSERT INTO cortex_hosted.ledger_events" in sql
    assert "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING" in sql
    assert "RETURNING event_id, event_hash" in sql
