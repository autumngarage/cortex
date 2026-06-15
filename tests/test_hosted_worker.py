"""Tests for the hosted worker loop (cortex#471).

``FakeQueueDb`` emulates exactly the Postgres slice the worker touches —
the jobs queue DML from ``cortex.hosted.jobs`` plus the idempotent ledger
append — the same in-memory fake-db idiom as ``tests/test_hosted_push.py``.
Claim ordering, attempt accounting, backoff scheduling, dead-lettering,
stale-claim recovery, and the ledger arrival record are exercised for
real, not assumed.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from cortex.hosted.api.config import ServiceConfigError
from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.jobs import HostedJobError, JobRequest
from cortex.hosted.ledger_events import LedgerEventType
from cortex.hosted.worker import (
    ArrivalRecorder,
    HandlerRegistry,
    Worker,
    build_default_registry,
    build_worker_registry,
    install_signal_handlers,
)

TENANT_ID = str(uuid4())
SOURCE_ID = str(uuid4())


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeQueueDb:
    """In-memory emulation of the worker's SQL surface (jobs + ledger)."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        # (tenant_id, idempotency_key) -> event_id, mirroring the ledger
        # UNIQUE (tenant_id, idempotency_key) + ON CONFLICT DO NOTHING.
        self.ledger: dict[tuple[str, str], str] = {}
        self.commits = 0
        self.rollbacks = 0

    def enqueue(self, request: JobRequest) -> str | None:
        if request.idempotency_key in self.jobs:
            return None
        job_id = str(uuid4())
        self.jobs[request.idempotency_key] = {
            "job_id": job_id,
            "job_type": request.job_type,
            "idempotency_key": request.idempotency_key,
            "payload": dict(request.payload),
            "status": "queued",
            "attempts": 0,
            "max_attempts": request.max_attempts,
            "enqueued_at": datetime.now(UTC),
            "next_attempt_at": datetime.now(UTC),
            "claimed_at": None,
            "claimed_by": None,
            "last_error": None,
            "result": None,
        }
        return job_id

    def _by_id(self, job_id: str) -> dict[str, Any] | None:
        for job in self.jobs.values():
            if job["job_id"] == job_id:
                return job
        return None

    def job(self, idempotency_key: str) -> dict[str, Any]:
        return self.jobs[idempotency_key]

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> FakeCursor:
        q = query.strip()
        p = dict(params or {})
        if q.startswith("UPDATE cortex_hosted.jobs") and "FOR UPDATE SKIP LOCKED" in q:
            now = datetime.now(UTC)
            due = [
                job
                for job in self.jobs.values()
                if job["status"] == "queued" and job["next_attempt_at"] <= now
            ]
            if not due:
                return FakeCursor([])
            job = min(due, key=lambda item: (item["next_attempt_at"], item["enqueued_at"]))
            job["status"] = "running"
            job["attempts"] += 1
            job["claimed_at"] = now
            job["claimed_by"] = str(p["claimed_by"])
            return FakeCursor(
                [
                    (
                        job["job_id"],
                        job["job_type"],
                        job["idempotency_key"],
                        json.dumps(job["payload"]),
                        job["attempts"],
                        job["max_attempts"],
                    )
                ]
            )
        if "SET status = 'succeeded'" in q:
            found = self._by_id(str(p["job_id"]))
            if found is None or found["status"] != "running":
                return FakeCursor([])
            found["status"] = "succeeded"
            found["last_error"] = None
            found["result"] = json.loads(str(p["result"]))
            return FakeCursor([(found["job_id"],)])
        if "SET status = 'queued'" in q and "backoff_seconds" in q:
            found = self._by_id(str(p["job_id"]))
            if found is None or found["status"] != "running":
                return FakeCursor([])
            found["status"] = "queued"
            found["next_attempt_at"] = datetime.now(UTC) + timedelta(
                seconds=float(p["backoff_seconds"])
            )
            found["last_error"] = str(p["error"])
            found["claimed_at"] = None
            found["claimed_by"] = None
            return FakeCursor([(found["job_id"],)])
        if "SET status = 'dead'" in q:
            found = self._by_id(str(p["job_id"]))
            if found is None or found["status"] != "running":
                return FakeCursor([])
            found["status"] = "dead"
            found["last_error"] = str(p["error"])
            return FakeCursor([(found["job_id"],)])
        if "claimed_at < now() - make_interval" in q:
            cutoff = datetime.now(UTC) - timedelta(seconds=float(p["stale_after_seconds"]))
            recovered: list[tuple[Any, ...]] = []
            for job in self.jobs.values():
                if (
                    job["status"] == "running"
                    and job["claimed_at"] is not None
                    and job["claimed_at"] < cutoff
                ):
                    job["status"] = "dead" if job["attempts"] >= job["max_attempts"] else "queued"
                    job["last_error"] = str(p["error"])
                    job["claimed_at"] = None
                    job["claimed_by"] = None
                    recovered.append((job["job_id"], job["status"]))
            return FakeCursor(recovered)
        if q.startswith("INSERT INTO cortex_hosted.ledger_events"):
            key = (str(p["tenant_id"]), str(p["idempotency_key"]))
            if key in self.ledger:
                return FakeCursor([])
            event_id = str(uuid4())
            self.ledger[key] = event_id
            return FakeCursor([(event_id, str(p["event_hash"]))])
        if q.startswith("SELECT") and "FROM cortex_hosted.review_rollout_events" in q:
            return FakeCursor([])
        raise AssertionError(f"FakeQueueDb saw unexpected SQL: {q[:80]}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


def _webhook_job_request(
    delivery: str = "guid-1", event: str = "pull_request", max_attempts: int = 3
) -> JobRequest:
    return JobRequest(
        job_type=f"github.{event}",
        idempotency_key=f"github-delivery:{delivery}",
        payload={
            "event": event,
            "delivery": delivery,
            "received_at": "2026-06-10T12:00:00+00:00",
            "body": {"action": "opened", "repository": {"full_name": "autumngarage/cortex"}},
        },
        max_attempts=max_attempts,
    )


def _review_webhook_job_request(delivery: str = "guid-review") -> JobRequest:
    return JobRequest(
        job_type="github.pull_request",
        idempotency_key=f"github-delivery:{delivery}",
        payload={
            "event": "pull_request",
            "delivery": delivery,
            "received_at": "2026-06-10T12:00:00+00:00",
            "body": {
                "action": "opened",
                "installation": {"id": 424242},
                "repository": {
                    "full_name": "autumngarage/cortex",
                    "name": "cortex",
                    "owner": {"login": "autumngarage"},
                },
                "pull_request": {
                    "number": 397,
                    "base": {"sha": "base-sha"},
                    "head": {"sha": "head-sha"},
                },
            },
        },
        max_attempts=3,
    )


def _worker(db: FakeQueueDb, registry: HandlerRegistry, **kwargs: Any) -> Worker:
    return Worker(conn=db, registry=registry, worker_id="w-test", **kwargs)


# ---------------------------------------------------------------------------
# Registry: explicit registration, open extension point
# ---------------------------------------------------------------------------


def test_registry_rejects_duplicate_registration() -> None:
    registry = HandlerRegistry()
    registry.register("github.pull_request", lambda job: {})
    with pytest.raises(HostedJobError, match="already registered"):
        registry.register("github.pull_request", lambda job: {})


def test_registry_admits_a_new_job_type_without_schema_change() -> None:
    # The cortex#471 extension point: a future job type (Stage 2 PR
    # evaluation, Slack console) is a job_type string + a handler.
    db = FakeQueueDb()
    registry = HandlerRegistry()
    registry.register("evaluate.pr", lambda job: {"evaluated": job.payload["pr"]})
    db.enqueue(JobRequest(job_type="evaluate.pr", idempotency_key="eval-1", payload={"pr": 99}))
    assert _worker(db, registry).run_once() is True
    job = db.job("eval-1")
    assert job["status"] == "succeeded"
    assert job["result"] == {"evaluated": 99}


# ---------------------------------------------------------------------------
# Claim/complete/retry/dead-letter lifecycle
# ---------------------------------------------------------------------------


def test_run_once_returns_false_on_empty_queue() -> None:
    assert _worker(FakeQueueDb(), HandlerRegistry()).run_once() is False


def test_successful_job_round_trips_with_structured_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = FakeQueueDb()
    registry = HandlerRegistry()
    registry.register("github.pull_request", lambda job: {"handled": True})
    db.enqueue(_webhook_job_request())
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert _worker(db, registry).run_once() is True
    job = db.job("github-delivery:guid-1")
    assert job["status"] == "succeeded"
    assert job["attempts"] == 1
    events = [json.loads(record.message)["event"] for record in caplog.records]
    assert events == ["job.claimed", "job.succeeded"]


def test_failed_job_is_requeued_with_capped_backoff(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = FakeQueueDb()
    registry = HandlerRegistry()

    def explode(job: Any) -> Mapping[str, Any]:
        raise RuntimeError("handler exploded")

    registry.register("github.pull_request", explode)
    db.enqueue(_webhook_job_request())
    worker = _worker(db, registry, retry_base_seconds=30, retry_cap_seconds=3600)
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert worker.run_once() is True
    job = db.job("github-delivery:guid-1")
    assert job["status"] == "queued"
    assert job["attempts"] == 1
    assert "RuntimeError: handler exploded" in job["last_error"]
    assert job["next_attempt_at"] > datetime.now(UTC)
    retry_line = json.loads(caplog.records[-1].message)
    assert retry_line["event"] == "job.retry_scheduled"
    assert retry_line["backoff_seconds"] == 30


def test_exhausted_attempts_dead_letter_with_visible_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = FakeQueueDb()
    registry = HandlerRegistry()
    registry.register("github.pull_request", _raise_runtime_error)
    db.enqueue(_webhook_job_request(max_attempts=2))
    worker = _worker(db, registry, retry_base_seconds=0.001, retry_cap_seconds=1)
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert worker.run_once() is True  # attempt 1 -> requeued
        _make_due(db, "github-delivery:guid-1")
        assert worker.run_once() is True  # attempt 2 -> dead letter
    job = db.job("github-delivery:guid-1")
    assert job["status"] == "dead"
    assert job["attempts"] == 2
    assert "RuntimeError" in job["last_error"]
    events = [json.loads(record.message)["event"] for record in caplog.records]
    assert events.count("job.retry_scheduled") == 1
    assert events.count("job.dead_lettered") == 1


def test_unknown_job_type_fails_visibly_not_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = FakeQueueDb()
    db.enqueue(JobRequest(job_type="github.unknown", idempotency_key="u1", payload={}))
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert _worker(db, HandlerRegistry()).run_once() is True
    job = db.job("u1")
    assert job["status"] == "queued"
    assert "no handler registered for job type 'github.unknown'" in job["last_error"]


def test_duplicate_enqueue_is_idempotent_at_the_queue() -> None:
    db = FakeQueueDb()
    assert db.enqueue(_webhook_job_request()) is not None
    assert db.enqueue(_webhook_job_request()) is None
    assert len(db.jobs) == 1


def test_claim_consumes_jobs_in_due_order() -> None:
    db = FakeQueueDb()
    registry = HandlerRegistry()
    seen: list[str] = []

    def record_delivery(job: Any) -> Mapping[str, Any]:
        seen.append(job.payload["delivery"])
        return {}

    registry.register("github.pull_request", record_delivery)
    db.enqueue(_webhook_job_request(delivery="first"))
    db.enqueue(_webhook_job_request(delivery="second"))
    worker = _worker(db, registry)
    assert worker.run_once() and worker.run_once()
    assert seen == ["first", "second"]


# ---------------------------------------------------------------------------
# Stale-claim recovery: crashed workers leave no ghosts
# ---------------------------------------------------------------------------


def test_stale_running_claim_is_requeued() -> None:
    db = FakeQueueDb()
    db.enqueue(_webhook_job_request())
    job = db.job("github-delivery:guid-1")
    job["status"] = "running"
    job["attempts"] = 1
    job["claimed_at"] = datetime.now(UTC) - timedelta(hours=2)
    worker = _worker(db, HandlerRegistry(), stale_claim_seconds=60)
    assert worker.recover_stale_claims() == 1
    assert job["status"] == "queued"
    assert "stale" in job["last_error"]


def test_stale_claim_with_exhausted_attempts_goes_dead() -> None:
    db = FakeQueueDb()
    db.enqueue(_webhook_job_request(max_attempts=1))
    job = db.job("github-delivery:guid-1")
    job["status"] = "running"
    job["attempts"] = 1
    job["claimed_at"] = datetime.now(UTC) - timedelta(hours=2)
    worker = _worker(db, HandlerRegistry(), stale_claim_seconds=60)
    assert worker.recover_stale_claims() == 1
    assert job["status"] == "dead"


def test_fresh_running_claim_is_left_alone() -> None:
    db = FakeQueueDb()
    db.enqueue(_webhook_job_request())
    job = db.job("github-delivery:guid-1")
    job["status"] = "running"
    job["attempts"] = 1
    job["claimed_at"] = datetime.now(UTC)
    worker = _worker(db, HandlerRegistry(), stale_claim_seconds=3600)
    assert worker.recover_stale_claims() == 0
    assert job["status"] == "running"


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


def test_run_drains_queue_then_stops_when_signalled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = FakeQueueDb()
    registry = HandlerRegistry()
    registry.register("github.pull_request", lambda job: {})
    db.enqueue(_webhook_job_request())
    stop = threading.Event()

    def stop_on_sleep(_seconds: float) -> None:
        stop.set()

    worker = Worker(conn=db, registry=registry, worker_id="w-test", sleep=stop_on_sleep)
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        worker.run(stop)
    assert db.job("github-delivery:guid-1")["status"] == "succeeded"
    events = [json.loads(record.message)["event"] for record in caplog.records]
    assert events[0] == "worker.started"
    assert events[-1] == "worker.stopped"


def test_signal_handler_sets_the_stop_event() -> None:
    stop = threading.Event()
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    try:
        install_signal_handlers(stop)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert stop.is_set()
    finally:
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


# ---------------------------------------------------------------------------
# Stub handlers + the ledger arrival record
# ---------------------------------------------------------------------------


def test_default_registry_registers_the_stage1_stubs() -> None:
    recorder = ArrivalRecorder(conn=FakeQueueDb(), tenant_id=None, source_id=None)
    registry = build_default_registry(recorder)
    assert registry.job_types() == ("github.issue_comment", "github.pull_request")


def test_review_dry_run_env_defaults_to_safe_dry_run() -> None:
    from cortex.hosted.worker import REVIEW_DRY_RUN_ENV, _env_flag

    # Unset and false-y/true tokens, so a deployed worker never posts until
    # CORTEX_REVIEW_DRY_RUN is deliberately set false-y.
    assert REVIEW_DRY_RUN_ENV == "CORTEX_REVIEW_DRY_RUN"
    assert _env_flag(None, default=True) is True
    assert _env_flag("", default=True) is True
    assert _env_flag("true", default=True) is True
    assert _env_flag("1", default=True) is True
    for falsey in ("0", "false", "no", "off", "False", " OFF "):
        assert _env_flag(falsey, default=True) is False


def test_stateless_worker_defaults_fresh_repo_to_rollout_disabled() -> None:
    db = FakeQueueDb()
    recorder = ArrivalRecorder(conn=db, tenant_id=None, source_id=None)
    fake_pem = "-----BEGIN " + "PLACEHOLDER TEST KEY-----\nnot-a-real-key\n"
    registry = build_worker_registry(
        recorder=recorder,
        environ={"GITHUB_APP_ID": "123", "GITHUB_APP_PRIVATE_KEY": fake_pem},
    )
    db.enqueue(_review_webhook_job_request())
    assert _worker(db, registry).run_once() is True
    job = db.job("github-delivery:guid-review")
    assert job["status"] == "succeeded"
    assert job["result"]["reason"] == "review_rollout_disabled"
    assert job["result"]["posted"] is False
    assert "review_mode" not in job["result"]


def test_stub_handler_without_mapping_reports_the_gap_visibly() -> None:
    db = FakeQueueDb()
    recorder = ArrivalRecorder(conn=db, tenant_id=None, source_id=None)
    db.enqueue(_webhook_job_request())
    assert _worker(db, build_default_registry(recorder)).run_once() is True
    job = db.job("github-delivery:guid-1")
    assert job["status"] == "succeeded"
    assert job["result"]["handled"] is True
    assert job["result"]["ledger_recorded"] is False
    assert job["result"]["reason"] == "tenant_mapping_unconfigured"
    assert db.ledger == {}


def test_stub_handler_records_arrival_as_a_raw_ledger_event() -> None:
    db = FakeQueueDb()
    recorder = ArrivalRecorder(conn=db, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    db.enqueue(_webhook_job_request())
    assert _worker(db, build_default_registry(recorder)).run_once() is True
    job = db.job("github-delivery:guid-1")
    assert job["result"]["ledger_recorded"] is True
    assert len(db.ledger) == 1
    ((tenant_id, _key),) = db.ledger.keys()
    assert tenant_id == TENANT_ID


def test_arrival_record_is_idempotent_across_redelivered_jobs() -> None:
    db = FakeQueueDb()
    recorder = ArrivalRecorder(conn=db, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    registry = build_default_registry(recorder)
    db.enqueue(_webhook_job_request(delivery="same-guid"))
    assert _worker(db, registry).run_once() is True
    # A redelivery enqueues a fresh job only if the delivery GUID differs;
    # simulate the worker-side replay by re-running the recorder directly.
    job = db.job("github-delivery:same-guid")
    from cortex.hosted.jobs import ClaimedJob

    replay = ClaimedJob(
        job_id=str(uuid4()),
        job_type=job["job_type"],
        idempotency_key=job["idempotency_key"],
        payload=job["payload"],
        attempts=1,
        max_attempts=3,
    )
    result = recorder.record(replay)
    assert result == {
        "ledger_recorded": False,
        "reason": "already_recorded",
        "delivery": "same-guid",
    }
    assert len(db.ledger) == 1


def test_arrival_record_uses_the_source_event_received_type() -> None:
    assert LedgerEventType.SOURCE_EVENT_RECEIVED.value == "source.event_received"


def test_recorder_refuses_a_job_without_delivery_guid() -> None:
    recorder = ArrivalRecorder(conn=FakeQueueDb(), tenant_id=TENANT_ID, source_id=SOURCE_ID)
    from cortex.hosted.jobs import ClaimedJob

    job = ClaimedJob(
        job_id=str(uuid4()),
        job_type="github.pull_request",
        idempotency_key="k",
        payload={"event": "pull_request"},
        attempts=1,
        max_attempts=3,
    )
    with pytest.raises(HostedJobError, match="no delivery GUID"):
        recorder.record(job)


# ---------------------------------------------------------------------------
# Worker constructor + taxonomy registration
# ---------------------------------------------------------------------------


def test_worker_rejects_malformed_intervals_up_front() -> None:
    with pytest.raises(HostedJobError, match="poll_interval_seconds"):
        Worker(conn=FakeQueueDb(), registry=HandlerRegistry(), poll_interval_seconds=0)
    with pytest.raises(HostedJobError, match="cap_seconds"):
        Worker(
            conn=FakeQueueDb(),
            registry=HandlerRegistry(),
            retry_base_seconds=10,
            retry_cap_seconds=1,
        )


def test_new_service_error_types_classify_in_the_degradation_taxonomy() -> None:
    assert classify_failure(HostedJobError("probe")) is DegradationMode.INVALID_INPUT_REJECTED
    assert classify_failure(ServiceConfigError("probe")) is DegradationMode.INVALID_INPUT_REJECTED


def _raise_runtime_error(_job: Any) -> Mapping[str, Any]:
    raise RuntimeError("boom")


def _make_due(db: FakeQueueDb, idempotency_key: str) -> None:
    db.job(idempotency_key)["next_attempt_at"] = datetime.now(UTC) - timedelta(seconds=1)


def test_review_token_budget_env_parsing() -> None:
    from cortex.hosted.worker import (
        DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET,
        _env_positive_int,
    )

    # Unset/blank -> the raised hosted default (not the 8k session guardrail).
    assert _env_positive_int(None, default=DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET) == 32000
    assert _env_positive_int("  ", default=DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET) == 32000
    # Explicit override.
    assert _env_positive_int("48000", default=DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET) == 48000
    # Malformed / non-positive fail visibly (no silent shrink).
    import pytest as _pytest

    for bad in ("eight-thousand", "0", "-5"):
        with _pytest.raises(ServiceConfigError):
            _env_positive_int(bad, default=DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET)
