"""`cortex push` substrate — replay the local derive export into the hosted store.

Born from the PE-0 dogfood run (cortex#513): getting from a local derive
store to a live cited answer took three hand-written scripts (event push via
write plans, source-document/span rebuild, snapshot computation +
registration). This module is those scripts as one idempotent, visible
pipeline over an already-open :class:`cortex.hosted.db.HostedConnection`:

1. **Reconstruct** ``LedgerEvent`` envelopes from
   ``DeriveEventStore.export_events()`` rows (payload/metadata arrive as JSON
   strings; ``occurred_at`` may be an ISO string). Reconstruction re-derives
   the event hash and refuses a row whose stored ``event_hash`` disagrees —
   drift between the export and the envelope is never pushed.
2. **Ensure identity rows** — idempotent tenant/source inserts using the
   deterministic derive-default ids, slugged from the ids themselves so
   re-runs and pre-existing rows conflict harmlessly (``ON CONFLICT DO
   NOTHING``; the live row always wins).
3. **Rebuild provenance** for file-backed candidates by re-reading the
   source file named in the span payloads. The rebuilt document hash is
   content-keyed; a mismatch against the span's recorded
   ``source_document_hash`` is a visible skip naming the path, and the
   candidate is excluded — nothing silently diverges.
4. **Execute one ``graph_writes`` plan per event** (candidate / status
   transition), one transaction per plan. The plan contract is honored: a
   no-row return from the first (ledger-append) statement is a replay — roll
   back, count, skip the projections. A failed event names its idempotency
   key and the loop continues; failures are counted, never dropped.
5. **Recompute the snapshot over live projection rows** (the
   ``graph_snapshot`` row builders' ``from_mapping`` consume the SELECTs
   verbatim) and register ``graph_snapshots`` plus a ``projection.rebuilt``
   ledger event idempotently — the event's idempotency key is content-keyed
   on the snapshot hash, so an unchanged graph re-push is a replay.

Identifier determinism (the contract that lets a later push confirm an
earlier push's candidate): ``decision_node_id`` / ``decision_version_id``
are UUIDv5 values of the candidate's ``event_hash`` under
``PUSH_UUID_NAMESPACE``. A ``decision.confirmed`` / ``decision.rejected``
event carries its candidate's event hash in the payload
(``cortex.commands.confirm``), so the transition resolves the same node id
in any push run, on any machine, with no lookup table.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from cortex.hosted.ask_ledger import ASK_LEDGER_RETRIEVAL_CONFIG_VERSION
from cortex.hosted.confidence import ConfidenceValidationError
from cortex.hosted.db import HostedConnection
from cortex.hosted.event_ordering import ordering_key
from cortex.hosted.graph_snapshot import (
    EdgeRow,
    GraphSnapshotValidationError,
    NodeRow,
    ScopeRow,
    VersionRow,
    compute_graph_snapshot_hash,
)
from cortex.hosted.graph_writes import (
    GraphWriteValidationError,
    plan_candidate_proposed,
    plan_status_transition,
)
from cortex.hosted.lane_assignment import LaneAssignmentError, initial_confidence_state
from cortex.hosted.lanes import DeriveSourceType, LaneAssignment, LanePolicyValidationError
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
    source_document_insert_sql,
    source_span_insert_sql,
)
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION
from cortex.hosted.scopes import ScopeType, ScopeValidationError, normalize_scope_value

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

# Same UUIDv5 idiom as the derive identity defaults (commands/derive.py):
# deterministic node/version ids per candidate event hash, no stored mapping.
PUSH_UUID_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/autumngarage/cortex#push")

# Actor recorded on the projection.rebuilt event this pipeline appends.
PUSH_ACTOR = ActorRef(actor_type="cli", actor_id="cortex-push")

# sources.source_type for the derive-default identity rows push ensures.
PUSH_SOURCE_TYPE = "cortex-derive"

# Derive source types whose documents are repo files push can re-read from
# the working tree. Gathered text sources (commits, PRs) have no file to
# rebuild a content-keyed snapshot from, so their candidates skip visibly.
FILE_BACKED_SOURCE_TYPES = frozenset(
    {
        DeriveSourceType.AGENT_INSTRUCTIONS,
        DeriveSourceType.ADR,
        DeriveSourceType.CODEOWNERS,
    }
)

# Status each transition event projects — mirrors graph_writes.plan_status_transition.
_STATUS_BY_EVENT_TYPE: Mapping[LedgerEventType, str] = MappingProxyType(
    {
        LedgerEventType.DECISION_CONFIRMED: "confirmed",
        LedgerEventType.DECISION_REJECTED: "rejected",
        LedgerEventType.STALE_MARKED: "stale",
    }
)

_SPAN_PAYLOAD_KEYS = ("start_offset", "end_offset", "permalink", "source_document_hash", "span_hash")

# Projection SELECT column orders; the snapshot row builders' from_mapping
# consume mappings zipped from exactly these tuples.
_NODE_COLUMNS = ("decision_node_id", "status", "confidence", "current_version_id", "repo_id")
_VERSION_COLUMNS = (
    "decision_version_id",
    "decision_node_id",
    "decision_text",
    "source_span_hashes",
    "scope",
    "decided_at",
)
_EDGE_COLUMNS = ("from_node_id", "to_node_id", "edge_type")
_SCOPE_COLUMNS = ("decision_node_id", "scope_type", "scope_value", "normalized_value")


class HostedPushError(ValueError):
    """Raised when the local derive export cannot be replayed into the hosted store.

    The marquee failure is drift: a stored row whose recomputed event hash,
    or a working-tree file whose content-keyed document hash, no longer
    matches what the export recorded. Every message names the drifted side.
    """


@dataclass(frozen=True)
class PushSkip:
    """One event visibly excluded from the push, with the reason named."""

    idempotency_key: str
    reason: str


@dataclass(frozen=True)
class PushFailure:
    """One event whose plan failed; the push continued, the failure counted."""

    idempotency_key: str
    reason: str


@dataclass(frozen=True)
class CandidateProvenance:
    """Rebuilt source snapshot + spans backing one candidate event."""

    document: SourceDocument
    spans: tuple[SourceSpan, ...]


@dataclass(frozen=True)
class SnapshotResult:
    """Outcome of the final recompute-and-register stage."""

    snapshot_hash: str
    registered: bool
    event_appended: bool
    nodes: int
    versions: int
    edges: int
    scopes: int


@dataclass(frozen=True)
class PushOutcome:
    """Per-stage arithmetic for one push run (the CLI prints every field)."""

    total_events: int
    appended: int
    replayed: int
    skipped: tuple[PushSkip, ...]
    failed: tuple[PushFailure, ...]
    documents_upserted: int
    spans_upserted: int
    candidates_projected: int
    transitions_projected: int
    snapshot: SnapshotResult | None


def decision_node_id_for_candidate(candidate_event_hash: str) -> str:
    """Deterministic decision_node_id for a candidate event hash."""

    _require_sha256("candidate_event_hash", candidate_event_hash)
    return str(uuid5(PUSH_UUID_NAMESPACE, f"decision-node:{candidate_event_hash}"))


def decision_version_id_for_candidate(candidate_event_hash: str) -> str:
    """Deterministic decision_version_id for a candidate event hash."""

    _require_sha256("candidate_event_hash", candidate_event_hash)
    return str(uuid5(PUSH_UUID_NAMESPACE, f"decision-version:{candidate_event_hash}"))


def tenant_slug(tenant_id: str) -> str:
    """Deterministic slug for a derive-default tenant row."""

    return f"derive-{tenant_id}"


def source_external_id(source_id: str) -> str:
    """Deterministic sources.external_id for a derive-default source row."""

    return f"derive-{source_id}"


def tenant_insert_sql(schema: str = "cortex_hosted") -> str:
    """Idempotent tenants insert; a pre-existing row always wins."""

    _validate_sql_identifier(schema)
    return (
        f"INSERT INTO {schema}.tenants (tenant_id, slug, display_name) "
        "VALUES (%(tenant_id)s, %(slug)s, %(display_name)s) "
        "ON CONFLICT DO NOTHING;"
    )


def source_insert_sql(schema: str = "cortex_hosted") -> str:
    """Idempotent sources insert; a pre-existing row always wins."""

    _validate_sql_identifier(schema)
    return (
        f"INSERT INTO {schema}.sources (source_id, tenant_id, source_type, external_id) "
        "VALUES (%(source_id)s, %(tenant_id)s, %(source_type)s, %(external_id)s) "
        "ON CONFLICT DO NOTHING;"
    )


def graph_snapshot_insert_sql(schema: str = "cortex_hosted") -> str:
    """Idempotent graph_snapshots registration keyed to the rebuilt event."""

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.graph_snapshots (
    tenant_id, graph_snapshot_hash, schema_version, retrieval_config_version,
    source_event_id, metadata
)
SELECT %(tenant_id)s, %(graph_snapshot_hash)s, %(schema_version)s,
       %(retrieval_config_version)s, event_id, %(metadata)s::jsonb
FROM {schema}.ledger_events
WHERE tenant_id = %(tenant_id)s AND idempotency_key = %(idempotency_key)s
ON CONFLICT (tenant_id, graph_snapshot_hash) DO NOTHING
RETURNING graph_snapshot_id;
""".strip()


