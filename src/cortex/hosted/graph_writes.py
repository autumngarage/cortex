"""Immutable-with-supersede write plans for the decision graph (cortex#314).

The DB triggers shipped in PR #477 *forbid* mutating `ledger_events` and
provenance; the deferred ``decision_nodes_current_version_fk`` makes the
node↔version cycle insertable — but nothing composes an event with its
projection mutations as one transaction. This module is that composition
layer, expressed the same way as the rest of the substrate: **parameterized
SQL statement plans** (strings + params), executable once cortex#472 lands
the first driver. Projections (`decision_nodes`, `decision_versions`,
`decision_edges`, `decision_scopes`) are rebuildable views of the event
log, so plans may UPDATE projections — but every plan's first statement is
the idempotent append to `ledger_events`, and no plan ever contains an
UPDATE or DELETE against `ledger_events`, `source_documents`, or
`source_spans` (a unit test enforces this invariant over every planner).

**The cortex#487 decision, recorded here:** there is no ``decision.merged``
ledger event type. A curation *merge* (duplicate decision A folded into
surviving decision B) is represented as the supersede pair:

1. a ``decision.superseded`` event for A whose payload carries
   ``{"merge": true, "merged_into": <B's node id>}``, and
2. a ``duplicates`` edge A→B (alongside the standard ``supersedes`` edge
   B→A), so dedup analytics can distinguish merges from ordinary
   supersedes without a new event type.

Rationale: adding an enum member would bump the DDL CHECK and
``HOSTED_SCHEMA_VERSION`` before any executable path exists, for semantics
the existing vocabulary already expresses losslessly. Slack curation
(cortex#491) builds on ``plan_merge`` below; if merge ever needs distinct
replay semantics, promoting it to a first-class event type is a
schema-version bump with this module as the single call site to change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from cortex.hosted.ledger_events import (
    LedgerEvent,
    LedgerEventType,
    ledger_event_insert_sql,
)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SUPERSEDE_EDGE_TYPE = "supersedes"
MERGE_DUPLICATE_EDGE_TYPE = "duplicates"


class GraphWriteValidationError(ValueError):
    """Raised when a write plan would violate graph invariants."""


@dataclass(frozen=True)
class PlannedStatement:
    """One parameterized statement inside a write transaction."""

    purpose: str
    sql: str
    parameters: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.purpose.strip():
            raise GraphWriteValidationError("purpose must not be empty")
        if not self.sql.strip():
            raise GraphWriteValidationError("sql must not be empty")


@dataclass(frozen=True)
class GraphWritePlan:
    """An ordered, single-transaction statement plan for one graph write.

    Executors run every statement in order inside one transaction; the
    first statement is always the idempotent ledger append. When that
    append returns no row (idempotency-key conflict — the event already
    exists), the executor MUST roll back and skip the projection
    statements: the projections were already updated by the first
    delivery. That contract is part of this plan's meaning, not an
    executor nicety.
    """

    event: LedgerEvent
    statements: tuple[PlannedStatement, ...]

    def __post_init__(self) -> None:
        if not self.statements:
            raise GraphWriteValidationError("a write plan needs at least one statement")
        first = self.statements[0]
        if first.purpose != "append-ledger-event":
            raise GraphWriteValidationError(
                "every write plan must begin with the idempotent ledger append"
            )


def plan_candidate_proposed(
    event: LedgerEvent,
    *,
    decision_node_id: str,
    decision_version_id: str,
    decision_text: str,
    confidence: str,
    scopes: tuple[tuple[str, str, str], ...] = (),
    repo_id: str | None = None,
    schema: str = "cortex_hosted",
) -> GraphWritePlan:
    """Plan: a new candidate decision enters the graph projection.

    ``scopes`` entries are ``(scope_type, scope_value, normalized_value)``
    triples already normalized by ``cortex.hosted.scopes``.
    """

    _require_event_type(event, LedgerEventType.CANDIDATE_PROPOSED)
    _require_uuid("decision_node_id", decision_node_id)
    _require_uuid("decision_version_id", decision_version_id)
    _require_non_empty("decision_text", decision_text)
    _require_non_empty("confidence", confidence)
    if repo_id is not None:
        _require_uuid("repo_id", repo_id)
    _validate_sql_identifier(schema)

    statements = [
        _append_statement(event, schema),
        PlannedStatement(
            purpose="insert-decision-node",
            sql=f"""
