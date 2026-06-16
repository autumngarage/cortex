"""GitHub App installation identity for hosted review traffic (cortex#572).

The GitHub webhook payload gives us an installation id and a repository name;
hosted telemetry needs the tenant/source rows those belong to.  This module is
the narrow bridge: installation lifecycle webhooks maintain stored bindings,
and review/feedback workers resolve ``installation_id + repo`` through those
bindings before writing any tenant-scoped telemetry.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from cortex.hosted.db import HostedConnection

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REPO_FULL_NAME_RE = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/autumngarage/cortex#github-installations")


class GithubInstallationError(ValueError):
    """Raised when a GitHub installation binding would be ambiguous."""


@dataclass(frozen=True)
class GithubRepoRef:
    """One repository visible to a GitHub App installation."""

    repo_full_name: str
    external_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_full_name", normalize_repo_full_name(self.repo_full_name))
        if not isinstance(self.external_id, str) or not self.external_id.strip():
            raise GithubInstallationError("repo external_id must be a non-empty string")
        object.__setattr__(self, "external_id", self.external_id.strip())


@dataclass(frozen=True)
class GithubInstallationIdentity:
    """The tenant/source identity resolved for one installed repository."""

    installation_id: str
    tenant_id: str
    source_id: str
    repo_full_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "installation_id", normalize_installation_id(self.installation_id))
        object.__setattr__(self, "repo_full_name", normalize_repo_full_name(self.repo_full_name))
        for name, value in (("tenant_id", self.tenant_id), ("source_id", self.source_id)):
            if not isinstance(value, str) or not value.strip():
                raise GithubInstallationError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class GithubInstallationEvent:
    """Parsed installation lifecycle event."""

    action: str
    installation_id: str
    account_login: str
    account_type: str
    repositories: tuple[GithubRepoRef, ...]
    occurred_at: datetime


@dataclass(frozen=True)
class GithubInstallationRepositoriesEvent:
    """Parsed installation_repositories lifecycle event."""

    action: str
    installation_id: str
    account_login: str
    account_type: str
    repositories_added: tuple[GithubRepoRef, ...]
    repositories_removed: tuple[GithubRepoRef, ...]
    occurred_at: datetime


class GithubInstallationStore:
    """Small DB adapter for installation -> tenant/source resolution."""

    def __init__(self, conn: HostedConnection) -> None:
        self._conn = conn

    def resolve(
        self, *, installation_id: str, repo_full_name: str
    ) -> GithubInstallationIdentity | None:
        """Resolve an active installation/repo binding, or ``None`` if absent."""

        installation = normalize_installation_id(installation_id)
        repo = normalize_repo_full_name(repo_full_name)
        row = self._conn.execute(
            github_installation_resolve_sql(),
            {"installation_id": installation, "repo_full_name": repo},
        ).fetchone()
        if row is None:
            return None
        return identity_from_row(row)

    def upsert_installation(
        self,
        *,
        installation_id: str,
        account_login: str,
        account_type: str,
        active: bool,
        occurred_at: datetime,
    ) -> str:
        """Ensure the tenant + installation rows exist and return tenant_id."""

        installation = normalize_installation_id(installation_id)
        tenant_id = tenant_id_for_installation(installation)
        row = self._conn.execute(
            github_installation_upsert_sql(),
            {
                "installation_id": installation,
                "tenant_id": tenant_id,
                "tenant_slug": tenant_slug_for_installation(account_login, installation),
                "tenant_display_name": tenant_display_name_for_installation(
                    account_login, installation
                ),
                "account_login": _require_non_empty_str(account_login, "account_login"),
                "account_type": _require_non_empty_str(account_type, "account_type"),
                "active": bool(active),
                "occurred_at": _require_aware_datetime(occurred_at),
            },
        ).fetchone()
        if row is None:
            raise GithubInstallationError(
                f"installation {installation} upsert returned no row; cannot resolve tenant"
            )
        return str(row[0])

    def upsert_repo(
        self,
        *,
        installation_id: str,
        account_login: str,
        account_type: str,
        repo: GithubRepoRef,
        occurred_at: datetime,
    ) -> GithubInstallationIdentity:
        """Ensure one installed repo binding exists and is active."""

        installation = normalize_installation_id(installation_id)
        tenant_id = self.upsert_installation(
            installation_id=installation,
            account_login=account_login,
            account_type=account_type,
            active=True,
            occurred_at=occurred_at,
        )
        source_id = source_id_for_installation_repo(installation, repo.repo_full_name)
        row = self._conn.execute(
            github_installation_repo_upsert_sql(),
            {
                "installation_id": installation,
                "tenant_id": tenant_id,
                "source_id": source_id,
                "repo_full_name": repo.repo_full_name,
                "source_external_id": repo.external_id,
                "visibility": json.dumps(
                    {
                        "github_installation_id": installation,
                        "repo_installation_id": installation,
                    },
                    sort_keys=True,
                ),
                "occurred_at": _require_aware_datetime(occurred_at),
            },
        ).fetchone()
        if row is None:
            raise GithubInstallationError(
                f"repo binding {installation}:{repo.repo_full_name} upsert returned no row"
            )
        return identity_from_row(row)

    def deactivate_repo(
        self, *, installation_id: str, repo: GithubRepoRef, occurred_at: datetime
    ) -> GithubInstallationIdentity | None:
        """Mark one installed repo inactive and revoke its source visibility."""

        installation = normalize_installation_id(installation_id)
        row = self._conn.execute(
            github_installation_repo_deactivate_sql(),
            {
                "installation_id": installation,
                "repo_full_name": repo.repo_full_name,
                "occurred_at": _require_aware_datetime(occurred_at),
            },
        ).fetchone()
        if row is None:
            return None
        identity = identity_from_row(row)
        self._mark_source_revoked(identity.source_id)
        return identity

    def deactivate_installation(
        self, *, installation_id: str, occurred_at: datetime
    ) -> tuple[GithubInstallationIdentity, ...]:
        """Mark an installation and all repo bindings inactive."""

        installation = normalize_installation_id(installation_id)
        self._conn.execute(
            github_installation_deactivate_sql(),
            {"installation_id": installation, "occurred_at": _require_aware_datetime(occurred_at)},
        ).fetchone()
        rows = self._conn.execute(
            github_installation_repo_deactivate_all_sql(),
            {"installation_id": installation, "occurred_at": _require_aware_datetime(occurred_at)},
        ).fetchall()
        identities = tuple(identity_from_row(row) for row in rows)
        for identity in identities:
            self._mark_source_revoked(identity.source_id)
        return identities

    def _mark_source_revoked(self, source_id: str) -> None:
        self._conn.execute(source_mark_repo_installation_revoked_sql(), {"source_id": source_id})


def record_installation_event(
    store: GithubInstallationStore, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a ``github.installation`` webhook to the identity store."""

    event = parse_installation_event(payload)
    if event.action in {"created", "unsuspend", "new_permissions_accepted"}:
        tenant_id = store.upsert_installation(
            installation_id=event.installation_id,
            account_login=event.account_login,
            account_type=event.account_type,
            active=True,
            occurred_at=event.occurred_at,
        )
        recorded = [
            store.upsert_repo(
                installation_id=event.installation_id,
                account_login=event.account_login,
                account_type=event.account_type,
                repo=repo,
                occurred_at=event.occurred_at,
            )
            for repo in event.repositories
        ]
        return {
            "handled": True,
            "installation_id": event.installation_id,
            "installation_action": event.action,
            "tenant_id": tenant_id,
            "repos_recorded": len(recorded),
        }
    if event.action in {"deleted", "suspend"}:
        deactivated = store.deactivate_installation(
            installation_id=event.installation_id,
            occurred_at=event.occurred_at,
        )
        return {
            "handled": True,
            "installation_id": event.installation_id,
            "installation_action": event.action,
            "repos_deactivated": len(deactivated),
        }
    return {
        "handled": True,
        "installation_id": event.installation_id,
        "installation_action": event.action,
        "reason": "unsupported_installation_action",
    }


