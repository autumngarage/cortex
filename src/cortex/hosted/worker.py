"""Hosted job worker loop (cortex#471).

Polls the canonical ``cortex_hosted.jobs`` queue (no broker — the
documented database-backed choice in :mod:`cortex.hosted.jobs`), claims one
job at a time via ``FOR UPDATE SKIP LOCKED``, and dispatches by job type
through a handler registry. Every lifecycle transition emits one structured
JSON log line, and every failure ends in a visible state: requeued with
capped exponential backoff while attempts remain, dead-lettered with the
error text when they are exhausted. Nothing is dropped silently.

Crash safety: the claim is committed before the handler runs, so a worker
that dies mid-job leaves the row in ``running`` with a claimant stamp; the
stale-claim sweep (:func:`cortex.hosted.jobs.recover_stale_claims_sql`)
returns such rows to the queue (or the dead letter) on a later poll. The
failure is finite and visible, never a lock held by a ghost.

Stage 1 handlers are deliberately stubs: ``github.pull_request`` and
``github.issue_comment`` mark the delivery handled and record its arrival
in the hosted ledger as a ``source.event_received`` event when the webhook's
installation/repo resolves to stored tenant/source rows. Without a binding the
result names the gap instead of pretending. ``CORTEX_TENANT_ID`` /
``CORTEX_SOURCE_ID`` are an explicit dev fallback only, gated by
``CORTEX_STATIC_TENANT_FALLBACK`` and logged whenever used.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from cortex.hosted.api.config import ServiceConfig, ServiceConfigError
from cortex.hosted.db import HostedConnection, connect
from cortex.hosted.jobs import (
    ClaimedJob,
    HostedJobError,
    claim_job_sql,
    complete_job_sql,
    compute_backoff_seconds,
    dead_letter_job_sql,
    recover_stale_claims_sql,
    retry_job_sql,
    select_prunable_terminal_jobs_sql,
    terminal_job_payload_skeleton,
    update_job_payload_sql,
)
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
    ledger_event_insert_sql,
)
from cortex.hosted.logging import exception_for_log, log_event, validate_log_fields
from cortex.hosted.migrations import apply_schema

logger = logging.getLogger("cortex.hosted.worker")

SERVICE_NAME = "cortex-worker"

# When set to a false-y value (0/false/no/off), the stateless review worker
# posts advisory comments. Unset or any other value keeps it dry-run (safe
# default): a freshly deployed worker never posts to a customer PR until an
# operator deliberately flips this.
REVIEW_DRY_RUN_ENV = "CORTEX_REVIEW_DRY_RUN"
_FALSE_TOKENS = frozenset({"0", "false", "no", "off"})

# The token budget for one hosted review's decision pack. The local
# ``cortex review`` default is the manifest session guardrail (8k), but that
# is a *session* budget, not a per-review judge budget — a frontier model has
# a far larger context, and a real catch (PR #561) checked only 3 of ~22
# decisions and disclosed 19 dropped over-budget. The hosted default is
# raised so a typical repo's full decision set is checked; CORTEX_REVIEW_TOKEN_BUDGET
# overrides per deployment. Cost stays trivial (~$0.10/review at this size).
REVIEW_TOKEN_BUDGET_ENV = "CORTEX_REVIEW_TOKEN_BUDGET"
DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET = 32000

# Reactions have no webhook (cortex#393): the worker sweeps them on a clock
# between queue drains. Seconds between sweeps; "0" disables the sweep
# explicitly. The 15-minute default keeps GitHub API usage trivial (a handful
# of reads per recently-reviewed PR) while feedback lands the same quarter
# hour a human reacts.
REACTION_POLL_SECONDS_ENV = "CORTEX_REACTION_POLL_SECONDS"
DEFAULT_REACTION_POLL_SECONDS = 900.0

# Terminal webhook payload minimization (cortex#533). Raw webhook bodies stay
# available for a short debug window, then a housekeeping pass replaces them
# with a content-free skeleton plus a body hash. The default is the documented
# 7-day grace window; tests can set it to 0, but dogfood should keep it above
# the reaction-sweep window so feedback polling can still derive PR targets.
JOB_PAYLOAD_PRUNE_GRACE_SECONDS_ENV = "CORTEX_JOB_PAYLOAD_PRUNE_GRACE_SECONDS"
DEFAULT_JOB_PAYLOAD_PRUNE_GRACE_SECONDS = 7 * 24 * 60 * 60.0
DEFAULT_JOB_PAYLOAD_PRUNE_BATCH_SIZE = 100

UNSUPPORTED_GITHUB_REVIEW_JOB_TYPES = (
    "github.pull_request_review",
    "github.pull_request_review_comment",
)

_RESULT_OMIT_KEYS = frozenset({"comment_body"})

JobHandler = Callable[[ClaimedJob], Mapping[str, Any]]


def _env_flag(raw: str | None, *, default: bool) -> bool:
    """Parse a boolean env flag; unset -> ``default``, false-y -> ``False``."""

    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in _FALSE_TOKENS


def _env_positive_int(raw: str | None, *, default: int) -> int:
    """Parse a positive-int env value; unset/blank -> ``default``.

    A malformed or non-positive value is a visible configuration error, not a
    silent fallback to the default — a typo'd budget must not quietly shrink
    every review.
    """

    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ServiceConfigError(
            f"{REVIEW_TOKEN_BUDGET_ENV} must be a positive integer; got {raw.strip()!r}"
        ) from exc
    if value < 1:
        raise ServiceConfigError(f"{REVIEW_TOKEN_BUDGET_ENV} must be >= 1; got {value}")
    return value


def _env_nonnegative_float(raw: str | None, *, default: float, name: str) -> float:
    """Parse a non-negative float env value; unset/blank -> ``default``."""

    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ServiceConfigError(
            f"{name} must be a non-negative number; got {raw.strip()!r}"
        ) from exc
    if value < 0:
        raise ServiceConfigError(f"{name} must be >= 0; got {value}")
    return value


def _log(event: str, **fields: Any) -> None:
    """One structured JSON log line per worker transition."""

    log_event(logger, event, **fields)


def content_free_job_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Return the durable job result after dropping known content fields."""

    cleaned = {
        str(key): value
        for key, value in result.items()
        if str(key).lower() not in _RESULT_OMIT_KEYS
    }
    validate_log_fields(cleaned)
    return cleaned


