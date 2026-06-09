"""Normalized decision scope indexing for hosted Cortex."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ISSUE_REF_RE = re.compile(r"(?:^|/issues/|#)(\d+)$")

SEMANTIC_MATCH_WEIGHT = 40


class ScopeValidationError(ValueError):
    """Raised when a scope cannot be normalized into the structural index."""


class ScopeType(StrEnum):
    PATH = "path"
    GLOB = "glob"
    SYMBOL = "symbol"
    PACKAGE = "package"
    CONFIG_KEY = "config_key"
    OWNER = "owner"
    SERVICE = "service"
    ISSUE_REF = "issue_ref"
    CHANNEL_REF = "channel_ref"


STRUCTURAL_SCOPE_WEIGHTS: dict[ScopeType, int] = {
    ScopeType.PATH: 100,
    ScopeType.GLOB: 98,
    ScopeType.SYMBOL: 95,
    ScopeType.CONFIG_KEY: 90,
    ScopeType.PACKAGE: 75,
    ScopeType.OWNER: 70,
    ScopeType.SERVICE: 70,
    ScopeType.ISSUE_REF: 65,
    ScopeType.CHANNEL_REF: 55,
}


@dataclass(frozen=True)
class DecisionScope:
    """A normalized scope row attached to a decision node."""

    tenant_id: str
    decision_node_id: str
    scope_type: ScopeType
    scope_value: str
    source_event_id: str
    repo_id: str | None = None

    def __post_init__(self) -> None:
        _require_uuid("tenant_id", self.tenant_id)
        _require_uuid("decision_node_id", self.decision_node_id)
        _require_uuid("source_event_id", self.source_event_id)
        if self.repo_id is not None:
            _require_uuid("repo_id", self.repo_id)
        normalize_scope_value(self.scope_type, self.scope_value)

    @property
    def normalized_value(self) -> str:
        return normalize_scope_value(self.scope_type, self.scope_value)

    @property
    def reason_code(self) -> str:
        return scope_reason_code(self.scope_type, self.normalized_value)

    @property
    def structural_weight(self) -> int:
        return STRUCTURAL_SCOPE_WEIGHTS[self.scope_type]

    def as_insert_parameters(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "repo_id": self.repo_id,
            "decision_node_id": self.decision_node_id,
            "scope_type": self.scope_type.value,
            "scope_value": self.scope_value,
            "normalized_value": self.normalized_value,
            "source_event_id": self.source_event_id,
        }


@dataclass(frozen=True)
class QueryScope:
    """A normalized scope extracted from a query or PR diff."""

    scope_type: ScopeType
    normalized_value: str

    @property
    def reason_code(self) -> str:
        return scope_reason_code(self.scope_type, self.normalized_value)

    @property
    def structural_weight(self) -> int:
        return STRUCTURAL_SCOPE_WEIGHTS[self.scope_type]


@dataclass(frozen=True)
class ChangedSurface:
    """Structural surface extracted from a diff."""

    paths: tuple[str, ...] = ()
    globs: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    config_keys: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    services: tuple[str, ...] = ()
    issue_refs: tuple[str, ...] = ()
    channel_refs: tuple[str, ...] = ()

    def query_scopes(self) -> tuple[QueryScope, ...]:
        pairs: list[tuple[ScopeType, str]] = []
        pairs.extend((ScopeType.PATH, value) for value in self.paths)
        pairs.extend((ScopeType.GLOB, value) for value in self.globs)
        pairs.extend((ScopeType.SYMBOL, value) for value in self.symbols)
        pairs.extend((ScopeType.PACKAGE, value) for value in self.packages)
        pairs.extend((ScopeType.CONFIG_KEY, value) for value in self.config_keys)
        pairs.extend((ScopeType.OWNER, value) for value in self.owners)
        pairs.extend((ScopeType.SERVICE, value) for value in self.services)
        pairs.extend((ScopeType.ISSUE_REF, value) for value in self.issue_refs)
        pairs.extend((ScopeType.CHANNEL_REF, value) for value in self.channel_refs)

        seen: set[tuple[ScopeType, str]] = set()
        scopes: list[QueryScope] = []
        for scope_type, raw_value in pairs:
            normalized = normalize_scope_value(scope_type, raw_value)
            key = (scope_type, normalized)
            if key in seen:
                continue
            seen.add(key)
            scopes.append(QueryScope(scope_type=scope_type, normalized_value=normalized))
        return tuple(scopes)


def normalize_scope_value(scope_type: ScopeType, value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ScopeValidationError("scope value must not be empty")

    if scope_type in {ScopeType.PATH, ScopeType.GLOB}:
        return _normalize_path_like(raw)
    if scope_type is ScopeType.SYMBOL:
        return " ".join(raw.split())
    if scope_type is ScopeType.PACKAGE:
        return raw.lower().replace("_", "-")
    if scope_type is ScopeType.CONFIG_KEY:
        return raw.lower().replace("__", ".").strip(".")
    if scope_type is ScopeType.OWNER:
        return raw.lower().removeprefix("@")
    if scope_type is ScopeType.SERVICE:
        return raw.lower().replace("_", "-")
    if scope_type is ScopeType.ISSUE_REF:
        return _normalize_issue_ref(raw)
    if scope_type is ScopeType.CHANNEL_REF:
        return "#" + raw.lower().removeprefix("#")
    raise ScopeValidationError(f"unsupported scope type: {scope_type}")


def scope_reason_code(scope_type: ScopeType, normalized_value: str) -> str:
    normalized = normalize_scope_value(scope_type, normalized_value)
    return f"scope:{scope_type.value}:{normalized}"


def query_scope_parameters(scopes: Iterable[QueryScope]) -> dict[str, list[object]]:
    rows = tuple(scopes)
    return {
        "scope_types": [scope.scope_type.value for scope in rows],
        "normalized_values": [scope.normalized_value for scope in rows],
        "reason_codes": [scope.reason_code for scope in rows],
        "structural_weights": [scope.structural_weight for scope in rows],
    }


def decision_scope_insert_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.decision_scopes (
    tenant_id,
    repo_id,
    decision_node_id,
    scope_type,
    scope_value,
    normalized_value,
    source_event_id
) VALUES (
    %(tenant_id)s,
    %(repo_id)s,
    %(decision_node_id)s,
    %(scope_type)s,
    %(scope_value)s,
    %(normalized_value)s,
    %(source_event_id)s
)
ON CONFLICT (tenant_id, decision_node_id, scope_type, normalized_value) DO NOTHING
RETURNING decision_scope_id, scope_type, normalized_value;
""".strip()


