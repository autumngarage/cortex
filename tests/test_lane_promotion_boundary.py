"""Adversarial tests of the structured-source auto-promotion boundary (cortex#360).

The test oracle is exactly the cortex#315 promotion-lane policy contract as
shipped in ``cortex.hosted.lanes``, implemented by derive-time lane
assignment (cortex#358) and the backfill advisory-only enforcement
(cortex#362). No policy is invented in test code: ``POLICY_TABLE`` pins the
ratified contract and a guard test asserts the pin matches the shipped
``DEFAULT_LANE_POLICY`` verbatim. Statuses are asserted against the shipped
``decision_nodes.status`` enum values only (``DecisionStatus`` mirrors the
``schema.py`` CHECK; the drift test lives in ``tests/test_hosted_lanes.py``).

Note on custom-policy weakening: ``lanes.py`` validates policies at
construction (``SourceTypeRule.__post_init__`` rejects auto-promote outside
the structured lane; ``LaneAssignment.__post_init__`` rejects auto-promotable
backfilled state), so a weakened policy is rejected before assignment time;
the ``__post_init__``-bypass route is additionally caught by ``assign_lane``'s
re-validation. There is no gap to document.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import product
from pathlib import Path

import pytest

from cortex.hosted.confidence import ConfidenceState
from cortex.hosted.derive_store import AppendOutcome, DeriveEventStore
from cortex.hosted.lane_assignment import (
    CONFIDENCE_PAYLOAD_KEY,
    LaneAssignmentError,
    assign_lane,
    candidate_proposed_event,
    dropped_chatter_record,
    validate_policy_auto_promotion,
)
from cortex.hosted.lanes import (
    AUTO_PROMOTION_FROM_STATUS,
    AUTO_PROMOTION_TO_STATUS,
    DEFAULT_LANE_POLICY,
    LANE_POLICY_VERSION,
    LANE_STATUS_TRANSITIONS,
    DecisionStatus,
    DeriveSourceType,
    Lane,
    LaneAssignment,
    LanePolicy,
    LanePolicyValidationError,
    SourceTypeRule,
    validate_entry_status,
    validate_status_transition,
)
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType
from cortex.hosted.model_interfaces import DeriveCandidate
from cortex.hosted.provenance import SourceDocument

TENANT_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_ID = "44444444-4444-4444-8444-444444444444"
ACTOR = ActorRef(actor_type="agent", actor_id="cortex-boundary-test")
OCCURRED_AT = datetime(2026, 6, 9, 13, 0, 0, tzinfo=UTC)

# The cortex#315 policy table as ratified: source type -> (lane, auto_promote).
# Pinned here so the sweep cannot become a tautology over the shipped table;
# test_pinned_policy_table_matches_shipped_contract ties the pin to lanes.py.
POLICY_TABLE: dict[DeriveSourceType, tuple[Lane, bool]] = {
    DeriveSourceType.AGENT_INSTRUCTIONS: (Lane.STRUCTURED, True),
    DeriveSourceType.ADR: (Lane.STRUCTURED, True),
    DeriveSourceType.CODEOWNERS: (Lane.STRUCTURED, True),
    DeriveSourceType.COMMIT_MESSAGE: (Lane.PROVISIONAL, False),
    DeriveSourceType.PR_DESCRIPTION: (Lane.PROVISIONAL, False),
    DeriveSourceType.PR_REVIEW_COMMENT: (Lane.PROVISIONAL, False),
    DeriveSourceType.UNATTRIBUTED_CHATTER: (Lane.DROPPED, False),
}

# The full allowed set at the auto-promotion boundary, as
# (source_type, lane, backfilled, auto_promotable claim) tuples.
ALLOWED_AUTO_PROMOTIONS = frozenset(
    (source_type, Lane.STRUCTURED, False, True)
    for source_type, (lane, auto_promote) in POLICY_TABLE.items()
    if lane is Lane.STRUCTURED and auto_promote
)


def _document(content: str, *, external_id: str = "docs/adr/0007-event-sourcing.md") -> SourceDocument:
    return SourceDocument(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        document_type="repo-file",
        external_id=external_id,
        permalink=external_id,
        author_ref="henry",
        source_timestamp=OCCURRED_AT,
        content=content,
    )


def _candidate(content: str = "Decision: adopt event sourcing for the ledger.\n") -> DeriveCandidate:
    document = _document(content)
    span = document.span(start_offset=0, end_offset=len(document.content))
    return DeriveCandidate(decision_text="Adopt event sourcing for the ledger.", spans=(span,))


def _event(assignment: LaneAssignment, candidate: DeriveCandidate) -> LedgerEvent:
    return candidate_proposed_event(
        assignment,
        candidate,
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        actor=ACTOR,
        occurred_at=OCCURRED_AT,
        source_event_external_id="boundary-case-0",
    )


def _claimed_assignment(
    source_type: DeriveSourceType,
    lane: Lane,
    backfilled: bool,
    auto_promotable: bool,
) -> LaneAssignment:
    """Construct a claim with internally consistent derived fields.

    ``advisory_only`` and ``enters_graph`` are set to the values the lanes
    contract derives, so the sweep isolates the auto-promotion claim itself.
    """

    return LaneAssignment(
        policy_version=LANE_POLICY_VERSION,
        source_type=source_type,
        lane=lane,
        backfilled=backfilled,
        advisory_only=backfilled or lane is not Lane.STRUCTURED,
        auto_promotable=auto_promotable,
        enters_graph=lane is not Lane.DROPPED,
        rule_citation=(
            f"cortex.hosted.lanes/v{LANE_POLICY_VERSION}/{source_type.value}/{lane.value}"
        ),
    )


def test_pinned_policy_table_matches_shipped_contract() -> None:
    assert set(POLICY_TABLE) == set(DeriveSourceType)
    for source_type, (lane, auto_promote) in POLICY_TABLE.items():
        rule = DEFAULT_LANE_POLICY.rule_for(source_type)
        assert rule.lane is lane
        assert rule.auto_promote is auto_promote
    assert DEFAULT_LANE_POLICY.policy_version == LANE_POLICY_VERSION


def test_full_product_sweep_only_the_policy_allowed_set_auto_promotes() -> None:
    """Sweep source_type x lane x backfilled x auto_promotable claim (84 combos).

    Exactly ``ALLOWED_AUTO_PROMOTIONS`` passes the boundary; every other
    combination either cannot be constructed (lanes.py unrepresentability)
    or is refused by the policy revalidation / auto-promotion gate.
    """

    passed: set[tuple[DeriveSourceType, Lane, bool, bool]] = set()
    swept = 0
    for source_type, lane, backfilled, claims_auto in product(
        DeriveSourceType, Lane, (False, True), (False, True)
    ):
        swept += 1
        combo = (source_type, lane, backfilled, claims_auto)
        constructible = not claims_auto or (lane is Lane.STRUCTURED and not backfilled)
        if not constructible:
            # The claim is unrepresentable: lanes.py refuses construction.
            with pytest.raises(LanePolicyValidationError):
                _claimed_assignment(*combo)
            continue
        assignment = _claimed_assignment(*combo)
        try:
            event = validate_policy_auto_promotion(assignment)
        except (LaneAssignmentError, LanePolicyValidationError):
            continue
        assert event is LedgerEventType.DECISION_CONFIRMED
        passed.add(combo)
    assert swept == len(DeriveSourceType) * len(Lane) * 2 * 2
    assert passed == ALLOWED_AUTO_PROMOTIONS


def test_every_auto_promotion_rule_has_pass_and_adversarial_fail_cases() -> None:
    for source_type, (lane, auto_promote) in POLICY_TABLE.items():
        if not auto_promote:
            continue
        # Passing input for rule cortex.hosted.lanes/v1/<source>/structured.
        passing = assign_lane(source_type, backfilled=False)
        assert passing.lane is lane
        assert passing.rule_citation == (
            f"cortex.hosted.lanes/v{LANE_POLICY_VERSION}/{source_type.value}/structured"
        )
        assert validate_policy_auto_promotion(passing) is LedgerEventType.DECISION_CONFIRMED
        # Adversarial input for the same rule: identical source class,
        # backfilled — the cortex#362 non-negotiable refuses it.
        adversarial = assign_lane(source_type, backfilled=True)
        with pytest.raises(LanePolicyValidationError, match="never auto-promotable"):
            validate_policy_auto_promotion(adversarial)
    for source_type, (lane, auto_promote) in POLICY_TABLE.items():
        if auto_promote:
            continue
        # Non-auto-promoting rules have no passing input at this boundary.
        assignment = assign_lane(source_type, backfilled=False)
        assert assignment.lane is lane
        with pytest.raises(LanePolicyValidationError):
            validate_policy_auto_promotion(assignment)


def test_chatter_dressed_as_structured_source_never_auto_promotes() -> None:
    # Rule exercised: cortex.hosted.lanes/v1/unattributed_chatter/dropped.
    # Source class decides the lane; content shaped like an accepted ADR
    # does not move chatter out of the dropped lane.
    structured_looking = (
        "# ADR-007: Adopt event sourcing\n"
        "Status: Accepted\n"
        "Deciders: platform team\n"
        "Decision: we will adopt event sourcing for the ledger.\n"
    )
    assignment = assign_lane(DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False)
    assert assignment.lane is Lane.DROPPED
    with pytest.raises(LaneAssignmentError, match="never becomes graph state"):
        _event(assignment, _candidate(structured_looking))
    record = dropped_chatter_record(assignment, excerpt=structured_looking)
    assert record.reason_code.endswith(
        f"/v{LANE_POLICY_VERSION}/unattributed_chatter/dropped"
    )
    # Forging the structured lane claim outright is caught by the policy
    # revalidation step of the boundary.
    forged = _claimed_assignment(
        DeriveSourceType.UNATTRIBUTED_CHATTER, Lane.STRUCTURED, False, True
    )
    with pytest.raises(LaneAssignmentError, match="does not match lane policy"):
        validate_policy_auto_promotion(forged)


def test_backfilled_with_genuine_structured_provenance_still_advisory_only() -> None:
    # The cortex#362 non-negotiable on the hardest case: the candidate cites
    # genuine structured-source provenance spans, and is still refused.
    adr_content = (
        "# ADR-0007: Adopt event sourcing\n"
        "Status: Accepted\n"
        "Decision: the hosted ledger is append-only and event-sourced.\n"
    )
    document = _document(adr_content)
    span = document.span(start_offset=0, end_offset=len(adr_content))
    candidate = DeriveCandidate(
        decision_text="The hosted ledger is append-only and event-sourced.",
        spans=(span,),
    )
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=True)
    assert assignment.advisory_only is True
    assert assignment.auto_promotable is False
    event = _event(assignment, candidate)
    # Genuine structured provenance is cited on the event...
    assert event.source_span_hashes == candidate.span_hashes
    confidence = ConfidenceState.from_payload(event.payload[CONFIDENCE_PAYLOAD_KEY])
    assert confidence.advisory_only is True
    # ...and the boundary still refuses auto-promotion, with the verbatim
    # non-negotiable as the visible reason.
    with pytest.raises(
        LanePolicyValidationError,
        match="backfilled nodes default advisory-only and are never auto-promotable",
    ):
        validate_policy_auto_promotion(assignment)


def test_provisional_candidate_claiming_auto_promotable_is_unrepresentable() -> None:
    with pytest.raises(LanePolicyValidationError, match="only structured-lane"):
        _claimed_assignment(
            DeriveSourceType.COMMIT_MESSAGE, Lane.PROVISIONAL, False, True
        )
    # lanes.py validates policies at construction: the weakened rule itself
    # cannot exist, so no custom policy can grant auto-promotion outside the
    # structured lane (the cortex#360 custom-policy weakening check).
    with pytest.raises(LanePolicyValidationError, match="auto_promote is only legal"):
        SourceTypeRule(DeriveSourceType.COMMIT_MESSAGE, Lane.PROVISIONAL, auto_promote=True)
    with pytest.raises(LanePolicyValidationError, match="auto_promote is only legal"):
        SourceTypeRule(DeriveSourceType.UNATTRIBUTED_CHATTER, Lane.DROPPED, auto_promote=True)


def test_dropped_candidate_attempting_graph_entry_raises() -> None:
    assignment = assign_lane(DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False)
    with pytest.raises(LaneAssignmentError, match="never becomes graph state"):
        _event(assignment, _candidate())
    # Asserted against the shipped enum values only: no status grants the
    # dropped lane graph entry, and it has no lifecycle transitions at all.
    for status in DecisionStatus:
        with pytest.raises(LanePolicyValidationError, match="never enters"):
            validate_entry_status(Lane.DROPPED, status)
    for from_status, to_status in product(DecisionStatus, DecisionStatus):
        with pytest.raises(LanePolicyValidationError, match="no lifecycle transitions"):
            validate_status_transition(Lane.DROPPED, from_status, to_status)


def test_dropped_duplication_and_idempotent_replay_yield_zero_graph_rows(
    tmp_path: Path,
) -> None:
    # Duplication / idempotent resubmission of the same dropped item must
    # produce zero graph-state rows on every attempt. Events are the only
    # persistence surface in the Stage 0 substrate — decision_nodes,
    # decision_versions, and decision_edges rows all derive from persisted
    # ledger events — so an item that can never construct an event can never
    # become any graph row.
    assignment = assign_lane(DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False)
    chatter = "duplicate chatter resubmitted across runs\n"
    db_path = tmp_path / "derive-events.sqlite"
    for _ in range(3):
        with DeriveEventStore(db_path) as store:
            with pytest.raises(LaneAssignmentError):
                _event(assignment, _candidate(chatter))
            record = dropped_chatter_record(assignment, excerpt=chatter)
            assert record.reason_code.endswith("/unattributed_chatter/dropped")
            assert store.append_events([]) == AppendOutcome(inserted=0, ignored=0)
            assert store.event_hashes() == frozenset()
            assert store.export_events() == ()


def test_transitions_absent_from_the_contract_are_illegal_by_default() -> None:
    # Exhaustive over the shipped enum: for each graph lane, every
    # status-to-status pair not listed in the contract is rejected.
    for lane in (Lane.STRUCTURED, Lane.PROVISIONAL):
        listed = {
            (transition.from_status, transition.to_status)
            for transition in LANE_STATUS_TRANSITIONS[lane]
        }
        for from_status, to_status in product(DecisionStatus, DecisionStatus):
            if (from_status, to_status) in listed:
                assert isinstance(
                    validate_status_transition(lane, from_status, to_status),
                    LedgerEventType,
                )
            else:
                with pytest.raises(LanePolicyValidationError):
                    validate_status_transition(lane, from_status, to_status)


def test_auto_promotion_boundary_is_candidate_to_confirmed_only() -> None:
    # Asserted against the shipped enum values only.
    assert AUTO_PROMOTION_FROM_STATUS is DecisionStatus.CANDIDATE
    assert AUTO_PROMOTION_TO_STATUS is DecisionStatus.CONFIRMED
    assert (
        validate_status_transition(
            Lane.STRUCTURED, AUTO_PROMOTION_FROM_STATUS, AUTO_PROMOTION_TO_STATUS
        )
        is LedgerEventType.DECISION_CONFIRMED
    )


def test_assignment_citing_another_policy_version_is_refused() -> None:
    # Version-boundary rule: replay must not blend policy regimes.
    assignment = assign_lane(DeriveSourceType.ADR, backfilled=False)
    future_policy = LanePolicy(
        policy_version=LANE_POLICY_VERSION + 1,
        rules=dict(DEFAULT_LANE_POLICY.rules),
    )
    with pytest.raises(LaneAssignmentError, match="blending policy regimes"):
        validate_policy_auto_promotion(assignment, policy=future_policy)