def result_log_fields(result: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize a content-free result for the ``job.succeeded`` log line."""

    fields: dict[str, Any] = {}
    for key in (
        "handled",
        "review_mode",
        "dry_run",
        "posted",
        "review_skipped",
        "reason",
        "repo_full_name",
        "pr_number",
        "head_sha",
        "comment_id",
        "comment_url",
        "finding_count",
        "decision_count",
        "no_decisions",
        "feedback_recorded",
        "feedback_kind",
        "unsupported_event",
        "github_event",
        "action",
        "repository",
        "installation_id",
        "tenant_id",
        "source_id",
        "ledger_recorded",
        "repos_recorded",
        "repos_deactivated",
        "repos_added",
        "repos_removed",
        "installation_action",
    ):
        if key in result:
            fields[key] = result[key]
    cost = result.get("cost")
    if isinstance(cost, Mapping):
        for key in ("model", "input_tokens", "output_tokens", "usd", "call_count"):
            if key in cost:
                fields[f"cost_{key}"] = cost[key]
    validate_log_fields(fields)
    return fields


def _tenant_id_from_result(result: Mapping[str, Any], *, fallback: str) -> str:
    """Read resolved tenant identity from a handler result when present."""

    tenant_id = result.get("tenant_id")
    if isinstance(tenant_id, str) and tenant_id.strip():
        return tenant_id
    return fallback


class HandlerRegistry:
    """Dispatch table from job type to handler. Registration is explicit."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        if not job_type.strip():
            raise HostedJobError("job_type must not be empty")
        if job_type in self._handlers:
            raise HostedJobError(
                f"handler for job type {job_type!r} is already registered; "
                "two handlers for one type would make dispatch order-dependent"
            )
        self._handlers[job_type] = handler

    def resolve(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    def job_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))


