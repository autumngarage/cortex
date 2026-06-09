"""Append-only hosted ledger event boundary."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

EVENT_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LedgerEventValidationError(ValueError):
    """Raised when an event would violate ledger replay invariants."""


class LedgerEventType(StrEnum):
    """Versioned hosted ledger event types."""

    CANDIDATE_PROPOSED = "candidate.proposed"
    DECISION_CONFIRMED = "decision.confirmed"
    DECISION_REJECTED = "decision.rejected"
    DECISION_SUPERSEDED = "decision.superseded"
    FINDING_EMITTED = "finding.emitted"
    FEEDBACK_RECORDED = "feedback.recorded"
    STALE_MARKED = "decision.stale_marked"
    PROJECTION_REBUILT = "projection.rebuilt"


SOURCE_SPAN_REQUIRED_EVENTS = frozenset(
    {
        LedgerEventType.DECISION_CONFIRMED,
        LedgerEventType.DECISION_SUPERSEDED,
        LedgerEventType.FINDING_EMITTED,
    }
)
GRAPH_SNAPSHOT_REQUIRED_EVENTS = frozenset(
    {
        LedgerEventType.FINDING_EMITTED,
        LedgerEventType.FEEDBACK_RECORDED,
        LedgerEventType.PROJECTION_REBUILT,
    }
)
MODEL_VERSION_REQUIRED_EVENTS = frozenset({LedgerEventType.FINDING_EMITTED})


@dataclass(frozen=True)
class ActorRef:
    """Actor responsible for a ledger event."""

    actor_type: str
    actor_id: str

    def __post_init__(self) -> None:
        _require_non_empty("actor_type", self.actor_type)
        _require_non_empty("actor_id", self.actor_id)

    def as_payload(self) -> dict[str, str]:
        return {"actor_type": self.actor_type, "actor_id": self.actor_id}


@dataclass(frozen=True)
class LedgerEvent:
    """Immutable hosted ledger event ready for Postgres insertion."""

    tenant_id: str
    source_id: str
    event_type: LedgerEventType
    actor: ActorRef
    occurred_at: datetime
    idempotency_key: str
    payload: Mapping[str, Any]
    source_span_hashes: tuple[str, ...] = ()
    graph_snapshot_hash: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    event_version: int = EVENT_SCHEMA_VERSION
    source_event_external_id: str | None = None
    previous_event_hash: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_uuid("tenant_id", self.tenant_id)
        _require_uuid("source_id", self.source_id)
        _require_non_empty("idempotency_key", self.idempotency_key)
        if self.event_version < 1:
            raise LedgerEventValidationError("event_version must be >= 1")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise LedgerEventValidationError("occurred_at must be timezone-aware")

        _validate_json_object("payload", self.payload)
        _validate_json_object("metadata", self.metadata)
        _validate_hashes("source_span_hashes", self.source_span_hashes)
        _validate_optional_hash("graph_snapshot_hash", self.graph_snapshot_hash)
        _validate_optional_hash("previous_event_hash", self.previous_event_hash)

        if self.event_type in SOURCE_SPAN_REQUIRED_EVENTS and not self.source_span_hashes:
            raise LedgerEventValidationError(
                f"{self.event_type.value} requires at least one source span hash"
            )
        if self.event_type in GRAPH_SNAPSHOT_REQUIRED_EVENTS and self.graph_snapshot_hash is None:
            raise LedgerEventValidationError(
                f"{self.event_type.value} requires a graph snapshot hash"
            )

        has_model = self.model_id is not None
        has_prompt = self.prompt_version is not None
        if has_model != has_prompt:
            raise LedgerEventValidationError("model_id and prompt_version must be provided together")
        if self.event_type in MODEL_VERSION_REQUIRED_EVENTS and not has_model:
            raise LedgerEventValidationError(
                f"{self.event_type.value} requires model_id and prompt_version"
            )

        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def event_hash(self) -> str:
        """Content hash over immutable replay material, excluding database IDs."""

        return _hash_mapping(self.as_immutable_payload())

    def as_immutable_payload(self) -> dict[str, Any]:
        """Return the stable event material used for replay and hashing."""

        return {
            "actor": self.actor.as_payload(),
            "event_type": self.event_type.value,
            "event_version": self.event_version,
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "idempotency_key": self.idempotency_key,
            "metadata": dict(self.metadata),
            "model_id": self.model_id,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": dict(self.payload),
            "previous_event_hash": self.previous_event_hash,
            "prompt_version": self.prompt_version,
            "schema_version": EVENT_SCHEMA_VERSION,
            "source_event_external_id": self.source_event_external_id,
            "source_id": self.source_id,
            "source_span_hashes": list(self.source_span_hashes),
            "tenant_id": self.tenant_id,
        }

    def as_insert_parameters(self) -> dict[str, Any]:
        """Return DB-API named parameters for `ledger_event_insert_sql`."""

        return {
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "event_type": self.event_type.value,
            "event_version": self.event_version,
            "actor_type": self.actor.actor_type,
            "actor_id": self.actor.actor_id,
            "occurred_at": self.occurred_at,
            "idempotency_key": self.idempotency_key,
            "source_event_external_id": self.source_event_external_id,
            "source_span_hashes": list(self.source_span_hashes),
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "payload": json.dumps(dict(self.payload), sort_keys=True, separators=(",", ":")),
            "metadata": json.dumps(dict(self.metadata), sort_keys=True, separators=(",", ":")),
            "previous_event_hash": self.previous_event_hash,
            "event_hash": self.event_hash,
        }


def derive_idempotency_key(
    *,
    source_id: str,
    event_type: LedgerEventType,
    source_event_external_id: str,
    payload: Mapping[str, Any] | None = None,
) -> str:
    """Derive a retry-safe idempotency key for webhook/Slack event delivery."""

    _require_uuid("source_id", source_id)
    _require_non_empty("source_event_external_id", source_event_external_id)
    material: dict[str, Any] = {
        "event_type": event_type.value,
        "source_event_external_id": source_event_external_id,
        "source_id": source_id,
    }
    if payload is not None:
        _validate_json_object("payload", payload)
        material["payload_hash"] = _hash_mapping(payload)
    return _hash_mapping(material)


def ledger_event_insert_sql(schema: str = "cortex_hosted") -> str:
    """Return the idempotent append statement for hosted ledger events."""

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.ledger_events (
    tenant_id,
    source_id,
    event_type,
    event_version,
    actor_type,
    actor_id,
    occurred_at,
    idempotency_key,
    source_event_external_id,
    source_span_hashes,
    graph_snapshot_hash,
    model_id,
    prompt_version,
    payload,
    metadata,
    previous_event_hash,
    event_hash
) VALUES (
    %(tenant_id)s,
    %(source_id)s,
    %(event_type)s,
    %(event_version)s,
    %(actor_type)s,
    %(actor_id)s,
    %(occurred_at)s,
    %(idempotency_key)s,
    %(source_event_external_id)s,
    %(source_span_hashes)s,
    %(graph_snapshot_hash)s,
    %(model_id)s,
    %(prompt_version)s,
    %(payload)s::jsonb,
    %(metadata)s::jsonb,
    %(previous_event_hash)s,
    %(event_hash)s
)
ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
RETURNING event_id, event_hash;
""".strip()


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise LedgerEventValidationError(f"{name} must not be empty")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise LedgerEventValidationError(f"{name} must be a UUID") from exc


def _validate_hashes(name: str, hashes: tuple[str, ...]) -> None:
    for value in hashes:
        if not _SHA256_RE.match(value):
            raise LedgerEventValidationError(f"{name} values must be sha256 hex strings")


def _validate_optional_hash(name: str, value: str | None) -> None:
    if value is not None and not _SHA256_RE.match(value):
        raise LedgerEventValidationError(f"{name} must be a sha256 hex string")


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise LedgerEventValidationError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise LedgerEventValidationError(f"{name} must be JSON-serializable") from exc


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise LedgerEventValidationError(f"invalid SQL identifier: {name!r}")
