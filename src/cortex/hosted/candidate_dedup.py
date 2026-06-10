"""Exact-hash dedup for identical decision candidates (cortex#318, #319).

Re-derives over unchanged sources, webhook redeliveries with fresh external
ids, and parallel derive runs all produce ``candidate.proposed`` events whose
*content* is identical even when their ledger identity (idempotency key,
event hash, ``occurred_at``) differs. Writing each copy as its own decision
node would fragment confirmations and supersedes across clones of one
decision. This module folds those exact duplicates into one survivor
**before** any graph write, while retaining every duplicate's provenance —
counted and attributed, never dropped.

**The identity basis is named and versioned** (``CANDIDATE_IDENTITY_VERSION
= 1``). A candidate's identity is the sha256 over the canonical JSON of:

1. ``normalized_decision_text`` — the decision text normalized in exactly
   two steps, in order:

   a. ``" ".join(text.split())`` — split on Unicode whitespace, rejoin with
      a single ASCII space. This strips leading/trailing whitespace and
      collapses every internal whitespace run (spaces, tabs, newlines) to
      one space.
   b. ``str.casefold()`` — aggressive Unicode case-insensitive folding
      (lowercases, and e.g. folds ``ß`` to ``ss``).

2. ``span_hashes`` — the candidate's source-span hashes as a sorted,
   de-duplicated list (set semantics: order and repetition never change
   identity).
3. ``scopes`` — the candidate's proposed scopes as a sorted, de-duplicated
   list of ``[scope_type, normalized_value]`` pairs (set semantics; the raw
   ``value`` spelling does not participate — ``normalized_value`` is the
   scope identity per ``cortex.hosted.scopes``).

``tenant_id`` is deliberately excluded from the hash: like the graph
snapshot hash, candidate identity is content-addressed, and tenant scoping
lives in the grouping key (``(tenant_id, identity_hash)``) so identical
content in two tenants can never merge.

Because span hashes participate in the identity, two candidates with the
same text but different source spans are **not** duplicates under v1 —
exact-hash dedup is deliberately conservative. **Near-duplicate semantic
merge is explicitly out of scope** here; that is future work tracked as
cortex#421.

**The cortex#318/#319 boundary with cortex#487, recorded here:** absorbed
duplicates never became decision *nodes*, so dedup produces **no
``decision_edges`` rows of any type**. The schema's ``duplicates`` edge is
the cortex#487 merge representation *between existing nodes*
(``graph_writes.plan_supersede(merge=True)``), and ``derived_from`` is
likewise node-level vocabulary. Provenance for absorbed duplicates is
retained instead as (a) the survivor version's merged ``source_span_hashes``
set and (b) a metadata attribution list naming every absorbed event — see
``survivor_write_material``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cortex.hosted.event_ordering import ordering_key
from cortex.hosted.ledger_events import LedgerEvent, LedgerEventType
from cortex.hosted.scopes import ScopeType

CANDIDATE_IDENTITY_VERSION = 1

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class CandidateDedupError(ValueError):
    """Raised when candidate material cannot participate in exact-hash dedup."""


def normalize_decision_text(text: str) -> str:
    """Normalize decision text for identity hashing (documented contract).

    Exactly two steps, in order: collapse whitespace
    (``" ".join(text.split())``), then ``casefold()``. Changing either step
    changes every persisted identity hash and requires a
    ``CANDIDATE_IDENTITY_VERSION`` bump.
    """

    if not isinstance(text, str):
        raise CandidateDedupError("decision_text must be a string")
    collapsed = " ".join(text.split())
    if not collapsed:
        raise CandidateDedupError("decision_text must not be empty or whitespace-only")
    return collapsed.casefold()


def candidate_identity_material(
    *,
    decision_text: str,
    span_hashes: Iterable[str],
    scopes: Iterable[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Return the exact mapping ``candidate_identity_hash`` hashes.

    Public so callers and tests can inspect the canonical serialization
    instead of trusting an opaque digest (same pattern as
    ``graph_snapshot.graph_snapshot_hash_material``).
    """

    normalized_spans = _normalized_span_hashes(span_hashes)
    normalized_scopes = _normalized_scopes(scopes)
    return {
        "identity_version": CANDIDATE_IDENTITY_VERSION,
        "normalized_decision_text": normalize_decision_text(decision_text),
        "scopes": [list(pair) for pair in normalized_scopes],
        "span_hashes": list(normalized_spans),
    }


def candidate_identity_hash(
    *,
    decision_text: str,
    span_hashes: Iterable[str],
    scopes: Iterable[tuple[str, str]] = (),
) -> str:
    """Compute the versioned exact-content identity hash for one candidate."""

    return _hash_mapping(
        candidate_identity_material(
            decision_text=decision_text, span_hashes=span_hashes, scopes=scopes
        )
    )


