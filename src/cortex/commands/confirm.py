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
- ``cortex candidates triage`` — the interactive confirm/reject ritual over
  the pending worklist (cortex#514): structured lane first, y/n/s/q per
  candidate, with scripted batch confirms via ``--accept-refs FILE`` and an
  explicit ``--accept-structured`` flag for structured-lane batch accepts.
  Triage emits decisions through the same envelope path as confirm/reject —
  one write path, no duplication.

Human-confirmed writes only — **there is no auto-confirm flag, and none may
be added** (non-negotiable; reject any such option in review). The
provisional lane never batch-accepts: ``--accept-structured`` applies to the
structured lane exclusively, and ``--accept-refs`` confirms only refs a
human explicitly enumerated. Confirmation
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from cortex.hosted.degradation import remediation_for
from cortex.hosted.derive_store import (
    AppendOutcome,
    DeriveEventStore,
    DeriveStoreError,
    derive_store_path,
)
from cortex.hosted.lanes import Lane
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

# Triage presents the structured lane first: structured sources (agent
# instructions, accepted ADRs, CODEOWNERS) are already human-authored
# normative artifacts, so they are the cheapest confirms and the right
# onboarding ramp. Lanes outside this order (malformed payloads) sort last.
_TRIAGE_LANE_ORDER: Mapping[str, int] = {
    Lane.STRUCTURED.value: 0,
    Lane.PROVISIONAL.value: 1,
}
# Display cap for the provenance excerpt preview in triage; the full excerpt
# stays in the store and the permalink points at the source.
EXCERPT_PREVIEW_CHARS = 120
# Why --accept-structured exists and why it never widens: the lane policy
# (cortex.hosted.lanes) — provisional material never auto-promotes; human
# confirmation is required per candidate. Structured-lane sources are
# human-authored normative artifacts, so batch-accepting them restates an
# authorship fact rather than laundering automation.
ACCEPT_STRUCTURED_POLICY = (
    "structured-lane sources are human-authored normative artifacts (agent "
    "instructions, accepted ADRs, CODEOWNERS); the provisional lane never "
    "batch-accepts — per-candidate human confirmation is required "
    "(lane policy, cortex.hosted.lanes)"
)
# End-of-session hint chain (cortex#514): confirm -> push -> ask.
TRIAGE_NEXT_STEPS = (
    "next: run `cortex push` to project confirmed decisions, then "
    '`cortex ask "<question>"` for cited answers'
)
_TRIAGE_PROMPT = "confirm? [y]es / [n]o / [s]kip / [q]uit"


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
            f"no derive store found at {db_path}; "
            f"{remediation_for('derive_store_missing')}"
        )
    return DeriveEventStore(db_path)


def count_pending_candidates(project_root: Path) -> int | None:
    """Count proposed-but-undecided candidates in the local derive store.

    Returns ``None`` when no store exists yet (nothing derived) so callers
    can render their hint without a count. Store read failures raise —
    a corrupt store is never silently counted as zero.
    """

    db_path = derive_store_path(project_root.resolve())
    if not db_path.exists():
        return None
    with DeriveEventStore(db_path) as store:
        candidates, statuses = load_candidate_rows(store.export_events())
    return sum(1 for candidate in candidates if candidate.event_hash not in statuses)


def _record_decision(
    store: DeriveEventStore,
    candidate: CandidateRow,
    *,
    event_type: LedgerEventType,
    actor_id: str,
) -> tuple[LedgerEvent, AppendOutcome]:
    """The one confirm/reject write path: envelope-validated build + append.

    Both the per-ref commands and triage route through here, so the
    DECISION_CONFIRMED/REJECTED emission cannot fork (span citations and
    payload shape are enforced once, in ``build_decision_event`` and the
    envelope itself).
    """

    event = build_decision_event(
        candidate,
        event_type=event_type,
        actor_id=actor_id,
        occurred_at=datetime.now(tz=UTC),
    )
    outcome = store.append_events([event])
    return event, outcome


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
            event, outcome = _record_decision(
                store, candidate, event_type=event_type, actor_id=actor_id
            )
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


# ---------------------------------------------------------------------------
# cortex candidates triage (cortex#514)
# ---------------------------------------------------------------------------


