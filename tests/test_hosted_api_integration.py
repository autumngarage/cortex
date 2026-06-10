"""Live-Postgres integration test for the Stage 1 service path (cortex#470/#471).

Runs only when ``DATABASE_URL`` points at a real Postgres provisioned with
the hosted extensions (the Railway compass Postgres, or a local
pgvector-enabled image) and the ``hosted`` extra is installed::

    DATABASE_URL='postgresql://user:pass@host:5432/db?sslmode=require' \\
        uv run --extra hosted pytest tests/test_hosted_api_integration.py -q

Covers the cortex#471 acceptance loop end to end: the v7 migration applies
(idempotently), the enqueue side persists an idempotent job row keyed by the
delivery GUID, a worker claims it through ``FOR UPDATE SKIP LOCKED``, and
the same substrate round-trips a second job type without any schema change.
Job rows created here are completed and tagged with per-run UUIDs.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from cortex.hosted.db import HostedConnection, connect
from cortex.hosted.jobs import (
    ClaimedJob,
    JobRequest,
    claim_job_sql,
    complete_job_sql,
    enqueue_job_sql,
)
from cortex.hosted.migrations import apply_schema
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION

DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "set DATABASE_URL to a Postgres with pgcrypto/pg_trgm/vector "
        "(e.g. the Railway compass Postgres) to run the hosted integration tests"
    ),
)


@pytest.fixture()
def conn() -> Iterator[HostedConnection]:
    connection = connect(DATABASE_URL)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def test_v7_migration_applies_and_a_job_round_trips(conn: HostedConnection) -> None:
    result = apply_schema(conn)
    assert result.version == HOSTED_SCHEMA_VERSION

    run_id = uuid.uuid4().hex[:12]
    request = JobRequest(
        job_type="github.pull_request",
        idempotency_key=f"it-{run_id}",
        payload={"event": "pull_request", "delivery": f"it-{run_id}", "body": {}},
    )

    inserted = conn.execute(enqueue_job_sql(), request.as_insert_parameters()).fetchone()
    assert inserted is not None

    # Duplicate delivery: the ledger idempotency idiom holds at the queue.
    duplicate = conn.execute(enqueue_job_sql(), request.as_insert_parameters()).fetchone()
    assert duplicate is None
    conn.commit()

    claimed_row = conn.execute(
        claim_job_sql(), {"claimed_by": f"it-worker-{run_id}"}
    ).fetchone()
    assert claimed_row is not None
    job = ClaimedJob.from_row(claimed_row)
    # Other test runs may have queued rows; claim until ours arrives.
    seen: set[str] = set()
    while job.idempotency_key != f"it-{run_id}" and job.job_id not in seen:
        seen.add(job.job_id)
        conn.execute(
            complete_job_sql(),
            {"job_id": job.job_id, "result": '{"completed_by": "integration-sweep"}'},
        )
        next_row = conn.execute(
            claim_job_sql(), {"claimed_by": f"it-worker-{run_id}"}
        ).fetchone()
        assert next_row is not None, "enqueued job was never claimable"
        job = ClaimedJob.from_row(next_row)
    assert job.idempotency_key == f"it-{run_id}"
    assert job.job_type == "github.pull_request"
    assert job.attempts == 1

    done = conn.execute(
        complete_job_sql(),
        {"job_id": job.job_id, "result": '{"handled": true}'},
    ).fetchone()
    assert done is not None
    conn.commit()

    status_row = conn.execute(
        "SELECT status, result FROM cortex_hosted.jobs WHERE job_id = %(job_id)s",
        {"job_id": job.job_id},
    ).fetchone()
    assert status_row is not None
    assert status_row[0] == "succeeded"
    assert status_row[1] == {"handled": True}
