"""Candidate-set quality metrics for the Stage 0 eval harness (cortex#341).

Diff-scoped candidate gating is the load-bearing technical bet. Retrieval
itself shipped as substrate (``decisions_for_diff.py``, PR #481: hybrid RRF,
result cap ``MAX_DECISIONS_FOR_DIFF_LIMIT``, ``omitted_counts``); this
module defines how candidate-set quality is *measured* against labeled
relevant-decision sets. It does not rebuild retrieval. The substrate is
non-executing (SQL strings only), so metric computation runs in the Stage 0
local harness over materialized candidate packs, not against hosted
Postgres (that path lands with cortex#472, Stage 1).

Formulas (the committed definitions, verbatim):

- ``recall_at_k = |relevant ∩ candidates| / |relevant|`` where
  ``candidates`` is the top-K slice of the pack's score ordering. Undefined
  when ``|relevant| = 0``: reported as ``None`` plus an explicit reason,
  never a silent ``0.0``.
- ``precision_at_k = |relevant ∩ retrieved@K| / K``.
- ``reciprocal_rank = 1 / rank of first relevant result`` within the top-K
  ordering; ``0.0`` when relevant decisions exist but none appear in the
  top K. The batch aggregate is ``MRR = mean(1 / rank of first relevant
  result)`` over the fixtures where it is defined.
- ``relevant_present`` — THE master-plan gate signal: 'the relevant
  decision is present in the bounded candidate set'.
- ``omitted_relevant_count`` — relevant ids appearing in NO candidate — the
  silent-failure detector.

Metrics are reported at K = ``MAX_DECISIONS_FOR_DIFF_LIMIT`` (the shipped
retrieval cap) and at least one smaller K (``DEFAULT_REPORT_KS``) so future
cap changes are measured rather than assumed. Every metrics row carries the
pack's ``omitted_counts`` so a high precision number cannot silently mask
truncation.

Wire-in: cortex#337's Stage 0 gate report consumes these metrics via the
stage0-gate-report template's Candidate-set quality section.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cortex.hosted.decisions_for_diff import (
    MAX_DECISIONS_FOR_DIFF_LIMIT,
    DecisionsForDiffCandidatePack,
)

# The shipped retrieval cap plus one smaller probe K, so a regression that
# only shows up below the cap is measured rather than assumed away.
DEFAULT_REPORT_KS: tuple[int, ...] = (5, MAX_DECISIONS_FOR_DIFF_LIMIT)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class CandidateMetricsValidationError(ValueError):
    """Raised when metric inputs or rows cannot support honest measurement."""


@dataclass(frozen=True)
class CandidateSetMetrics:
    """Frozen quality metrics for one candidate pack against one labeled set.

    ``recall_at_k``, ``precision_at_k``, and ``reciprocal_rank`` are ``None``
    exactly when ``unavailable_reason`` is set (``|relevant| = 0``); a silent
    ``0.0`` from an empty label set is unrepresentable.
    """

    query_hash: str
    candidate_set_hash: str
    k: int
    candidates_in_budget: int
    relevant_decision_ids: tuple[str, ...]
    present_relevant_ids: tuple[str, ...]
    omitted_relevant_ids: tuple[str, ...]
    pack_omitted_counts: Mapping[str, int]
    recall_at_k: float | None
    precision_at_k: float | None
    reciprocal_rank: float | None
    unavailable_reason: str | None

    def __post_init__(self) -> None:
        _validate_hash("query_hash", self.query_hash)
        _validate_hash("candidate_set_hash", self.candidate_set_hash)
        if self.k < 1:
            raise CandidateMetricsValidationError("k must be >= 1")
        if self.candidates_in_budget < 0:
            raise CandidateMetricsValidationError("candidates_in_budget must be >= 0")
        _validate_sorted_unique_ids("relevant_decision_ids", self.relevant_decision_ids)
        _validate_sorted_unique_ids("present_relevant_ids", self.present_relevant_ids)
        _validate_sorted_unique_ids("omitted_relevant_ids", self.omitted_relevant_ids)
        relevant = set(self.relevant_decision_ids)
        present = set(self.present_relevant_ids)
        omitted = set(self.omitted_relevant_ids)
        if not present <= relevant or not omitted <= relevant:
            raise CandidateMetricsValidationError(
                "present and omitted ids must be subsets of the relevant set"
            )
        if present & omitted:
            raise CandidateMetricsValidationError(
                "an id present in the top-K cannot also be omitted from the pack"
            )
        if len(present) > min(self.k, self.candidates_in_budget):
            raise CandidateMetricsValidationError(
                "present ids cannot exceed the considered candidate count"
            )
        _validate_omitted_counts(self.pack_omitted_counts)
        object.__setattr__(
            self, "pack_omitted_counts", MappingProxyType(dict(self.pack_omitted_counts))
        )

        metric_values = (self.recall_at_k, self.precision_at_k, self.reciprocal_rank)
        if not self.relevant_decision_ids:
            if self.unavailable_reason is None or not self.unavailable_reason.strip():
                raise CandidateMetricsValidationError(
                    "an empty relevant set requires an explicit unavailable_reason; "
                    "silent 0.0 metrics are unrepresentable"
                )
            if any(value is not None for value in metric_values):
                raise CandidateMetricsValidationError(
                    "metrics must be None when the relevant set is empty"
                )
            return
        if self.unavailable_reason is not None:
            raise CandidateMetricsValidationError(
                "unavailable_reason is only valid when the relevant set is empty"
            )
        recall, precision, reciprocal_rank = metric_values
        if recall is None or precision is None or reciprocal_rank is None:
            raise CandidateMetricsValidationError(
                "metrics must be present when the relevant set is non-empty"
            )
        if recall != len(present) / len(relevant):
            raise CandidateMetricsValidationError(
                "recall_at_k must equal |relevant ∩ candidates| / |relevant|"
            )
        if precision != len(present) / self.k:
            raise CandidateMetricsValidationError(
                "precision_at_k must equal |relevant ∩ retrieved@K| / K"
            )
        if not 0.0 <= reciprocal_rank <= 1.0:
            raise CandidateMetricsValidationError("reciprocal_rank must be within [0, 1]")
        if (reciprocal_rank == 0.0) != (not present):
            raise CandidateMetricsValidationError(
                "reciprocal_rank is 0.0 exactly when no relevant id is in the top-K"
            )

    @property
    def relevant_present(self) -> bool:
        """THE master-plan gate signal: 'the relevant decision is present in
        the bounded candidate set'.

        True when at least one labeled relevant decision appears anywhere in
        the bounded pack (not just the top-K slice). With multiple labeled
        decisions this is at-least-one presence; ``omitted_relevant_count``
        names how many are missing entirely.
        """

        return bool(self.relevant_decision_ids) and len(self.omitted_relevant_ids) < len(
            self.relevant_decision_ids
        )

    @property
    def omitted_relevant_count(self) -> int:
        """Relevant ids appearing in NO candidate — the silent-failure detector."""

        return len(self.omitted_relevant_ids)


def compute_candidate_set_metrics(
    *,
    pack: DecisionsForDiffCandidatePack,
    relevant_decision_ids: Iterable[str],
    k: int = MAX_DECISIONS_FOR_DIFF_LIMIT,
) -> CandidateSetMetrics:
    """Compute frozen candidate-set quality metrics for one labeled fixture.

    ``relevant_decision_ids`` is the known-relevant set from a labeled
    fixture; candidate identity is ``decision_node_id``. ``k`` slices the
    pack's score ordering; the default is the shipped retrieval cap.
    """

    if k < 1:
        raise CandidateMetricsValidationError("k must be >= 1")
    relevant = _normalized_relevant_ids(relevant_decision_ids)
    ordered_candidate_ids = [candidate.decision_node_id for candidate in pack.candidates]
    top_k_ids = ordered_candidate_ids[:k]
    present = tuple(sorted(set(relevant) & set(top_k_ids)))
    omitted = tuple(sorted(set(relevant) - set(ordered_candidate_ids)))

    recall: float | None = None
    precision: float | None = None
    reciprocal_rank: float | None = None
    reason: str | None = None
    if relevant:
        recall = len(present) / len(relevant)
        precision = len(present) / k
        reciprocal_rank = 0.0
        relevant_set = set(relevant)
        for rank, candidate_id in enumerate(top_k_ids, start=1):
            if candidate_id in relevant_set:
                reciprocal_rank = 1.0 / rank
                break
    else:
        reason = (
            f"|relevant| = 0 for this pack; recall@{k}, precision@{k}, and "
            "reciprocal rank are undefined — refusing to report a silent 0.0"
        )

    return CandidateSetMetrics(
        query_hash=pack.query_hash,
        candidate_set_hash=pack.candidate_set_hash,
        k=k,
        candidates_in_budget=len(pack.candidates),
        relevant_decision_ids=relevant,
        present_relevant_ids=present,
        omitted_relevant_ids=omitted,
        pack_omitted_counts=dict(pack.omitted_counts),
        recall_at_k=recall,
        precision_at_k=precision,
        reciprocal_rank=reciprocal_rank,
        unavailable_reason=reason,
    )


@dataclass(frozen=True)
class LabeledCandidatePack:
    """One fixture's candidate pack plus its labeled relevant-decision set."""

    fixture_id: str
    pack: DecisionsForDiffCandidatePack
    relevant_decision_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id.strip():
            raise CandidateMetricsValidationError("fixture_id must be a non-empty string")


