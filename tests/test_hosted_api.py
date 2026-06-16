"""Tests for the hosted API shell (cortex#470).

``handle_request`` is exercised directly (the transport-free code path the
socket handler also calls), with an in-memory ``FakeJobDb`` standing in for
Postgres — the same fake-db idiom as ``tests/test_hosted_push.py``. Live
Postgres coverage stays env-gated in ``tests/test_hosted_api_integration.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest

import cortex
from cortex.hosted.api.app import (
    MAX_WEBHOOK_BODY_BYTES,
    ApiDependencies,
    handle_request,
)
from cortex.hosted.api.config import ServiceConfig, ServiceConfigError
from cortex.hosted.api.webhooks import (
    WebhookValidationError,
    job_request_from_delivery,
    verify_signature,
)
from cortex.hosted.db import HostedDbError
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION

SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class FakeJobDb:
    """In-memory slice of the jobs + schema_status SQL surface."""

    def __init__(self, schema_version: int | None = HOSTED_SCHEMA_VERSION) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.schema_version = schema_version
        self.committed = 0
        self.closed = False

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> FakeCursor:
        q = query.strip()
        p = dict(params or {})
        if q.startswith("INSERT INTO cortex_hosted.jobs"):
            key = str(p["idempotency_key"])
            if key in self.jobs:
                return FakeCursor([])
            job_id = str(uuid4())
            self.jobs[key] = {
                "job_id": job_id,
                "job_type": str(p["job_type"]),
                "payload": json.loads(str(p["payload"])),
                "status": "queued",
            }
            return FakeCursor([(job_id,)])
        if q.startswith("SELECT to_regclass"):
            return FakeCursor([("cortex_hosted.schema_migrations",)])
        if q.startswith("SELECT max(version)"):
            return FakeCursor([(self.schema_version,)])
        if q.startswith("SELECT count(*)"):
            return FakeCursor([(15,)])
        raise AssertionError(f"FakeJobDb saw unexpected SQL: {q[:80]}")

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _config(**overrides: Any) -> ServiceConfig:
    defaults: dict[str, Any] = {
        "database_url": "postgresql://cortex@db.internal:5432/cortex",
        "github_webhook_secret": SECRET,
    }
    defaults.update(overrides)
    return ServiceConfig(**defaults)


def _deps(db: FakeJobDb) -> ApiDependencies:
    return ApiDependencies(connect=lambda _url: db)


def _post_webhook(
    config: ServiceConfig,
    deps: ApiDependencies,
    body: bytes,
    *,
    signature: str | None = None,
    event: str | None = "pull_request",
    delivery: str | None = "d1234-guid",
) -> Any:
    headers: dict[str, str] = {}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    if event is not None:
        headers["X-GitHub-Event"] = event
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    return handle_request(config, deps, "POST", "/webhooks/github", headers, body)


# ---------------------------------------------------------------------------
# ServiceConfig: fail-closed env parsing
# ---------------------------------------------------------------------------


def test_config_from_env_parses_a_full_environment() -> None:
    tenant = str(uuid4())
    source = str(uuid4())
    config = ServiceConfig.from_env(
        {
            "DATABASE_URL": "postgresql://u:p@host:5432/db?sslmode=require",
            "GITHUB_WEBHOOK_SECRET": "s3cret",
            "PORT": "9000",
            "CORTEX_TENANT_ID": tenant,
            "CORTEX_SOURCE_ID": source,
            "CORTEX_STATIC_TENANT_FALLBACK": "true",
            "CORTEX_APPLY_SCHEMA_ON_START": "true",
        }
    )
    assert config.port == 9000
    assert config.tenant_id == tenant
    assert config.static_tenant_fallback is True
    assert config.apply_schema_on_start is True


def test_config_missing_optional_pieces_is_valid() -> None:
    config = ServiceConfig.from_env({})
    assert config.database_url is None
    assert config.github_webhook_secret is None


def test_config_rejects_malformed_port() -> None:
    with pytest.raises(ServiceConfigError, match="PORT must be an integer"):
        ServiceConfig.from_env({"PORT": "eighty"})
    with pytest.raises(ServiceConfigError, match=r"PORT must be in 0\.\.65535"):
        ServiceConfig.from_env({"PORT": "70000"})
    with pytest.raises(ServiceConfigError, match=r"PORT must be in 0\.\.65535"):
        ServiceConfig.from_env({"PORT": "-1"})


def test_config_rejects_blank_but_set_database_url() -> None:
    with pytest.raises(ServiceConfigError, match="DATABASE_URL is set but blank"):
        ServiceConfig.from_env({"DATABASE_URL": "   "})


def test_config_rejects_non_uuid_tenant_id() -> None:
    with pytest.raises(ServiceConfigError, match="CORTEX_TENANT_ID must be a UUID"):
        ServiceConfig.from_env({"CORTEX_TENANT_ID": "not-a-uuid", "CORTEX_SOURCE_ID": str(uuid4())})


def test_config_rejects_unpaired_tenant_mapping() -> None:
    with pytest.raises(ServiceConfigError, match="must be set together"):
        ServiceConfig.from_env({"CORTEX_TENANT_ID": str(uuid4())})


def test_config_static_tenant_fallback_requires_the_static_pair() -> None:
    with pytest.raises(ServiceConfigError, match="CORTEX_STATIC_TENANT_FALLBACK requires"):
        ServiceConfig.from_env({"CORTEX_STATIC_TENANT_FALLBACK": "1"})


def test_config_rejects_unrecognized_boolean_token() -> None:
    with pytest.raises(ServiceConfigError, match="CORTEX_APPLY_SCHEMA_ON_START"):
        ServiceConfig.from_env({"CORTEX_APPLY_SCHEMA_ON_START": "maybe"})


# ---------------------------------------------------------------------------
# Signature verification: constant-time HMAC-SHA256
# ---------------------------------------------------------------------------


def test_signature_valid_is_accepted() -> None:
    body = b'{"action":"opened"}'
    assert verify_signature(SECRET, body, _sign(body)) is True


def test_signature_mismatch_is_rejected() -> None:
    body = b'{"action":"opened"}'
    # Same length, wrong digest: the equal-length comparison path.
    wrong = _sign(body, secret="another-secret")
    assert verify_signature(SECRET, body, wrong) is False


def test_signature_missing_header_is_rejected() -> None:
    assert verify_signature(SECRET, b"{}", None) is False


def test_signature_wrong_scheme_is_rejected() -> None:
    body = b"{}"
    sha1_style = "sha1=" + hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()
    assert verify_signature(SECRET, body, sha1_style) is False
    assert verify_signature(SECRET, body, "sha256=") is False


def test_signature_tampered_body_is_rejected() -> None:
    signature = _sign(b'{"action":"opened"}')
    assert verify_signature(SECRET, b'{"action":"closed"}', signature) is False


def test_signature_refuses_empty_server_secret() -> None:
    with pytest.raises(WebhookValidationError, match="secret must not be empty"):
        verify_signature("", b"{}", "sha256=00")


# ---------------------------------------------------------------------------
# Delivery -> JobRequest translation
# ---------------------------------------------------------------------------


def test_job_request_from_delivery_keys_on_the_delivery_guid() -> None:
    request = job_request_from_delivery(
        event="pull_request",
        delivery="guid-1",
        body={"action": "opened"},
        received_at_iso="2026-06-10T12:00:00+00:00",
    )
    assert request.job_type == "github.pull_request"
    assert request.idempotency_key == "github-delivery:guid-1"
    assert request.payload["body"] == {"action": "opened"}


def test_job_request_from_delivery_rejects_malformed_event_and_guid() -> None:
    with pytest.raises(WebhookValidationError, match="malformed X-GitHub-Event"):
        job_request_from_delivery(
            event="Pull Request!",
            delivery="g",
            body={},
            received_at_iso="2026-06-10T12:00:00+00:00",
        )
    with pytest.raises(WebhookValidationError, match="exceeds"):
        job_request_from_delivery(
            event="ping",
            delivery="x" * 101,
            body={},
            received_at_iso="2026-06-10T12:00:00+00:00",
        )


# ---------------------------------------------------------------------------
# /healthz: liveness + DB round trip, degraded JSON, never a crash
# ---------------------------------------------------------------------------


def test_healthz_ok_reports_schema_version() -> None:
    db = FakeJobDb()
    response = handle_request(_config(), _deps(db), "GET", "/healthz", {}, b"")
    assert response.status == 200
    assert response.body["status"] == "ok"
    assert response.body["db"]["schema_version"] == HOSTED_SCHEMA_VERSION
    assert db.closed is True


def test_healthz_degrades_visibly_without_database_url() -> None:
    response = handle_request(
        _config(database_url=None), ApiDependencies(), "GET", "/healthz", {}, b""
    )
    assert response.status == 200
    assert response.body["status"] == "degraded"
    assert response.body["db"] == {
        "configured": False,
        "reachable": False,
        "schema_version": None,
        "reason": "database_url_missing",
    }


def test_healthz_degrades_visibly_when_connect_fails_and_leaks_no_dsn() -> None:
    def refuse(_url: str) -> Any:
        raise HostedDbError(
            "hosted Postgres connection failed (unreachable) for "
            "postgresql://cortex:***@db.internal:5432/cortex: boom"
        )

    response = handle_request(
        _config(), ApiDependencies(connect=refuse), "GET", "/healthz", {}, b""
    )
    assert response.status == 200
    assert response.body["status"] == "degraded"
    assert response.body["db"]["reason"] == "connect_failed"
    assert "db.internal" not in json.dumps(dict(response.body))


def test_healthz_flags_schema_version_drift() -> None:
    db = FakeJobDb(schema_version=HOSTED_SCHEMA_VERSION - 1)
    response = handle_request(_config(), _deps(db), "GET", "/healthz", {}, b"")
    assert response.body["status"] == "degraded"
    assert response.body["db"]["reason"] == "schema_version_mismatch"
    assert response.body["db"]["expected_schema_version"] == HOSTED_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# /version and routing
# ---------------------------------------------------------------------------


def test_version_endpoint_reports_build_info() -> None:
    response = handle_request(
        _config(commit_sha="abc1234"), ApiDependencies(), "GET", "/version", {}, b""
    )
    assert response.status == 200
    assert response.body["version"] == cortex.__version__
    assert response.body["hosted_schema_version"] == HOSTED_SCHEMA_VERSION
    assert response.body["commit"] == "abc1234"


def test_unknown_route_is_404_and_wrong_method_is_405() -> None:
    deps = ApiDependencies()
    assert handle_request(_config(), deps, "GET", "/nope", {}, b"").status == 404
    assert handle_request(_config(), deps, "POST", "/healthz", {}, b"").status == 405
    assert handle_request(_config(), deps, "GET", "/webhooks/github", {}, b"").status == 405


# ---------------------------------------------------------------------------
# POST /webhooks/github
# ---------------------------------------------------------------------------


def test_webhook_valid_delivery_persists_an_idempotent_job_and_returns_202() -> None:
    db = FakeJobDb()
    body = json.dumps({"action": "opened", "repository": {"full_name": "a/b"}}).encode()
    response = _post_webhook(_config(), _deps(db), body, signature=_sign(body))
    assert response.status == 202
    assert response.body["status"] == "queued"
    stored = db.jobs["github-delivery:d1234-guid"]
    assert stored["job_type"] == "github.pull_request"
    assert stored["payload"]["body"]["action"] == "opened"
    assert db.committed == 1


def test_webhook_redelivery_is_idempotent() -> None:
    db = FakeJobDb()
    body = json.dumps({"action": "opened"}).encode()
    first = _post_webhook(_config(), _deps(db), body, signature=_sign(body))
    replay = _post_webhook(_config(), _deps(db), body, signature=_sign(body))
    assert first.status == 202
    assert replay.status == 202
    assert replay.body["status"] == "duplicate"
    assert len(db.jobs) == 1


def test_webhook_bad_signature_is_401_and_persists_nothing() -> None:
    db = FakeJobDb()
    body = b'{"action":"opened"}'
    response = _post_webhook(_config(), _deps(db), body, signature=_sign(body, secret="wrong"))
    assert response.status == 401
    assert db.jobs == {}


def test_webhook_missing_signature_is_401() -> None:
    db = FakeJobDb()
    response = _post_webhook(_config(), _deps(db), b"{}", signature=None)
    assert response.status == 401
    assert db.jobs == {}


def test_webhook_without_configured_secret_refuses_visibly() -> None:
    db = FakeJobDb()
    response = _post_webhook(
        _config(github_webhook_secret=None), _deps(db), b"{}", signature="sha256=00"
    )
    assert response.status == 503
    assert "not configured" in str(response.body["error"])
    assert db.jobs == {}


def test_webhook_missing_event_headers_is_400() -> None:
    body = b"{}"
    response = _post_webhook(_config(), _deps(FakeJobDb()), body, signature=_sign(body), event=None)
    assert response.status == 400


def test_webhook_malformed_json_body_is_400() -> None:
    body = b"not json"
    response = _post_webhook(_config(), _deps(FakeJobDb()), body, signature=_sign(body))
    assert response.status == 400


def test_webhook_without_database_url_refuses_instead_of_dropping() -> None:
    body = b'{"action":"opened"}'
    response = _post_webhook(
        _config(database_url=None), ApiDependencies(), body, signature=_sign(body)
    )
    assert response.status == 503
    assert "persistence" in str(response.body["error"])


def test_webhook_headers_are_case_insensitive() -> None:
    db = FakeJobDb()
    body = b'{"action":"opened"}'
    headers = {
        "x-hub-signature-256": _sign(body),
        "X-GITHUB-EVENT": "issue_comment",
        "x-github-delivery": "guid-ci",
    }
    response = handle_request(_config(), _deps(db), "POST", "/webhooks/github", headers, body)
    assert response.status == 202
    assert db.jobs["github-delivery:guid-ci"]["job_type"] == "github.issue_comment"


def test_body_size_bound_derives_from_githubs_documented_cap() -> None:
    assert MAX_WEBHOOK_BODY_BYTES == 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# Socket smoke test: the stdlib transport serves the same code path
# ---------------------------------------------------------------------------


def test_built_server_serves_requests_over_a_real_socket() -> None:
    import threading
    import urllib.error
    import urllib.request

    from cortex.hosted.api.app import build_server

    db = FakeJobDb()
    # Port 0: the OS assigns a free port; no fixed-port flakiness.
    server = build_server(_config(host="127.0.0.1", port=0), _deps(db))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.socket.getsockname()[:2]
    base = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(f"{base}/healthz", timeout=5) as response:
            body = json.loads(response.read())
        assert body["service"] == "cortex-api"
        assert body["db"]["schema_version"] == HOSTED_SCHEMA_VERSION

        request = urllib.request.Request(f"{base}/nope", method="GET")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(request, timeout=5)
        assert excinfo.value.code == 404

        payload = b'{"action":"opened"}'
        webhook = urllib.request.Request(
            f"{base}/webhooks/github",
            data=payload,
            method="POST",
            headers={
                "X-Hub-Signature-256": _sign(payload),
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "socket-guid",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(webhook, timeout=5) as response:
            assert response.status == 202
            assert json.loads(response.read())["status"] == "queued"
        assert "github-delivery:socket-guid" in db.jobs

        # A negative Content-Length must not defeat the body cap by turning
        # the read into read-to-EOF (cortex security audit 2026-06-10, HIGH).
        # Drive it with a raw socket since urllib forbids negative lengths.
        import socket as _socket

        raw = (
            "POST /webhooks/github HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Length: -1\r\n"
            "X-GitHub-Event: pull_request\r\n"
            "X-GitHub-Delivery: neg-len\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        with _socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(raw)
            status_line = sock.makefile("rb").readline()
        assert b"400" in status_line
        assert "github-delivery:neg-len" not in db.jobs
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_handler_sets_a_socket_read_timeout() -> None:
    # Slowloris guard: a stalled read must not pin a handler thread forever.
    from cortex.hosted.api.app import SOCKET_READ_TIMEOUT_SECONDS, build_server

    server = build_server(_config(host="127.0.0.1", port=0), _deps(FakeJobDb()))
    try:
        timeout = getattr(server.RequestHandlerClass, "timeout", None)
        assert timeout == SOCKET_READ_TIMEOUT_SECONDS
        assert timeout is not None
    finally:
        server.server_close()


def test_post_without_content_length_is_411() -> None:
    import socket as _socket
    import threading

    from cortex.hosted.api.app import build_server

    server = build_server(_config(host="127.0.0.1", port=0), _deps(FakeJobDb()))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.socket.getsockname()[:2]
    try:
        raw = (
            "POST /webhooks/github HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "X-GitHub-Event: pull_request\r\n"
            "X-GitHub-Delivery: no-len\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        with _socket.create_connection((host, port), timeout=5) as sock:
            sock.sendall(raw)
            status_line = sock.makefile("rb").readline()
        assert b"411" in status_line
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
