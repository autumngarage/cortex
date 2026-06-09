"""Hybrid `ask_ledger` retrieval boundary for hosted Cortex."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

from cortex.hosted.embeddings import HOSTED_VECTOR_INDEX_CONFIG_VERSION
from cortex.hosted.scopes import QueryScope, query_scope_parameters

ASK_LEDGER_RETRIEVAL_CONFIG_VERSION = f"ask-ledger-v2+{HOSTED_VECTOR_INDEX_CONFIG_VERSION}"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class AskLedgerValidationError(ValueError):
    """Raised when an ask-ledger request or candidate cannot be replayed."""


class CandidateSource(StrEnum):
    EXACT = "exact"
    SCOPE = "scope"
    FULL_TEXT = "full_text"
    TRIGRAM = "trigram"
    VECTOR = "vector"
    GRAPH = "graph"


class AnswerState(StrEnum):
    READY = "ready"
    NO_ANSWER = "no_answer"


SOURCE_WEIGHTS: dict[CandidateSource, int] = {
    CandidateSource.EXACT: 120,
    CandidateSource.SCOPE: 100,
    CandidateSource.FULL_TEXT: 70,
    CandidateSource.TRIGRAM: 55,
    CandidateSource.VECTOR: 50,
    CandidateSource.GRAPH: 35,
}
RRF_K = 60


@dataclass(frozen=True)
class AskLedgerQuery:
    """Inputs needed to retrieve cited ledger context."""

    tenant_id: str
    query: str
    repo_id: str | None = None
    query_scopes: tuple[QueryScope, ...] = ()
    visible_source_ids: tuple[str, ...] | None = None
    exact_refs: tuple[str, ...] = ()
    limit: int = 10
    retrieval_config_version: str = ASK_LEDGER_RETRIEVAL_CONFIG_VERSION
    embedding_model_id: str | None = None
    embedding_epoch: str | None = None
    embedding_vector: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        _require_uuid("tenant_id", self.tenant_id)
        if self.repo_id is not None:
            _require_uuid("repo_id", self.repo_id)
        if self.visible_source_ids is not None:
            for source_id in self.visible_source_ids:
                _require_uuid("visible_source_ids", source_id)
        if not self.query.strip():
            raise AskLedgerValidationError("query must not be empty")
        if self.limit < 1:
            raise AskLedgerValidationError("limit must be >= 1")
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        has_embedding_meta = self.embedding_model_id is not None or self.embedding_epoch is not None
        has_embedding_vector = self.embedding_vector is not None
        if has_embedding_meta or has_embedding_vector:
            if (
                self.embedding_model_id is None
                or self.embedding_epoch is None
                or self.embedding_vector is None
            ):
                raise AskLedgerValidationError(
                    "embedding_model_id, embedding_epoch, and embedding_vector must be provided together"
                )
            if not self.embedding_vector:
                raise AskLedgerValidationError("embedding_vector must not be empty")

    @property
    def query_hash(self) -> str:
        return _hash_mapping(
            {
                "exact_refs": list(self.exact_refs),
                "embedding_epoch": self.embedding_epoch,
                "embedding_model_id": self.embedding_model_id,
                "embedding_vector": None
                if self.embedding_vector is None
                else list(self.embedding_vector),
                "limit": self.limit,
                "query": self.query.strip(),
                "repo_id": self.repo_id,
                "retrieval_config_version": self.retrieval_config_version,
                "scopes": [
                    {"scope_type": scope.scope_type.value, "value": scope.normalized_value}
                    for scope in self.query_scopes
                ],
                "tenant_id": self.tenant_id,
                "visible_source_ids": None
                if self.visible_source_ids is None
                else list(self.visible_source_ids),
            }
        )

    def as_sql_parameters(self) -> dict[str, object]:
        scope_params = query_scope_parameters(self.query_scopes)
        return {
            "tenant_id": self.tenant_id,
            "repo_id": self.repo_id,
            "query": self.query.strip(),
            "exact_refs": list(self.exact_refs),
            "visible_source_ids": (
                None if self.visible_source_ids is None else list(self.visible_source_ids)
            ),
            "limit": self.limit,
            "embedding_model_id": self.embedding_model_id,
            "embedding_epoch": self.embedding_epoch,
            "embedding_vector": None
            if self.embedding_vector is None
            else list(self.embedding_vector),
            **scope_params,
        }


@dataclass(frozen=True)
class CitedSourceSpan:
    """Cited source material for an answer context pack."""

    span_hash: str
    excerpt: str
    permalink: str
    source_document_id: str
    source_id: str

    def __post_init__(self) -> None:
        _validate_hash("span_hash", self.span_hash)
        _require_non_empty("excerpt", self.excerpt)
        _require_non_empty("permalink", self.permalink)
        _require_uuid("source_document_id", self.source_document_id)
        _require_uuid("source_id", self.source_id)

    def as_payload(self) -> dict[str, str]:
        return {
            "excerpt": self.excerpt,
            "permalink": self.permalink,
            "source_document_id": self.source_document_id,
            "source_id": self.source_id,
            "span_hash": self.span_hash,
        }


@dataclass(frozen=True)
class AskLedgerCandidate:
    """Candidate decision with cited context and retrieval reasons."""

    decision_node_id: str
    decision_version_id: str
    decision_text: str
    score: float
    reason_codes: tuple[str, ...]
    cited_spans: tuple[CitedSourceSpan, ...]

    def __post_init__(self) -> None:
        _require_uuid("decision_node_id", self.decision_node_id)
        _require_uuid("decision_version_id", self.decision_version_id)
        _require_non_empty("decision_text", self.decision_text)
        if self.score < 0:
            raise AskLedgerValidationError("score must be >= 0")
        if not self.reason_codes:
            raise AskLedgerValidationError("candidate must include at least one reason code")
        for reason in self.reason_codes:
            _require_non_empty("reason_code", reason)

    @property
    def has_citations(self) -> bool:
        return bool(self.cited_spans)

    def as_trace_payload(self) -> dict[str, object]:
        return {
            "decision_node_id": self.decision_node_id,
            "decision_version_id": self.decision_version_id,
            "reason_codes": list(self.reason_codes),
            "score": self.score,
            "source_span_hashes": [span.span_hash for span in self.cited_spans],
        }

    def as_context_payload(self) -> dict[str, object]:
        return {
            **self.as_trace_payload(),
            "decision_text": self.decision_text,
            "citations": [span.as_payload() for span in self.cited_spans],
        }


@dataclass(frozen=True)
class CitedContextPack:
    """Bounded cited context returned by `ask_ledger` before answer synthesis."""

    query_hash: str
    retrieval_config_version: str
    graph_snapshot_hash: str
    candidates: tuple[AskLedgerCandidate, ...]
    omitted_counts: Mapping[str, int] = field(default_factory=dict)
    answer_state: AnswerState = AnswerState.READY
    no_answer_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_hash("query_hash", self.query_hash)
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        _validate_hash("graph_snapshot_hash", self.graph_snapshot_hash)
        _validate_json_object("omitted_counts", self.omitted_counts)
        if self.answer_state is AnswerState.READY and not self.candidates:
            raise AskLedgerValidationError("ready context pack requires cited candidates")
        if any(not candidate.has_citations for candidate in self.candidates):
            raise AskLedgerValidationError("context pack candidates must include citations")
        if self.answer_state is AnswerState.NO_ANSWER and self.no_answer_reason is None:
            raise AskLedgerValidationError("no-answer context pack requires a reason")
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
            query_input_hash=self.query_hash,
            candidate_set_hash=self.candidate_set_hash,
            candidates=tuple(candidate.as_trace_payload() for candidate in self.candidates),
            omitted_counts=dict(self.omitted_counts),
            reason_codes=self.reason_codes,
        )


@dataclass(frozen=True)
class RetrievalTrace:
    """Replay/debug trace for an ask-ledger candidate set."""

    graph_snapshot_hash: str
    retrieval_config_version: str
    query_input_hash: str
    candidate_set_hash: str
    candidates: tuple[Mapping[str, object], ...]
    omitted_counts: Mapping[str, int]
    reason_codes: Mapping[str, Sequence[str]]
    tenant_id: str | None = None
    query_kind: str = "ask_ledger"

    def __post_init__(self) -> None:
        if self.tenant_id is not None:
            _require_uuid("tenant_id", self.tenant_id)
        _validate_hash("graph_snapshot_hash", self.graph_snapshot_hash)
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        _validate_hash("query_input_hash", self.query_input_hash)
        _validate_hash("candidate_set_hash", self.candidate_set_hash)
        _validate_json_array("candidates", self.candidates)
        _validate_json_object("omitted_counts", self.omitted_counts)
        _validate_json_object("reason_codes", self.reason_codes)

    def as_insert_parameters(self, *, tenant_id: str | None = None) -> dict[str, object]:
        resolved_tenant = tenant_id or self.tenant_id
        if resolved_tenant is None:
            raise AskLedgerValidationError("tenant_id is required to insert a retrieval trace")
        _require_uuid("tenant_id", resolved_tenant)
        return {
            "tenant_id": resolved_tenant,
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "retrieval_config_version": self.retrieval_config_version,
            "query_kind": self.query_kind,
            "query_input_hash": self.query_input_hash,
            "candidate_set_hash": self.candidate_set_hash,
            "candidates": json.dumps(list(self.candidates), sort_keys=True, separators=(",", ":")),
            "omitted_counts": json.dumps(
                dict(self.omitted_counts), sort_keys=True, separators=(",", ":")
            ),
            "reason_codes": json.dumps(
                {key: list(value) for key, value in self.reason_codes.items()},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }


@dataclass(frozen=True)
class SourceRank:
    """A source-specific candidate rank before rank fusion."""

    decision_node_id: str
    source: CandidateSource
    rank: int
    reason_code: str

    def __post_init__(self) -> None:
        _require_uuid("decision_node_id", self.decision_node_id)
        if self.rank < 1:
            raise AskLedgerValidationError("rank must be >= 1")
        _require_non_empty("reason_code", self.reason_code)


@dataclass(frozen=True)
class FusedRank:
    decision_node_id: str
    score: float
    reason_codes: tuple[str, ...]


def reciprocal_rank_fusion(ranks: Iterable[SourceRank], *, k: int = RRF_K) -> tuple[FusedRank, ...]:
    """Fuse exact/scope/lexical/vector/graph rankings with source weights."""

    scores: defaultdict[str, float] = defaultdict(float)
    reasons: defaultdict[str, list[str]] = defaultdict(list)
    for row in ranks:
        scores[row.decision_node_id] += SOURCE_WEIGHTS[row.source] / (k + row.rank)
        if row.reason_code not in reasons[row.decision_node_id]:
            reasons[row.decision_node_id].append(row.reason_code)
    return tuple(
        FusedRank(
            decision_node_id=decision_node_id,
            score=score,
            reason_codes=tuple(reasons[decision_node_id]),
        )
        for decision_node_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    )


def build_cited_context_pack(
    *,
    query_hash: str,
    retrieval_config_version: str,
    graph_snapshot_hash: str,
    candidates: Iterable[AskLedgerCandidate],
    limit: int,
) -> CitedContextPack:
    """Build a bounded context pack and fail closed when citations are absent."""

    if limit < 1:
        raise AskLedgerValidationError("limit must be >= 1")
    ordered = tuple(sorted(candidates, key=lambda candidate: candidate.score, reverse=True))
    cited = tuple(candidate for candidate in ordered if candidate.has_citations)
    omitted_counts = {
        "missing_citations": len(ordered) - len(cited),
        "over_limit": max(0, len(cited) - limit),
    }
    bounded = cited[:limit]
    if not bounded:
        return CitedContextPack(
            query_hash=query_hash,
            retrieval_config_version=retrieval_config_version,
            graph_snapshot_hash=graph_snapshot_hash,
            candidates=(),
            omitted_counts=omitted_counts,
            answer_state=AnswerState.NO_ANSWER,
            no_answer_reason="no_cited_support",
        )
    return CitedContextPack(
        query_hash=query_hash,
        retrieval_config_version=retrieval_config_version,
        graph_snapshot_hash=graph_snapshot_hash,
        candidates=bounded,
        omitted_counts=omitted_counts,
    )


def build_ask_ledger_context_pack(
    *,
    query: AskLedgerQuery,
    graph_snapshot_hash: str,
    rows: Iterable[Mapping[str, object]],
) -> CitedContextPack:
    """Build an ask-ledger context pack from `ask_ledger_retrieval_sql` rows."""

    return build_cited_context_pack(
        query_hash=query.query_hash,
        retrieval_config_version=query.retrieval_config_version,
        graph_snapshot_hash=graph_snapshot_hash,
        candidates=(_candidate_from_retrieval_row(row) for row in rows),
        limit=query.limit,
    )


def ask_ledger_retrieval_sql(schema: str = "cortex_hosted") -> str:
    """Return hybrid retrieval SQL for `ask_ledger`.

    The query produces cited candidates only. It includes exact/link, scope,
    full-text, trigram, optional vector, and one-hop graph expansion sources.
    Embeddings are a rebuildable projection keyed by model, dimension, and
    epoch; callers pass NULL embedding parameters when vector search is absent.
    """

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
      AND node.status = 'confirmed'
),
exact_candidates AS (
    SELECT
        version.decision_node_id,
        'exact'::text AS source,
        row_number() OVER (ORDER BY version.updated_at DESC) AS source_rank,
        'exact:ref'::text AS reason_code
    FROM base_versions AS version
    JOIN {schema}.source_spans AS span
      ON span.tenant_id = %(tenant_id)s
     AND span.span_hash = ANY(version.source_span_hashes)
    JOIN visible_docs AS doc
      ON doc.source_document_id = span.source_document_id
    WHERE version.decision_node_id::text = ANY(%(exact_refs)s::text[])
       OR version.decision_version_id::text = ANY(%(exact_refs)s::text[])
       OR span.permalink = ANY(%(exact_refs)s::text[])
       OR span.span_hash = ANY(%(exact_refs)s::text[])
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
            ORDER BY ts_rank_cd(to_tsvector('english', version.decision_text), websearch_to_tsquery('english', %(query)s)) DESC
        ) AS source_rank,
        'full_text:decision_text'::text AS reason_code
    FROM base_versions AS version
    WHERE to_tsvector('english', version.decision_text) @@ websearch_to_tsquery('english', %(query)s)
),
trigram_candidates AS (
    SELECT
        version.decision_node_id,
        'trigram'::text AS source,
        row_number() OVER (ORDER BY similarity(version.decision_text, %(query)s) DESC) AS source_rank,
        'trigram:decision_text'::text AS reason_code
    FROM base_versions AS version
    WHERE similarity(version.decision_text, %(query)s) >= 0.2
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
    SELECT * FROM exact_candidates
    UNION ALL SELECT * FROM scope_candidates
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
     AND edge.edge_type IN ('duplicates', 'refines', 'supersedes', 'contradicts')
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
                WHEN 'exact' THEN 120.0
                WHEN 'scope' THEN 100.0
                WHEN 'full_text' THEN 70.0
                WHEN 'trigram' THEN 55.0
                WHEN 'vector' THEN 50.0
                WHEN 'graph' THEN 35.0
            END / (60.0 + source_rank)
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
    bounded.decision_text,
    bounded.fused_score,
    bounded.reason_codes,
    COALESCE(span_rows.cited_spans, '[]'::jsonb) AS cited_spans
FROM bounded
LEFT JOIN span_rows
  ON span_rows.decision_node_id = bounded.decision_node_id
ORDER BY bounded.fused_score DESC;
""".strip()


