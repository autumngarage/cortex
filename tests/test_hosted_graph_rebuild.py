"""Tests for deterministic projection rebuild from the event log (cortex#320)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.graph_rebuild import (
    REPLAY_REQUIRED_PAYLOAD_KEYS,
    GraphRebuildError,
    RebuiltGraph,
    draft_projection_rebuilt_event,
    replay_events,
)
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType

TENANT = "00000000-0000-4000-8000-000000000001"
OTHER_TENANT = "00000000-0000-4000-8000-000000000002"
SOURCE = "00000000-0000-4000-8000-000000000003"
REPO = "00000000-0000-4000-8000-000000000004"
NODE_A = "11111111-1111-4111-8111-111111111111"
NODE_B = "22222222-2222-4222-8222-222222222222"
VERSION_A = "33333333-3333-4333-8333-333333333333"
VERSION_B = "44444444-4444-4444-8444-444444444444"
SPAN_A = "a" * 64
SPAN_B = "b" * 64
SNAPSHOT_PROBE = "c" * 64

T0 = datetime(2026, 6, 10, 14, 0, tzinfo=UTC)

# The byte-level empty-graph contract pinned in test_hosted_graph_snapshot.py:
# an empty replay must hash to exactly the same constant, or rebuild and
# snapshot serializations have drifted apart.
EMPTY_GRAPH_HASH = "b57c7da4a0477e6429d0059349af620ce96643f50bd81b13428f28a834332b4f"

_ACTOR = ActorRef(actor_type="rebuild", actor_id="test")


def _candidate(
    *,
    node_id: str = NODE_A,
    version_id: str = VERSION_A,
    text: str = "Use SQLite for the local cache",
    confidence: str = "medium",
    occurred_at: datetime = T0,
    spans: tuple[str, ...] = (SPAN_A,),
    scopes: tuple[dict[str, str], ...] = (),
    repo_id: str | None = None,
    key: str | None = None,
    tenant_id: str = TENANT,
) -> LedgerEvent:
    payload: dict[str, Any] = {
        "confidence": confidence,
        "decision_node_id": node_id,
        "decision_text": text,
        "decision_version_id": version_id,
    }
    if scopes:
        payload["proposed_scopes"] = list(scopes)
    if repo_id is not None:
        payload["repo_id"] = repo_id
    return LedgerEvent(
        tenant_id=tenant_id,
        source_id=SOURCE,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=_ACTOR,
        occurred_at=occurred_at,
        idempotency_key=key or f"candidate:{node_id}:{version_id}",
        payload=payload,
        source_span_hashes=spans,
    )


def _status(
    event_type: LedgerEventType,
    *,
    node_id: str = NODE_A,
    occurred_at: datetime,
    key: str,
    spans: tuple[str, ...] = (SPAN_A,),
) -> LedgerEvent:
    return LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=event_type,
        actor=_ACTOR,
        occurred_at=occurred_at,
        idempotency_key=key,
        payload={"decision_node_id": node_id},
        source_span_hashes=spans,
    )


def _supersede(
    *,
    superseded: str = NODE_A,
    superseding: str = NODE_B,
    occurred_at: datetime,
    key: str,
    merge: bool = False,
    payload_extra: dict[str, Any] | None = None,
) -> LedgerEvent:
    payload: dict[str, Any] = {
        "superseded_node_id": superseded,
        "superseding_node_id": superseding,
    }
    if merge:
        payload["merge"] = True
        payload["merged_into"] = superseding
    if payload_extra:
        payload.update(payload_extra)
    return LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=LedgerEventType.DECISION_SUPERSEDED,
        actor=_ACTOR,
        occurred_at=occurred_at,
        idempotency_key=key,
        payload=payload,
        source_span_hashes=(SPAN_A,),
    )


def _two_node_history() -> list[LedgerEvent]:
    return [
        _candidate(node_id=NODE_A, version_id=VERSION_A, occurred_at=T0),
        _candidate(
            node_id=NODE_B,
            version_id=VERSION_B,
            text="Use Postgres for the hosted ledger",
            occurred_at=T0 + timedelta(minutes=5),
            spans=(SPAN_B,),
        ),
        _status(
            LedgerEventType.DECISION_CONFIRMED,
            node_id=NODE_B,
            occurred_at=T0 + timedelta(minutes=10),
            key="confirm:node-b",
        ),
        _supersede(occurred_at=T0 + timedelta(minutes=20), key="supersede:a-by-b"),
    ]


def test_empty_replay_matches_pinned_empty_graph_hash() -> None:
    graph = replay_events([])
    assert graph == RebuiltGraph(tenant_id=None)
    computed = graph.snapshot_hash(schema_version=1, retrieval_config_version="retrieval-v1")
    assert computed == EMPTY_GRAPH_HASH


def test_single_candidate_builds_projection_rows() -> None:
    scope = {"scope_type": "path", "value": "src/cache.py", "normalized_value": "src/cache.py"}
    graph = replay_events([_candidate(scopes=(scope,), repo_id=REPO)])
    assert graph.tenant_id == TENANT
    assert graph.applied_events == 1
    (node,) = graph.nodes
    assert node.status == "candidate"
    assert node.confidence == "medium"
    assert node.current_version_id == VERSION_A
    assert node.repo_id == REPO
    (version,) = graph.versions
    assert version.decision_node_id == NODE_A
    assert version.decision_text == "Use SQLite for the local cache"
    assert version.source_span_hashes == (SPAN_A,)
    assert version.decided_at == T0
    (scope_row,) = graph.scopes
    assert scope_row.scope_type == "path"
    assert scope_row.normalized_value == "src/cache.py"
    assert graph.edges == ()


def test_replay_is_deterministic_under_shuffle() -> None:
    events = _two_node_history()
    baseline = replay_events(events)
    baseline_hash = baseline.snapshot_hash(
        schema_version=1, retrieval_config_version="retrieval-v1"
    )
    rng = random.Random(0xC02)
    for _ in range(10):
        shuffled = list(events)
        rng.shuffle(shuffled)
        rebuilt = replay_events(shuffled)
        assert rebuilt == baseline
        assert (
            rebuilt.snapshot_hash(schema_version=1, retrieval_config_version="retrieval-v1")
            == baseline_hash
        )


def test_refold_is_idempotent() -> None:
    events = _two_node_history()
    first = replay_events(events)
    second = replay_events(events)
    assert first == second
    assert first.snapshot_hash(
        schema_version=1, retrieval_config_version="retrieval-v1"
    ) == second.snapshot_hash(schema_version=1, retrieval_config_version="retrieval-v1")


def test_replaying_drafted_rebuilt_event_is_noop() -> None:
    events = _two_node_history()
    graph = replay_events(events)
    rebuilt_event = draft_projection_rebuilt_event(
        graph,
        tenant_id=TENANT,
        source_id=SOURCE,
        actor=_ACTOR,
        occurred_at=T0 + timedelta(hours=1),
        schema_version=1,
        retrieval_config_version="retrieval-v1",
    )
    again = replay_events([*events, rebuilt_event])
    assert again.nodes == graph.nodes
    assert again.versions == graph.versions
    assert again.edges == graph.edges
    assert again.scopes == graph.scopes
    assert again.noop_events == graph.noop_events + 1
    assert again.snapshot_hash(
        schema_version=1, retrieval_config_version="retrieval-v1"
    ) == graph.snapshot_hash(schema_version=1, retrieval_config_version="retrieval-v1")


def test_supersede_ordering_follows_occurred_at_not_arrival() -> None:
    """cortex#313: a redelivery-reordered confirm cannot flip a later supersede."""

    base = [
        _candidate(node_id=NODE_A, version_id=VERSION_A, occurred_at=T0),
        _candidate(
            node_id=NODE_B,
            version_id=VERSION_B,
            text="Use Postgres for the hosted ledger",
            occurred_at=T0,
            spans=(SPAN_B,),
        ),
    ]
    # The supersede was authored last (15:00) but ingested first; the confirm
    # was authored earlier (14:30) and ingested an hour later (redelivery).
    supersede = {
        "tenant_id": TENANT,
        "event_type": "decision.superseded",
        "idempotency_key": "supersede:a-by-b",
        "event_hash": "d" * 64,
        "occurred_at": T0 + timedelta(hours=1),
        "ingested_at": T0 + timedelta(hours=1, minutes=5),
        "payload": {"superseded_node_id": NODE_A, "superseding_node_id": NODE_B},
    }
    confirm = {
        "tenant_id": TENANT,
        "event_type": "decision.confirmed",
        "idempotency_key": "confirm:node-a",
        "event_hash": "e" * 64,
        "occurred_at": T0 + timedelta(minutes=30),
        "ingested_at": T0 + timedelta(hours=2),
        "payload": {"decision_node_id": NODE_A},
    }
    # Arrival order (supersede before confirm) is also the input order; if
    # the fold followed arrival, the confirm would win and flip the status.
    graph = replay_events([supersede, confirm, *base])
    node_a = next(row for row in graph.nodes if row.decision_node_id == NODE_A)
    assert node_a.status == "superseded"
    assert any(row.edge_type == "supersedes" for row in graph.edges)


