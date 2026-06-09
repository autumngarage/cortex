from __future__ import annotations

import pytest

from cortex.hosted.visibility import (
    SourceVisibilityScope,
    VisibilityBoundaryValidationError,
    normalize_visible_source_ids,
    visibility_sql_parameters,
    visible_decision_version_exists_sql,
    visible_source_documents_ctes,
)

SOURCE_ID = "33333333-3333-4333-8333-333333333333"
OTHER_SOURCE_ID = "55555555-5555-4555-8555-555555555555"


def test_visible_source_ids_are_required_and_canonicalized() -> None:
    with pytest.raises(VisibilityBoundaryValidationError, match="authorized source"):
        normalize_visible_source_ids(None)

    with pytest.raises(VisibilityBoundaryValidationError, match="authorized source"):
        normalize_visible_source_ids(())

    assert normalize_visible_source_ids((SOURCE_ID, SOURCE_ID, OTHER_SOURCE_ID)) == (
        SOURCE_ID,
        OTHER_SOURCE_ID,
    )


def test_visibility_scope_rejects_malformed_sources_and_empty_install_scope() -> None:
    with pytest.raises(VisibilityBoundaryValidationError, match="must be a UUID"):
        SourceVisibilityScope(visible_source_ids=("not-a-uuid",))

    with pytest.raises(VisibilityBoundaryValidationError, match="repo_installation_id"):
        SourceVisibilityScope(visible_source_ids=(SOURCE_ID,), repo_installation_id=" ")


def test_visibility_parameters_fail_closed() -> None:
    params = visibility_sql_parameters(
        visible_source_ids=(SOURCE_ID,),
        repo_installation_id="install-123",
    )

    assert params == {
        "repo_installation_id": "install-123",
        "visible_source_ids": [SOURCE_ID],
    }


def test_visible_source_document_ctes_enforce_source_and_visibility_boundaries() -> None:
    sql = visible_source_documents_ctes()

    assert "visible_sources AS" in sql
    assert "visible_docs AS" in sql
    assert "source.tenant_id = %(tenant_id)s" in sql
    assert "source.source_id = ANY(%(visible_source_ids)s::uuid[])" in sql
    assert "%(visible_source_ids)s::uuid[] IS NULL" not in sql
    assert "source.repo_id IS NULL OR source.repo_id = %(repo_id)s::uuid" in sql
    assert "source.source_type NOT IN ('github', 'github_repo', 'repo')" in sql
    assert "source.visibility->>'repo_installation_id' = %(repo_installation_id)s::text" in sql
    assert "source.visibility->>'github_installation_id' = %(repo_installation_id)s::text" in sql
    assert "slack_channel_excluded" in sql
    assert "repo_installation_revoked" in sql
    assert "revoked" in sql
    assert "deleted" in sql
    assert "doc.document_hash" in sql


def test_visible_decision_guard_requires_every_cited_span_to_be_visible() -> None:
    sql = visible_decision_version_exists_sql()

    assert "cardinality(version.source_span_hashes) > 0" in sql
    assert "FROM unnest(version.source_span_hashes) AS cited_span(span_hash)" in sql
    assert "WHERE NOT EXISTS (" in sql
    assert "JOIN visible_docs AS visible_doc" in sql
    assert "visible_span.tenant_id = node.tenant_id" in sql
    assert "visible_span.span_hash = cited_span.span_hash" in sql
    assert "visible_span.span_hash = ANY(version.source_span_hashes)" not in sql


def test_visibility_sql_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(VisibilityBoundaryValidationError, match="invalid SQL identifier"):
        visible_source_documents_ctes("bad;drop")
