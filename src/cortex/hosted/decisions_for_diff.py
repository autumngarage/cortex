"""Hybrid `decisions_for_diff` retrieval boundary for hosted Cortex."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

from cortex.hosted.ask_ledger import (
    RRF_K,
    SOURCE_WEIGHTS,
    CandidateSource,
    CitedSourceSpan,
    RetrievalTrace,
)
from cortex.hosted.embeddings import HOSTED_VECTOR_INDEX_CONFIG_VERSION
from cortex.hosted.scopes import ChangedSurface, QueryScope, query_scope_parameters

DECISIONS_FOR_DIFF_RETRIEVAL_CONFIG_VERSION = (
    f"decisions-for-diff-v2+{HOSTED_VECTOR_INDEX_CONFIG_VERSION}"
)
DEFAULT_DECISIONS_FOR_DIFF_LIMIT = 30
MAX_DECISIONS_FOR_DIFF_LIMIT = 30

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_DIFF_STATUSES = frozenset({"candidate", "confirmed"})


class DecisionsForDiffValidationError(ValueError):
    """Raised when a diff retrieval request or candidate is not replayable."""


@dataclass(frozen=True)
class DecisionsForDiffQuery:
    """Inputs needed to retrieve evaluator candidates for a PR diff."""

    tenant_id: str
    changed_surface: ChangedSurface = field(default_factory=ChangedSurface)
    repo_id: str | None = None
    diff_text: str = ""
    visible_source_ids: tuple[str, ...] | None = None
    limit: int = DEFAULT_DECISIONS_FOR_DIFF_LIMIT
    retrieval_config_version: str = DECISIONS_FOR_DIFF_RETRIEVAL_CONFIG_VERSION
    embedding_model_id: str | None = None
    embedding_epoch: str | None = None
    embedding_vector: tuple[float, ...] | None = None
    statuses: tuple[str, ...] = ("candidate", "confirmed")

    @classmethod
    def from_diff_metadata(
        cls,
        *,
        tenant_id: str,
        repo_id: str | None = None,
        changed_paths: Sequence[str] = (),
        symbols: Sequence[str] = (),
        imports: Sequence[str] = (),
        config_keys: Sequence[str] = (),
        issue_refs: Sequence[str] = (),
        package_names: Sequence[str] = (),
        owners: Sequence[str] = (),
        services: Sequence[str] = (),
        diff_text: str = "",
        visible_source_ids: Sequence[str] | None = None,
        limit: int = DEFAULT_DECISIONS_FOR_DIFF_LIMIT,
        embedding_model_id: str | None = None,
        embedding_epoch: str | None = None,
        embedding_vector: Sequence[float] | None = None,
    ) -> DecisionsForDiffQuery:
        packages = tuple(package_names) + tuple(imports)
        return cls(
            tenant_id=tenant_id,
            repo_id=repo_id,
            changed_surface=ChangedSurface(
                paths=tuple(changed_paths),
                symbols=tuple(symbols),
                packages=packages,
                config_keys=tuple(config_keys),
                owners=tuple(owners),
                services=tuple(services),
                issue_refs=tuple(issue_refs),
            ),
            diff_text=diff_text,
            visible_source_ids=None
            if visible_source_ids is None
            else tuple(visible_source_ids),
            limit=limit,
            embedding_model_id=embedding_model_id,
            embedding_epoch=embedding_epoch,
            embedding_vector=None
            if embedding_vector is None
            else tuple(float(value) for value in embedding_vector),
        )

    def __post_init__(self) -> None:
        _require_uuid("tenant_id", self.tenant_id)
        if self.repo_id is not None:
            _require_uuid("repo_id", self.repo_id)
        if self.visible_source_ids is not None:
            for source_id in self.visible_source_ids:
                _require_uuid("visible_source_ids", source_id)
        if not 1 <= self.limit <= MAX_DECISIONS_FOR_DIFF_LIMIT:
            raise DecisionsForDiffValidationError(
                f"limit must be between 1 and {MAX_DECISIONS_FOR_DIFF_LIMIT}"
            )
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        if not self.statuses:
            raise DecisionsForDiffValidationError("statuses must not be empty")
        for status in self.statuses:
            if status not in _DIFF_STATUSES:
                raise DecisionsForDiffValidationError(f"unsupported decision status: {status}")
        has_embedding_meta = self.embedding_model_id is not None or self.embedding_epoch is not None
        has_embedding_vector = self.embedding_vector is not None
        if has_embedding_meta or has_embedding_vector:
            if (
                self.embedding_model_id is None
                or self.embedding_epoch is None
                or self.embedding_vector is None
            ):
                raise DecisionsForDiffValidationError(
                    "embedding_model_id, embedding_epoch, and embedding_vector must be provided together"
                )
            if not self.embedding_vector:
                raise DecisionsForDiffValidationError("embedding_vector must not be empty")
        if not self.query_scopes and not self.diff_text.strip():
            raise DecisionsForDiffValidationError("diff retrieval requires changed surface or diff_text")

    @property
    def query_scopes(self) -> tuple[QueryScope, ...]:
        return self.changed_surface.query_scopes()

    @property
    def query_text(self) -> str:
        if self.diff_text.strip():
            return self.diff_text.strip()
        return " ".join(scope.normalized_value for scope in self.query_scopes)

    @property
    def query_hash(self) -> str:
        return _hash_mapping(
            {
                "changed_surface": _changed_surface_payload(self.changed_surface),
                "diff_text": self.diff_text.strip(),
                "embedding_epoch": self.embedding_epoch,
                "embedding_model_id": self.embedding_model_id,
                "embedding_vector": None
                if self.embedding_vector is None
                else list(self.embedding_vector),
                "limit": self.limit,
                "query_text": self.query_text,
                "repo_id": self.repo_id,
                "retrieval_config_version": self.retrieval_config_version,
                "statuses": _status_values(self.statuses),
                "tenant_id": self.tenant_id,
                "visible_source_ids": None
                if self.visible_source_ids is None
                else list(self.visible_source_ids),
            }
        )

    def as_sql_parameters(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "repo_id": self.repo_id,
            "query": self.query_text,
            "visible_source_ids": (
                None if self.visible_source_ids is None else list(self.visible_source_ids)
            ),
            "limit": self.limit,
            "statuses": _status_values(self.statuses),
            "embedding_model_id": self.embedding_model_id,
            "embedding_epoch": self.embedding_epoch,
            "embedding_vector": None
            if self.embedding_vector is None
            else list(self.embedding_vector),
            **query_scope_parameters(self.query_scopes),
        }


@dataclass(frozen=True)
class DecisionsForDiffCandidate:
    """A bounded decision candidate fed into the evaluator."""

    decision_node_id: str
    decision_version_id: str
    status: str
    decision_text: str
    score: float
    reason_codes: tuple[str, ...]
    cited_spans: tuple[CitedSourceSpan, ...]

    def __post_init__(self) -> None:
        _require_uuid("decision_node_id", self.decision_node_id)
        _require_uuid("decision_version_id", self.decision_version_id)
        if self.status not in _DIFF_STATUSES:
            raise DecisionsForDiffValidationError(f"unsupported decision status: {self.status}")
        _require_non_empty("decision_text", self.decision_text)
        if self.score < 0:
            raise DecisionsForDiffValidationError("score must be >= 0")
        if not self.reason_codes:
            raise DecisionsForDiffValidationError("candidate must include at least one reason code")
        for reason in self.reason_codes:
            _require_non_empty("reason_code", reason)
        if not self.cited_spans:
            raise DecisionsForDiffValidationError("diff candidates must include citations")

    def as_trace_payload(self) -> dict[str, object]:
        return {
            "decision_node_id": self.decision_node_id,
            "decision_version_id": self.decision_version_id,
            "reason_codes": list(self.reason_codes),
            "score": self.score,
            "source_span_hashes": [span.span_hash for span in self.cited_spans],
            "status": self.status,
        }

    def as_context_payload(self) -> dict[str, object]:
        return {
            **self.as_trace_payload(),
            "citations": [span.as_payload() for span in self.cited_spans],
            "decision_text": self.decision_text,
        }


@dataclass(frozen=True)
class DecisionsForDiffCandidatePack:
    """Bounded evaluator input plus retrieval metrics for a PR diff."""

    query_hash: str
    retrieval_config_version: str
    graph_snapshot_hash: str
    candidates: tuple[DecisionsForDiffCandidate, ...]
    omitted_counts: Mapping[str, int]
    graph_node_count: int
    candidate_pool_size: int

    def __post_init__(self) -> None:
        _validate_hash("query_hash", self.query_hash)
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        _validate_hash("graph_snapshot_hash", self.graph_snapshot_hash)
        if self.graph_node_count < 0:
            raise DecisionsForDiffValidationError("graph_node_count must be >= 0")
        if self.candidate_pool_size < 0:
            raise DecisionsForDiffValidationError("candidate_pool_size must be >= 0")
        if self.candidate_pool_size < len(self.candidates):
            raise DecisionsForDiffValidationError(
                "candidate_pool_size must be >= candidate count"
            )
        _validate_json_object("omitted_counts", self.omitted_counts)
        object.__setattr__(self, "omitted_counts", MappingProxyType(dict(self.omitted_counts)))

    @property
    def candidate_set_hash(self) -> str:
        return _hash_mapping(
            {
                "candidates": [candidate.as_trace_payload() for candidate in self.candidates],
                "query_hash": self.query_hash,
                "retrieval_config_version": self.retrieval_config_version,
            }
        )

    @property
    def candidate_growth_ratio(self) -> float:
        if self.graph_node_count == 0:
            return 0.0
        return self.candidate_pool_size / self.graph_node_count

    @property
    def reason_codes(self) -> dict[str, list[str]]:
        return {
            candidate.decision_node_id: list(candidate.reason_codes)
            for candidate in self.candidates
        }

    def as_trace(self) -> RetrievalTrace:
        return RetrievalTrace(
            tenant_id=None,
            graph_snapshot_hash=self.graph_snapshot_hash,
            retrieval_config_version=self.retrieval_config_version,
            query_kind="decisions_for_diff",
            query_input_hash=self.query_hash,
            candidate_set_hash=self.candidate_set_hash,
            candidates=tuple(candidate.as_trace_payload() for candidate in self.candidates),
            omitted_counts={
                **dict(self.omitted_counts),
                "candidate_pool_size": self.candidate_pool_size,
                "graph_node_count": self.graph_node_count,
            },
            reason_codes=self.reason_codes,
        )


def build_decisions_for_diff_candidate_pack(
    *,
    query: DecisionsForDiffQuery,
    graph_snapshot_hash: str,
    rows: Iterable[Mapping[str, object]],
) -> DecisionsForDiffCandidatePack:
    """Build a bounded evaluator candidate pack from retrieval rows."""

    materialized = tuple(rows)
    candidates = tuple(_candidate_from_diff_row(row) for row in materialized[: query.limit])
    candidate_pool_size = _first_row_int(materialized, "candidate_pool_size", len(materialized))
    graph_node_count = _first_row_int(materialized, "graph_node_count", 0)
    omitted_counts = {
        "over_limit": max(0, candidate_pool_size - len(candidates)),
    }
    return DecisionsForDiffCandidatePack(
        query_hash=query.query_hash,
        retrieval_config_version=query.retrieval_config_version,
        graph_snapshot_hash=graph_snapshot_hash,
        candidates=candidates,
        omitted_counts=omitted_counts,
        graph_node_count=graph_node_count,
        candidate_pool_size=candidate_pool_size,
    )


def decisions_for_diff_retrieval_sql(schema: str = "cortex_hosted") -> str:
    """Return hybrid retrieval SQL for `decisions_for_diff` evaluator input."""

    _validate_sql_identifier(schema)
    scope_weight = SOURCE_WEIGHTS[CandidateSource.SCOPE]
    full_text_weight = SOURCE_WEIGHTS[CandidateSource.FULL_TEXT]
    trigram_weight = SOURCE_WEIGHTS[CandidateSource.TRIGRAM]
    vector_weight = SOURCE_WEIGHTS[CandidateSource.VECTOR]
    graph_weight = SOURCE_WEIGHTS[CandidateSource.GRAPH]
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
lexical_query AS (
    SELECT NULLIF(%(query)s, '') AS query_text
),
visible_docs AS (
    SELECT source_document_id, source_id
    FROM {schema}.source_documents
    WHERE tenant_id = %(tenant_id)s
      AND (
          %(visible_source_ids)s::uuid[] IS NULL
          OR source_id = ANY(%(visible_source_ids)s::uuid[])
      )
),
base_versions AS (
    SELECT
        node.decision_node_id,
        version.decision_version_id,
        node.status,
        version.decision_text,
        version.source_span_hashes,
        node.updated_at
    FROM {schema}.decision_nodes AS node
    JOIN {schema}.decision_versions AS version
      ON version.tenant_id = node.tenant_id
     AND version.decision_node_id = node.decision_node_id
     AND version.decision_version_id = node.current_version_id
    WHERE node.tenant_id = %(tenant_id)s
      AND (%(repo_id)s::uuid IS NULL OR node.repo_id IS NULL OR node.repo_id = %(repo_id)s::uuid)
      AND node.status = ANY(%(statuses)s::text[])
),
graph_size AS (
    SELECT count(*)::integer AS graph_node_count
    FROM {schema}.decision_nodes AS node
    WHERE node.tenant_id = %(tenant_id)s
      AND (%(repo_id)s::uuid IS NULL OR node.repo_id IS NULL OR node.repo_id = %(repo_id)s::uuid)
      AND node.status = ANY(%(statuses)s::text[])
),
scope_candidates AS (
    SELECT DISTINCT ON (version.decision_node_id)
        version.decision_node_id,
        'scope'::text AS source,
        row_number() OVER (ORDER BY q.structural_weight DESC, version.updated_at DESC) AS source_rank,
        q.reason_code
    FROM query_scopes AS q
    JOIN {schema}.decision_scopes AS scope
      ON scope.tenant_id = %(tenant_id)s
     AND scope.scope_type = q.scope_type
     AND scope.normalized_value = q.normalized_value
     AND (%(repo_id)s::uuid IS NULL OR scope.repo_id IS NULL OR scope.repo_id = %(repo_id)s::uuid)
    JOIN base_versions AS version
      ON version.decision_node_id = scope.decision_node_id
    ORDER BY version.decision_node_id, q.structural_weight DESC, version.updated_at DESC
),
fts_candidates AS (
    SELECT
        version.decision_node_id,
        'full_text'::text AS source,
        row_number() OVER (
            ORDER BY ts_rank_cd(to_tsvector('english', version.decision_text), websearch_to_tsquery('english', query.query_text)) DESC
        ) AS source_rank,
        'full_text:decision_text'::text AS reason_code
    FROM base_versions AS version
    CROSS JOIN lexical_query AS query
    WHERE query.query_text IS NOT NULL
      AND to_tsvector('english', version.decision_text) @@ websearch_to_tsquery('english', query.query_text)
),
trigram_candidates AS (
    SELECT
        version.decision_node_id,
        'trigram'::text AS source,
        row_number() OVER (ORDER BY similarity(version.decision_text, query.query_text) DESC) AS source_rank,
        'trigram:decision_text'::text AS reason_code
    FROM base_versions AS version
    CROSS JOIN lexical_query AS query
    WHERE query.query_text IS NOT NULL
      AND similarity(version.decision_text, query.query_text) >= 0.2
),
vector_candidates AS (
    SELECT
        version.decision_node_id,
        'vector'::text AS source,
        row_number() OVER (ORDER BY embedding.embedding <=> %(embedding_vector)s::vector) AS source_rank,
        'vector:decision_embedding'::text AS reason_code
    FROM base_versions AS version
    JOIN {schema}.embeddings AS embedding
      ON embedding.tenant_id = %(tenant_id)s
     AND embedding.item_type = 'decision_version'
     AND embedding.item_id = version.decision_version_id
     AND embedding.embedding_model_id = %(embedding_model_id)s
     AND embedding.embedding_dimension = vector_dims(%(embedding_vector)s::vector)
     AND embedding.embedding_epoch = %(embedding_epoch)s
    WHERE %(embedding_vector)s::vector IS NOT NULL
),
seed_candidates AS (
    SELECT * FROM scope_candidates
    UNION ALL SELECT * FROM fts_candidates
    UNION ALL SELECT * FROM trigram_candidates
    UNION ALL SELECT * FROM vector_candidates
),
graph_candidates AS (
    SELECT
        neighbor.decision_node_id,
        'graph'::text AS source,
        row_number() OVER (ORDER BY seed.source_rank ASC, neighbor.updated_at DESC) AS source_rank,
        'graph:' || edge.edge_type AS reason_code
    FROM seed_candidates AS seed
    JOIN {schema}.decision_edges AS edge
      ON edge.tenant_id = %(tenant_id)s
     AND edge.edge_type IN ('duplicates', 'refines', 'supersedes', 'contradicts', 'derived_from', 'mentioned_with')
     AND (edge.from_node_id = seed.decision_node_id OR edge.to_node_id = seed.decision_node_id)
    JOIN base_versions AS neighbor
      ON neighbor.decision_node_id = CASE
          WHEN edge.from_node_id = seed.decision_node_id THEN edge.to_node_id
          ELSE edge.from_node_id
      END
),
all_candidates AS (
    SELECT * FROM seed_candidates
    UNION ALL SELECT * FROM graph_candidates
),
fused AS (
    SELECT
        decision_node_id,
        sum(
            CASE source
                WHEN 'scope' THEN {scope_weight}.0
                WHEN 'full_text' THEN {full_text_weight}.0
                WHEN 'trigram' THEN {trigram_weight}.0
                WHEN 'vector' THEN {vector_weight}.0
                WHEN 'graph' THEN {graph_weight}.0
            END / ({RRF_K}.0 + source_rank)
        ) AS fused_score,
        array_agg(DISTINCT reason_code ORDER BY reason_code) AS reason_codes
    FROM all_candidates
    GROUP BY decision_node_id
),
cited_fused AS (
    SELECT fused.*
    FROM fused
    JOIN base_versions AS version
      ON version.decision_node_id = fused.decision_node_id
    WHERE EXISTS (
        SELECT 1
        FROM {schema}.source_spans AS span
        JOIN visible_docs AS doc
          ON doc.source_document_id = span.source_document_id
        WHERE span.tenant_id = %(tenant_id)s
          AND span.span_hash = ANY(version.source_span_hashes)
    )
),
bounded AS (
    SELECT
        version.decision_node_id,
        version.decision_version_id,
        version.status,
        version.decision_text,
        cited_fused.fused_score,
        cited_fused.reason_codes,
        version.source_span_hashes
    FROM cited_fused
    JOIN base_versions AS version
      ON version.decision_node_id = cited_fused.decision_node_id
    ORDER BY cited_fused.fused_score DESC
    LIMIT %(limit)s
),
span_rows AS (
    SELECT
        bounded.decision_node_id,
        jsonb_agg(
            jsonb_build_object(
                'span_hash', span.span_hash,
                'excerpt', span.excerpt,
                'permalink', span.permalink,
                'source_document_id', span.source_document_id,
                'source_id', doc.source_id
            )
            ORDER BY array_position(bounded.source_span_hashes, span.span_hash)
        ) AS cited_spans
    FROM bounded
    JOIN {schema}.source_spans AS span
      ON span.tenant_id = %(tenant_id)s
     AND span.span_hash = ANY(bounded.source_span_hashes)
    JOIN visible_docs AS doc
      ON doc.source_document_id = span.source_document_id
    GROUP BY bounded.decision_node_id
)
SELECT
    bounded.decision_node_id::text AS decision_node_id,
    bounded.decision_version_id::text AS decision_version_id,
    bounded.status,
    bounded.decision_text,
    bounded.fused_score,
    bounded.reason_codes,
    COALESCE(span_rows.cited_spans, '[]'::jsonb) AS cited_spans,
    (SELECT count(*)::integer FROM cited_fused) AS candidate_pool_size,
    (SELECT graph_node_count FROM graph_size) AS graph_node_count
FROM bounded
LEFT JOIN span_rows
  ON span_rows.decision_node_id = bounded.decision_node_id
ORDER BY bounded.fused_score DESC;
""".strip()


