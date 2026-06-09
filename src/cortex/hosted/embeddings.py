"""Hosted embedding projection and vector recall boundaries."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

DEFAULT_HNSW_MIN_ROWS = 1_000
DEFAULT_HNSW_M = 16
DEFAULT_HNSW_EF_CONSTRUCTION = 64
DEFAULT_HNSW_EF_SEARCH = 40
DEFAULT_VECTOR_RECALL_FLOOR = 0.95


class HostedEmbeddingValidationError(ValueError):
    """Raised when a hosted embedding projection is not rebuildable."""


class EmbeddingItemType(StrEnum):
    """Hosted projection item kinds that can be rebuilt from canonical rows."""

    DECISION_VERSION = "decision_version"
    SOURCE_SPAN = "source_span"


class VectorMetric(StrEnum):
    """Distance metrics supported by the hosted vector projection."""

    COSINE = "cosine"


@dataclass(frozen=True)
class VectorIndexConfig:
    """Versioned approximate-search settings recorded with retrieval traces."""

    metric: VectorMetric = VectorMetric.COSINE
    min_rows_for_hnsw: int = DEFAULT_HNSW_MIN_ROWS
    hnsw_m: int = DEFAULT_HNSW_M
    hnsw_ef_construction: int = DEFAULT_HNSW_EF_CONSTRUCTION
    hnsw_ef_search: int = DEFAULT_HNSW_EF_SEARCH
    version_prefix: str = "pgvector-hnsw-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric", VectorMetric(self.metric))
        for name, value in (
            ("min_rows_for_hnsw", self.min_rows_for_hnsw),
            ("hnsw_m", self.hnsw_m),
            ("hnsw_ef_construction", self.hnsw_ef_construction),
            ("hnsw_ef_search", self.hnsw_ef_search),
        ):
            if value < 1:
                raise HostedEmbeddingValidationError(f"{name} must be >= 1")
        if not self.version_prefix.strip():
            raise HostedEmbeddingValidationError("version_prefix must not be empty")

    @property
    def config_version(self) -> str:
        return (
            f"{self.version_prefix}-{self.metric.value}"
            f"-m{self.hnsw_m}"
            f"-efc{self.hnsw_ef_construction}"
            f"-efs{self.hnsw_ef_search}"
            f"-min{self.min_rows_for_hnsw}"
        )

    def as_trace_payload(self) -> dict[str, object]:
        return {
            "config_version": self.config_version,
            "hnsw_ef_construction": self.hnsw_ef_construction,
            "hnsw_ef_search": self.hnsw_ef_search,
            "hnsw_m": self.hnsw_m,
            "metric": self.metric.value,
            "min_rows_for_hnsw": self.min_rows_for_hnsw,
        }


DEFAULT_VECTOR_INDEX_CONFIG = VectorIndexConfig()
HOSTED_VECTOR_INDEX_CONFIG_VERSION = DEFAULT_VECTOR_INDEX_CONFIG.config_version


@dataclass(frozen=True)
class EmbeddingProjectionRow:
    """One rebuildable embedding row derived from a canonical hosted item."""

    tenant_id: str
    item_type: EmbeddingItemType
    item_id: str
    item_hash: str
    text: str
    embedding_model_id: str
    embedding_epoch: str
    embedding_vector: tuple[float, ...]
    repo_id: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_uuid("tenant_id", self.tenant_id)
        if self.repo_id is not None:
            _require_uuid("repo_id", self.repo_id)
        object.__setattr__(self, "item_type", EmbeddingItemType(self.item_type))
        _require_uuid("item_id", self.item_id)
        _validate_hash("item_hash", self.item_hash)
        _require_non_empty("text", self.text)
        _require_non_empty("embedding_model_id", self.embedding_model_id)
        _require_non_empty("embedding_epoch", self.embedding_epoch)
        vector = tuple(_coerce_vector_value(value) for value in self.embedding_vector)
        if not vector:
            raise HostedEmbeddingValidationError("embedding_vector must not be empty")
        object.__setattr__(self, "embedding_vector", vector)
        _validate_json_object("metadata", self.metadata)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def embedding_dimension(self) -> int:
        return len(self.embedding_vector)

    @property
    def projection_hash(self) -> str:
        return _hash_mapping(
            {
                "embedding_dimension": self.embedding_dimension,
                "embedding_epoch": self.embedding_epoch,
                "embedding_model_id": self.embedding_model_id,
                "item_hash": self.item_hash,
                "item_id": self.item_id,
                "item_type": self.item_type.value,
                "tenant_id": self.tenant_id,
            }
        )

    def as_upsert_parameters(self) -> dict[str, object]:
        return {
            "embedding_dimension": self.embedding_dimension,
            "embedding_epoch": self.embedding_epoch,
            "embedding_model_id": self.embedding_model_id,
            "embedding_vector": list(self.embedding_vector),
            "item_hash": self.item_hash,
            "item_id": self.item_id,
            "item_type": self.item_type.value,
            "metadata": json.dumps(
                dict(self.metadata),
                sort_keys=True,
                separators=(",", ":"),
            ),
            "repo_id": self.repo_id,
            "tenant_id": self.tenant_id,
        }


@dataclass(frozen=True)
class VectorRecallSample:
    """Exact-vs-approximate vector retrieval result for one sampled query."""

    sample_id: str
    exact_item_ids: tuple[str, ...]
    approximate_item_ids: tuple[str, ...]
    k: int

    def __post_init__(self) -> None:
        _require_non_empty("sample_id", self.sample_id)
        _require_positive("k", self.k)
        if not self.exact_item_ids:
            raise HostedEmbeddingValidationError("exact_item_ids must not be empty")
        for item_id in (*self.exact_item_ids, *self.approximate_item_ids):
            _require_uuid("item_id", item_id)

    @property
    def expected_at_k(self) -> tuple[str, ...]:
        return self.exact_item_ids[: self.k]

    @property
    def observed_at_k(self) -> tuple[str, ...]:
        return self.approximate_item_ids[: self.k]

    @property
    def recall_at_k(self) -> float:
        expected = set(self.expected_at_k)
        if not expected:
            return 1.0
        return len(expected.intersection(self.observed_at_k)) / len(expected)

    @property
    def missing_item_ids(self) -> tuple[str, ...]:
        observed = set(self.observed_at_k)
        return tuple(item_id for item_id in self.expected_at_k if item_id not in observed)

    def as_payload(self) -> dict[str, object]:
        return {
            "approximate_item_ids": list(self.observed_at_k),
            "exact_item_ids": list(self.expected_at_k),
            "k": self.k,
            "missing_item_ids": list(self.missing_item_ids),
            "recall_at_k": self.recall_at_k,
            "sample_id": self.sample_id,
        }


@dataclass(frozen=True)
class VectorRecallReport:
    """Recall gate for approximate vector search against exact search."""

    retrieval_config_version: str
    vector_index_config: VectorIndexConfig
    samples: tuple[VectorRecallSample, ...]
    minimum_required_recall: float = DEFAULT_VECTOR_RECALL_FLOOR

    def __post_init__(self) -> None:
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        if not 0 <= self.minimum_required_recall <= 1:
            raise HostedEmbeddingValidationError("minimum_required_recall must be between 0 and 1")
        if not self.samples:
            raise HostedEmbeddingValidationError("recall report requires at least one sample")

    @property
    def average_recall(self) -> float:
        return sum(sample.recall_at_k for sample in self.samples) / len(self.samples)

    @property
    def worst_recall(self) -> float:
        return min(sample.recall_at_k for sample in self.samples)

    @property
    def passed(self) -> bool:
        return self.worst_recall >= self.minimum_required_recall

    @property
    def failed_samples(self) -> tuple[VectorRecallSample, ...]:
        return tuple(
            sample
            for sample in self.samples
            if sample.recall_at_k < self.minimum_required_recall
        )

    def as_trace_payload(self) -> dict[str, object]:
        return {
            "average_recall": self.average_recall,
            "failed_samples": [sample.as_payload() for sample in self.failed_samples],
            "minimum_required_recall": self.minimum_required_recall,
            "passed": self.passed,
            "retrieval_config_version": self.retrieval_config_version,
            "samples": [sample.as_payload() for sample in self.samples],
            "vector_index_config": self.vector_index_config.as_trace_payload(),
            "worst_recall": self.worst_recall,
        }


def evaluate_vector_recall(
    *,
    retrieval_config_version: str,
    samples: Iterable[VectorRecallSample],
    minimum_required_recall: float = DEFAULT_VECTOR_RECALL_FLOOR,
    vector_index_config: VectorIndexConfig = DEFAULT_VECTOR_INDEX_CONFIG,
) -> VectorRecallReport:
    """Compare approximate vector results to exact results for sampled queries."""

    return VectorRecallReport(
        retrieval_config_version=retrieval_config_version,
        vector_index_config=vector_index_config,
        samples=tuple(samples),
        minimum_required_recall=minimum_required_recall,
    )


def embedding_projection_source_sql(schema: str = "cortex_hosted") -> str:
    """Return rows that need embedding for a model/epoch/dimension projection."""

    _validate_sql_identifier(schema)
    return f"""
