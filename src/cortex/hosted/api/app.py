"""Hosted Cortex HTTP API shell (cortex#470).

Transport choice: the Python standard library (``http.server``) and nothing
else. The shell exposes three small endpoints (healthz, version, one
webhook receiver) that parse headers and JSON and write one job row — a
framework dependency (starlette/uvicorn/fastapi) would add a supply chain
and a second runtime model to operate for no behavioral gain at this
surface area, and the repo's vendor-boundary discipline favors stdlib
transports. If the surface grows real routing/middleware needs, promoting
to a framework is a deliberate later decision, not a default.

Structure: pure request handling (:func:`handle_request`) is separated from
the socket transport (:func:`build_server`) so tests exercise the full
endpoint logic — signature verification, idempotent enqueue, degraded
modes — without binding ports. One code path serves tests and production;
only the I/O boundary differs.

Endpoint contract (cortex#470):

- ``GET /healthz`` — process liveness plus a DB round-trip reporting the
  recorded schema version. Missing/unreachable ``DATABASE_URL`` produces a
  degraded JSON body naming what failed, never a crash and never DSN
  details in the response.
- ``GET /version`` — build/version info.
- ``POST /webhooks/github`` — HMAC-SHA256 verification (constant-time)
  against ``GITHUB_WEBHOOK_SECRET``; 401 on mismatch/missing signature;
  idempotent job persistence keyed by the delivery GUID; 202 fast with no
  inline processing.
"""

from __future__ import annotations

import json
import logging
import platform
import signal
import threading
import types
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import cortex
from cortex.hosted.api.config import ServiceConfig
from cortex.hosted.api.webhooks import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    WebhookValidationError,
    job_request_from_delivery,
    parse_json_body,
    verify_signature,
)
from cortex.hosted.db import HostedConnection, HostedDbError, connect
from cortex.hosted.jobs import enqueue_job_sql
from cortex.hosted.migrations import HostedMigrationError, schema_status
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION

logger = logging.getLogger("cortex.hosted.api")

SERVICE_NAME = "cortex-api"

# GitHub documents a 25 MB cap on webhook payloads; the bound is theirs,
# not ours. Anything larger is not a GitHub delivery.
MAX_WEBHOOK_BODY_BYTES = 25 * 1024 * 1024


@dataclass(frozen=True)
class ApiResponse:
    """One JSON response: status code plus a JSON-object body."""

    status: int
    body: Mapping[str, Any]

    def encoded(self) -> bytes:
        return json.dumps(dict(self.body), sort_keys=True).encode("utf-8")


@dataclass(frozen=True)
class ApiDependencies:
    """Injection point for everything the shell touches beyond the socket.

    ``connect`` produces a policy-conformant DB connection; tests swap in
    the in-memory fake. ``now`` exists so arrival timestamps are testable.
    """

    connect: Callable[[str], HostedConnection] = connect
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


def handle_request(
    config: ServiceConfig,
    deps: ApiDependencies,
    method: str,
    path: str,
    headers: Mapping[str, str],
    body: bytes,
) -> ApiResponse:
    """Route one request. Pure with respect to the HTTP transport."""

    route = (method.upper(), path.split("?", 1)[0])
    if route == ("GET", "/healthz"):
        return _healthz(config, deps)
    if route == ("GET", "/version"):
        return _version(config)
    if route == ("POST", "/webhooks/github"):
        return _github_webhook(config, deps, headers, body)
    if route[1] in {"/healthz", "/version", "/webhooks/github"}:
        return ApiResponse(405, {"error": f"method {route[0]} not allowed for {route[1]}"})
    return ApiResponse(404, {"error": f"no route for {route[1]}"})


def _version(config: ServiceConfig) -> ApiResponse:
    return ApiResponse(
        200,
        {
            "service": SERVICE_NAME,
            "version": cortex.__version__,
            "hosted_schema_version": HOSTED_SCHEMA_VERSION,
            "commit": config.commit_sha,
            "python": platform.python_version(),
        },
    )


def _healthz(config: ServiceConfig, deps: ApiDependencies) -> ApiResponse:
    """Liveness plus DB round-trip. Degrades to JSON, never to a crash.

    The degraded body names what failed (reason code) without echoing DSN
    material; the full error is logged server-side for operators.
    """

    db_report: dict[str, Any] = {
        "configured": config.database_url is not None,
        "reachable": False,
        "schema_version": None,
    }
    status = "ok"
    if config.database_url is None:
        status = "degraded"
        db_report["reason"] = "database_url_missing"
    else:
        try:
            conn = deps.connect(config.database_url)
        except HostedDbError as exc:
            status = "degraded"
            db_report["reason"] = "connect_failed"
            logger.error("healthz database connect failed: %s", exc)
        else:
            try:
                report = schema_status(conn)
                db_report["reachable"] = True
                db_report["schema_version"] = report.version
                db_report["table_count"] = report.table_count
                if report.version != HOSTED_SCHEMA_VERSION:
                    status = "degraded"
                    db_report["reason"] = "schema_version_mismatch"
                    db_report["expected_schema_version"] = HOSTED_SCHEMA_VERSION
            except (HostedDbError, HostedMigrationError) as exc:
                status = "degraded"
                db_report["reason"] = "schema_status_failed"
                logger.error("healthz schema status failed: %s", exc)
            finally:
                conn.close()
    return ApiResponse(
        200,
        {
            "service": SERVICE_NAME,
            "status": status,
            "version": cortex.__version__,
            "db": db_report,
        },
    )


