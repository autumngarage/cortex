from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from cortex.hosted import ledger_events
from cortex.hosted.graph_snapshot import (
    EDGE_HASH_FIELDS,
    EDGE_TYPES,
    NODE_HASH_FIELDS,
    NODE_STATUSES,
    SCOPE_HASH_FIELDS,
    SNAPSHOT_HASH_VERSION,
    VERSION_HASH_FIELDS,
    EdgeRow,
    GraphSnapshotValidationError,
    NodeRow,
    ScopeRow,
    VersionRow,
    compute_graph_snapshot_hash,
    graph_snapshot_hash_material,
)
from cortex.hosted.schema import create_schema_sql
from cortex.hosted.scopes import ScopeType

NODE_A = "11111111-1111-4111-8111-111111111111"
NODE_B = "22222222-2222-4222-8222-222222222222"
VERSION_A1 = "33333333-3333-4333-8333-333333333333"
VERSION_B1 = "44444444-4444-4444-8444-444444444444"
REPO_ID = "55555555-5555-4555-8555-555555555555"
SPAN_A = "a" * 64
SPAN_B = "b" * 64
SPAN_C = "c" * 64

# Pinned byte-level contract for SNAPSHOT_HASH_VERSION = 1: sha256 over the
# canonical JSON of the empty material with schema_version=1 and
# retrieval_config_version="retrieval-v1". If this constant ever stops
# matching, the serialization changed and every persisted hash is orphaned.
EMPTY_GRAPH_HASH = "b57c7da4a0477e6429d0059349af620ce96643f50bd81b13428f28a834332b4f"


def _node_a() -> NodeRow:
    return NodeRow(
        decision_node_id=NODE_A,
        status="superseded",
        confidence="medium",
        current_version_id=VERSION_A1,
        repo_id=REPO_ID,
    )


def _node_b() -> NodeRow:
    return NodeRow(
        decision_node_id=NODE_B,
        status="confirmed",
        confidence="high",
        current_version_id=VERSION_B1,
        repo_id=REPO_ID,
    )


def _version_a1() -> VersionRow:
    return VersionRow(
        decision_version_id=VERSION_A1,
        decision_node_id=NODE_A,
        decision_text="Use SQLite for the local cache",
        source_span_hashes=(SPAN_A,),
        scope={"service": "api"},
        decided_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
    )


def _version_b1() -> VersionRow:
    return VersionRow(
        decision_version_id=VERSION_B1,
        decision_node_id=NODE_B,
        decision_text="Use Railway Postgres for the hosted ledger",
        source_span_hashes=(SPAN_B, SPAN_C),
        scope={},
        decided_at=datetime(2026, 6, 2, 9, 30, tzinfo=UTC),
    )


def _edge() -> EdgeRow:
    return EdgeRow(from_node_id=NODE_B, to_node_id=NODE_A, edge_type="supersedes")


def _scope() -> ScopeRow:
    return ScopeRow(
        decision_node_id=NODE_B,
        scope_type=ScopeType.PATH,
        scope_value="src/API/Storage.py",
        normalized_value="src/api/storage.py",
    )


def _hash(
    nodes: tuple[NodeRow, ...] | None = None,
    versions: tuple[VersionRow, ...] | None = None,
    edges: tuple[EdgeRow, ...] | None = None,
    scopes: tuple[ScopeRow, ...] | None = None,
    *,
    schema_version: int = 1,
    retrieval_config_version: str = "retrieval-v1",
) -> str:
    return compute_graph_snapshot_hash(
        nodes if nodes is not None else (_node_a(), _node_b()),
        versions if versions is not None else (_version_a1(), _version_b1()),
        edges if edges is not None else (_edge(),),
        scopes if scopes is not None else (_scope(),),
        schema_version=schema_version,
        retrieval_config_version=retrieval_config_version,
    )


def test_same_input_twice_yields_same_hash() -> None:
    first = _hash()
    second = _hash()

    assert first == second
    assert len(first) == 64


