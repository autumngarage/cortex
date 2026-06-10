"""Source-timestamp total ordering for hosted ledger events (cortex#313).

Why source timestamps, never webhook arrival: GitHub and Slack deliveries
retry, batch, and reorder, so the ledger records two times per event —
``occurred_at`` (when the action happened at the source, timezone-required)
and ``ingested_at`` (when our webhook happened to receive it). A supersede
authored at 15:00 can arrive *before* a redelivered copy of the decision it
supersedes (authored 14:00, redelivered 15:30). If currency followed
arrival, every redelivery storm could silently flip which decision is
current. The master-plan rule this module implements: **supersedes are
ordered by source timestamp, never webhook arrival.** Arrival participates
only as a deterministic tiebreak when two events claim the same source
instant, and ``event_hash`` closes the comparison into a total order so
every replay sorts an event batch identically.

The total order, ascending (the maximum element is the current event):

1. ``occurred_at`` — the source timestamp; the only semantically
   meaningful level.
2. ``ingested_at`` — arrival tiebreak. Events that have not been persisted
   (no ``ingested_at`` — e.g. in-memory ``LedgerEvent`` instances before
   insert; the column is DB-assigned) order *after* every persisted
   arrival at the same ``occurred_at``: their arrival has not happened
   yet, so it is later than any recorded one. The absence is an explicit
   branch of this contract, not a fallback.
3. ``event_hash`` — deterministic content hash. Two events tying at every
   level share the hash and therefore identical content, so either is the
   same answer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, TypeVar

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

# Placeholder for the arrival slot of unpersisted events. The presence flag
# preceding it short-circuits tuple comparison, so this value is never
# compared against a real arrival; it only keeps the key shape uniform.
_UNPERSISTED_ARRIVAL = datetime(1970, 1, 1, tzinfo=UTC)

OrderingKey = tuple[datetime, int, datetime, str]

_EventT = TypeVar("_EventT")


class EventOrderingError(ValueError):
    """Raised when an event cannot participate in the canonical total order."""


def ordering_key(event: object) -> OrderingKey:
    """Return the canonical sort key ``(occurred_at, arrival, event_hash)``.

    Accepts ``LedgerEvent`` instances and plain mappings carrying
    ``occurred_at`` / ``ingested_at`` / ``event_hash`` through one accessor.
    Treat the key as opaque: compare it, do not unpack it.
    """

    occurred_at = _read_field(event, "occurred_at")
    if not _is_aware_datetime(occurred_at):
        raise EventOrderingError("occurred_at must be a timezone-aware datetime")
    event_hash = _read_field(event, "event_hash")
    if not isinstance(event_hash, str) or not _SHA256_RE.match(event_hash):
        raise EventOrderingError("event_hash must be a sha256 hex string")
    ingested_at = _read_field(event, "ingested_at", required=False)
    if ingested_at is None:
        return (occurred_at, 1, _UNPERSISTED_ARRIVAL, event_hash)
    if not _is_aware_datetime(ingested_at):
        raise EventOrderingError("ingested_at must be a timezone-aware datetime when present")
    return (occurred_at, 0, ingested_at, event_hash)


def resolve_current(events: Iterable[_EventT]) -> _EventT:
    """Return the current event among one decision's contention group.

    The caller groups events per decision (for example every
    ``decision.confirmed`` / ``decision.superseded`` event addressing one
    node); this returns the event that wins under the source-timestamp total
    order. Out-of-order webhook delivery — a later-authored supersede
    ingested earlier, or an earlier-authored decision redelivered later —
    cannot flip the outcome, because ``occurred_at`` is compared before
    ``ingested_at`` ever participates.
    """

    contenders = list(events)
    if not contenders:
        raise EventOrderingError(
            "resolve_current requires at least one event; an empty group has no current decision"
        )
    return max(contenders, key=ordering_key)


def _read_field(event: object, name: str, *, required: bool = True) -> Any:
    value = event.get(name) if isinstance(event, Mapping) else getattr(event, name, None)
    if value is None and required:
        raise EventOrderingError(f"event is missing required ordering field {name!r}")
    return value


def _is_aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )
