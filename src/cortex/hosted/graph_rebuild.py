"""Deterministic projection rebuild from the hosted event log (cortex#320).

This is the REBUILDABLE-PROJECTIONS invariant made executable. The graph
projections (``decision_nodes``, ``decision_versions``, ``decision_edges``,
``decision_scopes``) are declared rebuildable views of ``ledger_events``
(``graph_writes`` module contract); until now nothing could actually rebuild
them. ``replay_events`` is that rebuild: a **pure function fold** over one
tenant's events that applies the ``graph_writes`` semantics in the canonical
source-timestamp total order (``event_ordering.ordering_key`` — so supersede
status transitions honor ``occurred_at``, never webhook arrival, per
cortex#313) and returns the full projection row sets as
``graph_snapshot`` row types, ready for ``compute_graph_snapshot_hash``.

Determinism contract (tests prove all three):

- the same multiset of events, in any iteration order, folds to an
  identical ``RebuiltGraph`` and identical snapshot hash;
- rebuild-after-rebuild is idempotent — re-folding the same events (with or
  without the ``projection.rebuilt`` event a prior rebuild drafted) yields
  the same graph;
- redelivered events (same ``(tenant_id, idempotency_key)``, same
  ``event_hash``) apply once, mirroring the executor contract on
  ``GraphWritePlan`` (the ledger append's ``ON CONFLICT DO NOTHING`` skips
  the projection statements on redelivery). The skip is counted in
  ``RebuiltGraph.duplicate_events``, never silent. The same key with a
  *different* hash is refused outright — replay cannot decide which content
  is real.

**Replay payload contract** (``REPLAY_PAYLOAD_CONTRACT_VERSION = 1``). The
``graph_writes`` planners receive projection identifiers as keyword
arguments, so a rebuildable log requires the write path to echo them into
the event payload. The required keys per event type are named in
``REPLAY_REQUIRED_PAYLOAD_KEYS``; an event missing them fails closed with
the missing key named — replay never guesses identifiers. Event types that
do not mutate the decision-graph projections (``finding.emitted``,
``feedback.recorded``, ``projection.rebuilt`` — they write findings,
feedback, and snapshot bookkeeping, not graph rows) fold as **explicit,
counted no-ops** (``RebuiltGraph.noop_events``), so a full-ledger replay is
legal and nothing is skipped silently.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

from cortex.hosted.event_ordering import EventOrderingError, ordering_key
from cortex.hosted.graph_snapshot import (
    SNAPSHOT_HASH_VERSION,
    EdgeRow,
    GraphSnapshotValidationError,
    NodeRow,
    ScopeRow,
    VersionRow,
    compute_graph_snapshot_hash,
)
from cortex.hosted.graph_writes import MERGE_DUPLICATE_EDGE_TYPE, SUPERSEDE_EDGE_TYPE
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)
from cortex.hosted.scopes import ScopeType, ScopeValidationError, normalize_scope_value

REPLAY_PAYLOAD_CONTRACT_VERSION = 1

# The write-path echo contract: payload keys replay requires per event type.
REPLAY_REQUIRED_PAYLOAD_KEYS: Mapping[LedgerEventType, tuple[str, ...]] = MappingProxyType(
    {
        LedgerEventType.CANDIDATE_PROPOSED: (
            "confidence",
            "decision_node_id",
            "decision_text",
            "decision_version_id",
        ),
        LedgerEventType.DECISION_CONFIRMED: ("decision_node_id",),
        LedgerEventType.DECISION_REJECTED: ("decision_node_id",),
        LedgerEventType.STALE_MARKED: ("decision_node_id",),
        LedgerEventType.DECISION_SUPERSEDED: (
            "superseded_node_id",
            "superseding_node_id",
        ),
    }
)

# Status each transition event projects, mirroring graph_writes planners.
_STATUS_BY_EVENT_TYPE: Mapping[LedgerEventType, str] = MappingProxyType(
    {
        LedgerEventType.DECISION_CONFIRMED: "confirmed",
        LedgerEventType.DECISION_REJECTED: "rejected",
        LedgerEventType.STALE_MARKED: "stale",
    }
)

# Ledger event types that never mutate the decision-graph projections.
_PROJECTION_NOOP_EVENT_TYPES = frozenset(
    {
        LedgerEventType.FINDING_EMITTED,
        LedgerEventType.FEEDBACK_RECORDED,
        LedgerEventType.PROJECTION_REBUILT,
    }
)


class GraphRebuildError(ValueError):
    """Raised when the event log cannot fold into a valid projection."""


@dataclass(frozen=True)
class RebuiltGraph:
    """The projection row sets one deterministic replay produced.

    Rows are the ``graph_snapshot`` participating-field types, sorted by
    their identity sort keys, so two equal graphs compare equal and hash
    identically regardless of event iteration order. ``tenant_id`` is
    ``None`` only for an empty replay (no events, no tenant).
    """

    tenant_id: str | None
    nodes: tuple[NodeRow, ...] = ()
    versions: tuple[VersionRow, ...] = ()
    edges: tuple[EdgeRow, ...] = ()
    scopes: tuple[ScopeRow, ...] = ()
    applied_events: int = 0
    duplicate_events: int = 0
    noop_events: int = 0

    def snapshot_hash(self, *, schema_version: int, retrieval_config_version: str) -> str:
        """Canonical snapshot hash of the rebuilt graph (``graph_snapshot``).

        ``compute_graph_snapshot_hash`` re-validates referential integrity,
        so a fold bug that produced dangling references fails here instead
        of registering a hash for an invalid graph.
        """

        return compute_graph_snapshot_hash(
            self.nodes,
            self.versions,
            self.edges,
            self.scopes,
            schema_version=schema_version,
            retrieval_config_version=retrieval_config_version,
        )


@dataclass
class _FoldState:
    tenant_id: str | None = None
    nodes: dict[str, NodeRow] = field(default_factory=dict)
    versions: dict[str, VersionRow] = field(default_factory=dict)
    edges: set[EdgeRow] = field(default_factory=set)
    scopes: set[ScopeRow] = field(default_factory=set)
    seen_event_hash_by_key: dict[str, str] = field(default_factory=dict)
    applied: int = 0
    duplicates: int = 0
    noops: int = 0


def replay_events(events: Iterable[object]) -> RebuiltGraph:
    """Fold one tenant's ledger events into the decision-graph projections.

    Accepts ``LedgerEvent`` instances and DB-row-shaped mappings (the same
    dual accessor contract as ``event_ordering.ordering_key``). Events are
    sorted into the canonical total order first, so iteration order can
    never change the result, and a supersede authored later always lands
    after the decision it supersedes regardless of delivery order.
    """

    try:
        ordered = sorted(events, key=ordering_key)
    except EventOrderingError as exc:
        raise GraphRebuildError(f"event cannot enter the replay total order: {exc}") from exc

    state = _FoldState()
    for event in ordered:
        _fold_event(state, event)

    return RebuiltGraph(
        tenant_id=state.tenant_id,
        nodes=tuple(sorted(state.nodes.values(), key=lambda row: row.decision_node_id)),
        versions=tuple(
            sorted(state.versions.values(), key=lambda row: row.decision_version_id)
        ),
        edges=tuple(
            sorted(state.edges, key=lambda row: (row.from_node_id, row.to_node_id, row.edge_type))
        ),
        scopes=tuple(
            sorted(
                state.scopes,
                key=lambda row: (row.decision_node_id, row.scope_type, row.normalized_value),
            )
        ),
        applied_events=state.applied,
        duplicate_events=state.duplicates,
        noop_events=state.noops,
    )


def draft_projection_rebuilt_event(
    graph: RebuiltGraph,
    *,
    tenant_id: str,
    source_id: str,
    actor: ActorRef,
    occurred_at: datetime,
    schema_version: int,
    retrieval_config_version: str,
) -> LedgerEvent:
    """Draft the ``projection.rebuilt`` event recording one rebuild.

    Carries the new snapshot hash both as the event's
    ``graph_snapshot_hash`` (required for this event type) and in the
    payload alongside row counts and replay diagnostics, so the rebuild is
    auditable from the ledger alone. Replaying the drafted event is a
    counted no-op (see module docstring), which is what makes
    rebuild-after-rebuild idempotent.
    """

    if not isinstance(graph, RebuiltGraph):
        raise GraphRebuildError(
            f"draft_projection_rebuilt_event consumes RebuiltGraph; got {type(graph).__name__}"
        )
    if graph.tenant_id is not None and graph.tenant_id != tenant_id:
        raise GraphRebuildError(
            f"rebuilt graph belongs to tenant {graph.tenant_id}; refusing to draft a "
            f"projection.rebuilt event for tenant {tenant_id}"
        )
    snapshot_hash = graph.snapshot_hash(
        schema_version=schema_version, retrieval_config_version=retrieval_config_version
    )
    payload: dict[str, Any] = {
        "counts": {
            "edges": len(graph.edges),
            "nodes": len(graph.nodes),
            "scopes": len(graph.scopes),
            "versions": len(graph.versions),
        },
        "graph_snapshot_hash": snapshot_hash,
        "replay": {
            "applied_events": graph.applied_events,
            "duplicate_events": graph.duplicate_events,
            "noop_events": graph.noop_events,
            "payload_contract_version": REPLAY_PAYLOAD_CONTRACT_VERSION,
        },
        "retrieval_config_version": retrieval_config_version,
        "schema_version": schema_version,
        "snapshot_hash_version": SNAPSHOT_HASH_VERSION,
    }
    external_ref = f"projection-rebuilt@{occurred_at.isoformat()}#{snapshot_hash}"
    return LedgerEvent(
        tenant_id=tenant_id,
        source_id=source_id,
        event_type=LedgerEventType.PROJECTION_REBUILT,
        actor=actor,
        occurred_at=occurred_at,
        idempotency_key=derive_idempotency_key(
            source_id=source_id,
            event_type=LedgerEventType.PROJECTION_REBUILT,
            source_event_external_id=external_ref,
            payload=payload,
        ),
        source_event_external_id=external_ref,
        payload=payload,
        graph_snapshot_hash=snapshot_hash,
    )


def _fold_event(state: _FoldState, event: object) -> None:
    tenant_id = _read_str(event, "tenant_id")
    if state.tenant_id is None:
        state.tenant_id = tenant_id
    elif state.tenant_id != tenant_id:
        raise GraphRebuildError(
            "replay is per-tenant: refusing to fold events for tenant "
            f"{tenant_id} into a rebuild for tenant {state.tenant_id}"
        )

    event_type = _read_event_type(event)
    idempotency_key = _read_str(event, "idempotency_key")
    event_hash = _read_str(event, "event_hash")
    seen_hash = state.seen_event_hash_by_key.get(idempotency_key)
    if seen_hash is not None:
        if seen_hash != event_hash:
            raise GraphRebuildError(
                f"idempotency key {idempotency_key!r} appears with two different "
                f"event hashes ({seen_hash} vs {event_hash}); the log content has "
                "drifted and replay cannot decide which event is real"
            )
        # Mirror of the executor contract: a redelivered append returns no
        # row, so its projection statements never run. Counted, not silent.
        state.duplicates += 1
        return
    state.seen_event_hash_by_key[idempotency_key] = event_hash

    if event_type in _PROJECTION_NOOP_EVENT_TYPES:
        # finding.emitted / feedback.recorded / projection.rebuilt write
        # findings, feedback, and snapshot bookkeeping — not the decision
        # graph. Explicit branch, counted in noop_events.
        state.noops += 1
        return

    payload = _read_payload(event)
    _require_payload_keys(event_type, payload)
    if event_type is LedgerEventType.CANDIDATE_PROPOSED:
        _apply_candidate_proposed(state, event, payload)
    elif event_type in _STATUS_BY_EVENT_TYPE:
        _apply_status_transition(state, event_type, payload)
    elif event_type is LedgerEventType.DECISION_SUPERSEDED:
        _apply_supersede(state, payload)
    else:  # pragma: no cover - every LedgerEventType is handled above
        raise GraphRebuildError(f"no replay semantics for event type {event_type.value}")
    state.applied += 1


def _apply_candidate_proposed(
    state: _FoldState, event: object, payload: Mapping[str, Any]
) -> None:
    node_id = _payload_str(payload, "decision_node_id")
    version_id = _payload_str(payload, "decision_version_id")
    occurred_at = _read_occurred_at(event)
    span_hashes = _read_span_hashes(event)
    scope = payload.get("scope", {})
    if not isinstance(scope, Mapping):
        raise GraphRebuildError("payload 'scope' must be a JSON object when present")
    repo_id = payload.get("repo_id")

    try:
        version = VersionRow(
            decision_version_id=version_id,
            decision_node_id=node_id,
            decision_text=_payload_str(payload, "decision_text"),
            source_span_hashes=span_hashes,
            scope=scope,
            decided_at=occurred_at,
        )
        node = NodeRow(
            decision_node_id=node_id,
            status="candidate",
            confidence=_payload_str(payload, "confidence"),
            current_version_id=version_id,
            repo_id=repo_id if repo_id is None else str(repo_id),
        )
    except GraphSnapshotValidationError as exc:
        raise GraphRebuildError(f"candidate.proposed event is not replayable: {exc}") from exc

    if node.decision_node_id in state.nodes:
        raise GraphRebuildError(
            f"candidate.proposed re-inserts existing node {node.decision_node_id} "
            "under a new idempotency key; replay contract v1 has exactly one "
            "version per node insert (no second-version planner exists)"
        )
    if version.decision_version_id in state.versions:
        raise GraphRebuildError(
            f"candidate.proposed re-inserts existing version {version.decision_version_id}"
        )
    state.nodes[node.decision_node_id] = node
    state.versions[version.decision_version_id] = version
    for scope_row in _scope_rows(node.decision_node_id, payload):
        # Mirrors the decision_scopes ON CONFLICT DO NOTHING: the identity
        # triple is a set, repeats collapse without error.
        state.scopes.add(scope_row)


def _apply_status_transition(
    state: _FoldState, event_type: LedgerEventType, payload: Mapping[str, Any]
) -> None:
    node_id = _payload_str(payload, "decision_node_id")
    node = state.nodes.get(_canonical_node_id(node_id))
    if node is None:
        raise GraphRebuildError(
            f"{event_type.value} addresses unknown node {node_id}; the log is "
            "missing the candidate.proposed event that created it"
        )
    state.nodes[node.decision_node_id] = NodeRow(
        decision_node_id=node.decision_node_id,
        status=_STATUS_BY_EVENT_TYPE[event_type],
        confidence=node.confidence,
        current_version_id=node.current_version_id,
        repo_id=node.repo_id,
    )


def _apply_supersede(state: _FoldState, payload: Mapping[str, Any]) -> None:
    superseded_id = _canonical_node_id(_payload_str(payload, "superseded_node_id"))
    superseding_id = _canonical_node_id(_payload_str(payload, "superseding_node_id"))
    if superseded_id == superseding_id:
        raise GraphRebuildError("a decision cannot supersede itself")
    superseded = state.nodes.get(superseded_id)
    if superseded is None:
        raise GraphRebuildError(
            f"decision.superseded addresses unknown superseded node {superseded_id}"
        )
    if superseding_id not in state.nodes:
        raise GraphRebuildError(
            f"decision.superseded references unknown superseding node {superseding_id}"
        )
    merge = payload.get("merge", False)
    if merge is not False and merge is not True:
        raise GraphRebuildError("payload 'merge' must be a boolean when present")
    if merge and payload.get("merged_into") != _payload_str(payload, "superseding_node_id"):
        # Mirrors graph_writes.plan_supersede's cortex#487 merge markers.
        raise GraphRebuildError(
            "merge supersede events must carry payload merged_into=<superseding node id>"
        )

    state.nodes[superseded_id] = NodeRow(
        decision_node_id=superseded.decision_node_id,
        status="superseded",
        confidence=superseded.confidence,
        current_version_id=superseded.current_version_id,
        repo_id=superseded.repo_id,
    )
    # Edge identity triples are sets (ON CONFLICT DO NOTHING semantics).
    state.edges.add(
        EdgeRow(
            from_node_id=superseding_id,
            to_node_id=superseded_id,
            edge_type=SUPERSEDE_EDGE_TYPE,
        )
    )
    if merge:
        state.edges.add(
            EdgeRow(
                from_node_id=superseded_id,
                to_node_id=superseding_id,
                edge_type=MERGE_DUPLICATE_EDGE_TYPE,
            )
        )


def _scope_rows(node_id: str, payload: Mapping[str, Any]) -> list[ScopeRow]:
    raw_scopes = payload.get("proposed_scopes", [])
    if isinstance(raw_scopes, (str, bytes)) or not isinstance(raw_scopes, Iterable):
        raise GraphRebuildError("payload 'proposed_scopes' must be a sequence of scope payloads")
    rows: list[ScopeRow] = []
    for entry in raw_scopes:
        if not isinstance(entry, Mapping):
            raise GraphRebuildError(
                "each proposed scope must be a mapping with scope_type, value, "
                "and normalized_value"
            )
        scope_type = entry.get("scope_type")
        scope_value = entry.get("value")
        normalized_value = entry.get("normalized_value")
        if (
            not isinstance(scope_type, str)
            or not isinstance(scope_value, str)
            or not isinstance(normalized_value, str)
        ):
            raise GraphRebuildError(
                "proposed scope payloads require string scope_type, value, and "
                "normalized_value"
            )
        try:
            row = ScopeRow(
                decision_node_id=node_id,
                scope_type=scope_type,
                scope_value=scope_value,
                normalized_value=normalized_value,
            )
            rederived = normalize_scope_value(ScopeType(row.scope_type), scope_value)
        except (GraphSnapshotValidationError, ScopeValidationError) as exc:
            raise GraphRebuildError(f"proposed scope is not replayable: {exc}") from exc
        if rederived != normalized_value:
            # Fail closed on forged or stale normalization: the payload claims
            # a normalized form the scope vocabulary does not produce.
            raise GraphRebuildError(
                f"scope normalized_value {normalized_value!r} does not match the "
                f"re-derived normalization {rederived!r} for ({scope_type!r}, "
                f"{scope_value!r})"
            )
        rows.append(row)
    return rows


def _require_payload_keys(event_type: LedgerEventType, payload: Mapping[str, Any]) -> None:
    required = REPLAY_REQUIRED_PAYLOAD_KEYS.get(event_type, ())
    missing = [key for key in required if key not in payload]
    if missing:
        raise GraphRebuildError(
            f"{event_type.value} payload is missing replay-required key(s) "
            f"{missing!r} (replay payload contract v{REPLAY_PAYLOAD_CONTRACT_VERSION}: "
            "the write path must echo projection identifiers into the event payload)"
        )


def _read_field(event: object, name: str) -> Any:
    value = event.get(name) if isinstance(event, Mapping) else getattr(event, name, None)
    if value is None:
        raise GraphRebuildError(f"event is missing required replay field {name!r}")
    return value


def _read_str(event: object, name: str) -> str:
    value = _read_field(event, name)
    if not isinstance(value, str) or not value.strip():
        raise GraphRebuildError(f"event field {name!r} must be a non-empty string")
    return value


def _read_event_type(event: object) -> LedgerEventType:
    value = _read_field(event, "event_type")
    if isinstance(value, LedgerEventType):
        return value
    try:
        return LedgerEventType(value)
    except ValueError as exc:
        raise GraphRebuildError(f"unknown ledger event type: {value!r}") from exc


def _read_payload(event: object) -> Mapping[str, Any]:
    value = _read_field(event, "payload")
    if not isinstance(value, Mapping):
        raise GraphRebuildError("event payload must be a JSON object")
    return value


def _read_occurred_at(event: object) -> datetime:
    value = _read_field(event, "occurred_at")
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GraphRebuildError("occurred_at must be a timezone-aware datetime")
    return value


def _read_span_hashes(event: object) -> tuple[str, ...]:
    value = _read_field(event, "source_span_hashes")
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise GraphRebuildError("source_span_hashes must be a sequence of sha256 hex strings")
    return tuple(value)


def _payload_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GraphRebuildError(f"payload key {key!r} must be a non-empty string")
    return value


def _canonical_node_id(value: str) -> str:
    # NodeRow canonicalizes UUID spellings; probe lookups must match. Reuse
    # the row type itself so the two canonicalizations cannot drift.
    try:
        return NodeRow(
            decision_node_id=value, status="candidate", confidence="probe"
        ).decision_node_id
    except GraphSnapshotValidationError as exc:
        raise GraphRebuildError(f"decision node id is not a UUID: {value!r}") from exc
