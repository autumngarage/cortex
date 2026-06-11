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
in the hosted ledger as a ``source.event_received`` event when a
tenant/source mapping is configured (``CORTEX_TENANT_ID`` /
``CORTEX_SOURCE_ID``). Without a mapping the result names the gap instead
of pretending — Stage 2 (#386) replaces the static mapping with
installation-based tenant resolution and real PR-evaluation jobs (#388)
registered on this same registry.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
)
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
    ledger_event_insert_sql,
)
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
        raise ServiceConfigError(
            f"{REVIEW_TOKEN_BUDGET_ENV} must be >= 1; got {value}"
        )
    return value


def _log(event: str, **fields: Any) -> None:
    """One structured JSON log line per worker transition."""

    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


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

    When no tenant/source mapping is configured the recorder reports the
    gap in the job result instead of writing nothing silently; Stage 2's
    installation-based tenant resolution (#386) replaces the static
    mapping.
    """

    conn: HostedConnection
    tenant_id: str | None
    source_id: str | None

    def record(self, job: ClaimedJob) -> dict[str, Any]:
        if self.tenant_id is None or self.source_id is None:
            return {
                "ledger_recorded": False,
                "reason": "tenant_mapping_unconfigured",
                "remediation": "set CORTEX_TENANT_ID and CORTEX_SOURCE_ID on the worker service",
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
            tenant_id=self.tenant_id,
            source_id=self.source_id,
            event_type=LedgerEventType.SOURCE_EVENT_RECEIVED,
            actor=ActorRef(actor_type="github-webhook", actor_id=delivery),
            occurred_at=occurred_at,
            idempotency_key=derive_idempotency_key(
                source_id=self.source_id,
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
            return {"ledger_recorded": False, "reason": "already_recorded", "delivery": delivery}
        return {"ledger_recorded": True, "ledger_event_id": str(row[0]), "delivery": delivery}


def _stub_github_handler(recorder: ArrivalRecorder) -> JobHandler:
    def handle(job: ClaimedJob) -> Mapping[str, Any]:
        result = {"handled": True, "job_type": job.job_type}
        result.update(recorder.record(job))
        return result

    return handle


def build_default_registry(recorder: ArrivalRecorder) -> HandlerRegistry:
    """The Stage 1 registry: stub GitHub handlers over one shared recorder."""

    registry = HandlerRegistry()
    registry.register("github.pull_request", _stub_github_handler(recorder))
    registry.register("github.issue_comment", _stub_github_handler(recorder))
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
    ) -> None:
        if poll_interval_seconds <= 0:
            raise HostedJobError(
                f"poll_interval_seconds must be positive, got {poll_interval_seconds}"
            )
        if stale_claim_seconds <= 0:
            raise HostedJobError(
                f"stale_claim_seconds must be positive, got {stale_claim_seconds}"
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
            self._fail(job, error=f"{type(exc).__name__}: {exc}")
            return True
        # Operator-internal review cost (cortex#547): persist one append-only
        # row and emit one structured log line for a successful review, in the
        # SAME transaction as the job completion so a redelivery is atomic. A
        # DB error here fails the job visibly (it is a real failure); a missing
        # cost / non-review result is a visible skip, never silent.
        self._record_review_cost(job, result)
        self._conn.execute(
            complete_job_sql(),
            {
                "job_id": job.job_id,
                "result": json.dumps(dict(result), sort_keys=True, default=str),
            },
        )
        self._conn.commit()
        _log("job.succeeded", job_id=job.job_id, job_type=job.job_type, result=dict(result))
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
            record = ReviewCostRecord(
                tenant_id=event.tenant_id,
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
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        row = self._conn.execute(
            review_cost_insert_sql(), record.as_insert_parameters()
        ).fetchone()
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
            processed = True
            while processed and not stop.is_set():
                processed = self.run_once()
            if not stop.is_set():
                self._sleep(self._poll_interval_seconds)
        _log("worker.stopped", worker=self._worker_id)

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
    *, recorder: ArrivalRecorder, environ: Mapping[str, str] | None = None
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

    if not github_app_credentials_present(environ):
        _log("worker.registry_selected", registry="default", reason="github_app_unconfigured")
        return build_default_registry(recorder)

    from cortex.hosted.github_app_auth import (
        GithubAppConfig,
        GithubInstallationClient,
        InstallationTokenSource,
    )
    from cortex.hosted.stateless_review import (
        ReviewHandlerConfig,
        build_review_registry,
        default_model_resolver,
    )

    env = environ if environ is not None else os.environ
    config = GithubAppConfig.from_env(env)
    token_source = InstallationTokenSource(config)

    def client_factory(installation_id: str) -> GithubInstallationClient:
        return GithubInstallationClient(token_source, installation_id)

    # Posting is opt-in and OFF by default: the worker dry-runs (evaluates and
    # logs the comment it would post) until CORTEX_REVIEW_DRY_RUN is explicitly
    # set to a false-y value. This keeps a freshly deployed worker safe — it
    # cannot post to a customer PR until an operator deliberately flips it.
    dry_run = _env_flag(env.get(REVIEW_DRY_RUN_ENV), default=True)
    token_budget = _env_positive_int(
        env.get(REVIEW_TOKEN_BUDGET_ENV), default=DEFAULT_HOSTED_REVIEW_TOKEN_BUDGET
    )
    # Reply feedback capture (cortex#393/#394) is wired when a tenant is
    # configured: the issue_comment handler writes ground-truth reply events to
    # the same connection the worker uses. Without a tenant mapping the
    # handler cannot key feedback to a tenant, so the issue_comment slot stays
    # the Stage 1 stub — the gap is named in the log, never silent.
    issue_comment_handler = _maybe_build_feedback_handler(
        recorder=recorder, client_factory=client_factory
    )
    _log(
        "worker.registry_selected",
        registry="stateless_review",
        reason="github_app_configured",
        dry_run=dry_run,
        token_budget=token_budget,
        feedback_capture=issue_comment_handler is not None,
    )
    return build_review_registry(
        client_factory=client_factory,
        model_resolver=default_model_resolver(),
        config=ReviewHandlerConfig(dry_run=dry_run, token_budget=token_budget),
        issue_comment_handler=issue_comment_handler,
    )


def _maybe_build_feedback_handler(
    *,
    recorder: ArrivalRecorder,
    client_factory: Callable[[str], Any],
) -> JobHandler | None:
    """Build the issue_comment reply-feedback handler when a tenant is mapped.

    Returns ``None`` (leaving the Stage 1 stub) when no tenant id is configured
    — feedback must key to a tenant, and a handler that cannot is worse than the
    honest stub. The handler shares the worker's connection so a reply event and
    the job completion commit together.
    """

    if recorder.tenant_id is None:
        return None

    from cortex.hosted.review_feedback_capture import handle_issue_comment_feedback

    tenant_id = recorder.tenant_id
    conn = recorder.conn

    def handle(job: ClaimedJob) -> Mapping[str, Any]:
        return handle_issue_comment_feedback(
            job, conn=conn, client_factory=client_factory, tenant_id=tenant_id
        )

    return handle


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
        conn=conn, tenant_id=config.tenant_id, source_id=config.source_id
    )
    worker = Worker(
        conn=conn,
        registry=build_worker_registry(recorder=recorder),
        poll_interval_seconds=config.poll_interval_seconds,
        stale_claim_seconds=config.stale_claim_seconds,
    )
    stop = threading.Event()
    install_signal_handlers(stop)
    try:
        worker.run(stop)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
