"""Tests for GitHub App installation auth + REST client (cortex#386).

Every test runs fully offline: HTTP is a scripted opener, the clock is an
injected callable, and the JWT path uses a real (but locally generated) RSA
keypair so signatures are verifiable with the public key without any network.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from cortex.hosted.github_app_auth import (
    DEFAULT_API_ROOT,
    GITHUB_APP_ID_ENV,
    GITHUB_APP_PRIVATE_KEY_ENV,
    JWT_TTL_SECONDS,
    ChangedFile,
    DirectoryEntry,
    GithubApiError,
    GithubAppConfig,
    GithubAuthConfigError,
    GithubInstallationClient,
    InstallationToken,
    InstallationTokenSource,
    RetryPolicy,
)

# ---------------------------------------------------------------------------
# Offline fixtures: RSA keypair, scripted opener, deterministic clock
# ---------------------------------------------------------------------------


@dataclass
class _Keypair:
    private_pem: str
    public_pem: str


@pytest.fixture(scope="module")
def keypair() -> _Keypair:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return _Keypair(private_pem=private_pem, public_pem=public_pem)


@pytest.fixture
def config(keypair: _Keypair) -> GithubAppConfig:
    return GithubAppConfig(app_id="4023580", private_key_pem=keypair.private_pem)


class _FrozenClock:
    """A deterministic clock the token source/cache read instead of wall time."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@dataclass
class _CannedResponse:
    """One scripted HTTP outcome: either a body or a raised HTTPError."""

    status: int = 200
    body: bytes = b"{}"
    headers: dict[str, str] = field(default_factory=dict)
    http_error: bool = False


class _ScriptedOpener:
    """A urllib-shaped opener that pops canned responses per (METHOD, path).

    Records every request it served so tests can assert on auth headers,
    Accept headers, methods, and bodies — all without touching the network.
    """

    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], list[_CannedResponse]] = {}
        self.requests: list[urllib.request.Request] = []
        self.sleeps: list[float] = []

    def enqueue(self, method: str, path: str, response: _CannedResponse) -> None:
        self._queues.setdefault((method.upper(), path), []).append(response)

    def __call__(self, request: urllib.request.Request, *, timeout: float) -> Any:
        self.requests.append(request)
        split = urllib.parse.urlsplit(request.full_url)
        key = (request.get_method(), split.path)
        queue = self._queues.get(key)
        if not queue:
            raise AssertionError(f"no canned response for {key}; query={split.query!r}")
        canned = queue.pop(0)
        if canned.http_error:
            raise urllib.error.HTTPError(
                request.full_url,
                canned.status,
                "scripted",
                canned.headers,  # type: ignore[arg-type]
                BytesIO(canned.body),
            )
        return _FakeHttpResponse(canned)


class _FakeHttpResponse:
    def __init__(self, canned: _CannedResponse) -> None:
        self.status = canned.status
        self._body = canned.body
        self._headers = {k.lower(): v for k, v in canned.headers.items()}

    def read(self) -> bytes:
        return self._body

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name.lower(), default)

    def close(self) -> None:
        return None


def _token_response_at(
    clock: _FrozenClock, *, token: str = "ghs_secret_installation_token", expires_in: int = 3600
) -> bytes:
    expires_at = (clock() + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps({"token": token, "expires_at": expires_at}).encode("utf-8")


def _request_json(request: urllib.request.Request) -> Any:
    """Decode a request's JSON body (data is a bytes payload in these tests)."""

    data = request.data
    assert isinstance(data, bytes)
    return json.loads(data)


def _make_source(
    config: GithubAppConfig,
    opener: _ScriptedOpener,
    clock: _FrozenClock,
) -> InstallationTokenSource:
    return InstallationTokenSource(
        config,
        opener=opener,
        now=clock,
        sleep=opener.sleeps.append,
        retry_policy=RetryPolicy(max_retries=3, backoff_base_seconds=0.0, backoff_cap_seconds=0.0),
    )


def _make_client(
    config: GithubAppConfig,
    opener: _ScriptedOpener,
    clock: _FrozenClock,
) -> GithubInstallationClient:
    source = _make_source(config, opener, clock)
    return GithubInstallationClient(
        source,
        "12345",
        opener=opener,
        sleep=opener.sleeps.append,
        retry_policy=RetryPolicy(max_retries=3, backoff_base_seconds=0.0, backoff_cap_seconds=0.0),
    )


def _client_with_token(
    config: GithubAppConfig, opener: _ScriptedOpener, clock: _FrozenClock
) -> GithubInstallationClient:
    """A client whose first token exchange is already enqueued."""

    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(status=201, body=_token_response_at(clock)),
    )
    return _make_client(config, opener, clock)


