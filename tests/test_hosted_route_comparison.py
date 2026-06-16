"""Tests for route comparison reports (cortex#349)."""

from __future__ import annotations

import pytest

from cortex.hosted.quality_series import QualitySeriesPoint
from cortex.hosted.route_comparison import (
    RouteComparisonReport,
    RouteComparisonValidationError,
    RouteRow,
    median_wall_ms,
)


def _quality(fp: float | None, graded: int = 10) -> QualitySeriesPoint:
    if fp is None:
        return QualitySeriesPoint(
            graded_emitted_count=0, incorrect_precision_count=0,
            tone_flagged_count=0, missed_expected_count=0, override_context_count=0,
            false_positive_rate=None,
            false_positive_rate_unavailable_reason="ungraded route",
            tone_rate=None, tone_rate_unavailable_reason="ungraded route",
        )
    incorrect = round(fp * graded)
    return QualitySeriesPoint(
        graded_emitted_count=graded, incorrect_precision_count=incorrect,
        tone_flagged_count=0, missed_expected_count=0, override_context_count=0,
        false_positive_rate=incorrect / graded,
        false_positive_rate_unavailable_reason=None,
        tone_rate=0.0, tone_rate_unavailable_reason=None,
    )


def _row(
    model: str,
    *,
    fp: float | None = 0.2,
    usd: float | None = 1.0,
    wall: int | None = 900,
) -> RouteRow:
    return RouteRow(
        model_id=model,
        prompt_version="evaluate-contradiction/v1+aaaaaaaaaaaa",
        call_count=10,
        failed_call_count=0,
        known_usd_total=usd,
        usd_unavailable_reason=None if usd is not None else "unreported tokens",
        median_wall_ms=wall,
        wall_ms_unavailable_reason=None if wall is not None else "no timing",
        quality=_quality(fp),
    )


def test_rows_sort_deterministically_by_route_key() -> None:
    report = RouteComparisonReport(rows=(_row("z/last"), _row("a/first")))
    assert [row.model_id for row in report.rows] == ["a/first", "z/last"]
    assert report.to_canonical_json() == RouteComparisonReport(
        rows=(_row("a/first"), _row("z/last"))
    ).to_canonical_json()


def test_dominance_requires_no_worse_on_both_and_better_on_one() -> None:
    cheap_precise = _row("a/cheap", fp=0.1, usd=0.5)
    pricey_sloppy = _row("b/pricey", fp=0.3, usd=2.0)
    report = RouteComparisonReport(rows=(cheap_precise, pricey_sloppy))
    assert report.dominating_routes == (cheap_precise.route_key,)


def test_tradeoff_pairs_produce_no_dominator() -> None:
    precise_pricey = _row("a/precise", fp=0.1, usd=2.0)
    cheap_sloppy = _row("b/cheap", fp=0.3, usd=0.5)
    report = RouteComparisonReport(rows=(precise_pricey, cheap_sloppy))
    assert report.dominating_routes == ()


def test_unmeasured_routes_are_incomparable_not_dominated() -> None:
    measured = _row("a/measured", fp=0.1, usd=0.5)
    ungraded = _row("b/ungraded", fp=None, usd=1.0)
    report = RouteComparisonReport(rows=(measured, ungraded))
    assert report.dominating_routes == ()
    assert report.incomparable_pairs == ((measured.route_key, ungraded.route_key),)


def test_missing_measurement_needs_reason() -> None:
    with pytest.raises(RouteComparisonValidationError, match="exactly one"):
        _row("a/bad", usd=None).__class__(
            model_id="a/bad",
            prompt_version="p/v1+aaaaaaaaaaaa",
            call_count=1,
            failed_call_count=0,
            known_usd_total=None,
            usd_unavailable_reason=None,
            median_wall_ms=1,
            wall_ms_unavailable_reason=None,
            quality=_quality(0.1),
        )


def test_duplicate_routes_rejected() -> None:
    with pytest.raises(RouteComparisonValidationError, match="duplicate"):
        RouteComparisonReport(rows=(_row("a/same"), _row("a/same")))


def test_median_wall_ms() -> None:
    assert median_wall_ms(()) is None
    assert median_wall_ms((5,)) == 5
    assert median_wall_ms((1, 9)) == 5
    assert median_wall_ms((1, 3, 9)) == 3


def test_failed_calls_bounded_by_call_count() -> None:
    with pytest.raises(RouteComparisonValidationError, match="cannot exceed"):
        RouteRow(
            model_id="a/x",
            prompt_version="p/v1+aaaaaaaaaaaa",
            call_count=1,
            failed_call_count=2,
            known_usd_total=1.0,
            usd_unavailable_reason=None,
            median_wall_ms=1,
            wall_ms_unavailable_reason=None,
            quality=_quality(0.1),
        )