@dataclass(frozen=True)
class ArrivalRecorder:
    """Records raw webhook arrival in the hosted ledger (cortex#471).

    Production resolution is installation-based: ``identity_resolver`` maps the
    webhook's installation/repo to stored tenant/source rows (cortex#572).
    ``tenant_id``/``source_id`` remain only as an explicit dev fallback; using
    them requires ``static_tenant_fallback_enabled`` and emits a structured log
    line, so static mapping can never masquerade as normal telemetry.
    """

    conn: HostedConnection
    tenant_id: str | None
    source_id: str | None
    identity_resolver: Callable[[ClaimedJob], Any | None] | None = None
    static_tenant_fallback_enabled: bool = False

    def record(self, job: ClaimedJob) -> dict[str, Any]:
        tenant_id: str | None = None
        source_id: str | None = None
        if self.identity_resolver is not None:
            identity = self.identity_resolver(job)
            if identity is not None:
                tenant_id = str(identity.tenant_id)
                source_id = str(identity.source_id)
        if tenant_id is None or source_id is None:
            if (
                self.static_tenant_fallback_enabled
                and self.tenant_id is not None
                and self.source_id is not None
            ):
                tenant_id = self.tenant_id
                source_id = self.source_id
                _log(
                    "worker.static_tenant_fallback_used",
                    job_id=job.job_id,
                    job_type=job.job_type,
                    reason="installation_identity_unresolved",
                )
            else:
                return {
                    "ledger_recorded": False,
                    "reason": "installation_identity_unresolved",
                    "remediation": (
                        "record GitHub installation lifecycle webhooks before review "
                        "traffic; for local development only, set "
                        "CORTEX_STATIC_TENANT_FALLBACK=1 with CORTEX_TENANT_ID and "
                        "CORTEX_SOURCE_ID"
                    ),
                }
        if tenant_id is None or source_id is None:
            return {
                "ledger_recorded": False,
                "reason": "installation_identity_unresolved",
            }
        delivery = str(job.payload.get("delivery", "")).strip()
        if not delivery:
            raise HostedJobError(
                f"job {job.job_id} payload carries no delivery GUID; "
                "cannot derive a ledger idempotency key"
            )
        received_at_raw = job.payload.get("received_at")
        occurred_at = (
            datetime.fromisoformat(str(received_at_raw))
            if received_at_raw is not None
            else datetime.now(UTC)
        )
        if occurred_at.tzinfo is None:
            raise HostedJobError(
                f"job {job.job_id} received_at must be timezone-aware, got {received_at_raw!r}"
            )
        body = job.payload.get("body")
        body_map: Mapping[str, Any] = body if isinstance(body, Mapping) else {}
        repository = body_map.get("repository")
        repository_full_name = (
            repository.get("full_name") if isinstance(repository, Mapping) else None
        )
        event = LedgerEvent(
            tenant_id=tenant_id,
            source_id=source_id,
            event_type=LedgerEventType.SOURCE_EVENT_RECEIVED,
            actor=ActorRef(actor_type="github-webhook", actor_id=delivery),
            occurred_at=occurred_at,
            idempotency_key=derive_idempotency_key(
                source_id=source_id,
                event_type=LedgerEventType.SOURCE_EVENT_RECEIVED,
                source_event_external_id=delivery,
            ),
            payload={
                "delivery": delivery,
                "event": job.payload.get("event"),
                "action": body_map.get("action"),
                "repository": repository_full_name,
                "job_id": job.job_id,
            },
            source_event_external_id=delivery,
        )
        row = self.conn.execute(ledger_event_insert_sql(), event.as_insert_parameters()).fetchone()
        if row is None:
            # Redelivery: the arrival is already on the ledger. Visible, not
            # an error — the idempotency idiom held.
            return {
                "ledger_recorded": False,
                "reason": "already_recorded",
                "delivery": delivery,
                "tenant_id": tenant_id,
                "source_id": source_id,
            }
        return {
            "ledger_recorded": True,
            "ledger_event_id": str(row[0]),
            "delivery": delivery,
            "tenant_id": tenant_id,
            "source_id": source_id,
        }


def _stub_github_handler(recorder: ArrivalRecorder) -> JobHandler:
    def handle(job: ClaimedJob) -> Mapping[str, Any]:
        result = {"handled": True, "job_type": job.job_type}
        result.update(recorder.record(job))
        return result

    return handle


def unsupported_github_review_event_handler(job: ClaimedJob) -> Mapping[str, Any]:
    """Acknowledge expected GitHub review webhooks that Cortex does not use yet."""

    body = job.payload.get("body")
    body_map: Mapping[str, Any] = body if isinstance(body, Mapping) else {}
    repository = body_map.get("repository")
    repository_map: Mapping[str, Any] = repository if isinstance(repository, Mapping) else {}
    pull_request = body_map.get("pull_request")
    pr_map: Mapping[str, Any] = pull_request if isinstance(pull_request, Mapping) else {}
    return {
        "handled": True,
        "job_type": job.job_type,
        "unsupported_event": True,
        "reason": "unsupported_github_review_event",
        "github_event": job.payload.get("event"),
        "action": body_map.get("action"),
        "repository": repository_map.get("full_name"),
        "pr_number": pr_map.get("number"),
    }


def build_default_registry(recorder: ArrivalRecorder) -> HandlerRegistry:
    """The Stage 1 registry: stub GitHub handlers over one shared recorder."""

    registry = HandlerRegistry()
    registry.register("github.pull_request", _stub_github_handler(recorder))
    registry.register("github.issue_comment", _stub_github_handler(recorder))
    for job_type in UNSUPPORTED_GITHUB_REVIEW_JOB_TYPES:
        registry.register(job_type, unsupported_github_review_event_handler)
    return registry