WITH projection_sources AS (
{_projection_sources_sql(schema)}
),
existing_embeddings AS (
    SELECT tenant_id, item_type, item_id, item_hash
    FROM {schema}.embeddings
    WHERE tenant_id = %(tenant_id)s
      AND embedding_model_id = %(embedding_model_id)s
      AND embedding_epoch = %(embedding_epoch)s
      AND embedding_dimension = %(embedding_dimension)s::integer
)
SELECT
    source.tenant_id::text AS tenant_id,
    source.repo_id::text AS repo_id,
    source.item_type,
    source.item_id::text AS item_id,
    source.item_hash,
    source.text
FROM projection_sources AS source
LEFT JOIN existing_embeddings AS embedding
  ON embedding.tenant_id = source.tenant_id
 AND embedding.item_type = source.item_type
 AND embedding.item_id = source.item_id
WHERE source.tenant_id = %(tenant_id)s
  AND (%(repo_id)s::uuid IS NULL OR source.repo_id IS NULL OR source.repo_id = %(repo_id)s::uuid)
  AND (%(item_types)s::text[] IS NULL OR source.item_type = ANY(%(item_types)s::text[]))
  AND (embedding.item_id IS NULL OR embedding.item_hash <> source.item_hash)
ORDER BY source.item_type, source.item_id
LIMIT %(limit)s;
""".strip()


def embedding_projection_counts_sql(schema: str = "cortex_hosted") -> str:
    """Return missing/stale/orphan counts for one embedding projection."""

    _validate_sql_identifier(schema)
    return f"""