def projection_select_sql(schema: str = "cortex_hosted") -> dict[str, str]:
    """Per-table SELECTs feeding the snapshot row builders, keyed by table."""

    _validate_sql_identifier(schema)
    return {
        "decision_nodes": (
            f"SELECT {', '.join(_NODE_COLUMNS)} FROM {schema}.decision_nodes "
            "WHERE tenant_id = %(tenant_id)s ORDER BY decision_node_id;"
        ),
        "decision_versions": (
            f"SELECT {', '.join(_VERSION_COLUMNS)} FROM {schema}.decision_versions "
            "WHERE tenant_id = %(tenant_id)s ORDER BY decision_version_id;"
        ),
        "decision_edges": (
            f"SELECT {', '.join(_EDGE_COLUMNS)} FROM {schema}.decision_edges "
            "WHERE tenant_id = %(tenant_id)s ORDER BY from_node_id, to_node_id, edge_type;"
        ),
        "decision_scopes": (
            f"SELECT {', '.join(_SCOPE_COLUMNS)} FROM {schema}.decision_scopes "
            "WHERE tenant_id = %(tenant_id)s "
            "ORDER BY decision_node_id, scope_type, normalized_value;"
        ),
    }


def reconstruct_ledger_event(row: Mapping[str, Any]) -> LedgerEvent:
    """Rebuild the exact ``LedgerEvent`` a derive-store export row round-trips.

    ``payload`` / ``metadata`` arrive as canonical JSON strings (mappings are
    also accepted); ``occurred_at`` may be an ISO-8601 string or a datetime.
    The reconstructed envelope's ``event_hash`` must equal the stored
    ``event_hash`` — a mismatch means the export and the envelope have
    drifted, and the row is refused with both hashes named.
    """

    key = str(row.get("idempotency_key", "(missing idempotency_key)"))
    try:
        event = LedgerEvent(
            tenant_id=_row_str(row, "tenant_id", key),
            source_id=_row_str(row, "source_id", key),
            event_type=LedgerEventType(_row_str(row, "event_type", key)),
            actor=ActorRef(
                actor_type=_row_str(row, "actor_type", key),
                actor_id=_row_str(row, "actor_id", key),
            ),
            occurred_at=_row_datetime(row, "occurred_at", key),
            idempotency_key=_row_str(row, "idempotency_key", key),
            payload=_row_json_object(row, "payload", key),
            source_span_hashes=tuple(row.get("source_span_hashes") or ()),
            graph_snapshot_hash=_row_optional_str(row, "graph_snapshot_hash"),
            model_id=_row_optional_str(row, "model_id"),
            prompt_version=_row_optional_str(row, "prompt_version"),
            event_version=int(row.get("event_version", 0)),
            source_event_external_id=_row_optional_str(row, "source_event_external_id"),
            previous_event_hash=_row_optional_str(row, "previous_event_hash"),
            metadata=_row_json_object(row, "metadata", key),
        )
    except (LedgerEventValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, HostedPushError):
            raise
        raise HostedPushError(f"event {key!r} cannot be reconstructed: {exc}") from exc
    stored_hash = row.get("event_hash")
    if stored_hash != event.event_hash:
        raise HostedPushError(
            f"event {key!r} has drifted: stored event hash {stored_hash!r} != "
            f"recomputed event hash {event.event_hash!r}; refusing to push "
            "content that disagrees with its recorded identity"
        )
    return event