class Worker:
    """Single-connection polling worker over the canonical job queue."""

    def __init__(
        self,
        *,
        conn: HostedConnection,
        registry: HandlerRegistry,
        worker_id: str | None = None,
        poll_interval_seconds: float = 2.0,
        stale_claim_seconds: float = 1800.0,
        retry_base_seconds: float = 30.0,
        retry_cap_seconds: float = 3600.0,
        sleep: Callable[[float], None] | None = None,
        reaction_sweep: Callable[[], Mapping[str, Any]] | None = None,
        reaction_sweep_seconds: float = 900.0,
        payload_prune_grace_seconds: float = DEFAULT_JOB_PAYLOAD_PRUNE_GRACE_SECONDS,
        payload_prune_batch_size: int = DEFAULT_JOB_PAYLOAD_PRUNE_BATCH_SIZE,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise HostedJobError(
                f"poll_interval_seconds must be positive, got {poll_interval_seconds}"
            )
        if stale_claim_seconds <= 0:
            raise HostedJobError(f"stale_claim_seconds must be positive, got {stale_claim_seconds}")
        if reaction_sweep is not None and reaction_sweep_seconds <= 0:
            raise HostedJobError(
                f"reaction_sweep_seconds must be positive, got {reaction_sweep_seconds}"
            )
        if payload_prune_grace_seconds < 0:
            raise HostedJobError(
                "payload_prune_grace_seconds must be non-negative, "
                f"got {payload_prune_grace_seconds}"
            )
        if payload_prune_batch_size < 1:
            raise HostedJobError(
                f"payload_prune_batch_size must be >= 1, got {payload_prune_batch_size}"
            )
        # Validate the backoff parameters once, up front, instead of on the
        # first failed job.
        compute_backoff_seconds(1, base_seconds=retry_base_seconds, cap_seconds=retry_cap_seconds)
        self._conn = conn
        self._registry = registry
        self._worker_id = worker_id or f"{SERVICE_NAME}@{socket.gethostname()}"
        self._poll_interval_seconds = poll_interval_seconds
        self._stale_claim_seconds = stale_claim_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_cap_seconds = retry_cap_seconds
        self._sleep = sleep if sleep is not None else threading.Event().wait
        self._reaction_sweep = reaction_sweep
        self._reaction_sweep_seconds = reaction_sweep_seconds
        self._payload_prune_grace_seconds = payload_prune_grace_seconds
        self._payload_prune_batch_size = payload_prune_batch_size
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._last_reaction_sweep: float | None = None

    def recover_stale_claims(self) -> int:
        """Return stale ``running`` rows to the queue (or the dead letter)."""

        rows = self._conn.execute(
            recover_stale_claims_sql(),
            {
                "stale_after_seconds": self._stale_claim_seconds,
                "error": (
                    f"claim went stale after {self._stale_claim_seconds}s; "
                    "claimant presumed crashed; recovered by stale-claim sweep"
                ),
            },
        ).fetchall()
        self._conn.commit()
        for job_id, status in rows:
            _log("job.stale_claim_recovered", job_id=str(job_id), status=str(status))
        return len(rows)

    def prune_terminal_payloads(self) -> int:
        """Replace old terminal raw webhook payloads with content-free skeletons."""

        rows = self._conn.execute(
            select_prunable_terminal_jobs_sql(),
            {
                "grace_seconds": self._payload_prune_grace_seconds,
                "limit": self._payload_prune_batch_size,
            },
        ).fetchall()
        pruned = 0
        for job_id, payload in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, Mapping):
                raise HostedJobError(f"job {job_id} payload is not a JSON object")
            skeleton = terminal_job_payload_skeleton(payload)
            updated = self._conn.execute(
                update_job_payload_sql(),
                {
                    "job_id": str(job_id),
                    "payload": json.dumps(skeleton, sort_keys=True, separators=(",", ":")),
                },
            ).fetchone()
            if updated is None:
                raise HostedJobError(f"job {job_id} was not terminal when payload prune ran")
            pruned += 1
        self._conn.commit()
        if pruned:
            _log("job.payloads_pruned", count=pruned)
        return pruned

    def run_once(self) -> bool:
        """Claim and process at most one job. Returns whether one was claimed."""

        row = self._conn.execute(claim_job_sql(), {"claimed_by": self._worker_id}).fetchone()
        if row is None:
            self._conn.commit()
            return False
        # Commit the claim before handling so a crash mid-handler leaves a
        # visible running row for the stale-claim sweep, not a rolled-back
        # invisible retry.
        self._conn.commit()
        job = ClaimedJob.from_row(row)
        _log(
            "job.claimed",
            job_id=job.job_id,
            job_type=job.job_type,
            attempt=job.attempts,
            max_attempts=job.max_attempts,
            worker=self._worker_id,
        )
        handler = self._registry.resolve(job.job_type)
        if handler is None:
            self._fail(
                job,
                error=(
                    f"no handler registered for job type {job.job_type!r} "
                    f"(registered: {', '.join(self._registry.job_types()) or 'none'})"
                ),
            )
            return True
        try:
            result = handler(job)
        except Exception as exc:
            self._conn.rollback()
            self._fail(job, error=exception_for_log(exc))
            return True
        # Operator-internal review cost (cortex#547): persist one append-only
        # row and emit one structured log line for a successful review, in the
        # SAME transaction as the job completion so a redelivery is atomic. A
        # DB error here fails the job visibly (it is a real failure); a missing
        # cost / non-review result is a visible skip, never silent.
        self._record_review_cost(job, result)
        # Staged-traffic registry (cortex#575): demo-fixture PRs are a
        # different data regime from organic work; one idempotent registry row
        # per staged PR lets precision metrics exclude them by JOIN without
        # ever rewriting the append-only feedback corpus. Same transaction as
        # job completion, same atomicity argument as the cost row.
        self._record_staged_pr(job, result)
        stored_result = content_free_job_result(result)
        self._conn.execute(
            complete_job_sql(),
            {
                "job_id": job.job_id,
                "result": json.dumps(stored_result, sort_keys=True, default=str),
            },
        )
        self._conn.commit()
        _log(
            "job.succeeded",
            job_id=job.job_id,
            job_type=job.job_type,
            **result_log_fields(stored_result),
        )
        return True

    def _record_review_cost(self, job: ClaimedJob, result: Mapping[str, Any]) -> None:
        """Persist + log the operator-internal cost of a successful review.

        Operator-INTERNAL only (cortex#547): provider dollars (tokens x list
        rate), never the customer-facing credits meter, never in the rendered
        PR comment. Reads the cost the handler attached under the ``cost`` key
        and the PR identity from the webhook payload, writes one append-only
        row (``ON CONFLICT DO NOTHING`` — idempotent on redelivery), and emits
        one ``review.cost`` structured log line.

        A non-review result, a result without a ``cost`` block, or an
        unparseable PR payload is a visible skip (logged), not a job failure —
        the review already succeeded, and failing it over cost bookkeeping
        would be worse than recording the gap. A DB write error, by contrast,
        propagates and fails the job: a database that cannot persist is a real
        failure, not a degradation.
        """

        if result.get("review_mode") != "stateless":
            return
        cost = result.get("cost")
        if not isinstance(cost, Mapping):
            _log(
                "review.cost_skipped",
                job_id=job.job_id,
                reason="no_cost_in_result",
            )
            return

        from cortex.hosted.review_cost import (
            ReviewCostError,
            ReviewCostRecord,
            review_cost_insert_sql,
        )
        from cortex.hosted.stateless_review import (
            StatelessReviewError,
            parse_pull_request_payload,
        )

        try:
            event = parse_pull_request_payload(job.payload)
            tenant_id = _tenant_id_from_result(result, fallback=event.tenant_id)
            record = ReviewCostRecord(
                tenant_id=tenant_id,
                repo_full_name=f"{event.owner}/{event.repo}",
                pr_number=event.pr_number,
                head_sha=event.head_sha,
                model_id=str(cost["model"]),
                input_tokens=int(cost["input_tokens"]),
                output_tokens=int(cost["output_tokens"]),
                usd=float(cost["usd"]),
                occurred_at=datetime.now(UTC),
            )
        except (StatelessReviewError, ReviewCostError, KeyError, TypeError, ValueError) as exc:
            _log(
                "review.cost_skipped",
                job_id=job.job_id,
                reason="unrecordable_cost",
                error=exception_for_log(exc),
            )
            return

        row = self._conn.execute(review_cost_insert_sql(), record.as_insert_parameters()).fetchone()
        _log(
            "review.cost",
            job_id=job.job_id,
            tenant=record.tenant_id,
            repo=record.repo_full_name,
            pr=record.pr_number,
            head_sha=record.head_sha,
            model=record.model_id,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            usd=record.usd,
            recorded=row is not None,
        )

    def _record_staged_pr(self, job: ClaimedJob, result: Mapping[str, Any]) -> None:
        """Register a staged demo-fixture PR in the exclusion registry.

        Detection is the documented convention (title token ``[cortex-demo]``
        or label ``cortex-demo-fixture`` — cortex#575); an organic PR simply
        produces no row and no log line. A staged PR gets one idempotent
        registry row (``ON CONFLICT DO NOTHING`` on the PR identity) and one
        ``review.staged_pr`` structured log line so the exclusion is visible
        at write time, never silent. An unparseable PR identity on a payload
        that DID match the staged convention is a visible skip (logged), not
        a job failure — mirroring the cost-row degradation contract. A DB
        write error propagates and fails the job: a database that cannot
        persist is a real failure.
        """

        if result.get("review_mode") != "stateless":
            return

        from cortex.hosted.staged_pr import (
            StagedPrError,
            StagedPrRecord,
            detect_staged_reason,
            staged_pr_insert_sql,
        )
        from cortex.hosted.stateless_review import (
            StatelessReviewError,
            parse_pull_request_payload,
        )

        reason = detect_staged_reason(job.payload)
        if reason is None:
            return
        try:
            event = parse_pull_request_payload(job.payload)
            tenant_id = _tenant_id_from_result(result, fallback=event.tenant_id)
            record = StagedPrRecord(
                tenant_id=tenant_id,
                repo_full_name=f"{event.owner}/{event.repo}",
                pr_number=event.pr_number,
                reason=reason,
                recorded_at=datetime.now(UTC),
            )
        except (StatelessReviewError, StagedPrError) as exc:
            _log(
                "review.staged_pr_skipped",
                job_id=job.job_id,
                reason="unrecordable_identity",
                error=exception_for_log(exc),
            )
            return

        row = self._conn.execute(staged_pr_insert_sql(), record.as_insert_parameters()).fetchone()
        _log(
            "review.staged_pr",
            job_id=job.job_id,
            tenant=record.tenant_id,
            repo=record.repo_full_name,
            pr=record.pr_number,
            staged_reason=record.reason,
            recorded=row is not None,
        )

    def run(self, stop: threading.Event) -> None:
        """Poll until ``stop`` is set; recover stale claims between polls."""

        _log(
            "worker.started",
            worker=self._worker_id,
            job_types=list(self._registry.job_types()),
            poll_interval_seconds=self._poll_interval_seconds,
        )
        while not stop.is_set():
            self.recover_stale_claims()
            self.prune_terminal_payloads()
            processed = True
            while processed and not stop.is_set():
                processed = self.run_once()
            if not stop.is_set():
                self.maybe_run_reaction_sweep()
            if not stop.is_set():
                self._sleep(self._poll_interval_seconds)
        _log("worker.stopped", worker=self._worker_id)

    def maybe_run_reaction_sweep(self) -> bool:
        """Run the reaction sweep when configured and due; never let it crash
        the worker.

        Reactions have no webhook (cortex#393), so the worker sweeps them on a
        clock between queue drains. A sweep failure is a visible degradation
        (one ``feedback.reaction_sweep_failed`` line) — the queue keeps
        draining either way, and the next due tick retries. Returns whether a
        sweep was attempted.
        """

        if self._reaction_sweep is None:
            return False
        now = self._monotonic()
        if (
            self._last_reaction_sweep is not None
            and now - self._last_reaction_sweep < self._reaction_sweep_seconds
        ):
            return False
        self._last_reaction_sweep = now
        try:
            self._reaction_sweep()
        except Exception as exc:
            self._conn.rollback()
            _log(
                "feedback.reaction_sweep_failed",
                worker=self._worker_id,
                error=exception_for_log(exc),
            )
        return True

    def _fail(self, job: ClaimedJob, *, error: str) -> None:
        if job.attempts_exhausted:
            self._conn.execute(dead_letter_job_sql(), {"job_id": job.job_id, "error": error})
            self._conn.commit()
            _log(
                "job.dead_lettered",
                job_id=job.job_id,
                job_type=job.job_type,
                attempt=job.attempts,
                max_attempts=job.max_attempts,
                error=error,
            )
            return
        backoff = compute_backoff_seconds(
            job.attempts,
            base_seconds=self._retry_base_seconds,
            cap_seconds=self._retry_cap_seconds,
        )
        self._conn.execute(
            retry_job_sql(),
            {"job_id": job.job_id, "backoff_seconds": backoff, "error": error},
        )
        self._conn.commit()
        _log(
            "job.retry_scheduled",
            job_id=job.job_id,
            job_type=job.job_type,
            attempt=job.attempts,
            max_attempts=job.max_attempts,
            backoff_seconds=backoff,
            error=error,
        )


