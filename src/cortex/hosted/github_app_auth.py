"""GitHub App installation authentication for the Stage 2 reviewer (cortex#386).

The hosted reviewer reads PR diffs and posts advisory comments as a GitHub
*App installation*, never as a user. That requires a two-step credential
dance GitHub mandates:

1. **App JWT.** A short-lived (<=10 min) RS256 JWT signed with the App's
   private key (``iss`` = App id) proves "I am this App". RS256 is not
   optional — GitHub rejects any other algorithm for App auth — so this
   module depends on PyJWT (``pyjwt[crypto]``, in the ``hosted`` extra). That
   dependency is a crypto/auth primitive, not a model/vendor SDK, so the
   cortex#348 vendor-boundary discipline does not apply to it.
2. **Installation token exchange.** ``POST /app/installations/{id}/access_tokens``
   with the App JWT returns a ~1h installation token scoped to one
   installation. The reviewer's REST calls (read file contents, list PR
   files, read the diff, post/update/list issue comments) carry that token.

Design rules, mirroring ``cortex.hosted.api_transport`` and
``cortex.hosted.db``:

- **No vendor SDK** (cortex#348): all outbound HTTP is raw ``urllib.request``;
  the GitHub REST shape never crosses this module's boundary. PyJWT signs the
  App JWT — it is a crypto primitive, not a model SDK.
- **Fail-closed config:** :class:`GithubAppConfig` reads ``GITHUB_APP_ID`` and
  ``GITHUB_APP_PRIVATE_KEY`` from the environment and refuses construction on
  a missing or blank value, naming the variable and carrying the
  ``github_app_credentials_missing`` remediation. A half-configured App never
  mints a token.
- **The token is a secret:** it is never logged, never placed in an exception
  message, and never persisted by this module. The per-installation cache
  lives only in process memory and is an injected/owned structure so tests
  control the clock.
- **Bounded, visible retries:** rate-limit (403/429 with ``Retry-After``) and
  5xx responses are retried with capped backoff, each retry logged; other 4xx
  responses raise a :class:`GithubApiError` carrying the status and a
  sanitized context (never the token). Errors register in the degradation
  taxonomy with a remediation hint.

Consumers: the worker's ``github.pull_request`` / ``github.issue_comment``
handlers (#388/#389) build a :class:`GithubInstallationClient` from the
installation id resolved off the webhook payload.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

logger = logging.getLogger("cortex.hosted.github_app_auth")

GITHUB_APP_ID_ENV = "GITHUB_APP_ID"
GITHUB_APP_PRIVATE_KEY_ENV = "GITHUB_APP_PRIVATE_KEY"

DEFAULT_API_ROOT = "https://api.github.com"
DEFAULT_USER_AGENT = "cortex-hosted-reviewer"

# GitHub caps App JWT lifetime at 10 minutes and rejects clocks running fast,
# so we mint a 9-minute token and backdate iat by 60s for clock skew.
JWT_TTL_SECONDS = 9 * 60
JWT_IAT_BACKDATE_SECONDS = 60
_JWT_ALGORITHM = "RS256"

# Re-mint an installation token this many seconds before its stated expiry so
# a call never races the boundary with a token about to be rejected.
TOKEN_REFRESH_LEEWAY_SECONDS = 60

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_CAP_SECONDS = 30.0
DEFAULT_TIMEOUT_SECONDS = 30.0
# GitHub's per-page maximum; pagination follows Link headers regardless.
_DEFAULT_PER_PAGE = 100
_MAX_PAGES = 1000

_RATE_LIMIT_STATUS = 403
_SECONDARY_LIMIT_STATUS = 429
_RETRYABLE_STATUS_FLOOR = 500
_NOT_FOUND_STATUS = 404

_ERROR_BODY_EXCERPT_CHARS = 240

GITHUB_APP_CREDENTIALS_REMEDIATION = (
    f"set {GITHUB_APP_ID_ENV} to the GitHub App's numeric App ID and "
    f"{GITHUB_APP_PRIVATE_KEY_ENV} to the App private key PEM in the service "
    "environment (for the hosted deployment, Railway service variables) — see "
    "docs/setup/github-app.md"
)
"""The one actionable next step for ``github_app_credentials_missing``.

