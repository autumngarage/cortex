"""Operator-INTERNAL operational telemetry aggregation (cortex#565).

OPERATOR-INTERNAL boundary: every output of this module is operational health
data about *our* hosted service — throughput, success rate, dead-letter
reasons, review latency, and run coverage. It is never a customer surface and
never billed. The matching header line lives in
:mod:`cortex.commands.ops_report`; the boundary is restated here so a reader of
the module alone cannot miss it.

This module is the pure, offline-testable core: every function takes plain row
dicts (the command does the ``DATABASE_URL`` query and feeds them in) and
returns frozen, deterministic, total-ordered dataclasses. No DB handle, no
clock, no environment access lives here.

Honesty about sourcing. Two durable tables back these metrics:

- ``cortex_hosted.jobs`` — ``job_type``, ``status`` (``queued`` / ``running``
  / ``succeeded`` / ``dead``), ``attempts``, ``enqueued_at``, ``claimed_at``,
  ``finished_at``, ``last_error``. The queue is the source of throughput,
  success/dead-letter rate, retry counts, latency, and error breakdown.
- ``cortex_hosted.review_cost_records`` — one append-only row per *successful*
  review (``model_id``, ``occurred_at``, token counts, ``usd``). Its presence
  is the source of "a review actually ran" coverage by model.

What is NOT durably queryable today is named as a gap, never fabricated:

- The job lifecycle status vocabulary is ``succeeded`` (the brief's "done") /
  ``dead`` / ``running`` / ``queued``; this module mirrors the DB CHECK
  constraint exactly via :class:`~cortex.hosted.jobs.JobStatus`.
- Exact findings-vs-no-findings and decisions-checked-per-review ratios live
  in each job's ``result`` JSON, not in a queryable column. Coverage here
  reports "reviews that ran, by model" from the cost ledger and surfaces the
  finer ratios as a declared gap (:data:`COVERAGE_GAP_NOTE`) rather than
  guessing. Closing the gap needs a ``review_outcomes`` column/table.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cortex.hosted.jobs import JobStatus

# The job type the hosted worker registers for PR reviews. Latency and coverage
# are scoped to this type so a future non-review job type cannot silently
# pollute the review-latency tail.
REVIEW_JOB_TYPE = "github.pull_request"

COVERAGE_GAP_NOTE = (
    "not yet tracked: exact findings-vs-no-findings / no-decisions ratio and "
    "avg decisions-checked-per-review are not durably queryable (they live in "
    "each job's result JSON, not a column). Closing this needs a "
    "review_outcomes column/table. Coverage below reports reviews that "
    "actually ran (one cost row per successful review), by model."
)

# Length of the dead-letter reason bucket. A fixed prefix collapses the
# long, parameterized tail of an error string ("connection refused to host
# X", "host Y") into a stable class while keeping enough text to be legible.
ERROR_BUCKET_PREFIX_LEN = 60


class OpsMetricsError(ValueError):
    """Raised when a telemetry row cannot support a trustworthy metric."""


# ---------------------------------------------------------------------------
# Throughput + success
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobTypeThroughput:
    """Per-``job_type`` queue health derived from durable status + attempts."""

    job_type: str
    total: int
    queued: int
    running: int
    succeeded: int
    dead: int
    success_rate: float | None
    dead_letter_rate: float | None
    mean_attempts: float

    def as_payload(self) -> dict[str, Any]:
        return {
            "job_type": self.job_type,
            "total": self.total,
            "queued": self.queued,
            "running": self.running,
            "succeeded": self.succeeded,
            "dead": self.dead,
            "success_rate": self.success_rate,
            "dead_letter_rate": self.dead_letter_rate,
            "mean_attempts": self.mean_attempts,
        }


@dataclass(frozen=True)
class ThroughputReport:
    """Total-ordered (by ``job_type``) throughput across all job types."""

    by_job_type: tuple[JobTypeThroughput, ...]

    def as_payload(self) -> dict[str, Any]:
        return {"by_job_type": [t.as_payload() for t in self.by_job_type]}


def _require_status(value: Any, index: int) -> JobStatus:
    """Coerce a row's status into the closed vocabulary, failing visibly."""

    if isinstance(value, JobStatus):
        return value
    if not isinstance(value, str):
        raise OpsMetricsError(f"job row {index}: status must be a string, got {value!r}")
    try:
        return JobStatus(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in JobStatus)
        raise OpsMetricsError(
            f"job row {index}: unknown status {value!r} (valid: {valid})"
        ) from exc


