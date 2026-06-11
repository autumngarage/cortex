"""`cortex cost-report` — OPERATOR-INTERNAL provider-dollar cost report (cortex#547).

This is the internal cost-understanding tool, separate from the customer credits
meter. It aggregates the append-only internal cost ledger
(``cortex_hosted.review_cost_records``, written by the hosted worker) into the
shape we use to price the product to be profitable: total reviews, total USD,
and mean / median / p95 USD per review, broken down by model.

The boundary is load-bearing and stated in the report header itself: this is
**provider dollars** (tokens x provider list rate), NOT customer credits. The
customer-facing meter is credits (``docs/HOSTED-PRICING.md``). Do not expose
this report to a customer surface.

The aggregation math (:func:`aggregate_cost_rows`) is a pure function over plain
row dicts so it is fully testable offline; the click command only adds the
``DATABASE_URL`` query and the rendering.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import click

from cortex.hosted.db import HostedDbError, connect

INTERNAL_REPORT_HEADER = (
    "Internal operator cost report — provider dollars, not customer credits. "
    "Do not expose."
)
"""The load-bearing header line. The internal/customer boundary is stated in the
report itself, not just in code comments."""


class CostReportError(ValueError):
    """Raised when the cost report cannot be built from its inputs."""


@dataclass(frozen=True)
class ModelCostBreakdown:
    """Per-model aggregation of internal review cost (provider dollars)."""

    model_id: str
    review_count: int
    total_usd: float
    mean_usd: float
    median_usd: float
    p95_usd: float

    def as_payload(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "review_count": self.review_count,
            "total_usd": self.total_usd,
            "mean_usd": self.mean_usd,
            "median_usd": self.median_usd,
            "p95_usd": self.p95_usd,
        }


@dataclass(frozen=True)
class CostReport:
    """The full internal cost report — provider dollars, never customer credits."""

    total_reviews: int
    total_usd: float
    mean_usd: float
    median_usd: float
    p95_usd: float
    by_model: tuple[ModelCostBreakdown, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "header": INTERNAL_REPORT_HEADER,
            "total_reviews": self.total_reviews,
            "total_usd": self.total_usd,
            "mean_usd": self.mean_usd,
            "median_usd": self.median_usd,
            "p95_usd": self.p95_usd,
            "by_model": [breakdown.as_payload() for breakdown in self.by_model],
        }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _median(values: Sequence[float]) -> float:
    """Median of a non-empty value list (average of the two middle for even n)."""

    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _p95(values: Sequence[float]) -> float:
    """95th percentile by the nearest-rank method on the sorted values.

    Nearest-rank is deterministic and needs no interpolation assumptions: the
    p-th percentile is the value at ceil(p/100 * n) (1-indexed). For a single
    value it returns that value; for the bulk it returns a real observed cost,
    which is what an operator wants when sizing the tail.
    """

    ordered = sorted(values)
    n = len(ordered)
    # ceil(0.95 * n) in integer arithmetic, clamped into [1, n].
    rank = -(-(95 * n) // 100)
    rank = max(1, min(rank, n))
    return ordered[rank - 1]


def aggregate_cost_rows(rows: Sequence[Mapping[str, Any]]) -> CostReport:
    """Aggregate internal review cost rows into the report (pure, offline-testable).

    Each row must carry ``model_id`` (str) and ``usd`` (number). The function
    computes the overall and per-model total/mean/median/p95 over the per-review
    USD values. An empty input yields an all-zero report (no reviews recorded
    yet) rather than raising — "nothing to report" is a valid, visible answer.
    """

    usd_values: list[float] = []
    per_model: dict[str, list[float]] = {}
    for index, row in enumerate(rows):
        raw_usd = row.get("usd")
        if isinstance(raw_usd, bool) or not isinstance(raw_usd, int | float):
            raise CostReportError(f"row {index}: usd must be a number, got {raw_usd!r}")
        usd = float(raw_usd)
        if usd < 0:
            raise CostReportError(f"row {index}: usd must be non-negative, got {usd}")
        model_id = row.get("model_id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise CostReportError(f"row {index}: model_id must be a non-empty string")
        usd_values.append(usd)
        per_model.setdefault(model_id, []).append(usd)

    if not usd_values:
        return CostReport(
            total_reviews=0,
            total_usd=0.0,
            mean_usd=0.0,
            median_usd=0.0,
            p95_usd=0.0,
            by_model=(),
        )

    by_model = tuple(
        ModelCostBreakdown(
            model_id=model_id,
            review_count=len(values),
            total_usd=sum(values),
            mean_usd=_mean(values),
            median_usd=_median(values),
            p95_usd=_p95(values),
        )
        for model_id, values in sorted(per_model.items())
    )
    return CostReport(
        total_reviews=len(usd_values),
        total_usd=sum(usd_values),
        mean_usd=_mean(usd_values),
        median_usd=_median(usd_values),
        p95_usd=_p95(usd_values),
        by_model=by_model,
    )


def render_cost_report(report: CostReport) -> str:
    """Render the internal cost report as plain text, header first."""

    lines = [
        INTERNAL_REPORT_HEADER,
        "",
        f"total reviews: {report.total_reviews}",
        f"total USD:     ${report.total_usd:.6f}",
    ]
    if report.total_reviews:
        lines.extend(
            [
                f"mean USD/review:   ${report.mean_usd:.6f}",
                f"median USD/review: ${report.median_usd:.6f}",
                f"p95 USD/review:    ${report.p95_usd:.6f}",
                "",
                "by model:",
            ]
        )
        for breakdown in report.by_model:
            lines.append(
                f"  {breakdown.model_id}: {breakdown.review_count} review(s), "
                f"total ${breakdown.total_usd:.6f}, mean ${breakdown.mean_usd:.6f}, "
                f"median ${breakdown.median_usd:.6f}, p95 ${breakdown.p95_usd:.6f}"
            )
    else:
        lines.append("(no reviews recorded yet)")
    return "\n".join(lines)


def review_cost_query_sql(
    schema: str = "cortex_hosted",
    *,
    since: bool = False,
    repo: bool = False,
) -> str:
    """Return the SELECT over the internal cost ledger, optionally filtered.

    Filters are bound parameters, never string-interpolated values; only the
    presence of each WHERE clause varies with the flags.
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


