"""`cortex candidates` — human confirmation over the local derive store.

Stage 0 issue #359. `cortex derive` proposes candidate decisions
(``CANDIDATE_PROPOSED`` events in the local replay-export SQLite store);
this command group is the human curation loop over that worklist:

- ``cortex candidates list`` — show proposed candidates with lane assignment
  and provenance (source external id, span count, event-hash ref). This is
  the operator's *local input queue*, not the hosted decision corpus — see
  the read-surface inventory in ``cortex.hosted.ask_surface`` (cortex#382):
  the listing never reads the hosted ledger and renders provenance on every
  row.
- ``cortex candidates confirm <event-ref>`` — emit a ``DECISION_CONFIRMED``
  ledger event into the same local store through the one envelope. The
  confirmation cites the candidate's span hashes, and the envelope enforces
  spans on confirm (``SOURCE_SPAN_REQUIRED_EVENTS``).
- ``cortex candidates reject <event-ref>`` — emit ``DECISION_REJECTED``,
  carrying the same span citations for provenance.

Human-confirmed writes only — **there is no auto-confirm flag, and none may
be added** (non-negotiable; reject any such option in review). Confirmation
is the act that promotes a candidate toward graph entry, and the lane policy
(`cortex.hosted.lanes`) already forbids laundering that act through
automation. Payload conventions follow ``cortex.hosted.graph_writes``: the
status itself is derived from the event type (``plan_status_transition``),
so the payload carries only the candidate reference material a hosted loader
needs to resolve the transition.
"""

from __future__ import annotations

import getpass
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from cortex.hosted.derive_store import (
    DeriveEventStore,
    DeriveStoreError,
    derive_store_path,
)
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    LedgerEventValidationError,
    derive_idempotency_key,
)

MIN_EVENT_REF_LENGTH = 8
CONFIRM_ACTOR_TYPE = "human"
_DECISION_EVENT_TYPES = {
    LedgerEventType.DECISION_CONFIRMED.value: "confirmed",
    LedgerEventType.DECISION_REJECTED.value: "rejected",
}


class CandidateCommandError(ValueError):
    """Raised when a candidates subcommand cannot proceed; always names why."""


@dataclass(frozen=True)
class CandidateRow:
    """One CANDIDATE_PROPOSED row decoded from the derive store export."""

    event_hash: str
    tenant_id: str
    source_id: str
    idempotency_key: str
    external_id: str | None
    span_hashes: tuple[str, ...]
    payload: Mapping[str, Any]

    @property
    def decision_text(self) -> str:
        text = self.payload.get("decision_text")
        return text if isinstance(text, str) else "(decision text not recorded)"

    @property
    def lane(self) -> str:
        lane_assignment = self.payload.get("lane_assignment")
        if isinstance(lane_assignment, Mapping):
            lane = lane_assignment.get("lane")
            if isinstance(lane, str):
                return lane
        return "(lane not recorded)"

    @property
    def source_type(self) -> str:
        source_type = self.payload.get("source_type")
        return source_type if isinstance(source_type, str) else "(source type not recorded)"


def load_candidate_rows(
    rows: tuple[dict[str, Any], ...],
) -> tuple[tuple[CandidateRow, ...], dict[str, str]]:
    """Split exported rows into candidates and a candidate-hash -> status map."""

    candidates: list[CandidateRow] = []
    statuses: dict[str, str] = {}
    for row in rows:
        event_type = row["event_type"]
        if event_type == LedgerEventType.CANDIDATE_PROPOSED.value:
            payload = json.loads(row["payload"])
            if not isinstance(payload, dict):
                raise CandidateCommandError(
                    f"candidate event {row['event_hash']} carries a non-object payload"
                )
            candidates.append(
                CandidateRow(
                    event_hash=row["event_hash"],
                    tenant_id=row["tenant_id"],
                    source_id=row["source_id"],
                    idempotency_key=row["idempotency_key"],
                    external_id=row["source_event_external_id"],
                    span_hashes=tuple(row["source_span_hashes"]),
                    payload=payload,
                )
            )
            continue
        status = _DECISION_EVENT_TYPES.get(event_type)
        if status is not None:
            payload = json.loads(row["payload"])
            candidate_hash = (
                payload.get("candidate_event_hash") if isinstance(payload, dict) else None
            )
            if isinstance(candidate_hash, str):
                statuses[candidate_hash] = status
    return tuple(candidates), statuses


