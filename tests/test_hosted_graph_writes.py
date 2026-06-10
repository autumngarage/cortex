"""Tests for the immutable-with-supersede write plans (cortex#314, #487)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from cortex.hosted.graph_writes import (
    MERGE_DUPLICATE_EDGE_TYPE,
    SUPERSEDE_EDGE_TYPE,
    GraphWritePlan,
    GraphWriteValidationError,
    plan_candidate_proposed,
    plan_status_transition,
    plan_supersede,
)
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType

TENANT = "0b6f9f3e-3a2f-4f2e-9c8d-1a2b3c4d5e6f"
SOURCE = "1c7f8e2d-4b3a-4c5d-8e9f-2b3c4d5e6f70"
NODE_A = "3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81"
NODE_B = "4e0f9a8b-6c5d-4e7f-a091-4d5e6f7a8b92"
VERSION_1 = "5f1a0b9c-7d6e-4f80-b1a2-5e6f7a8b9c03"

SPAN = hashlib.sha256(b"span").hexdigest()
GRAPH = hashlib.sha256(b"graph").hexdigest()


def _event(
    event_type: LedgerEventType, payload: dict[str, object] | None = None
) -> LedgerEvent:
    needs_span = event_type in {
        LedgerEventType.CANDIDATE_PROPOSED,
        LedgerEventType.DECISION_CONFIRMED,
        LedgerEventType.DECISION_SUPERSEDED,
    }
    return LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=event_type,
        actor=ActorRef(actor_type="human", actor_id="henry"),
        occurred_at=datetime(2026, 6, 9, 22, 0, tzinfo=UTC),
        idempotency_key=f"key-{event_type.value}",
        payload=payload or {},
        source_span_hashes=(SPAN,) if needs_span else (),
    )


def test_candidate_plan_shape_and_order() -> None:
    plan = plan_candidate_proposed(
        _event(LedgerEventType.CANDIDATE_PROPOSED),
        decision_node_id=NODE_A,
        decision_version_id=VERSION_1,
        decision_text="Webhook retries use exponential backoff.",
        confidence="suggest",
        scopes=(("path", "src/api/client.py", "src/api/client.py"),),
    )
    purposes = [s.purpose for s in plan.statements]
    assert purposes == [
        "append-ledger-event",
        "insert-decision-node",
        "insert-decision-version",
        "insert-decision-scope:path",
    ]
    node_stmt = plan.statements[1]
    assert "'candidate'" in node_stmt.sql
    version_stmt = plan.statements[2]
    assert version_stmt.parameters["source_span_hashes"] == [SPAN]


def test_status_transition_plans_match_event_types() -> None:
    for event_type, status in (
        (LedgerEventType.DECISION_CONFIRMED, "confirmed"),
        (LedgerEventType.DECISION_REJECTED, "rejected"),
        (LedgerEventType.STALE_MARKED, "stale"),
    ):
        plan = plan_status_transition(
            _event(event_type), decision_node_id=NODE_A, new_status=status
        )
        assert plan.statements[0].purpose == "append-ledger-event"
        assert plan.statements[1].purpose == f"project-node-status:{status}"

    with pytest.raises(GraphWriteValidationError, match="must set status"):
        plan_status_transition(
            _event(LedgerEventType.DECISION_CONFIRMED),
            decision_node_id=NODE_A,
            new_status="stale",
        )
    with pytest.raises(GraphWriteValidationError, match="not a status-transition"):
        plan_status_transition(
            _event(LedgerEventType.CANDIDATE_PROPOSED),
            decision_node_id=NODE_A,
            new_status="confirmed",
        )


def test_supersede_plan_adds_edge_and_status() -> None:
    plan = plan_supersede(
        _event(LedgerEventType.DECISION_SUPERSEDED),
        superseded_node_id=NODE_A,
        superseding_node_id=NODE_B,
    )
    purposes = [s.purpose for s in plan.statements]
    assert purposes == [
        "append-ledger-event",
        "project-node-status:superseded",
        "insert-supersedes-edge",
    ]
    edge = plan.statements[2]
    assert edge.parameters["from_node_id"] == NODE_B
    assert edge.parameters["to_node_id"] == NODE_A
    assert edge.parameters["edge_type"] == SUPERSEDE_EDGE_TYPE


def test_merge_is_a_supersede_pair_with_duplicates_edge() -> None:
    """The cortex#487 decision: merge = supersede + duplicates edge."""

    event = _event(
        LedgerEventType.DECISION_SUPERSEDED,
        payload={"merge": True, "merged_into": NODE_B},
    )
    plan = plan_supersede(
        event, superseded_node_id=NODE_A, superseding_node_id=NODE_B, merge=True
    )
    assert [s.purpose for s in plan.statements][-1] == "insert-merge-duplicates-edge"
    dup = plan.statements[-1]
    assert dup.parameters["from_node_id"] == NODE_A
    assert dup.parameters["to_node_id"] == NODE_B
    assert dup.parameters["edge_type"] == MERGE_DUPLICATE_EDGE_TYPE