WITH projection_sources AS (
{_projection_sources_sql(schema)}
),
filtered_sources AS (
    SELECT *
    FROM projection_sources AS source
    WHERE source.tenant_id = %(tenant_id)s
      AND (%(repo_id)s::uuid IS NULL OR source.repo_id IS NULL OR source.repo_id = %(repo_id)s::uuid)
      AND (%(item_types)s::text[] IS NULL OR source.item_type = ANY(%(item_types)s::text[]))
),
target_embeddings AS (
    SELECT *
    FROM {schema}.embeddings AS embedding
    WHERE embedding.tenant_id = %(tenant_id)s
      AND embedding.embedding_model_id = %(embedding_model_id)s
      AND embedding.embedding_epoch = %(embedding_epoch)s
      AND embedding.embedding_dimension = %(embedding_dimension)s::integer
      AND (%(repo_id)s::uuid IS NULL OR embedding.repo_id IS NULL OR embedding.repo_id = %(repo_id)s::uuid)
      AND (%(item_types)s::text[] IS NULL OR embedding.item_type = ANY(%(item_types)s::text[]))
)
SELECT
    count(*) FILTER (WHERE embedding.embedding_id IS NULL)::integer AS missing_count,
    count(*) FILTER (
        WHERE embedding.embedding_id IS NOT NULL
          AND embedding.item_hash <> source.item_hash
    )::integer AS stale_count,
    (
        SELECT count(*)::integer
        FROM target_embeddings AS embedding
        LEFT JOIN filtered_sources AS source
          ON source.tenant_id = embedding.tenant_id
         AND source.item_type = embedding.item_type
         AND source.item_id = embedding.item_id
        WHERE source.item_id IS NULL
    ) AS orphan_count,
    (SELECT count(*)::integer FROM filtered_sources) AS source_count,
    (SELECT count(*)::integer FROM target_embeddings) AS embedding_count
FROM filtered_sources AS source
LEFT JOIN target_embeddings AS embedding
  ON embedding.tenant_id = source.tenant_id
 AND embedding.item_type = source.item_type
 AND embedding.item_id = source.item_id;
