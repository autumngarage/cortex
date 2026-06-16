from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from cortex.hosted.github_installations import (
    GithubInstallationError,
    GithubInstallationIdentity,
    GithubInstallationStore,
    GithubRepoRef,
    github_installation_repo_upsert_sql,
    github_installation_resolve_sql,
    normalize_repo_full_name,
    parse_installation_event,
    record_installation_event,
    record_installation_repositories_event,
    source_id_for_installation_repo,
    tenant_id_for_installation,
)


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _InstallationDb:
    def __init__(self) -> None:
        self.tenants: set[str] = set()
        self.bindings: dict[tuple[str, str], GithubInstallationIdentity] = {}
        self.revoked_sources: set[str] = set()

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> _Cursor:
        q = query.strip()
        p = dict(params or {})
        if q.startswith("WITH tenant_row AS"):
            self.tenants.add(str(p["tenant_id"]))
            return _Cursor([(str(p["tenant_id"]),)])
        if q.startswith("WITH source_row AS"):
            identity = GithubInstallationIdentity(
                installation_id=str(p["installation_id"]),
                tenant_id=str(p["tenant_id"]),
                source_id=str(p["source_id"]),
                repo_full_name=str(p["repo_full_name"]),
            )
            self.bindings[(identity.installation_id, identity.repo_full_name)] = identity
            return _Cursor(
                [
                    (
                        identity.installation_id,
                        identity.tenant_id,
                        identity.source_id,
                        identity.repo_full_name,
                    )
                ]
            )
        if q.startswith("SELECT") and "FROM cortex_hosted.github_installation_repositories" in q:
            found_identity = self.bindings.get(
                (str(p["installation_id"]), normalize_repo_full_name(str(p["repo_full_name"])))
            )
            if found_identity is None:
                return _Cursor([])
            return _Cursor(
                [
                    (
                        found_identity.installation_id,
                        found_identity.tenant_id,
                        found_identity.source_id,
                        found_identity.repo_full_name,
                    )
                ]
            )
        if q.startswith("UPDATE cortex_hosted.github_installation_repositories") and (
            "repo_full_name = %(repo_full_name)s" in q
        ):
            key = (
                str(p["installation_id"]),
                normalize_repo_full_name(str(p["repo_full_name"])),
            )
            found_identity = self.bindings.get(key)
            if found_identity is None:
                return _Cursor([])
            del self.bindings[key]
            return _Cursor(
                [
                    (
                        found_identity.installation_id,
                        found_identity.tenant_id,
                        found_identity.source_id,
                        found_identity.repo_full_name,
                    )
                ]
            )
        if q.startswith("UPDATE cortex_hosted.github_installation_repositories"):
            rows = []
            for key, identity in list(self.bindings.items()):
                if key[0] == str(p["installation_id"]):
                    rows.append(
                        (
                            identity.installation_id,
                            identity.tenant_id,
                            identity.source_id,
                            identity.repo_full_name,
                        )
                    )
                    del self.bindings[key]
            return _Cursor(rows)
        if q.startswith("UPDATE cortex_hosted.github_installations"):
            return _Cursor([(tenant_id_for_installation(str(p["installation_id"])),)])
        if q.startswith("UPDATE cortex_hosted.sources"):
            self.revoked_sources.add(str(p["source_id"]))
            return _Cursor([(str(p["source_id"]),)])
        raise AssertionError(f"unexpected SQL: {q[:80]}")


def _installation_payload(action: str = "created") -> dict[str, Any]:
    return {
        "received_at": "2026-06-16T12:00:00+00:00",
        "body": {
            "action": action,
            "installation": {
                "id": 111,
                "account": {"login": "AutumnGarage", "type": "Organization"},
            },
            "repositories": [{"id": 42, "full_name": "AutumnGarage/Cortex"}],
        },
    }


def test_parse_installation_event_normalizes_repo_identity() -> None:
    event = parse_installation_event(_installation_payload())

    assert event.installation_id == "111"
    assert event.account_login == "AutumnGarage"
    assert event.repositories == (GithubRepoRef("autumngarage/cortex", "42"),)
    assert event.occurred_at == datetime(2026, 6, 16, 12, tzinfo=UTC)


def test_installation_created_records_resolvable_tenant_source_binding() -> None:
    db = _InstallationDb()
    store = GithubInstallationStore(db)  # type: ignore[arg-type]

    result = record_installation_event(store, _installation_payload())
    identity = store.resolve(installation_id="111", repo_full_name="autumngarage/cortex")

    assert result["repos_recorded"] == 1
    assert identity is not None
    assert identity.tenant_id == tenant_id_for_installation("111")
    assert identity.source_id == source_id_for_installation_repo("111", "autumngarage/cortex")


def test_repository_removed_deactivates_binding_and_marks_source_revoked() -> None:
    db = _InstallationDb()
    store = GithubInstallationStore(db)  # type: ignore[arg-type]
    record_installation_event(store, _installation_payload())

    result = record_installation_repositories_event(
        store,
        {
            "received_at": "2026-06-16T12:05:00+00:00",
            "body": {
                "action": "removed",
                "installation": {
                    "id": 111,
                    "account": {"login": "AutumnGarage", "type": "Organization"},
                },
                "repositories_removed": [{"id": 42, "full_name": "AutumnGarage/Cortex"}],
            },
        },
    )

    assert result["repos_removed"] == 1
    assert store.resolve(installation_id="111", repo_full_name="autumngarage/cortex") is None
    assert db.revoked_sources == {source_id_for_installation_repo("111", "autumngarage/cortex")}


def test_installation_sql_mentions_active_resolution_boundary() -> None:
    upsert = github_installation_repo_upsert_sql()
    resolve = github_installation_resolve_sql()

    assert "INSERT INTO cortex_hosted.sources" in upsert
    assert "'github_repo'" in upsert
    assert "repo_installation_revoked" in upsert
    assert "JOIN cortex_hosted.github_installations" in resolve
    assert "repo.active = true" in resolve
    assert "installation.active = true" in resolve


def test_installation_sql_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(GithubInstallationError, match="invalid SQL identifier"):
        github_installation_resolve_sql("bad;drop")