@click.command("cost-report", context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--since",
    "since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Only include reviews on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--repo",
    "repo",
    default=None,
    help="Only include reviews for this repo (owner/name).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON.",
)
def cost_report_command(
    *, since: datetime | None, repo: str | None, as_json: bool
) -> None:
    """Operator-internal review cost report (provider dollars, NOT customer credits).

    Aggregates the internal review cost ledger
    (``cortex_hosted.review_cost_records``) reached via ``DATABASE_URL`` into
    total reviews, total USD, and mean / median / p95 USD per review, broken
    down by model. This is the internal cost-understanding tool — separate from
    the customer-facing credits meter (docs/HOSTED-PRICING.md). Do not expose
    its output to a customer surface.
    """

    import os

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        click.echo(
            "error: DATABASE_URL is not set; cost-report reads the internal "
            "review cost ledger from the hosted Postgres",
            err=True,
        )
        sys.exit(1)

    params: dict[str, Any] = {}
    if since is not None:
        params["since"] = since.date() if isinstance(since, datetime) else since
    if repo is not None:
        cleaned_repo = repo.strip()
        if not cleaned_repo:
            raise click.BadParameter("--repo must not be blank", param_hint="--repo")
        params["repo"] = cleaned_repo

    query = review_cost_query_sql(since=since is not None, repo=repo is not None)
    try:
        connection = connect(dsn)
        import psycopg

        try:
            cursor = connection.execute(query, params)
            column_names = [description[0] for description in cursor.description or ()]
            rows = [dict(zip(column_names, row, strict=True)) for row in cursor.fetchall()]
        except psycopg.Error as exc:
            raise CostReportError(f"internal cost ledger query failed: {exc}") from exc
        finally:
            connection.close()
    except (HostedDbError, CostReportError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    # Postgres numeric comes back as Decimal; the aggregation expects floats.
    normalized = [{"model_id": row["model_id"], "usd": float(row["usd"])} for row in rows]
    report = aggregate_cost_rows(normalized)
    if as_json:
        import json

        click.echo(json.dumps(report.as_payload(), sort_keys=True))
        return
    click.echo(render_cost_report(report))