def decisions_for_diff_scope_sql(schema: str = "cortex_hosted") -> str:
    """Return structural candidate retrieval SQL for `decisions_for_diff`."""

    _validate_sql_identifier(schema)
    return f"""
WITH query_scopes AS (
    SELECT *
    FROM unnest(
        %(scope_types)s::text[],
        %(normalized_values)s::text[],
        %(reason_codes)s::text[],
        %(structural_weights)s::integer[]
    ) AS q(scope_type, normalized_value, reason_code, structural_weight)
),
ranked_matches AS (
    SELECT DISTINCT ON (node.decision_node_id)
        node.decision_node_id,
        node.status,
        scope.scope_type,
        scope.normalized_value,
        q.reason_code,
        q.structural_weight,
        node.updated_at
    FROM query_scopes AS q
    JOIN {schema}.decision_scopes AS scope
      ON scope.scope_type = q.scope_type
     AND scope.normalized_value = q.normalized_value
    JOIN {schema}.decision_nodes AS node
      ON node.tenant_id = scope.tenant_id
     AND node.decision_node_id = scope.decision_node_id
    WHERE scope.tenant_id = %(tenant_id)s
      AND (%(repo_id)s::uuid IS NULL OR scope.repo_id IS NULL OR scope.repo_id = %(repo_id)s::uuid)
      AND (%(repo_id)s::uuid IS NULL OR node.repo_id IS NULL OR node.repo_id = %(repo_id)s::uuid)
      AND node.status IN ('candidate', 'confirmed')
    ORDER BY node.decision_node_id, q.structural_weight DESC, node.updated_at DESC
)
SELECT
    decision_node_id,
    status,
    scope_type,
    normalized_value,
    reason_code,
    structural_weight
FROM ranked_matches
ORDER BY structural_weight DESC, updated_at DESC
LIMIT %(limit)s;
""".strip()


def _normalize_path_like(value: str) -> str:
    normalized = value.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    normalized = normalized.removeprefix("./")
    return normalized.rstrip("/") or "."


def _normalize_issue_ref(value: str) -> str:
    match = _ISSUE_REF_RE.search(value)
    if match is None:
        raise ScopeValidationError(f"issue ref must be a number, #number, or issue URL: {value!r}")
    return f"#{match.group(1)}"


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise ScopeValidationError(f"{name} must be a UUID") from exc


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ScopeValidationError(f"invalid SQL identifier: {name!r}")