``cortex.hosted.degradation`` registers this string in
``REMEDIATION_BY_REASON`` (the import runs that way around so this module
never imports the taxonomy, mirroring ``api_transport.API_KEY_REMEDIATION``)."""

GITHUB_API_REMEDIATION = (
    "inspect the named GitHub API status and sanitized context; a 401/403 "
    "means the installation token was rejected or the App lacks the requested "
    "permission (verify the App's Contents:read / PullRequests:write scopes "
    "and that the installation is not revoked) — see docs/setup/github-app.md"
)
"""The one actionable next step for ``github_api_request_failed`` refusals."""


class GithubAppAuthError(ValueError):
    """Base class for every GitHub App auth/transport failure.

    Concrete subclasses are what the degradation taxonomy classifies; this
    base exists so callers can catch the whole family. It is never registered
    directly (the taxonomy dispatches by exact type).
    """


class GithubAuthConfigError(GithubAppAuthError):
    """Raised when the GitHub App credential configuration is incomplete."""


class GithubApiError(GithubAppAuthError):
    """Raised when a GitHub REST call fails after bounded retries.

    Carries the HTTP ``status`` (``None`` for a pre-response transport error)
    and a sanitized ``context`` string. The installation token never appears
    in the message — only the status, endpoint, and a bounded body excerpt.
    """

    def __init__(self, message: str, *, status: int | None = None, context: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.context = context


@dataclass(frozen=True)
class GithubAppConfig:
    """Validated GitHub App credentials, sourced fail-closed from the env.

    ``private_key_pem`` is the App's RSA private key PEM. It is held only to
    sign App JWTs and is never logged or echoed in an error; ``__repr__`` is
    overridden so an accidental log of the config cannot leak it.
    """

    app_id: str
    private_key_pem: str

    def __post_init__(self) -> None:
        app_id = self.app_id.strip()
        if not app_id:
            raise GithubAuthConfigError(
                f"{GITHUB_APP_ID_ENV} is unset or blank; {GITHUB_APP_CREDENTIALS_REMEDIATION}"
            )
        if not app_id.isdigit():
            raise GithubAuthConfigError(
                f"{GITHUB_APP_ID_ENV} must be the App's numeric id; got a "
                f"non-numeric value of length {len(app_id)}"
            )
        object.__setattr__(self, "app_id", app_id)
        # Do not strip the PEM body (newlines are significant); only reject a
        # blank or structurally non-PEM value before any signing attempt.
        if not self.private_key_pem.strip():
            raise GithubAuthConfigError(
                f"{GITHUB_APP_PRIVATE_KEY_ENV} is unset or blank; "
                f"{GITHUB_APP_CREDENTIALS_REMEDIATION}"
            )
        if "-----BEGIN" not in self.private_key_pem:
            raise GithubAuthConfigError(
                f"{GITHUB_APP_PRIVATE_KEY_ENV} does not look like a PEM private key "
                "(missing a '-----BEGIN' header); paste the App's .pem contents verbatim"
            )

    def __repr__(self) -> str:
        return f"GithubAppConfig(app_id={self.app_id!r}, private_key_pem=<redacted>)"

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> GithubAppConfig:
        """Build the config from a process environment mapping, fail-closed."""

        return cls(
            app_id=environ.get(GITHUB_APP_ID_ENV, ""),
            private_key_pem=environ.get(GITHUB_APP_PRIVATE_KEY_ENV, ""),
        )


@dataclass(frozen=True)
class InstallationToken:
    """A short-lived installation access token and its stated expiry.

    The token value is a secret: ``__repr__`` redacts it so a log of the
    token never leaks the credential.
    """

    token: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise GithubApiError("installation token exchange returned an empty token")
        if self.expires_at.tzinfo is None:
            raise GithubApiError(
                "installation token expiry must be timezone-aware; GitHub returns UTC"
            )

    def __repr__(self) -> str:
        return f"InstallationToken(token=<redacted>, expires_at={self.expires_at.isoformat()})"

    def is_fresh(self, *, now: datetime, leeway_seconds: int = TOKEN_REFRESH_LEEWAY_SECONDS) -> bool:
        """True when the token is still usable ``leeway_seconds`` before expiry."""

        return now + timedelta(seconds=leeway_seconds) < self.expires_at


@dataclass(frozen=True)
class ChangedFile:
    """One entry from the PR "list files" API: the narrow slice the reviewer reads."""

    filename: str
    status: str
    patch: str | None = None


class HttpResponse(Protocol):
    """The slice of an HTTP response this module needs."""

    status: int

    def read(self) -> bytes: ...

    def getheader(self, name: str, default: str | None = None) -> str | None: ...

    def close(self) -> None: ...


class UrlOpener(Protocol):
    """Injectable transport seam matching ``urllib.request.urlopen``'s shape.

    Tests inject a scripted opener; production uses :func:`_default_opener`, a
    thin wrapper over ``urllib.request.urlopen``. ``urlopen`` raises
    ``urllib.error.HTTPError`` for >=400 statuses — that subclass *is* an
    ``HttpResponse`` (it has ``read``/``status``), so the opener never hides a
    status code behind an exception type.
    """

    def __call__(self, request: urllib.request.Request, *, timeout: float) -> HttpResponse: ...


class JwtSigner(Protocol):
    """Injectable App-JWT signer.

    Production binds :func:`_pyjwt_signer`, which loads PyJWT lazily; tests
    inject a recorded signer so the suite never needs a real RSA key.
    """

    def __call__(self, *, payload: Mapping[str, Any], private_key_pem: str) -> str: ...


def _default_opener(request: urllib.request.Request, *, timeout: float) -> HttpResponse:
    # The request URL is always built from DEFAULT_API_ROOT (https) below.
    return cast("HttpResponse", urllib.request.urlopen(request, timeout=timeout))


def _pyjwt_signer(*, payload: Mapping[str, Any], private_key_pem: str) -> str:
    """Sign an App JWT with RS256 using PyJWT, loaded lazily.

    PyJWT lives in the optional ``hosted`` extra; a missing install surfaces
    as a :class:`GithubAuthConfigError` naming the install hint, never a bare
    ``ImportError`` as the contract (the same lazy-import discipline as
    ``cortex.hosted.db``).
    """

    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - exercised via injected signer in tests
        raise GithubAuthConfigError(
            "the GitHub App JWT signer requires PyJWT, which is not installed; "
            "install the hosted extra with `pip install 'cortex[hosted]'` "
            "(or `uv sync --extra hosted`)"
        ) from exc
    token: str = jwt.encode(dict(payload), private_key_pem, algorithm=_JWT_ALGORITHM)
    return token


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded, visible retry/backoff knobs for GitHub REST calls."""

    max_retries: int = DEFAULT_MAX_RETRIES
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    backoff_cap_seconds: float = DEFAULT_BACKOFF_CAP_SECONDS

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise GithubApiError(f"max_retries must be >= 0, got {self.max_retries}")
        if self.backoff_base_seconds < 0:
            raise GithubApiError(
                f"backoff_base_seconds must be >= 0, got {self.backoff_base_seconds}"
            )
        if self.backoff_cap_seconds < 0:
            raise GithubApiError(
                f"backoff_cap_seconds must be >= 0, got {self.backoff_cap_seconds}"
            )

    def backoff_for(self, attempt: int, *, retry_after_seconds: float | None) -> float:
        """Backoff before retry ``attempt`` (1-based), honoring ``Retry-After``."""

        exponential = self.backoff_base_seconds * float(2 ** (attempt - 1))
        computed = min(self.backoff_cap_seconds, exponential)
        if retry_after_seconds is not None:
            # A server-stated Retry-After wins, still capped so a hostile or
            # mistaken header cannot pin a worker open indefinitely.
            return min(self.backoff_cap_seconds, max(computed, retry_after_seconds))
        return computed