""".strip()


def embedding_upsert_sql(schema: str = "cortex_hosted") -> str:
    """Return SQL to upsert a derived hosted embedding projection row."""

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.embeddings (
    tenant_id,
    repo_id,
    item_type,
    item_id,
    item_hash,
    embedding_model_id,
    embedding_dimension,
    embedding_epoch,
    embedding,
    metadata
) VALUES (
    %(tenant_id)s::uuid,
    %(repo_id)s::uuid,
    %(item_type)s,
    %(item_id)s::uuid,
    %(item_hash)s,
    %(embedding_model_id)s,
    %(embedding_dimension)s::integer,
    %(embedding_epoch)s,
    %(embedding_vector)s::vector,
    %(metadata)s::jsonb
)
ON CONFLICT ON CONSTRAINT embeddings_projection_unique DO UPDATE
SET
    item_hash = EXCLUDED.item_hash,
    embedding = EXCLUDED.embedding,
    metadata = EXCLUDED.metadata,
    created_at = now()
RETURNING
    embedding_id::text AS embedding_id,
    item_type,
    item_id::text AS item_id,
    embedding_dimension;
""".strip()


def embedding_delete_orphans_sql(schema: str = "cortex_hosted") -> str:
    """Return SQL to remove derived embeddings whose canonical item disappeared."""

    _validate_sql_identifier(schema)
    return f"""
WITH projection_sources AS (
{_projection_sources_sql(schema)}
)
DELETE FROM {schema}.embeddings AS embedding
WHERE embedding.tenant_id = %(tenant_id)s
  AND embedding.embedding_model_id = %(embedding_model_id)s
  AND embedding.embedding_epoch = %(embedding_epoch)s
  AND embedding.embedding_dimension = %(embedding_dimension)s::integer
  AND (%(repo_id)s::uuid IS NULL OR embedding.repo_id IS NULL OR embedding.repo_id = %(repo_id)s::uuid)
  AND (%(item_types)s::text[] IS NULL OR embedding.item_type = ANY(%(item_types)s::text[]))
  AND NOT EXISTS (
      SELECT 1
      FROM projection_sources AS source
      WHERE source.tenant_id = embedding.tenant_id
        AND source.item_type = embedding.item_type
        AND source.item_id = embedding.item_id
  )
RETURNING
    embedding.embedding_id::text AS embedding_id,
    embedding.item_type,
    embedding.item_id::text AS item_id;
""".strip()


def embedding_hnsw_index_sql(
    *,
    embedding_dimension: int,
    config: VectorIndexConfig = DEFAULT_VECTOR_INDEX_CONFIG,
    schema: str = "cortex_hosted",
) -> str:
    """Return DDL that creates a pgvector HNSW index after a row-count floor."""

    _validate_sql_identifier(schema)
    _require_positive("embedding_dimension", embedding_dimension)
    return f"""
DO $$
DECLARE
    embedding_row_count integer;
BEGIN
    SELECT count(*)::integer
    INTO embedding_row_count
    FROM {schema}.embeddings
    WHERE embedding_dimension = {embedding_dimension};

    IF embedding_row_count >= {config.min_rows_for_hnsw} THEN
        CREATE INDEX IF NOT EXISTS embeddings_vector_cosine_hnsw_dim_{embedding_dimension}_idx
            ON {schema}.embeddings
            USING hnsw ((embedding::vector({embedding_dimension})) vector_cosine_ops)
            WITH (m = {config.hnsw_m}, ef_construction = {config.hnsw_ef_construction})
            WHERE embedding_dimension = {embedding_dimension};
    END IF;
END;
$$;
""".strip()


def exact_vector_search_sql(*, embedding_dimension: int, schema: str = "cortex_hosted") -> str:
    """Return exact vector search SQL for recall baselines."""

    _validate_sql_identifier(schema)
    _require_positive("embedding_dimension", embedding_dimension)
    return _vector_search_sql(
        schema=schema,
        approximate=False,
        embedding_dimension=embedding_dimension,
    )