@dataclass(frozen=True)
class FixtureCandidateSetRow:
    """Per-fixture metrics row inside a batch report."""

    fixture_id: str
    metrics: CandidateSetMetrics

    def __post_init__(self) -> None:
        if not isinstance(self.fixture_id, str) or not self.fixture_id.strip():
            raise CandidateMetricsValidationError("fixture_id must be a non-empty string")


@dataclass(frozen=True)
class CandidateSetBatchReport:
    """Batch metrics over many fixtures: per-fixture rows + aggregates.

    ``presence_rate = |fixtures where relevant_present| / |fixtures with a
    non-empty relevant set|``; fixtures without labeled relevant decisions
    are excluded from the denominator and counted visibly instead of
    silently deflating the rate. ``MRR = mean(1 / rank of first relevant
    result)`` over the rows where reciprocal rank is defined.
    """

    k: int
    rows: tuple[FixtureCandidateSetRow, ...]
    presence_rate: float | None
    presence_rate_unavailable_reason: str | None
    mean_reciprocal_rank: float | None
    mean_reciprocal_rank_unavailable_reason: str | None

    def __post_init__(self) -> None:
        if self.k < 1:
            raise CandidateMetricsValidationError("k must be >= 1")
        fixture_ids = [row.fixture_id for row in self.rows]
        if fixture_ids != sorted(fixture_ids):
            raise CandidateMetricsValidationError("rows must be sorted by fixture_id")
        if len(set(fixture_ids)) != len(fixture_ids):
            raise CandidateMetricsValidationError("fixture_id values must be unique")
        for row in self.rows:
            if row.metrics.k != self.k:
                raise CandidateMetricsValidationError(
                    "every row must be computed at the report's K"
                )

        eligible = [row for row in self.rows if row.metrics.relevant_decision_ids]
        if eligible:
            expected_rate = sum(
                1 for row in eligible if row.metrics.relevant_present
            ) / len(eligible)
            if self.presence_rate != expected_rate:
                raise CandidateMetricsValidationError(
                    "presence_rate must equal |fixtures where relevant_present| / "
                    "|fixtures with a non-empty relevant set|"
                )
            if self.presence_rate_unavailable_reason is not None:
                raise CandidateMetricsValidationError(
                    "presence_rate_unavailable_reason is only valid when no fixture "
                    "has a non-empty relevant set"
                )
        else:
            if self.presence_rate is not None:
                raise CandidateMetricsValidationError(
                    "presence_rate must be None when no fixture has a non-empty "
                    "relevant set; silent rates are unrepresentable"
                )
            if (
                self.presence_rate_unavailable_reason is None
                or not self.presence_rate_unavailable_reason.strip()
            ):
                raise CandidateMetricsValidationError(
                    "a missing presence_rate requires an explicit reason"
                )

        defined_ranks = [
            row.metrics.reciprocal_rank
            for row in self.rows
            if row.metrics.reciprocal_rank is not None
        ]
        if defined_ranks:
            if self.mean_reciprocal_rank != sum(defined_ranks) / len(defined_ranks):
                raise CandidateMetricsValidationError(
                    "mean_reciprocal_rank must equal mean(1 / rank of first relevant result)"
                )
            if self.mean_reciprocal_rank_unavailable_reason is not None:
                raise CandidateMetricsValidationError(
                    "mean_reciprocal_rank_unavailable_reason is only valid when no "
                    "row defines a reciprocal rank"
                )
        else:
            if self.mean_reciprocal_rank is not None:
                raise CandidateMetricsValidationError(
                    "mean_reciprocal_rank must be None when no row defines a "
                    "reciprocal rank"
                )
            if (
                self.mean_reciprocal_rank_unavailable_reason is None
                or not self.mean_reciprocal_rank_unavailable_reason.strip()
            ):
                raise CandidateMetricsValidationError(
                    "a missing mean_reciprocal_rank requires an explicit reason"
                )

    @property
    def fixtures_total(self) -> int:
        return len(self.rows)

    @property
    def fixtures_with_relevant(self) -> int:
        return sum(1 for row in self.rows if row.metrics.relevant_decision_ids)

    @property
    def fixtures_without_relevant(self) -> int:
        return self.fixtures_total - self.fixtures_with_relevant

    @property
    def fixtures_with_relevant_present(self) -> int:
        return sum(1 for row in self.rows if row.metrics.relevant_present)


