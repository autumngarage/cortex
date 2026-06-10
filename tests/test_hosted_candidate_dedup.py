"""Tests for exact-hash candidate dedup + provenance retention (cortex#318, #319)."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cortex.hosted.candidate_dedup import (
    CANDIDATE_IDENTITY_VERSION,
    CandidateDedupError,
    CandidateIdentity,
    candidate_identity_hash,
    candidate_identity_material,
    dedup_candidates,
    normalize_decision_text,
    survivor_write_material,
)
from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType

TENANT = "00000000-0000-4000-8000-000000000001"
OTHER_TENANT = "00000000-0000-4000-8000-000000000002"
SOURCE = "00000000-0000-4000-8000-000000000003"
SPAN_A = "a" * 64
SPAN_B = "b" * 64
SPAN_C = "c" * 64

T0 = datetime(2026, 6, 10, 9, 0, tzinfo=UTC)

# Byte-level identity contract for CANDIDATE_IDENTITY_VERSION = 1: sha256
# over the canonical JSON of {identity_version, normalized text "x", scopes
# [["path", "src/a.py"]], span_hashes ["a"*64]}. If this stops matching, the
# identity serialization changed and every persisted identity is orphaned —
# that requires a version bump, not a constant update.
PINNED_IDENTITY_HASH = "2b883bbba2968d5669855502d819e120d7e4f34a6b1ed302326c6ecb629230b9"

_ACTOR = ActorRef(actor_type="derive", actor_id="repo-native/test@v1")


def _candidate_event(
    *,
    text: str = "Never commit on the default branch",
    spans: tuple[str, ...] = (SPAN_A,),
    scopes: tuple[dict[str, str], ...] = (),
    occurred_at: datetime = T0,
    key: str = "candidate:1",
    tenant_id: str = TENANT,
) -> LedgerEvent:
    payload: dict[str, Any] = {"decision_text": text, "source_type": "agent_instructions"}
    if scopes:
        payload["proposed_scopes"] = list(scopes)
    return LedgerEvent(
        tenant_id=tenant_id,
        source_id=SOURCE,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=_ACTOR,
        occurred_at=occurred_at,
        idempotency_key=key,
        payload=payload,
        source_span_hashes=spans,
    )


# --- normalization (the documented two-step contract) -----------------------


def test_normalize_collapses_whitespace_then_casefolds() -> None:
    assert (
        normalize_decision_text("  Use\tSQLite\n  for   the CACHE  ")
        == "use sqlite for the cache"
    )


def test_normalize_casefold_is_unicode_aware() -> None:
    # casefold (not lower): the German sharp s folds to "ss".
    assert normalize_decision_text("STRASSE") == normalize_decision_text("straße")


def test_normalize_rejects_empty_and_whitespace_only() -> None:
    with pytest.raises(CandidateDedupError, match="must not be empty"):
        normalize_decision_text("   \n\t ")


# --- identity hash basis -----------------------------------------------------


def test_identity_hash_pinned_byte_contract() -> None:
    computed = candidate_identity_hash(
        decision_text="x", span_hashes=(SPAN_A,), scopes=[("path", "src/a.py")]
    )
    assert computed == PINNED_IDENTITY_HASH
    material = candidate_identity_material(
        decision_text="x", span_hashes=(SPAN_A,), scopes=[("path", "src/a.py")]
    )
    assert material["identity_version"] == CANDIDATE_IDENTITY_VERSION == 1
    assert material["normalized_decision_text"] == "x"


def test_whitespace_and_case_variants_collide() -> None:
    base = candidate_identity_hash(decision_text="Use SQLite for cache", span_hashes=(SPAN_A,))
    assert (
        candidate_identity_hash(decision_text="  use  SQLITE\nfor cache ", span_hashes=(SPAN_A,))
        == base
    )


def test_different_text_does_not_collide() -> None:
    base = candidate_identity_hash(decision_text="Use SQLite for cache", span_hashes=(SPAN_A,))
    other = candidate_identity_hash(decision_text="Use Postgres for cache", span_hashes=(SPAN_A,))
    assert other != base


def test_span_order_and_repetition_do_not_change_identity() -> None:
    base = candidate_identity_hash(decision_text="x", span_hashes=(SPAN_A, SPAN_B))
    assert candidate_identity_hash(decision_text="x", span_hashes=(SPAN_B, SPAN_A)) == base
    assert (
        candidate_identity_hash(decision_text="x", span_hashes=(SPAN_A, SPAN_B, SPAN_A)) == base
    )


def test_span_set_participates_in_identity() -> None:
    base = candidate_identity_hash(decision_text="x", span_hashes=(SPAN_A,))
    widened = candidate_identity_hash(decision_text="x", span_hashes=(SPAN_A, SPAN_B))
    assert widened != base


def test_scope_order_and_repetition_do_not_change_identity() -> None:
    pair_a = ("path", "src/a.py")
    pair_b = ("owner", "autumngarage")
    base = candidate_identity_hash(
        decision_text="x", span_hashes=(SPAN_A,), scopes=[pair_a, pair_b]
    )
    assert (
        candidate_identity_hash(
            decision_text="x", span_hashes=(SPAN_A,), scopes=[pair_b, pair_a, pair_a]
        )
        == base
    )


def test_identity_requires_at_least_one_span_hash() -> None:
    with pytest.raises(CandidateDedupError, match="at least one source span hash"):
        candidate_identity_hash(decision_text="x", span_hashes=())


def test_identity_rejects_malformed_span_hash() -> None:
    with pytest.raises(CandidateDedupError, match="sha256 hex"):
        candidate_identity_hash(decision_text="x", span_hashes=("not-a-hash",))


def test_identity_rejects_unknown_scope_type() -> None:
    with pytest.raises(CandidateDedupError, match="scope_type must be one of"):
        candidate_identity_hash(
            decision_text="x", span_hashes=(SPAN_A,), scopes=[("galaxy", "milky-way")]
        )


def test_identity_from_event_matches_direct_hash() -> None:
    scope = {"scope_type": "path", "value": "src/a.py", "normalized_value": "src/a.py"}
    event = _candidate_event(text="x", spans=(SPAN_A,), scopes=(scope,))
    identity = CandidateIdentity.from_event(event)
    assert identity.identity_hash == PINNED_IDENTITY_HASH
    assert identity.span_hashes == (SPAN_A,)
    assert identity.scopes == (("path", "src/a.py"),)


# --- dedup fold ---------------------------------------------------------------


def test_dedup_keeps_earliest_by_ordering_key() -> None:
    later = _candidate_event(occurred_at=T0 + timedelta(hours=2), key="candidate:later")
    earlier = _candidate_event(occurred_at=T0, key="candidate:earlier")
    # Input order is the wrong order on purpose; the total order decides.
    result = dedup_candidates([later, earlier])
    (group,) = result.groups
    assert group.survivor is earlier
    assert group.absorbed == (later,)
    assert result.total_events == 2
    assert result.unique_candidates == 1
    assert result.absorbed_duplicates == 1


def test_dedup_separates_distinct_identities() -> None:
    one = _candidate_event(key="candidate:one")
    other = _candidate_event(
        text="Always run validate before pushing",
        spans=(SPAN_B,),
        occurred_at=T0 + timedelta(minutes=1),
        key="candidate:two",
    )
    result = dedup_candidates([one, other])
    assert result.unique_candidates == 2
    assert result.absorbed_duplicates == 0
    assert [group.survivor for group in result.groups] == [one, other]


def test_dedup_provenance_retention_arithmetic() -> None:
    """#319: nothing dropped — every span hash and event id survives the fold."""

    spans = (SPAN_B, SPAN_A)
    events = [
        _candidate_event(spans=spans, occurred_at=T0, key="candidate:first"),
        _candidate_event(spans=spans, occurred_at=T0 + timedelta(hours=1), key="candidate:second"),
        _candidate_event(spans=spans, occurred_at=T0 + timedelta(hours=2), key="candidate:third"),
    ]
    result = dedup_candidates(events)
    (group,) = result.groups
    union_of_inputs: set[str] = set()
    for event in events:
        union_of_inputs.update(event.source_span_hashes)
    assert set(group.merged_span_hashes) == union_of_inputs
    assert group.merged_span_hashes == tuple(sorted(union_of_inputs))
    # Every absorbed duplicate is attributed by event hash + idempotency key.
    assert len(group.attribution) == len(group.absorbed) == 2
    attributed_keys = {entry.idempotency_key for entry in group.attribution}
    assert attributed_keys == {"candidate:second", "candidate:third"}
    attributed_hashes = {entry.event_hash for entry in group.attribution}
    assert attributed_hashes == {event.event_hash for event in events[1:]}
    for entry in group.attribution:
        assert set(entry.span_hashes) <= set(group.merged_span_hashes)
    assert result.total_events == result.unique_candidates + result.absorbed_duplicates