def approximate_vector_search_sql(
    *,
    embedding_dimension: int,
    config: VectorIndexConfig = DEFAULT_VECTOR_INDEX_CONFIG,
    schema: str = "cortex_hosted",
) -> str:
    """Return approximate vector search SQL using the versioned HNSW settings."""

    _validate_sql_identifier(schema)
    _require_positive("embedding_dimension", embedding_dimension)
    _validate_vector_index_config(config)
    return _vector_search_sql(
        schema=schema,
        approximate=True,
        config=config,
        embedding_dimension=embedding_dimension,
    )


def _vector_search_sql(
    *,
    schema: str,
    approximate: bool,
    embedding_dimension: int,
    config: VectorIndexConfig = DEFAULT_VECTOR_INDEX_CONFIG,
) -> str:
    setup = (
        f"SET LOCAL hnsw.ef_search = {config.hnsw_ef_search};"
        if approximate
        else "SET LOCAL enable_indexscan = off;\nSET LOCAL enable_bitmapscan = off;"
    )
    embedding_expr = f"embedding.embedding::vector({embedding_dimension})"
    query_expr = f"%(embedding_vector)s::vector({embedding_dimension})"
    return f"""
{setup}
SELECT
    embedding.item_type,
    embedding.item_id::text AS item_id,
    row_number() OVER (
        ORDER BY {embedding_expr} <=> {query_expr}
    ) AS rank,
    ({embedding_expr} <=> {query_expr}) AS distance
FROM {schema}.embeddings AS embedding
WHERE embedding.tenant_id = %(tenant_id)s
  AND (%(repo_id)s::uuid IS NULL OR embedding.repo_id IS NULL OR embedding.repo_id = %(repo_id)s::uuid)
  AND embedding.embedding_model_id = %(embedding_model_id)s
  AND embedding.embedding_epoch = %(embedding_epoch)s
  AND embedding.embedding_dimension = {embedding_dimension}
  AND (%(item_types)s::text[] IS NULL OR embedding.item_type = ANY(%(item_types)s::text[]))
ORDER BY distance ASC
LIMIT %(limit)s;
""".strip()


def _projection_sources_sql(schema: str) -> str:
    return f"""
    SELECT
        version.tenant_id,
        node.repo_id,
        'decision_version'::text AS item_type,
        version.decision_version_id AS item_id,
        encode(
            digest(
                jsonb_build_object(
                    'decision_text', version.decision_text,
                    'decision_version_id', version.decision_version_id::text,
                    'source_span_hashes', version.source_span_hashes
                )::text,
                'sha256'
            ),
            'hex'
        ) AS item_hash,
        version.decision_text AS text
    FROM {schema}.decision_versions AS version
    JOIN {schema}.decision_nodes AS node
      ON node.tenant_id = version.tenant_id
     AND node.decision_node_id = version.decision_node_id
    UNION ALL
    SELECT
        span.tenant_id,
        source.repo_id,
        'source_span'::text AS item_type,
        span.source_span_id AS item_id,
        span.span_hash AS item_hash,
        span.excerpt AS text
    FROM {schema}.source_spans AS span
    JOIN {schema}.source_documents AS doc
      ON doc.tenant_id = span.tenant_id
     AND doc.source_document_id = span.source_document_id
     AND doc.document_hash = span.source_document_hash
    JOIN {schema}.sources AS source
      ON source.tenant_id = doc.tenant_id
     AND source.source_id = doc.source_id
""".rstrip()


def _validate_vector_index_config(config: VectorIndexConfig) -> None:
    if not isinstance(config, VectorIndexConfig):
        raise HostedEmbeddingValidationError("config must be a VectorIndexConfig")


def _require_positive(name: str, value: int) -> None:
    if value < 1:
        raise HostedEmbeddingValidationError(f"{name} must be >= 1")


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise HostedEmbeddingValidationError(f"{name} must not be empty")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise HostedEmbeddingValidationError(f"{name} must be a UUID") from exc


def _validate_hash(name: str, value: str) -> None:
    if not _SHA256_RE.match(value):
        raise HostedEmbeddingValidationError(f"{name} must be a sha256 hex string")


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise HostedEmbeddingValidationError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HostedEmbeddingValidationError(f"{name} must be JSON-serializable") from exc


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise HostedEmbeddingValidationError(f"invalid SQL identifier: {name!r}")


def _coerce_vector_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise HostedEmbeddingValidationError("embedding_vector must contain numbers")
    return float(value)