def aggregate_candidate_set_metrics(
    items: Iterable[LabeledCandidatePack],
    *,
    k: int = MAX_DECISIONS_FOR_DIFF_LIMIT,
) -> CandidateSetBatchReport:
    """Aggregate per-fixture metrics into a deterministic batch report.

    Rows are sorted by ``fixture_id`` so the report is identical regardless
    of input order; duplicate fixture ids fail instead of silently
    overwriting a row.
    """

    materialized = sorted(items, key=lambda item: item.fixture_id)
    fixture_ids = [item.fixture_id for item in materialized]
    if len(set(fixture_ids)) != len(fixture_ids):
        duplicates = sorted({value for value in fixture_ids if fixture_ids.count(value) > 1})
        raise CandidateMetricsValidationError(
            f"duplicate fixture_id values in batch input: {duplicates}"
        )
    rows = tuple(
        FixtureCandidateSetRow(
            fixture_id=item.fixture_id,
            metrics=compute_candidate_set_metrics(
                pack=item.pack,
                relevant_decision_ids=item.relevant_decision_ids,
                k=k,
            ),
        )
        for item in materialized
    )

    eligible = [row for row in rows if row.metrics.relevant_decision_ids]
    presence_rate: float | None = None
    presence_reason: str | None = None
    if eligible:
        presence_rate = sum(1 for row in eligible if row.metrics.relevant_present) / len(
            eligible
        )
    else:
        presence_reason = (
            "no fixture in the batch carries a non-empty relevant set; the "
            "presence rate is undefined — refusing to report a silent rate"
        )

    defined_ranks = [
        row.metrics.reciprocal_rank
        for row in rows
        if row.metrics.reciprocal_rank is not None
    ]
    mean_reciprocal_rank: float | None = None
    mrr_reason: str | None = None
    if defined_ranks:
        mean_reciprocal_rank = sum(defined_ranks) / len(defined_ranks)
    else:
        mrr_reason = (
            "no row defines a reciprocal rank (every fixture had an empty "
            "relevant set); MRR is undefined — refusing to report a silent 0.0"
        )

    return CandidateSetBatchReport(
        k=k,
        rows=rows,
        presence_rate=presence_rate,
        presence_rate_unavailable_reason=presence_reason,
        mean_reciprocal_rank=mean_reciprocal_rank,
        mean_reciprocal_rank_unavailable_reason=mrr_reason,
    )


