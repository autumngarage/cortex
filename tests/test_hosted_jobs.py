"""Tests for the canonical hosted job queue substrate (cortex#471).

String-assertion coverage of the SQL surface (the substrate idiom), plus
validation and backoff behavior. Execution-shaped coverage lives in
``tests/test_hosted_worker.py`` (in-memory fake) and the env-gated
``tests/test_hosted_api_integration.py`` (live Postgres).
"""

from __future__ import annotations

import pytest

from cortex.hosted.jobs import (
    DEFAULT_MAX_ATTEMPTS,
    ClaimedJob,
    HostedJobError,
    JobRequest,
    JobStatus,
    claim_job_sql,
    complete_job_sql,
    compute_backoff_seconds,
    dead_letter_job_sql,
    enqueue_job_sql,
    recover_stale_claims_sql,
    retry_job_sql,
)
from cortex.hosted.schema import create_schema_sql

# ---------------------------------------------------------------------------
# JobRequest validation (fail-closed)
# ---------------------------------------------------------------------------


def test_job_request_rejects_empty_job_type() -> None:
    with pytest.raises(HostedJobError, match="job_type must not be empty"):
        JobRequest(job_type="  ", idempotency_key="k", payload={})


def test_job_request_rejects_empty_idempotency_key() -> None:
    with pytest.raises(HostedJobError, match="idempotency_key must not be empty"):
        JobRequest(job_type="github.ping", idempotency_key="", payload={})


def test_job_request_rejects_non_json_payload() -> None:
    with pytest.raises(HostedJobError, match="payload must be JSON-serializable"):
        JobRequest(
            job_type="github.ping",
            idempotency_key="k",
            payload={"bad": object()},
        )


def test_job_request_rejects_non_positive_max_attempts() -> None:
    with pytest.raises(HostedJobError, match="max_attempts must be >= 1"):
        JobRequest(job_type="github.ping", idempotency_key="k", payload={}, max_attempts=0)


def test_job_request_insert_parameters_serialize_canonical_json() -> None:
    request = JobRequest(
        job_type="github.pull_request",
        idempotency_key="github-delivery:abc",
        payload={"b": 2, "a": 1},
    )
    params = request.as_insert_parameters()
    assert params["payload"] == '{"a":1,"b":2}'
    assert params["max_attempts"] == DEFAULT_MAX_ATTEMPTS
    assert params["idempotency_key"] == "github-delivery:abc"


# ---------------------------------------------------------------------------
# ClaimedJob validation
# ---------------------------------------------------------------------------


def test_claimed_job_requires_consumed_attempt() -> None:
    with pytest.raises(HostedJobError, match="at least one attempt"):
        ClaimedJob(
            job_id="j1",
            job_type="github.ping",
            idempotency_key="k",
            payload={},
            attempts=0,
            max_attempts=3,
        )


def test_claimed_job_from_row_decodes_json_payload_text() -> None:
    job = ClaimedJob.from_row(("j1", "github.ping", "k", '{"x": 1}', 1, 5))
    assert job.payload == {"x": 1}
    assert job.attempts_exhausted is False


def test_claimed_job_from_row_rejects_wrong_arity_and_bad_payload() -> None:
    with pytest.raises(HostedJobError, match="6 columns"):
        ClaimedJob.from_row(("j1", "github.ping", "k", "{}", 1))
    with pytest.raises(HostedJobError, match="not valid JSON"):
        ClaimedJob.from_row(("j1", "github.ping", "k", "{nope", 1, 5))
    with pytest.raises(HostedJobError, match="JSON object"):
        ClaimedJob.from_row(("j1", "github.ping", "k", "[1]", 1, 5))


def test_claimed_job_attempts_exhausted_at_boundary() -> None:
    job = ClaimedJob.from_row(("j1", "github.ping", "k", "{}", 5, 5))
    assert job.attempts_exhausted is True


# ---------------------------------------------------------------------------
# Backoff: capped exponential, exact at small/typical/large scales
# ---------------------------------------------------------------------------


def test_backoff_grows_exponentially_then_caps() -> None:
    assert compute_backoff_seconds(1, base_seconds=30, cap_seconds=3600) == 30
    assert compute_backoff_seconds(2, base_seconds=30, cap_seconds=3600) == 60
    assert compute_backoff_seconds(5, base_seconds=30, cap_seconds=3600) == 480
    assert compute_backoff_seconds(8, base_seconds=30, cap_seconds=3600) == 3600