def candidate_confidence(payload: Mapping[str, Any], *, citation_count: int) -> str:
    """Resolve the confidence string a candidate enters the projection with.

    Precedence: an explicit payload ``confidence`` string; a payload
    ``confidence`` object's ``tier`` (the ``ConfidenceState`` shape); else
    the creation-time tier derived from the recorded ``lane_assignment``
    through ``initial_confidence_state`` — the same derivation the lane
    policy applies at graph entry. No silent default exists.
    """

    explicit = payload.get("confidence")
    if isinstance(explicit, str):
        if explicit.strip():
            return explicit
        raise HostedPushError("candidate payload 'confidence' string must not be empty")
    if isinstance(explicit, Mapping):
        tier = explicit.get("tier")
        if isinstance(tier, str) and tier.strip():
            return tier
        raise HostedPushError(
            "candidate payload 'confidence' object carries no string 'tier'"
        )
    lane_payload = payload.get("lane_assignment")
    if not isinstance(lane_payload, Mapping):
        raise HostedPushError(
            "candidate payload carries neither 'confidence' nor a 'lane_assignment' "
            "object; the projection confidence cannot be derived"
        )
    try:
        assignment = LaneAssignment.from_payload(lane_payload)
        state = initial_confidence_state(assignment, citation_count=citation_count)
    except (LanePolicyValidationError, LaneAssignmentError, ConfidenceValidationError) as exc:
        raise HostedPushError(f"candidate lane assignment cannot derive confidence: {exc}") from exc
    return state.tier.value