def _normalized_relevant_ids(values: Iterable[str]) -> tuple[str, ...]:
    normalized = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise CandidateMetricsValidationError(
                "relevant_decision_ids must be non-empty strings"
            )
        normalized.append(value)
    return tuple(sorted(set(normalized)))


def _validate_sorted_unique_ids(name: str, values: tuple[str, ...]) -> None:
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise CandidateMetricsValidationError(f"{name} must be non-empty strings")
    if list(values) != sorted(set(values)):
        raise CandidateMetricsValidationError(f"{name} must be sorted and unique")


def _validate_hash(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise CandidateMetricsValidationError(f"{name} must be a sha256 hex string")


def _validate_omitted_counts(value: Mapping[str, int]) -> None:
    if not isinstance(value, Mapping):
        raise CandidateMetricsValidationError("pack_omitted_counts must be a JSON object")
    for key, count in value.items():
        if not isinstance(key, str) or not key.strip():
            raise CandidateMetricsValidationError(
                "pack_omitted_counts keys must be non-empty strings"
            )
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise CandidateMetricsValidationError(
                "pack_omitted_counts values must be non-negative integers"
            )
    try:
        json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise CandidateMetricsValidationError(
            "pack_omitted_counts must be JSON-serializable"
        ) from exc