def retrieval_trace_insert_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.retrieval_traces (
    tenant_id,
    graph_snapshot_hash,
    retrieval_config_version,
    query_kind,
    query_input_hash,
    candidate_set_hash,
    candidates,
    omitted_counts,
    reason_codes
) VALUES (
    %(tenant_id)s,
    %(graph_snapshot_hash)s,
    %(retrieval_config_version)s,
    %(query_kind)s,
    %(query_input_hash)s,
    %(candidate_set_hash)s,
    %(candidates)s::jsonb,
    %(omitted_counts)s::jsonb,
    %(reason_codes)s::jsonb
)
RETURNING retrieval_trace_id;
""".strip()


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise AskLedgerValidationError(f"{name} must not be empty")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise AskLedgerValidationError(f"{name} must be a UUID") from exc


def _validate_hash(name: str, value: str) -> None:
    if not _SHA256_RE.match(value):
        raise AskLedgerValidationError(f"{name} must be a sha256 hex string")


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise AskLedgerValidationError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AskLedgerValidationError(f"{name} must be JSON-serializable") from exc


def _validate_json_array(name: str, value: Sequence[Mapping[str, object]]) -> None:
    try:
        json.dumps(list(value), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AskLedgerValidationError(f"{name} must be JSON-serializable") from exc


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise AskLedgerValidationError(f"invalid SQL identifier: {name!r}")


def _candidate_from_retrieval_row(row: Mapping[str, object]) -> AskLedgerCandidate:
    return AskLedgerCandidate(
        decision_node_id=_required_string(row, "decision_node_id"),
        decision_version_id=_required_string(row, "decision_version_id"),
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


def _required_string(row: Mapping[str, object], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str):
        raise AskLedgerValidationError(f"{name} must be a string")
    return value


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row.get(name)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise AskLedgerValidationError(f"{name} must be numeric") from exc


def _required_string_sequence(row: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = row.get(name)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise AskLedgerValidationError(f"{name} must be a string array")
    items = tuple(value)
    if not all(isinstance(item, str) for item in items):
        raise AskLedgerValidationError(f"{name} must be a string array")
    return cast(tuple[str, ...], items)


def _required_mapping_sequence(
    row: Mapping[str, object], name: str
) -> tuple[Mapping[str, object], ...]:
    value = row.get(name)
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise AskLedgerValidationError(f"{name} must be an object array")
    items = tuple(value)
    if not all(isinstance(item, Mapping) for item in items):
        raise AskLedgerValidationError(f"{name} must be an object array")
    return cast(tuple[Mapping[str, object], ...], items)
