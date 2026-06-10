"""Hosted service configuration from environment variables (cortex#470).

One frozen ``ServiceConfig`` shared by the API shell and the worker loop —
one code path for config parsing, per the engineering principles. Parsing is
fail-closed: a malformed value (non-integer port, non-UUID tenant id, a
boolean that is not a recognized token, a variable set to whitespace) raises
``ServiceConfigError`` naming the variable, instead of starting a service on
a half-understood environment.

Missing *optional* pieces do not raise — they degrade visibly per endpoint:

- no ``DATABASE_URL``: ``/healthz`` reports a degraded body and the webhook
  endpoint refuses persistence with 503 (cortex#470's contract);
- no ``GITHUB_WEBHOOK_SECRET``: the webhook endpoint refuses all deliveries
  with 503 — verification cannot be skipped;
- no ``CORTEX_TENANT_ID``/``CORTEX_SOURCE_ID``: worker stub handlers mark
  jobs handled but report that no ledger arrival event was recorded.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_STALE_CLAIM_SECONDS = 1800.0
_TRUE_TOKENS = frozenset({"1", "true", "yes"})
_FALSE_TOKENS = frozenset({"0", "false", "no"})


class ServiceConfigError(ValueError):
    """Raised when the hosted service environment is malformed."""


@dataclass(frozen=True)
class ServiceConfig:
    """Validated hosted service configuration.

    ``database_url`` and ``github_webhook_secret`` are optional at the type
    level because the API shell degrades per endpoint; the worker entrypoint
    requires ``database_url`` and refuses to start without it.
    """

    database_url: str | None = None
    github_webhook_secret: str | None = None
    port: int = DEFAULT_PORT
    host: str = DEFAULT_HOST
    commit_sha: str | None = None
    tenant_id: str | None = None
    source_id: str | None = None
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    stale_claim_seconds: float = DEFAULT_STALE_CLAIM_SECONDS
    apply_schema_on_start: bool = False

    def __post_init__(self) -> None:
        if self.database_url is not None and not self.database_url.strip():
            raise ServiceConfigError("DATABASE_URL is set but blank; unset it or set a DSN")
        if self.github_webhook_secret is not None and not self.github_webhook_secret.strip():
            raise ServiceConfigError(
                "GITHUB_WEBHOOK_SECRET is set but blank; unset it or set the App secret"
            )
        # 0 is the well-defined "OS assigns an ephemeral port" bind value
        # (used by tests and local smoke runs); Railway always sets a real
        # PORT, and a stray 0 in production fails the healthcheck visibly.
        if not (0 <= self.port <= 65535):
            raise ServiceConfigError(f"PORT must be in 0..65535, got {self.port}")
        if not self.host.strip():
            raise ServiceConfigError("host must not be empty")
        for name, value in (("CORTEX_TENANT_ID", self.tenant_id), ("CORTEX_SOURCE_ID", self.source_id)):
            if value is not None:
                try:
                    UUID(value)
                except ValueError as exc:
                    raise ServiceConfigError(f"{name} must be a UUID, got {value!r}") from exc
        if (self.tenant_id is None) != (self.source_id is None):
            raise ServiceConfigError(
                "CORTEX_TENANT_ID and CORTEX_SOURCE_ID must be set together; a ledger "
                "arrival event needs both the tenant and the source row"
            )
        if self.poll_interval_seconds <= 0:
            raise ServiceConfigError(
                f"CORTEX_WORKER_POLL_SECONDS must be positive, got {self.poll_interval_seconds}"
            )
        if self.stale_claim_seconds <= 0:
            raise ServiceConfigError(
                f"CORTEX_STALE_CLAIM_SECONDS must be positive, got {self.stale_claim_seconds}"
            )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ServiceConfig:
        """Build the config from the process environment, fail-closed."""

        env = os.environ if environ is None else environ
        return cls(
            database_url=_optional(env, "DATABASE_URL"),
            github_webhook_secret=_optional(env, "GITHUB_WEBHOOK_SECRET"),
            port=_parse_int(env, "PORT", DEFAULT_PORT),
            host=env.get("CORTEX_API_HOST", DEFAULT_HOST),
            commit_sha=_optional(env, "RAILWAY_GIT_COMMIT_SHA"),
            tenant_id=_optional(env, "CORTEX_TENANT_ID"),
            source_id=_optional(env, "CORTEX_SOURCE_ID"),
            poll_interval_seconds=_parse_float(
                env, "CORTEX_WORKER_POLL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS
            ),
            stale_claim_seconds=_parse_float(
                env, "CORTEX_STALE_CLAIM_SECONDS", DEFAULT_STALE_CLAIM_SECONDS
            ),
            apply_schema_on_start=_parse_bool(env, "CORTEX_APPLY_SCHEMA_ON_START", False),
        )


def _optional(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    # Set-but-blank is malformed, not absent: fail closed in __post_init__
    # for the secrets, here for everything routed through _optional.
    return value


def _parse_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ServiceConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _parse_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ServiceConfigError(f"{name} must be a number, got {raw!r}") from exc


def _parse_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE_TOKENS:
        return True
    if lowered in _FALSE_TOKENS:
        return False
    raise ServiceConfigError(
        f"{name} must be one of 1/0/true/false/yes/no, got {raw!r}"
    )
