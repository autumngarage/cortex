"""`cortex ops-report` — OPERATOR-INTERNAL operational telemetry (cortex#565).

Internal operator ops report — do not expose. This command is the operational
health view of the hosted service: queue throughput, success vs dead-letter
rate by job type, review latency (p50/p95), run coverage, provider-dollar cost
(reused from ``cost-report``), and the top dead-letter reason classes. It is an
operator-internal tool, never a customer surface and never billed.

The command does the ``DATABASE_URL`` query and feeds plain row dicts into the
pure aggregation functions in :mod:`cortex.hosted.ops_metrics`; the cost half
reuses :func:`cortex.commands.cost_report.aggregate_cost_rows` so the cost math
has exactly one owner. No ingestion happens here — this is read-only
aggregation over the durable ``jobs`` queue and ``review_cost_records`` ledger.

Honesty about sourcing (see ``ops_metrics`` for the full account): the
``jobs`` table has no ``repo_full_name`` column, so ``--repo`` scopes the cost
ledger and coverage (which do carry the repo) but cannot scope the queue-side
throughput/latency/error metrics; that limitation is printed in the report
rather than silently ignored. Findings-vs-no-findings ratios are not a column
today and are named as a gap, not fabricated.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import click

from cortex.commands.cost_report import CostReport, aggregate_cost_rows
from cortex.hosted.db import HostedDbError, connect
from cortex.hosted.ops_metrics import (
    CoverageReport,
    ErrorBreakdown,
    LatencyReport,
    OpsMetricsError,
    ThroughputReport,
    coverage,
    error_breakdown,
    review_latency,
    throughput_and_success,
)

INTERNAL_OPS_HEADER = "Internal operator ops report — do not expose."
"""The load-bearing header line. The operator-internal boundary is stated in
the report itself, not just in code comments."""

REPO_SCOPE_LIMITATION = (
    "note: --repo scopes the cost ledger + coverage only; the jobs queue has no "
    "repo column, so throughput / latency / error metrics are service-wide."
)
"""Printed whenever ``--repo`` is supplied so the operator is never misled into
thinking the queue-side numbers were repo-filtered."""


class OpsReportError(ValueError):
    """Raised when the ops report cannot be built from its inputs."""


def jobs_query_sql(schema: str = "cortex_hosted", *, since: bool = False) -> str:
    """SELECT the jobs-queue columns the report needs, optionally since a date.

    The ``--repo`` filter is intentionally absent: the jobs table carries no
    ``repo_full_name`` column (repo identity lives in the ``payload`` JSON), so
    a repo filter here would be a fabricated boundary. Only ``--since`` (on
    ``enqueued_at``) is a durable, indexed filter.
    """

    from cortex.hosted.schema import _validate_sql_identifier

    _validate_sql_identifier(schema)
    where = "\nWHERE enqueued_at >= %(since)s" if since else ""
    return (
        f"SELECT job_type, status, attempts, enqueued_at, finished_at, last_error "
        f"FROM {schema}.jobs{where} "
        "ORDER BY enqueued_at"
    ).strip()


def cost_query_sql(
    schema: str = "cortex_hosted", *, since: bool = False, repo: bool = False
) -> str:
    """SELECT the cost ledger columns the report needs, optionally filtered.

    Mirrors ``cost_report.review_cost_query_sql`` (``model_id`` + ``usd``,
    ordered by ``occurred_at``): the report feeds these rows to both the cost
    aggregation and the coverage count. Filters are bound parameters, never
    string-interpolated values; only the presence of each WHERE clause varies
    with the flags.
    """

    from cortex.hosted.schema import _validate_sql_identifier

    _validate_sql_identifier(schema)
    clauses: list[str] = []
    if since:
        clauses.append("occurred_at >= %(since)s")
    if repo:
        clauses.append("repo_full_name = %(repo)s")
    where = f"\nWHERE {' AND '.join(clauses)}" if clauses else ""
    return (
        f"SELECT model_id, usd FROM {schema}.review_cost_records{where} "
        "ORDER BY occurred_at"
    ).strip()


def build_ops_report(
    job_rows: Sequence[Mapping[str, Any]],
    cost_rows: Sequence[Mapping[str, Any]],
) -> tuple[ThroughputReport, LatencyReport, CoverageReport, CostReport, ErrorBreakdown]:
    """Run every aggregation over the queried rows (pure, offline-testable).

    Dead rows for the error breakdown are derived from the same job rows
    (status == 'dead'); there is one query, one in-memory pass per metric, no
    second DB round-trip. The cost half reuses ``aggregate_cost_rows`` verbatim
    so the dollar math has a single owner.
    """

    throughput = throughput_and_success(job_rows)
    latency = review_latency(job_rows)
    coverage_report = coverage(cost_rows)
    cost = aggregate_cost_rows(cost_rows)
    dead_rows = [row for row in job_rows if row.get("status") == "dead"]
    errors = error_breakdown(dead_rows)
    return throughput, latency, coverage_report, cost, errors


def _fmt_rate(rate: float | None) -> str:
    return "n/a (no terminal jobs)" if rate is None else f"{rate * 100:.1f}%"


def render_ops_report(
    throughput: ThroughputReport,
    latency: LatencyReport,
    coverage_report: CoverageReport,
    cost: CostReport,
    errors: ErrorBreakdown,
    *,
    repo_scoped: bool,
) -> str:
    """Render the full operator ops report as plain text, header first."""

    lines = [INTERNAL_OPS_HEADER, ""]
    if repo_scoped:
        lines.extend([REPO_SCOPE_LIMITATION, ""])

    lines.append("== throughput + success (by job_type) ==")
    if throughput.by_job_type:
        for t in throughput.by_job_type:
            lines.append(
                f"  {t.job_type}: {t.total} total "
                f"(queued {t.queued}, running {t.running}, "
                f"succeeded {t.succeeded}, dead {t.dead}); "
                f"success {_fmt_rate(t.success_rate)}, "
                f"dead-letter {_fmt_rate(t.dead_letter_rate)}, "
                f"mean attempts {t.mean_attempts:.2f}"
            )
    else:
        lines.append("  (no jobs recorded yet)")

    lines.extend(["", "== review latency (enqueue -> finish, seconds) =="])
    if latency.sample_count:
        lines.append(
            f"  samples {latency.sample_count}: "
            f"mean {latency.mean_seconds:.1f}s, "
            f"p50 {latency.median_seconds:.1f}s, "
            f"p95 {latency.p95_seconds:.1f}s"
        )
    else:
        lines.append("  (no finished reviews yet)")
    if latency.pending_count:
        lines.append(f"  pending (not yet finished): {latency.pending_count}")

    lines.extend(["", "== coverage (reviews that ran, by model) =="])
    lines.append(f"  total reviews run: {coverage_report.total_reviews_run}")
    for m in coverage_report.by_model:
        lines.append(f"    {m.model_id}: {m.reviews_run}")
    lines.append(f"  {coverage_report.gap_note}")

    lines.extend(["", "== cost (provider dollars, reused from cost-report) =="])
    lines.append(f"  total reviews: {cost.total_reviews}")
    lines.append(f"  total USD: ${cost.total_usd:.6f}")
    if cost.total_reviews:
        lines.append(
            f"  per review — mean ${cost.mean_usd:.6f}, "
            f"median ${cost.median_usd:.6f}, p95 ${cost.p95_usd:.6f}"
        )
        for b in cost.by_model:
            lines.append(
                f"    {b.model_id}: {b.review_count} review(s), "
                f"mean ${b.mean_usd:.6f}, p95 ${b.p95_usd:.6f}"
            )

    lines.extend(["", "== error breakdown (dead-letter reason classes) =="])
    lines.append(f"  total dead: {errors.total_dead}")
    if errors.missing_reason_count:
        lines.append(f"  dead with no recorded reason: {errors.missing_reason_count}")
    for c in errors.classes:
        lines.append(f"    [{c.count}] {c.reason}")
    if not errors.classes and not errors.missing_reason_count:
        lines.append("  (no dead-letter jobs)")

    return "\n".join(lines)


def ops_report_payload(
    throughput: ThroughputReport,
    latency: LatencyReport,
    coverage_report: CoverageReport,
    cost: CostReport,
    errors: ErrorBreakdown,
    *,
    repo_scoped: bool,
) -> dict[str, Any]:
    """Machine-readable shape mirroring the rendered report, header included."""

    payload: dict[str, Any] = {
        "header": INTERNAL_OPS_HEADER,
        "throughput": throughput.as_payload(),
        "review_latency": latency.as_payload(),
        "coverage": coverage_report.as_payload(),
        "cost": cost.as_payload(),
        "error_breakdown": errors.as_payload(),
    }
    if repo_scoped:
        payload["repo_scope_limitation"] = REPO_SCOPE_LIMITATION
    return payload


def _query_rows(connection: Any, query: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    import psycopg

    try:
        cursor = connection.execute(query, params)
        column_names = [description[0] for description in cursor.description or ()]
        return [dict(zip(column_names, row, strict=True)) for row in cursor.fetchall()]
    except psycopg.Error as exc:
        raise OpsReportError(f"ops telemetry query failed: {exc}") from exc


@click.command("ops-report", context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--since",
    "since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Only include jobs/reviews on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--repo",
    "repo",
    default=None,
    help="Scope the cost ledger + coverage to this repo (owner/name); "
    "queue-side metrics stay service-wide (no repo column on jobs).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON.",
)
def ops_report_command(
    *, since: datetime | None, repo: str | None, as_json: bool
) -> None:
    """Operator-internal operational telemetry report (do not expose).

    Aggregates the durable hosted ``jobs`` queue and ``review_cost_records``
    ledger reached via ``DATABASE_URL`` into throughput / success rate by job
    type, review latency (p50/p95), run coverage by model, provider-dollar cost
    (reused from ``cost-report``), and top dead-letter reason classes. This is
    operator-internal operational health data — never a customer surface.
    """

    import os

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        click.echo(
            "error: DATABASE_URL is not set; ops-report reads the hosted jobs "
            "queue and review cost ledger from the hosted Postgres",
            err=True,
        )
        sys.exit(1)

    cost_params: dict[str, Any] = {}
    job_params: dict[str, Any] = {}
    if since is not None:
        since_value = since.date() if isinstance(since, datetime) else since
        cost_params["since"] = since_value
        job_params["since"] = since_value
    cleaned_repo: str | None = None
    if repo is not None:
        cleaned_repo = repo.strip()
        if not cleaned_repo:
            raise click.BadParameter("--repo must not be blank", param_hint="--repo")
        cost_params["repo"] = cleaned_repo

    jobs_sql = jobs_query_sql(since=since is not None)
    cost_sql = cost_query_sql(since=since is not None, repo=cleaned_repo is not None)

    try:
        connection = connect(dsn)
        try:
            job_rows = _query_rows(connection, jobs_sql, job_params)
            raw_cost_rows = _query_rows(connection, cost_sql, cost_params)
        finally:
            connection.close()
    except (HostedDbError, OpsReportError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    # Postgres numeric (usd) comes back as Decimal; the cost aggregation expects
    # floats. Normalize once, here, at the I/O boundary.
    cost_rows = [{"model_id": row["model_id"], "usd": float(row["usd"])} for row in raw_cost_rows]

    try:
        throughput, latency, coverage_report, cost, errors = build_ops_report(
            job_rows, cost_rows
        )
    except (OpsMetricsError, OpsReportError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    repo_scoped = cleaned_repo is not None
    if as_json:
        import json

        payload = ops_report_payload(
            throughput, latency, coverage_report, cost, errors, repo_scoped=repo_scoped
        )
        click.echo(json.dumps(payload, sort_keys=True))
        return
    click.echo(
        render_ops_report(
            throughput, latency, coverage_report, cost, errors, repo_scoped=repo_scoped
        )
    )
