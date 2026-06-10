from __future__ import annotations

import re
from itertools import product

import pytest

from cortex.hosted.lanes import (
    AUTO_PROMOTION_FROM_STATUS,
    AUTO_PROMOTION_TO_STATUS,
    DEFAULT_LANE_POLICY,
    LANE_ENTRY_EVENT,
    LANE_ENTRY_STATUSES,
    LANE_POLICY_VERSION,
    LANE_STATUS_TRANSITIONS,
    TERMINAL_STATUSES,
    DecisionStatus,
    DeriveSourceType,
    Lane,
    LaneAssignment,
    LanePolicy,
    LanePolicyValidationError,
    SourceTypeRule,
    StatusTransition,
    allowed_entry_statuses,
    validate_auto_promotion,
    validate_entry_status,
    validate_status_transition,
)
from cortex.hosted.ledger_events import LedgerEventType
from cortex.hosted.schema import create_schema_sql


def test_decision_status_mirrors_shipped_schema_check_verbatim() -> None:
    ddl = create_schema_sql()
    match = re.search(r"CHECK \(status IN \(([^)]+)\)\)", ddl)
    assert match is not None, "decision_nodes.status CHECK not found in shipped DDL"
    shipped = tuple(value.strip().strip("'") for value in match.group(1).split(","))
    assert shipped == tuple(status.value for status in DecisionStatus)


def test_policy_table_is_total_over_every_source_type_and_backfill_flag() -> None:
    for source_type, backfilled in product(DeriveSourceType, (False, True)):
        assignment = DEFAULT_LANE_POLICY.assign(source_type, backfilled=backfilled)
        assert assignment.lane in Lane
        assert assignment.rule_citation.startswith(
            f"cortex.hosted.lanes/v{LANE_POLICY_VERSION}/"
        )


def test_every_lane_has_entry_statuses_and_transition_table() -> None:
    assert set(LANE_ENTRY_STATUSES) == set(Lane)
    assert set(LANE_STATUS_TRANSITIONS) == set(Lane)


def test_legal_transitions_are_a_closed_set_per_lane() -> None:
    for lane in Lane:
        listed = {
            (transition.from_status, transition.to_status)
            for transition in LANE_STATUS_TRANSITIONS[lane]
        }
        for from_status, to_status in product(DecisionStatus, DecisionStatus):
            if (from_status, to_status) in listed:
                event = validate_status_transition(lane, from_status, to_status)
                assert isinstance(event, LedgerEventType)
            else:
                with pytest.raises(LanePolicyValidationError):
                    validate_status_transition(lane, from_status, to_status)


def test_every_legal_transition_names_its_recording_ledger_event() -> None:
    expected = {
        (DecisionStatus.CANDIDATE, DecisionStatus.CONFIRMED): (
            LedgerEventType.DECISION_CONFIRMED
        ),
        (DecisionStatus.CANDIDATE, DecisionStatus.REJECTED): (
            LedgerEventType.DECISION_REJECTED
        ),
        (DecisionStatus.CANDIDATE, DecisionStatus.SUPERSEDED): (
            LedgerEventType.DECISION_SUPERSEDED
        ),
        (DecisionStatus.CANDIDATE, DecisionStatus.STALE): LedgerEventType.STALE_MARKED,
        (DecisionStatus.CONFIRMED, DecisionStatus.SUPERSEDED): (
            LedgerEventType.DECISION_SUPERSEDED
        ),
        (DecisionStatus.CONFIRMED, DecisionStatus.STALE): LedgerEventType.STALE_MARKED,
        (DecisionStatus.STALE, DecisionStatus.CONFIRMED): (
            LedgerEventType.DECISION_CONFIRMED
        ),
        (DecisionStatus.STALE, DecisionStatus.SUPERSEDED): (
            LedgerEventType.DECISION_SUPERSEDED
        ),
    }
    for lane in (Lane.STRUCTURED, Lane.PROVISIONAL):
        actual = {
            (transition.from_status, transition.to_status): transition.ledger_event
            for transition in LANE_STATUS_TRANSITIONS[lane]
        }
        assert actual == expected
    assert LANE_STATUS_TRANSITIONS[Lane.DROPPED] == ()