class InstallationTokenSource:
    """Mints and caches installation tokens, one per installation id.

    The cache is process-local and in-memory by design: tokens are secrets
    that must not be persisted. ``now`` is injected so tests drive expiry
    deterministically without sleeping. A token is re-minted once it is within
    :data:`TOKEN_REFRESH_LEEWAY_SECONDS` of its stated expiry.
    """

    def __init__(
        self,
        config: GithubAppConfig,
        *,
        opener: UrlOpener = _default_opener,
        signer: JwtSigner = _pyjwt_signer,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        api_root: str = DEFAULT_API_ROOT,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._config = config
        self._opener = opener
        self._signer = signer
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._api_root = _validate_api_root(api_root)
        self._retry_policy = retry_policy or RetryPolicy()
        self._timeout_seconds = timeout_seconds
        self._cache: dict[str, InstallationToken] = {}

    def mint_app_jwt(self) -> str:
        """Mint a fresh short-lived RS256 App JWT (<=10 min lifetime)."""

        issued = self._now()
        iat = int(issued.timestamp()) - JWT_IAT_BACKDATE_SECONDS
        exp = int(issued.timestamp()) + JWT_TTL_SECONDS
        payload = {"iat": iat, "exp": exp, "iss": self._config.app_id}
        return self._signer(payload=payload, private_key_pem=self._config.private_key_pem)

    def token_for(self, installation_id: str, *, force_refresh: bool = False) -> InstallationToken:
        """Return a fresh installation token, re-minting before expiry."""

        installation_id = _require_installation_id(installation_id)
        if not force_refresh:
            cached = self._cache.get(installation_id)
            if cached is not None and cached.is_fresh(now=self._now()):
                return cached
        token = self._exchange(installation_id)
        self._cache[installation_id] = token
        return token

    def _exchange(self, installation_id: str) -> InstallationToken:
        app_jwt = self.mint_app_jwt()
        url = f"{self._api_root}/app/installations/{urllib.parse.quote(installation_id)}/access_tokens"
        request = _build_request(
            url,
            method="POST",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
            },
            data=b"",
        )
        body, _headers, _status = _send(
            request,
            opener=self._opener,
            sleep=self._sleep,
            retry_policy=self._retry_policy,
            timeout_seconds=self._timeout_seconds,
            context=f"installation token exchange for installation {installation_id}",
        )
        payload = _json_mapping(body, context="installation token exchange response")
        token = payload.get("token")
        if not isinstance(token, str) or not token.strip():
            raise GithubApiError(
                "installation token exchange response is missing a string 'token'",
                context=f"installation {installation_id}",
            )
        expires_at_raw = payload.get("expires_at")
        if not isinstance(expires_at_raw, str) or not expires_at_raw.strip():
            raise GithubApiError(
                "installation token exchange response is missing a string 'expires_at'",
                context=f"installation {installation_id}",
            )
        return InstallationToken(token=token, expires_at=_parse_github_timestamp(expires_at_raw))


