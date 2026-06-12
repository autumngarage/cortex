"""Scheduled reaction sweep over recently-reviewed PRs (cortex#393).

GitHub emits no webhook for reactions, so a 👍/👎 on a Compass Review comment
is invisible until something reads the reactions API. Replies arrive via the
``issue_comment`` webhook; reactions need this sweep. Before it existed the
one reaction in the ground-truth corpus was captured by a hand-run poll — a
flywheel that starves the moment nobody remembers to turn the crank.

The sweep is deliberately bounded and derived from what already exists:

- **Discovery is the job queue, not a new table.** Recently *succeeded*
  ``github.pull_request`` jobs carry the installation/owner/repo/PR identity
  in their payloads — exactly the PRs that can have a Compass comment. The
  sweep re-reads those payloads with the same fail-closed parser the
  reviewer uses, deduplicates to the newest job per PR, and caps the target
  list (cap exceeded → logged, never silent).
- **Capture is the existing poll.** Per target it resolves the latest
  Compass comment by marker (:func:`find_cortex_review_comment`) and feeds
  :func:`poll_comment_reactions`, which is idempotent per
  ``(comment_id, actor, content)`` — re-sweeping never double-counts.
- **Tenant follows the feedback corpus convention** (the env-mapped tenant
  the reply path uses), NOT the per-repo deterministic tenant — the corpus
  must stay internally consistent until cortex#572 unifies tenant identity.

One target failing (API error, missing comment) never aborts the sweep:
each outcome is counted and the summary is one structured log line.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cortex.hosted.db import HostedConnection
from cortex.hosted.review_feedback_capture import (
    FeedbackGithubClient,
    find_cortex_review_comment,
    poll_comment_reactions,
)
from cortex.hosted.stateless_review import (
    StatelessReviewError,
    parse_pull_request_payload,
)

logger = logging.getLogger("cortex.hosted.reaction_sweep")

DEFAULT_SWEEP_WINDOW_HOURS = 48
DEFAULT_SWEEP_TARGET_CAP = 50


class ReactionSweepError(ValueError):
    """Raised when the sweep is configured or fed something invalid."""


def _log(event: str, **fields: Any) -> None:
    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


@dataclass(frozen=True)
class SweepTarget:
    """One recently-reviewed PR the sweep should poll reactions for."""

    installation_id: str
    owner: str
    repo: str
    pr_number: int

    @property
    def repo_full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


def sweep_jobs_query_sql(schema: str = "cortex_hosted") -> str:
    """Return the SELECT for recent succeeded review jobs, newest first.

    Bound parameters only: ``since`` (timestamptz lower bound on
    ``finished_at``) and ``cap`` (LIMIT). Newest-first means the per-PR dedupe
    in :func:`discover_sweep_targets` keeps the latest job for a PR — the one
    whose comment marker reflects the current review state.
    """

    if not schema.replace("_", "").isalnum() or schema[0].isdigit():
        raise ReactionSweepError(f"invalid SQL identifier: {schema!r}")
    return (
        "SELECT payload\n"
        f"FROM {schema}.jobs\n"
        "WHERE job_type = 'github.pull_request'\n"
        "  AND status = 'succeeded'\n"
        "  AND finished_at >= %(since)s\n"
        "ORDER BY finished_at DESC\n"
        "LIMIT %(cap)s"
    )


def discover_sweep_targets(
    conn: HostedConnection,
    *,
    window_hours: int = DEFAULT_SWEEP_WINDOW_HOURS,
    cap: int = DEFAULT_SWEEP_TARGET_CAP,
    now: datetime | None = None,
) -> tuple[SweepTarget, ...]:
    """Derive the sweep's PR targets from recently succeeded review jobs.

    Payloads that fail the reviewer's fail-closed parse are skipped with a
    count (they could never have produced a comment); duplicate PRs collapse
    to their newest job. Hitting the cap is logged — a bounded sweep is fine,
    a silently-truncated one is not.
    """

    if window_hours <= 0:
        raise ReactionSweepError(f"window_hours must be > 0, got {window_hours}")
    if cap <= 0:
        raise ReactionSweepError(f"cap must be > 0, got {cap}")
    when = now or datetime.now(UTC)
    rows = conn.execute(
        sweep_jobs_query_sql(),
        {"since": when - timedelta(hours=window_hours), "cap": cap},
    ).fetchall()

    targets: list[SweepTarget] = []
    seen: set[tuple[str, int]] = set()
    skipped_unparseable = 0
    for (payload,) in rows:
        body = payload if isinstance(payload, Mapping) else json.loads(payload)
        try:
            event = parse_pull_request_payload(body)
        except (StatelessReviewError, TypeError, ValueError):
            skipped_unparseable += 1
            continue
        key = (f"{event.owner}/{event.repo}", event.pr_number)
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            SweepTarget(
                installation_id=event.installation_id,
                owner=event.owner,
                repo=event.repo,
                pr_number=event.pr_number,
            )
        )
    if skipped_unparseable:
        _log("feedback.reaction_sweep_unparseable_jobs", skipped=skipped_unparseable)
    if len(rows) >= cap:
        _log(
            "feedback.reaction_sweep_capped",
            cap=cap,
            note="older reviewed PRs in the window were not swept this round",
        )
    return tuple(targets)


def run_reaction_sweep(
    conn: HostedConnection,
    client_factory: Callable[[str], FeedbackGithubClient],
    *,
    tenant_id: str,
    window_hours: int = DEFAULT_SWEEP_WINDOW_HOURS,
    cap: int = DEFAULT_SWEEP_TARGET_CAP,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Sweep reactions for every recently-reviewed PR into the corpus.

    Per-target failures are counted and logged, never fatal to the sweep;
    a PR without a Compass comment is the visible ``no_comment`` outcome.
    Inserts are committed once at the end — a crashed sweep re-runs cleanly
    thanks to the per-reaction idempotency keys.
    """

    if not tenant_id.strip():
        raise ReactionSweepError("tenant_id must be a non-empty string")
    targets = discover_sweep_targets(conn, window_hours=window_hours, cap=cap, now=now)
    summary: dict[str, Any] = {
        "targets": len(targets),
        "polled": 0,
        "recorded": 0,
        "duplicates": 0,
        "no_comment": 0,
        "errors": 0,
    }
    for target in targets:
        try:
            client = client_factory(target.installation_id)
            review = find_cortex_review_comment(
                client,
                owner=target.owner,
                repo=target.repo,
                pr_number=target.pr_number,
            )
            if review is None:
                summary["no_comment"] += 1
                continue
            outcome = poll_comment_reactions(
                client,
                conn=conn,
                tenant_id=tenant_id,
                review=review,
                repo_full_name=target.repo_full_name,
                occurred_at=now,
            )
            summary["polled"] += 1
            summary["recorded"] += int(outcome.get("recorded", 0))
            summary["duplicates"] += int(outcome.get("duplicates", 0))
        except Exception as exc:
            summary["errors"] += 1
            _log(
                "feedback.reaction_sweep_target_failed",
                repo=target.repo_full_name,
                pr=target.pr_number,
                error=f"{type(exc).__name__}: {exc}",
            )
    conn.commit()
    _log("feedback.reaction_sweep", **summary)
    return summary
