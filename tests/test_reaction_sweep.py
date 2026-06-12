"""Scheduled reaction sweep tests (cortex#393).

Invariants under test: discovery is bounded and derived from succeeded
review jobs (dedupe to newest per PR, caps logged, never silent); one
failing target never aborts the sweep; re-sweeping is idempotent via the
per-reaction keys; the worker's queue outlives a crashing sweep; and every
disabled precondition is named, never silent.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from cortex.hosted.github_app_auth import CommentReaction
from cortex.hosted.github_comment import make_marker, make_replay_marker
from cortex.hosted.reaction_sweep import (
    ReactionSweepError,
    discover_sweep_targets,
    run_reaction_sweep,
    sweep_jobs_query_sql,
)

_TENANT = str(uuid4())
_APP_LOGIN = "compass-review[bot]"


def _job_payload(*, owner: str = "acme", repo: str = "widgets", pr: int = 7) -> dict[str, Any]:
    return {
        "event": "pull_request",
        "body": {
            "action": "opened",
            "installation": {"id": 424242},
            "repository": {"name": repo, "owner": {"login": owner}},
            "pull_request": {
                "number": pr,
                "base": {"sha": "1111111"},
                "head": {"sha": "2222222"},
            },
        },
    }


def _review_comment_body(pr: int = 7) -> str:
    return "\n".join(
        [
            make_marker(pr, "2222222"),
            make_replay_marker(
                model_id="anthropic/claude-sonnet-4-6",
                prompt_version="review-evaluate/v1",
                snapshot_hash="a" * 64,
            ),
            "### Cortex reviewed this PR",
        ]
    )


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _SweepDb:
    """Fake DB serving the jobs discovery query and feedback inserts."""

    def __init__(
        self, payloads: list[Mapping[str, Any]], *, fail_feedback_inserts: bool = False
    ) -> None:
        self._payloads = payloads
        self._fail_feedback_inserts = fail_feedback_inserts
        self.feedback_keys: set[str] = set()
        self.commits = 0
        self.rollbacks = 0

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> _Cursor:
        q = query.strip()
        p = dict(params or {})
        if q.startswith("SELECT payload"):
            assert "job_type = 'github.pull_request'" in q
            payloads = self._payloads
            if "cap" in p:
                payloads = payloads[: int(p["cap"])]
            return _Cursor([(payload,) for payload in payloads])
        if q.startswith("INSERT INTO cortex_hosted.review_feedback_events"):
            if self._fail_feedback_inserts:
                raise RuntimeError("database insert failed")
            key = str(p["idempotency_key"])
            if key in self.feedback_keys:
                return _Cursor([])
            self.feedback_keys.add(key)
            return _Cursor([(str(uuid4()),)])
        raise AssertionError(f"unexpected SQL: {q[:70]}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


class _SweepClient:
    """Fake GitHub client: comments per (owner/repo, pr), reactions per comment."""

    def __init__(
        self,
        comments: tuple[Mapping[str, Any], ...] = (),
        reactions: tuple[CommentReaction, ...] = (),
        raise_on_comments: bool = False,
    ) -> None:
        self._comments = comments
        self._reactions = reactions
        self._raise = raise_on_comments

    def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[Mapping[str, Any], ...]:
        if self._raise:
            raise RuntimeError("github unavailable")
        return self._comments

    def list_comment_reactions(
        self, owner: str, repo: str, comment_id: int
    ) -> tuple[CommentReaction, ...]:
        return self._reactions


def _cortex_comment(pr: int = 7, comment_id: int = 9001) -> dict[str, Any]:
    return {
        "id": comment_id,
        "body": _review_comment_body(pr),
        "user": {"login": _APP_LOGIN},
    }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discovery_dedupes_to_newest_job_per_pr_and_parses_identity() -> None:
    db = _SweepDb(
        [
            _job_payload(pr=7),  # newest first (ORDER BY finished_at DESC)
            _job_payload(pr=7),  # older job for the same PR — collapsed
            _job_payload(pr=8),
        ]
    )
    targets = discover_sweep_targets(db, now=datetime.now(UTC))
    assert [(t.repo_full_name, t.pr_number) for t in targets] == [
        ("acme/widgets", 7),
        ("acme/widgets", 8),
    ]
    assert targets[0].installation_id == "424242"


def test_discovery_caps_unique_valid_targets_after_dedupe() -> None:
    db = _SweepDb(
        [
            _job_payload(pr=7),  # newest duplicate must not consume target capacity
            _job_payload(pr=7),
            _job_payload(pr=8),
        ]
    )
    targets = discover_sweep_targets(db, cap=2, now=datetime.now(UTC))
    assert [(t.repo_full_name, t.pr_number) for t in targets] == [
        ("acme/widgets", 7),
        ("acme/widgets", 8),
    ]


def test_discovery_skips_unparseable_payloads_visibly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _SweepDb([{"event": "pull_request", "body": {"junk": True}}, _job_payload(pr=9)])
    with caplog.at_level(logging.INFO, logger="cortex.hosted.reaction_sweep"):
        targets = discover_sweep_targets(db, now=datetime.now(UTC))
    assert [t.pr_number for t in targets] == [9]
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "feedback.reaction_sweep_unparseable_jobs" in events


def test_discovery_logs_when_capped(caplog: pytest.LogCaptureFixture) -> None:
    db = _SweepDb([_job_payload(pr=n) for n in range(1, 6)])
    with caplog.at_level(logging.INFO, logger="cortex.hosted.reaction_sweep"):
        targets = discover_sweep_targets(db, cap=3, now=datetime.now(UTC))
    assert len(targets) == 3
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "feedback.reaction_sweep_capped" in events


def test_discovery_rejects_invalid_bounds() -> None:
    db = _SweepDb([])
    with pytest.raises(ReactionSweepError, match="window_hours"):
        discover_sweep_targets(db, window_hours=0)
    with pytest.raises(ReactionSweepError, match="cap"):
        discover_sweep_targets(db, cap=0)
    with pytest.raises(ReactionSweepError, match="identifier"):
        sweep_jobs_query_sql("drop table;--")


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


def test_sweep_records_reactions_and_is_idempotent_on_resweep() -> None:
    db = _SweepDb([_job_payload(pr=7)])
    client = _SweepClient(
        comments=(_cortex_comment(pr=7),),
        reactions=(
            CommentReaction(content="+1", user_login="alice"),
            CommentReaction(content="-1", user_login="bob"),
            CommentReaction(content="+1", user_login=_APP_LOGIN),  # self — skipped
        ),
    )
    summary = run_reaction_sweep(
        db, lambda _installation: client, tenant_id=_TENANT, now=datetime.now(UTC)
    )
    assert summary["targets"] == 1
    assert summary["polled"] == 1
    assert summary["recorded"] == 2  # alice + bob; the App's own reaction never lands
    assert db.commits == 1
    resweep = run_reaction_sweep(
        db, lambda _installation: client, tenant_id=_TENANT, now=datetime.now(UTC)
    )
    assert resweep["recorded"] == 0
    assert resweep["duplicates"] == 2  # idempotency keys collapsed the re-poll


def test_sweep_counts_prs_without_a_cortex_comment() -> None:
    db = _SweepDb([_job_payload(pr=7)])
    client = _SweepClient(comments=())  # no Compass comment on the PR
    summary = run_reaction_sweep(db, lambda _i: client, tenant_id=_TENANT)
    assert summary["no_comment"] == 1
    assert summary["polled"] == 0


def test_one_failing_target_never_aborts_the_sweep(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _SweepDb([_job_payload(pr=7), _job_payload(pr=8)])
    healthy = _SweepClient(
        comments=(_cortex_comment(pr=8),),
        reactions=(CommentReaction(content="+1", user_login="alice"),),
    )
    broken = _SweepClient(raise_on_comments=True)
    clients = iter([broken, healthy])
    with caplog.at_level(logging.INFO, logger="cortex.hosted.reaction_sweep"):
        summary = run_reaction_sweep(db, lambda _i: next(clients), tenant_id=_TENANT)
    assert summary["errors"] == 1
    assert summary["recorded"] == 1  # the healthy PR still swept
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "feedback.reaction_sweep_target_failed" in events


def test_feedback_insert_failure_aborts_sweep_without_commit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _SweepDb([_job_payload(pr=7)], fail_feedback_inserts=True)
    client = _SweepClient(
        comments=(_cortex_comment(pr=7),),
        reactions=(CommentReaction(content="+1", user_login="alice"),),
    )
    with (
        caplog.at_level(logging.INFO, logger="cortex.hosted.reaction_sweep"),
        pytest.raises(RuntimeError, match="database insert failed"),
    ):
        run_reaction_sweep(db, lambda _installation: client, tenant_id=_TENANT)
    assert db.commits == 0
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "feedback.reaction_sweep_target_failed" not in events
    assert "feedback.reaction_sweep" not in events


def test_sweep_rejects_blank_tenant() -> None:
    with pytest.raises(ReactionSweepError, match="tenant_id"):
        run_reaction_sweep(_SweepDb([]), lambda _i: _SweepClient(), tenant_id=" ")


# ---------------------------------------------------------------------------
# Worker scheduling
# ---------------------------------------------------------------------------


def _idle_worker_db() -> Any:
    class _IdleDb:
        def execute(self, query: str, params: Mapping[str, Any] | None = None) -> _Cursor:
            return _Cursor([])

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    return _IdleDb()


def test_worker_runs_sweep_only_when_due() -> None:
    from cortex.hosted.worker import HandlerRegistry, Worker

    clock = {"now": 0.0}
    runs: list[float] = []

    def record_sweep() -> Mapping[str, Any]:
        runs.append(clock["now"])
        return {}

    worker = Worker(
        conn=_idle_worker_db(),
        registry=HandlerRegistry(),
        worker_id="w-test",
        reaction_sweep=record_sweep,
        reaction_sweep_seconds=900.0,
        monotonic=lambda: clock["now"],
    )
    assert worker.maybe_run_reaction_sweep() is True  # first tick always runs
    assert worker.maybe_run_reaction_sweep() is False  # not due yet
    clock["now"] = 899.0
    assert worker.maybe_run_reaction_sweep() is False
    clock["now"] = 901.0
    assert worker.maybe_run_reaction_sweep() is True
    assert runs == [0.0, 901.0]


def test_worker_survives_a_crashing_sweep(caplog: pytest.LogCaptureFixture) -> None:
    from cortex.hosted.worker import HandlerRegistry, Worker

    def explode() -> Mapping[str, Any]:
        raise RuntimeError("github melted")

    worker = Worker(
        conn=_idle_worker_db(),
        registry=HandlerRegistry(),
        worker_id="w-test",
        reaction_sweep=explode,
        reaction_sweep_seconds=900.0,
        monotonic=lambda: 0.0,
    )
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert worker.maybe_run_reaction_sweep() is True  # attempted, survived
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "feedback.reaction_sweep_failed" in events


def test_worker_without_sweep_is_a_noop() -> None:
    from cortex.hosted.worker import HandlerRegistry, Worker

    worker = Worker(conn=_idle_worker_db(), registry=HandlerRegistry(), worker_id="w")
    assert worker.maybe_run_reaction_sweep() is False


# ---------------------------------------------------------------------------
# Env wiring
# ---------------------------------------------------------------------------


def test_build_reaction_sweep_names_every_disabled_precondition(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from cortex.hosted.worker import ArrivalRecorder, build_reaction_sweep

    recorder = ArrivalRecorder(conn=_idle_worker_db(), tenant_id=_TENANT, source_id=str(uuid4()))
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert (
            build_reaction_sweep(
                recorder=recorder, environ={"CORTEX_REACTION_POLL_SECONDS": "0"}
            )
            is None
        )
        assert build_reaction_sweep(recorder=recorder, environ={}) is None  # no app creds
        no_tenant = ArrivalRecorder(conn=_idle_worker_db(), tenant_id=None, source_id=None)
        assert (
            build_reaction_sweep(
                recorder=no_tenant,
                environ={"GITHUB_APP_ID": "1", "GITHUB_APP_PRIVATE_KEY": "x"},
            )
            is None
        )
    reasons = [
        json.loads(rec.message).get("reason")
        for rec in caplog.records
        if json.loads(rec.message).get("event") == "worker.reaction_sweep_disabled"
    ]
    assert reasons == ["interval_zero", "github_app_unconfigured", "tenant_unconfigured"]


def test_build_reaction_sweep_rejects_malformed_interval() -> None:
    from cortex.hosted.api.config import ServiceConfigError
    from cortex.hosted.worker import ArrivalRecorder, build_reaction_sweep

    recorder = ArrivalRecorder(conn=_idle_worker_db(), tenant_id=_TENANT, source_id=str(uuid4()))
    with pytest.raises(ServiceConfigError, match="CORTEX_REACTION_POLL_SECONDS"):
        build_reaction_sweep(
            recorder=recorder, environ={"CORTEX_REACTION_POLL_SECONDS": "soon"}
        )
