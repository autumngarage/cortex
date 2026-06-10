"""Non-interactive hand-labeling workflow over the frozen eval-fixture format.

This module owns the workflow that produces hand labels in the cortex#332
taxonomy (cortex#333). It never defines label classes — ``eval_fixtures.py``
owns ``LabelClass`` — and it never mutates fixtures: every labeling operation
returns a new ``EvalFixture`` whose ``diffs`` / ``decisions`` /
``expected_findings`` payload sections are byte-identical to the input. Write
back via ``EvalFixture.to_canonical_json()`` so labeled fixtures stay
byte-stable and reload with zero manual fixups.

Workflow contract:

1. ``load_unlabeled_findings`` lists the expected findings still awaiting a
   hand grade.
2. ``apply_label`` appends one ``FixtureLabel`` with full provenance (grader,
   graded_at, the finding whose ``cited_span_hashes`` tie the judgment to
   source spans). Missing provenance is rejected visibly, never defaulted.
3. Inter-rater spot-check: a second grader labels the same finding by
   appending a second label. ``spot_check_sample_size`` names the sample bar
   from cortex#333 (at least 10 items or 10% of the batch, whichever is
   larger). ``disagreement_report`` records agreement and surfaces
   disagreements; a disagreement is resolved by appending a label under a
   distinct resolver identity with a ``note`` explaining the resolution —
   existing labels are append-only, never edited.
4. ``label_tally`` aggregates counts per ``LabelClass`` plus the two derived
   metrics the master plan names: ``precision_correct`` and ``useful_rate``.
   ``missed_expected`` is a recall signal counted separately and excluded
   from both denominators. Zero denominators yield ``None`` with a visible
   reason — never a silent ``0.0``.

Model output is never ground truth; labels come from human graders.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cortex.hosted.eval_fixtures import (
    EvalFixture,
    ExpectedFinding,
    FixtureLabel,
    LabelClass,
)

SPOT_CHECK_MINIMUM_ITEMS = 10
# 10% of the labeling batch, expressed as a denominator so the ceiling
# division below stays exact integer math (cortex#333 acceptance criterion 2).
SPOT_CHECK_FRACTION_DENOMINATOR = 10

# The labels attached to findings the evaluator actually emitted. These form
# the denominator of both derived metrics; MISSED_EXPECTED is a false
# negative the evaluator never emitted, so it is a recall signal counted
# separately and excluded here.
GRADED_EMITTED_CLASSES = (
    LabelClass.CORRECT_USEFUL,
    LabelClass.CORRECT_NOT_USEFUL,
    LabelClass.INCORRECT_PRECISION,
)


class LabelingError(ValueError):
    """Raised when a labeling operation would corrupt grading ground truth."""


def load_unlabeled_findings(fixture: EvalFixture) -> tuple[ExpectedFinding, ...]:
    """Return the expected findings that no grader has labeled yet, in fixture order."""

    labeled_ids = {label.finding_id for label in fixture.labels}
    return tuple(
        finding
        for finding in fixture.expected_findings
        if finding.finding_id not in labeled_ids
    )


def apply_label(
    fixture: EvalFixture,
    finding_id: str,
    label_class: LabelClass | str,
    grader: str,
    graded_at: str,
    note: str | None = None,
) -> EvalFixture:
    """Return a new ``EvalFixture`` with one label appended; never mutates the input.

    Provenance is mandatory: ``grader`` and ``graded_at`` must be non-empty,
    and ``finding_id`` must name an expected finding in this fixture (whose
    ``cited_span_hashes`` anchor the judgment to source spans). A grader may
    label a given finding once; a second grader appends a second label
    (inter-rater review), and resolution appends under a distinct resolver
    identity with a ``note`` — labels are append-only.
    """

    _require_non_empty("finding_id", finding_id)
    _require_non_empty("grader", grader)
    _require_non_empty("graded_at", graded_at)
    if note is not None:
        _require_non_empty("note", note)

    try:
        resolved_class = LabelClass(label_class)
    except ValueError as exc:
        raise LabelingError(
            f"unknown label class {label_class!r}; the cortex#332 taxonomy is fixed: "
            f"{sorted(item.value for item in LabelClass)}"
        ) from exc

    known_findings = {finding.finding_id for finding in fixture.expected_findings}
    if finding_id not in known_findings:
        raise LabelingError(
            f"finding {finding_id!r} does not exist in fixture {fixture.fixture_id!r}; "
            f"labels attach only to expected findings (known: {sorted(known_findings)})"
        )

    for existing in fixture.labels:
        if existing.finding_id == finding_id and existing.grader == grader:
            raise LabelingError(
                f"grader {grader!r} already labeled finding {finding_id!r} in fixture "
                f"{fixture.fixture_id!r} ({existing.label.value} at {existing.graded_at}); "
                "labels are append-only — inter-rater review or resolution appends "
                "under a distinct grader identity"
            )

    label = FixtureLabel(
        finding_id=finding_id,
        label=resolved_class,
        grader=grader,
        graded_at=graded_at,
        note=note,
    )
    return EvalFixture(
        fixture_id=fixture.fixture_id,
        diff=fixture.diff,
        decisions=fixture.decisions,
        expected_findings=fixture.expected_findings,
        labels=(*fixture.labels, label),
        fixture_schema_version=fixture.fixture_schema_version,
        metadata=fixture.metadata,
    )


def spot_check_sample_size(batch_size: int) -> int:
    """The cortex#333 inter-rater sample: max(10 items, 10% of the batch), capped at the batch.

    Batches smaller than the 10-item floor are double-labeled in full — the
    spot check can never demand more items than the batch contains.
    """

    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
        raise LabelingError("batch_size must be an integer count of labeled findings")
    if batch_size <= 0:
        raise LabelingError(
            "spot-check sample is undefined for an empty labeling batch; "
            "label findings before sizing the inter-rater sample"
        )
    fraction = -(-batch_size // SPOT_CHECK_FRACTION_DENOMINATOR)
    return min(batch_size, max(SPOT_CHECK_MINIMUM_ITEMS, fraction))


@dataclass(frozen=True)
class GraderDisagreement:
    """All labels for one finding graded by two or more distinct graders."""

    fixture_id: str
    finding_id: str
    labels: tuple[FixtureLabel, ...]

    def __post_init__(self) -> None:
        _require_non_empty("fixture_id", self.fixture_id)
        _require_non_empty("finding_id", self.finding_id)
        if len(self.labels) < 2:
            raise LabelingError(
                "a double-labeled entry requires at least two labels; got "
                f"{len(self.labels)} for finding {self.finding_id!r}"
            )
        for label in self.labels:
            if label.finding_id != self.finding_id:
                raise LabelingError(
                    f"label for finding {label.finding_id!r} grouped under "
                    f"{self.finding_id!r}; entries must be single-finding"
                )
        if len({label.grader for label in self.labels}) < 2:
            raise LabelingError(
                f"finding {self.finding_id!r} needs labels from at least two distinct "
                "graders to count as inter-rater double-labeling"
            )

    @property
    def graders(self) -> tuple[str, ...]:
        ordered: list[str] = []
        for label in self.labels:
            if label.grader not in ordered:
                ordered.append(label.grader)
        return tuple(ordered)

    @property
    def distinct_label_values(self) -> tuple[str, ...]:
        return tuple(sorted({label.label.value for label in self.labels}))

    @property
    def agreed(self) -> bool:
        return len(self.distinct_label_values) == 1

    @property
    def resolution_recorded(self) -> bool:
        """True when any label carries a note — the cortex#333 resolution record."""

        return any(label.note is not None for label in self.labels)