def candidate_scopes(payload: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    """Validated ``(scope_type, scope_value, normalized_value)`` triples.

    Mirrors the graph-rebuild forged-normalization check: every payload
    ``normalized_value`` is re-derived through ``normalize_scope_value`` and
    a mismatch fails closed — a stale or forged normalization never reaches
    ``decision_scopes``.
    """

    raw = payload.get("proposed_scopes", [])
    if isinstance(raw, str | bytes) or not isinstance(raw, Iterable):
        raise HostedPushError("candidate payload 'proposed_scopes' must be a sequence")
    triples: list[tuple[str, str, str]] = []
    for entry in raw:
        if not isinstance(entry, Mapping):
            raise HostedPushError(
                "each proposed scope must be a mapping with scope_type, value, "
                "and normalized_value"
            )
        scope_type = entry.get("scope_type")
        scope_value = entry.get("value")
        normalized_value = entry.get("normalized_value")
        if (
            not isinstance(scope_type, str)
            or not isinstance(scope_value, str)
            or not isinstance(normalized_value, str)
        ):
            raise HostedPushError(
                "proposed scope payloads require string scope_type, value, and "
                "normalized_value"
            )
        try:
            rederived = normalize_scope_value(ScopeType(scope_type), scope_value)
        except (ScopeValidationError, ValueError) as exc:
            raise HostedPushError(f"proposed scope is not pushable: {exc}") from exc
        if rederived != normalized_value:
            raise HostedPushError(
                f"scope normalized_value {normalized_value!r} does not match the "
                f"re-derived normalization {rederived!r} for ({scope_type!r}, "
                f"{scope_value!r})"
            )
        triples.append((scope_type, scope_value, rederived))
    return tuple(triples)


def rebuild_candidate_provenance(
    event: LedgerEvent,
    *,
    project_root: Path,
    document_type: str,
    author_ref: str,
) -> CandidateProvenance | PushSkip:
    """Re-read the file behind a candidate's spans and rebuild its provenance.

    Returns a :class:`PushSkip` (candidate excluded, path and reason named)
    when the source is not file-backed, the file is missing or unreadable,
    or the content has drifted past the recorded content-keyed
    ``source_document_hash``. Malformed payloads — material that was never
    valid — raise :class:`HostedPushError` instead.
    """

    payload = event.payload
    source_type_raw = payload.get("source_type")
    if not isinstance(source_type_raw, str) or not source_type_raw.strip():
        raise HostedPushError("candidate payload carries no 'source_type' string")
    try:
        source_type = DeriveSourceType(source_type_raw)
    except ValueError as exc:
        raise HostedPushError(f"unknown derive source type {source_type_raw!r}") from exc

    spans_raw = payload.get("spans")
    if not isinstance(spans_raw, list) or not spans_raw:
        raise HostedPushError("candidate payload carries no 'spans' list")
    span_payloads: list[Mapping[str, Any]] = []
    for entry in spans_raw:
        if not isinstance(entry, Mapping):
            raise HostedPushError("candidate payload spans must be mappings")
        missing = [span_key for span_key in _SPAN_PAYLOAD_KEYS if span_key not in entry]
        if missing:
            raise HostedPushError(f"candidate span payload is missing key(s) {missing!r}")
        span_payloads.append(entry)

    if source_type not in FILE_BACKED_SOURCE_TYPES:
        return PushSkip(
            idempotency_key=event.idempotency_key,
            reason=(
                f"source type {source_type.value!r} is not file-backed; its spans "
                "cannot be rebuilt from the working tree — candidate excluded"
            ),
        )

    permalinks = {str(entry["permalink"]) for entry in span_payloads}
    document_hashes = {str(entry["source_document_hash"]) for entry in span_payloads}
    if len(permalinks) != 1 or len(document_hashes) != 1:
        raise HostedPushError(
            "candidate spans must cite exactly one source document; got "
            f"permalinks {sorted(permalinks)!r} and document hashes "
            f"{sorted(document_hashes)!r}"
        )
    relative = permalinks.pop()
    expected_document_hash = document_hashes.pop()
    _require_repo_relative_path(relative)

    path = project_root / relative
    if not path.is_file():
        return PushSkip(
            idempotency_key=event.idempotency_key,
            reason=f"{relative}: source file is missing from the working tree; candidate excluded",
        )
    try:
        content = path.read_text(encoding="utf-8")
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except (OSError, UnicodeDecodeError) as exc:
        return PushSkip(
            idempotency_key=event.idempotency_key,
            reason=f"{relative}: cannot read source file ({exc}); candidate excluded",
        )
    try:
        document = SourceDocument(
            tenant_id=event.tenant_id,
            source_id=event.source_id,
            document_type=document_type,
            external_id=relative,
            permalink=relative,
            author_ref=author_ref,
            source_timestamp=modified_at,
            content=content,
        )
    except ProvenanceValidationError as exc:
        return PushSkip(
            idempotency_key=event.idempotency_key,
            reason=f"{relative}: rebuilt snapshot is invalid ({exc}); candidate excluded",
        )
    if document.document_hash != expected_document_hash:
        return PushSkip(
            idempotency_key=event.idempotency_key,
            reason=(
                f"{relative}: content drift since derive (document hash "
                f"{document.document_hash[:12]} != recorded "
                f"{expected_document_hash[:12]}); candidate excluded"
            ),
        )

    spans: list[SourceSpan] = []
    for entry in span_payloads:
        try:
            span = document.span(
                start_offset=int(entry["start_offset"]),
                end_offset=int(entry["end_offset"]),
                permalink=str(entry["permalink"]),
            )
        except (ProvenanceValidationError, TypeError, ValueError) as exc:
            return PushSkip(
                idempotency_key=event.idempotency_key,
                reason=f"{relative}: span offsets no longer fit the file ({exc}); "
                "candidate excluded",
            )
        if span.span_hash != str(entry["span_hash"]):
            return PushSkip(
                idempotency_key=event.idempotency_key,
                reason=(
                    f"{relative}: span drift (rebuilt span hash {span.span_hash[:12]} != "
                    f"recorded {str(entry['span_hash'])[:12]}); candidate excluded"
                ),
            )
        spans.append(span)
    if frozenset(event.source_span_hashes) != frozenset(span.span_hash for span in spans):
        raise HostedPushError(
            "candidate payload spans do not match the event's cited source_span_hashes"
        )
    return CandidateProvenance(document=document, spans=tuple(spans))


def run_push(
    conn: HostedConnection,
    rows: tuple[Mapping[str, Any], ...],
    *,
    project_root: Path,
    document_type: str,
    author_ref: str,
    schema: str = "cortex_hosted",
    retrieval_config_version: str = ASK_LEDGER_RETRIEVAL_CONFIG_VERSION,
    now: datetime | None = None,
) -> PushOutcome:
    """Push one derive-store export into the hosted ledger, projection, snapshot.

    Transaction discipline: one transaction per event plan plus one final
    snapshot transaction. A failed event rolls its transaction back, is
    recorded with its idempotency key, and the loop continues. The push is
    per-tenant (the snapshot boundary); a multi-tenant export is refused
    before any write.
    """

    _validate_sql_identifier(schema)
    occurred_now = now if now is not None else datetime.now(tz=UTC)
    if occurred_now.tzinfo is None or occurred_now.utcoffset() is None:
        raise HostedPushError("now must be a timezone-aware datetime")

    failed: list[PushFailure] = []
    skipped: list[PushSkip] = []
    reconstructed: list[LedgerEvent] = []
    for row in rows:
        try:
            reconstructed.append(reconstruct_ledger_event(row))
        except HostedPushError as exc:
            failed.append(
                PushFailure(
                    idempotency_key=str(row.get("idempotency_key", "(unknown)")),
                    reason=str(exc),
                )
            )

    if not reconstructed:
        return PushOutcome(
            total_events=len(rows),
            appended=0,
            replayed=0,
            skipped=tuple(skipped),
            failed=tuple(failed),
            documents_upserted=0,
            spans_upserted=0,
            candidates_projected=0,
            transitions_projected=0,
            snapshot=None,
        )

    tenant_ids = {event.tenant_id for event in reconstructed}
    if len(tenant_ids) > 1:
        raise HostedPushError(
            "the local derive export spans multiple tenants "
            f"({', '.join(sorted(tenant_ids))}); push is per-tenant — the snapshot "
            "boundary cannot mix tenants"
        )
    tenant_id = tenant_ids.pop()
    ordered = sorted(reconstructed, key=ordering_key)
    identity_pairs = sorted({(event.tenant_id, event.source_id) for event in ordered})
    snapshot_source_id = identity_pairs[0][1]

    _ensure_identity_rows(conn, identity_pairs, schema=schema)

    appended = 0
    replayed = 0
    documents_upserted = 0
    spans_upserted = 0
    candidates_projected = 0
    transitions_projected = 0

    for event in ordered:
        try:
            if event.event_type is LedgerEventType.CANDIDATE_PROPOSED:
                provenance = rebuild_candidate_provenance(
                    event,
                    project_root=project_root,
                    document_type=document_type,
                    author_ref=author_ref,
                )
                if isinstance(provenance, PushSkip):
                    skipped.append(provenance)
                    continue
                outcome = _push_candidate(conn, event, provenance, schema=schema)
                if outcome == "replayed":
                    replayed += 1
                    continue
                appended += 1
                candidates_projected += 1
                documents_upserted += 1
                spans_upserted += len(provenance.spans)
            elif event.event_type in _STATUS_BY_EVENT_TYPE:
                outcome = _push_transition(conn, event, schema=schema)
                if outcome == "replayed":
                    replayed += 1
                    continue
                appended += 1
                transitions_projected += 1
            else:
                skipped.append(
                    PushSkip(
                        idempotency_key=event.idempotency_key,
                        reason=(
                            f"{event.event_type.value} events are not pushable from "
                            "the local derive export"
                        ),
                    )
                )
        # Broad by contract: a failed event (plan validation, driver error,
        # constraint violation) is rolled back, counted, and NAMED — the loop
        # continues so one bad event never silently drops the rest.
        except Exception as exc:
            _rollback_quietly(conn)
            failed.append(
                PushFailure(idempotency_key=event.idempotency_key, reason=str(exc))
            )

    snapshot = _recompute_and_register_snapshot(
        conn,
        tenant_id=tenant_id,
        source_id=snapshot_source_id,
        schema=schema,
        retrieval_config_version=retrieval_config_version,
        now=occurred_now,
    )

    return PushOutcome(
        total_events=len(rows),
        appended=appended,
        replayed=replayed,
        skipped=tuple(skipped),
        failed=tuple(failed),
        documents_upserted=documents_upserted,
        spans_upserted=spans_upserted,
        candidates_projected=candidates_projected,
        transitions_projected=transitions_projected,
        snapshot=snapshot,
    )


def _ensure_identity_rows(
    conn: HostedConnection, pairs: list[tuple[str, str]], *, schema: str
) -> None:
    """Idempotently insert the derive-default tenant and source rows."""

    try:
        for tenant_id in sorted({tenant for tenant, _ in pairs}):
            conn.execute(
                tenant_insert_sql(schema),
                {
                    "tenant_id": tenant_id,
                    "slug": tenant_slug(tenant_id),
                    "display_name": f"cortex derive tenant {tenant_id}",
                },
            )
        for tenant_id, source_id in pairs:
            conn.execute(
                source_insert_sql(schema),
                {
                    "source_id": source_id,
                    "tenant_id": tenant_id,
                    "source_type": PUSH_SOURCE_TYPE,
                    "external_id": source_external_id(source_id),
                },
            )
        conn.commit()
    except Exception as exc:
        _rollback_quietly(conn)
        raise HostedPushError(f"cannot ensure tenant/source identity rows: {exc}") from exc


def _push_candidate(
    conn: HostedConnection,
    event: LedgerEvent,
    provenance: CandidateProvenance,
    *,
    schema: str,
) -> str:
    """Execute one candidate plan + provenance upserts in one transaction."""

    payload = event.payload
    decision_text = payload.get("decision_text")
    if not isinstance(decision_text, str) or not decision_text.strip():
        raise HostedPushError("candidate payload carries no 'decision_text' string")
    try:
        plan = plan_candidate_proposed(
            event,
            decision_node_id=decision_node_id_for_candidate(event.event_hash),
            decision_version_id=decision_version_id_for_candidate(event.event_hash),
            decision_text=decision_text,
            confidence=candidate_confidence(
                payload, citation_count=len(event.source_span_hashes)
            ),
            scopes=candidate_scopes(payload),
            schema=schema,
        )
    except GraphWriteValidationError as exc:
        raise HostedPushError(f"candidate plan cannot be built: {exc}") from exc

    append = plan.statements[0]
    if conn.execute(append.sql, append.parameters).fetchone() is None:
        # GraphWritePlan contract: a no-row append is a replay — roll back
        # and skip the projection statements (already applied on first push).
        conn.rollback()
        return "replayed"

    document_row = conn.execute(
        source_document_insert_sql(schema), provenance.document.as_insert_parameters()
    ).fetchone()
    if document_row is None:
        raise HostedPushError(
            f"source document upsert for {provenance.document.external_id!r} "
            "returned no row; the insert-or-select contract was not honored"
        )
    source_document_id = str(document_row[0])
    for span in provenance.spans:
        span_row = conn.execute(
            source_span_insert_sql(schema),
            span.as_insert_parameters(source_document_id=source_document_id),
        ).fetchone()
        if span_row is None:
            raise HostedPushError(
                f"source span upsert {span.span_hash[:12]} returned no row; the "
                "insert-or-select contract was not honored"
            )
    for statement in plan.statements[1:]:
        conn.execute(statement.sql, statement.parameters)
    conn.commit()
    return "appended"


def _push_transition(conn: HostedConnection, event: LedgerEvent, *, schema: str) -> str:
    """Execute one status-transition plan in one transaction."""

    candidate_hash = event.payload.get("candidate_event_hash")
    if not isinstance(candidate_hash, str) or not _SHA256_RE.match(candidate_hash):
        raise HostedPushError(
            f"{event.event_type.value} payload carries no 'candidate_event_hash' "
            "sha256 string; the decision node cannot be resolved"
        )
    node_id = decision_node_id_for_candidate(candidate_hash)
    try:
        plan = plan_status_transition(
            event,
            decision_node_id=node_id,
            new_status=_STATUS_BY_EVENT_TYPE[event.event_type],
            schema=schema,
        )
    except GraphWriteValidationError as exc:
        raise HostedPushError(f"status-transition plan cannot be built: {exc}") from exc

    append = plan.statements[0]
    if conn.execute(append.sql, append.parameters).fetchone() is None:
        conn.rollback()
        return "replayed"
    update = plan.statements[1]
    cursor = conn.execute(update.sql, update.parameters)
    rowcount = getattr(cursor, "rowcount", None)
    if rowcount != 1:
        raise HostedPushError(
            f"{event.event_type.value} {event.idempotency_key!r} updated "
            f"{rowcount!r} decision node row(s) for node {node_id} (expected 1); "
            "was its candidate pushed?"
        )
    conn.commit()
    return "appended"


def _recompute_and_register_snapshot(
    conn: HostedConnection,
    *,
    tenant_id: str,
    source_id: str,
    schema: str,
    retrieval_config_version: str,
    now: datetime,
) -> SnapshotResult:
    """Hash the live projection rows and register snapshot + event idempotently."""

    selects = projection_select_sql(schema)
    parameters = {"tenant_id": tenant_id}
    try:
        nodes = [
            NodeRow.from_mapping(dict(zip(_NODE_COLUMNS, row, strict=True)))
            for row in conn.execute(selects["decision_nodes"], parameters).fetchall()
        ]
        versions = [
            VersionRow.from_mapping(dict(zip(_VERSION_COLUMNS, row, strict=True)))
            for row in conn.execute(selects["decision_versions"], parameters).fetchall()
        ]
        edges = [
            EdgeRow.from_mapping(dict(zip(_EDGE_COLUMNS, row, strict=True)))
            for row in conn.execute(selects["decision_edges"], parameters).fetchall()
        ]
        scopes = [
            ScopeRow.from_mapping(dict(zip(_SCOPE_COLUMNS, row, strict=True)))
            for row in conn.execute(selects["decision_scopes"], parameters).fetchall()
        ]
        snapshot_hash = compute_graph_snapshot_hash(
            nodes,
            versions,
            edges,
            scopes,
            schema_version=HOSTED_SCHEMA_VERSION,
            retrieval_config_version=retrieval_config_version,
        )
    except GraphSnapshotValidationError as exc:
        _rollback_quietly(conn)
        raise HostedPushError(
            f"live projection rows for tenant {tenant_id} cannot form a canonical "
            f"snapshot: {exc}"
        ) from exc

    external_ref = f"projection-rebuilt#{snapshot_hash}"
    payload: dict[str, Any] = {
        "counts": {
            "edges": len(edges),
            "nodes": len(nodes),
            "scopes": len(scopes),
            "versions": len(versions),
        },
        "graph_snapshot_hash": snapshot_hash,
        "retrieval_config_version": retrieval_config_version,
        "schema_version": HOSTED_SCHEMA_VERSION,
    }
    # The idempotency key is content-keyed on the snapshot hash alone (no
    # payload, no timestamp), so re-pushing an unchanged graph is a replay
    # instead of an endless chain of projection.rebuilt events.
    event = LedgerEvent(
        tenant_id=tenant_id,
        source_id=source_id,
        event_type=LedgerEventType.PROJECTION_REBUILT,
        actor=PUSH_ACTOR,
        occurred_at=now,
        idempotency_key=derive_idempotency_key(
            source_id=source_id,
            event_type=LedgerEventType.PROJECTION_REBUILT,
            source_event_external_id=external_ref,
        ),
        source_event_external_id=external_ref,
        payload=payload,
        graph_snapshot_hash=snapshot_hash,
    )

    try:
        event_appended = (
            conn.execute(ledger_event_insert_sql(schema), event.as_insert_parameters()).fetchone()
            is not None
        )
        registered = (
            conn.execute(
                graph_snapshot_insert_sql(schema),
                {
                    "tenant_id": tenant_id,
                    "graph_snapshot_hash": snapshot_hash,
                    "schema_version": HOSTED_SCHEMA_VERSION,
                    "retrieval_config_version": retrieval_config_version,
                    "metadata": "{}",
                    "idempotency_key": event.idempotency_key,
                },
            ).fetchone()
            is not None
        )
        exists = conn.execute(
            f"SELECT graph_snapshot_id FROM {schema}.graph_snapshots "
            "WHERE tenant_id = %(tenant_id)s "
            "AND graph_snapshot_hash = %(graph_snapshot_hash)s;",
            {"tenant_id": tenant_id, "graph_snapshot_hash": snapshot_hash},
        ).fetchone()
        if exists is None:
            raise HostedPushError(
                f"graph snapshot {snapshot_hash[:12]} was neither registered nor "
                "already present after the registration transaction"
            )
        conn.commit()
    except HostedPushError:
        _rollback_quietly(conn)
        raise
    except Exception as exc:
        _rollback_quietly(conn)
        raise HostedPushError(f"snapshot registration failed: {exc}") from exc

    return SnapshotResult(
        snapshot_hash=snapshot_hash,
        registered=registered,
        event_appended=event_appended,
        nodes=len(nodes),
        versions=len(versions),
        edges=len(edges),
        scopes=len(scopes),
    )


def _rollback_quietly(conn: HostedConnection) -> None:
    """Best-effort rollback; the original failure is the one that must surface."""

    # The original failure stays primary; a rollback error on an
    # already-broken connection adds nothing the caller can act on.
    with contextlib.suppress(Exception):
        conn.rollback()


def _row_str(row: Mapping[str, Any], key: str, idempotency_key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise HostedPushError(
            f"event {idempotency_key!r} export row field {key!r} must be a "
            f"non-empty string, got {value!r}"
        )
    return value


def _row_optional_str(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    return str(value)


def _row_datetime(row: Mapping[str, Any], key: str, idempotency_key: str) -> datetime:
    value = row.get(key)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise HostedPushError(
                f"event {idempotency_key!r} field {key!r} is not an ISO-8601 "
                f"timestamp: {value!r}"
            ) from exc
    if not isinstance(value, datetime):
        raise HostedPushError(
            f"event {idempotency_key!r} field {key!r} must be a datetime or "
            f"ISO-8601 string, got {type(value).__name__}"
        )
    return value


def _row_json_object(row: Mapping[str, Any], key: str, idempotency_key: str) -> dict[str, Any]:
    value = row.get(key)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise HostedPushError(
                f"event {idempotency_key!r} field {key!r} is not valid JSON: {exc}"
            ) from exc
    if not isinstance(value, Mapping):
        raise HostedPushError(
            f"event {idempotency_key!r} field {key!r} must be a JSON object"
        )
    return dict(value)


def _require_repo_relative_path(value: str) -> None:
    if not value.strip():
        raise HostedPushError("span permalink must not be empty")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise HostedPushError(
            f"span permalink {value!r} must be a repo-relative path without '..'"
        )


def _require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise HostedPushError(f"{name} must be a sha256 hex string")


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise HostedPushError(f"invalid SQL identifier: {name!r}")
