"""Hosted Cortex substrate boundaries.

The hosted package is intentionally separate from the `.cortex/` file-format
CLI. Hosted code may project local Cortex files into Postgres, but the protocol
and CLI remain usable without hosted services.
"""

from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    LedgerEventValidationError,
    derive_idempotency_key,
    ledger_event_insert_sql,
)
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION, create_schema_sql
from cortex.hosted.storage import (
    CANONICAL_HOSTED_STORE,
    REBUILDABLE_SQLITE_CACHE_ROLES,
    StoreBoundaryError,
    validate_canonical_store,
    validate_rebuildable_cache_store,
)

__all__ = [
    "CANONICAL_HOSTED_STORE",
    "HOSTED_SCHEMA_VERSION",
    "REBUILDABLE_SQLITE_CACHE_ROLES",
    "ActorRef",
    "LedgerEvent",
    "LedgerEventType",
    "LedgerEventValidationError",
    "StoreBoundaryError",
    "create_schema_sql",
    "derive_idempotency_key",
    "ledger_event_insert_sql",
    "validate_canonical_store",
    "validate_rebuildable_cache_store",
]