@dataclass(frozen=True)
class DisagreementReport:
    """Per-finding inter-rater listing for the cortex#333 spot-check requirement.

    Every count and rate is derived from ``entries`` on read — nothing is
    persisted that could drift from the underlying labels.
    """

    entries: tuple[GraderDisagreement, ...]

    @property
    def double_labeled_finding_count(self) -> int:
        return len(self.entries)

    @property
    def disagreeing_finding_count(self) -> int:
        return sum(1 for entry in self.entries if not entry.agreed)

    @property
    def unresolved_disagreements(self) -> tuple[GraderDisagreement, ...]:
        """Disagreements with no resolution note recorded — must be empty before the gate."""

        return tuple(
            entry
            for entry in self.entries
            if not entry.agreed and not entry.resolution_recorded
        )

    @property
    def agreement_rate(self) -> float | None:
        if not self.entries:
            return None
        agreed = self.double_labeled_finding_count - self.disagreeing_finding_count
        return agreed / self.double_labeled_finding_count

    @property
    def agreement_rate_unavailable_reason(self) -> str | None:
        if self.entries:
            return None
        return (
            "agreement_rate is undefined: no finding carries labels from two or more "
            "graders, so the inter-rater spot-check has not been recorded "
            "(reported as None, never a silent 0.0)"
        )