def test_dedup_is_tenant_scoped() -> None:
    mine = _candidate_event(key="candidate:mine")
    theirs = _candidate_event(tenant_id=OTHER_TENANT, key="candidate:theirs")
    result = dedup_candidates([mine, theirs])
    assert result.unique_candidates == 2
    assert result.absorbed_duplicates == 0


def test_dedup_is_deterministic_under_shuffle() -> None:
    events = [
        _candidate_event(occurred_at=T0, key="candidate:a1"),
        _candidate_event(occurred_at=T0 + timedelta(minutes=1), key="candidate:a2"),
        _candidate_event(
            text="Always run validate before pushing",
            spans=(SPAN_B,),
            occurred_at=T0 + timedelta(minutes=2),
            key="candidate:b1",
        ),
        _candidate_event(
            text="always RUN validate   before pushing",
            spans=(SPAN_B,),
            occurred_at=T0 + timedelta(minutes=3),
            key="candidate:b2",
        ),
    ]
    baseline = dedup_candidates(events)
    rng = random.Random(0x318)
    for _ in range(10):
        shuffled = list(events)
        rng.shuffle(shuffled)
        assert dedup_candidates(shuffled) == baseline


def test_dedup_rejects_non_candidate_events() -> None:
    confirm = LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=LedgerEventType.DECISION_CONFIRMED,
        actor=_ACTOR,
        occurred_at=T0,
        idempotency_key="confirm:1",
        payload={},
        source_span_hashes=(SPAN_A,),
    )
    with pytest.raises(CandidateDedupError, match=r"candidate\.proposed"):
        dedup_candidates([confirm])