def test_confirm_with_later_occurred_at_wins() -> None:
    events = [
        *_two_node_history(),
        _status(
            LedgerEventType.DECISION_CONFIRMED,
            node_id=NODE_A,
            occurred_at=T0 + timedelta(hours=2),
            key="confirm:node-a-late",
        ),
    ]
    graph = replay_events(events)
    node_a = next(row for row in graph.nodes if row.decision_node_id == NODE_A)
    assert node_a.status == "confirmed"
    # The supersede edge stays: edges are facts, status is the fold's last word.
    assert any(row.edge_type == "supersedes" for row in graph.edges)


def test_merge_supersede_adds_duplicates_edge() -> None:
    events = [
        *_two_node_history()[:2],
        _supersede(occurred_at=T0 + timedelta(minutes=30), key="merge:a-into-b", merge=True),
    ]
    graph = replay_events(events)
    edge_types = {(row.from_node_id, row.to_node_id, row.edge_type) for row in graph.edges}
    assert (NODE_B, NODE_A, "supersedes") in edge_types
    assert (NODE_A, NODE_B, "duplicates") in edge_types


def test_merge_without_merged_into_rejected() -> None:
    bad = _supersede(
        occurred_at=T0 + timedelta(minutes=30),
        key="merge:bad",
        payload_extra={"merge": True},
    )
    with pytest.raises(GraphRebuildError, match="merged_into"):
        replay_events([*_two_node_history()[:2], bad])