def _github_webhook(
    config: ServiceConfig,
    deps: ApiDependencies,
    headers: Mapping[str, str],
    body: bytes,
) -> ApiResponse:
    """Verify, persist idempotently, answer 202. No inline processing."""

    if config.github_webhook_secret is None:
        # Visible refusal: accepting unverifiable deliveries would be a
        # silent authentication bypass, and 2xx-ing then dropping them
        # would be a silent loss. 503 tells GitHub (and the operator) the
        # receiver is not ready.
        logger.error("webhook refused: GITHUB_WEBHOOK_SECRET is not configured")
        return ApiResponse(503, {"error": "webhook receiver not configured"})
    if not verify_signature(
        config.github_webhook_secret, body, _header(headers, SIGNATURE_HEADER)
    ):
        logger.warning("webhook rejected: signature verification failed")
        return ApiResponse(401, {"error": "signature verification failed"})

    event = _header(headers, EVENT_HEADER)
    delivery = _header(headers, DELIVERY_HEADER)
    if event is None or delivery is None:
        return ApiResponse(
            400,
            {"error": f"missing required headers {EVENT_HEADER} and/or {DELIVERY_HEADER}"},
        )
    try:
        payload = parse_json_body(body)
        request = job_request_from_delivery(
            event=event,
            delivery=delivery,
            body=payload,
            received_at_iso=deps.now().isoformat(),
        )
    except WebhookValidationError as exc:
        return ApiResponse(400, {"error": str(exc)})

    if config.database_url is None:
        # Same no-silent-loss rule as the missing secret: a 202 we cannot
        # back with a job row would drop the delivery invisibly.
        logger.error(
            "webhook refused: DATABASE_URL is not configured; delivery %s not persisted",
            delivery,
        )
        return ApiResponse(503, {"error": "job persistence not configured"})
    try:
        conn = deps.connect(config.database_url)
    except HostedDbError as exc:
        logger.error("webhook enqueue connect failed for delivery %s: %s", delivery, exc)
        return ApiResponse(503, {"error": "job persistence unavailable"})
    try:
        row = conn.execute(enqueue_job_sql(), request.as_insert_parameters()).fetchone()
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error("webhook enqueue failed for delivery %s: %s", delivery, exc)
        return ApiResponse(503, {"error": "job persistence failed"})
    finally:
        conn.close()

    if row is None:
        logger.info(
            json.dumps(
                {"event": "webhook.duplicate", "delivery": delivery, "job_type": request.job_type},
                sort_keys=True,
            )
        )
        return ApiResponse(202, {"status": "duplicate", "delivery": delivery})
    job_id = str(row[0])
    logger.info(
        json.dumps(
            {
                "event": "webhook.queued",
                "delivery": delivery,
                "job_id": job_id,
                "job_type": request.job_type,
            },
            sort_keys=True,
        )
    )
    return ApiResponse(202, {"status": "queued", "delivery": delivery, "job_id": job_id})


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup over a plain mapping."""

    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


# ---------------------------------------------------------------------------
# Socket transport
# ---------------------------------------------------------------------------


def build_server(
    config: ServiceConfig, deps: ApiDependencies | None = None
) -> ThreadingHTTPServer:
    """App factory: bind the request router to a threading HTTP server."""

    resolved_deps = deps if deps is not None else ApiDependencies()

    class _Handler(BaseHTTPRequestHandler):
        # Avoid leaking the default Python/BaseHTTP server banner.
        server_version = SERVICE_NAME
        sys_version = ""

        def do_GET(self) -> None:
            self._respond(b"")

        def do_POST(self) -> None:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                self._write(ApiResponse(400, {"error": "malformed Content-Length"}))
                return
            if length > MAX_WEBHOOK_BODY_BYTES:
                self._write(
                    ApiResponse(
                        413,
                        {"error": f"body exceeds {MAX_WEBHOOK_BODY_BYTES} bytes"},
                    )
                )
                return
            self._respond(self.rfile.read(length))

        def _respond(self, body: bytes) -> None:
            response = handle_request(
                config,
                resolved_deps,
                self.command,
                self.path,
                dict(self.headers.items()),
                body,
            )
            self._write(response)

        def _write(self, response: ApiResponse) -> None:
            encoded = response.encoded()
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            logger.info("%s %s", self.address_string(), format % args)

    return ThreadingHTTPServer((config.host, config.port), _Handler)


def main() -> None:
    """``cortex-api`` console entrypoint."""

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    config = ServiceConfig.from_env()
    server = build_server(config)
    logger.info(
        json.dumps(
            {
                "event": "api.started",
                "service": SERVICE_NAME,
                "version": cortex.__version__,
                "host": config.host,
                "port": config.port,
                "database_configured": config.database_url is not None,
                "webhook_secret_configured": config.github_webhook_secret is not None,
            },
            sort_keys=True,
        )
    )

    def _shutdown(signum: int, _frame: types.FrameType | None) -> None:
        logger.info(json.dumps({"event": "api.shutdown_requested", "signal": signum}))
        # shutdown() blocks until serve_forever exits; run it off the main
        # thread so the signal handler itself cannot deadlock the loop.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        logger.info(json.dumps({"event": "api.stopped"}))


if __name__ == "__main__":
    main()