def test_row_order_does_not_change_the_hash() -> None:
    forward = _hash(nodes=(_node_a(), _node_b()), versions=(_version_a1(), _version_b1()))
    reversed_rows = _hash(nodes=(_node_b(), _node_a()), versions=(_version_b1(), _version_a1()))

    assert forward == reversed_rows


def test_from_mapping_with_db_extras_matches_direct_construction() -> None:
    # DB rows carry mutable timestamps, surrogate ids, event references, and
    # tenant scoping; none of them participate in the hash.
    created_at = datetime(2026, 6, 3, 8, 0, tzinfo=UTC)
    node_rows = tuple(
        NodeRow.from_mapping(
            {
                **row.as_hash_material(),
                "tenant_id": UUID(int=9),
                "latest_event_id": UUID(int=10),
                "created_at": created_at,
                "updated_at": created_at + timedelta(hours=4),
            }
        )
        for row in (_node_a(), _node_b())
    )
    version_rows = tuple(
        VersionRow.from_mapping(
            {
                "decision_version_id": UUID(row.decision_version_id),
                "decision_node_id": UUID(row.decision_node_id),
                "decision_text": row.decision_text,
                "source_span_hashes": list(row.source_span_hashes),
                "scope": dict(row.scope),
                "decided_at": row.decided_at,
                "tenant_id": UUID(int=9),
                "source_event_id": UUID(int=11),
                "created_at": created_at,
            }
        )
        for row in (_version_a1(), _version_b1())
    )
    edge_rows = (
        EdgeRow.from_mapping(
            {
                "decision_edge_id": UUID(int=12),
                "from_node_id": UUID(NODE_B),
                "to_node_id": UUID(NODE_A),
                "edge_type": "supersedes",
                "source_event_id": UUID(int=11),
                "created_at": created_at,
            }
        ),
    )
    scope_rows = (
        ScopeRow.from_mapping(
            {
                "decision_scope_id": UUID(int=13),
                "decision_node_id": UUID(NODE_B),
                "scope_type": "path",
                "scope_value": "src/API/Storage.py",
                "normalized_value": "src/api/storage.py",
                "repo_id": UUID(REPO_ID),
                "source_event_id": UUID(int=11),
                "created_at": created_at,
            }
        ),
    )

    assert _hash(node_rows, version_rows, edge_rows, scope_rows) == _hash()


def test_every_participating_field_change_changes_the_hash() -> None:
    base = _hash()
    variants = {
        "node.status": _hash(nodes=(_node_a(), replace(_node_b(), status="stale"))),
        "node.confidence": _hash(nodes=(_node_a(), replace(_node_b(), confidence="low"))),
        "node.current_version_id": _hash(
            nodes=(_node_a(), replace(_node_b(), current_version_id=None))
        ),
        "node.repo_id": _hash(nodes=(_node_a(), replace(_node_b(), repo_id=None))),
        "version.decision_text": _hash(
            versions=(_version_a1(), replace(_version_b1(), decision_text="Use Neon Postgres"))
        ),
        "version.source_span_hashes": _hash(
            versions=(_version_a1(), replace(_version_b1(), source_span_hashes=(SPAN_B,)))
        ),
        "version.span_order": _hash(
            versions=(_version_a1(), replace(_version_b1(), source_span_hashes=(SPAN_C, SPAN_B)))
        ),
        "version.scope": _hash(
            versions=(replace(_version_a1(), scope={"service": "worker"}), _version_b1())
        ),
        "version.decided_at": _hash(
            versions=(
                _version_a1(),
                replace(_version_b1(), decided_at=datetime(2026, 6, 2, 9, 31, tzinfo=UTC)),
            )
        ),
        "version.decided_at_absent": _hash(
            versions=(_version_a1(), replace(_version_b1(), decided_at=None))
        ),
        "edge.edge_type": _hash(edges=(replace(_edge(), edge_type="refines"),)),
        "scope.scope_type": _hash(scopes=(replace(_scope(), scope_type="glob"),)),
        "scope.scope_value": _hash(scopes=(replace(_scope(), scope_value="src/api/storage.py"),)),
        "scope.normalized_value": _hash(
            scopes=(replace(_scope(), normalized_value="src/api/storage_v2.py"),)
        ),
        "schema_version": _hash(schema_version=2),
        "retrieval_config_version": _hash(retrieval_config_version="retrieval-v2"),
    }

    assert base not in variants.values()
    assert len(set(variants.values())) == len(variants)