def github_app_credentials_present(environ: Mapping[str, str] | None = None) -> bool:
    """True when both GitHub App credential env vars are set and non-blank.

    The seam that decides Stage 2 vs Stage 1 wiring without constructing a
    config (construction validates the PEM and would raise on a half-set
    environment). Mirrors ``GithubAppConfig.from_env``'s env var names so the
    chooser and the loader can never disagree about what "configured" means.
    """

    from cortex.hosted.github_app_auth import (
        GITHUB_APP_ID_ENV,
        GITHUB_APP_PRIVATE_KEY_ENV,
    )

    env = environ if environ is not None else os.environ
    return bool(env.get(GITHUB_APP_ID_ENV, "").strip()) and bool(
        env.get(GITHUB_APP_PRIVATE_KEY_ENV, "").strip()
    )


def build_worker_registry(
    *,
    recorder: ArrivalRecorder,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[[str], Any] | None = None,
    model_resolver: Callable[[], Any] | None = None,
) -> HandlerRegistry:
    """Choose the worker's handler registry from the environment.

    When the GitHub App credentials are present, the worker runs the Stage 2
    stateless review registry (``stateless_review.build_review_registry``): a
    real ``github.pull_request`` handler that fetches/evaluates/comments and
    forgets. Without them it falls back to the Stage 1 stub registry
    (``build_default_registry``), which records arrivals on the ledger. The
    two paths are a clean either/or, never a silent partial — the chosen
    registry is logged. ``stateless_review`` is imported lazily here so the
    worker module stays free of the command-layer import graph it pulls in.
    """

    from cortex.hosted.github_installations import GithubInstallationStore

    installation_store = GithubInstallationStore(recorder.conn)
    resolving_recorder = replace(
        recorder,
        identity_resolver=lambda job: _resolve_job_installation_identity(installation_store, job),
    )

    if not github_app_credentials_present(environ) and client_factory is None:
        _log("worker.registry_selected", registry="default", reason="github_app_unconfigured")
        registry = build_default_registry(resolving_recorder)
        _register_installation_handlers(registry, installation_store)
        return registry

    from cortex.hosted.github_app_auth import (
        GithubAppConfig,
        GithubInstallationClient,
        InstallationTokenSource,
    )
    from cortex.hosted.stateless_review import (
        PullRequestEvent,
        ReviewHandlerConfig,
        build_review_registry,
        default_model_resolver,
    )

    env = environ if environ is not None else os.environ
    if client_factory is None:
        config = GithubAppConfig.from_env(env)
        token_source = InstallationTokenSource(config)

        def resolved_client_factory(installation_id: str) -> GithubInstallationClient:
            return GithubInstallationClient(token_source, installation_id)

        selected_client_factory: Callable[[str], Any] = resolved_client_factory
    else:
        selected_client_factory = client_factory

    from cortex.hosted.review_rollout import ReviewRolloutStore

    rollout_store = ReviewRolloutStore(resolving_recorder.conn)

    def rollout_enabled(event: PullRequestEvent) -> bool:
        return rollout_store.is_enabled(f"{event.owner}/{event.repo}")

    def identity_for_event(event: PullRequestEvent) -> Any | None:
        return installation_store.resolve(
            installation_id=event.installation_id,
            repo_full_name=f"{event.owner}/{event.repo}",
        )

    # Posting is opt-in and OFF by default: the worker dry-runs (evaluates and
    # logs the comment it would post) until CORTEX_REVIEW_DRY_RUN is explicitly
    # set to a false-y value. This keeps a freshly deployed worker safe — it
    # cannot post to a customer PR until an operator deliberately flips it.
    dry_run = _env_flag(env.get(REVIEW_DRY_RUN_ENV), default=True)
    token_budget = _env_positive_int(
        env.get(REVIEW_TOKEN_BUDGET_ENV), default=DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET
    )
    # Reply feedback capture (cortex#393/#394) is wired when a tenant is
    # resolvable from the issue_comment payload's installation/repo pair. A
    # missing binding fails that job visibly instead of recording feedback
    # under a static or deterministic stand-in tenant.
    issue_comment_handler = _maybe_build_feedback_handler(
        recorder=resolving_recorder,
        client_factory=selected_client_factory,
        installation_store=installation_store,
    )
    _log(
        "worker.registry_selected",
        registry="stateless_review",
        reason="github_app_configured",
        dry_run=dry_run,
        token_budget=token_budget,
        feedback_capture=issue_comment_handler is not None,
    )
    registry = build_review_registry(
        client_factory=selected_client_factory,
        model_resolver=model_resolver or default_model_resolver(),
        config=ReviewHandlerConfig(dry_run=dry_run, token_budget=token_budget),
        issue_comment_handler=issue_comment_handler,
        rollout_checker=rollout_enabled,
        identity_resolver=identity_for_event,
    )
    _register_installation_handlers(registry, installation_store)
    return registry