def order_for_triage(
    candidates: Sequence[CandidateRow],
    statuses: Mapping[str, str],
    *,
    lane: str | None = None,
    limit: int | None = None,
) -> tuple[CandidateRow, ...]:
    """Select and order the pending triage queue: structured lane first.

    Only proposed-but-undecided candidates qualify. The sort is stable, so
    store order is preserved within each lane; lanes outside the known order
    (malformed payloads) sort last and stay visible rather than vanishing.
    """

    pending = [
        candidate for candidate in candidates if candidate.event_hash not in statuses
    ]
    if lane is not None:
        pending = [candidate for candidate in pending if candidate.lane == lane]
    pending.sort(
        key=lambda candidate: _TRIAGE_LANE_ORDER.get(candidate.lane, len(_TRIAGE_LANE_ORDER))
    )
    if limit is not None:
        pending = pending[:limit]
    return tuple(pending)


def parse_accept_refs(text: str) -> tuple[str, ...]:
    """Parse an ``--accept-refs`` file: one event ref per line.

    Blank lines and ``#`` comments are ignored. An effectively empty file is
    an error — a scripted batch confirm that confirms nothing is a mistake
    the operator should see, not a silent no-op.
    """

    refs: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            refs.append(line)
    if not refs:
        raise CandidateCommandError(
            "--accept-refs file contains no event refs (blank lines and "
            "`#` comments are ignored)"
        )
    return tuple(refs)


def _primary_span_fields(candidate: CandidateRow) -> tuple[str, str | None]:
    """Best-effort permalink + excerpt preview from the candidate's first span."""

    spans = candidate.payload.get("spans")
    permalink = "(permalink not recorded)"
    excerpt: str | None = None
    if isinstance(spans, Sequence) and not isinstance(spans, str) and spans:
        first = spans[0]
        if isinstance(first, Mapping):
            raw_permalink = first.get("permalink")
            if isinstance(raw_permalink, str) and raw_permalink.strip():
                permalink = raw_permalink
            raw_excerpt = first.get("excerpt")
            if isinstance(raw_excerpt, str) and raw_excerpt.strip():
                first_line = raw_excerpt.strip().splitlines()[0]
                if len(first_line) > EXCERPT_PREVIEW_CHARS:
                    first_line = first_line[:EXCERPT_PREVIEW_CHARS] + "..."
                excerpt = first_line
    return permalink, excerpt


def _render_triage_candidate(candidate: CandidateRow, *, position: int, total: int) -> str:
    permalink, excerpt = _primary_span_fields(candidate)
    lines = [
        f"[{position}/{total}] ref: {candidate.event_hash[:12]}  "
        f"lane: {candidate.lane} ({candidate.source_type})"
    ]
    lines.extend(f"  | {text_line}" for text_line in candidate.decision_text.splitlines())
    lines.append(
        f"  provenance: {candidate.external_id or '(external id not recorded)'} "
        f"({len(candidate.span_hashes)} span(s))"
    )
    if excerpt is not None:
        lines.append(f"  excerpt: {excerpt}")
    lines.append(f"  permalink: {permalink}")
    return "\n".join(lines)


@dataclass(frozen=True)
class TriageSummary:
    """End-of-session counts for the triage summary line."""

    confirmed: int = 0
    rejected: int = 0
    skipped: int = 0
    failed: int = 0
    already_confirmed: int = 0