INSERT INTO {schema}.decision_nodes (
    decision_node_id, tenant_id, repo_id, current_version_id,
    status, confidence, latest_event_id
) VALUES (
    %(decision_node_id)s, %(tenant_id)s, %(repo_id)s, %(decision_version_id)s,
    'candidate', %(confidence)s,
    (SELECT event_id FROM {schema}.ledger_events
     WHERE tenant_id = %(tenant_id)s AND idempotency_key = %(idempotency_key)s)
);
""".strip(),
            parameters={
                "decision_node_id": decision_node_id,
                "tenant_id": event.tenant_id,
                "repo_id": repo_id,
                "decision_version_id": decision_version_id,
                "confidence": confidence,
                "idempotency_key": event.idempotency_key,
            },
        ),
        PlannedStatement(
            purpose="insert-decision-version",
            sql=f"""
INSERT INTO {schema}.decision_versions (
    decision_version_id, tenant_id, decision_node_id, source_event_id,
    decision_text, source_span_hashes, scope, decided_at
) VALUES (
    %(decision_version_id)s, %(tenant_id)s, %(decision_node_id)s,
    (SELECT event_id FROM {schema}.ledger_events
     WHERE tenant_id = %(tenant_id)s AND idempotency_key = %(idempotency_key)s),
    %(decision_text)s, %(source_span_hashes)s, %(scope)s::jsonb, %(occurred_at)s
);
""".strip(),
            parameters={
                "decision_version_id": decision_version_id,
                "tenant_id": event.tenant_id,
                "decision_node_id": decision_node_id,
                "decision_text": decision_text,
                "source_span_hashes": list(event.source_span_hashes),
                "scope": "{}",
                "idempotency_key": event.idempotency_key,
                "occurred_at": event.occurred_at,
            },
        ),
    ]
    for scope_type, scope_value, normalized_value in scopes:
        _require_non_empty("scope_type", scope_type)
        _require_non_empty("scope_value", scope_value)
        _require_non_empty("normalized_value", normalized_value)
        statements.append(
            PlannedStatement(
                purpose=f"insert-decision-scope:{scope_type}",
                sql=f"""
INSERT INTO {schema}.decision_scopes (
    tenant_id, repo_id, decision_node_id, scope_type, scope_value,
    normalized_value, source_event_id
) VALUES (
    %(tenant_id)s, %(repo_id)s, %(decision_node_id)s, %(scope_type)s,
    %(scope_value)s, %(normalized_value)s,
    (SELECT event_id FROM {schema}.ledger_events
     WHERE tenant_id = %(tenant_id)s AND idempotency_key = %(idempotency_key)s)
)
ON CONFLICT (tenant_id, decision_node_id, scope_type, normalized_value) DO NOTHING;
""".strip(),
                parameters={
                    "tenant_id": event.tenant_id,
                    "repo_id": repo_id,
                    "decision_node_id": decision_node_id,
                    "scope_type": scope_type,
                    "scope_value": scope_value,
                    "normalized_value": normalized_value,
                    "idempotency_key": event.idempotency_key,
                },
            )
        )
    return GraphWritePlan(event=event, statements=tuple(statements))


def plan_status_transition(
    event: LedgerEvent,
    *,
    decision_node_id: str,
    new_status: str,
    schema: str = "cortex_hosted",
) -> GraphWritePlan:
    """Plan: confirm / reject / mark-stale an existing decision node."""

    transitions = {
        LedgerEventType.DECISION_CONFIRMED: "confirmed",
        LedgerEventType.DECISION_REJECTED: "rejected",
        LedgerEventType.STALE_MARKED: "stale",
    }
    expected = transitions.get(event.event_type)
    if expected is None:
        raise GraphWriteValidationError(
            f"{event.event_type.value} is not a status-transition event; "
            "use the dedicated planner for it"
        )
    if new_status != expected:
        raise GraphWriteValidationError(
            f"{event.event_type.value} must set status {expected!r}; got {new_status!r}"
        )
    _require_uuid("decision_node_id", decision_node_id)
    _validate_sql_identifier(schema)

    return GraphWritePlan(
        event=event,
        statements=(
            _append_statement(event, schema),
            _node_status_statement(
                schema,
                decision_node_id=decision_node_id,
                tenant_id=event.tenant_id,
                new_status=new_status,
                idempotency_key=event.idempotency_key,
            ),
        ),
    )


def plan_supersede(
    event: LedgerEvent,
    *,
    superseded_node_id: str,
    superseding_node_id: str,
    merge: bool = False,
    schema: str = "cortex_hosted",
) -> GraphWritePlan:
    """Plan: decision A is superseded by decision B (the only edit verb).

    With ``merge=True`` this is the cortex#487 merge representation: the
    event payload must carry ``merge: true`` and ``merged_into``, and the
    plan adds a ``duplicates`` edge A→B alongside the ``supersedes`` edge
    B→A.
    """

    _require_event_type(event, LedgerEventType.DECISION_SUPERSEDED)
    _require_uuid("superseded_node_id", superseded_node_id)
    _require_uuid("superseding_node_id", superseding_node_id)
    if superseded_node_id == superseding_node_id:
        raise GraphWriteValidationError("a decision cannot supersede itself")
    _validate_sql_identifier(schema)
    if merge:
        if event.payload.get("merge") is not True:
            raise GraphWriteValidationError(
                "merge supersede events must carry payload merge=true (cortex#487)"
            )
        if event.payload.get("merged_into") != superseding_node_id:
            raise GraphWriteValidationError(
                "merge supersede events must carry payload merged_into=<superseding node id>"
            )

    statements = [
        _append_statement(event, schema),
        _node_status_statement(
            schema,
            decision_node_id=superseded_node_id,
            tenant_id=event.tenant_id,
            new_status="superseded",
            idempotency_key=event.idempotency_key,
        ),
        _edge_statement(
            schema,
            purpose="insert-supersedes-edge",
            tenant_id=event.tenant_id,
            from_node_id=superseding_node_id,
            to_node_id=superseded_node_id,
            edge_type=SUPERSEDE_EDGE_TYPE,
            idempotency_key=event.idempotency_key,
        ),
    ]
    if merge:
        statements.append(
            _edge_statement(
                schema,
                purpose="insert-merge-duplicates-edge",
                tenant_id=event.tenant_id,
                from_node_id=superseded_node_id,
                to_node_id=superseding_node_id,
                edge_type=MERGE_DUPLICATE_EDGE_TYPE,
                idempotency_key=event.idempotency_key,
            )
        )
    return GraphWritePlan(event=event, statements=tuple(statements))


def _append_statement(event: LedgerEvent, schema: str) -> PlannedStatement:
    return PlannedStatement(
        purpose="append-ledger-event",
        sql=ledger_event_insert_sql(schema),
        parameters=event.as_insert_parameters(),
    )


def _node_status_statement(
    schema: str,
    *,
    decision_node_id: str,
    tenant_id: str,
    new_status: str,
    idempotency_key: str,
) -> PlannedStatement:
    return PlannedStatement(
        purpose=f"project-node-status:{new_status}",
        sql=f"""