def _require_job_type(value: Any, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OpsMetricsError(f"job row {index}: job_type must be a non-empty string")
    return value


def _require_attempts(value: Any, index: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise OpsMetricsError(f"job row {index}: attempts must be an integer, got {value!r}")
    attempts: int = value
    if attempts < 0:
        raise OpsMetricsError(f"job row {index}: attempts must be >= 0, got {attempts}")
    return attempts


def throughput_and_success(job_rows: Sequence[Mapping[str, Any]]) -> ThroughputReport:
    """Aggregate jobs into per-type throughput, success and dead-letter rates.

    Each row must carry ``job_type`` (str), ``status`` (one of the
    :class:`~cortex.hosted.jobs.JobStatus` values), and ``attempts`` (int).

    ``success_rate`` is ``succeeded / (succeeded + dead)`` — the rate among
    jobs that reached a *terminal* state, so in-flight ``queued`` / ``running``
    jobs do not dilute it. ``dead_letter_rate`` is its complement over the same
    terminal denominator. Both are ``None`` when no job of that type has
    terminated yet — an honest "no signal" rather than a misleading ``0.0`` or
    ``1.0``. ``mean_attempts`` is over every row of the type. Empty input
    yields an empty report.
    """

    buckets: dict[str, dict[str, int]] = {}
    attempts: dict[str, list[int]] = {}
    for index, row in enumerate(job_rows):
        job_type = _require_job_type(row.get("job_type"), index)
        status = _require_status(row.get("status"), index)
        attempt_count = _require_attempts(row.get("attempts"), index)
        counts = buckets.setdefault(
            job_type,
            {"total": 0, "queued": 0, "running": 0, "succeeded": 0, "dead": 0},
        )
        counts["total"] += 1
        counts[status.value] += 1
        attempts.setdefault(job_type, []).append(attempt_count)

    by_job_type = tuple(
        _build_throughput(job_type, counts, attempts[job_type])
        for job_type, counts in sorted(buckets.items())
    )
    return ThroughputReport(by_job_type=by_job_type)


def _build_throughput(
    job_type: str, counts: Mapping[str, int], attempt_values: Sequence[int]
) -> JobTypeThroughput:
    terminal = counts["succeeded"] + counts["dead"]
    success_rate = counts["succeeded"] / terminal if terminal else None
    dead_letter_rate = counts["dead"] / terminal if terminal else None
    mean_attempts = sum(attempt_values) / len(attempt_values) if attempt_values else 0.0
    return JobTypeThroughput(
        job_type=job_type,
        total=counts["total"],
        queued=counts["queued"],
        running=counts["running"],
        succeeded=counts["succeeded"],
        dead=counts["dead"],
        success_rate=success_rate,
        dead_letter_rate=dead_letter_rate,
        mean_attempts=mean_attempts,
    )


# ---------------------------------------------------------------------------
# Review latency
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LatencyReport:
    """Enqueue -> finish latency for terminal review jobs (seconds)."""

    sample_count: int
    mean_seconds: float
    median_seconds: float
    p95_seconds: float
    # Review jobs that have not reached a terminal (finished) state yet, so
    # they carry no latency. Counted, not silently dropped: a large backlog
    # here is operational signal that the sampled tail is unrepresentative.
    pending_count: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "mean_seconds": self.mean_seconds,
            "median_seconds": self.median_seconds,
            "p95_seconds": self.p95_seconds,
            "pending_count": self.pending_count,
        }


def _require_datetime(value: Any, field: str, index: int) -> datetime:
    if not isinstance(value, datetime):
        raise OpsMetricsError(
            f"review row {index}: {field} must be a datetime, got {value!r}"
        )
    return value


def review_latency(review_rows: Sequence[Mapping[str, Any]]) -> LatencyReport:
    """Compute enqueue->finish latency stats for the review job type.

    Each row carries ``job_type`` (str), ``enqueued_at`` (datetime), and
    ``finished_at`` (datetime or ``None``). Only rows whose ``job_type`` equals
    :data:`REVIEW_JOB_TYPE` are considered; a row with ``finished_at is None``
    is in flight and counted in ``pending_count`` instead of contributing a
    latency sample. ``mean`` / ``median`` / ``p95`` mirror the cost-report
    statistics (median averages the two middle values for even n; p95 is the
    nearest-rank value). Empty / all-pending input yields an all-zero report
    with the pending count preserved.

    Invariant: ``finished_at >= enqueued_at`` for every sampled row. A row that
    violates it would be a clock or data error and fails visibly rather than
    contributing a negative latency.
    """

    durations: list[float] = []
    pending = 0
    for index, row in enumerate(review_rows):
        job_type = _require_job_type(row.get("job_type"), index)
        if job_type != REVIEW_JOB_TYPE:
            continue
        finished = row.get("finished_at")
        if finished is None:
            pending += 1
            continue
        enqueued = _require_datetime(row.get("enqueued_at"), "enqueued_at", index)
        finished_at = _require_datetime(finished, "finished_at", index)
        seconds = (finished_at - enqueued).total_seconds()
        if seconds < 0:
            raise OpsMetricsError(
                f"review row {index}: finished_at precedes enqueued_at "
                f"({seconds:.3f}s) — clock or data error"
            )
        durations.append(seconds)

    if not durations:
        return LatencyReport(
            sample_count=0,
            mean_seconds=0.0,
            median_seconds=0.0,
            p95_seconds=0.0,
            pending_count=pending,
        )
    return LatencyReport(
        sample_count=len(durations),
        mean_seconds=_mean(durations),
        median_seconds=_median(durations),
        p95_seconds=_p95(durations),
        pending_count=pending,
    )


# ---------------------------------------------------------------------------
# Coverage (honest: what ran, by model; finer ratios are a declared gap)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCoverage:
    """How many reviews actually ran under one model (cost-row presence)."""

    model_id: str
    reviews_run: int

    def as_payload(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "reviews_run": self.reviews_run}


@dataclass(frozen=True)
class CoverageReport:
    """Reviews-that-ran coverage plus the named gap for the finer ratios."""

    total_reviews_run: int
    by_model: tuple[ModelCoverage, ...]
    gap_note: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "total_reviews_run": self.total_reviews_run,
            "by_model": [m.as_payload() for m in self.by_model],
            "gap_note": self.gap_note,
        }