def _interactive_triage(
    store: DeriveEventStore, queue: Sequence[CandidateRow], *, actor_id: str
) -> TriageSummary:
    """One prompt per pending candidate: y(confirm) / n(reject) / s(skip) / q(quit).

    Each decision persists immediately through the one write path. A
    candidate whose confirmation the envelope refuses (e.g. span-less) is
    reported visibly, counted as failed, and stays pending — never written.
    """

    confirmed = rejected = skipped = failed = 0
    total = len(queue)
    for position, candidate in enumerate(queue, start=1):
        click.echo(_render_triage_candidate(candidate, position=position, total=total))
        choice = click.prompt(
            _TRIAGE_PROMPT, type=click.Choice(("y", "n", "s", "q")), show_choices=False
        )
        if choice == "q":
            click.echo(f"quit: {total - position + 1} candidate(s) left unreviewed")
            break
        if choice == "s":
            skipped += 1
            continue
        event_type = (
            LedgerEventType.DECISION_CONFIRMED
            if choice == "y"
            else LedgerEventType.DECISION_REJECTED
        )
        verb = _DECISION_EVENT_TYPES[event_type.value]
        try:
            event, _ = _record_decision(
                store, candidate, event_type=event_type, actor_id=actor_id
            )
        except LedgerEventValidationError as exc:
            failed += 1
            click.echo(
                f"error: candidate {candidate.event_hash[:12]} failed envelope "
                f"validation; it stays pending: {exc}",
                err=True,
            )
            continue
        if verb == "confirmed":
            confirmed += 1
        else:
            rejected += 1
        click.echo(
            f"{verb}: candidate {candidate.event_hash[:12]} -> event "
            f"{event.event_hash[:12]} ({len(event.source_span_hashes)} span citation(s))"
        )
    return TriageSummary(
        confirmed=confirmed, rejected=rejected, skipped=skipped, failed=failed
    )


def _batch_confirm(
    store: DeriveEventStore,
    selected: Sequence[CandidateRow],
    *,
    actor_id: str,
    label: str,
) -> int:
    """Validate-all-then-persist batch confirmation through the one envelope.

    If any candidate fails envelope validation, the aggregated error names
    every failure and nothing is written — a failed batch leaves the store
    untouched and recoverable (same discipline as ``run_derive``).
    """

    occurred_at = datetime.now(tz=UTC)
    events: list[LedgerEvent] = []
    errors: list[str] = []
    for candidate in selected:
        try:
            events.append(
                build_decision_event(
                    candidate,
                    event_type=LedgerEventType.DECISION_CONFIRMED,
                    actor_id=actor_id,
                    occurred_at=occurred_at,
                )
            )
        except (CandidateCommandError, LedgerEventValidationError) as exc:
            errors.append(f"{candidate.event_hash[:12]}: {exc}")
    if errors:
        raise CandidateCommandError(
            f"{label}: {len(errors)} candidate(s) failed envelope validation; "
            "nothing was written:\n" + "\n".join(errors)
        )
    store.append_events(events)
    return len(events)


def _selected_for_refs(
    candidates: Sequence[CandidateRow],
    statuses: Mapping[str, str],
    refs: Sequence[str],
) -> tuple[tuple[CandidateRow, ...], int]:
    """Resolve ``--accept-refs`` entries; aggregate every failure before writing.

    Returns the deduplicated pending candidates plus the count of refs that
    were already confirmed (a visible no-op, mirroring the per-ref confirm
    command). A ref pointing at a rejected candidate is an error — a batch
    file must not silently flip a recorded rejection.
    """

    errors: list[str] = []
    selected: list[CandidateRow] = []
    seen: set[str] = set()
    already_confirmed = 0
    for ref in refs:
        try:
            candidate = resolve_candidate(tuple(candidates), ref)
        except CandidateCommandError as exc:
            errors.append(str(exc))
            continue
        if candidate.event_hash in seen:
            continue
        seen.add(candidate.event_hash)
        existing = statuses.get(candidate.event_hash)
        if existing == "confirmed":
            already_confirmed += 1
            continue
        if existing is not None:
            errors.append(
                f"candidate {candidate.event_hash[:12]} is already {existing}; "
                "refusing to record a contradictory confirmation — supersede is "
                "the only edit verb (cortex.hosted.graph_writes)"
            )
            continue
        selected.append(candidate)
    if errors:
        raise CandidateCommandError(
            "--accept-refs: nothing was written:\n" + "\n".join(errors)
        )
    return tuple(selected), already_confirmed


def _echo_triage_summary(summary: TriageSummary, *, pending_after: int) -> None:
    parts = [
        f"{summary.confirmed} confirmed",
        f"{summary.rejected} rejected",
        f"{summary.skipped} skipped",
    ]
    if summary.failed:
        parts.append(f"{summary.failed} failed")
    if summary.already_confirmed:
        parts.append(f"{summary.already_confirmed} already confirmed")
    click.echo(f"triage: {', '.join(parts)} ({pending_after} still pending)")
    if summary.confirmed:
        click.echo(TRIAGE_NEXT_STEPS)