def test_merge_without_payload_markers_is_rejected() -> None:
    with pytest.raises(GraphWriteValidationError, match="merge=true"):
        plan_supersede(
            _event(LedgerEventType.DECISION_SUPERSEDED),
            superseded_node_id=NODE_A,
            superseding_node_id=NODE_B,
            merge=True,
        )
    with pytest.raises(GraphWriteValidationError, match="merged_into"):
        plan_supersede(
            _event(LedgerEventType.DECISION_SUPERSEDED, payload={"merge": True}),
            superseded_node_id=NODE_A,
            superseding_node_id=NODE_B,
            merge=True,
        )


def test_self_supersede_is_rejected() -> None:
    with pytest.raises(GraphWriteValidationError, match="cannot supersede itself"):
        plan_supersede(
            _event(LedgerEventType.DECISION_SUPERSEDED),
            superseded_node_id=NODE_A,
            superseding_node_id=NODE_A,
        )


def _all_plans() -> list[GraphWritePlan]:
    return [
        plan_candidate_proposed(
            _event(LedgerEventType.CANDIDATE_PROPOSED),
            decision_node_id=NODE_A,
            decision_version_id=VERSION_1,
            decision_text="text",
            confidence="suggest",
            scopes=(("path", "a.py", "a.py"),),
        ),
        plan_status_transition(
            _event(LedgerEventType.DECISION_CONFIRMED),
            decision_node_id=NODE_A,
            new_status="confirmed",
        ),
        plan_supersede(
            _event(
                LedgerEventType.DECISION_SUPERSEDED,
                payload={"merge": True, "merged_into": NODE_B},
            ),
            superseded_node_id=NODE_A,
            superseding_node_id=NODE_B,
            merge=True,
        ),
    ]


def test_invariant_every_plan_starts_with_ledger_append() -> None:
    for plan in _all_plans():
        assert plan.statements[0].purpose == "append-ledger-event"
        assert "INSERT INTO cortex_hosted.ledger_events" in plan.statements[0].sql
        assert "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING" in plan.statements[0].sql


def test_invariant_no_plan_mutates_immutable_tables() -> None:
    """Append-only surfaces never see UPDATE/DELETE from any planner."""

    for plan in _all_plans():
        for statement in plan.statements:
            sql = statement.sql.upper()
            for table in ("LEDGER_EVENTS", "SOURCE_DOCUMENTS", "SOURCE_SPANS"):
                qualified = f"CORTEX_HOSTED.{table}"
                for verb in ("UPDATE", "DELETE FROM"):
                    assert f"{verb} {qualified}" not in sql, (
                        f"{statement.purpose} would {verb} {table} — append-only violation"
                    )


def test_projection_updates_reference_the_appended_event() -> None:
    """Every projection statement resolves the event by idempotency key,
    so a rolled-back append can never leave dangling projection writes."""

    for plan in _all_plans():
        for statement in plan.statements[1:]:
            assert "idempotency_key" in statement.parameters, statement.purpose
            assert "ledger_events" in statement.sql, statement.purpose
