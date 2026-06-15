"""Per-repo rollout gate for hosted PR review comments (cortex#397).

The GitHub App can be installed broadly while review comments stay opt-in per
repository. Rollout state is an append-only event stream:

- no event for a repo means **disabled** (fresh installs are off);
- each enable/disable writes a ``review_rollout_events`` row;
- the worker checks the latest row on every PR delivery, so changes take effect
  without redeploy and without an in-process cache.

This is operator-internal configuration, separate from the customer decision
ledger. The table records the rollout decision itself; review traffic, cost,
and feedback remain in their existing append-only stores.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cortex.hosted.db import HostedConnection

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ReviewRolloutError(ValueError):
    """Raised when rollout config would be ambiguous or unsafe."""


@dataclass(frozen=True)
class ReviewRolloutEvent:
    """One operator enable/disable action for a repository."""

    repo_full_name: str
    enabled: bool
    actor: str
    reason: str
    occurred_at: datetime
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_full_name", normalize_repo_full_name(self.repo_full_name))
        if not isinstance(self.enabled, bool):
            raise ReviewRolloutError("enabled must be a bool")
        for name, value in (
            ("actor", self.actor),
            ("reason", self.reason),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ReviewRolloutError(f"{name} must be a non-empty string")
        if not isinstance(self.occurred_at, datetime):
            raise ReviewRolloutError("occurred_at must be a datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ReviewRolloutError("occurred_at must be timezone-aware")
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip()
        ):
            raise ReviewRolloutError("idempotency_key must be non-empty when supplied")

    @property
    def stable_idempotency_key(self) -> str:
        """Stable key for this exact operator event."""

        if self.idempotency_key is not None:
            return self.idempotency_key.strip()
        material = "|".join(
            (
                self.repo_full_name,
                "enabled" if self.enabled else "disabled",
                self.actor.strip(),
                self.reason.strip(),
                self.occurred_at.isoformat(),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_insert_parameters(self) -> dict[str, Any]:
        return {
            "repo_full_name": self.repo_full_name,
            "enabled": self.enabled,
            "actor": self.actor.strip(),
            "reason": self.reason.strip(),
            "occurred_at": self.occurred_at,
            "idempotency_key": self.stable_idempotency_key,
        }


@dataclass(frozen=True)
class ReviewRolloutStatus:
    """Current derived rollout status for one repo."""

    repo_full_name: str
    enabled: bool
    configured: bool
    actor: str | None = None
    reason: str | None = None
    occurred_at: datetime | None = None
    recorded_at: datetime | None = None
    event_id: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "repo_full_name": self.repo_full_name,
            "enabled": self.enabled,
            "configured": self.configured,
            "actor": self.actor,
            "reason": self.reason,
            "occurred_at": None if self.occurred_at is None else self.occurred_at.isoformat(),
            "recorded_at": None if self.recorded_at is None else self.recorded_at.isoformat(),
            "event_id": self.event_id,
        }


def normalize_repo_full_name(repo_full_name: str) -> str:
    """Validate and normalize a GitHub ``owner/repo`` name.

    GitHub repo identity is case-insensitive for routing, so rollout config is
    stored lower-case. That prevents an enabled ``AutumnGarage/Cortex`` event
    from missing a webhook whose payload says ``autumngarage/cortex``.
    """

    if not isinstance(repo_full_name, str):
        raise ReviewRolloutError("repo_full_name must be a string")
    normalized = repo_full_name.strip().lower()
    if not _REPO_FULL_NAME_RE.match(normalized):
        raise ReviewRolloutError(
            "repo_full_name must be a GitHub owner/repo name using letters, "
            "numbers, '.', '_' or '-'"
        )
    owner, repo = normalized.split("/", maxsplit=1)
    if not owner or not repo:
        raise ReviewRolloutError("repo_full_name must include both owner and repo")
    return normalized


def review_rollout_insert_sql(schema: str = "cortex_hosted") -> str:
    """Return the idempotent append statement for rollout events."""

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.review_rollout_events (
    repo_full_name,
    enabled,
    actor,
    reason,
    occurred_at,
    idempotency_key
) VALUES (
    %(repo_full_name)s,
    %(enabled)s,
    %(actor)s,
    %(reason)s,
    %(occurred_at)s,
    %(idempotency_key)s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING review_rollout_event_id;
""".strip()


def review_rollout_status_sql(schema: str = "cortex_hosted") -> str:
    """Return the latest rollout event for one repo.

    No row is interpreted by the caller as disabled. The query is deliberately
    un-cached; the worker runs it per PR delivery so config changes take effect
    without redeploy.
    """

    _validate_sql_identifier(schema)
    return f"""
SELECT
    review_rollout_event_id,
    repo_full_name,
    enabled,
    actor,
    reason,
    occurred_at,
    recorded_at
FROM {schema}.review_rollout_events
WHERE repo_full_name = %(repo_full_name)s
ORDER BY occurred_at DESC, recorded_at DESC, review_rollout_event_id DESC
LIMIT 1;
""".strip()


class ReviewRolloutStore:
    """Small DB adapter for per-delivery rollout checks."""

    def __init__(self, conn: HostedConnection) -> None:
        self._conn = conn

    def status_for(self, repo_full_name: str) -> ReviewRolloutStatus:
        repo = normalize_repo_full_name(repo_full_name)
        row = self._conn.execute(review_rollout_status_sql(), {"repo_full_name": repo}).fetchone()
        if row is None:
            return ReviewRolloutStatus(
                repo_full_name=repo,
                enabled=False,
                configured=False,
                reason="no_rollout_event",
            )
        return _status_from_row(row)

    def is_enabled(self, repo_full_name: str) -> bool:
        return self.status_for(repo_full_name).enabled

    def record(self, event: ReviewRolloutEvent) -> str | None:
        row = self._conn.execute(
            review_rollout_insert_sql(), event.as_insert_parameters()
        ).fetchone()
        return None if row is None else str(row[0])


def _status_from_row(row: tuple[Any, ...] | Mapping[str, Any]) -> ReviewRolloutStatus:
    if isinstance(row, Mapping):
        event_id = row["review_rollout_event_id"]
        repo = row["repo_full_name"]
        enabled = row["enabled"]
        actor = row["actor"]
        reason = row["reason"]
        occurred_at = row["occurred_at"]
        recorded_at = row["recorded_at"]
    else:
        event_id, repo, enabled, actor, reason, occurred_at, recorded_at = row
    return ReviewRolloutStatus(
        repo_full_name=normalize_repo_full_name(str(repo)),
        enabled=bool(enabled),
        configured=True,
        actor=str(actor),
        reason=str(reason),
        occurred_at=occurred_at,
        recorded_at=recorded_at,
        event_id=str(event_id),
    )


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ReviewRolloutError(f"invalid SQL identifier: {name!r}")