@dataclass(frozen=True)
class CandidateIdentity:
    """The named identity basis of one candidate, plus its hash."""

    identity_hash: str
    identity_version: int
    normalized_decision_text: str
    span_hashes: tuple[str, ...]
    scopes: tuple[tuple[str, str], ...]

    @classmethod
    def from_event(cls, event: LedgerEvent) -> CandidateIdentity:
        """Extract the identity basis from one ``candidate.proposed`` event.

        Reads ``payload["decision_text"]``, the event's
        ``source_span_hashes``, and ``payload["proposed_scopes"]`` (the
        extractor payload shape: mappings carrying ``scope_type`` and
        ``normalized_value``). Anything malformed fails closed.
        """

        if not isinstance(event, LedgerEvent):
            raise CandidateDedupError(
                "dedup consumes LedgerEvent instances; got "
                f"{type(event).__name__}"
            )
        if event.event_type is not LedgerEventType.CANDIDATE_PROPOSED:
            raise CandidateDedupError(
                "exact-hash dedup only applies to candidate.proposed events; "
                f"got {event.event_type.value} (callers filter explicitly — "
                "nothing is skipped silently)"
            )
        decision_text = event.payload.get("decision_text")
        if not isinstance(decision_text, str):
            raise CandidateDedupError(
                "candidate.proposed payload is missing required key 'decision_text'"
            )
        raw_scopes = event.payload.get("proposed_scopes", [])
        if isinstance(raw_scopes, (str, bytes)) or not isinstance(raw_scopes, Iterable):
            raise CandidateDedupError(
                "payload 'proposed_scopes' must be a sequence of scope payloads"
            )
        scope_pairs: list[tuple[str, str]] = []
        for entry in raw_scopes:
            if not isinstance(entry, Mapping):
                raise CandidateDedupError(
                    "each proposed scope must be a mapping with scope_type "
                    "and normalized_value"
                )
            scope_type = entry.get("scope_type")
            normalized_value = entry.get("normalized_value")
            if not isinstance(scope_type, str) or not isinstance(normalized_value, str):
                raise CandidateDedupError(
                    "proposed scope payloads require string scope_type and "
                    "normalized_value"
                )
            scope_pairs.append((scope_type, normalized_value))
        material = candidate_identity_material(
            decision_text=decision_text,
            span_hashes=event.source_span_hashes,
            scopes=scope_pairs,
        )
        return cls(
            identity_hash=_hash_mapping(material),
            identity_version=CANDIDATE_IDENTITY_VERSION,
            normalized_decision_text=material["normalized_decision_text"],
            span_hashes=tuple(material["span_hashes"]),
            scopes=tuple((pair[0], pair[1]) for pair in material["scopes"]),
        )


@dataclass(frozen=True)
class AbsorbedDuplicate:
    """Attribution record for one duplicate folded into a survivor (#319)."""

    event_hash: str
    idempotency_key: str
    occurred_at: datetime
    source_event_external_id: str | None
    span_hashes: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "event_hash": self.event_hash,
            "idempotency_key": self.idempotency_key,
            "occurred_at": self.occurred_at.isoformat(),
            "source_event_external_id": self.source_event_external_id,
            "span_hashes": list(self.span_hashes),
        }


@dataclass(frozen=True)
class DedupGroup:
    """One identity class: the surviving event plus every absorbed duplicate.

    ``merged_span_hashes`` is the sorted union of span hashes across the
    survivor and every absorbed duplicate. Under identity v1 that union
    always equals the survivor's own (sorted, de-duplicated) span set —
    span hashes participate in the identity — and ``dedup_candidates``
    asserts that arithmetic as a runtime invariant rather than assuming it.
    """

    identity: CandidateIdentity
    survivor: LedgerEvent
    absorbed: tuple[LedgerEvent, ...]
    merged_span_hashes: tuple[str, ...]
    attribution: tuple[AbsorbedDuplicate, ...]


@dataclass(frozen=True)
class DedupResult:
    """Deterministic dedup outcome over one batch of candidate events."""

    groups: tuple[DedupGroup, ...]
    total_events: int
    unique_candidates: int
    absorbed_duplicates: int


