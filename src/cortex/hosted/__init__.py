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
from cortex.hosted.provenance import (
    ProvenanceValidationError,
    SourceDocument,
    SourceSpan,
    content_hash,
    source_document_insert_sql,
    source_span_insert_sql,
)
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION, create_schema_sql
from cortex.hosted.scopes import (
    SEMANTIC_MATCH_WEIGHT,
    STRUCTURAL_SCOPE_WEIGHTS,
    ChangedSurface,
    DecisionScope,
    QueryScope,
    ScopeType,
    ScopeValidationError,
    decision_scope_insert_sql,
    decisions_for_diff_scope_sql,
    normalize_scope_value,
    query_scope_parameters,
    scope_reason_code,
)
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
    "SEMANTIC_MATCH_WEIGHT",
    "STRUCTURAL_SCOPE_WEIGHTS",
    "ActorRef",
    "ChangedSurface",
    "DecisionScope",
    "LedgerEvent",
    "LedgerEventType",
    "LedgerEventValidationError",
    "ProvenanceValidationError",
    "QueryScope",
    "ScopeType",
    "ScopeValidationError",
    "SourceDocument",
    "SourceSpan",
    "StoreBoundaryError",
    "content_hash",
    "create_schema_sql",
    "decision_scope_insert_sql",
    "decisions_for_diff_scope_sql",
    "derive_idempotency_key",
    "ledger_event_insert_sql",
    "normalize_scope_value",
    "query_scope_parameters",
    "scope_reason_code",
    "source_document_insert_sql",
    "source_span_insert_sql",
    "validate_canonical_store",
    "validate_rebuildable_cache_store",
]