def resolve_candidate(candidates: tuple[CandidateRow, ...], event_ref: str) -> CandidateRow:
    """Resolve an event-hash ref (full hash or unique prefix) to one candidate."""

    ref = event_ref.strip().lower()
    if len(ref) < MIN_EVENT_REF_LENGTH:
        raise CandidateCommandError(
            f"event ref {event_ref!r} is too short; use at least "
            f"{MIN_EVENT_REF_LENGTH} hex characters of the candidate event hash"
        )
    matches = tuple(
        candidate for candidate in candidates if candidate.event_hash.startswith(ref)
    )
    if not matches:
        raise CandidateCommandError(
            f"no proposed candidate matches event ref {event_ref!r}; "
            "run `cortex candidates list` to see refs"
        )
    if len(matches) > 1:
        listing = ", ".join(candidate.event_hash[:12] for candidate in matches)
        raise CandidateCommandError(
            f"event ref {event_ref!r} is ambiguous; it matches: {listing}"
        )
    return matches[0]


def build_decision_event(
    candidate: CandidateRow,
    *,
    event_type: LedgerEventType,
    actor_id: str,
    occurred_at: datetime,
) -> LedgerEvent:
    """Build the confirm/reject event through the one ledger envelope.

    The decision cites the candidate's span hashes; the envelope itself
    enforces that ``decision.confirmed`` carries at least one span
    (``SOURCE_SPAN_REQUIRED_EVENTS``), so a span-less candidate fails closed
    here rather than producing an uncited confirmation.
    """

    if event_type.value not in _DECISION_EVENT_TYPES:
        raise CandidateCommandError(
            f"{event_type.value} is not a candidate confirmation event type"
        )
    payload: dict[str, Any] = {
        # graph_writes.plan_status_transition derives the projected status
        # from the event type; the payload carries the candidate reference
        # material a hosted loader needs to resolve the transition.
        "candidate_event_hash": candidate.event_hash,
        "candidate_idempotency_key": candidate.idempotency_key,
        "decision_text": candidate.decision_text,
    }
    external_ref = f"{candidate.event_hash}#{_DECISION_EVENT_TYPES[event_type.value]}"
    return LedgerEvent(
        tenant_id=candidate.tenant_id,
        source_id=candidate.source_id,
        event_type=event_type,
        actor=ActorRef(actor_type=CONFIRM_ACTOR_TYPE, actor_id=actor_id),
        occurred_at=occurred_at,
        idempotency_key=derive_idempotency_key(
            source_id=candidate.source_id,
            event_type=event_type,
            source_event_external_id=external_ref,
            payload=payload,
        ),
        source_event_external_id=external_ref,
        source_span_hashes=candidate.span_hashes,
        payload=payload,
        metadata={"cli": f"cortex candidates {_DECISION_EVENT_TYPES[event_type.value]}"},
    )


def _open_store(project_root: Path) -> DeriveEventStore:
    db_path = derive_store_path(project_root.resolve())
    if not db_path.exists():
        raise CandidateCommandError(
            f"no derive store found at {db_path}; run `cortex derive` first"
        )
    return DeriveEventStore(db_path)


# Shared, typed `--path` option decorator (kept as one definition so the three
# subcommands cannot drift apart on the project-root contract).
_PATH_OPTION = click.option(
    "--path",
    "project_root",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)