def _register_installation_handlers(registry: HandlerRegistry, installation_store: Any) -> None:
    from cortex.hosted.github_installations import (
        record_installation_event,
        record_installation_repositories_event,
    )

    def handle_installation(job: ClaimedJob) -> Mapping[str, Any]:
        result = {"job_type": job.job_type}
        result.update(record_installation_event(installation_store, job.payload))
        return result

    def handle_installation_repositories(job: ClaimedJob) -> Mapping[str, Any]:
        result = {"job_type": job.job_type}
        result.update(record_installation_repositories_event(installation_store, job.payload))
        return result

    registry.register("github.installation", handle_installation)
    registry.register("github.installation_repositories", handle_installation_repositories)


def _resolve_job_installation_identity(installation_store: Any, job: ClaimedJob) -> Any | None:
    body = job.payload.get("body")
    event_body: Mapping[str, Any] = body if isinstance(body, Mapping) else job.payload
    installation = event_body.get("installation")
    repository = event_body.get("repository")
    if not isinstance(installation, Mapping) or not isinstance(repository, Mapping):
        return None
    installation_id = installation.get("id")
    repo_full_name = _repo_full_name_from_payload(repository)
    if installation_id is None or repo_full_name is None:
        return None
    return installation_store.resolve(
        installation_id=str(installation_id),
        repo_full_name=repo_full_name,
    )