@candidates_group.command("triage")
@click.option(
    "--lane",
    type=click.Choice(sorted(_TRIAGE_LANE_ORDER)),
    default=None,
    help="Triage only this lane (default: structured lane first, then provisional).",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=None,
    help="Review at most N pending candidates this session.",
)
@click.option(
    "--accept-refs",
    "accept_refs",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "Batch-confirm the event refs listed in FILE (one per line, `#` "
        "comments allowed). The file is a human-authored explicit list; "
        "validate-all-then-persist, so a bad ref writes nothing."
    ),
)
@click.option(
    "--accept-structured",
    is_flag=True,
    help=(
        "Batch-confirm every pending structured-lane candidate without "
        f"prompting. Allowed only for the structured lane: {ACCEPT_STRUCTURED_POLICY}."
    ),
)
@click.option(
    "--by",
    "actor_id",
    default=getpass.getuser,
    show_default="current OS user",
    help="Human actor recorded on every decision event this session emits.",
)
@_PATH_OPTION
def triage_command(
    *,
    lane: str | None,
    limit: int | None,
    accept_refs: Path | None,
    accept_structured: bool,
    actor_id: str,
    project_root: Path,
) -> None:
    """Interactively confirm/reject pending candidates (structured lane first).

    The confirm-before-ask ritual (cortex#514): iterate proposed candidates,
    show decision text + lane + provenance + permalink, and record y/n/s/q
    per candidate through the same DECISION_CONFIRMED/REJECTED envelope as
    `cortex candidates confirm`/`reject`. There is no auto-confirm: batch
    accepts exist only as `--accept-refs FILE` (explicit human-listed refs)
    and `--accept-structured` (structured lane exclusively, per lane policy).
    """

    if accept_refs is not None and accept_structured:
        click.echo(
            "error: --accept-refs and --accept-structured are mutually exclusive; "
            "run one batch mode at a time",
            err=True,
        )
        sys.exit(2)
    if accept_structured and lane == Lane.PROVISIONAL.value:
        click.echo(
            f"error: --accept-structured never applies to the provisional lane; "
            f"{ACCEPT_STRUCTURED_POLICY}",
            err=True,
        )
        sys.exit(2)
    if accept_refs is not None and (lane is not None or limit is not None):
        click.echo(
            "error: --accept-refs confirms an explicit ref list; --lane/--limit "
            "do not apply to it",
            err=True,
        )
        sys.exit(2)

    try:
        with _open_store(project_root) as store:
            candidates, statuses = load_candidate_rows(store.export_events())
            pending_total = sum(
                1 for candidate in candidates if candidate.event_hash not in statuses
            )
            if accept_refs is not None:
                try:
                    refs_text = accept_refs.read_text(encoding="utf-8")
                except OSError as exc:
                    raise CandidateCommandError(
                        f"cannot read --accept-refs file {accept_refs}: {exc}"
                    ) from exc
                selected, already_confirmed = _selected_for_refs(
                    candidates, statuses, parse_accept_refs(refs_text)
                )
                confirmed = _batch_confirm(
                    store, selected, actor_id=actor_id, label="--accept-refs"
                )
                summary = TriageSummary(
                    confirmed=confirmed, already_confirmed=already_confirmed
                )
            elif accept_structured:
                queue = order_for_triage(
                    candidates, statuses, lane=Lane.STRUCTURED.value, limit=limit
                )
                confirmed = _batch_confirm(
                    store, queue, actor_id=actor_id, label="--accept-structured"
                )
                summary = TriageSummary(confirmed=confirmed)
            else:
                queue = order_for_triage(candidates, statuses, lane=lane, limit=limit)
                if not queue:
                    if not candidates:
                        click.echo(
                            "triage: no candidates proposed; run `cortex derive` "
                            "to extract some"
                        )
                    else:
                        click.echo("triage: no pending candidates to review")
                    return
                summary = _interactive_triage(store, queue, actor_id=actor_id)
    except (CandidateCommandError, DeriveStoreError, LedgerEventValidationError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    _echo_triage_summary(
        summary,
        pending_after=pending_total - summary.confirmed - summary.rejected,
    )
    if summary.failed:
        sys.exit(1)