def test_status_transition_unknown_node_rejected() -> None:
    orphan = _status(
        LedgerEventType.STALE_MARKED,
        node_id=NODE_B,
        occurred_at=T0,
        key="stale:orphan",
    )
    with pytest.raises(GraphRebuildError, match="unknown node"):
        replay_events([orphan])


def test_self_supersede_rejected() -> None:
    events = [
        _candidate(),
        _supersede(
            superseded=NODE_A,
            superseding=NODE_A,
            occurred_at=T0 + timedelta(minutes=1),
            key="supersede:self",
        ),
    ]
    with pytest.raises(GraphRebuildError, match="supersede itself"):
        replay_events(events)


def test_duplicate_node_insert_rejected() -> None:
    events = [
        _candidate(key="candidate:first"),
        _candidate(
            version_id=VERSION_B,
            occurred_at=T0 + timedelta(minutes=1),
            key="candidate:second",
        ),
    ]
    with pytest.raises(GraphRebuildError, match="re-inserts existing node"):
        replay_events(events)


def test_redelivered_event_applies_once() -> None:
    event = _candidate()
    once = replay_events([event])
    redelivered = replay_events([event, event])
    assert redelivered.nodes == once.nodes
    assert redelivered.versions == once.versions
    assert redelivered.duplicate_events == 1
    assert redelivered.applied_events == 1


def test_same_key_different_hash_rejected() -> None:
    first = _candidate(key="shared-key")
    second = _candidate(
        text="A different decision entirely",
        occurred_at=T0 + timedelta(minutes=1),
        key="shared-key",
    )
    with pytest.raises(GraphRebuildError, match="two different event hashes"):
        replay_events([first, second])


def test_missing_replay_payload_keys_rejected() -> None:
    event = LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=_ACTOR,
        occurred_at=T0,
        idempotency_key="candidate:missing-keys",
        payload={"decision_text": "No identifiers were echoed"},
        source_span_hashes=(SPAN_A,),
    )
    with pytest.raises(GraphRebuildError, match="decision_node_id"):
        replay_events([event])


def test_replay_required_payload_keys_cover_all_mutating_event_types() -> None:
    mutating = set(REPLAY_REQUIRED_PAYLOAD_KEYS)
    assert mutating == {
        LedgerEventType.CANDIDATE_PROPOSED,
        LedgerEventType.DECISION_CONFIRMED,
        LedgerEventType.DECISION_REJECTED,
        LedgerEventType.STALE_MARKED,
        LedgerEventType.DECISION_SUPERSEDED,
    }
    # Every other event type folds as an explicit no-op, never silently.
    graph = replay_events([])
    assert graph.noop_events == 0


def test_mixed_tenant_replay_rejected() -> None:
    events = [
        _candidate(),
        _candidate(
            node_id=NODE_B,
            version_id=VERSION_B,
            tenant_id=OTHER_TENANT,
            occurred_at=T0 + timedelta(minutes=1),
            key="candidate:other-tenant",
        ),
    ]
    with pytest.raises(GraphRebuildError, match="per-tenant"):
        replay_events(events)