def _repo_full_name_from_payload(repository: Mapping[str, Any]) -> str | None:
    full_name = repository.get("full_name")
    if isinstance(full_name, str) and full_name.strip():
        return full_name
    owner = repository.get("owner")
    name = repository.get("name")
    if isinstance(owner, Mapping):
        owner_login = owner.get("login")
        if isinstance(owner_login, str) and owner_login.strip() and isinstance(name, str):
            return f"{owner_login}/{name}"
    return None


def _maybe_build_feedback_handler(
    *,
    recorder: ArrivalRecorder,
    client_factory: Callable[[str], Any],
    installation_store: Any,
) -> JobHandler | None:
    """Build the issue_comment reply-feedback handler over installation identity."""

    from cortex.hosted.review_feedback_capture import handle_issue_comment_feedback

    conn = recorder.conn

    def handle(job: ClaimedJob) -> Mapping[str, Any]:
        return handle_issue_comment_feedback(
            job,
            conn=conn,
            client_factory=client_factory,
            identity_resolver=lambda event: installation_store.resolve(
                installation_id=event.installation_id,
                repo_full_name=f"{event.owner}/{event.repo}",
            ),
        )

    return handle


def build_reaction_sweep(
    *,
    recorder: ArrivalRecorder,
    environ: Mapping[str, str] | None = None,
) -> tuple[Callable[[], Mapping[str, Any]], float] | None:
    """Build the scheduled reaction sweep when its preconditions hold.

    Needs GitHub App credentials (to read comments/reactions) plus a non-zero
    ``CORTEX_REACTION_POLL_SECONDS``. Tenant identity is resolved per target
    from stored installation bindings, so one sweep can cover multiple tenants
    without a shared env tenant.
    """

    env = environ if environ is not None else os.environ
    raw_interval = env.get(REACTION_POLL_SECONDS_ENV, "").strip()
    interval = DEFAULT_REACTION_POLL_SECONDS
    if raw_interval:
        try:
            interval = float(raw_interval)
        except ValueError as exc:
            raise ServiceConfigError(
                f"{REACTION_POLL_SECONDS_ENV} must be a number of seconds; got {raw_interval!r}"
            ) from exc
    if interval <= 0:
        _log("worker.reaction_sweep_disabled", reason="interval_zero")
        return None
    if not github_app_credentials_present(env):
        _log("worker.reaction_sweep_disabled", reason="github_app_unconfigured")
        return None

    from cortex.hosted.github_app_auth import (
        GithubAppConfig,
        GithubInstallationClient,
        InstallationTokenSource,
    )
    from cortex.hosted.github_installations import GithubInstallationStore
    from cortex.hosted.reaction_sweep import run_reaction_sweep

    token_source = InstallationTokenSource(GithubAppConfig.from_env(env))
    conn = recorder.conn
    installation_store = GithubInstallationStore(conn)

    def sweep() -> Mapping[str, Any]:
        return run_reaction_sweep(
            conn,
            lambda installation_id: GithubInstallationClient(token_source, installation_id),
            identity_resolver=lambda target: installation_store.resolve(
                installation_id=target.installation_id,
                repo_full_name=target.repo_full_name,
            ),
        )

    _log("worker.reaction_sweep_enabled", interval_seconds=interval)
    return sweep, interval


