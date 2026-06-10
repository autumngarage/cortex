"""Postgres connection policy for hosted Cortex (cortex#472).

The hosted substrate shipped as SQL strings (PRs #477-#483); this module is
part of the first executable path. It owns exactly one concern: turning a
Railway-style ``DATABASE_URL`` into a policy-conformant psycopg connection
that fails visibly.

Policy invariants:

- explicit connect timeout — an unreachable host fails fast instead of
  hanging a worker indefinitely;
- ``application_name`` is always ``cortex-hosted`` so hosted connections are
  attributable in ``pg_stat_activity`` and Railway logs;
- ``statement_timeout`` is set for the session via the libpq ``options``
  parameter so a runaway migration or query cannot hold the database forever;
- the caller's URL is passed to the driver verbatim — ``?sslmode=require``
  on Railway-style URLs is honored, never stripped or rewritten, because the
  policy never reconstructs the URL and never passes an ``sslmode`` override.

The psycopg driver is an optional extra (``pip install 'cortex[hosted]'``)
imported lazily inside :func:`connect`; the core CLI install stays
driver-free per the standalone-boundary rule, and every failure surfaces as
a :class:`HostedDbError` naming exactly what failed (no driver, bad URL,
unreachable, auth) — never a bare driver traceback as the contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import SplitResult, parse_qs, urlsplit, urlunsplit

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_STATEMENT_TIMEOUT_MS = 30_000
HOSTED_APPLICATION_NAME = "cortex-hosted"

_ALLOWED_URL_SCHEMES = frozenset({"postgres", "postgresql"})
# libpq-defined sslmode values; a typo'd mode must fail here, not surface as
# an opaque driver error after a network round-trip.
_VALID_SSLMODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})
_DRIVER_INSTALL_HINT = (
    "the hosted Postgres driver is not installed; install it with "
    "`pip install 'cortex[hosted]'` (or `uv sync --extra hosted`)"
)

_AUTH_FAILURE_MARKERS = (
    "password authentication failed",
    "no password supplied",
    "authentication failed",
    "permission denied",
    "pg_hba.conf",
)
_UNREACHABLE_MARKERS = (
    "could not connect",
    "connection refused",
    "timeout expired",
    "timed out",
    "could not translate host name",
    "no route to host",
    "network is unreachable",
    "server closed the connection",
)


class HostedDbError(ValueError):
    """Raised when the hosted Postgres connection policy cannot be satisfied."""


class HostedConnection(Protocol):
    """Narrow structural slice of a psycopg connection used by hosted code.

    Keeping the interface to ``execute``/``commit``/``rollback``/``close``
    lets the migration runner and tests share one code path: the real driver
    connection and a scripted fake both satisfy this protocol.
    """

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ConnectionSettings:
    """Validated, policy-conformant connection material.

    ``conninfo`` is the caller's ``DATABASE_URL`` held verbatim; the policy
    never rewrites it, so URL-declared TLS material such as Railway's
    ``?sslmode=require`` cannot be lost on the way to the driver.
    """

    conninfo: str
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS
    application_name: str = HOSTED_APPLICATION_NAME

    def __post_init__(self) -> None:
        if not self.conninfo.strip():
            raise HostedDbError("invalid hosted database URL: URL must not be empty")
        parts = _split_and_validate_port(self.conninfo)
        if parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
            allowed = ", ".join(sorted(_ALLOWED_URL_SCHEMES))
            raise HostedDbError(
                f"invalid hosted database URL: scheme {parts.scheme!r} is not supported "
                f"(expected one of: {allowed})"
            )
        if not parts.hostname:
            raise HostedDbError("invalid hosted database URL: URL must name a database host")
        if self.connect_timeout_seconds <= 0:
            raise HostedDbError(
                f"connect_timeout_seconds must be positive, got {self.connect_timeout_seconds}"
            )
        if self.statement_timeout_ms <= 0:
            raise HostedDbError(
                f"statement_timeout_ms must be positive, got {self.statement_timeout_ms}"
            )
        if not self.application_name.strip():
            raise HostedDbError("application_name must not be empty")
        for value in parse_qs(parts.query, keep_blank_values=True).get("sslmode", []):
            if not value:
                raise HostedDbError(
                    "invalid hosted database URL: sslmode is declared but empty; "
                    "declare an explicit mode (Railway-style URLs use sslmode=require)"
                )
            if value not in _VALID_SSLMODES:
                valid = ", ".join(sorted(_VALID_SSLMODES))
                raise HostedDbError(
                    f"invalid hosted database URL: unknown sslmode {value!r} "
                    f"(valid modes: {valid})"
                )

    @property
    def sslmode(self) -> str | None:
        """The URL-declared sslmode (last occurrence wins, as in libpq), if any."""

        values = parse_qs(urlsplit(self.conninfo).query, keep_blank_values=True).get("sslmode")
        return values[-1] if values else None

    def connect_kwargs(self) -> dict[str, Any]:
        """Driver keyword arguments carrying the policy.

        Deliberately contains no ``sslmode`` key: TLS is owned by the URL and
        must never be overridden or stripped by the policy layer.
        """

        return {
            "connect_timeout": self.connect_timeout_seconds,
            "application_name": self.application_name,
            "options": f"-c statement_timeout={self.statement_timeout_ms}",
        }


def redacted_url(database_url: str) -> str:
    """Return the URL with any password replaced by ``***`` for error text."""

    try:
        parts = urlsplit(database_url)
        port = parts.port
    except ValueError:
        # Unparseable URLs may hide credentials in unexpected positions;
        # refuse to echo anything rather than risk leaking a secret.
        return "<unparseable database URL>"
    if parts.password is None:
        return database_url
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    username = parts.username or ""
    netloc = f"{username}:***@{host}"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def classify_connection_failure(message: str) -> str:
    """Classify a driver connection failure into a visible reason.

    Returns ``"auth"``, ``"unreachable"``, or ``"rejected"`` (server refused
    the connection for a reason that is neither credentials nor network —
    for example a nonexistent database). The original driver message is
    always carried alongside the classification, so an imperfect match
    narrows debugging without hiding anything.
    """

    lowered = message.lower()
    if any(marker in lowered for marker in _AUTH_FAILURE_MARKERS):
        return "auth"
    if any(marker in lowered for marker in _UNREACHABLE_MARKERS):
        return "unreachable"
    return "rejected"


def connect(
    database_url: str,
    *,
    connect_timeout_seconds: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
) -> HostedConnection:
    """Open a policy-conformant connection to the hosted Postgres.

    Raises :class:`HostedDbError` naming exactly what failed: missing driver
    (with the ``cortex[hosted]`` install hint), invalid URL, unreachable
    host, or authentication failure. Never lets a bare driver exception be
    the contract.
    """

    settings = ConnectionSettings(
        conninfo=database_url,
        connect_timeout_seconds=connect_timeout_seconds,
        statement_timeout_ms=statement_timeout_ms,
    )
    driver = _import_driver()
    try:
        connection = driver.connect(settings.conninfo, **settings.connect_kwargs())
    except driver.Error as exc:
        reason = classify_connection_failure(str(exc))
        raise HostedDbError(
            f"hosted Postgres connection failed ({reason}) for "
            f"{redacted_url(settings.conninfo)}: {exc}"
        ) from exc
    return cast(HostedConnection, connection)


def _split_and_validate_port(database_url: str) -> SplitResult:
    """Split the URL, converting stdlib parse errors into policy terms."""

    try:
        parts = urlsplit(database_url)
    except ValueError as exc:
        raise HostedDbError(f"invalid hosted database URL ({exc})") from exc
    _parsed_port(parts)
    return parts


def _parsed_port(parts: SplitResult) -> int | None:
    """Parse the URL port, converting the stdlib ValueError into policy terms."""

    try:
        return parts.port
    except ValueError as exc:
        raise HostedDbError(f"invalid hosted database URL ({exc})") from exc


def _import_driver() -> Any:
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError as exc:
        raise HostedDbError(f"{_DRIVER_INSTALL_HINT} (import failed: {exc})") from exc
    return psycopg
