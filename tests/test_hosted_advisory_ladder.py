"""Tests for the advisory-default emission ladder (cortex#375)."""

from __future__ import annotations

import pytest

from cortex.hosted.advisory_ladder import (
    BLOCKING_ENABLED,
    CONFIDENCE_LABEL_VOCABULARY,
    DEFAULT_ADVISORY_LADDER,
    TIER_EMISSION_BEHAVIOR,
    AdvisoryLadder,
    AdvisoryLadderError,
    EmissionBehavior,
    LadderAssessment,
)
from cortex.hosted.confidence import (
    BLOCKING_ELIGIBLE_TIER,
    CONFIDENCE_TIER_ORDER,
    EMISSION_FLOOR_TIER,
    ConfidenceTier,
)
from cortex.hosted.degradation import DegradationMode, classify_failure


def test_blocking_enabled_is_pinned_false_in_stage_0() -> None:
    """The master-plan policy: advisory-default, blocking is earned later."""

    assert BLOCKING_ENABLED is False


def test_emission_behavior_has_no_blocking_member() -> None:
    """Blocking rendering is unrepresentable, not merely discouraged."""

    assert not any(behavior.value == "blocking" for behavior in EmissionBehavior)
    assert {behavior.value for behavior in EmissionBehavior} == {
        "suggestion",
        "advisory_comment",
        "blocking_eligible_comment",
    }


def test_tier_behavior_mapping_covers_every_tier() -> None:
    assert set(TIER_EMISSION_BEHAVIOR) == set(CONFIDENCE_TIER_ORDER)
    assert TIER_EMISSION_BEHAVIOR[ConfidenceTier.SUGGEST] is EmissionBehavior.SUGGESTION
    assert (
        TIER_EMISSION_BEHAVIOR[ConfidenceTier.ADVISORY]
        is EmissionBehavior.ADVISORY_COMMENT
    )
    assert (
        TIER_EMISSION_BEHAVIOR[BLOCKING_ELIGIBLE_TIER]
        is EmissionBehavior.BLOCKING_ELIGIBLE_COMMENT
    )


def test_default_ladder_emits_every_tier() -> None:
    for tier in CONFIDENCE_TIER_ORDER:
        assessment = DEFAULT_ADVISORY_LADDER.assess(tier)
        assert assessment.emitted is True
        assert assessment.behavior is TIER_EMISSION_BEHAVIOR[tier]
        assert assessment.suppression_reason is None


def test_default_ladder_floor_is_the_module_floor() -> None:
    assert DEFAULT_ADVISORY_LADDER.emission_floor is EMISSION_FLOOR_TIER


def test_raised_floor_suppresses_below_floor_with_visible_reason() -> None:
    ladder = AdvisoryLadder(emission_floor=ConfidenceTier.ADVISORY)
    assessment = ladder.assess(ConfidenceTier.SUGGEST)
    assert assessment.emitted is False
    assert assessment.behavior is None
    assert assessment.suppression_reason is not None
    assert "suggest" in assessment.suppression_reason
    assert "advisory" in assessment.suppression_reason


def test_raised_floor_still_emits_at_and_above_the_floor() -> None:
    ladder = AdvisoryLadder(emission_floor=ConfidenceTier.ADVISORY)
    assert ladder.assess(ConfidenceTier.ADVISORY).emitted is True
    assert ladder.assess(ConfidenceTier.CONFIRMED_CITED).emitted is True


@pytest.mark.parametrize("label", [tier.value for tier in CONFIDENCE_TIER_ORDER])
def test_tier_for_label_parses_the_vocabulary(label: str) -> None:
    assert DEFAULT_ADVISORY_LADDER.tier_for_label(label) is ConfidenceTier(label)


def test_tier_for_label_strips_surrounding_whitespace() -> None:
    assert (
        DEFAULT_ADVISORY_LADDER.tier_for_label("  advisory\n")
        is ConfidenceTier.ADVISORY
    )


@pytest.mark.parametrize("label", ["vibes", "ADVISORY", "confirmed", ""])
def test_tier_for_label_fails_closed_on_unknown_labels(label: str) -> None:
    with pytest.raises(AdvisoryLadderError, match="unknown confidence label"):
        DEFAULT_ADVISORY_LADDER.tier_for_label(label)


def test_unknown_label_error_names_the_vocabulary() -> None:
    with pytest.raises(AdvisoryLadderError, match="confirmed_cited"):
        DEFAULT_ADVISORY_LADDER.tier_for_label("certain")


def test_vocabulary_matches_tier_order() -> None:
    expected = tuple(tier.value for tier in CONFIDENCE_TIER_ORDER)
    assert expected == CONFIDENCE_LABEL_VOCABULARY


def test_behavior_for_below_floor_raises() -> None:
    ladder = AdvisoryLadder(emission_floor=ConfidenceTier.ADVISORY)
    with pytest.raises(AdvisoryLadderError, match="below the emission floor"):
        ladder.behavior_for(ConfidenceTier.SUGGEST)


def test_emission_floor_must_be_a_confidence_tier() -> None:
    with pytest.raises(AdvisoryLadderError, match="must be a ConfidenceTier"):
        AdvisoryLadder(emission_floor="advisory")  # type: ignore[arg-type]


def test_assessment_emitted_requires_a_behavior() -> None:
    with pytest.raises(AdvisoryLadderError, match="requires a behavior"):
        LadderAssessment(
            tier=ConfidenceTier.ADVISORY,
            emitted=True,
            behavior=None,
            suppression_reason=None,
        )


def test_assessment_suppression_requires_a_reason() -> None:
    """Silent drops are unrepresentable: suppressed needs a non-empty reason."""

    with pytest.raises(AdvisoryLadderError, match="suppression"):
        LadderAssessment(
            tier=ConfidenceTier.SUGGEST,
            emitted=False,
            behavior=None,
            suppression_reason=None,
        )
    with pytest.raises(AdvisoryLadderError, match="suppression"):
        LadderAssessment(
            tier=ConfidenceTier.SUGGEST,
            emitted=False,
            behavior=None,
            suppression_reason="   ",
        )


def test_assessment_behavior_must_match_the_tier() -> None:
    with pytest.raises(AdvisoryLadderError, match="emits as"):
        LadderAssessment(
            tier=ConfidenceTier.SUGGEST,
            emitted=True,
            behavior=EmissionBehavior.BLOCKING_ELIGIBLE_COMMENT,
            suppression_reason=None,
        )


def test_assessment_payload_carries_blocking_policy() -> None:
    payload = DEFAULT_ADVISORY_LADDER.assess(ConfidenceTier.CONFIRMED_CITED).as_payload()
    assert payload["blocking_enabled"] is False
    assert payload["behavior"] == "blocking_eligible_comment"
    assert payload["emitted"] is True


def test_ladder_error_classifies_in_the_degradation_taxonomy() -> None:
    assert (
        classify_failure(AdvisoryLadderError("boundary probe"))
        is DegradationMode.INVALID_INPUT_REJECTED
    )
