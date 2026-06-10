from __future__ import annotations

import pytest

from cortex.hosted.confidence import (
    ADVISORY_ONLY_TIER_CAP,
    BLOCKING_CONFIRMATION_COUNT_K,
    BLOCKING_ELIGIBLE_TIER,
    BLOCKING_OVERRIDE_WINDOW_DAYS_W,
    CONFIDENCE_TIER_ORDER,
    DEFAULT_CONFIDENCE_TIER,
    EMISSION_FLOOR_TIER,
    TIER_MINIMUM_EVIDENCE,
    WILSON_LOWER_BOUND_GATE_ISSUE,
    ConfidenceState,
    ConfidenceTier,
    ConfidenceValidationError,
    apply_tier_transition,
    tier_rank,
    validate_emission_tier,
)


def _advisory_state(**overrides: object) -> ConfidenceState:
    fields: dict[str, object] = {
        "tier": ConfidenceTier.ADVISORY,
        "confirmation_count": 0,
        "citation_count": 1,
        "override_count_in_window": 0,
        "last_transition_reason": "derived with cited spans",
        "advisory_only": False,
    }
    fields.update(overrides)
    return ConfidenceState(**fields)  # type: ignore[arg-type]


def test_ladder_order_floor_default_and_blocking_tier() -> None:
    assert CONFIDENCE_TIER_ORDER == (
        ConfidenceTier.SUGGEST,
        ConfidenceTier.ADVISORY,
        ConfidenceTier.CONFIRMED_CITED,
    )
    assert set(CONFIDENCE_TIER_ORDER) == set(ConfidenceTier)
    assert EMISSION_FLOOR_TIER is CONFIDENCE_TIER_ORDER[0]
    assert DEFAULT_CONFIDENCE_TIER is ConfidenceTier.ADVISORY
    assert BLOCKING_ELIGIBLE_TIER is CONFIDENCE_TIER_ORDER[-1]
    assert ADVISORY_ONLY_TIER_CAP is ConfidenceTier.ADVISORY
    assert [tier_rank(tier) for tier in CONFIDENCE_TIER_ORDER] == [0, 1, 2]


def test_evidence_minimums_table_is_total_and_anchored_to_k() -> None:
    assert set(TIER_MINIMUM_EVIDENCE) == set(ConfidenceTier)
    assert (
        TIER_MINIMUM_EVIDENCE[ConfidenceTier.CONFIRMED_CITED].min_confirmations
        == BLOCKING_CONFIRMATION_COUNT_K
    )
    assert TIER_MINIMUM_EVIDENCE[ConfidenceTier.ADVISORY].min_citations == 1
    assert BLOCKING_CONFIRMATION_COUNT_K >= 1
    assert BLOCKING_OVERRIDE_WINDOW_DAYS_W >= 1


def test_wilson_bound_gate_is_explicitly_deferred_not_implemented() -> None:
    assert WILSON_LOWER_BOUND_GATE_ISSUE == "cortex#379"
    import cortex.hosted.confidence as confidence_module

    assert "wilson" not in {
        name.lower() for name in dir(confidence_module) if callable(getattr(confidence_module, name))
    }


def test_emission_floor_is_enforced() -> None:
    assert validate_emission_tier(ConfidenceTier.SUGGEST) is ConfidenceTier.SUGGEST
    with pytest.raises(ConfidenceValidationError, match="below the confidence floor"):
        validate_emission_tier(
            ConfidenceTier.SUGGEST, floor=ConfidenceTier.ADVISORY
        )


def test_state_rejects_negative_counts_and_empty_reason() -> None:
    with pytest.raises(ConfidenceValidationError, match="confirmation_count"):
        _advisory_state(confirmation_count=-1)
    with pytest.raises(ConfidenceValidationError, match="override_count_in_window"):
        _advisory_state(override_count_in_window=-1)
    with pytest.raises(ConfidenceValidationError, match="must not be empty"):
        _advisory_state(last_transition_reason="   ")


def test_uncited_advisory_state_is_unrepresentable() -> None:
    with pytest.raises(ConfidenceValidationError, match="citations"):
        _advisory_state(citation_count=0)


def test_suggest_state_needs_no_evidence() -> None:
    state = ConfidenceState(
        tier=ConfidenceTier.SUGGEST,
        confirmation_count=0,
        citation_count=0,
        override_count_in_window=0,
        last_transition_reason="initial derive emission",
    )
    assert state.tier is EMISSION_FLOOR_TIER


def test_blocking_eligible_tier_requires_k_confirmations() -> None:
    with pytest.raises(ConfidenceValidationError, match="confirmations"):
        ConfidenceState(
            tier=ConfidenceTier.CONFIRMED_CITED,
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K - 1,
            citation_count=1,
            override_count_in_window=0,
            last_transition_reason="not enough confirmations",
        )


def test_blocking_eligible_tier_requires_zero_overrides_in_window() -> None:
    with pytest.raises(ConfidenceValidationError, match="zero overrides"):
        ConfidenceState(
            tier=ConfidenceTier.CONFIRMED_CITED,
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=1,
            override_count_in_window=1,
            last_transition_reason="override inside window",
        )


def test_advisory_only_states_are_capped_below_blocking_eligibility() -> None:
    with pytest.raises(ConfidenceValidationError, match="capped"):
        ConfidenceState(
            tier=ConfidenceTier.CONFIRMED_CITED,
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=1,
            override_count_in_window=0,
            last_transition_reason="backfilled node",
            advisory_only=True,
        )