class GithubInstallationClient:
    """The narrow authenticated GitHub REST surface the reviewer uses.

    Every call carries a fresh installation token from the
    :class:`InstallationTokenSource` (re-minted before expiry) and the correct
    ``Accept`` header. The token is never logged. All HTTP is dependency
    injected so tests run fully offline.
    """

    def __init__(
        self,
        token_source: InstallationTokenSource,
        installation_id: str,
        *,
        opener: UrlOpener = _default_opener,
        sleep: Callable[[float], None] = time.sleep,
        api_root: str = DEFAULT_API_ROOT,
        retry_policy: RetryPolicy | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._token_source = token_source
        self._installation_id = _require_installation_id(installation_id)
        self._opener = opener
        self._sleep = sleep
        self._api_root = _validate_api_root(api_root)
        self._retry_policy = retry_policy or RetryPolicy()
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent

    # --- file + diff reads (Contents:read) ---------------------------------

    def get_file_contents(self, owner: str, repo: str, path: str, ref: str) -> bytes | None:
        """Return a file's decoded bytes, or ``None`` when it does not exist (404).

        A 404 is the documented "file absent at this ref" answer, not a
        failure — the reviewer treats a missing decision source as no source,
        not an error.
        """

        query = urllib.parse.urlencode({"ref": ref})
        url = (
            f"{self._api_root}/repos/{_segment(owner)}/{_segment(repo)}"
            f"/contents/{_path_segment(path)}?{query}"
        )
        try:
            body = self._get(url, accept="application/vnd.github+json")
        except GithubApiError as exc:
            if exc.status == _NOT_FOUND_STATUS:
                return None
            raise
        parsed = _json_loads(body, context=f"contents of {path}@{ref}")
        if not isinstance(parsed, Mapping):
            # The contents API returns a JSON array for a directory; that is
            # not a file the reviewer can read as bytes — refuse with the
            # directory-specific message rather than a generic shape error.
            raise GithubApiError(
                f"contents response for {path}@{ref} is not a base64-encoded file "
                "(the path resolved to a directory listing)",
                context=f"{path}@{ref}",
            )
        return _decode_contents_payload(parsed, path=path, ref=ref)

    def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Return the PR's unified diff (``Accept: application/vnd.github.v3.diff``)."""

        _require_pr_number(pr_number)
        url = (
            f"{self._api_root}/repos/{_segment(owner)}/{_segment(repo)}/pulls/{pr_number}"
        )
        body = self._get(url, accept="application/vnd.github.v3.diff")
        return body.decode("utf-8", errors="replace")

    def list_pull_request_files(
        self, owner: str, repo: str, pr_number: int
    ) -> tuple[ChangedFile, ...]:
        """Return the PR's changed-file records, following pagination."""

        _require_pr_number(pr_number)
        base = (
            f"{self._api_root}/repos/{_segment(owner)}/{_segment(repo)}"
            f"/pulls/{pr_number}/files"
        )
        files: list[ChangedFile] = []
        for page_body in self._paginate(base):
            entries = _json_array(page_body, context=f"PR #{pr_number} files page")
            files.extend(_changed_file_from_entry(entry) for entry in entries)
        return tuple(files)

    # --- comment reads + writes (PullRequests:write) -----------------------

    def post_issue_comment(self, owner: str, repo: str, issue_number: int, body: str) -> Mapping[str, Any]:
        """Create an issue/PR comment; return the created comment's id and html_url."""

        _require_pr_number(issue_number)
        _require_comment_body(body)
        url = (
            f"{self._api_root}/repos/{_segment(owner)}/{_segment(repo)}"
            f"/issues/{issue_number}/comments"
        )
        response = self._send_json(url, method="POST", payload={"body": body})
        return _comment_identity(response, context=f"posting a comment on #{issue_number}")

    def update_issue_comment(
        self, owner: str, repo: str, comment_id: int, body: str
    ) -> Mapping[str, Any]:
        """Edit an existing comment by id (idempotent update-not-duplicate)."""

        _require_comment_id(comment_id)
        _require_comment_body(body)
        url = (
            f"{self._api_root}/repos/{_segment(owner)}/{_segment(repo)}"
            f"/issues/comments/{comment_id}"
        )
        response = self._send_json(url, method="PATCH", payload={"body": body})
        return _comment_identity(response, context=f"updating comment {comment_id}")

    def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[Mapping[str, Any], ...]:
        """Return every comment on an issue/PR, following pagination.

        The renderer scans these for its update marker so it edits its prior
        comment instead of posting a duplicate.
        """

        _require_pr_number(issue_number)
        base = (
            f"{self._api_root}/repos/{_segment(owner)}/{_segment(repo)}"
            f"/issues/{issue_number}/comments"
        )
        comments: list[Mapping[str, Any]] = []
        for page_body in self._paginate(base):
            entries = _json_array(page_body, context=f"#{issue_number} comments page")
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise GithubApiError(
                        f"comment list for #{issue_number} contained a non-object entry"
                    )
                comments.append(dict(entry))
        return tuple(comments)

    # --- transport internals -----------------------------------------------

    def _auth_header(self) -> str:
        token = self._token_source.token_for(self._installation_id)
        return f"token {token.token}"

    def _headers(self, *, accept: str, content_type: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": self._auth_header(),
            "Accept": accept,
            "User-Agent": self._user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        return headers

    def _get(self, url: str, *, accept: str) -> bytes:
        request = _build_request(url, method="GET", headers=self._headers(accept=accept))
        body, _headers, _status = _send(
            request,
            opener=self._opener,
            sleep=self._sleep,
            retry_policy=self._retry_policy,
            timeout_seconds=self._timeout_seconds,
            context=f"GET {_sanitize_url(url)}",
        )
        return body

    def _send_json(self, url: str, *, method: str, payload: Mapping[str, Any]) -> bytes:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = _build_request(
            url,
            method=method,
            headers=self._headers(
                accept="application/vnd.github+json", content_type="application/json"
            ),
            data=data,
        )
        body, _headers, _status = _send(
            request,
            opener=self._opener,
            sleep=self._sleep,
            retry_policy=self._retry_policy,
            timeout_seconds=self._timeout_seconds,
            context=f"{method} {_sanitize_url(url)}",
        )
        return body

    def _paginate(self, base_url: str) -> list[bytes]:
        separator = "&" if "?" in base_url else "?"
        url: str | None = f"{base_url}{separator}per_page={_DEFAULT_PER_PAGE}"
        pages: list[bytes] = []
        seen = 0
        while url is not None:
            seen += 1
            if seen > _MAX_PAGES:
                raise GithubApiError(
                    f"pagination exceeded {_MAX_PAGES} pages for {_sanitize_url(base_url)}; "
                    "refusing to follow an unbounded Link chain"
                )
            request = _build_request(
                url, method="GET", headers=self._headers(accept="application/vnd.github+json")
            )
            body, headers, _status = _send(
                request,
                opener=self._opener,
                sleep=self._sleep,
                retry_policy=self._retry_policy,
                timeout_seconds=self._timeout_seconds,
                context=f"GET {_sanitize_url(url)}",
            )
            pages.append(body)
            url = _next_link(headers.get("Link"))
        return pages


# ---------------------------------------------------------------------------
# Shared HTTP send + bounded retry
# ---------------------------------------------------------------------------


def _send(
    request: urllib.request.Request,
    *,
    opener: UrlOpener,
    sleep: Callable[[float], None],
    retry_policy: RetryPolicy,
    timeout_seconds: float,
    context: str,
) -> tuple[bytes, Mapping[str, str | None], int]:
    """Send one request with bounded, visible retries on rate-limit/5xx.

    Returns ``(body, headers, status)``. A 404 raises a ``GithubApiError`` with
    ``status=404`` so the caller can choose to treat it as a soft miss; this
    keeps the not-found policy at the call site, not buried in the transport.
    """

    attempts = retry_policy.max_retries + 1
    last_failure = "no attempt ran"
    for attempt in range(1, attempts + 1):
        try:
            response = opener(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as exc:
            status = exc.code
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None)
            detail = _excerpt(_read_error_body(exc))
            if _is_retryable(status):
                last_failure = f"HTTP {status}: {detail}"
                if attempt < attempts:
                    delay = retry_policy.backoff_for(attempt, retry_after_seconds=retry_after)
                    logger.warning(
                        json.dumps(
                            {
                                "event": "github_api_retry",
                                "context": context,
                                "attempt": attempt,
                                "attempts": attempts,
                                "status": status,
                                "delay_seconds": round(delay, 3),
                            },
                            sort_keys=True,
                        )
                    )
                    sleep(delay)
                    continue
                raise GithubApiError(
                    f"{context} failed after {attempts} attempt(s); last failure: "
                    f"{last_failure}; retries are bounded and this failure is visible",
                    status=status,
                    context=detail,
                ) from exc
            raise GithubApiError(
                f"{context} was refused with HTTP {status} (not retryable)",
                status=status,
                context=detail,
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            last_failure = f"transport error: {exc}"
            if attempt < attempts:
                delay = retry_policy.backoff_for(attempt, retry_after_seconds=None)
                logger.warning(
                    json.dumps(
                        {
                            "event": "github_api_retry",
                            "context": context,
                            "attempt": attempt,
                            "attempts": attempts,
                            "status": None,
                            "delay_seconds": round(delay, 3),
                        },
                        sort_keys=True,
                    )
                )
                sleep(delay)
                continue
            raise GithubApiError(
                f"{context} failed before a response after {attempts} attempt(s): {exc}",
                status=None,
                context=str(exc),
            ) from exc
        status = response.status
        try:
            body = response.read()
            headers = {"Link": response.getheader("Link")}
        finally:
            response.close()
        if _is_retryable(status):
            # Some openers surface 5xx/secondary-limit as a normal response
            # rather than an HTTPError; treat it identically.
            retry_after = _retry_after_seconds(response.getheader("Retry-After"))
            last_failure = f"HTTP {status}: {_excerpt(body.decode('utf-8', errors='replace'))}"
            if attempt < attempts:
                delay = retry_policy.backoff_for(attempt, retry_after_seconds=retry_after)
                logger.warning(
                    json.dumps(
                        {
                            "event": "github_api_retry",
                            "context": context,
                            "attempt": attempt,
                            "attempts": attempts,
                            "status": status,
                            "delay_seconds": round(delay, 3),
                        },
                        sort_keys=True,
                    )
                )
                sleep(delay)
                continue
            raise GithubApiError(
                f"{context} failed after {attempts} attempt(s); last failure: {last_failure}",
                status=status,
                context=_excerpt(body.decode("utf-8", errors="replace")),
            )
        if status >= 400:
            raise GithubApiError(
                f"{context} was refused with HTTP {status}",
                status=status,
                context=_excerpt(body.decode("utf-8", errors="replace")),
            )
        return body, headers, status
    raise GithubApiError(
        f"{context} exhausted retries with no terminal outcome; last failure: {last_failure}"
    )


def _is_retryable(status: int) -> bool:
    return (
        status in (_RATE_LIMIT_STATUS, _SECONDARY_LIMIT_STATUS)
        or status >= _RETRYABLE_STATUS_FLOOR
    )


# ---------------------------------------------------------------------------
# Parsing + sanitizing helpers (the token never enters any of these)
# ---------------------------------------------------------------------------


def _build_request(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    data: bytes | None = None,
) -> urllib.request.Request:
    if not url.startswith("https://"):
        raise GithubApiError(f"refusing to call a non-https GitHub URL: {_sanitize_url(url)}")
    return urllib.request.Request(url, data=data, headers=dict(headers), method=method)


def _decode_contents_payload(
    payload: Mapping[str, Any], *, path: str, ref: str
) -> bytes:
    encoding = payload.get("encoding")
    content = payload.get("content")
    if encoding != "base64" or not isinstance(content, str):
        # The contents API returns base64 for files; a directory or symlink
        # response (a list, or encoding="none") is not a file the reviewer can
        # read as bytes — refuse rather than guess.
        raise GithubApiError(
            f"contents response for {path}@{ref} is not a base64-encoded file "
            f"(encoding={encoding!r}); the path may be a directory or too large",
            context=f"{path}@{ref}",
        )
    try:
        return base64.b64decode(content, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise GithubApiError(
            f"contents response for {path}@{ref} carried undecodable base64",
            context=f"{path}@{ref}",
        ) from exc


def _changed_file_from_entry(entry: Any) -> ChangedFile:
    if not isinstance(entry, Mapping):
        raise GithubApiError("PR files entry must be a JSON object")
    filename = entry.get("filename")
    status = entry.get("status")
    if not isinstance(filename, str) or not filename:
        raise GithubApiError("PR files entry is missing a string 'filename'")
    if not isinstance(status, str) or not status:
        raise GithubApiError(f"PR files entry for {filename!r} is missing a string 'status'")
    patch = entry.get("patch")
    if patch is not None and not isinstance(patch, str):
        raise GithubApiError(f"PR files entry for {filename!r} has a non-string 'patch'")
    return ChangedFile(filename=filename, status=status, patch=patch)


def _comment_identity(body: bytes, *, context: str) -> Mapping[str, Any]:
    payload = _json_mapping(body, context=context)
    comment_id = payload.get("id")
    html_url = payload.get("html_url")
    if not isinstance(comment_id, int) or isinstance(comment_id, bool):
        raise GithubApiError(f"comment response while {context} is missing an integer 'id'")
    if not isinstance(html_url, str) or not html_url:
        raise GithubApiError(f"comment response while {context} is missing a string 'html_url'")
    return {"id": comment_id, "html_url": html_url}


def _next_link(link_header: str | None) -> str | None:
    """Parse the ``rel="next"`` URL out of a GitHub ``Link`` header, if present."""

    if not link_header:
        return None
    for part in link_header.split(","):
        segments = part.split(";")
        if len(segments) < 2:
            continue
        url_segment = segments[0].strip()
        if not (url_segment.startswith("<") and url_segment.endswith(">")):
            continue
        url = url_segment[1:-1]
        for param in segments[1:]:
            param = param.strip()
            if param in {'rel="next"', "rel=next"}:
                return url
    return None


def _retry_after_seconds(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        # GitHub may send an HTTP-date; we do not parse it, falling back to
        # computed backoff. The header is advisory, never the only bound.
        return None
    return max(0.0, seconds)


def _json_mapping(body: bytes, *, context: str) -> Mapping[str, Any]:
    parsed = _json_loads(body, context=context)
    if not isinstance(parsed, Mapping):
        raise GithubApiError(f"{context} was not a JSON object")
    return parsed


def _json_array(body: bytes, *, context: str) -> list[Any]:
    parsed = _json_loads(body, context=context)
    if not isinstance(parsed, list):
        raise GithubApiError(f"{context} was not a JSON array")
    return parsed


def _json_loads(body: bytes, *, context: str) -> Any:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GithubApiError(f"{context} body was not valid UTF-8") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GithubApiError(f"{context} body was not valid JSON") from exc


def _excerpt(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= _ERROR_BODY_EXCERPT_CHARS:
        return cleaned
    return cleaned[:_ERROR_BODY_EXCERPT_CHARS] + "…"


def _read_error_body(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException, ValueError):
        return "<unreadable error body>"


def _sanitize_url(url: str) -> str:
    """Strip query strings from a URL for error/log text.

    Installation token exchange and comment URLs never carry the token in the
    query, but stripping it is defense in depth so a future signed-URL pattern
    cannot leak a credential into a log line.
    """

    split = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((split.scheme, split.netloc, split.path, "", ""))


def _validate_api_root(api_root: str) -> str:
    root = api_root.rstrip("/")
    if not root.startswith("https://"):
        raise GithubApiError(f"GitHub API root must be https://, got {api_root!r}")
    return root


def _segment(value: str) -> str:
    if not value or not value.strip():
        raise GithubApiError("owner and repo path segments must be non-empty")
    return urllib.parse.quote(value.strip(), safe="")


def _path_segment(path: str) -> str:
    if not path or not path.strip():
        raise GithubApiError("file path must be non-empty")
    # Preserve the path's slashes (contents API is /contents/a/b/c) but encode
    # each segment so spaces and unicode are safe.
    return "/".join(urllib.parse.quote(part, safe="") for part in path.strip("/").split("/"))


def _require_installation_id(installation_id: str) -> str:
    cleaned = (installation_id or "").strip()
    if not cleaned:
        raise GithubApiError("installation_id must be a non-empty string")
    return cleaned


def _require_pr_number(pr_number: int) -> None:
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
        raise GithubApiError(f"pull/issue number must be a positive integer, got {pr_number!r}")


def _require_comment_id(comment_id: int) -> None:
    if not isinstance(comment_id, int) or isinstance(comment_id, bool) or comment_id <= 0:
        raise GithubApiError(f"comment_id must be a positive integer, got {comment_id!r}")


def _require_comment_body(body: str) -> None:
    if not isinstance(body, str) or not body.strip():
        raise GithubApiError("comment body must be a non-empty string")


def _parse_github_timestamp(raw: str) -> datetime:
    """Parse a GitHub ISO-8601 UTC timestamp (e.g. ``2026-06-10T12:00:00Z``)."""

    candidate = raw.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise GithubApiError(
            f"installation token 'expires_at' is not an ISO-8601 timestamp: {raw!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