@click.group("candidates", context_settings={"help_option_names": ["-h", "--help"]})
def candidates_group() -> None:
    """Review and confirm derived candidate decisions (human-only writes)."""


@candidates_group.command("list")
@_PATH_OPTION
def list_command(*, project_root: Path) -> None:
    """List proposed candidates with lane, provenance, and status.

    This enumerates the local, rebuildable derive export — the operator's
    own confirmation worklist — never the hosted decision corpus
    (see the cortex#382 read-surface inventory in
    ``cortex.hosted.ask_surface``).
    """

    try:
        with _open_store(project_root) as store:
            candidates, statuses = load_candidate_rows(store.export_events())
    except (CandidateCommandError, DeriveStoreError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    if not candidates:
        click.echo("candidates: none proposed; run `cortex derive` to extract some")
        return
    click.echo(f"candidates: {len(candidates)} proposed")
    for candidate in candidates:
        status = statuses.get(candidate.event_hash, "proposed")
        click.echo(f"ref: {candidate.event_hash[:12]}  [{status}]")
        click.echo(f"  lane: {candidate.lane} ({candidate.source_type})")
        click.echo(
            f"  provenance: {candidate.external_id or '(external id not recorded)'} "
            f"({len(candidate.span_hashes)} span(s))"
        )
        click.echo(f"  text: {candidate.decision_text.splitlines()[0]}")


def _emit_decision(
    *,
    project_root: Path,
    event_ref: str,
    event_type: LedgerEventType,
    actor_id: str,
) -> None:
    verb = _DECISION_EVENT_TYPES[event_type.value]
    try:
        with _open_store(project_root) as store:
            candidates, statuses = load_candidate_rows(store.export_events())
            candidate = resolve_candidate(candidates, event_ref)
            existing = statuses.get(candidate.event_hash)
            if existing == verb:
                click.echo(
                    f"candidate {candidate.event_hash[:12]} is already {verb}; nothing to do"
                )
                return
            if existing is not None:
                raise CandidateCommandError(
                    f"candidate {candidate.event_hash[:12]} is already {existing}; "
                    f"refusing to record a contradictory {verb} — supersede is the "
                    "only edit verb (cortex.hosted.graph_writes)"
                )
            event = build_decision_event(
                candidate,
                event_type=event_type,
                actor_id=actor_id,
                occurred_at=datetime.now(tz=UTC),
            )
            outcome = store.append_events([event])
    except (CandidateCommandError, DeriveStoreError, LedgerEventValidationError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    click.echo(
        f"{verb}: candidate {candidate.event_hash[:12]} -> event "
        f"{event.event_hash[:12]} ({outcome.inserted} inserted, "
        f"{len(event.source_span_hashes)} span citation(s))"
    )


@candidates_group.command("confirm")
@click.argument("event_ref")
@click.option(
    "--by",
    "actor_id",
    default=getpass.getuser,
    show_default="current OS user",
    help="Human actor recorded on the confirmation event.",
)
@_PATH_OPTION
def confirm_command(*, event_ref: str, actor_id: str, project_root: Path) -> None:
    """Confirm a candidate, citing its span hashes (human-only; no auto-confirm)."""

    _emit_decision(
        project_root=project_root,
        event_ref=event_ref,
        event_type=LedgerEventType.DECISION_CONFIRMED,
        actor_id=actor_id,
    )


@candidates_group.command("reject")
@click.argument("event_ref")
@click.option(
    "--by",
    "actor_id",
    default=getpass.getuser,
    show_default="current OS user",
    help="Human actor recorded on the rejection event.",
)
@_PATH_OPTION
def reject_command(*, event_ref: str, actor_id: str, project_root: Path) -> None:
    """Reject a candidate (human-only; the event still cites the candidate spans)."""

    _emit_decision(
        project_root=project_root,
        event_ref=event_ref,
        event_type=LedgerEventType.DECISION_REJECTED,
        actor_id=actor_id,
    )
