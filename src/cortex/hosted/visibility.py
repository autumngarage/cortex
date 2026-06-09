"""Fail-closed source visibility boundary for hosted retrieval."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DENY_VISIBILITY_FLAGS = (
    "deleted",
    "revoked",
    "slack_channel_excluded",
    "repo_installation_revoked",
)


class VisibilityBoundaryValidationError(ValueError):
    """Raised when retrieval lacks an explicit source authorization boundary."""


@dataclass(frozen=True)
class SourceVisibilityScope:
    """Explicit source IDs authorized for one hosted retrieval request."""

    visible_source_ids: tuple[str, ...]
    repo_installation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "visible_source_ids",
            normalize_visible_source_ids(self.visible_source_ids),
        )
        if self.repo_installation_id is not None:
            repo_installation_id = self.repo_installation_id.strip()
            if not repo_installation_id:
                raise VisibilityBoundaryValidationError(
                    "repo_installation_id must not be empty when provided"
                )
            object.__setattr__(self, "repo_installation_id", repo_installation_id)

    @property
    def scope_hash(self) -> str:
        return _hash_mapping(
            {
                "repo_installation_id": self.repo_installation_id,
                "visible_source_ids": list(self.visible_source_ids),
            }
        )

    def as_sql_parameters(self) -> dict[str, object]:
        return visibility_sql_parameters(
            visible_source_ids=self.visible_source_ids,
            repo_installation_id=self.repo_installation_id,
        )


def normalize_visible_source_ids(visible_source_ids: Sequence[str] | None) -> tuple[str, ...]:
    """Return canonical authorized source IDs, or fail closed when absent."""

    if visible_source_ids is None:
        raise VisibilityBoundaryValidationError(
            "visible_source_ids must include at least one authorized source"
        )
    unique_source_ids = tuple(dict.fromkeys(visible_source_ids))
    if not unique_source_ids:
        raise VisibilityBoundaryValidationError(
            "visible_source_ids must include at least one authorized source"
        )
    for source_id in unique_source_ids:
        _require_uuid("visible_source_ids", source_id)
    return unique_source_ids


def visibility_sql_parameters(
    *,
    visible_source_ids: Sequence[str],
    repo_installation_id: str | None = None,
) -> dict[str, object]:
    """Return SQL parameters for the shared source visibility CTEs."""

    return {
        "repo_installation_id": repo_installation_id,
        "visible_source_ids": list(normalize_visible_source_ids(visible_source_ids)),
    }


def visible_source_documents_ctes(schema: str = "cortex_hosted") -> str:
    """Return CTEs that authorize source documents before retrieval scoring.

    The caller must also bind tenant_id, repo_id, visible_source_ids, and
    repo_installation_id parameters. Missing source IDs are rejected before SQL.
    """

    _validate_sql_identifier(schema)
    return f"""
visible_sources AS (
    SELECT
        source.source_id,
        source.repo_id
    FROM {schema}.sources AS source
    WHERE source.tenant_id = %(tenant_id)s
      AND source.source_id = ANY(%(visible_source_ids)s::uuid[])
      AND (%(repo_id)s::uuid IS NULL OR source.repo_id IS NULL OR source.repo_id = %(repo_id)s::uuid)
      AND (
          %(repo_installation_id)s::text IS NULL
          OR source.source_type NOT IN ('github', 'github_repo', 'repo')
          OR source.visibility->>'repo_installation_id' = %(repo_installation_id)s::text
          OR source.visibility->>'github_installation_id' = %(repo_installation_id)s::text
      )
{_deny_flag_predicates("source", "visibility", indent="      ")}
),
visible_docs AS (
    SELECT
        doc.source_document_id,
        doc.document_hash,
        doc.source_id,
        visible_source.repo_id
    FROM {schema}.source_documents AS doc
    JOIN visible_sources AS visible_source
      ON visible_source.source_id = doc.source_id
    WHERE doc.tenant_id = %(tenant_id)s
{_deny_flag_predicates("doc", "visibility", indent="      ")}
)""".strip()


def visible_decision_version_exists_sql(
    *,
    schema: str = "cortex_hosted",
    version_alias: str = "version",
    tenant_alias: str = "node",
) -> str:
    """Return a guard proving every decision citation resolves to visible source."""

    _validate_sql_identifier(schema)
    _validate_sql_identifier(version_alias)
    _validate_sql_identifier(tenant_alias)
    return f"""(
        cardinality({version_alias}.source_span_hashes) > 0
        AND NOT EXISTS (
            SELECT 1
            FROM unnest({version_alias}.source_span_hashes) AS cited_span(span_hash)
            WHERE NOT EXISTS (
                SELECT 1
                FROM {schema}.source_spans AS visible_span
                JOIN visible_docs AS visible_doc
                  ON visible_doc.source_document_id = visible_span.source_document_id
                WHERE visible_span.tenant_id = {tenant_alias}.tenant_id
                  AND visible_span.span_hash = cited_span.span_hash
            )
        )
    )"""


def _deny_flag_predicates(table_alias: str, column_name: str, *, indent: str) -> str:
    return "\n".join(
        f"{indent}AND NOT ({table_alias}.{column_name} @> '{{\"{flag}\": true}}'::jsonb)"
        for flag in _DENY_VISIBILITY_FLAGS
    )


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise VisibilityBoundaryValidationError(f"{name} must be a UUID") from exc


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise VisibilityBoundaryValidationError(f"invalid SQL identifier: {name!r}")


def _hash_mapping(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
