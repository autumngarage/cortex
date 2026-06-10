"""Advisory-default emission ladder for the soft evaluator (cortex#375).

Maps the cortex#316 confidence tiers (``confidence.ConfidenceTier``) onto
evaluator *emission behavior*. The ladder is advisory-default, mirroring the
master plan's earned-blocking bar:

- ``suggest``-tier findings render as suggestions,
- ``advisory``-tier findings render as advisory comments,
- ``confirmed_cited``-tier findings are *eligible* for future blocking but
  are NEVER rendered blocking in Stage 0 — see :data:`BLOCKING_ENABLED`.

Blocking is structurally unrepresentable here: :class:`EmissionBehavior` has
no blocking member, so no caller can render a blocking finding by accident.
When the earned-blocking gate ships (cortex#379's Wilson-lower-bound
eligibility, Future milestone m3), flipping the policy is an explicit,
reviewed change to :data:`BLOCKING_ENABLED` plus a new behavior member — not
a config drift.

The emission floor is the second invariant: findings whose tier sits below
:attr:`AdvisoryLadder.emission_floor` are not emitted, and the evaluator
(cortex#370) counts each one in a visible ``suppressed_below_floor`` counter.
Silent drops are unrepresentable — :meth:`AdvisoryLadder.assess` always
returns either an emission behavior or a non-empty suppression reason.

This module also owns the confidence-label vocabulary the model boundary
references (``model_interfaces.FindingDraft.confidence_label`` says "#375
owns the ladder vocabulary"): labels are exactly the tier values, and an
unknown label fails closed with :class:`AdvisoryLadderError`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from cortex.hosted.confidence import (
    BLOCKING_ELIGIBLE_TIER,
    CONFIDENCE_TIER_ORDER,
    EMISSION_FLOOR_TIER,
    ConfidenceTier,
    tier_rank,
)

# Stage 0 policy constant: the evaluator's output is advisory-only. Blocking
# is earned, never default — the hosted master plan (Obsidian
# ``cortex_master_plan.md``, canonical 2026-06-09) gates blocking behind the
# earned-blocking bar (>=2 distinct accepting authors per decision, zero
# overrides in window W) and the cortex#379 statistical eligibility gate,
# none of which exist in Stage 0. ``confirmed_cited`` findings therefore
# render as blocking-*eligible* advisory comments only.
BLOCKING_ENABLED = False


class AdvisoryLadderError(ValueError):
    """Raised when ladder vocabulary or assessment material is invalid."""


class EmissionBehavior(StrEnum):
    """How an emitted finding renders on the review surface.

    There is deliberately no blocking member: with
    :data:`BLOCKING_ENABLED` false in Stage 0, a blocking rendering is
    unrepresentable rather than merely discouraged.
    """

    SUGGESTION = "suggestion"
    ADVISORY_COMMENT = "advisory_comment"
    BLOCKING_ELIGIBLE_COMMENT = "blocking_eligible_comment"


# Tier -> rendering, one entry per ladder rung. ``confirmed_cited`` maps to
# the blocking-*eligible* advisory rendering; eligibility is metadata, the
# rendering stays advisory while BLOCKING_ENABLED is False.
TIER_EMISSION_BEHAVIOR: Mapping[ConfidenceTier, EmissionBehavior] = MappingProxyType(
    {
        ConfidenceTier.SUGGEST: EmissionBehavior.SUGGESTION,
        ConfidenceTier.ADVISORY: EmissionBehavior.ADVISORY_COMMENT,
        BLOCKING_ELIGIBLE_TIER: EmissionBehavior.BLOCKING_ELIGIBLE_COMMENT,
    }
)

# The label vocabulary the evaluate prompt asks for and FindingDraft carries:
# exactly the tier values, in ladder order. cortex#344's boundary defers
# label semantics to this module.
CONFIDENCE_LABEL_VOCABULARY: tuple[str, ...] = tuple(
    tier.value for tier in CONFIDENCE_TIER_ORDER
)


@dataclass(frozen=True)
class LadderAssessment:
    """One finding's ladder verdict: emit with a behavior, or suppress loudly.

    Exactly one of ``behavior`` / ``suppression_reason`` is set; an
    assessment that neither emits nor explains is unrepresentable.
    """

    tier: ConfidenceTier
    emitted: bool
    behavior: EmissionBehavior | None
    suppression_reason: str | None

    def __post_init__(self) -> None:
        if self.emitted:
            if self.behavior is None:
                raise AdvisoryLadderError("an emitted assessment requires a behavior")
            if self.suppression_reason is not None:
                raise AdvisoryLadderError(
                    "an emitted assessment must not carry a suppression reason"
                )
            if self.behavior is not TIER_EMISSION_BEHAVIOR[self.tier]:
                raise AdvisoryLadderError(
                    f"tier {self.tier.value!r} emits as "
                    f"{TIER_EMISSION_BEHAVIOR[self.tier].value!r}, "
                    f"not {self.behavior.value!r}"
                )
        else:
            if self.behavior is not None:
                raise AdvisoryLadderError(
                    "a suppressed assessment must not carry a behavior"
                )
            if self.suppression_reason is None or not self.suppression_reason.strip():
                raise AdvisoryLadderError(
                    "a suppressed assessment requires a non-empty suppression "
                    "reason; silent drops are unrepresentable"
                )

    def as_payload(self) -> dict[str, object]:
        return {
            "behavior": None if self.behavior is None else self.behavior.value,
            "blocking_enabled": BLOCKING_ENABLED,
            "emitted": self.emitted,
            "suppression_reason": self.suppression_reason,
            "tier": self.tier.value,
        }


@dataclass(frozen=True)
class AdvisoryLadder:
    """The advisory-default ladder the evaluator consults per finding.

    ``emission_floor`` may be raised above the module floor
    (``confidence.EMISSION_FLOOR_TIER``) per deployment — never lowered,
    which is structural: the module floor is the lowest tier, so every
    ``ConfidenceTier`` value is a valid (>= floor) configuration.
    """

    emission_floor: ConfidenceTier = EMISSION_FLOOR_TIER

    def __post_init__(self) -> None:
        if not isinstance(self.emission_floor, ConfidenceTier):
            raise AdvisoryLadderError(
                "emission_floor must be a ConfidenceTier; got "
                f"{self.emission_floor!r}"
            )

    def tier_for_label(self, label: str) -> ConfidenceTier:
        """Parse a model-reported confidence label, failing closed.

        The vocabulary is exactly :data:`CONFIDENCE_LABEL_VOCABULARY`; an
        unknown label is rejected so a fabricated or drifted label can never
        place a finding on the ladder.
        """

        candidate = label.strip()
        try:
            return ConfidenceTier(candidate)
        except ValueError as exc:
            raise AdvisoryLadderError(
                f"unknown confidence label {label!r}; the ladder vocabulary is "
                f"{list(CONFIDENCE_LABEL_VOCABULARY)}"
            ) from exc

    def behavior_for(self, tier: ConfidenceTier) -> EmissionBehavior:
        """Return the rendering for a tier at or above the emission floor."""

        if tier_rank(tier) < tier_rank(self.emission_floor):
            raise AdvisoryLadderError(
                f"tier {tier.value!r} is below the emission floor "
                f"{self.emission_floor.value!r}; assess() suppresses it instead "
                "of rendering it"
            )
        return TIER_EMISSION_BEHAVIOR[tier]

    def assess(self, tier: ConfidenceTier) -> LadderAssessment:
        """Assess one finding's tier against the floor.

        Below-floor tiers return a suppressed assessment with a non-empty
        reason; the evaluator counts these in ``suppressed_below_floor``.
        """

        if tier_rank(tier) < tier_rank(self.emission_floor):
            return LadderAssessment(
                tier=tier,
                emitted=False,
                behavior=None,
                suppression_reason=(
                    f"tier {tier.value!r} is below the emission floor "
                    f"{self.emission_floor.value!r}"
                ),
            )
        return LadderAssessment(
            tier=tier,
            emitted=True,
            behavior=TIER_EMISSION_BEHAVIOR[tier],
            suppression_reason=None,
        )


DEFAULT_ADVISORY_LADDER = AdvisoryLadder()
