"""Confidence model for decision nodes, separate from lifecycle state.

This module is the cortex#316 artifact. ``decision_nodes.status`` (owned by
``schema.py``) says where a node is in its lifecycle; the confidence tier says
how much trust its evidence has earned. The two never share a vocabulary.

The ladder is advisory-default: ``suggest`` is the emission floor, ``advisory``
is the default tier for cited derive output, and ``confirmed_cited`` is the
only blocking-eligible tier. Two roadmap invariants are encoded as validation,
not prose:

- No node emits below the confidence floor (``EMISSION_FLOOR_TIER``).
- No node reaches the blocking-eligible tier below confirmation count K
  (``BLOCKING_CONFIRMATION_COUNT_K``) or with any override recorded inside
  window W (``BLOCKING_OVERRIDE_WINDOW_DAYS_W``).

The K/W raw-count gate is the Stage 0 stand-in: the Wilson-lower-bound
eligibility calculation is deferred to cortex#379 (Future milestone m3,
tracker cortex#447) and is intentionally not implemented here. The evaluator
ladder (cortex#375) maps these tiers onto evaluator behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class ConfidenceValidationError(ValueError):
    """Raised when a confidence state or tier transition violates the model."""


class ConfidenceTier(StrEnum):
    """Advisory-default confidence ladder, ordered by ``CONFIDENCE_TIER_ORDER``."""

    SUGGEST = "suggest"
    ADVISORY = "advisory"
    CONFIRMED_CITED = "confirmed_cited"


CONFIDENCE_TIER_ORDER: tuple[ConfidenceTier, ...] = (
    ConfidenceTier.SUGGEST,
    ConfidenceTier.ADVISORY,
    ConfidenceTier.CONFIRMED_CITED,
)

# No node emits below this floor; cortex#375 may raise it per finding class
# but never lower it.
EMISSION_FLOOR_TIER = ConfidenceTier.SUGGEST

# Advisory-default: cited derive output starts here, not at the top.
DEFAULT_CONFIDENCE_TIER = ConfidenceTier.ADVISORY

# Advisory-only nodes (backfilled or provisional-lane, per cortex#362 and the
# lane policy in lanes.py) are capped at this tier and can never become
# blocking-eligible without leaving advisory-only through a human path.
ADVISORY_ONLY_TIER_CAP = ConfidenceTier.ADVISORY

BLOCKING_ELIGIBLE_TIER = ConfidenceTier.CONFIRMED_CITED

# K: minimum human confirmation events before the blocking-eligible tier.
# Value from the master plan's earned-blocking bar (">=2 distinct accepting
# authors per decision"); cortex#379's Wilson-lower-bound gate supersedes this
# raw count when the Future milestone lands.
BLOCKING_CONFIRMATION_COUNT_K = 2

# W: callers count override events inside this many days when reporting
# `override_count_in_window`. The window value is a Stage 0 placeholder to be
# calibrated by cortex#379 alongside the Wilson bound.
BLOCKING_OVERRIDE_WINDOW_DAYS_W = 30

# Machine-checkable deferral marker: the statistical eligibility gate is not
# implemented in this module by design.
WILSON_LOWER_BOUND_GATE_ISSUE = "cortex#379"


@dataclass(frozen=True)
class EvidenceMinimums:
    """Evidence floor a state must satisfy to sit at a tier."""

    min_confirmations: int
    min_citations: int


# Advisory output must cite at least one source span — uncited advisory
# confidence is unrepresentable, matching the cited-or-no-answer invariant.
TIER_MINIMUM_EVIDENCE: Mapping[ConfidenceTier, EvidenceMinimums] = MappingProxyType(
    {
        ConfidenceTier.SUGGEST: EvidenceMinimums(min_confirmations=0, min_citations=0),
        ConfidenceTier.ADVISORY: EvidenceMinimums(min_confirmations=0, min_citations=1),
        ConfidenceTier.CONFIRMED_CITED: EvidenceMinimums(
            min_confirmations=BLOCKING_CONFIRMATION_COUNT_K, min_citations=1
        ),
    }
)


def tier_rank(tier: ConfidenceTier) -> int:
    """Return the tier's position on the ladder (floor is 0)."""

    return CONFIDENCE_TIER_ORDER.index(tier)


def validate_emission_tier(
    tier: ConfidenceTier, *, floor: ConfidenceTier = EMISSION_FLOOR_TIER
) -> ConfidenceTier:
    """Refuse emission below the confidence floor."""

    if tier_rank(tier) < tier_rank(floor):
        raise ConfidenceValidationError(
            f"no node emits below the confidence floor; {tier.value!r} is below "
            f"{floor.value!r}"
        )
    return tier


