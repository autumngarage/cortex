"""Separated quality series: precision vs tone/preference (cortex#342).

Stage 0 wins or dies on one discipline the master plan names explicitly:
**never let tone/preference feedback move the precision gate** — conflating
them trains the system toward silence. This module makes the separation
structural: the two series derive from *disjoint* `LabelClass` partitions
fixed at import time, so no post-hoc filtering convention (or future
refactor) can leak one into the other.

The partitions:

- ``PRECISION_SERIES_CLASSES`` — labels that speak to factual correctness:
  ``correct_useful``, ``correct_not_useful`` (numerator side) and
  ``incorrect_precision`` (the failure). ``missed_expected`` is recall
  material and belongs to neither rate series (reported separately).
- ``TONE_SERIES_CLASSES`` — labels that speak to usefulness only:
  ``correct_not_useful`` is the Stage 0 tone/preference signal (the
  finding was *right* but a reviewer wouldn't act on it).

Stage 2 (cortex#380) extends this taxonomy with live-override context
classes (changed-decision, emergency-exception) on top of the same
partition rule; it must import these partitions rather than minting new
ones — that requirement is the third acceptance criterion of #342 and is
asserted in the partition-disjointness test.
"""

from __future__ import annotations

from dataclasses import dataclass

from cortex.hosted.eval_fixtures import EvalFixture, LabelClass

PRECISION_FAILURE_CLASSES = frozenset({LabelClass.INCORRECT_PRECISION})
PRECISION_CORRECT_CLASSES = frozenset(
    {LabelClass.CORRECT_USEFUL, LabelClass.CORRECT_NOT_USEFUL}
)
PRECISION_SERIES_CLASSES = PRECISION_CORRECT_CLASSES | PRECISION_FAILURE_CLASSES

TONE_SERIES_CLASSES = frozenset({LabelClass.CORRECT_NOT_USEFUL})

RECALL_CLASSES = frozenset({LabelClass.MISSED_EXPECTED})


class QualitySeriesValidationError(ValueError):
    """Raised when series material cannot support gate-grade reporting."""


@dataclass(frozen=True)
class QualitySeriesPoint:
    """One run's separated quality measurements."""

    graded_emitted_count: int
    incorrect_precision_count: int
    tone_flagged_count: int
    missed_expected_count: int
    false_positive_rate: float | None
    false_positive_rate_unavailable_reason: str | None
    tone_rate: float | None
    tone_rate_unavailable_reason: str | None

    def __post_init__(self) -> None:
        for name in (
            "graded_emitted_count",
            "incorrect_precision_count",
            "tone_flagged_count",
            "missed_expected_count",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise QualitySeriesValidationError(f"{name} must be a non-negative int")
        for rate_name, reason_name in (
            ("false_positive_rate", "false_positive_rate_unavailable_reason"),
            ("tone_rate", "tone_rate_unavailable_reason"),
        ):
            rate = getattr(self, rate_name)
            reason = getattr(self, reason_name)
            if (rate is None) == (reason is None):
                raise QualitySeriesValidationError(
                    f"exactly one of {rate_name} / {reason_name} must be set — "
                    "a missing rate is never a silent 0.0"
                )
            if rate is not None and not 0.0 <= rate <= 1.0:
                raise QualitySeriesValidationError(f"{rate_name} must be within [0, 1]")

    def as_payload(self) -> dict[str, object]:
        return {
            "false_positive_rate": self.false_positive_rate,
            "false_positive_rate_unavailable_reason": self.false_positive_rate_unavailable_reason,
            "graded_emitted_count": self.graded_emitted_count,
            "incorrect_precision_count": self.incorrect_precision_count,
            "missed_expected_count": self.missed_expected_count,
            "tone_flagged_count": self.tone_flagged_count,
            "tone_rate": self.tone_rate,
            "tone_rate_unavailable_reason": self.tone_rate_unavailable_reason,
        }


def quality_series_point(fixtures: tuple[EvalFixture, ...]) -> QualitySeriesPoint:
    """Compute the separated series over graded fixtures.

    FP rate = incorrect_precision / all precision-series labels.
    Tone rate = correct_not_useful / all precision-series labels.
    The denominators are identical *by construction* (both series measure
    graded emitted findings); the numerators come from disjoint class sets,
    so a tone-class label can never move the FP numerator and vice versa.
    """

    fixture_ids = [fixture.fixture_id for fixture in fixtures]
    if len(set(fixture_ids)) != len(fixture_ids):
        raise QualitySeriesValidationError("duplicate fixture_id in series input")

    precision_failures = 0
    precision_correct = 0
    tone_flagged = 0
    missed = 0
    for fixture in fixtures:
        for label in fixture.labels:
            if label.label in PRECISION_FAILURE_CLASSES:
                precision_failures += 1
            elif label.label in PRECISION_CORRECT_CLASSES:
                precision_correct += 1
                if label.label in TONE_SERIES_CLASSES:
                    tone_flagged += 1
            elif label.label in RECALL_CLASSES:
                missed += 1

    graded = precision_failures + precision_correct
    if graded == 0:
        return QualitySeriesPoint(
            graded_emitted_count=0,
            incorrect_precision_count=0,
            tone_flagged_count=tone_flagged,
            missed_expected_count=missed,
            false_positive_rate=None,
            false_positive_rate_unavailable_reason="no graded emitted findings in input",
            tone_rate=None,
            tone_rate_unavailable_reason="no graded emitted findings in input",
        )
    return QualitySeriesPoint(
        graded_emitted_count=graded,
        incorrect_precision_count=precision_failures,
        tone_flagged_count=tone_flagged,
        missed_expected_count=missed,
        false_positive_rate=precision_failures / graded,
        false_positive_rate_unavailable_reason=None,
        tone_rate=tone_flagged / graded,
        tone_rate_unavailable_reason=None,
    )