def test_decided_at_is_normalized_to_utc_before_hashing() -> None:
    # timestamptz has no zone; the session zone must not leak into the hash.
    plus_two = timezone(timedelta(hours=2))
    shifted = replace(
        _version_b1(), decided_at=datetime(2026, 6, 2, 11, 30, tzinfo=plus_two)
    )

    assert _hash(versions=(_version_a1(), shifted)) == _hash()


def test_uuid_inputs_are_canonicalized() -> None:
    upper_node = NodeRow(
        decision_node_id=NODE_A.upper(),
        status="superseded",
        confidence="medium",
        current_version_id=VERSION_A1.upper(),
        repo_id=REPO_ID,
    )

    assert upper_node == _node_a()


def test_empty_graph_hashes_deterministically() -> None:
    computed = compute_graph_snapshot_hash(
        (), (), (), (), schema_version=1, retrieval_config_version="retrieval-v1"
    )

    assert computed == EMPTY_GRAPH_HASH


def test_hash_matches_ledger_events_hash_idiom() -> None:
    # The canonical serialization must be byte-identical to the event-hash
    # idiom in ledger_events; include non-ASCII content so encoding drift
    # cannot hide.
    unicode_version = replace(_version_a1(), decision_text="Garder le café — décision ✓")
    material = graph_snapshot_hash_material(
        (_node_a(), _node_b()),
        (unicode_version, _version_b1()),
        (_edge(),),
        (_scope(),),
        schema_version=1,
        retrieval_config_version="retrieval-v1",
    )

    assert _hash(versions=(unicode_version, _version_b1())) == ledger_events._hash_mapping(material)


def test_hash_material_field_lists_are_the_contract() -> None:
    assert SNAPSHOT_HASH_VERSION == 1
    assert tuple(sorted(_node_a().as_hash_material())) == NODE_HASH_FIELDS
    assert tuple(sorted(_version_a1().as_hash_material())) == VERSION_HASH_FIELDS
    assert tuple(sorted(_edge().as_hash_material())) == EDGE_HASH_FIELDS
    assert tuple(sorted(_scope().as_hash_material())) == SCOPE_HASH_FIELDS

    material = graph_snapshot_hash_material(
        (_node_a(), _node_b()),
        (_version_a1(), _version_b1()),
        (_edge(),),
        (_scope(),),
        schema_version=1,
        retrieval_config_version="retrieval-v1",
    )
    assert sorted(material) == [
        "edges",
        "nodes",
        "retrieval_config_version",
        "schema_version",
        "scopes",
        "snapshot_hash_version",
        "versions",
    ]
    assert material["snapshot_hash_version"] == SNAPSHOT_HASH_VERSION


def test_vocabularies_cannot_drift_from_schema_ddl() -> None:
    ddl = create_schema_sql()

    for status in NODE_STATUSES:
        assert f"'{status}'" in ddl
    for edge_type in EDGE_TYPES:
        assert f"'{edge_type}'" in ddl