UPDATE {schema}.decision_nodes
SET status = %(new_status)s,
    latest_event_id = (
        SELECT event_id FROM {schema}.ledger_events
        WHERE tenant_id = %(tenant_id)s AND idempotency_key = %(idempotency_key)s
    ),
    updated_at = now()
WHERE tenant_id = %(tenant_id)s AND decision_node_id = %(decision_node_id)s;
""".strip(),
        parameters={
            "new_status": new_status,
            "tenant_id": tenant_id,
            "decision_node_id": decision_node_id,
            "idempotency_key": idempotency_key,
        },
    )


def _edge_statement(
    schema: str,
    *,
    purpose: str,
    tenant_id: str,
    from_node_id: str,
    to_node_id: str,
    edge_type: str,
    idempotency_key: str,
) -> PlannedStatement:
    return PlannedStatement(
        purpose=purpose,
        sql=f"""
INSERT INTO {schema}.decision_edges (
    tenant_id, from_node_id, to_node_id, edge_type, source_event_id
) VALUES (
    %(tenant_id)s, %(from_node_id)s, %(to_node_id)s, %(edge_type)s,
    (SELECT event_id FROM {schema}.ledger_events
     WHERE tenant_id = %(tenant_id)s AND idempotency_key = %(idempotency_key)s)
)
ON CONFLICT (tenant_id, from_node_id, to_node_id, edge_type) DO NOTHING;
""".strip(),
        parameters={
            "tenant_id": tenant_id,
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "edge_type": edge_type,
            "idempotency_key": idempotency_key,
        },
    )


def _require_event_type(event: LedgerEvent, expected: LedgerEventType) -> None:
    if event.event_type is not expected:
        raise GraphWriteValidationError(
            f"plan requires a {expected.value} event; got {event.event_type.value}"
        )


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise GraphWriteValidationError(f"{name} must be a non-empty string")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError) as exc:
        raise GraphWriteValidationError(f"{name} must be a UUID") from exc


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise GraphWriteValidationError(f"invalid SQL identifier: {name!r}")
