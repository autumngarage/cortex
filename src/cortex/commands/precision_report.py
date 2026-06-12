"""`cortex precision-report` — OPERATOR-INTERNAL advisory success rate (cortex#395).

Aggregates the human-ground-truth feedback corpus
(``cortex_hosted.review_feedback_events``, written by the hosted worker and
the reaction poll) into the advisory success-rate shape the flywheel turns
on: positive vs negative human signals, overall and broken down by replay
regime (model + prompt version), finding class, and repo.

Three boundaries are load-bearing and stated in the report itself:

- **Staged demo traffic is excluded by default** (cortex#575). Fixture PRs
  live in the ``review_staged_prs`` registry; their feedback is a planted
  data regime that must never read as product precision. Exclusion is
  visible (an excluded count, never a silent filter); ``--include-staged``
  opts back in for demo walkthroughs.
- **Absence is never approval.** Precision is computed over human-SCORED
  feedback only (sentiment positive/negative). Unclassified replies are
  counted and named as unscored, pending the cortex#549/#380 classifiers —
  they are neither successes nor failures.
- **This corpus does not know how many findings were emitted.** Findings
  live in posted comments and job results, not in a findings table, so
  feedback-per-finding coverage is a declared gap, not a fabricated ratio.

The aggregation (:func:`aggregate_feedback_rows`) is a pure function over
plain row dicts so it is fully testable offline; the click command only adds
the ``DATABASE_URL`` query and the rendering.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import click

from cortex.hosted.db import HostedDbError, connect

INTERNAL_PRECISION_HEADER = (
    "Internal operator precision report — human feedback ground truth, "
    "staged demo traffic excluded by default. Do not expose."
)
"""The load-bearing header line. The internal/customer and staged/organic
boundaries are stated in the report itself, not just in code comments."""

PRECISION_GAP_NOTE = (
    "precision is computed over human-scored feedback only; findings with no "
    "feedback are not counted (absence is never approval), and "
    "total-findings-emitted lives in job results, not this corpus"
)
"""Declared coverage gap (mirrors ops-report's COVERAGE_GAP_NOTE discipline):
the denominator this report cannot know is named, never fabricated."""

_COMMENT_LEVEL_CLASS = "(comment-level)"
_SCORED_SENTIMENTS = frozenset({"positive", "negative"})
_KNOWN_SENTIMENTS = frozenset({"positive", "negative", "neutral", "unclassified"})


class PrecisionReportError(ValueError):
    """Raised when the precision report cannot be built from its inputs."""


@dataclass(frozen=True)
class PrecisionBucket:
    """Signal counts and the success rate for one grouping key.

    ``precision`` is ``None`` (not 0.0) when no scored signal exists — an
    unmeasured bucket must never read as a failing one.
    """

    key: str
    positive: int
    negative: int
    neutral: int
    unscored: int

    @property
    def precision(self) -> float | None:
        scored = self.positive + self.negative
        if scored == 0:
            return None
        return self.positive / scored

    def as_payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "positive": self.positive,
            "negative": self.negative,
            "neutral": self.neutral,
            "unscored": self.unscored,
            "precision": self.precision,
        }


@dataclass(frozen=True)
class PrecisionReport:
    """The full internal precision report over the feedback corpus."""

    overall: PrecisionBucket
    by_regime: tuple[PrecisionBucket, ...]
    by_finding_class: tuple[PrecisionBucket, ...]
    by_repo: tuple[PrecisionBucket, ...]
    staged_excluded: int
    staged_included: int

    def as_payload(self) -> dict[str, Any]:
        return {
            "header": INTERNAL_PRECISION_HEADER,
            "gap_note": PRECISION_GAP_NOTE,
            "overall": self.overall.as_payload(),
            "by_regime": [bucket.as_payload() for bucket in self.by_regime],
            "by_finding_class": [bucket.as_payload() for bucket in self.by_finding_class],
            "by_repo": [bucket.as_payload() for bucket in self.by_repo],
            "staged_excluded": self.staged_excluded,
            "staged_included": self.staged_included,
        }


@dataclass
class _Tally:
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    unscored: int = 0

    def add(self, sentiment: str) -> None:
        if sentiment == "positive":
            self.positive += 1
        elif sentiment == "negative":
            self.negative += 1
        elif sentiment == "neutral":
            self.neutral += 1
        else:  # unclassified — validated upstream
            self.unscored += 1

    def bucket(self, key: str) -> PrecisionBucket:
        return PrecisionBucket(
            key=key,
            positive=self.positive,
            negative=self.negative,
            neutral=self.neutral,
            unscored=self.unscored,
        )


def aggregate_feedback_rows(
    rows: Sequence[Mapping[str, Any]], *, include_staged: bool = False
) -> PrecisionReport:
    """Aggregate feedback rows into the precision report (pure, offline-testable).

    Each row must carry ``sentiment``, ``model_id``, ``prompt_version``,
    ``finding_class`` (may be ``None``), ``repo_full_name``, and ``staged``
    (bool, from the registry JOIN). Sentiment is the authoritative signal —
    capture already maps reactions (up → positive, down → negative) and
    leaves replies ``unclassified`` for the late classifier. Staged rows are
    excluded from every metric unless ``include_staged``; either way the
    staged count is visible in the report. An empty input yields an all-zero
    report — "no feedback yet" is a valid, visible answer.
    """

    overall = _Tally()
    by_regime: dict[str, _Tally] = {}
    by_class: dict[str, _Tally] = {}
    by_repo: dict[str, _Tally] = {}
    staged_seen = 0

    for index, row in enumerate(rows):
        staged = row.get("staged")
        if isinstance(staged, bool) is False:
            raise PrecisionReportError(f"row {index}: staged must be a boolean, got {staged!r}")
        sentiment = row.get("sentiment")
        if not isinstance(sentiment, str) or sentiment not in _KNOWN_SENTIMENTS:
            raise PrecisionReportError(
                f"row {index}: sentiment must be one of {sorted(_KNOWN_SENTIMENTS)}, "
                f"got {sentiment!r}"
            )
        model_id = row.get("model_id")
        prompt_version = row.get("prompt_version")
        repo_full_name = row.get("repo_full_name")
        for name, value in (
            ("model_id", model_id),
            ("prompt_version", prompt_version),
            ("repo_full_name", repo_full_name),
        ):
            if not isinstance(value, str) or not value.strip():
                raise PrecisionReportError(
                    f"row {index}: {name} must be a non-empty string, got {value!r}"
                )
        finding_class = row.get("finding_class")
        if finding_class is not None and not isinstance(finding_class, str):
            raise PrecisionReportError(
                f"row {index}: finding_class must be a string or None, got {finding_class!r}"
            )

        if staged:
            staged_seen += 1
            if not include_staged:
                continue

        overall.add(sentiment)
        by_regime.setdefault(f"{model_id} {prompt_version}", _Tally()).add(sentiment)
        by_class.setdefault(finding_class or _COMMENT_LEVEL_CLASS, _Tally()).add(sentiment)
        by_repo.setdefault(str(repo_full_name), _Tally()).add(sentiment)

    return PrecisionReport(
        overall=overall.bucket("overall"),
        by_regime=tuple(tally.bucket(key) for key, tally in sorted(by_regime.items())),
        by_finding_class=tuple(tally.bucket(key) for key, tally in sorted(by_class.items())),
        by_repo=tuple(tally.bucket(key) for key, tally in sorted(by_repo.items())),
        staged_excluded=0 if include_staged else staged_seen,
        staged_included=staged_seen if include_staged else 0,
    )


def _format_precision(precision: float | None) -> str:
    return "n/a (no scored feedback)" if precision is None else f"{precision:.0%}"


def _bucket_line(bucket: PrecisionBucket) -> str:
    return (
        f"  {bucket.key}: precision {_format_precision(bucket.precision)} "
        f"({bucket.positive} positive / {bucket.negative} negative; "
        f"{bucket.neutral} neutral, {bucket.unscored} unscored)"
    )


def render_precision_report(report: PrecisionReport) -> str:
    """Render the internal precision report as plain text, header first."""

    overall = report.overall
    lines = [
        INTERNAL_PRECISION_HEADER,
        "",
        f"scored feedback events: {overall.positive + overall.negative}",
        f"overall precision: {_format_precision(overall.precision)} "
        f"({overall.positive} positive / {overall.negative} negative)",
        f"neutral: {overall.neutral}   unscored (pending classification): {overall.unscored}",
        f"staged demo events excluded: {report.staged_excluded}"
        + (f" (included: {report.staged_included})" if report.staged_included else ""),
        "",
        f"note: {PRECISION_GAP_NOTE}",
    ]
    for title, buckets in (
        ("by regime (model + prompt version):", report.by_regime),
        ("by finding class:", report.by_finding_class),
        ("by repo:", report.by_repo),
    ):
        if buckets:
            lines.extend(["", title])
            lines.extend(_bucket_line(bucket) for bucket in buckets)
    if not (report.by_regime or report.staged_excluded or report.staged_included):
        lines.extend(["", "(no feedback recorded yet)"])
    return "\n".join(lines)


def feedback_query_sql(
    schema: str = "cortex_hosted",
    *,
    since: bool = False,
    repo: bool = False,
) -> str:
    """Return the SELECT over the feedback corpus joined to the staged registry.

    The LEFT JOIN surfaces staged membership as a boolean column so exclusion
    happens in one visible place (the aggregation), never silently in SQL.
    Filters are bound parameters; only the presence of each WHERE clause
    varies with the flags.
    """

    from cortex.hosted.schema import _validate_sql_identifier

    _validate_sql_identifier(schema)
    clauses: list[str] = []
    if since:
        clauses.append("f.occurred_at >= %(since)s")
    if repo:
        clauses.append("f.repo_full_name = %(repo)s")
    where = f"\nWHERE {' AND '.join(clauses)}" if clauses else ""
    return (
        "SELECT f.sentiment, f.model_id, f.prompt_version, f.finding_class, "
        "f.repo_full_name, (s.staged_pr_id IS NOT NULL) AS staged\n"
        f"FROM {schema}.review_feedback_events AS f\n"
        f"LEFT JOIN {schema}.review_staged_prs AS s\n"
        "    ON s.tenant_id = f.tenant_id\n"
        "    AND s.repo_full_name = f.repo_full_name\n"
        "    AND s.pr_number = f.pr_number"
        f"{where}\n"
        "ORDER BY f.occurred_at"
    ).strip()


@click.command(
    "precision-report", context_settings={"help_option_names": ["-h", "--help"]}
)
@click.option(
    "--since",
    "since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help="Only include feedback on or after this date (YYYY-MM-DD).",
)
@click.option(
    "--repo",
    "repo",
    default=None,
    help="Only include feedback for this repo (owner/name).",
)
@click.option(
    "--include-staged",
    "include_staged",
    is_flag=True,
    default=False,
    help="Include staged demo-fixture traffic (cortex#575) in the metrics.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON.",
)
def precision_report_command(
    *,
    since: datetime | None,
    repo: str | None,
    include_staged: bool,
    as_json: bool,
) -> None:
    """Operator-internal advisory precision report (human feedback ground truth).

    Aggregates the feedback corpus (``cortex_hosted.review_feedback_events``)
    reached via ``DATABASE_URL`` into positive/negative signal counts and the
    advisory success rate, broken down by replay regime, finding class, and
    repo. Staged demo traffic (``review_staged_prs``, cortex#575) is excluded
    by default with a visible count. Do not expose to a customer surface.
    """

    import os

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        click.echo(
            "error: DATABASE_URL is not set; precision-report reads the "
            "feedback corpus from the hosted Postgres",
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

    query = feedback_query_sql(since=since is not None, repo=repo is not None)
    try:
        connection = connect(dsn)
        import psycopg

        try:
            cursor = connection.execute(query, params)
            column_names = [description[0] for description in cursor.description or ()]
            rows = [dict(zip(column_names, row, strict=True)) for row in cursor.fetchall()]
        except psycopg.Error as exc:
            raise PrecisionReportError(f"feedback corpus query failed: {exc}") from exc
        finally:
            connection.close()
    except (HostedDbError, PrecisionReportError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    try:
        report = aggregate_feedback_rows(rows, include_staged=include_staged)
    except PrecisionReportError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    if as_json:
        import json

        click.echo(json.dumps(report.as_payload(), sort_keys=True))
        return
    click.echo(render_precision_report(report))
