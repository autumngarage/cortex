"""Source document and span provenance for hosted Cortex."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ProvenanceValidationError(ValueError):
    """Raised when source material cannot support cited replay."""


@dataclass(frozen=True)
class SourceDocument:
    """Immutable source snapshot used to derive cited source spans."""

    tenant_id: str
    source_id: str
    document_type: str
    external_id: str
    permalink: str
    author_ref: str
    source_timestamp: datetime
    content: str
    source_revision: str | None = None
    visibility: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_uuid("tenant_id", self.tenant_id)
        _require_uuid("source_id", self.source_id)
        _require_non_empty("document_type", self.document_type)
        _require_non_empty("external_id", self.external_id)
        _require_non_empty("permalink", self.permalink)
        _require_non_empty("author_ref", self.author_ref)
        if self.source_revision is not None:
            _require_non_empty("source_revision", self.source_revision)
        if self.source_timestamp.tzinfo is None or self.source_timestamp.utcoffset() is None:
            raise ProvenanceValidationError("source_timestamp must be timezone-aware")
        if not self.content:
            raise ProvenanceValidationError("content must not be empty")
        _validate_json_object("visibility", self.visibility)
        _validate_json_object("metadata", self.metadata)
        object.__setattr__(self, "visibility", MappingProxyType(dict(self.visibility)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def content_hash(self) -> str:
        return content_hash(self.content)

    @property
    def document_hash(self) -> str:
        """Stable identity for this source snapshot, independent of DB IDs."""

        return _hash_mapping(
            {
                "content_hash": self.content_hash,
                "external_id": self.external_id,
                "source_id": self.source_id,
                "tenant_id": self.tenant_id,
            }
        )

    def span(self, *, start_offset: int, end_offset: int, permalink: str | None = None) -> SourceSpan:
        """Build and validate a source span from this document's content."""

        if start_offset < 0:
            raise ProvenanceValidationError("start_offset must be >= 0")
        if end_offset <= start_offset:
            raise ProvenanceValidationError("end_offset must be greater than start_offset")
        if end_offset > len(self.content):
            raise ProvenanceValidationError("end_offset exceeds document content length")
        excerpt = self.content[start_offset:end_offset]
        if not excerpt:
            raise ProvenanceValidationError("source span excerpt must not be empty")
        return SourceSpan(
            tenant_id=self.tenant_id,
            source_document_hash=self.document_hash,
            start_offset=start_offset,
            end_offset=end_offset,
            excerpt=excerpt,
            permalink=permalink or self.permalink,
        )

    def as_insert_parameters(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "document_type": self.document_type,
            "external_id": self.external_id,
            "permalink": self.permalink,
            "author_ref": self.author_ref,
            "source_timestamp": self.source_timestamp,
            "content_hash": self.content_hash,
            "document_hash": self.document_hash,
            "source_revision": self.source_revision,
            "visibility": json.dumps(dict(self.visibility), sort_keys=True, separators=(",", ":")),
            "metadata": json.dumps(dict(self.metadata), sort_keys=True, separators=(",", ":")),
        }


@dataclass(frozen=True)
class SourceSpan:
    """Citable excerpt inside a source document snapshot."""

    tenant_id: str
    source_document_hash: str
    start_offset: int
    end_offset: int
    excerpt: str
    permalink: str

    def __post_init__(self) -> None:
        _require_uuid("tenant_id", self.tenant_id)
        _validate_hash("source_document_hash", self.source_document_hash)
        if self.start_offset < 0:
            raise ProvenanceValidationError("start_offset must be >= 0")
        if self.end_offset <= self.start_offset:
            raise ProvenanceValidationError("end_offset must be greater than start_offset")
        _require_non_empty("excerpt", self.excerpt)
        if len(self.excerpt) != self.end_offset - self.start_offset:
            raise ProvenanceValidationError("excerpt length must match offsets")
        _require_non_empty("permalink", self.permalink)

    @property
    def span_hash(self) -> str:
        return _hash_mapping(
            {
                "end_offset": self.end_offset,
                "excerpt_hash": content_hash(self.excerpt),
                "source_document_hash": self.source_document_hash,
                "start_offset": self.start_offset,
            }
        )

    def as_insert_parameters(self, *, source_document_id: str) -> dict[str, Any]:
        _require_uuid("source_document_id", source_document_id)
        return {
            "tenant_id": self.tenant_id,
            "source_document_id": source_document_id,
            "source_document_hash": self.source_document_hash,
            "span_hash": self.span_hash,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "excerpt": self.excerpt,
            "permalink": self.permalink,
        }


def content_hash(content: str) -> str:
    """Return the canonical sha256 hash for source text content."""

    if not content:
        raise ProvenanceValidationError("content must not be empty")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def source_document_insert_sql(schema: str = "cortex_hosted") -> str:
    """Return idempotent insert SQL for immutable source document snapshots."""

    _validate_sql_identifier(schema)
    return f"""
WITH inserted AS (
    INSERT INTO {schema}.source_documents (
        tenant_id,
        source_id,
        document_type,
        external_id,
        permalink,
        author_ref,
        source_timestamp,
        content_hash,
        document_hash,
        source_revision,
        visibility,
        metadata
    ) VALUES (
        %(tenant_id)s,
        %(source_id)s,
        %(document_type)s,
        %(external_id)s,
        %(permalink)s,
        %(author_ref)s,
        %(source_timestamp)s,
        %(content_hash)s,
        %(document_hash)s,
        %(source_revision)s,
        %(visibility)s::jsonb,
        %(metadata)s::jsonb
    )
    ON CONFLICT (tenant_id, source_id, external_id, content_hash) DO NOTHING
    RETURNING source_document_id, content_hash, document_hash
)
SELECT source_document_id, content_hash, document_hash FROM inserted
UNION ALL
SELECT source_document_id, content_hash, document_hash
FROM {schema}.source_documents
WHERE tenant_id = %(tenant_id)s
  AND source_id = %(source_id)s
  AND external_id = %(external_id)s
  AND content_hash = %(content_hash)s
LIMIT 1;
""".strip()


def source_span_insert_sql(schema: str = "cortex_hosted") -> str:
    """Return idempotent insert SQL for citable source spans."""

    _validate_sql_identifier(schema)
    return f"""
WITH inserted AS (
    INSERT INTO {schema}.source_spans (
        tenant_id,
        source_document_id,
        source_document_hash,
        span_hash,
        start_offset,
        end_offset,
        excerpt,
        permalink
    ) VALUES (
        %(tenant_id)s,
        %(source_document_id)s,
        %(source_document_hash)s,
        %(span_hash)s,
        %(start_offset)s,
        %(end_offset)s,
        %(excerpt)s,
        %(permalink)s
    )
    ON CONFLICT (tenant_id, span_hash) DO NOTHING
    RETURNING source_span_id, source_document_hash, span_hash
)
SELECT source_span_id, source_document_hash, span_hash FROM inserted
UNION ALL
SELECT source_span_id, source_document_hash, span_hash
FROM {schema}.source_spans
WHERE tenant_id = %(tenant_id)s
  AND span_hash = %(span_hash)s
LIMIT 1;
""".strip()


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise ProvenanceValidationError(f"{name} must not be empty")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise ProvenanceValidationError(f"{name} must be a UUID") from exc


def _validate_hash(name: str, value: str) -> None:
    if not _SHA256_RE.match(value):
        raise ProvenanceValidationError(f"{name} must be a sha256 hex string")


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise ProvenanceValidationError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProvenanceValidationError(f"{name} must be JSON-serializable") from exc


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ProvenanceValidationError(f"invalid SQL identifier: {name!r}")
