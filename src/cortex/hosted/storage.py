"""Storage boundary for hosted Cortex.

Postgres is the only canonical hosted store. SQLite remains acceptable for
explicit rebuildable caches and fixture exports, such as the existing local
retrieve index, but never for core hosted ledger or graph semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

CANONICAL_HOSTED_STORE = "postgres"
REBUILDABLE_SQLITE_CACHE_ROLES = frozenset(
    {
        "local-replay-export",
        "retrieve-index-cache",
    }
)


class StoreBoundaryError(ValueError):
    """Raised when hosted code tries to use a noncanonical product store."""


@dataclass(frozen=True)
class HostedStorageDecision:
    """The storage decision issue #460 relies on."""

    canonical_store: str
    sqlite_roles: frozenset[str]
    deferred_stores: tuple[str, ...]


HOSTED_STORAGE_DECISION = HostedStorageDecision(
    canonical_store=CANONICAL_HOSTED_STORE,
    sqlite_roles=REBUILDABLE_SQLITE_CACHE_ROLES,
    deferred_stores=(
        "graph-database",
        "external-vector-database",
        "opensearch",
        "kafka",
        "tenant-partitioned-store",
    ),
)


def _normalize_store_name(name: str) -> str:
    normalized = name.strip().lower().replace("_", "-")
    if not normalized:
        raise StoreBoundaryError("store name must not be empty")
    return normalized


def validate_canonical_store(name: str) -> str:
    """Return the normalized canonical store name or raise.

    This is a guardrail against accidentally growing a second product substrate
    while Stage 0 is still trying to prove the Postgres-shaped ledger.
    """

    normalized = _normalize_store_name(name)
    if normalized != CANONICAL_HOSTED_STORE:
        raise StoreBoundaryError(
            "hosted Cortex canonical storage must be Postgres; "
            f"{name!r} is allowed only as an explicit rebuildable cache/export"
        )
    return normalized


def validate_rebuildable_cache_store(name: str, *, role: str) -> str:
    """Validate a noncanonical cache/export store and make its role explicit."""

    normalized = _normalize_store_name(name)
    normalized_role = role.strip().lower().replace("_", "-")
    if normalized != "sqlite":
        raise StoreBoundaryError(
            "only SQLite cache/export stores are recognized before measured pressure "
            f"requires another projection store; got {name!r}"
        )
    if normalized_role not in REBUILDABLE_SQLITE_CACHE_ROLES:
        known = ", ".join(sorted(REBUILDABLE_SQLITE_CACHE_ROLES))
        raise StoreBoundaryError(
            f"SQLite role {role!r} is not an approved rebuildable cache/export role "
            f"(known: {known})"
        )
    return normalized
