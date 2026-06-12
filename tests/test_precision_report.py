"""Precision-report tests (cortex#395).

Invariants under test: sentiment is the authoritative signal (absence is
never approval — unscored feedback is named, not counted); staged demo
traffic (cortex#575) is excluded by default with a VISIBLE count, never a
silent filter; an unmeasured bucket reports precision None, never 0.0.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from click.testing import CliRunner

from cortex.commands.precision_report import (
    INTERNAL_PRECISION_HEADER,
    PRECISION_GAP_NOTE,
    PrecisionReportError,
    aggregate_feedback_rows,
    feedback_query_sql,
    precision_report_command,
    render_precision_report,
)


def _row(
    *,
    sentiment: str = "positive",
    model_id: str = "anthropic/claude-sonnet-4-6",
    prompt_version: str = "review-evaluate/v1",
    finding_class: str | None = "contradicts-prior-decision",
    repo: str = "acme/widgets",
    staged: bool = False,
) -> dict[str, Any]:
    return {
        "sentiment": sentiment,
        "model_id": model_id,
        "prompt_version": prompt_version,
        "finding_class": finding_class,
        "repo_full_name": repo,
        "staged": staged,
    }


# ---------------------------------------------------------------------------
# Signal semantics
# ---------------------------------------------------------------------------


def test_precision_is_positive_over_scored_only() -> None:
    rows = [
        _row(sentiment="positive"),
        _row(sentiment="positive"),
        _row(sentiment="positive"),
        _row(sentiment="negative"),
        _row(sentiment="neutral"),
        _row(sentiment="unclassified"),  # pending #549/#380 — neither success nor failure
    ]
    report = aggregate_feedback_rows(rows)
    assert report.overall.positive == 3
    assert report.overall.negative == 1
    assert report.overall.neutral == 1
    assert report.overall.unscored == 1
    assert report.overall.precision == pytest.approx(0.75)


def test_unmeasured_precision_is_none_not_zero() -> None:
    report = aggregate_feedback_rows([_row(sentiment="unclassified")])
    assert report.overall.precision is None
    empty = aggregate_feedback_rows([])
    assert empty.overall.precision is None
    assert empty.overall.positive == 0


def test_groupings_are_sorted_and_comment_level_is_named() -> None:
    rows = [
        _row(model_id="m/b", prompt_version="p/1", finding_class=None, repo="z/last"),
        _row(model_id="m/a", prompt_version="p/1", repo="a/first"),
    ]
    report = aggregate_feedback_rows(rows)
    assert [bucket.key for bucket in report.by_regime] == ["m/a p/1", "m/b p/1"]
    assert "(comment-level)" in [bucket.key for bucket in report.by_finding_class]
    assert [bucket.key for bucket in report.by_repo] == ["a/first", "z/last"]


def test_malformed_rows_are_rejected() -> None:
    with pytest.raises(PrecisionReportError, match="sentiment"):
        aggregate_feedback_rows([_row(sentiment="enthusiastic")])
    with pytest.raises(PrecisionReportError, match="model_id"):
        aggregate_feedback_rows([_row(model_id=" ")])
    with pytest.raises(PrecisionReportError, match="staged"):
        aggregate_feedback_rows([{**_row(), "staged": "yes"}])


# ---------------------------------------------------------------------------
# Staged exclusion: visible, never silent
# ---------------------------------------------------------------------------


def test_staged_rows_are_excluded_by_default_with_visible_count() -> None:
    rows = [
        _row(sentiment="positive", staged=True),  # the planted demo catch
        _row(sentiment="negative", staged=False),  # real organic signal
    ]
    report = aggregate_feedback_rows(rows)
    assert report.staged_excluded == 1
    assert report.staged_included == 0
    # The planted positive must NOT lift precision: only the organic row counts.
    assert report.overall.positive == 0
    assert report.overall.negative == 1
    assert report.overall.precision == pytest.approx(0.0)


def test_include_staged_opts_back_in_and_reports_it() -> None:
    rows = [
        _row(sentiment="positive", staged=True),
        _row(sentiment="negative", staged=False),
    ]
    report = aggregate_feedback_rows(rows, include_staged=True)
    assert report.staged_excluded == 0
    assert report.staged_included == 1
    assert report.overall.positive == 1
    assert report.overall.precision == pytest.approx(0.5)


def test_fixture_only_corpus_reports_zero_precision_data_by_default() -> None:
    # The exact PR #561 shape: every event in the corpus is staged.
    rows = [_row(sentiment="positive", staged=True) for _ in range(3)]
    report = aggregate_feedback_rows(rows)
    assert report.staged_excluded == 3
    assert report.overall.precision is None  # no organic ground truth yet


# ---------------------------------------------------------------------------
# Rendering and payload
# ---------------------------------------------------------------------------


def test_render_states_boundaries_and_exclusions() -> None:
    rows = [
        _row(sentiment="positive"),
        _row(sentiment="positive", staged=True),
    ]
    rendered = render_precision_report(aggregate_feedback_rows(rows))
    assert rendered.splitlines()[0] == INTERNAL_PRECISION_HEADER
    assert PRECISION_GAP_NOTE in rendered
    assert "staged demo events excluded: 1" in rendered
    assert "overall precision: 100%" in rendered


def test_empty_report_renders_visibly() -> None:
    rendered = render_precision_report(aggregate_feedback_rows([]))
    assert "(no feedback recorded yet)" in rendered


def test_payload_is_json_safe_and_round_trips() -> None:
    payload = aggregate_feedback_rows([_row()]).as_payload()
    assert json.loads(json.dumps(payload, sort_keys=True)) == payload
    assert payload["header"] == INTERNAL_PRECISION_HEADER
    assert payload["gap_note"] == PRECISION_GAP_NOTE


# ---------------------------------------------------------------------------
# SQL builder
# ---------------------------------------------------------------------------


def test_query_joins_staged_registry_and_binds_filters() -> None:
    base = feedback_query_sql()
    assert "LEFT JOIN cortex_hosted.review_staged_prs" in base
    assert "(s.staged_pr_id IS NOT NULL) AS staged" in base
    assert "WHERE" not in base
    filtered = feedback_query_sql(since=True, repo=True)
    assert "f.occurred_at >= %(since)s" in filtered
    assert "f.repo_full_name = %(repo)s" in filtered
    with pytest.raises(Exception, match="identifier"):
        feedback_query_sql("drop table;--")


# ---------------------------------------------------------------------------
# Command degradation
# ---------------------------------------------------------------------------


def test_command_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = CliRunner().invoke(precision_report_command, [])
    assert result.exit_code == 1
    assert "DATABASE_URL is not set" in result.output


# ---------------------------------------------------------------------------
# Round-trip over a real Postgres (DATABASE_URL-gated)
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set; live round-trip skipped")
def test_precision_round_trip_excludes_staged_pr_by_default() -> None:
    from cortex.hosted.db import connect
    from cortex.hosted.migrations import apply_schema
    from cortex.hosted.review_feedback import (
        FeedbackKind,
        FeedbackSentiment,
        ReviewFeedbackEvent,
        reaction_idempotency_key,
        review_feedback_insert_sql,
    )
    from cortex.hosted.staged_pr import (
        STAGED_REASON_BACKFILL,
        StagedPrRecord,
        staged_pr_insert_sql,
    )

    tenant = str(uuid4())
    repo = f"test/{uuid4().hex[:8]}"
    snapshot = "a" * 64

    def _event(*, pr_number: int, comment_id: int, kind: FeedbackKind, sentiment: FeedbackSentiment) -> ReviewFeedbackEvent:
        return ReviewFeedbackEvent(
            tenant_id=tenant,
            repo_full_name=repo,
            pr_number=pr_number,
            head_sha="2222222",
            cortex_comment_id=comment_id,
            model_id="anthropic/claude-sonnet-4-6",
            prompt_version="review-evaluate/v1",
            snapshot_hash=snapshot,
            feedback_kind=kind,
            sentiment=sentiment,
            actor_login="tester",
            occurred_at=datetime.now(UTC),
            idempotency_key=reaction_idempotency_key(
                cortex_comment_id=comment_id, actor_login="tester", content="+1"
            ),
        )

    connection = connect(DATABASE_URL)
    try:
        apply_schema(connection)
        # PR 1 is staged (the demo fixture); PR 2 is organic.
        connection.execute(
            staged_pr_insert_sql(),
            StagedPrRecord(
                tenant_id=tenant,
                repo_full_name=repo,
                pr_number=1,
                reason=STAGED_REASON_BACKFILL,
                recorded_at=datetime.now(UTC),
            ).as_insert_parameters(),
        )
        for event in (
            _event(pr_number=1, comment_id=101, kind=FeedbackKind.REACTION_UP, sentiment=FeedbackSentiment.POSITIVE),
            _event(pr_number=2, comment_id=202, kind=FeedbackKind.REACTION_UP, sentiment=FeedbackSentiment.POSITIVE),
        ):
            connection.execute(review_feedback_insert_sql(), event.as_insert_parameters())

        cursor = connection.execute(feedback_query_sql(repo=True), {"repo": repo})
        column_names = [description[0] for description in cursor.description or ()]
        rows = [dict(zip(column_names, row, strict=True)) for row in cursor.fetchall()]

        default_report = aggregate_feedback_rows(rows)
        assert default_report.staged_excluded == 1
        assert default_report.overall.positive == 1  # the organic event only
        included_report = aggregate_feedback_rows(rows, include_staged=True)
        assert included_report.overall.positive == 2
    finally:
        connection.rollback()
        connection.close()