def record_installation_repositories_event(
    store: GithubInstallationStore, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a ``github.installation_repositories`` webhook."""

    event = parse_installation_repositories_event(payload)
    if event.action != "added" and event.action != "removed":
        return {
            "handled": True,
            "installation_id": event.installation_id,
            "installation_action": event.action,
            "reason": "unsupported_installation_repositories_action",
        }
    added = [
        store.upsert_repo(
            installation_id=event.installation_id,
            account_login=event.account_login,
            account_type=event.account_type,
            repo=repo,
            occurred_at=event.occurred_at,
        )
        for repo in event.repositories_added
    ]
    removed = [
        store.deactivate_repo(
            installation_id=event.installation_id,
            repo=repo,
            occurred_at=event.occurred_at,
        )
        for repo in event.repositories_removed
    ]
    return {
        "handled": True,
        "installation_id": event.installation_id,
        "installation_action": event.action,
        "repos_added": len(added),
        "repos_removed": sum(1 for identity in removed if identity is not None),
    }


def parse_installation_event(payload: Mapping[str, Any]) -> GithubInstallationEvent:
    body = _event_body(payload)
    installation = _require_mapping(body, "installation")
    account = _require_mapping(installation, "account")
    return GithubInstallationEvent(
        action=_optional_str(body, "action") or "unknown",
        installation_id=_require_scalar_str(installation, "id"),
        account_login=_require_str(account, "login"),
        account_type=_optional_str(account, "type") or "unknown",
        repositories=_repo_tuple(body.get("repositories")),
        occurred_at=_occurred_at(payload),
    )


def parse_installation_repositories_event(
    payload: Mapping[str, Any],
) -> GithubInstallationRepositoriesEvent:
    body = _event_body(payload)
    installation = _require_mapping(body, "installation")
    account = _require_mapping(installation, "account")
    return GithubInstallationRepositoriesEvent(
        action=_optional_str(body, "action") or "unknown",
        installation_id=_require_scalar_str(installation, "id"),
        account_login=_require_str(account, "login"),
        account_type=_optional_str(account, "type") or "unknown",
        repositories_added=_repo_tuple(body.get("repositories_added")),
        repositories_removed=_repo_tuple(body.get("repositories_removed")),
        occurred_at=_occurred_at(payload),
    )


def normalize_installation_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise GithubInstallationError("installation_id must be a non-empty string or integer")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise GithubInstallationError("installation_id must be a non-empty string or integer")
    return value.strip()


def normalize_repo_full_name(repo_full_name: str) -> str:
    if not isinstance(repo_full_name, str):
        raise GithubInstallationError("repo_full_name must be a string")
    normalized = repo_full_name.strip().lower()
    if not _REPO_FULL_NAME_RE.match(normalized):
        raise GithubInstallationError(
            "repo_full_name must be a GitHub owner/repo name using letters, "
            "numbers, '.', '_' or '-'"
        )
    return normalized


def tenant_id_for_installation(installation_id: str) -> str:
    return str(uuid5(_NAMESPACE, f"tenant:{normalize_installation_id(installation_id)}"))


def source_id_for_installation_repo(installation_id: str, repo_full_name: str) -> str:
    return str(
        uuid5(
            _NAMESPACE,
            f"source:{normalize_installation_id(installation_id)}:"
            f"{normalize_repo_full_name(repo_full_name)}",
        )
    )


def tenant_slug_for_installation(account_login: str, installation_id: str) -> str:
    account = _SLUG_RE.sub("-", _require_non_empty_str(account_login, "account_login").lower())
    return f"github-{account.strip('-') or 'account'}-{normalize_installation_id(installation_id)}"


def tenant_display_name_for_installation(account_login: str, installation_id: str) -> str:
    return (
        f"GitHub {account_login.strip()} installation {normalize_installation_id(installation_id)}"
    )


def identity_from_row(row: tuple[Any, ...] | Mapping[str, Any]) -> GithubInstallationIdentity:
    if isinstance(row, Mapping):
        installation_id = row["installation_id"]
        tenant_id = row["tenant_id"]
        source_id = row["source_id"]
        repo_full_name = row["repo_full_name"]
    else:
        installation_id, tenant_id, source_id, repo_full_name = row
    return GithubInstallationIdentity(
        installation_id=str(installation_id),
        tenant_id=str(tenant_id),
        source_id=str(source_id),
        repo_full_name=str(repo_full_name),
    )


def github_installation_upsert_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
WITH tenant_row AS (
    INSERT INTO {schema}.tenants (tenant_id, slug, display_name)
    VALUES (%(tenant_id)s, %(tenant_slug)s, %(tenant_display_name)s)
    ON CONFLICT (tenant_id) DO UPDATE
        SET display_name = EXCLUDED.display_name
    RETURNING tenant_id
)
INSERT INTO {schema}.github_installations (
    installation_id,
    tenant_id,
    account_login,
    account_type,
    active,
    installed_at,
    updated_at
) VALUES (
    %(installation_id)s,
    (SELECT tenant_id FROM tenant_row),
    %(account_login)s,
    %(account_type)s,
    %(active)s,
    %(occurred_at)s,
    now()
)
ON CONFLICT (installation_id) DO UPDATE
    SET tenant_id = EXCLUDED.tenant_id,
        account_login = EXCLUDED.account_login,
        account_type = EXCLUDED.account_type,
        active = EXCLUDED.active,
        deleted_at = CASE WHEN EXCLUDED.active THEN NULL ELSE {schema}.github_installations.deleted_at END,
        updated_at = now()
RETURNING tenant_id;
""".strip()


def github_installation_repo_upsert_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
WITH source_row AS (
    INSERT INTO {schema}.sources (
        source_id,
        tenant_id,
        source_type,
        external_id,
        visibility
    ) VALUES (
        %(source_id)s,
        %(tenant_id)s,
        'github_repo',
        %(source_external_id)s,
        %(visibility)s::jsonb
    )
    ON CONFLICT (tenant_id, source_type, external_id) DO UPDATE
        SET visibility = (
            ({schema}.sources.visibility - 'repo_installation_revoked')
            || EXCLUDED.visibility
        )
    RETURNING source_id
)
INSERT INTO {schema}.github_installation_repositories (
    installation_id,
    repo_full_name,
    tenant_id,
    source_id,
    active,
    added_at,
    removed_at,
    updated_at
) VALUES (
    %(installation_id)s,
    %(repo_full_name)s,
    %(tenant_id)s,
    (SELECT source_id FROM source_row),
    true,
    %(occurred_at)s,
    NULL,
    now()
)
ON CONFLICT (installation_id, repo_full_name) DO UPDATE
    SET tenant_id = EXCLUDED.tenant_id,
        source_id = EXCLUDED.source_id,
        active = true,
        removed_at = NULL,
        updated_at = now()
RETURNING installation_id, tenant_id, source_id, repo_full_name;
""".strip()


def github_installation_repo_deactivate_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.github_installation_repositories
SET active = false,
    removed_at = %(occurred_at)s,
    updated_at = now()
WHERE installation_id = %(installation_id)s
  AND repo_full_name = %(repo_full_name)s
RETURNING installation_id, tenant_id, source_id, repo_full_name;
""".strip()


def github_installation_deactivate_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.github_installations
SET active = false,
    deleted_at = %(occurred_at)s,
    updated_at = now()
WHERE installation_id = %(installation_id)s
RETURNING tenant_id;
""".strip()


def github_installation_repo_deactivate_all_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.github_installation_repositories
SET active = false,
    removed_at = %(occurred_at)s,
    updated_at = now()
WHERE installation_id = %(installation_id)s
RETURNING installation_id, tenant_id, source_id, repo_full_name;
""".strip()


def source_mark_repo_installation_revoked_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
UPDATE {schema}.sources
SET visibility = visibility || '{{"repo_installation_revoked": true}}'::jsonb
WHERE source_id = %(source_id)s
RETURNING source_id;
""".strip()


def github_installation_resolve_sql(schema: str = "cortex_hosted") -> str:
    _validate_sql_identifier(schema)
    return f"""
SELECT
    repo.installation_id,
    repo.tenant_id,
    repo.source_id,
    repo.repo_full_name
FROM {schema}.github_installation_repositories AS repo
JOIN {schema}.github_installations AS installation
  ON installation.installation_id = repo.installation_id
WHERE repo.installation_id = %(installation_id)s
  AND repo.repo_full_name = %(repo_full_name)s
  AND repo.active = true
  AND installation.active = true
LIMIT 1;
""".strip()


def _event_body(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise GithubInstallationError("github installation payload must be a JSON object")
    body = payload.get("body")
    return body if isinstance(body, Mapping) else payload


def _repo_tuple(value: Any) -> tuple[GithubRepoRef, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise GithubInstallationError("repositories must be an array when supplied")
    return tuple(_repo_from_mapping(item) for item in value)


def _repo_from_mapping(value: Any) -> GithubRepoRef:
    if not isinstance(value, Mapping):
        raise GithubInstallationError("repository entries must be JSON objects")
    full_name = _optional_str(value, "full_name")
    if full_name is None:
        owner = value.get("owner")
        owner_login = owner.get("login") if isinstance(owner, Mapping) else None
        name = _optional_str(value, "name")
        if owner_login is None or name is None:
            raise GithubInstallationError(
                "repository entry must carry full_name or owner.login/name"
            )
        full_name = f"{owner_login}/{name}"
    external = value.get("id")
    external_id = (
        str(external) if external is not None and not isinstance(external, bool) else full_name
    )
    return GithubRepoRef(repo_full_name=full_name, external_id=external_id)


def _occurred_at(payload: Mapping[str, Any]) -> datetime:
    raw = payload.get("received_at")
    if raw is None:
        return datetime.now(UTC)
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise GithubInstallationError(f"received_at is not an ISO timestamp: {raw!r}") from exc
    return _require_aware_datetime(value)


def _require_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise GithubInstallationError(
            f"github installation payload field {key!r} must be an object"
        )
    return value


def _require_str(payload: Mapping[str, Any], key: str) -> str:
    return _require_non_empty_str(payload.get(key), key)


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GithubInstallationError(f"{key} must be a non-empty string when supplied")
    return value.strip()


def _require_scalar_str(payload: Mapping[str, Any], key: str) -> str:
    return normalize_installation_id(payload.get(key))


def _require_non_empty_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GithubInstallationError(f"{name} must be a non-empty string")
    return value.strip()


def _require_aware_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise GithubInstallationError("occurred_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise GithubInstallationError("occurred_at must be timezone-aware")
    return value


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise GithubInstallationError(f"invalid SQL identifier: {name!r}")