def dedup_candidates(events: Iterable[LedgerEvent]) -> DedupResult:
    """Group ``candidate.proposed`` events by exact-content identity.

    Within each ``(tenant_id, identity_hash)`` group the survivor is the
    earliest event under ``event_ordering.ordering_key`` (source-timestamp
    total order — input order never decides). Every other event in the
    group is absorbed: counted, attributed, and carried on the group's
    merged provenance. Groups are returned sorted by the survivor's
    ordering key, so the same multiset of events always yields an
    identical ``DedupResult`` regardless of iteration order.

    Non-``candidate.proposed`` events fail closed — callers filter
    explicitly; nothing is dropped without a visible decision.
    """

    grouped: dict[tuple[str, str], list[tuple[LedgerEvent, CandidateIdentity]]] = {}
    total = 0
    for event in events:
        identity = CandidateIdentity.from_event(event)
        total += 1
        grouped.setdefault((event.tenant_id, identity.identity_hash), []).append(
            (event, identity)
        )

    groups: list[DedupGroup] = []
    absorbed_total = 0
    for members in grouped.values():
        members.sort(key=lambda pair: ordering_key(pair[0]))
        (survivor, identity) = members[0]
        absorbed_events = tuple(event for event, _ in members[1:])
        merged: set[str] = set(survivor.source_span_hashes)
        attribution: list[AbsorbedDuplicate] = []
        for duplicate in absorbed_events:
            merged.update(duplicate.source_span_hashes)
            attribution.append(
                AbsorbedDuplicate(
                    event_hash=duplicate.event_hash,
                    idempotency_key=duplicate.idempotency_key,
                    occurred_at=duplicate.occurred_at,
                    source_event_external_id=duplicate.source_event_external_id,
                    span_hashes=tuple(sorted(set(duplicate.source_span_hashes))),
                )
            )
        merged_span_hashes = tuple(sorted(merged))
        # Provenance-retention invariant (#319): the merged set must equal
        # the identity's span set — every duplicate's span hashes survive
        # the fold. Under identity v1 this is structural; asserting it here
        # keeps a future identity revision from silently dropping spans.
        if merged_span_hashes != identity.span_hashes:
            raise CandidateDedupError(
                "provenance retention violated: merged span hashes "
                f"{merged_span_hashes!r} do not match the identity span set "
                f"{identity.span_hashes!r}"
            )
        absorbed_total += len(absorbed_events)
        groups.append(
            DedupGroup(
                identity=identity,
                survivor=survivor,
                absorbed=absorbed_events,
                merged_span_hashes=merged_span_hashes,
                attribution=tuple(attribution),
            )
        )

    groups.sort(key=lambda group: ordering_key(group.survivor))
    return DedupResult(
        groups=tuple(groups),
        total_events=total,
        unique_candidates=len(groups),
        absorbed_duplicates=absorbed_total,
    )


@dataclass(frozen=True)
class SurvivorWriteMaterial:
    """Write-path provenance material for one dedup survivor (cortex#319).

    Consumed alongside ``graph_writes.plan_candidate_proposed``: the
    survivor event becomes the planned ledger append, the merged span
    hashes become the surviving decision version's ``source_span_hashes``,
    and ``attribution_metadata`` travels in the event/projection metadata
    so every absorbed duplicate stays counted and attributed. No
    ``decision_edges`` drafts are produced here — absorbed duplicates never
    became nodes, so node-level edge vocabulary (``duplicates`` for the
    cortex#487 merge, ``derived_from`` for node derivations) does not apply
    (see module docstring for the #318/#319 vs #487 boundary).
    """

    survivor: LedgerEvent
    source_span_hashes: tuple[str, ...]
    attribution: tuple[AbsorbedDuplicate, ...]
    identity_hash: str
    identity_version: int

    def attribution_metadata(self) -> dict[str, Any]:
        """JSON-ready metadata recording the fold on the surviving version."""

        return {
            "absorbed_duplicates": [entry.as_payload() for entry in self.attribution],
            "candidate_identity_hash": self.identity_hash,
            "candidate_identity_version": self.identity_version,
        }


def survivor_write_material(group: DedupGroup) -> SurvivorWriteMaterial:
    """Render one dedup group as write-path material for the survivor."""

    if not isinstance(group, DedupGroup):
        raise CandidateDedupError(
            f"survivor_write_material consumes DedupGroup; got {type(group).__name__}"
        )
    return SurvivorWriteMaterial(
        survivor=group.survivor,
        source_span_hashes=group.merged_span_hashes,
        attribution=group.attribution,
        identity_hash=group.identity.identity_hash,
        identity_version=group.identity.identity_version,
    )


def _normalized_span_hashes(span_hashes: Iterable[str]) -> tuple[str, ...]:
    if isinstance(span_hashes, (str, bytes)):
        raise CandidateDedupError("span_hashes must be a sequence of sha256 hex strings")
    unique: set[str] = set()
    for value in span_hashes:
        if not isinstance(value, str) or not _SHA256_RE.match(value):
            raise CandidateDedupError("span_hashes values must be sha256 hex strings")
        unique.add(value)
    if not unique:
        raise CandidateDedupError(
            "candidate identity requires at least one source span hash "
            "(uncited candidates are unrepresentable)"
        )
    return tuple(sorted(unique))


def _normalized_scopes(scopes: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    unique: set[tuple[str, str]] = set()
    for pair in scopes:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise CandidateDedupError(
                "scopes must be (scope_type, normalized_value) pairs; "
                f"got {pair!r}"
            )
        scope_type, normalized_value = pair
        try:
            canonical_type = ScopeType(scope_type).value
        except ValueError as exc:
            raise CandidateDedupError(
                f"scope_type must be one of "
                f"{sorted(member.value for member in ScopeType)}, got {scope_type!r}"
            ) from exc
        if not isinstance(normalized_value, str) or not normalized_value.strip():
            raise CandidateDedupError("scope normalized_value must be a non-empty string")
        unique.add((canonical_type, normalized_value))
    return tuple(sorted(unique))


def _hash_mapping(value: Mapping[str, Any]) -> str:
    # Same bytes semantics as ledger_events._hash_mapping (canonical JSON via
    # sort_keys + compact separators, utf-8, sha256), kept module-local like
    # graph_snapshot does; a test pins a known-input digest so the idioms
    # cannot drift apart silently.
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