def _candidate_from_diff_row(row: Mapping[str, object]) -> DecisionsForDiffCandidate:
    return DecisionsForDiffCandidate(
        decision_node_id=_required_string(row, "decision_node_id"),
        decision_version_id=_required_string(row, "decision_version_id"),
        status=_required_string(row, "status"),
        decision_text=_required_string(row, "decision_text"),
        score=_required_float(row, "fused_score"),
        reason_codes=_required_string_sequence(row, "reason_codes"),
        cited_spans=tuple(
            CitedSourceSpan(
                span_hash=_required_string(span, "span_hash"),
                excerpt=_required_string(span, "excerpt"),
                permalink=_required_string(span, "permalink"),
                source_document_id=_required_string(span, "source_document_id"),
                source_id=_required_string(span, "source_id"),
            )
            for span in _required_mapping_sequence(row, "cited_spans")
        ),
    )


def _changed_surface_payload(surface: ChangedSurface) -> dict[str, list[str]]:
    return {
        "config_keys": list(surface.config_keys),
        "globs": list(surface.globs),
        "issue_refs": list(surface.issue_refs),
        "owners": list(surface.owners),
        "packages": list(surface.packages),
        "paths": list(surface.paths),
        "services": list(surface.services),
        "symbols": list(surface.symbols),
    }