@dataclass(frozen=True)
class ConfidenceState:
    """A node's confidence tier plus the evidence counts supporting it.

    ``override_count_in_window`` counts override events inside the last
    ``BLOCKING_OVERRIDE_WINDOW_DAYS_W`` days; the caller owns the clock, the
    model owns the gate. ``advisory_only`` is the cortex#362 marking: it caps
    the tier at ``ADVISORY_ONLY_TIER_CAP`` and is distinct from lifecycle
    status.
    """

    tier: ConfidenceTier
    confirmation_count: int
    citation_count: int
    override_count_in_window: int
    last_transition_reason: str
    advisory_only: bool = False

    def __post_init__(self) -> None:
        for name, count in (
            ("confirmation_count", self.confirmation_count),
            ("citation_count", self.citation_count),
            ("override_count_in_window", self.override_count_in_window),
        ):
            if count < 0:
                raise ConfidenceValidationError(f"{name} must be >= 0")
        if not self.last_transition_reason.strip():
            raise ConfidenceValidationError("last_transition_reason must not be empty")
        validate_emission_tier(self.tier)
        if self.advisory_only and tier_rank(self.tier) > tier_rank(ADVISORY_ONLY_TIER_CAP):
            raise ConfidenceValidationError(
                f"advisory-only nodes are capped at {ADVISORY_ONLY_TIER_CAP.value!r}; "
                f"{self.tier.value!r} is not reachable"
            )
        minimums = TIER_MINIMUM_EVIDENCE[self.tier]
        if self.confirmation_count < minimums.min_confirmations:
            raise ConfidenceValidationError(
                f"{self.tier.value!r} requires >= {minimums.min_confirmations} "
                f"confirmations (K={BLOCKING_CONFIRMATION_COUNT_K} for the "
                f"blocking-eligible tier); got {self.confirmation_count}"
            )
        if self.citation_count < minimums.min_citations:
            raise ConfidenceValidationError(
                f"{self.tier.value!r} requires >= {minimums.min_citations} citations; "
                f"got {self.citation_count}"
            )
        if self.tier is BLOCKING_ELIGIBLE_TIER and self.override_count_in_window > 0:
            raise ConfidenceValidationError(
                f"the blocking-eligible tier requires zero overrides within "
                f"W={BLOCKING_OVERRIDE_WINDOW_DAYS_W} days; got "
                f"{self.override_count_in_window}"
            )

    def as_payload(self) -> dict[str, Any]:
        """Return the JSON-safe payload recorded alongside ledger events."""

        return {
            "advisory_only": self.advisory_only,
            "citation_count": self.citation_count,
            "confirmation_count": self.confirmation_count,
            "last_transition_reason": self.last_transition_reason,
            "override_count_in_window": self.override_count_in_window,
            "tier": self.tier.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ConfidenceState:
        """Rebuild a state from a payload, failing closed on bad fields."""

        try:
            tier = ConfidenceTier(_payload_field(payload, "tier", str))
        except ValueError as exc:
            if isinstance(exc, ConfidenceValidationError):
                raise
            raise ConfidenceValidationError(
                f"confidence state payload is invalid: {exc}"
            ) from exc
        return cls(
            tier=tier,
            confirmation_count=_payload_field(payload, "confirmation_count", int),
            citation_count=_payload_field(payload, "citation_count", int),
            override_count_in_window=_payload_field(
                payload, "override_count_in_window", int
            ),
            last_transition_reason=_payload_field(
                payload, "last_transition_reason", str
            ),
            advisory_only=_payload_field(payload, "advisory_only", bool),
        )


def _payload_field(payload: Mapping[str, Any], key: str, expected: type) -> Any:
    """Return a payload field of the exact expected type, failing closed."""

    if key not in payload:
        raise ConfidenceValidationError(
            f"confidence state payload is invalid: missing {key!r}"
        )
    value = payload[key]
    # bool subclasses int; an int field carrying True/False is a tampered payload.
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ConfidenceValidationError(
            f"confidence state payload is invalid: {key!r} must be {expected.__name__}"
        )
    return value


def apply_tier_transition(
    state: ConfidenceState,
    *,
    to_tier: ConfidenceTier,
    reason: str,
    confirmation_count: int,
    citation_count: int,
    override_count_in_window: int,
) -> ConfidenceState:
    """Apply a tier transition and return the new state.

    Promotion is monotonic: exactly one rung up per transition, and evidence
    counts may not shrink while promoting. Demotion is always allowed, any
    number of rungs, and is loud — the non-empty reason is recorded on the
    returned state for the caller to log and ledger.
    """

    if not reason.strip():
        raise ConfidenceValidationError("a tier transition requires a non-empty reason")
    if to_tier is state.tier:
        raise ConfidenceValidationError(
            f"transition must change the tier; already at {state.tier.value!r}"
        )

    rank_delta = tier_rank(to_tier) - tier_rank(state.tier)
    if rank_delta > 0:
        if rank_delta != 1:
            raise ConfidenceValidationError(
                f"promotion is monotonic: {state.tier.value!r} -> {to_tier.value!r} "
                f"skips a tier; promote one rung at a time"
            )
        if state.advisory_only and tier_rank(to_tier) > tier_rank(ADVISORY_ONLY_TIER_CAP):
            raise ConfidenceValidationError(
                f"advisory-only nodes are never promoted past "
                f"{ADVISORY_ONLY_TIER_CAP.value!r}; clearing advisory-only requires "
                f"a human path, not a tier transition"
            )
        if confirmation_count < state.confirmation_count:
            raise ConfidenceValidationError(
                "promotion cannot shrink confirmation evidence"
            )
        if citation_count < state.citation_count:
            raise ConfidenceValidationError("promotion cannot shrink citation evidence")

    return replace(
        state,
        tier=to_tier,
        confirmation_count=confirmation_count,
        citation_count=citation_count,
        override_count_in_window=override_count_in_window,
        last_transition_reason=reason,
    )
