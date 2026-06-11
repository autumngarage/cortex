"""Canonical hosted job queue substrate (cortex#471).

One queue for every hosted background job type — Stage 1 webhook deliveries
today, Stage 2 PR-evaluation jobs (#388) and Stage 3 Slack console jobs
later — backed by the ``cortex_hosted.jobs`` table (schema v7) instead of an
external broker. Rationale (the documented queue choice from cortex#471):
the executable Postgres path from cortex#472 already exists, carries the
connection policy and migration runner, and a `FOR UPDATE SKIP LOCKED`
claim is sufficient at hosted-MVP volume; a broker would be a second
stateful service to operate before the first customer exists.

Duplicate-delivery handling reuses the shipped ledger idempotency idiom
(`derive_idempotency_key` + ``ON CONFLICT ... DO NOTHING`` in
``cortex.hosted.ledger_events``): a job's identity is its caller-supplied
idempotency key (for GitHub webhooks, the delivery GUID), and a redelivered
job inserts nothing — the enqueue returns no row instead of a second job.

Extension point (exercised by tests): a new job type is a new ``job_type``
string plus a registered worker handler. No schema change is required —
``payload`` is a JSON object owned by the job type.

Like the rest of the substrate, this module emits SQL strings and validated
frozen dataclasses; execution happens through ``cortex.hosted.db``
connections in the API shell (enqueue side, cortex#470) and the worker loop
(claim side, ``cortex.hosted.worker``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Defaults derived from the delivery domain, not invented per call site:
# - GitHub redelivers manually, not automatically, so retries are the
#   worker's responsibility; five attempts with capped exponential backoff
#   bounds a poison job to a known, finite cost.
# - The base/cap pair (30s base, 1h cap) keeps transient-failure retries
#   fast while guaranteeing a stuck dependency cannot produce a hot loop.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_RETRY_BASE_SECONDS = 30.0
DEFAULT_RETRY_CAP_SECONDS = 3600.0


class HostedJobError(ValueError):
    """Raised when a job would violate the hosted queue contract."""


class JobStatus(StrEnum):
    """Closed job lifecycle vocabulary mirrored by the DB CHECK constraint."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD = "dead"


@dataclass(frozen=True)
class JobRequest:
    """A validated enqueue request for the canonical hosted queue."""

    job_type: str
    idempotency_key: str
    payload: Mapping[str, Any]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        _require_non_empty("job_type", self.job_type)
        _require_non_empty("idempotency_key", self.idempotency_key)
        _validate_json_object("payload", self.payload)
        _validate_json_object("metadata", self.metadata)
        if self.max_attempts < 1:
            raise HostedJobError(f"max_attempts must be >= 1, got {self.max_attempts}")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def as_insert_parameters(self) -> dict[str, Any]:
        """Return DB-API named parameters for :func:`enqueue_job_sql`."""

        return {
            "job_type": self.job_type,
            "idempotency_key": self.idempotency_key,
            "payload": json.dumps(dict(self.payload), sort_keys=True, separators=(",", ":")),
            "metadata": json.dumps(dict(self.metadata), sort_keys=True, separators=(",", ":")),
            "max_attempts": self.max_attempts,
        }