# ---------------------------------------------------------------------------
# 1. Config fail-closed
# ---------------------------------------------------------------------------


def test_config_from_env_reads_both_variables(keypair: _Keypair) -> None:
    config = GithubAppConfig.from_env(
        {GITHUB_APP_ID_ENV: "4023580", GITHUB_APP_PRIVATE_KEY_ENV: keypair.private_pem}
    )
    assert config.app_id == "4023580"


@pytest.mark.parametrize("app_id", ["", "   "], ids=["empty", "whitespace"])
def test_config_rejects_blank_app_id(keypair: _Keypair, app_id: str) -> None:
    with pytest.raises(GithubAuthConfigError, match=GITHUB_APP_ID_ENV):
        GithubAppConfig(app_id=app_id, private_key_pem=keypair.private_pem)


def test_config_rejects_non_numeric_app_id(keypair: _Keypair) -> None:
    with pytest.raises(GithubAuthConfigError, match="numeric"):
        GithubAppConfig(app_id="not-a-number", private_key_pem=keypair.private_pem)


@pytest.mark.parametrize("pem", ["", "   "], ids=["empty", "whitespace"])
def test_config_rejects_blank_private_key(pem: str) -> None:
    with pytest.raises(GithubAuthConfigError, match=GITHUB_APP_PRIVATE_KEY_ENV):
        GithubAppConfig(app_id="4023580", private_key_pem=pem)


def test_config_rejects_non_pem_private_key() -> None:
    with pytest.raises(GithubAuthConfigError, match="PEM"):
        GithubAppConfig(app_id="4023580", private_key_pem="this is not a pem")


def test_config_from_env_missing_vars_fails_closed() -> None:
    with pytest.raises(GithubAuthConfigError):
        GithubAppConfig.from_env({})


def test_config_repr_redacts_private_key(config: GithubAppConfig) -> None:
    text = repr(config)
    assert "redacted" in text
    assert "BEGIN" not in text
    assert config.private_key_pem not in text


# ---------------------------------------------------------------------------
# 2. App JWT: claims + signature verifiable with the public key
# ---------------------------------------------------------------------------


def test_app_jwt_claims_and_signature_verify_with_public_key(
    config: GithubAppConfig, keypair: _Keypair
) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    source = _make_source(config, opener, clock)

    token = source.mint_app_jwt()

    decoded = jwt.decode(
        token,
        keypair.public_pem,
        algorithms=["RS256"],
        options={"verify_exp": False},
    )
    assert decoded["iss"] == "4023580"
    now_ts = int(clock().timestamp())
    # iat is backdated for clock skew; exp is within GitHub's 10-minute ceiling.
    assert decoded["iat"] <= now_ts
    assert decoded["exp"] > now_ts
    assert decoded["exp"] - decoded["iat"] <= 600
    assert decoded["exp"] - now_ts <= JWT_TTL_SECONDS


def test_app_jwt_rejected_by_wrong_public_key(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_public = (
        other_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    source = _make_source(config, _ScriptedOpener(), clock)
    token = source.mint_app_jwt()
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, other_public, algorithms=["RS256"], options={"verify_exp": False})


def test_jwt_signer_is_injectable(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    captured: dict[str, Any] = {}

    def signer(*, payload: Mapping[str, Any], private_key_pem: str) -> str:
        captured["payload"] = dict(payload)
        captured["pem"] = private_key_pem
        return "fake.jwt.token"

    source = InstallationTokenSource(config, signer=signer, now=clock, opener=_ScriptedOpener())
    assert source.mint_app_jwt() == "fake.jwt.token"
    assert captured["payload"]["iss"] == "4023580"
    assert captured["pem"] == config.private_key_pem


# ---------------------------------------------------------------------------
# 3. Token exchange + cache
# ---------------------------------------------------------------------------


def test_token_exchange_posts_with_app_jwt_bearer(
    config: GithubAppConfig, keypair: _Keypair
) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(status=201, body=_token_response_at(clock, token="ghs_abc")),
    )
    source = _make_source(config, opener, clock)

    token = source.token_for("12345")

    assert isinstance(token, InstallationToken)
    assert token.token == "ghs_abc"
    request = opener.requests[0]
    assert request.get_method() == "POST"
    auth = request.get_header("Authorization")
    assert auth is not None
    assert auth.startswith("Bearer ")
    # The bearer is the App JWT, signed by the App key (verifiable, not the token).
    bearer = auth.removeprefix("Bearer ")
    decoded = jwt.decode(
        bearer, keypair.public_pem, algorithms=["RS256"], options={"verify_exp": False}
    )
    assert decoded["iss"] == "4023580"


def test_token_cache_hit_avoids_second_exchange(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(status=201, body=_token_response_at(clock, expires_in=3600)),
    )
    source = _make_source(config, opener, clock)

    first = source.token_for("12345")
    clock.advance(100)  # still well within the hour
    second = source.token_for("12345")

    assert first.token == second.token
    assert len(opener.requests) == 1  # cache hit, no re-exchange


def test_token_cache_remints_before_expiry(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(status=201, body=_token_response_at(clock, token="first", expires_in=3600)),
    )
    source = _make_source(config, opener, clock)
    first = source.token_for("12345")
    assert first.token == "first"

    # Advance to within the refresh leeway window of expiry → re-mint.
    clock.advance(3600 - 30)
    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(
            status=201, body=_token_response_at(clock, token="second", expires_in=3600)
        ),
    )
    second = source.token_for("12345")
    assert second.token == "second"
    assert len(opener.requests) == 2