def install_signal_handlers(stop: threading.Event) -> None:
    """Wire SIGTERM/SIGINT to a graceful stop of the polling loop."""

    def _handle(signum: int, _frame: types.FrameType | None) -> None:
        _log("worker.shutdown_requested", signal=signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main() -> None:
    """``cortex-worker`` console entrypoint."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = ServiceConfig.from_env()
    if config.database_url is None:
        # The worker exists to drain the Postgres queue; without a database
        # there is nothing to poll and starting would fake liveness.
        raise ServiceConfigError(
            "DATABASE_URL is required for cortex-worker; set it to the hosted "
            "(compass) Postgres DSN — see docs/hosted-deploy.md"
        )
    conn = connect(config.database_url)
    if config.apply_schema_on_start:
        result = apply_schema(conn)
        _log("worker.schema_applied", detail=result.describe())
    recorder = ArrivalRecorder(
        conn=conn,
        tenant_id=config.tenant_id,
        source_id=config.source_id,
        static_tenant_fallback_enabled=config.static_tenant_fallback,
    )
    sweep_config = build_reaction_sweep(recorder=recorder)
    worker = Worker(
        conn=conn,
        registry=build_worker_registry(recorder=recorder),
        poll_interval_seconds=config.poll_interval_seconds,
        stale_claim_seconds=config.stale_claim_seconds,
        payload_prune_grace_seconds=_env_nonnegative_float(
            os.environ.get(JOB_PAYLOAD_PRUNE_GRACE_SECONDS_ENV),
            default=DEFAULT_JOB_PAYLOAD_PRUNE_GRACE_SECONDS,
            name=JOB_PAYLOAD_PRUNE_GRACE_SECONDS_ENV,
        ),
        reaction_sweep=sweep_config[0] if sweep_config else None,
        reaction_sweep_seconds=sweep_config[1] if sweep_config else 900.0,
    )
    stop = threading.Event()
    install_signal_handlers(stop)
    try:
        worker.run(stop)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
