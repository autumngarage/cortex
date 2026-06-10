"""Route comparison for eval reports (cortex#349).

The master plan's discipline: model routes are rented and interchangeable;
what makes a route choice defensible is *measured* precision, latency, and
cost side by side — never vibes, never a silent auto-pick. This module
assembles per-route rows from the boundary's own accounting (cost
``RunLedger`` summaries, ``quality_series`` points, ``CostRecord``
latencies) and renders a deterministic comparison the Stage 0 gate report
(#337/#343) embeds.

The report **ranks and shows; humans decide.** A route is listed in
``dominating_routes`` only when it is strictly no-worse on precision and
cost than every comparable peer and strictly better than at least one —
and even then the report carries the evidence, not a verdict. Missing
measurements stay visible as None + reason (the same never-a-silent-zero
rule as everywhere else); incomparable routes are reported, not papered
over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from cortex.hosted.quality_series import QualitySeriesPoint

ROUTE_COMPARISON_VERSION = 1


class RouteComparisonValidationError(ValueError):
    """Raised when comparison material cannot support a gate-grade report."""


@dataclass(frozen=True)
class RouteRow:
    """One route's measured run: identity + cost + latency + quality."""

    model_id: str
    prompt_version: str
    call_count: int
    failed_call_count: int
    known_usd_total: float | None
    usd_unavailable_reason: str | None
    median_wall_ms: int | None
    wall_ms_unavailable_reason: str | None
    quality: QualitySeriesPoint

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise RouteComparisonValidationError("model_id must not be empty")
        if not self.prompt_version.strip():
            raise RouteComparisonValidationError("prompt_version must not be empty")
        for name in ("call_count", "failed_call_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RouteComparisonValidationError(f"{name} must be a non-negative int")
        if self.failed_call_count > self.call_count:
            raise RouteComparisonValidationError(
                "failed_call_count cannot exceed call_count"
            )
        for value_name, reason_name in (
            ("known_usd_total", "usd_unavailable_reason"),
            ("median_wall_ms", "wall_ms_unavailable_reason"),
        ):
            value = getattr(self, value_name)
            reason = getattr(self, reason_name)
            if (value is None) == (reason is None):
                raise RouteComparisonValidationError(
                    f"exactly one of {value_name} / {reason_name} must be set"
                )

    @property
    def route_key(self) -> str:
        return f"{self.model_id}+{self.prompt_version}"

    def as_payload(self) -> dict[str, object]:
        return {
            "call_count": self.call_count,
            "failed_call_count": self.failed_call_count,
            "known_usd_total": self.known_usd_total,
            "median_wall_ms": self.median_wall_ms,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "quality": self.quality.as_payload(),
            "route_key": self.route_key,
            "usd_unavailable_reason": self.usd_unavailable_reason,
            "wall_ms_unavailable_reason": self.wall_ms_unavailable_reason,
        }


def median_wall_ms(wall_ms_values: tuple[int, ...]) -> int | None:
    """Median latency over a route's calls; None for an empty series."""

    if not wall_ms_values:
        return None
    ordered = sorted(wall_ms_values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def _comparable(a: RouteRow, b: RouteRow) -> bool:
    return (
        a.quality.false_positive_rate is not None
        and b.quality.false_positive_rate is not None
        and a.known_usd_total is not None
        and b.known_usd_total is not None
    )


def _dominates(candidate: RouteRow, other: RouteRow) -> bool:
    """Strict no-worse on FP rate and cost, strictly better on >= one.

    Only defined over comparable pairs; an unmeasured route can never
    dominate or be dominated.
    """

    if not _comparable(candidate, other):
        return False
    a_fp = candidate.quality.false_positive_rate
    b_fp = other.quality.false_positive_rate
    a_usd = candidate.known_usd_total
    b_usd = other.known_usd_total
    assert a_fp is not None and b_fp is not None
    assert a_usd is not None and b_usd is not None
    no_worse = a_fp <= b_fp and a_usd <= b_usd
    strictly_better = a_fp < b_fp or a_usd < b_usd
    return no_worse and strictly_better


@dataclass(frozen=True)
class RouteComparisonReport:
    """Deterministically ordered comparison across routes."""

    rows: tuple[RouteRow, ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise RouteComparisonValidationError("a comparison needs at least one route")
        keys = [row.route_key for row in self.rows]
        if len(set(keys)) != len(keys):
            raise RouteComparisonValidationError("duplicate route_key in comparison")
        object.__setattr__(
            self, "rows", tuple(sorted(self.rows, key=lambda row: row.route_key))
        )

    @property
    def dominating_routes(self) -> tuple[str, ...]:
        """Routes that dominate at least one peer and are dominated by none."""

        winners = []
        for row in self.rows:
            others = [other for other in self.rows if other.route_key != row.route_key]
            if not others:
                continue
            dominated_by_any = any(_dominates(other, row) for other in others)
            dominates_some = any(_dominates(row, other) for other in others)
            if dominates_some and not dominated_by_any:
                winners.append(row.route_key)
        return tuple(winners)

    @property
    def incomparable_pairs(self) -> tuple[tuple[str, str], ...]:
        """Route pairs missing measurements — visible, never papered over."""

        pairs = []
        for i, row in enumerate(self.rows):
            for other in self.rows[i + 1 :]:
                if not _comparable(row, other):
                    pairs.append((row.route_key, other.route_key))
        return tuple(pairs)

    def as_payload(self) -> dict[str, object]:
        return {
            "dominating_routes": list(self.dominating_routes),
            "incomparable_pairs": [list(pair) for pair in self.incomparable_pairs],
            "note": "ranks and evidence only; route selection is a human decision",
            "rows": [row.as_payload() for row in self.rows],
            "version": ROUTE_COMPARISON_VERSION,
        }

    def to_canonical_json(self) -> str:
        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n"
        )