def disagreement_report(fixtures: Iterable[EvalFixture]) -> DisagreementReport:
    """Build the per-finding grader disagreement listing across a fixture batch."""

    entries: list[GraderDisagreement] = []
    for fixture in _materialized_unique_fixtures(fixtures):
        labels_by_finding: dict[str, list[FixtureLabel]] = {}
        for label in fixture.labels:
            labels_by_finding.setdefault(label.finding_id, []).append(label)
        for finding in fixture.expected_findings:
            labels = labels_by_finding.get(finding.finding_id, [])
            if len({label.grader for label in labels}) >= 2:
                entries.append(
                    GraderDisagreement(
                        fixture_id=fixture.fixture_id,
                        finding_id=finding.finding_id,
                        labels=tuple(labels),
                    )
                )
    return DisagreementReport(entries=tuple(entries))


@dataclass(frozen=True)
class LabelTally:
    """Counts per ``LabelClass`` plus the two derived master-plan metrics.

    ``counts`` is the only stored state; both metrics and every intermediate
    count derive from it on read. ``missed_expected`` is the recall signal:
    it is counted, surfaced via ``missed_expected_count``, and excluded from
    both metric denominators because a missed finding was never an emitted
    advisory comment.
    """

    counts: Mapping[LabelClass, int]

    def __post_init__(self) -> None:
        if set(self.counts.keys()) != set(LabelClass):
            raise LabelingError(
                "tally counts must cover exactly the cortex#332 label classes "
                f"{sorted(item.value for item in LabelClass)}; got "
                f"{sorted(key.value for key in self.counts if isinstance(key, LabelClass))}"
            )
        for label_class, count in self.counts.items():
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise LabelingError(
                    f"count for {label_class.value} must be a non-negative integer; "
                    f"got {count!r}"
                )
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))

    @property
    def correct_count(self) -> int:
        """correct = correct_useful + correct_not_useful (master-plan definition)."""

        return (
            self.counts[LabelClass.CORRECT_USEFUL]
            + self.counts[LabelClass.CORRECT_NOT_USEFUL]
        )

    @property
    def graded_emitted_count(self) -> int:
        """The master plan's ``all_graded`` denominator: every graded emitted finding.

        Excludes ``missed_expected`` — those findings were never emitted, so
        they belong to recall (``missed_expected_count``), not to the
        precision or usefulness denominators.
        """

        return sum(self.counts[label_class] for label_class in GRADED_EMITTED_CLASSES)

    @property
    def missed_expected_count(self) -> int:
        """Recall signal, reported separately from the two derived metrics."""

        return self.counts[LabelClass.MISSED_EXPECTED]

    @property
    def precision_correct(self) -> float | None:
        """precision_correct = correct / (correct + incorrect_precision), or None."""

        if self.graded_emitted_count == 0:
            return None
        return self.correct_count / self.graded_emitted_count

    @property
    def precision_correct_unavailable_reason(self) -> str | None:
        if self.graded_emitted_count != 0:
            return None
        return (
            "precision_correct is undefined: no graded emitted findings "
            "(correct_useful + correct_not_useful + incorrect_precision == 0); "
            "reported as None, never a silent 0.0"
        )

    @property
    def useful_rate(self) -> float | None:
        """useful_rate = correct_useful / all_graded, or None when nothing is graded."""

        if self.graded_emitted_count == 0:
            return None
        return self.counts[LabelClass.CORRECT_USEFUL] / self.graded_emitted_count

    @property
    def useful_rate_unavailable_reason(self) -> str | None:
        if self.graded_emitted_count != 0:
            return None
        return (
            "useful_rate is undefined: no graded emitted findings "
            "(correct_useful + correct_not_useful + incorrect_precision == 0); "
            "reported as None, never a silent 0.0"
        )


def label_tally(fixtures: Iterable[EvalFixture]) -> LabelTally:
    """Aggregate label counts per ``LabelClass`` across a fixture batch."""

    counts: dict[LabelClass, int] = dict.fromkeys(LabelClass, 0)
    for fixture in _materialized_unique_fixtures(fixtures):
        for label in fixture.labels:
            counts[label.label] += 1
    return LabelTally(counts=counts)


def _materialized_unique_fixtures(
    fixtures: Iterable[EvalFixture],
) -> tuple[EvalFixture, ...]:
    materialized = tuple(fixtures)
    seen: set[str] = set()
    for fixture in materialized:
        if fixture.fixture_id in seen:
            raise LabelingError(
                f"duplicate fixture_id {fixture.fixture_id!r} in the labeling batch; "
                "aggregating duplicates would double-count labels"
            )
        seen.add(fixture.fixture_id)
    return materialized


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise LabelingError(
            f"{name} must be a non-empty string; label provenance is mandatory "
            "(who graded, when, and which finding)"
        )