def test_terminal_statuses_have_no_outgoing_transitions() -> None:
    for lane in Lane:
        for transition in LANE_STATUS_TRANSITIONS[lane]:
            assert transition.from_status not in TERMINAL_STATUSES


def test_graph_entry_is_candidate_only_and_recorded_by_candidate_proposed() -> None:
    assert LANE_ENTRY_EVENT is LedgerEventType.CANDIDATE_PROPOSED
    for lane in (Lane.STRUCTURED, Lane.PROVISIONAL):
        assert allowed_entry_statuses(lane) == (DecisionStatus.CANDIDATE,)
        assert (
            validate_entry_status(lane, DecisionStatus.CANDIDATE)
            is LedgerEventType.CANDIDATE_PROPOSED
        )
        with pytest.raises(LanePolicyValidationError, match="cannot enter the graph"):
            validate_entry_status(lane, DecisionStatus.CONFIRMED)


def test_dropped_lane_never_enters_the_graph() -> None:
    assert allowed_entry_statuses(Lane.DROPPED) == ()
    assignment = DEFAULT_LANE_POLICY.assign(
        DeriveSourceType.UNATTRIBUTED_CHATTER, backfilled=False
    )
    assert assignment.lane is Lane.DROPPED
    assert assignment.enters_graph is False
    assert assignment.auto_promotable is False
    for status in DecisionStatus:
        with pytest.raises(LanePolicyValidationError, match="never enters"):
            validate_entry_status(Lane.DROPPED, status)


def test_backfilled_assignments_are_advisory_only_for_every_lane() -> None:
    for source_type in DeriveSourceType:
        assignment = DEFAULT_LANE_POLICY.assign(source_type, backfilled=True)
        assert assignment.advisory_only is True
        assert assignment.auto_promotable is False
        if assignment.enters_graph:
            with pytest.raises(
                LanePolicyValidationError, match="never auto-promotable"
            ):
                validate_auto_promotion(assignment)


def test_backfilled_auto_promotable_assignment_is_unrepresentable() -> None:
    with pytest.raises(LanePolicyValidationError, match="never auto-promotable"):
        LaneAssignment(
            policy_version=LANE_POLICY_VERSION,
            source_type=DeriveSourceType.ADR,
            lane=Lane.STRUCTURED,
            backfilled=True,
            advisory_only=True,
            auto_promotable=True,
            enters_graph=True,
            rule_citation="cortex.hosted.lanes/v1/adr/structured",
        )


def test_backfilled_assignment_claiming_full_trust_is_unrepresentable() -> None:
    with pytest.raises(LanePolicyValidationError, match="advisory_only"):
        LaneAssignment(
            policy_version=LANE_POLICY_VERSION,
            source_type=DeriveSourceType.ADR,
            lane=Lane.STRUCTURED,
            backfilled=True,
            advisory_only=False,
            auto_promotable=False,
            enters_graph=True,
            rule_citation="cortex.hosted.lanes/v1/adr/structured",
        )


def test_provisional_lane_never_auto_promotes() -> None:
    for source_type in (
        DeriveSourceType.COMMIT_MESSAGE,
        DeriveSourceType.PR_DESCRIPTION,
        DeriveSourceType.PR_REVIEW_COMMENT,
    ):
        assignment = DEFAULT_LANE_POLICY.assign(source_type, backfilled=False)
        assert assignment.lane is Lane.PROVISIONAL
        assert assignment.advisory_only is True
        assert assignment.auto_promotable is False
        with pytest.raises(
            LanePolicyValidationError, match="human confirmation is required"
        ):
            validate_auto_promotion(assignment)