def test_token_cache_is_per_installation(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    for installation in ("111", "222"):
        opener.enqueue(
            "POST",
            f"/app/installations/{installation}/access_tokens",
            _CannedResponse(
                status=201, body=_token_response_at(clock, token=f"tok-{installation}")
            ),
        )
    source = _make_source(config, opener, clock)
    assert source.token_for("111").token == "tok-111"
    assert source.token_for("222").token == "tok-222"
    assert len(opener.requests) == 2


def test_token_for_rejects_blank_installation_id(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    source = _make_source(config, _ScriptedOpener(), clock)
    with pytest.raises(GithubApiError, match="installation_id"):
        source.token_for("  ")


def test_token_exchange_missing_token_field_fails(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(status=201, body=json.dumps({"expires_at": "2026-06-10T13:00:00Z"}).encode()),
    )
    source = _make_source(config, opener, clock)
    with pytest.raises(GithubApiError, match="missing a string 'token'"):
        source.token_for("12345")


def test_installation_token_requires_aware_expiry() -> None:
    with pytest.raises(GithubApiError, match="timezone-aware"):
        InstallationToken(token="x", expires_at=datetime(2026, 6, 10, 13, 0, 0))


# ---------------------------------------------------------------------------
# 4. get_file_contents: decode + 404 -> None
# ---------------------------------------------------------------------------


def test_get_file_contents_base64_decodes(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    raw = b"decision: keep retries bounded\n"
    opener.enqueue(
        "GET",
        "/repos/autumngarage/cortex/contents/.cortex/state.md",
        _CannedResponse(
            body=json.dumps(
                {"encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}
            ).encode()
        ),
    )
    result = client.get_file_contents("autumngarage", "cortex", ".cortex/state.md", "main")
    assert result == raw
    # The ref travels as a query param.
    contents_request = opener.requests[-1]
    assert "ref=main" in urllib.parse.urlsplit(contents_request.full_url).query


def test_get_file_contents_404_returns_none(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/autumngarage/cortex/contents/missing.md",
        _CannedResponse(status=404, body=b'{"message":"Not Found"}', http_error=True),
    )
    assert client.get_file_contents("autumngarage", "cortex", "missing.md", "main") is None


def test_get_file_contents_uses_correct_accept_and_auth(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/contents/f.md",
        _CannedResponse(body=json.dumps({"encoding": "base64", "content": ""}).encode()),
    )
    client.get_file_contents("o", "r", "f.md", "abc123")
    request = opener.requests[-1]
    assert request.get_header("Accept") == "application/vnd.github+json"
    assert request.get_header("Authorization") == "token ghs_secret_installation_token"


def test_get_file_contents_directory_response_refused(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/contents/adir",
        _CannedResponse(body=json.dumps([{"name": "a"}, {"name": "b"}]).encode()),
    )
    with pytest.raises(GithubApiError, match="not a base64-encoded file"):
        client.get_file_contents("o", "r", "adir", "main")


# ---------------------------------------------------------------------------
# 4b. list_directory: array -> entries, 404 -> (), file response refused
# ---------------------------------------------------------------------------


def test_list_directory_returns_entries(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/autumngarage/cortex/contents/.cortex/doctrine",
        _CannedResponse(
            body=json.dumps(
                [
                    {"path": ".cortex/doctrine/0001-a.md", "type": "file"},
                    {"path": ".cortex/doctrine/sub", "type": "dir"},
                ]
            ).encode()
        ),
    )
    entries = client.list_directory("autumngarage", "cortex", ".cortex/doctrine", "main")
    assert entries == (
        DirectoryEntry(path=".cortex/doctrine/0001-a.md", type="file"),
        DirectoryEntry(path=".cortex/doctrine/sub", type="dir"),
    )
    assert "ref=main" in urllib.parse.urlsplit(opener.requests[-1].full_url).query


def test_list_directory_404_returns_empty(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/contents/.cortex/doctrine",
        _CannedResponse(status=404, body=b'{"message":"Not Found"}', http_error=True),
    )
    assert client.list_directory("o", "r", ".cortex/doctrine", "main") == ()


def test_list_directory_file_response_refused(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/contents/CLAUDE.md",
        _CannedResponse(body=json.dumps({"encoding": "base64", "content": ""}).encode()),
    )
    with pytest.raises(GithubApiError, match="not a directory listing"):
        client.list_directory("o", "r", "CLAUDE.md", "main")


def test_list_directory_rejects_entry_missing_path(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/contents/d",
        _CannedResponse(body=json.dumps([{"type": "file"}]).encode()),
    )
    with pytest.raises(GithubApiError, match="missing a string 'path'"):
        client.list_directory("o", "r", "d", "main")


# ---------------------------------------------------------------------------
# 5. get_pull_request_diff
# ---------------------------------------------------------------------------


def test_get_pull_request_diff_uses_diff_accept(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    diff_text = "diff --git a/x b/x\n@@ -1 +1 @@\n-old\n+new\n"
    opener.enqueue("GET", "/repos/o/r/pulls/7", _CannedResponse(body=diff_text.encode()))
    result = client.get_pull_request_diff("o", "r", 7)
    assert result == diff_text
    assert opener.requests[-1].get_header("Accept") == "application/vnd.github.v3.diff"


def test_get_pull_request_diff_rejects_bad_number(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    with pytest.raises(GithubApiError, match="positive integer"):
        client.get_pull_request_diff("o", "r", 0)


# ---------------------------------------------------------------------------
# 6. list_pull_request_files: parsing + pagination
# ---------------------------------------------------------------------------


def test_list_pull_request_files_parses_records(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/pulls/7/files",
        _CannedResponse(
            body=json.dumps(
                [
                    {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@"},
                    {"filename": "b.py", "status": "added"},
                ]
            ).encode()
        ),
    )
    files = client.list_pull_request_files("o", "r", 7)
    assert files == (
        ChangedFile(filename="a.py", status="modified", patch="@@ -1 +1 @@"),
        ChangedFile(filename="b.py", status="added", patch=None),
    )


def test_list_pull_request_files_follows_pagination(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    next_url = f"{DEFAULT_API_ROOT}/repos/o/r/pulls/7/files?per_page=100&page=2"
    opener.enqueue(
        "GET",
        "/repos/o/r/pulls/7/files",
        _CannedResponse(
            body=json.dumps([{"filename": "a.py", "status": "modified"}]).encode(),
            headers={"Link": f'<{next_url}>; rel="next"'},
        ),
    )
    opener.enqueue(
        "GET",
        "/repos/o/r/pulls/7/files",
        _CannedResponse(body=json.dumps([{"filename": "b.py", "status": "removed"}]).encode()),
    )
    files = client.list_pull_request_files("o", "r", 7)
    assert [f.filename for f in files] == ["a.py", "b.py"]
    # Two file-list pages fetched (plus the one token exchange).
    file_requests = [r for r in opener.requests if "/files" in r.full_url]
    assert len(file_requests) == 2


def test_list_pull_request_files_non_object_entry_refused(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/pulls/7/files",
        _CannedResponse(body=json.dumps(["not-an-object"]).encode()),
    )
    with pytest.raises(GithubApiError, match="must be a JSON object"):
        client.list_pull_request_files("o", "r", 7)


# ---------------------------------------------------------------------------
# 7. Comment post / update / list
# ---------------------------------------------------------------------------


def test_post_issue_comment_returns_identity(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "POST",
        "/repos/o/r/issues/7/comments",
        _CannedResponse(
            status=201,
            body=json.dumps({"id": 999, "html_url": "https://github.com/o/r/pull/7#c999"}).encode(),
        ),
    )
    result = client.post_issue_comment("o", "r", 7, "advisory body")
    assert result == {"id": 999, "html_url": "https://github.com/o/r/pull/7#c999"}
    request = opener.requests[-1]
    assert request.get_method() == "POST"
    assert _request_json(request) == {"body": "advisory body"}
    assert request.get_header("Content-type") == "application/json"


def test_post_issue_comment_rejects_blank_body(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    with pytest.raises(GithubApiError, match="comment body"):
        client.post_issue_comment("o", "r", 7, "   ")


def test_update_issue_comment_patches_by_id(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "PATCH",
        "/repos/o/r/issues/comments/999",
        _CannedResponse(
            body=json.dumps({"id": 999, "html_url": "https://github.com/o/r/pull/7#c999"}).encode()
        ),
    )
    result = client.update_issue_comment("o", "r", 999, "edited body")
    assert result["id"] == 999
    request = opener.requests[-1]
    assert request.get_method() == "PATCH"
    assert _request_json(request) == {"body": "edited body"}


def test_list_issue_comments_paginates(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    next_url = f"{DEFAULT_API_ROOT}/repos/o/r/issues/7/comments?per_page=100&page=2"
    opener.enqueue(
        "GET",
        "/repos/o/r/issues/7/comments",
        _CannedResponse(
            body=json.dumps([{"id": 1, "body": "<!-- cortex -->"}]).encode(),
            headers={"Link": f'<{next_url}>; rel="next"'},
        ),
    )
    opener.enqueue(
        "GET",
        "/repos/o/r/issues/7/comments",
        _CannedResponse(body=json.dumps([{"id": 2, "body": "human reply"}]).encode()),
    )
    comments = client.list_issue_comments("o", "r", 7)
    assert [c["id"] for c in comments] == [1, 2]


def test_update_issue_comment_rejects_bad_comment_id(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    with pytest.raises(GithubApiError, match="comment_id"):
        client.update_issue_comment("o", "r", -1, "body")


# ---------------------------------------------------------------------------
# 8. Retry on rate-limit + secondary limit + 5xx, and non-retryable refusal
# ---------------------------------------------------------------------------


def test_retry_on_403_rate_limit_then_success(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/pulls/7",
        _CannedResponse(
            status=403,
            body=b'{"message":"rate limited"}',
            headers={"Retry-After": "2"},
            http_error=True,
        ),
    )
    opener.enqueue("GET", "/repos/o/r/pulls/7", _CannedResponse(body=b"the-diff"))
    result = client.get_pull_request_diff("o", "r", 7)
    assert result == "the-diff"
    # Backoff honored the Retry-After (cap is 0 in this policy, but the server
    # value is taken into account before the cap is applied — assert a sleep ran).
    assert opener.sleeps  # at least one backoff sleep occurred


def test_retry_on_429_secondary_limit(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "POST",
        "/repos/o/r/issues/7/comments",
        _CannedResponse(status=429, body=b'{"message":"secondary"}', http_error=True),
    )
    opener.enqueue(
        "POST",
        "/repos/o/r/issues/7/comments",
        _CannedResponse(status=201, body=json.dumps({"id": 5, "html_url": "u"}).encode()),
    )
    result = client.post_issue_comment("o", "r", 7, "body")
    assert result["id"] == 5


def test_retry_on_500_then_success(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/pulls/7",
        _CannedResponse(status=502, body=b"bad gateway", http_error=True),
    )
    opener.enqueue("GET", "/repos/o/r/pulls/7", _CannedResponse(body=b"ok-diff"))
    assert client.get_pull_request_diff("o", "r", 7) == "ok-diff"


def test_retries_are_bounded_then_raise(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    for _ in range(4):  # max_retries=3 -> 4 attempts
        opener.enqueue(
            "GET",
            "/repos/o/r/pulls/7",
            _CannedResponse(status=503, body=b"down", http_error=True),
        )
    with pytest.raises(GithubApiError) as excinfo:
        client.get_pull_request_diff("o", "r", 7)
    assert excinfo.value.status == 503


def test_non_retryable_4xx_refused_immediately(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "POST",
        "/repos/o/r/issues/7/comments",
        _CannedResponse(status=422, body=b'{"message":"Unprocessable"}', http_error=True),
    )
    with pytest.raises(GithubApiError) as excinfo:
        client.post_issue_comment("o", "r", 7, "body")
    assert excinfo.value.status == 422
    # 422 is terminal: exactly one attempt (no retries).
    comment_requests = [r for r in opener.requests if "/comments" in r.full_url]
    assert len(comment_requests) == 1


def test_response_status_5xx_without_httperror_still_retries(config: GithubAppConfig) -> None:
    """An opener that returns a 500 as a normal response (not HTTPError) retries."""

    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue("GET", "/repos/o/r/pulls/7", _CannedResponse(status=500, body=b"boom"))
    opener.enqueue("GET", "/repos/o/r/pulls/7", _CannedResponse(body=b"recovered"))
    assert client.get_pull_request_diff("o", "r", 7) == "recovered"


# ---------------------------------------------------------------------------
# 9. The token never leaks into any error text or log
# ---------------------------------------------------------------------------


SECRET_TOKEN = "ghs_DO_NOT_LEAK_THIS_SECRET"


def test_token_never_appears_in_api_error_text(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(status=201, body=_token_response_at(clock, token=SECRET_TOKEN)),
    )
    client = _make_client(config, opener, clock)
    opener.enqueue(
        "POST",
        "/repos/o/r/issues/7/comments",
        _CannedResponse(status=422, body=b'{"message":"bad"}', http_error=True),
    )
    with pytest.raises(GithubApiError) as excinfo:
        client.post_issue_comment("o", "r", 7, "body")
    rendered = f"{excinfo.value} {excinfo.value.context} {excinfo.value!r}"
    assert SECRET_TOKEN not in rendered


def test_token_never_logged_on_retry(config: GithubAppConfig, caplog: Any) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    opener.enqueue(
        "POST",
        "/app/installations/12345/access_tokens",
        _CannedResponse(status=201, body=_token_response_at(clock, token=SECRET_TOKEN)),
    )
    client = _make_client(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/pulls/7",
        _CannedResponse(status=503, body=b"down", http_error=True),
    )
    opener.enqueue("GET", "/repos/o/r/pulls/7", _CannedResponse(body=b"diff"))
    with caplog.at_level("WARNING"):
        client.get_pull_request_diff("o", "r", 7)
    assert SECRET_TOKEN not in caplog.text
    # A retry was actually logged (the visibility requirement).
    assert "github_api_retry" in caplog.text


def test_installation_token_repr_redacts_secret() -> None:
    token = InstallationToken(
        token=SECRET_TOKEN, expires_at=datetime(2026, 6, 10, 13, 0, 0, tzinfo=UTC)
    )
    assert SECRET_TOKEN not in repr(token)
    assert "redacted" in repr(token)


# ---------------------------------------------------------------------------
# 10. RetryPolicy invariants
# ---------------------------------------------------------------------------


def test_retry_policy_rejects_negative_retries() -> None:
    with pytest.raises(GithubApiError, match="max_retries"):
        RetryPolicy(max_retries=-1)


def test_retry_policy_backoff_honors_retry_after_capped() -> None:
    policy = RetryPolicy(max_retries=3, backoff_base_seconds=1.0, backoff_cap_seconds=10.0)
    # A server-stated 50s Retry-After is taken but capped at 10s.
    assert policy.backoff_for(1, retry_after_seconds=50.0) == 10.0
    # Without a Retry-After, exponential backoff applies, capped.
    assert policy.backoff_for(1, retry_after_seconds=None) == 1.0
    assert policy.backoff_for(5, retry_after_seconds=None) == 10.0


# ---------------------------------------------------------------------------
# 11. URL safety: non-https refused, owner/repo/path encoded
# ---------------------------------------------------------------------------


def test_non_https_api_root_refused(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    with pytest.raises(GithubApiError, match="https"):
        InstallationTokenSource(config, now=clock, api_root="http://insecure.example")


def test_owner_repo_path_segments_are_encoded(config: GithubAppConfig) -> None:
    clock = _FrozenClock(datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC))
    opener = _ScriptedOpener()
    client = _client_with_token(config, opener, clock)
    opener.enqueue(
        "GET",
        "/repos/o/r/contents/dir%20with%20space/file.md",
        _CannedResponse(body=json.dumps({"encoding": "base64", "content": ""}).encode()),
    )
    # A path with spaces is percent-encoded segment-wise; slashes are preserved.
    client.get_file_contents("o", "r", "dir with space/file.md", "main")
    path = urllib.parse.urlsplit(opener.requests[-1].full_url).path
    assert path == "/repos/o/r/contents/dir%20with%20space/file.md"
