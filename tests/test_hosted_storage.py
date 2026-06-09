from __future__ import annotations

import pytest

from cortex.hosted.storage import (
    CANONICAL_HOSTED_STORE,
    HOSTED_STORAGE_DECISION,
    StoreBoundaryError,
    validate_canonical_store,
    validate_rebuildable_cache_store,
)


def test_hosted_canonical_store_is_postgres() -> None:
    assert CANONICAL_HOSTED_STORE == "postgres"
    assert HOSTED_STORAGE_DECISION.canonical_store == "postgres"


def test_sqlite_cannot_be_canonical_hosted_store() -> None:
    with pytest.raises(StoreBoundaryError, match="canonical storage must be Postgres"):
        validate_canonical_store("sqlite")


def test_postgres_canonical_store_normalizes() -> None:
    assert validate_canonical_store(" Postgres ") == "postgres"


def test_sqlite_is_allowed_only_for_named_rebuildable_roles() -> None:
    assert (
        validate_rebuildable_cache_store("sqlite", role="retrieve-index-cache")
        == "sqlite"
    )
    with pytest.raises(StoreBoundaryError, match="not an approved rebuildable"):
        validate_rebuildable_cache_store("sqlite", role="decision-graph")


def test_non_sqlite_projection_store_is_deferred() -> None:
    with pytest.raises(StoreBoundaryError, match="before measured pressure"):
        validate_rebuildable_cache_store("opensearch", role="retrieve-index-cache")