def coverage(review_cost_rows: Sequence[Mapping[str, Any]]) -> CoverageReport:
    """Derive run coverage from the cost ledger (one row per successful review).

    Findings-vs-no-findings and decisions-checked ratios are NOT columns today,
    so this function does not fabricate them: it reports the number of reviews
    that actually ran, by ``model_id`` (presence of a ``review_cost_records``
    row is the durable "a review ran" signal), and carries
    :data:`COVERAGE_GAP_NOTE` so the missing ratios are named, not implied.

    Each row must carry ``model_id`` (str). Empty input yields a zero report
    with the gap note still attached.
    """

    per_model: dict[str, int] = {}
    for index, row in enumerate(review_cost_rows):
        model_id = row.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise OpsMetricsError(
                f"cost row {index}: model_id must be a non-empty string"
            )
        per_model[model_id] = per_model.get(model_id, 0) + 1

    by_model = tuple(
        ModelCoverage(model_id=model_id, reviews_run=count)
        for model_id, count in sorted(per_model.items())
    )
    return CoverageReport(
        total_reviews_run=sum(per_model.values()),
        by_model=by_model,
        gap_note=COVERAGE_GAP_NOTE,
    )


# ---------------------------------------------------------------------------
# Error breakdown (dead-letter reason classes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorClass:
    """One dead-letter reason bucket with its occurrence count."""

    reason: str
    count: int

    def as_payload(self) -> dict[str, Any]:
        return {"reason": self.reason, "count": self.count}


@dataclass(frozen=True)
class ErrorBreakdown:
    """Total-ordered dead-letter reason classes (by count desc, then reason)."""

    total_dead: int
    # Dead rows whose last_error was NULL/blank — a dead-letter with no recorded
    # reason is itself a finding, surfaced rather than dropped.
    missing_reason_count: int
    classes: tuple[ErrorClass, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "total_dead": self.total_dead,
            "missing_reason_count": self.missing_reason_count,
            "classes": [c.as_payload() for c in self.classes],
        }


def _bucket_reason(last_error: str) -> str:
    """Collapse a full error string to a stable reason bucket.

    The first :data:`ERROR_BUCKET_PREFIX_LEN` characters, whitespace-collapsed,
    keep the stable head of the message while dropping the parameterized tail
    (host names, ids) that would otherwise scatter one failure mode across many
    singleton buckets.
    """

    collapsed = " ".join(last_error.split())
    return collapsed[:ERROR_BUCKET_PREFIX_LEN]


def error_breakdown(dead_rows: Sequence[Mapping[str, Any]]) -> ErrorBreakdown:
    """Bucket dead-letter ``last_error`` strings into ranked reason classes.

    Each row carries ``last_error`` (str or ``None``). Rows with a missing or
    blank reason are counted in ``missing_reason_count`` (a dead job with no
    recorded reason is a gap worth seeing) and excluded from the ranked
    classes. Classes are ordered by count descending, then reason ascending, so
    the output is deterministic and total-ordered even on ties. Empty input
    yields an all-zero breakdown.
    """

    counts: dict[str, int] = {}
    missing = 0
    for row in dead_rows:
        last_error = row.get("last_error")
        if last_error is None or (isinstance(last_error, str) and not last_error.strip()):
            missing += 1
            continue
        if not isinstance(last_error, str):
            raise OpsMetricsError(
                f"dead row: last_error must be a string or None, got {last_error!r}"
            )
        reason = _bucket_reason(last_error)
        counts[reason] = counts.get(reason, 0) + 1

    classes = tuple(
        ErrorClass(reason=reason, count=count)
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )
    return ErrorBreakdown(
        total_dead=len(dead_rows),
        missing_reason_count=missing,
        classes=classes,
    )


# ---------------------------------------------------------------------------
# Shared statistics (mirror cost_report's mean/median/p95 nearest-rank)
# ---------------------------------------------------------------------------


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _median(values: Sequence[float]) -> float:
    """Median of a non-empty list (average of the two middle for even n)."""

    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _p95(values: Sequence[float]) -> float:
    """95th percentile by nearest-rank — identical method to cost_report._p95.

    Nearest-rank is deterministic and assumption-free: the p-th percentile is
    the value at ceil(p/100 * n) (1-indexed), clamped into ``[1, n]``.
    """

    ordered = sorted(values)
    n = len(ordered)
    rank = -(-(95 * n) // 100)
    rank = max(1, min(rank, n))
    return ordered[rank - 1]