def test_promotion_climbs_one_rung_with_evidence() -> None:
    suggest = ConfidenceState(
        tier=ConfidenceTier.SUGGEST,
        confirmation_count=0,
        citation_count=0,
        override_count_in_window=0,
        last_transition_reason="initial derive emission",
    )
    advisory = apply_tier_transition(
        suggest,
        to_tier=ConfidenceTier.ADVISORY,
        reason="citations attached",
        confirmation_count=0,
        citation_count=2,
        override_count_in_window=0,
    )
    assert advisory.tier is ConfidenceTier.ADVISORY
    assert advisory.last_transition_reason == "citations attached"
    confirmed = apply_tier_transition(
        advisory,
        to_tier=ConfidenceTier.CONFIRMED_CITED,
        reason="two distinct authors confirmed",
        confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
        citation_count=2,
        override_count_in_window=0,
    )
    assert confirmed.tier is BLOCKING_ELIGIBLE_TIER


def test_promotion_may_not_skip_a_tier() -> None:
    suggest = ConfidenceState(
        tier=ConfidenceTier.SUGGEST,
        confirmation_count=0,
        citation_count=0,
        override_count_in_window=0,
        last_transition_reason="initial derive emission",
    )
    with pytest.raises(ConfidenceValidationError, match="skips a tier"):
        apply_tier_transition(
            suggest,
            to_tier=ConfidenceTier.CONFIRMED_CITED,
            reason="attempted jump",
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=1,
            override_count_in_window=0,
        )


def test_promotion_without_required_evidence_fails_closed() -> None:
    with pytest.raises(ConfidenceValidationError, match="confirmations"):
        apply_tier_transition(
            _advisory_state(),
            to_tier=ConfidenceTier.CONFIRMED_CITED,
            reason="premature promotion",
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K - 1,
            citation_count=1,
            override_count_in_window=0,
        )


def test_promotion_cannot_shrink_evidence() -> None:
    state = _advisory_state(citation_count=3)
    with pytest.raises(ConfidenceValidationError, match="cannot shrink citation"):
        apply_tier_transition(
            state,
            to_tier=ConfidenceTier.CONFIRMED_CITED,
            reason="evidence shrank",
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=2,
            override_count_in_window=0,
        )


def test_promotion_with_overrides_in_window_fails_closed() -> None:
    with pytest.raises(ConfidenceValidationError, match="zero overrides"):
        apply_tier_transition(
            _advisory_state(),
            to_tier=ConfidenceTier.CONFIRMED_CITED,
            reason="override still in window",
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=1,
            override_count_in_window=1,
        )


def test_advisory_only_nodes_are_never_promoted_past_the_cap() -> None:
    state = _advisory_state(advisory_only=True)
    with pytest.raises(ConfidenceValidationError, match="never promoted past"):
        apply_tier_transition(
            state,
            to_tier=ConfidenceTier.CONFIRMED_CITED,
            reason="backfilled node trying to earn blocking",
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=1,
            override_count_in_window=0,
        )


def test_demotion_is_always_allowed_and_records_the_reason() -> None:
    confirmed = ConfidenceState(
        tier=ConfidenceTier.CONFIRMED_CITED,
        confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
        citation_count=2,
        override_count_in_window=0,
        last_transition_reason="earned",
    )
    demoted = apply_tier_transition(
        confirmed,
        to_tier=ConfidenceTier.SUGGEST,
        reason="precision-wrong override recorded",
        confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
        citation_count=0,
        override_count_in_window=1,
    )
    assert demoted.tier is ConfidenceTier.SUGGEST
    assert demoted.last_transition_reason == "precision-wrong override recorded"
    assert demoted.override_count_in_window == 1


def test_demotion_requires_a_loud_reason() -> None:
    confirmed = ConfidenceState(
        tier=ConfidenceTier.CONFIRMED_CITED,
        confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
        citation_count=2,
        override_count_in_window=0,
        last_transition_reason="earned",
    )
    with pytest.raises(ConfidenceValidationError, match="non-empty reason"):
        apply_tier_transition(
            confirmed,
            to_tier=ConfidenceTier.ADVISORY,
            reason="  ",
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=2,
            override_count_in_window=0,
        )


def test_same_tier_transition_is_illegal() -> None:
    with pytest.raises(ConfidenceValidationError, match="must change the tier"):
        apply_tier_transition(
            _advisory_state(),
            to_tier=ConfidenceTier.ADVISORY,
            reason="no-op",
            confirmation_count=0,
            citation_count=1,
            override_count_in_window=0,
        )


def test_confidence_state_payload_round_trips() -> None:
    for state in (
        _advisory_state(),
        _advisory_state(advisory_only=True),
        ConfidenceState(
            tier=ConfidenceTier.CONFIRMED_CITED,
            confirmation_count=BLOCKING_CONFIRMATION_COUNT_K,
            citation_count=4,
            override_count_in_window=0,
            last_transition_reason="earned",
        ),
    ):
        assert ConfidenceState.from_payload(state.as_payload()) == state


def test_confidence_state_payload_fails_closed_on_bad_fields() -> None:
    payload = _advisory_state().as_payload()
    payload["tier"] = "blocking"
    with pytest.raises(ConfidenceValidationError, match="payload is invalid"):
        ConfidenceState.from_payload(payload)
    payload = _advisory_state().as_payload()
    del payload["citation_count"]
    with pytest.raises(ConfidenceValidationError, match="payload is invalid"):
        ConfidenceState.from_payload(payload)
