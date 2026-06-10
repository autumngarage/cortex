"""Canonical graph-snapshot hashing for hosted Cortex (cortex#323).

The schema already demands this hash — ``graph_snapshots`` registers one row
per ``(tenant_id, graph_snapshot_hash)``, ``retrieval_traces`` carries it on
every query, and ``finding.emitted`` events refuse to exist without it — but
until now nothing computed it. This module owns the computation: a sha256
over a canonical serialization of the decision graph, so the same graph
state always yields the same hash regardless of row arrival order, mapping
key order, or which process computed it.

**The participating-field list is the contract**, versioned by
``SNAPSHOT_HASH_VERSION``. Changing the list without bumping the version
would silently orphan every persisted hash, so the list is explicit:

Participating fields (``SNAPSHOT_HASH_VERSION = 1``):

- nodes (``decision_nodes``): ``decision_node_id`` (identity, sort key),
  ``status``, ``confidence``, ``current_version_id``, ``repo_id``
- versions (``decision_versions``): ``decision_version_id`` (identity, sort
  key), ``decision_node_id``, ``decision_text``, ``source_span_hashes``
  (stored array order preserved — the array is immutable row content, same
  treatment as ``LedgerEvent.as_immutable_payload``), ``scope``,
  ``decided_at`` (immutable source timestamp, normalized to UTC)
- edges (``decision_edges``): ``from_node_id``, ``to_node_id``,
  ``edge_type`` — the identity triple per the table's UNIQUE constraint,
  also the sort key
- scopes (``decision_scopes``): ``decision_node_id``, ``scope_type``,
  ``normalized_value`` — the identity triple per the table's UNIQUE
  constraint, also the sort key — plus ``scope_value`` (the captured raw
  form is immutable row content)
- snapshot scalars: ``schema_version``, ``retrieval_config_version``,
  ``snapshot_hash_version``

Excluded, deliberately:

- ``created_at`` / ``updated_at`` everywhere — mutable bookkeeping
  timestamps; rebuilding the same graph later must yield the same hash.
- DB-generated surrogate ids that are not identity: ``decision_edge_id``
  and ``decision_scope_id`` (their UNIQUE triples are the identity), and
  the ``source_event_id`` / ``latest_event_id`` event-row references
  (``ledger_events.event_id`` is ``gen_random_uuid()``, so a replay of the
  same logical events produces different ids).
- ``tenant_id`` — the hash is content-addressed; tenant scoping lives in
  the ``UNIQUE (tenant_id, graph_snapshot_hash)`` constraint, not in the
  hash input.
- ``decision_scopes.repo_id`` — a denormalized copy of the owning node's
  ``repo_id``, which already participates on the node row.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, TypeVar
from uuid import UUID

from cortex.hosted.scopes import ScopeType

SNAPSHOT_HASH_VERSION = 1

# Mirror the decision_nodes.status / decision_edges.edge_type CHECK
# constraints in schema.create_schema_sql; tests assert the DDL and these
# sets cannot drift apart.
NODE_STATUSES = frozenset({"candidate", "confirmed", "rejected", "superseded", "stale"})
EDGE_TYPES = frozenset(
    {"supersedes", "duplicates", "refines", "contradicts", "derived_from", "mentioned_with"}
)

NODE_HASH_FIELDS = ("confidence", "current_version_id", "decision_node_id", "repo_id", "status")
VERSION_HASH_FIELDS = (
    "decided_at",
    "decision_node_id",
    "decision_text",
    "decision_version_id",
    "scope",
    "source_span_hashes",
)
EDGE_HASH_FIELDS = ("edge_type", "from_node_id", "to_node_id")
SCOPE_HASH_FIELDS = ("decision_node_id", "normalized_value", "scope_type", "scope_value")

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class GraphSnapshotValidationError(ValueError):
    """Raised when graph rows cannot participate in a canonical snapshot hash."""


@dataclass(frozen=True)
class NodeRow:
    """Participating ``decision_nodes`` columns (see module contract)."""

    decision_node_id: str
    status: str
    confidence: str
    current_version_id: str | None = None
    repo_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_node_id", _canonical_uuid("decision_node_id", self.decision_node_id)
        )
        object.__setattr__(
            self,
            "current_version_id",
            _canonical_optional_uuid("current_version_id", self.current_version_id),
        )
        object.__setattr__(self, "repo_id", _canonical_optional_uuid("repo_id", self.repo_id))
        if self.status not in NODE_STATUSES:
            raise GraphSnapshotValidationError(
                f"status must be one of {sorted(NODE_STATUSES)}, got {self.status!r}"
            )
        _require_non_empty("confidence", self.confidence)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> NodeRow:
        """Build from a DB-shaped mapping; non-participating columns are ignored."""

        return cls(
            decision_node_id=_required(row, "decision_node_id"),
            status=_required(row, "status"),
            confidence=_required(row, "confidence"),
            current_version_id=row.get("current_version_id"),
            repo_id=row.get("repo_id"),
        )

    def as_hash_material(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "current_version_id": self.current_version_id,
            "decision_node_id": self.decision_node_id,
            "repo_id": self.repo_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class VersionRow:
    """Participating ``decision_versions`` columns (see module contract)."""

    decision_version_id: str
    decision_node_id: str
    decision_text: str
    source_span_hashes: tuple[str, ...]
    scope: Mapping[str, Any] = field(default_factory=dict)
    decided_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision_version_id",
            _canonical_uuid("decision_version_id", self.decision_version_id),
        )
        object.__setattr__(
            self, "decision_node_id", _canonical_uuid("decision_node_id", self.decision_node_id)
        )
        _require_non_empty("decision_text", self.decision_text)
        spans = tuple(self.source_span_hashes)
        if not spans:
            # Mirrors the decision_versions CHECK (cardinality(source_span_hashes) > 0):
            # an uncited decision version is structurally unrepresentable.
            raise GraphSnapshotValidationError(
                "source_span_hashes requires at least one source span hash"
            )
        for value in spans:
            if not isinstance(value, str) or not _SHA256_RE.match(value):
                raise GraphSnapshotValidationError(
                    "source_span_hashes values must be sha256 hex strings"
                )
        object.__setattr__(self, "source_span_hashes", spans)
        _validate_json_object("scope", self.scope)
        if self.decided_at is not None and (
            not isinstance(self.decided_at, datetime)
            or self.decided_at.tzinfo is None
            or self.decided_at.utcoffset() is None
        ):
            raise GraphSnapshotValidationError(
                "decided_at must be a timezone-aware datetime when present"
            )
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> VersionRow:
        """Build from a DB-shaped mapping; non-participating columns are ignored."""

        raw_spans = _required(row, "source_span_hashes")
        if isinstance(raw_spans, str) or not isinstance(raw_spans, Iterable):
            raise GraphSnapshotValidationError(
                "source_span_hashes must be a sequence of sha256 hex strings"
            )
        return cls(
            decision_version_id=_required(row, "decision_version_id"),
            decision_node_id=_required(row, "decision_node_id"),
            decision_text=_required(row, "decision_text"),
            source_span_hashes=tuple(raw_spans),
            scope=_required(row, "scope"),
            decided_at=row.get("decided_at"),
        )

    def as_hash_material(self) -> dict[str, Any]:
        # timestamptz stores no zone; the client-side zone depends on session
        # settings, so the canonical form pins UTC before serializing.
        decided_at = None if self.decided_at is None else self.decided_at.astimezone(UTC).isoformat()
        return {
            "decided_at": decided_at,
            "decision_node_id": self.decision_node_id,
            "decision_text": self.decision_text,
            "decision_version_id": self.decision_version_id,
            "scope": dict(self.scope),
            "source_span_hashes": list(self.source_span_hashes),
        }


@dataclass(frozen=True)
class EdgeRow:
    """Participating ``decision_edges`` columns (see module contract)."""

    from_node_id: str
    to_node_id: str
    edge_type: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "from_node_id", _canonical_uuid("from_node_id", self.from_node_id))
        object.__setattr__(self, "to_node_id", _canonical_uuid("to_node_id", self.to_node_id))
        if self.edge_type not in EDGE_TYPES:
            raise GraphSnapshotValidationError(
                f"edge_type must be one of {sorted(EDGE_TYPES)}, got {self.edge_type!r}"
            )
        # Compare after canonicalization so differing spellings of one UUID
        # cannot smuggle a self-loop past the schema CHECK mirror.
        if self.from_node_id == self.to_node_id:
            raise GraphSnapshotValidationError("edges must not connect a node to itself")

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> EdgeRow:
        """Build from a DB-shaped mapping; non-participating columns are ignored."""

        return cls(
            from_node_id=_required(row, "from_node_id"),
            to_node_id=_required(row, "to_node_id"),
            edge_type=_required(row, "edge_type"),
        )

    def as_hash_material(self) -> dict[str, Any]:
        return {
            "edge_type": self.edge_type,
            "from_node_id": self.from_node_id,
            "to_node_id": self.to_node_id,
        }


@dataclass(frozen=True)
class ScopeRow:
    """Participating ``decision_scopes`` columns (see module contract)."""

    decision_node_id: str
    scope_type: str
    scope_value: str
    normalized_value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_node_id", _canonical_uuid("decision_node_id", self.decision_node_id)
        )
        try:
            canonical_type = ScopeType(self.scope_type).value
        except ValueError as exc:
            raise GraphSnapshotValidationError(
                f"scope_type must be one of {sorted(member.value for member in ScopeType)}, "
                f"got {self.scope_type!r}"
            ) from exc
        object.__setattr__(self, "scope_type", canonical_type)
        _require_non_empty("scope_value", self.scope_value)
        _require_non_empty("normalized_value", self.normalized_value)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> ScopeRow:
        """Build from a DB-shaped mapping; non-participating columns are ignored."""

        return cls(
            decision_node_id=_required(row, "decision_node_id"),
            scope_type=_required(row, "scope_type"),
            scope_value=_required(row, "scope_value"),
            normalized_value=_required(row, "normalized_value"),
        )

    def as_hash_material(self) -> dict[str, Any]:
        return {
            "decision_node_id": self.decision_node_id,
            "normalized_value": self.normalized_value,
            "scope_type": self.scope_type,
            "scope_value": self.scope_value,
        }


def graph_snapshot_hash_material(
    nodes: Iterable[NodeRow],
    versions: Iterable[VersionRow],
    edges: Iterable[EdgeRow],
    scopes: Iterable[ScopeRow],
    *,
    schema_version: int,
    retrieval_config_version: str,
) -> dict[str, Any]:
    """Return the exact mapping ``compute_graph_snapshot_hash`` hashes.

    Public so callers and tests can inspect the canonical serialization
    instead of trusting an opaque digest.
    """

    # bool is an int subtype but serializes as true/false, which would change
    # the hash silently; refuse it outright.
    if isinstance(schema_version, bool) or schema_version < 1:
        raise GraphSnapshotValidationError("schema_version must be an integer >= 1")
    _require_non_empty("retrieval_config_version", retrieval_config_version)

    node_rows = _checked_rows("nodes", nodes, NodeRow)
    version_rows = _checked_rows("versions", versions, VersionRow)
    edge_rows = _checked_rows("edges", edges, EdgeRow)
    scope_rows = _checked_rows("scopes", scopes, ScopeRow)

    node_ids = _unique_identities("nodes", node_rows, lambda row: row.decision_node_id)
    _unique_identities("versions", version_rows, lambda row: row.decision_version_id)
    _unique_identities(
        "edges", edge_rows, lambda row: (row.from_node_id, row.to_node_id, row.edge_type)
    )
    _unique_identities(
        "scopes", scope_rows, lambda row: (row.decision_node_id, row.scope_type, row.normalized_value)
    )

    versions_by_id = {row.decision_version_id: row for row in version_rows}
    for version in version_rows:
        if version.decision_node_id not in node_ids:
            raise GraphSnapshotValidationError(
                f"version {version.decision_version_id} references unknown node "
                f"{version.decision_node_id}"
            )
    for node in node_rows:
        if node.current_version_id is None:
            continue
        current = versions_by_id.get(node.current_version_id)
        if current is None:
            raise GraphSnapshotValidationError(
                f"node {node.decision_node_id} current_version_id {node.current_version_id} "
                "is not in the snapshot"
            )
        if current.decision_node_id != node.decision_node_id:
            raise GraphSnapshotValidationError(
                f"node {node.decision_node_id} current_version_id {node.current_version_id} "
                f"belongs to node {current.decision_node_id}"
            )
    for edge in edge_rows:
        for endpoint in (edge.from_node_id, edge.to_node_id):
            if endpoint not in node_ids:
                raise GraphSnapshotValidationError(
                    f"edge ({edge.from_node_id} -> {edge.to_node_id}, {edge.edge_type}) "
                    f"references unknown node {endpoint}"
                )
    for scope in scope_rows:
        if scope.decision_node_id not in node_ids:
            raise GraphSnapshotValidationError(
                f"scope ({scope.scope_type}, {scope.normalized_value}) references unknown node "
                f"{scope.decision_node_id}"
            )

    return {
        "edges": [
            row.as_hash_material()
            for row in sorted(
                edge_rows, key=lambda row: (row.from_node_id, row.to_node_id, row.edge_type)
            )
        ],
        "nodes": [
            row.as_hash_material()
            for row in sorted(node_rows, key=lambda row: row.decision_node_id)
        ],
        "retrieval_config_version": retrieval_config_version,
        "schema_version": schema_version,
        "scopes": [
            row.as_hash_material()
            for row in sorted(
                scope_rows,
                key=lambda row: (row.decision_node_id, row.scope_type, row.normalized_value),
            )
        ],
        "snapshot_hash_version": SNAPSHOT_HASH_VERSION,
        "versions": [
            row.as_hash_material()
            for row in sorted(version_rows, key=lambda row: row.decision_version_id)
        ],
    }


def compute_graph_snapshot_hash(
    nodes: Iterable[NodeRow],
    versions: Iterable[VersionRow],
    edges: Iterable[EdgeRow],
    scopes: Iterable[ScopeRow],
    *,
    schema_version: int,
    retrieval_config_version: str,
) -> str:
    """Compute the canonical sha256 the ``graph_snapshots`` table registers."""

    return _hash_mapping(
        graph_snapshot_hash_material(
            nodes,
            versions,
            edges,
            scopes,
            schema_version=schema_version,
            retrieval_config_version=retrieval_config_version,
        )
    )


_RowT = TypeVar("_RowT", NodeRow, VersionRow, EdgeRow, ScopeRow)


def _checked_rows(name: str, rows: Iterable[_RowT], row_type: type[_RowT]) -> list[_RowT]:
    checked: list[_RowT] = []
    for row in rows:
        if not isinstance(row, row_type):
            raise GraphSnapshotValidationError(
                f"{name} must contain {row_type.__name__} instances; "
                f"build DB rows via {row_type.__name__}.from_mapping"
            )
        checked.append(row)
    return checked


def _unique_identities(
    name: str, rows: list[_RowT], identity: Callable[[_RowT], object]
) -> set[object]:
    seen: set[object] = set()
    for row in rows:
        key = identity(row)
        if key in seen:
            raise GraphSnapshotValidationError(f"duplicate {name} identity: {key!r}")
        seen.add(key)
    return seen


def _canonical_uuid(name: str, value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise GraphSnapshotValidationError(f"{name} must be a UUID") from exc
    raise GraphSnapshotValidationError(f"{name} must be a UUID")


def _canonical_optional_uuid(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _canonical_uuid(name, value)


def _required(row: Mapping[str, Any], key: str) -> Any:
    if key not in row:
        raise GraphSnapshotValidationError(f"row is missing required column {key!r}")
    return row[key]


def _require_non_empty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise GraphSnapshotValidationError(f"{name} must be a non-empty string")


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise GraphSnapshotValidationError(f"{name} must be a JSON object")
    try:
        # dict() first so MappingProxyType-wrapped rows revalidate cleanly
        # when dataclasses.replace re-runs __post_init__.
        json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise GraphSnapshotValidationError(f"{name} must be JSON-serializable") from exc


def _hash_mapping(value: Mapping[str, Any]) -> str:
    # Same bytes semantics as ledger_events._hash_mapping (canonical JSON via
    # sort_keys + compact separators, utf-8, sha256). Kept module-local rather
    # than importing the private helper; a test asserts cross-module
    # equivalence so the two idioms cannot drift apart silently.
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
