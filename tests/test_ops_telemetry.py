"""Tests for the operator-INTERNAL ops telemetry report (cortex#565).

Offline-first: the four pure aggregation functions in
:mod:`cortex.hosted.ops_metrics` are exercised on seeded rows (success rate,
nearest-rank p95 latency, error bucketing, empty input), the command renders +
``--json`` are checked against an in-memory fake DB, the cost-math reuse is
asserted to produce the *same numbers* as ``cost_report.aggregate_cost_rows``
on shared rows, and the untracked-metric gap is asserted to be named rather
than fabricated. One ``DATABASE_URL``-gated round-trip drives the real query
path end to end.

The operator-internal boundary is asserted explicitly: the report header line
says "do not expose" and the module docstring states the boundary.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from click.testing import CliRunner

from cortex.commands.cost_report import aggregate_cost_rows
from cortex.commands.ops_report import (
    INTERNAL_OPS_HEADER,
    REPO_SCOPE_LIMITATION,
    build_ops_report,
    cost_query_sql,
    jobs_query_sql,
    ops_report_command,
    ops_report_payload,
    render_ops_report,
)
from cortex.hosted.ops_metrics import (
    COVERAGE_GAP_NOTE,
    OpsMetricsError,
    coverage,
    error_breakdown,
    review_latency,
    throughput_and_success,
)

REVIEW = "github.pull_request"
MODEL_A = "anthropic/claude-cli"
MODEL_B = "anthropic/claude-opus-4-1"


def _job(
    *,
    job_type: str = REVIEW,
    status: str = "succeeded",
    attempts: int = 1,
    enqueued_at: datetime | None = None,
    finished_at: datetime | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    base = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    return {
        "job_type": job_type,
        "status": status,
        "attempts": attempts,
        "enqueued_at": enqueued_at if enqueued_at is not None else base,
        "finished_at": finished_at,
        "last_error": last_error,
    }


# ---------------------------------------------------------------------------
# throughput_and_success
# ---------------------------------------------------------------------------


def test_throughput_success_rate_uses_terminal_denominator() -> None:
    # 3 succeeded, 1 dead, 1 queued, 1 running. Success rate is over the
    # terminal jobs only: 3 / (3 + 1) = 0.75; the in-flight 2 do not dilute it.
    rows = (
        [_job(status="succeeded", attempts=1) for _ in range(3)]
        + [_job(status="dead", attempts=5)]
        + [_job(status="queued", attempts=0)]
        + [_job(status="running", attempts=1)]
    )
    report = throughput_and_success(rows)
    assert len(report.by_job_type) == 1
    t = report.by_job_type[0]
    assert t.job_type == REVIEW
    assert t.total == 6
    assert t.succeeded == 3
    assert t.dead == 1
    assert t.queued == 1
    assert t.running == 1
    assert t.success_rate == pytest.approx(0.75)
    assert t.dead_letter_rate == pytest.approx(0.25)
    # mean attempts over all six rows: (1+1+1+5+0+1)/6 = 9/6 = 1.5
    assert t.mean_attempts == pytest.approx(1.5)


def test_throughput_rates_are_none_with_no_terminal_jobs() -> None:
    # An honest "no signal": queued/running only -> rate is None, not a
    # misleading 0.0 or 1.0.
    rows = [_job(status="queued", attempts=0), _job(status="running", attempts=1)]
    t = throughput_and_success(rows).by_job_type[0]
    assert t.success_rate is None
    assert t.dead_letter_rate is None
    assert t.mean_attempts == pytest.approx(0.5)


def test_throughput_is_total_ordered_by_job_type() -> None:
    rows = [
        _job(job_type="zeta.task", status="succeeded"),
        _job(job_type="alpha.task", status="succeeded"),
        _job(job_type=REVIEW, status="succeeded"),
    ]
    report = throughput_and_success(rows)
    assert [t.job_type for t in report.by_job_type] == [
        "alpha.task",
        REVIEW,
        "zeta.task",
    ]


def test_throughput_empty_is_empty_report() -> None:
    assert throughput_and_success([]).by_job_type == ()


def test_throughput_rejects_unknown_status() -> None:
    with pytest.raises(OpsMetricsError, match="unknown status"):
        throughput_and_success([_job(status="done")])  # 'done' is not the DB vocab


def test_throughput_rejects_bad_attempts() -> None:
    with pytest.raises(OpsMetricsError, match="attempts"):
        throughput_and_success([_job(attempts=-1)])


# ---------------------------------------------------------------------------
# review_latency
# ---------------------------------------------------------------------------


def test_review_latency_nearest_rank_p95() -> None:
    base = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    # Ten finished reviews with latencies 1..10 seconds.
    rows = [
        _job(
            status="succeeded",
            enqueued_at=base,
            finished_at=base + timedelta(seconds=n),
        )
        for n in range(1, 11)
    ]
    report = review_latency(rows)
    assert report.sample_count == 10
    assert report.mean_seconds == pytest.approx(5.5)
    # Even n -> median averages the 5th and 6th (5 and 6) -> 5.5.
    assert report.median_seconds == pytest.approx(5.5)
    # Nearest-rank p95 of 1..10: ceil(0.95*10)=10 -> the 10th value = 10.
    assert report.p95_seconds == pytest.approx(10.0)
    assert report.pending_count == 0


def test_review_latency_counts_pending_and_ignores_other_job_types() -> None:
    base = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    rows = [
        _job(status="succeeded", enqueued_at=base, finished_at=base + timedelta(seconds=4)),
        _job(status="running", enqueued_at=base, finished_at=None),  # pending
        _job(
            job_type="other.task",
            status="succeeded",
            enqueued_at=base,
            finished_at=base + timedelta(seconds=999),  # must NOT pollute the tail
        ),
    ]
    report = review_latency(rows)
    assert report.sample_count == 1
    assert report.p95_seconds == pytest.approx(4.0)
    assert report.pending_count == 1


def test_review_latency_empty_is_zero_with_pending_preserved() -> None:
    report = review_latency([_job(status="queued", finished_at=None)])
    assert report.sample_count == 0
    assert report.mean_seconds == 0.0
    assert report.p95_seconds == 0.0
    assert report.pending_count == 1


def test_review_latency_rejects_finished_before_enqueued() -> None:
    base = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    rows = [_job(status="succeeded", enqueued_at=base, finished_at=base - timedelta(seconds=1))]
    with pytest.raises(OpsMetricsError, match="precedes enqueued_at"):
        review_latency(rows)


# ---------------------------------------------------------------------------
# coverage (honest: what ran, by model; finer ratios are a declared gap)
# ---------------------------------------------------------------------------


def test_coverage_counts_reviews_run_by_model() -> None:
    rows = [
        {"model_id": MODEL_A},
        {"model_id": MODEL_A},
        {"model_id": MODEL_B},
    ]
    report = coverage(rows)
    assert report.total_reviews_run == 3
    by_model = {m.model_id: m.reviews_run for m in report.by_model}
    assert by_model == {MODEL_A: 2, MODEL_B: 1}
    # by_model is total-ordered by model_id.
    assert [m.model_id for m in report.by_model] == sorted(by_model)


def test_coverage_names_the_untracked_ratio_gap_not_fabricated() -> None:
    report = coverage([{"model_id": MODEL_A}])
    assert report.gap_note == COVERAGE_GAP_NOTE
    assert "not yet tracked" in report.gap_note
    assert "review_outcomes" in report.gap_note
    # The function reports nothing it cannot source: the payload carries only
    # what is durable (total + per-model run counts + the gap note). No
    # fabricated findings/no-findings/no-decisions ratio keys are present.
    payload = report.as_payload()
    assert set(payload) == {"total_reviews_run", "by_model", "gap_note"}
    for fabricated in ("findings_ratio", "no_findings", "no_decisions", "decisions_checked"):
        assert fabricated not in payload
    assert payload["gap_note"] == COVERAGE_GAP_NOTE


def test_coverage_empty_is_zero_with_gap_note() -> None:
    report = coverage([])
    assert report.total_reviews_run == 0
    assert report.by_model == ()
    assert report.gap_note == COVERAGE_GAP_NOTE


def test_coverage_rejects_blank_model() -> None:
    with pytest.raises(OpsMetricsError, match="model_id"):
        coverage([{"model_id": ""}])


# ---------------------------------------------------------------------------
# error_breakdown
# ---------------------------------------------------------------------------


def test_error_breakdown_buckets_by_prefix_ranked_desc() -> None:
    # Two distinct error classes. The first two share a stable >60-char head and
    # differ only in the parameterized tail (the timeout value / host id past
    # char 60), so the prefix bucket collapses them into one class.
    shared_head = "hosted Postgres connection failed (unreachable) for db host: "
    assert len(shared_head) >= 60
    rows = [
        {"last_error": shared_head + "timed out after 10s"},
        {"last_error": shared_head + "connection refused on retry 3"},
        {"last_error": "no handler registered for job type 'x'"},
    ]
    breakdown = error_breakdown(rows)
    assert breakdown.total_dead == 3
    assert breakdown.missing_reason_count == 0
    # The two connection failures share the 60-char prefix bucket; ranked first.
    assert breakdown.classes[0].count == 2
    assert breakdown.classes[0].reason.startswith("hosted Postgres connection failed")
    assert breakdown.classes[1].count == 1


def test_error_breakdown_counts_missing_reason() -> None:
    rows: list[dict[str, Any]] = [
        {"last_error": None},
        {"last_error": "   "},
        {"last_error": "real failure"},
    ]
    breakdown = error_breakdown(rows)
    assert breakdown.total_dead == 3
    assert breakdown.missing_reason_count == 2
    assert len(breakdown.classes) == 1
    assert breakdown.classes[0].reason == "real failure"


def test_error_breakdown_is_total_ordered_on_ties() -> None:
    # Equal counts -> deterministic tiebreak by reason ascending.
    rows = [{"last_error": "bravo"}, {"last_error": "alpha"}]
    breakdown = error_breakdown(rows)
    assert [c.reason for c in breakdown.classes] == ["alpha", "bravo"]


def test_error_breakdown_empty() -> None:
    breakdown = error_breakdown([])
    assert breakdown.total_dead == 0
    assert breakdown.missing_reason_count == 0
    assert breakdown.classes == ()


# ---------------------------------------------------------------------------
# cost reuse: same numbers as cost_report on shared rows
# ---------------------------------------------------------------------------


def test_cost_half_reuses_cost_report_aggregation_exactly() -> None:
    cost_rows = [
        {"model_id": MODEL_A, "usd": 0.0},
        {"model_id": MODEL_B, "usd": 2.0},
        {"model_id": MODEL_B, "usd": 4.0},
    ]
    job_rows = [_job(status="succeeded")]
    _, _, _, cost, _ = build_ops_report(job_rows, cost_rows)
    # The cost report inside ops-report must equal cost_report's own output on
    # the identical rows — one cost-math owner, verified.
    expected = aggregate_cost_rows(cost_rows)
    assert cost == expected
    assert cost.total_usd == pytest.approx(6.0)
    assert cost.total_reviews == 3


def test_build_ops_report_derives_dead_rows_for_error_breakdown() -> None:
    job_rows = [
        _job(status="succeeded"),
        _job(status="dead", attempts=5, last_error="boom"),
        _job(status="dead", attempts=5, last_error="boom"),
    ]
    _, _, _, _, errors = build_ops_report(job_rows, [])
    assert errors.total_dead == 2
    assert errors.classes[0].reason == "boom"
    assert errors.classes[0].count == 2


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------


def test_jobs_query_has_no_repo_filter_clause() -> None:
    # The jobs table has no repo column; a repo filter here would be fabricated.
    plain = jobs_query_sql()
    assert "WHERE" not in plain
    assert "repo" not in plain
    since = jobs_query_sql(since=True)
    assert "enqueued_at >= %(since)s" in since


def test_cost_query_adds_filters_only_when_requested() -> None:
    plain = cost_query_sql()
    assert "WHERE" not in plain
    both = cost_query_sql(since=True, repo=True)
    assert "occurred_at >= %(since)s" in both
    assert "repo_full_name = %(repo)s" in both
    assert "AND" in both


# ---------------------------------------------------------------------------
# rendering + JSON shape + operator-internal boundary
# ---------------------------------------------------------------------------


def _sample_reports() -> tuple[Any, Any, Any, Any, Any]:
    base = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    job_rows = [
        _job(status="succeeded", enqueued_at=base, finished_at=base + timedelta(seconds=3)),
        _job(status="dead", attempts=5, last_error="connection refused"),
    ]
    cost_rows = [{"model_id": MODEL_A, "usd": 1.5}]
    return build_ops_report(job_rows, cost_rows)


def test_render_states_operator_internal_boundary() -> None:
    throughput, latency, cov, cost, errors = _sample_reports()
    rendered = render_ops_report(throughput, latency, cov, cost, errors, repo_scoped=False)
    assert rendered.splitlines()[0] == INTERNAL_OPS_HEADER
    assert "do not expose" in rendered.lower()
    # Each section header is present.
    assert "throughput + success" in rendered
    assert "review latency" in rendered
    assert "coverage" in rendered
    assert "cost (provider dollars" in rendered
    assert "error breakdown" in rendered
    # The gap is named in the rendered report, not fabricated.
    assert "not yet tracked" in rendered


def test_render_repo_scope_limitation_only_when_scoped() -> None:
    throughput, latency, cov, cost, errors = _sample_reports()
    scoped = render_ops_report(throughput, latency, cov, cost, errors, repo_scoped=True)
    assert REPO_SCOPE_LIMITATION in scoped
    unscoped = render_ops_report(throughput, latency, cov, cost, errors, repo_scoped=False)
    assert REPO_SCOPE_LIMITATION not in unscoped


def test_json_payload_shape() -> None:
    throughput, latency, cov, cost, errors = _sample_reports()
    payload = ops_report_payload(throughput, latency, cov, cost, errors, repo_scoped=True)
    assert payload["header"] == INTERNAL_OPS_HEADER
    assert set(payload) >= {
        "header",
        "throughput",
        "review_latency",
        "coverage",
        "cost",
        "error_breakdown",
        "repo_scope_limitation",
    }
    assert payload["coverage"]["gap_note"] == COVERAGE_GAP_NOTE
    assert payload["cost"]["total_usd"] == pytest.approx(1.5)
    # Round-trips through json cleanly (datetimes never leak into the payload).
    assert json.loads(json.dumps(payload, sort_keys=True))["header"] == INTERNAL_OPS_HEADER


# ---------------------------------------------------------------------------
# command degrades visibly when DATABASE_URL is unset
# ---------------------------------------------------------------------------


def test_command_errors_visibly_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = CliRunner().invoke(ops_report_command, [])
    assert result.exit_code == 1
    assert "DATABASE_URL is not set" in result.output


def test_command_rejects_blank_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://user@host:5432/db")
    result = CliRunner().invoke(ops_report_command, ["--repo", "   "])
    assert result.exit_code != 0
    assert "--repo must not be blank" in result.output


# ---------------------------------------------------------------------------
# DATABASE_URL-gated round-trip over real rows
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set DATABASE_URL to a pgvector Postgres to run the ops-report round-trip",
)
def test_ops_report_round_trip_over_real_rows() -> None:
    import psycopg

    from cortex.hosted.db import connect
    from cortex.hosted.migrations import apply_schema

    connection = connect(DATABASE_URL)
    try:
        apply_schema(connection)
        # Seed one succeeded review job + one dead job + one cost row, then run
        # the real query path the command uses and assert the aggregations.
        tenant = str(uuid4())
        marker = f"ops-rt-{tenant[:8]}"
        connection.execute(
            "INSERT INTO cortex_hosted.jobs "
            "(job_type, idempotency_key, status, payload, max_attempts, "
            " enqueued_at, finished_at, attempts) VALUES "
            "(%(jt)s, %(k1)s, 'succeeded', '{}'::jsonb, 5, now() - interval '5 seconds', "
            " now(), 1)",
            {"jt": REVIEW, "k1": f"{marker}-ok"},
        )
        connection.execute(
            "INSERT INTO cortex_hosted.jobs "
            "(job_type, idempotency_key, status, payload, max_attempts, "
            " enqueued_at, finished_at, attempts, last_error) VALUES "
            "(%(jt)s, %(k2)s, 'dead', '{}'::jsonb, 5, now(), now(), 5, %(err)s)",
            {"jt": REVIEW, "k2": f"{marker}-dead", "err": "round-trip seeded failure"},
        )
        from cortex.hosted.review_cost import ReviewCostRecord, review_cost_insert_sql

        record = ReviewCostRecord(
            tenant_id=tenant,
            repo_full_name="autumngarage/cortex",
            pr_number=565,
            head_sha="opsrt12",
            model_id=MODEL_A,
            input_tokens=10,
            output_tokens=5,
            usd=0.25,
            occurred_at=datetime.now(UTC),
        )
        connection.execute(review_cost_insert_sql(), record.as_insert_parameters())
        connection.commit()

        # Run the exact query strings the command builds (no filters).
        cursor = connection.execute(jobs_query_sql(), {})
        cols = [d[0] for d in cursor.description or ()]
        job_rows = [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]
        cursor = connection.execute(cost_query_sql(), {})
        cols = [d[0] for d in cursor.description or ()]
        cost_rows = [
            {"model_id": r[cols.index("model_id")], "usd": float(r[cols.index("usd")])}
            for r in cursor.fetchall()
        ]
        connection.rollback()
    except psycopg.Error:
        connection.rollback()
        raise
    finally:
        connection.close()

    review_rows = [r for r in job_rows if r["job_type"] == REVIEW]
    assert len(review_rows) >= 2
    latency = review_latency(review_rows)
    assert latency.sample_count >= 2
    assert latency.p95_seconds >= 0.0
    errors = error_breakdown([r for r in job_rows if r["status"] == "dead"])
    assert errors.total_dead >= 1
    cov = coverage(cost_rows)
    assert cov.total_reviews_run >= 1
    assert cov.gap_note == COVERAGE_GAP_NOTE