def test_dedup_rejects_missing_decision_text() -> None:
    event = LedgerEvent(
        tenant_id=TENANT,
        source_id=SOURCE,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=_ACTOR,
        occurred_at=T0,
        idempotency_key="candidate:textless",
        payload={"source_type": "agent_instructions"},
        source_span_hashes=(SPAN_A,),
    )
    with pytest.raises(CandidateDedupError, match="decision_text"):
        dedup_candidates([event])


def test_dedup_empty_input_is_empty_result() -> None:
    result = dedup_candidates([])
    assert result.groups == ()
    assert result.total_events == 0
    assert result.unique_candidates == 0
    assert result.absorbed_duplicates == 0


# --- survivor write material (#319) -------------------------------------------


def test_survivor_write_material_shape() -> None:
    events = [
        _candidate_event(occurred_at=T0, key="candidate:first"),
        _candidate_event(occurred_at=T0 + timedelta(hours=1), key="candidate:second"),
    ]
    (group,) = dedup_candidates(events).groups
    material = survivor_write_material(group)
    assert material.survivor is events[0]
    assert material.source_span_hashes == group.merged_span_hashes
    # Under identity v1 the merged set equals the identity span set.
    assert material.source_span_hashes == group.identity.span_hashes
    metadata = material.attribution_metadata()
    # No edge drafts of any type: absorbed duplicates never became nodes, so
    # provenance lives on the surviving version + this attribution list (the
    # #318/#319 boundary with the #487 node-level `duplicates` edge).
    assert set(metadata) == {
        "absorbed_duplicates",
        "candidate_identity_hash",
        "candidate_identity_version",
    }
    assert metadata["candidate_identity_hash"] == group.identity.identity_hash
    assert metadata["candidate_identity_version"] == CANDIDATE_IDENTITY_VERSION
    (entry,) = metadata["absorbed_duplicates"]
    assert entry["idempotency_key"] == "candidate:second"
    assert entry["event_hash"] == events[1].event_hash
    assert entry["span_hashes"] == sorted(set(events[1].source_span_hashes))
    # JSON-ready: this travels in event/projection metadata.
    json.dumps(metadata)


def test_survivor_write_material_rejects_non_group() -> None:
    with pytest.raises(CandidateDedupError, match="DedupGroup"):
        survivor_write_material("not-a-group")  # type: ignore[arg-type]


def test_dedup_error_classifies_as_invalid_input_rejected() -> None:
    assert classify_failure(CandidateDedupError("probe")) is (
        DegradationMode.INVALID_INPUT_REJECTED
    )