def test_backoff_is_finite_and_capped_at_large_attempt_counts() -> None:
    # Invariant: backoff never exceeds the cap for any attempt in the space.
    for attempt in (1, 10, 63, 64, 100, 10_000):
        value = compute_backoff_seconds(attempt, base_seconds=1, cap_seconds=300)
        assert 0 < value <= 300


def test_backoff_rejects_malformed_parameters() -> None:
    with pytest.raises(HostedJobError, match="attempt must be >= 1"):
        compute_backoff_seconds(0)
    with pytest.raises(HostedJobError, match="base_seconds must be positive"):
        compute_backoff_seconds(1, base_seconds=0)
    with pytest.raises(HostedJobError, match="cap_seconds"):
        compute_backoff_seconds(1, base_seconds=10, cap_seconds=5)


# ---------------------------------------------------------------------------
# SQL surface: the ledger idempotency idiom + SKIP LOCKED claim
# ---------------------------------------------------------------------------


def test_enqueue_sql_reuses_the_ledger_idempotency_idiom() -> None:
    sql = enqueue_job_sql()
    assert "INSERT INTO cortex_hosted.jobs" in sql
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert "RETURNING job_id" in sql


def test_claim_sql_uses_skip_locked_and_consumes_an_attempt() -> None:
    sql = claim_job_sql()
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "attempts = attempts + 1" in sql
    assert f"WHERE status = '{JobStatus.QUEUED.value}'" in sql
    assert "next_attempt_at <= now()" in sql
    assert "LIMIT 1" in sql
    assert "RETURNING job_id, job_type, idempotency_key, payload, attempts, max_attempts" in sql


def test_transition_sql_only_moves_live_claims() -> None:
    # Invariant: every post-claim transition is guarded on status='running',
    # so a stale-claim recovery and a slow worker cannot double-finish a job.
    for sql in (complete_job_sql(), retry_job_sql(), dead_letter_job_sql()):
        assert f"AND status = '{JobStatus.RUNNING.value}'" in sql
        assert "RETURNING job_id" in sql


def test_retry_sql_applies_explicit_backoff_and_visible_error() -> None:
    sql = retry_job_sql()
    assert "make_interval(secs => %(backoff_seconds)s)" in sql
    assert "last_error = %(error)s" in sql
    assert f"SET status = '{JobStatus.QUEUED.value}'" in sql


def test_stale_claim_recovery_requeues_or_dead_letters() -> None:
    sql = recover_stale_claims_sql()
    assert f"WHERE status = '{JobStatus.RUNNING.value}'" in sql
    assert "claimed_at < now() - make_interval(secs => %(stale_after_seconds)s)" in sql
    assert f"WHEN attempts >= max_attempts THEN '{JobStatus.DEAD.value}'" in sql
    assert f"ELSE '{JobStatus.QUEUED.value}'" in sql


def test_sql_builders_reject_unsafe_schema_identifier() -> None:
    for builder in (
        enqueue_job_sql,
        claim_job_sql,
        complete_job_sql,
        retry_job_sql,
        dead_letter_job_sql,
        recover_stale_claims_sql,
    ):
        with pytest.raises(HostedJobError, match="invalid SQL identifier"):
            builder("cortex; DROP SCHEMA public")


# ---------------------------------------------------------------------------
# Schema v7: the jobs table ships in the canonical DDL (one migration path)
# ---------------------------------------------------------------------------


def test_schema_v7_ships_the_jobs_table() -> None:
    sql = create_schema_sql()
    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.jobs" in sql
    assert "CONSTRAINT jobs_idempotency_key_unique UNIQUE (idempotency_key)" in sql
    assert "CHECK (status IN ('queued', 'running', 'succeeded', 'dead'))" in sql
    assert "jobs_claim_idx" in sql
    assert "WHERE status = 'queued'" in sql
    assert "Canonical hosted job queue" in sql


def test_schema_v7_refreshes_the_ledger_event_type_check() -> None:
    sql = create_schema_sql()
    assert "DROP CONSTRAINT ledger_events_event_type_check" in sql
    assert "ADD CONSTRAINT ledger_events_event_type_check" in sql
    assert "'source.event_received'" in sql