def _status_values(statuses: Sequence[str]) -> list[str]:
    return sorted(set(statuses))


def _first_row_int(rows: Sequence[Mapping[str, object]], name: str, default: int) -> int:
    if not rows:
        return default
    value = rows[0].get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        raise DecisionsForDiffValidationError(f"{name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise DecisionsForDiffValidationError(f"{name} must be an integer") from exc
    else:
        raise DecisionsForDiffValidationError(f"{name} must be an integer")
    if parsed < 0:
        raise DecisionsForDiffValidationError(f"{name} must be >= 0")
    return parsed


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise DecisionsForDiffValidationError(f"{name} must not be empty")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise DecisionsForDiffValidationError(f"{name} must be a UUID") from exc


def _validate_hash(name: str, value: str) -> None:
    if not _SHA256_RE.match(value):
        raise DecisionsForDiffValidationError(f"{name} must be a sha256 hex string")


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise DecisionsForDiffValidationError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise DecisionsForDiffValidationError(f"{name} must be JSON-serializable") from exc


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise DecisionsForDiffValidationError(f"invalid SQL identifier: {name!r}")


def _required_string(row: Mapping[str, object], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str):
        raise DecisionsForDiffValidationError(f"{name} must be a string")
    return value


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row.get(name)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise DecisionsForDiffValidationError(f"{name} must be numeric") from exc


def _required_string_sequence(row: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = row.get(name)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise DecisionsForDiffValidationError(f"{name} must be a string array")
    items = tuple(value)
    if not all(isinstance(item, str) for item in items):
        raise DecisionsForDiffValidationError(f"{name} must be a string array")
    return cast(tuple[str, ...], items)


def _required_mapping_sequence(
    row: Mapping[str, object], name: str
) -> tuple[Mapping[str, object], ...]:
    value = row.get(name)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise DecisionsForDiffValidationError(f"{name} must be an object array")
    items = tuple(value)
    if not all(isinstance(item, Mapping) for item in items):
        raise DecisionsForDiffValidationError(f"{name} must be an object array")
    return cast(tuple[Mapping[str, object], ...], items)