@pytest.mark.parametrize(
    ("label", "build"),
    [
        ("bad node uuid", lambda: NodeRow("not-a-uuid", "confirmed", "high")),
        ("unknown status", lambda: NodeRow(NODE_A, "shipped", "high")),
        ("empty confidence", lambda: NodeRow(NODE_A, "confirmed", "   ")),
        (
            "no span hashes",
            lambda: VersionRow(VERSION_A1, NODE_A, "Use Postgres", ()),
        ),
        (
            "malformed span hash",
            lambda: VersionRow(VERSION_A1, NODE_A, "Use Postgres", ("zz",)),
        ),
        (
            "empty decision text",
            lambda: VersionRow(VERSION_A1, NODE_A, "  ", (SPAN_A,)),
        ),
        (
            "naive decided_at",
            lambda: VersionRow(
                VERSION_A1,
                NODE_A,
                "Use Postgres",
                (SPAN_A,),
                decided_at=datetime(2026, 6, 1, 12, 0),
            ),
        ),
        (
            "non-json scope",
            lambda: VersionRow(
                VERSION_A1, NODE_A, "Use Postgres", (SPAN_A,), scope={"key": object()}
            ),
        ),
        ("edge self-loop", lambda: EdgeRow(NODE_A, NODE_A, "supersedes")),
        ("unknown edge type", lambda: EdgeRow(NODE_A, NODE_B, "blocks")),
        (
            "unknown scope type",
            lambda: ScopeRow(NODE_A, "directory", "src", "src"),
        ),
        (
            "empty normalized value",
            lambda: ScopeRow(NODE_A, "path", "src/api.py", " "),
        ),
    ],
)
def test_invalid_rows_fail_closed(label: str, build: Callable[[], object]) -> None:
    with pytest.raises(GraphSnapshotValidationError):
        build()


def test_duplicate_identities_fail_closed() -> None:
    with pytest.raises(GraphSnapshotValidationError, match="duplicate nodes identity"):
        _hash(nodes=(_node_a(), _node_a(), _node_b()))
    with pytest.raises(GraphSnapshotValidationError, match="duplicate versions identity"):
        _hash(versions=(_version_a1(), _version_a1(), _version_b1()))
    with pytest.raises(GraphSnapshotValidationError, match="duplicate edges identity"):
        _hash(edges=(_edge(), _edge()))
    with pytest.raises(GraphSnapshotValidationError, match="duplicate scopes identity"):
        _hash(scopes=(_scope(), _scope()))


def test_cross_references_fail_closed() -> None:
    with pytest.raises(GraphSnapshotValidationError, match="references unknown node"):
        _hash(nodes=(replace(_node_a(), current_version_id=None),), edges=(), scopes=())
    with pytest.raises(GraphSnapshotValidationError, match="references unknown node"):
        _hash(edges=(EdgeRow(NODE_B, "66666666-6666-4666-8666-666666666666", "refines"), _edge()))
    with pytest.raises(GraphSnapshotValidationError, match="references unknown node"):
        _hash(
            scopes=(
                ScopeRow(
                    "66666666-6666-4666-8666-666666666666", "path", "src/api.py", "src/api.py"
                ),
            )
        )
    with pytest.raises(GraphSnapshotValidationError, match="is not in the snapshot"):
        _hash(versions=(_version_a1(),), edges=(), scopes=())
    with pytest.raises(GraphSnapshotValidationError, match="belongs to node"):
        _hash(nodes=(replace(_node_a(), current_version_id=VERSION_B1), _node_b()))


def test_snapshot_scalars_fail_closed() -> None:
    with pytest.raises(GraphSnapshotValidationError, match="schema_version"):
        _hash(schema_version=0)
    with pytest.raises(GraphSnapshotValidationError, match="schema_version"):
        _hash(schema_version=True)
    with pytest.raises(GraphSnapshotValidationError, match="retrieval_config_version"):
        _hash(retrieval_config_version=" ")


def test_non_row_inputs_fail_closed_with_builder_pointer() -> None:
    with pytest.raises(GraphSnapshotValidationError, match=r"NodeRow\.from_mapping"):
        compute_graph_snapshot_hash(
            (_node_a().as_hash_material(),),  # type: ignore[arg-type]
            (),
            (),
            (),
            schema_version=1,
            retrieval_config_version="retrieval-v1",
        )


def test_from_mapping_missing_column_fails_closed() -> None:
    with pytest.raises(GraphSnapshotValidationError, match="missing required column 'status'"):
        NodeRow.from_mapping({"decision_node_id": NODE_A, "confidence": "high"})
    with pytest.raises(
        GraphSnapshotValidationError, match="sequence of sha256 hex strings"
    ):
        VersionRow.from_mapping(
            {
                "decision_version_id": VERSION_A1,
                "decision_node_id": NODE_A,
                "decision_text": "Use Postgres",
                "source_span_hashes": SPAN_A,
                "scope": {},
            }
        )