def test_non_graph_events_fold_as_counted_noops() -> None:
    finding = LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=LedgerEventType.FINDING_EMITTED,
        actor=_ACTOR,
        occurred_at=T0 + timedelta(minutes=1),
        idempotency_key="finding:1",
        payload={"finding": "advisory"},
        source_span_hashes=(SPAN_A,),
        graph_snapshot_hash=SNAPSHOT_PROBE,
        model_id="claude-test",
        prompt_version="prompt-v1",
    )
    feedback = LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=LedgerEventType.FEEDBACK_RECORDED,
        actor=_ACTOR,
        occurred_at=T0 + timedelta(minutes=2),
        idempotency_key="feedback:1",
        payload={"reaction": "accepted"},
        graph_snapshot_hash=SNAPSHOT_PROBE,
    )
    graph = replay_events([_candidate(), finding, feedback])
    assert graph.noop_events == 2
    assert graph.applied_events == 1
    assert len(graph.nodes) == 1


def test_scope_normalization_drift_rejected() -> None:
    forged = {
        "scope_type": "owner",
        "value": "@AutumnGarage",
        "normalized_value": "@AutumnGarage",  # re-derivation yields "autumngarage"
    }
    with pytest.raises(GraphRebuildError, match="does not match the re-derived"):
        replay_events([_candidate(scopes=(forged,))])


def test_rows_sorted_canonically_regardless_of_event_order() -> None:
    events = [
        _candidate(
            node_id=NODE_B,
            version_id=VERSION_B,
            text="Use Postgres for the hosted ledger",
            occurred_at=T0,
            spans=(SPAN_B,),
        ),
        _candidate(node_id=NODE_A, version_id=VERSION_A, occurred_at=T0 + timedelta(minutes=1)),
    ]
    graph = replay_events(events)
    assert [row.decision_node_id for row in graph.nodes] == sorted([NODE_A, NODE_B])
    assert [row.decision_version_id for row in graph.versions] == sorted([VERSION_A, VERSION_B])


def test_draft_projection_rebuilt_event_shape() -> None:
    graph = replay_events(_two_node_history())
    occurred_at = T0 + timedelta(hours=3)
    event = draft_projection_rebuilt_event(
        graph,
        tenant_id=TENANT,
        source_id=SOURCE,
        actor=_ACTOR,
        occurred_at=occurred_at,
        schema_version=1,
        retrieval_config_version="retrieval-v1",
    )
    expected_hash = graph.snapshot_hash(
        schema_version=1, retrieval_config_version="retrieval-v1"
    )
    assert event.event_type is LedgerEventType.PROJECTION_REBUILT
    assert event.graph_snapshot_hash == expected_hash
    assert event.payload["graph_snapshot_hash"] == expected_hash
    assert event.payload["counts"] == {
        "edges": len(graph.edges),
        "nodes": len(graph.nodes),
        "scopes": len(graph.scopes),
        "versions": len(graph.versions),
    }
    assert event.payload["replay"]["applied_events"] == graph.applied_events
    # Drafting is deterministic: the same rebuild yields the same identity.
    twin = draft_projection_rebuilt_event(
        graph,
        tenant_id=TENANT,
        source_id=SOURCE,
        actor=_ACTOR,
        occurred_at=occurred_at,
        schema_version=1,
        retrieval_config_version="retrieval-v1",
    )
    assert twin.idempotency_key == event.idempotency_key
    assert twin.event_hash == event.event_hash


def test_draft_rejects_tenant_mismatch() -> None:
    graph = replay_events([_candidate()])
    with pytest.raises(GraphRebuildError, match="refusing to draft"):
        draft_projection_rebuilt_event(
            graph,
            tenant_id=OTHER_TENANT,
            source_id=SOURCE,
            actor=_ACTOR,
            occurred_at=T0,
            schema_version=1,
            retrieval_config_version="retrieval-v1",
        )


def test_unknown_event_type_rejected() -> None:
    bogus = {
        "tenant_id": TENANT,
        "event_type": "decision.bogus",
        "idempotency_key": "bogus:1",
        "event_hash": "f" * 64,
        "occurred_at": T0,
        "payload": {},
    }
    with pytest.raises(GraphRebuildError, match="unknown ledger event type"):
        replay_events([bogus])


def test_unorderable_event_rejected() -> None:
    naive = {
        "tenant_id": TENANT,
        "event_type": "decision.confirmed",
        "idempotency_key": "naive:1",
        "event_hash": "f" * 64,
        "occurred_at": datetime(2026, 6, 10, 14, 0),  # naive: no tz
        "payload": {"decision_node_id": NODE_A},
    }
    with pytest.raises(GraphRebuildError, match="total order"):
        replay_events([naive])


def test_rebuild_error_classifies_as_invalid_input_rejected() -> None:
    assert classify_failure(GraphRebuildError("probe")) is (
        DegradationMode.INVALID_INPUT_REJECTED
    )
