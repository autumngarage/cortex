"""Tests for derive-time lane assignment (cortex#358) and backfill enforcement (cortex#362).

The policy oracle is the shipped cortex#315 contract in ``cortex.hosted.lanes``;
no policy is redefined here. Adversarial sweeps of the auto-promotion boundary
live in ``tests/test_lane_promotion_boundary.py`` (cortex#360).
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import product
from pathlib import Path
from typing import cast

import pytest

import cortex.hosted.lane_assignment as lane_assignment_module
from cortex.hosted.confidence import (
    ADVISORY_ONLY_TIER_CAP,
    BLOCKING_ELIGIBLE_TIER,
    ConfidenceState,
    ConfidenceValidationError,
    tier_rank,
)
from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.derive_store import AppendOutcome, DeriveEventStore
from cortex.hosted.lane_assignment import (
    BACKFILL_ADVISORY_ONLY_RULE,
    CONFIDENCE_PAYLOAD_KEY,
    DROPPED_CHATTER_REASON_PREFIX,
    LANE_ASSIGNMENT_PAYLOAD_KEY,
    LaneAssignmentError,
    assign_lane,
    candidate_proposed_event,
    dropped_chatter_record,
    enforce_backfill_advisory_only,
    initial_confidence_state,
    validate_policy_auto_promotion,
)
from cortex.hosted.lanes import (
    DEFAULT_LANE_POLICY,
    LANE_POLICY_VERSION,
    DecisionStatus,
    DeriveSourceType,
    Lane,
    LaneAssignment,
    LanePolicy,
    LanePolicyValidationError,
    validate_auto_promotion,
    validate_entry_status,
    validate_status_transition,
)
from cortex.hosted.ledger_events import (
    EVENT_SCHEMA_VERSION,
    ActorRef,
    LedgerEvent,
    LedgerEventType,
)
from cortex.hosted.model_interfaces import DeriveCandidate
from cortex.hosted.provenance import SourceDocument, content_hash

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
ACTOR = ActorRef(actor_type="agent", actor_id="cortex-derive-test")
OCCURRED_AT = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)


def _document(content: str = "Decision: use uv for dependency management.\n") -> SourceDocument:
    return SourceDocument(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        document_type="repo-file",
        external_id="docs/adr/0001-use-uv.md",
        permalink="docs/adr/0001-use-uv.md",
        author_ref="henry",
        source_timestamp=OCCURRED_AT,
        content=content,
    )


def _candidate(content: str = "Decision: use uv for dependency management.\n") -> DeriveCandidate:
    document = _document(content)
    span = document.span(start_offset=0, end_offset=len(document.content))
    return DeriveCandidate(decision_text="Use uv for dependency management.", spans=(span,))


def _event(
    assignment: LaneAssignment,
    candidate: DeriveCandidate,
    *,
    external_id: str = "docs/adr/0001-use-uv.md#0",
) -> LedgerEvent:
    return candidate_proposed_event(
        assignment,
        candidate,
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        actor=ACTOR,
        occurred_at=OCCURRED_AT,
        source_event_external_id=external_id,
    )


class _BypassedAssignment(LaneAssignment):
    """Test double that skips lanes.py validation, modeling a weakened policy."""

    def __post_init__(self) -> None:
        # Deliberately bypass LaneAssignment's invariants.
        return


class _WeakenedPolicy(LanePolicy):
    """Adversarial policy returning auto-promotable backfilled assignments."""

    def assign(self, source_type: DeriveSourceType, *, backfilled: bool) -> LaneAssignment:
        honest = LanePolicy.assign(self, source_type, backfilled=backfilled)
        return _BypassedAssignment(
            policy_version=honest.policy_version,
            source_type=honest.source_type,
            lane=Lane.STRUCTURED,
            backfilled=backfilled,
            advisory_only=False,
            auto_promotable=True,
            enters_graph=True,
            rule_citation=honest.rule_citation,
        )


class _LaunderingPolicy(LanePolicy):
    """Adversarial policy that silently drops the backfilled flag."""

    def assign(self, source_type: DeriveSourceType, *, backfilled: bool) -> LaneAssignment:
        _ = backfilled
        return LanePolicy.assign(self, source_type, backfilled=False)


# --- cortex#358: lane assignment via the shipped policy -----------------------


def test_assign_lane_covers_each_lane_with_policy_rule_citation() -> None:
    cases = (
        (DeriveSourceType.ADR, Lane.STRUCTURED),
        (DeriveSourceType.COMMIT_MESSAGE, Lane.PROVISIONAL),
        (DeriveSourceType.UNATTRIBUTED_CHATTER, Lane.DROPPED),
    )
    for source_type, expected_lane in cases:
        assignment = assign_lane(source_type, backfilled=False)
        assert assignment.lane is expected_lane
        assert assignment.rule_citation == (
            f"cortex.hosted.lanes/v{LANE_POLICY_VERSION}/"
            f"{source_type.value}/{expected_lane.value}"
        )


def test_every_input_gets_exactly_one_lane() -> None:
    for source_type, backfilled in product(DeriveSourceType, (False, True)):
        assignment = assign_lane(source_type, backfilled=backfilled)
        assert assignment.lane in tuple(Lane)
        assert assignment.enters_graph == (assignment.lane is not Lane.DROPPED)
        assert assignment.policy_version == DEFAULT_LANE_POLICY.policy_version


def test_lane_assignment_is_replayable() -> None:
    corpus = tuple(product(DeriveSourceType, (False, True)))
    first = [assign_lane(st, backfilled=bf) for st, bf in corpus]
    second = [assign_lane(st, backfilled=bf) for st, bf in corpus]
    assert first == second

    candidate = _candidate()
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=False)
    event_one = _event(assignment, candidate)
    event_two = _event(assignment, candidate)
    assert event_one.event_hash == event_two.event_hash
    assert event_one.idempotency_key == event_two.idempotency_key


def test_assign_lane_supports_injected_policies() -> None:
    custom = LanePolicy(
        policy_version=LANE_POLICY_VERSION,
        rules=dict(DEFAULT_LANE_POLICY.rules),
    )
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=False, policy=custom)
    assert assignment == assign_lane(DeriveSourceType.ADR, backfilled=False)


def test_assign_lane_fails_closed_on_wrong_types() -> None:
    with pytest.raises(LaneAssignmentError, match="DeriveSourceType"):
        assign_lane(cast(DeriveSourceType, "adr"), backfilled=False)
    with pytest.raises(LaneAssignmentError, match="bool"):
        assign_lane(DeriveSourceType.ADR, backfilled=cast(bool, 1))
    with pytest.raises(LaneAssignmentError, match="LanePolicy"):
        assign_lane(DeriveSourceType.ADR, backfilled=False, policy=cast(LanePolicy, object()))


# --- cortex#362: backfill is advisory-only, never auto-promotable -------------


def test_backfilled_material_is_advisory_only_for_every_source_type() -> None:
    for source_type in DeriveSourceType:
        assignment = assign_lane(source_type, backfilled=True)
        assert assignment.advisory_only is True
        assert assignment.auto_promotable is False


def test_backfill_advisory_only_rule_is_exported_for_evaluator_ladder() -> None:
    # cortex#375 consumes this named constant instead of re-deriving the rule.
    assert BACKFILL_ADVISORY_ONLY_RULE == (
        "backfilled nodes default advisory-only and are never auto-promotable"
    )
    module_doc = " ".join((lane_assignment_module.__doc__ or "").split())
    assert BACKFILL_ADVISORY_ONLY_RULE in module_doc
    assert "cortex#375" in module_doc
    enforce_doc = " ".join((enforce_backfill_advisory_only.__doc__ or "").split())
    assert BACKFILL_ADVISORY_ONLY_RULE in enforce_doc


def test_backfilled_confidence_state_is_capped_at_advisory_only_tier() -> None:
    for source_type in (DeriveSourceType.ADR, DeriveSourceType.COMMIT_MESSAGE):
        assignment = assign_lane(source_type, backfilled=True)
        state = initial_confidence_state(assignment, citation_count=1)
        assert state.advisory_only is True
        assert tier_rank(state.tier) <= tier_rank(ADVISORY_ONLY_TIER_CAP)
        assert assignment.rule_citation in state.last_transition_reason


def test_backfilled_node_cannot_be_created_confirmed() -> None:
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=True)
    event = _event(assignment, _candidate())
    # The only entry status the policy table permits is the shipped enum's
    # 'candidate'; entering as 'confirmed' is unrepresentable.
    assert event.payload["entry_status"] == DecisionStatus.CANDIDATE.value
    with pytest.raises(LanePolicyValidationError, match="cannot enter the graph"):
        validate_entry_status(assignment.lane, DecisionStatus.CONFIRMED)
    # The advisory-only marking also makes the blocking-eligible confidence
    # tier unreachable at creation.
    with pytest.raises(ConfidenceValidationError, match="advisory-only"):
        ConfidenceState(
            tier=BLOCKING_ELIGIBLE_TIER,
            confirmation_count=2,
            citation_count=1,
            override_count_in_window=0,
            last_transition_reason="adversarial probe",
            advisory_only=True,
        )


def test_backfilled_auto_promotion_refused_with_visible_reason() -> None:
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=True)
    with pytest.raises(LanePolicyValidationError, match="never auto-promotable"):
        validate_auto_promotion(assignment)
    with pytest.raises(LanePolicyValidationError, match="never auto-promotable"):
        validate_policy_auto_promotion(assignment)


def test_backfilled_promotion_only_via_human_confirm_event() -> None:
    # The only promotion path for a backfilled node: a human confirm event,
    # recorded by the ledger event the lanes contract names for the
    # candidate -> confirmed transition.
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=True)
    with pytest.raises(LanePolicyValidationError):
        validate_policy_auto_promotion(assignment)
    event_type = validate_status_transition(
        assignment.lane, DecisionStatus.CANDIDATE, DecisionStatus.CONFIRMED
    )
    assert event_type is LedgerEventType.DECISION_CONFIRMED


def test_structured_source_auto_promotion_succeeds_and_cites_rule() -> None:
    # Fixture for the permitted path: a non-backfilled structured-source
    # candidate, citing the cortex.hosted.lanes/v1/adr/structured rule.
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=False)
    assert assignment.rule_citation == (
        f"cortex.hosted.lanes/v{LANE_POLICY_VERSION}/adr/structured"
    )
    assert validate_policy_auto_promotion(assignment) is LedgerEventType.DECISION_CONFIRMED


def test_enforce_backfill_rule_catches_validation_bypass() -> None:
    weakened = _BypassedAssignment(
        policy_version=LANE_POLICY_VERSION,
        source_type=DeriveSourceType.ADR,
        lane=Lane.STRUCTURED,
        backfilled=True,
        advisory_only=False,
        auto_promotable=True,
        enters_graph=True,
        rule_citation="forged",
    )
    with pytest.raises(LaneAssignmentError, match="never auto-promotable"):
        enforce_backfill_advisory_only(weakened)


def test_assign_lane_revalidates_through_lanes_contract() -> None:
    # A policy whose subclass bypasses LaneAssignment.__post_init__ cannot
    # smuggle a weakened assignment through: assign_lane re-validates via
    # LaneAssignment.from_payload, which runs the full lanes.py invariants.
    policy = _WeakenedPolicy(
        policy_version=LANE_POLICY_VERSION,
        rules=dict(DEFAULT_LANE_POLICY.rules),
    )
    with pytest.raises(LaneAssignmentError, match="violates the lane policy contract"):
        assign_lane(DeriveSourceType.ADR, backfilled=True, policy=policy)


def test_assign_lane_refuses_backfill_laundering() -> None:
    policy = _LaunderingPolicy(
        policy_version=LANE_POLICY_VERSION,
        rules=dict(DEFAULT_LANE_POLICY.rules),
    )
    with pytest.raises(LaneAssignmentError, match="does not echo the request"):
        assign_lane(DeriveSourceType.ADR, backfilled=True, policy=policy)


# --- cortex#361 (absorbed): dropped chatter is logged, never graph state ------


def test_dropped_chatter_record_carries_machine_readable_reason_code() -> None:
    assignment = assign_lane(DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False)
    excerpt = "standup chatter: maybe rewrite the worker in rust someday?"
    record = dropped_chatter_record(assignment, excerpt=excerpt)
    assert record.reason_code == (
        f"{DROPPED_CHATTER_REASON_PREFIX}:{assignment.rule_citation}"
    )
    assert record.excerpt_hash == content_hash(excerpt)


def test_dropped_assignment_cannot_produce_a_persistable_event() -> None:
    # The cortex#361 invariant: a 'dropped' assignment cannot construct a
    # persistable ledger event, so it can never become graph state.
    assignment = assign_lane(DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False)
    with pytest.raises(LaneAssignmentError, match="never becomes graph state"):
        _event(assignment, _candidate())
    with pytest.raises(LaneAssignmentError, match="never becomes graph state"):
        initial_confidence_state(assignment, citation_count=1)


def test_dropped_chatter_record_refuses_graph_eligible_material() -> None:
    for source_type in (DeriveSourceType.ADR, DeriveSourceType.COMMIT_MESSAGE):
        assignment = assign_lane(source_type, backfilled=False)
        with pytest.raises(LaneAssignmentError, match="silently drop"):
            dropped_chatter_record(assignment, excerpt="not chatter")


def test_dropped_chatter_record_refuses_empty_excerpt() -> None:
    assignment = assign_lane(DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False)
    with pytest.raises(LaneAssignmentError, match="unauditable"):
        dropped_chatter_record(assignment, excerpt="   ")


def test_no_silent_drops_every_input_lands_in_lane_or_drop_log(tmp_path: Path) -> None:
    corpus: tuple[tuple[DeriveSourceType, str], ...] = (
        (DeriveSourceType.ADR, "Decision: adopt event sourcing for the ledger.\n"),
        (DeriveSourceType.AGENT_INSTRUCTIONS, "Decision: validate before persisting.\n"),
        (DeriveSourceType.COMMIT_MESSAGE, "fix: stop blending policy regimes\n"),
        (DeriveSourceType.UNATTRIBUTED_CHATTER, "random slack chatter about lunch\n"),
        (DeriveSourceType.UNATTRIBUTED_CHATTER, "more chatter, structured-sounding\n"),
    )
    events = []
    dropped = []
    for index, (source_type, text) in enumerate(corpus):
        assignment = assign_lane(source_type, backfilled=False)
        if assignment.enters_graph:
            events.append(_event(assignment, _candidate(text), external_id=f"corpus-{index}"))
        else:
            dropped.append(dropped_chatter_record(assignment, excerpt=text))
    # Reconciliation: every input either landed in a graph lane or in the
    # dropped-chatter log — nothing vanished.
    assert len(events) + len(dropped) == len(corpus)
    assert len(dropped) == 2
    with DeriveEventStore(tmp_path / "derive-events.sqlite") as store:
        outcome = store.append_events(events)
        assert outcome == AppendOutcome(inserted=len(events), ignored=0)
        assert len(store.event_hashes()) == len(events)


def test_dropped_item_resubmission_never_creates_graph_state(tmp_path: Path) -> None:
    assignment = assign_lane(DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False)
    db_path = tmp_path / "derive-events.sqlite"
    for _ in range(2):
        with DeriveEventStore(db_path) as store:
            with pytest.raises(LaneAssignmentError):
                _event(assignment, _candidate("the same chatter item, resubmitted\n"))
            record = dropped_chatter_record(
                assignment, excerpt="the same chatter item, resubmitted\n"
            )
            assert record.reason_code.endswith("/unattributed_chatter/dropped")
            # No event exists, so nothing can be appended: the replay-export
            # store (the only persistence surface; decision_nodes /
            # decision_versions / decision_edges all derive from these
            # events) stays empty on every attempt.
            assert store.append_events([]) == AppendOutcome(inserted=0, ignored=0)
            assert store.event_hashes() == frozenset()
            assert store.export_events() == ()


# --- event stamping ------------------------------------------------------------


def test_candidate_event_stamps_lane_and_confidence_payloads() -> None:
    assignment = assign_lane(DeriveSourceType.AGENT_INSTRUCTIONS, backfilled=False)
    candidate = _candidate()
    event = _event(assignment, candidate, external_id="CLAUDE.md#0")
    assert event.event_type is LedgerEventType.CANDIDATE_PROPOSED
    assert event.event_version == EVENT_SCHEMA_VERSION
    assert event.source_span_hashes == candidate.span_hashes
    stamped = event.payload[LANE_ASSIGNMENT_PAYLOAD_KEY]
    assert stamped["lane"] == Lane.STRUCTURED.value
    assert stamped["auto_promotable"] is True
    assert LaneAssignment.from_payload(stamped) == assignment
    confidence = ConfidenceState.from_payload(event.payload[CONFIDENCE_PAYLOAD_KEY])
    assert confidence.advisory_only is False
    assert confidence.citation_count == len(candidate.span_hashes)
    assert event.payload["entry_status"] == DecisionStatus.CANDIDATE.value


def test_provisional_candidate_enters_as_advisory_candidate() -> None:
    assignment = assign_lane(DeriveSourceType.COMMIT_MESSAGE, backfilled=False)
    event = _event(assignment, _candidate(), external_id="commit-abc123")
    stamped = event.payload[LANE_ASSIGNMENT_PAYLOAD_KEY]
    assert stamped["lane"] == Lane.PROVISIONAL.value
    assert stamped["auto_promotable"] is False
    assert stamped["advisory_only"] is True
    confidence = ConfidenceState.from_payload(event.payload[CONFIDENCE_PAYLOAD_KEY])
    assert confidence.advisory_only is True
    assert event.payload["entry_status"] == DecisionStatus.CANDIDATE.value


def test_candidate_event_rejects_non_candidate_material() -> None:
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=False)
    with pytest.raises(LaneAssignmentError, match="DeriveCandidate"):
        candidate_proposed_event(
            assignment,
            cast(DeriveCandidate, "not a candidate"),
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
            actor=ACTOR,
            occurred_at=OCCURRED_AT,
            source_event_external_id="x",
        )


# --- degradation taxonomy --------------------------------------------------------


def test_lane_assignment_error_is_classified_in_degradation_taxonomy() -> None:
    assert (
        classify_failure(LaneAssignmentError("boundary probe"))
        is DegradationMode.INVALID_INPUT_REJECTED
    )