def test_structured_source_rule_permits_auto_promotion() -> None:
    assignment = DEFAULT_LANE_POLICY.assign(DeriveSourceType.ADR, backfilled=False)
    assert assignment.lane is Lane.STRUCTURED
    assert assignment.advisory_only is False
    assert assignment.auto_promotable is True
    assert validate_auto_promotion(assignment) is LedgerEventType.DECISION_CONFIRMED
    assert AUTO_PROMOTION_FROM_STATUS is DecisionStatus.CANDIDATE
    assert AUTO_PROMOTION_TO_STATUS is DecisionStatus.CONFIRMED


def test_structured_rule_without_auto_promote_refuses_unattended_promotion() -> None:
    policy = LanePolicy(
        policy_version=2,
        rules={
            **dict(DEFAULT_LANE_POLICY.rules),
            DeriveSourceType.ADR: SourceTypeRule(DeriveSourceType.ADR, Lane.STRUCTURED),
        },
    )
    assignment = policy.assign(DeriveSourceType.ADR, backfilled=False)
    with pytest.raises(LanePolicyValidationError, match="does not permit auto-promotion"):
        validate_auto_promotion(assignment)


def test_auto_promote_rule_outside_structured_lane_is_unrepresentable() -> None:
    with pytest.raises(LanePolicyValidationError, match="only legal in the structured"):
        SourceTypeRule(
            DeriveSourceType.COMMIT_MESSAGE, Lane.PROVISIONAL, auto_promote=True
        )


def test_lane_policy_must_be_total() -> None:
    rules = dict(DEFAULT_LANE_POLICY.rules)
    del rules[DeriveSourceType.ADR]
    with pytest.raises(LanePolicyValidationError, match="must be total"):
        LanePolicy(policy_version=1, rules=rules)


def test_lane_policy_rejects_mismatched_rule_keys() -> None:
    rules = dict(DEFAULT_LANE_POLICY.rules)
    rules[DeriveSourceType.ADR] = SourceTypeRule(
        DeriveSourceType.CODEOWNERS, Lane.STRUCTURED, auto_promote=True
    )
    with pytest.raises(LanePolicyValidationError, match="names codeowners"):
        LanePolicy(policy_version=1, rules=rules)


def test_lane_policy_rejects_non_positive_version() -> None:
    with pytest.raises(LanePolicyValidationError, match="policy_version"):
        LanePolicy(policy_version=0, rules=dict(DEFAULT_LANE_POLICY.rules))


def test_status_transition_must_change_status() -> None:
    with pytest.raises(LanePolicyValidationError, match="must change"):
        StatusTransition(
            DecisionStatus.CANDIDATE,
            DecisionStatus.CANDIDATE,
            LedgerEventType.DECISION_CONFIRMED,
        )


def test_lane_assignment_payload_round_trips() -> None:
    for source_type, backfilled in product(DeriveSourceType, (False, True)):
        assignment = DEFAULT_LANE_POLICY.assign(source_type, backfilled=backfilled)
        assert LaneAssignment.from_payload(assignment.as_payload()) == assignment


def test_lane_assignment_payload_fails_closed_on_missing_fields() -> None:
    payload = DEFAULT_LANE_POLICY.assign(
        DeriveSourceType.ADR, backfilled=False
    ).as_payload()
    del payload["lane"]
    with pytest.raises(LanePolicyValidationError, match="payload is invalid"):
        LaneAssignment.from_payload(payload)


def test_lane_assignment_payload_fails_closed_on_unknown_lane() -> None:
    payload = DEFAULT_LANE_POLICY.assign(
        DeriveSourceType.ADR, backfilled=False
    ).as_payload()
    payload["lane"] = "express"
    with pytest.raises(LanePolicyValidationError, match="payload is invalid"):
        LaneAssignment.from_payload(payload)