@dataclass(frozen=True)
class ClaimedJob:
    """One job claimed by a worker via :func:`claim_job_sql`."""

    job_id: str
    job_type: str
    idempotency_key: str
    payload: Mapping[str, Any]
    attempts: int
    max_attempts: int

    def __post_init__(self) -> None:
        _require_non_empty("job_id", self.job_id)
        _require_non_empty("job_type", self.job_type)
        _require_non_empty("idempotency_key", self.idempotency_key)
        _validate_json_object("payload", self.payload)
        if self.attempts < 1:
            raise HostedJobError(
                f"a claimed job has consumed at least one attempt, got {self.attempts}"
            )
        if self.max_attempts < 1:
            raise HostedJobError(f"max_attempts must be >= 1, got {self.max_attempts}")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    @property
    def attempts_exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

    @classmethod
    def from_row(cls, row: tuple[Any, ...]) -> ClaimedJob:
        """Build a claimed job from a :func:`claim_job_sql` RETURNING row."""

        if len(row) != 6:
            raise HostedJobError(
                f"claim row must have 6 columns (job_id, job_type, idempotency_key, "
                f"payload, attempts, max_attempts), got {len(row)}"
            )
        payload = row[3]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                raise HostedJobError(f"claimed job payload is not valid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise HostedJobError("claimed job payload must decode to a JSON object")
        return cls(
            job_id=str(row[0]),
            job_type=str(row[1]),
            idempotency_key=str(row[2]),
            payload=payload,
            attempts=int(row[4]),
            max_attempts=int(row[5]),
        )


def compute_backoff_seconds(
    attempt: int,
    *,
    base_seconds: float = DEFAULT_RETRY_BASE_SECONDS,
    cap_seconds: float = DEFAULT_RETRY_CAP_SECONDS,
) -> float:
    """Capped exponential backoff for retry scheduling.

    ``attempt`` is the attempt that just failed (1-based). The exponent is
    computed in capped space so a large attempt count cannot overflow.
    """

    if attempt < 1:
        raise HostedJobError(f"attempt must be >= 1, got {attempt}")
    if base_seconds <= 0:
        raise HostedJobError(f"base_seconds must be positive, got {base_seconds}")
    if cap_seconds < base_seconds:
        raise HostedJobError(
            f"cap_seconds ({cap_seconds}) must be >= base_seconds ({base_seconds})"
        )
    # 2**63 dwarfs any real cap; clamping the exponent keeps the arithmetic
    # exact instead of trusting float overflow behavior at scale boundaries.
    exponent = min(attempt - 1, 63)
    return min(cap_seconds, base_seconds * (2.0**exponent))


def enqueue_job_sql(schema: str = "cortex_hosted") -> str:
    """Idempotent enqueue: the ledger ``ON CONFLICT DO NOTHING`` idiom.

    Returns the new job id, or no row when the idempotency key was already
    enqueued (duplicate delivery).
    """

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.jobs (
    job_type,
    idempotency_key,
    payload,
    metadata,
    max_attempts
) VALUES (
    %(job_type)s,
    %(idempotency_key)s,
    %(payload)s::jsonb,
    %(metadata)s::jsonb,
    %(max_attempts)s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING job_id;
""".strip()


def claim_job_sql(schema: str = "cortex_hosted") -> str:
    """Claim the next due queued job with ``FOR UPDATE SKIP LOCKED``.

    Concurrent workers never claim the same row; claiming consumes one
    attempt and stamps the claimant for stale-claim recovery.
    """

    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.jobs
SET status = '{JobStatus.RUNNING.value}',
    attempts = attempts + 1,
    claimed_at = now(),
    claimed_by = %(claimed_by)s
WHERE job_id = (
    SELECT job_id
    FROM {schema}.jobs
    WHERE status = '{JobStatus.QUEUED.value}'
      AND next_attempt_at <= now()
    ORDER BY next_attempt_at, enqueued_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING job_id, job_type, idempotency_key, payload, attempts, max_attempts;
""".strip()


def complete_job_sql(schema: str = "cortex_hosted") -> str:
    """Mark a running job succeeded; guarded so only a live claim completes."""

    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.jobs
SET status = '{JobStatus.SUCCEEDED.value}',
    finished_at = now(),
    last_error = NULL,
    result = %(result)s::jsonb
WHERE job_id = %(job_id)s
  AND status = '{JobStatus.RUNNING.value}'
RETURNING job_id;
""".strip()


def retry_job_sql(schema: str = "cortex_hosted") -> str:
    """Requeue a failed running job with explicit backoff and visible error."""

    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.jobs
SET status = '{JobStatus.QUEUED.value}',
    next_attempt_at = now() + make_interval(secs => %(backoff_seconds)s),
    last_error = %(error)s,
    claimed_at = NULL,
    claimed_by = NULL
WHERE job_id = %(job_id)s
  AND status = '{JobStatus.RUNNING.value}'
RETURNING job_id;
""".strip()


def dead_letter_job_sql(schema: str = "cortex_hosted") -> str:
    """Move a running job whose attempts are exhausted to the dead letter."""

    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.jobs
SET status = '{JobStatus.DEAD.value}',
    finished_at = now(),
    last_error = %(error)s
WHERE job_id = %(job_id)s
  AND status = '{JobStatus.RUNNING.value}'
RETURNING job_id;
""".strip()


def recover_stale_claims_sql(schema: str = "cortex_hosted") -> str:
    """Requeue (or dead-letter) running jobs whose claimant disappeared.

    A worker that crashed between claim and completion leaves a job in
    ``running`` forever; this sweep makes that failure visible and finite
    instead of a silent drop. Jobs with attempts left go back to the queue;
    exhausted jobs go to the dead letter.
    """

    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.jobs
SET status = CASE
        WHEN attempts >= max_attempts THEN '{JobStatus.DEAD.value}'
        ELSE '{JobStatus.QUEUED.value}'
    END,
    finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE finished_at END,
    last_error = %(error)s,
    claimed_at = NULL,
    claimed_by = NULL
WHERE status = '{JobStatus.RUNNING.value}'
  AND claimed_at < now() - make_interval(secs => %(stale_after_seconds)s)
RETURNING job_id, status;
""".strip()


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise HostedJobError(f"{name} must not be empty")


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise HostedJobError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HostedJobError(f"{name} must be JSON-serializable") from exc


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise HostedJobError(f"invalid SQL identifier: {name!r}")
