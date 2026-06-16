"""Tests for per-repo hosted review rollout (cortex#397)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from click.testing import CliRunner

from cortex.commands.review_rollout import (
    render_review_rollout_status,
    review_rollout_group,
)
from cortex.hosted.review_rollout import (
    ReviewRolloutError,
    ReviewRolloutEvent,
    ReviewRolloutStore,
    normalize_repo_full_name,
    review_rollout_insert_sql,
    review_rollout_status_sql,
)
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION, create_schema_sql

WHEN = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class FakeRolloutDb:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> FakeCursor:
        q = query.strip()
        p = dict(params or {})
        if q.startswith("INSERT INTO cortex_hosted.review_rollout_events"):
            if any(event["idempotency_key"] == p["idempotency_key"] for event in self.events):
                return FakeCursor([])
            event_id = str(uuid4())
            self.events.append(
                {
                    "review_rollout_event_id": event_id,
                    "repo_full_name": p["repo_full_name"],
                    "enabled": p["enabled"],
                    "actor": p["actor"],
                    "reason": p["reason"],
                    "occurred_at": p["occurred_at"],
                    "recorded_at": datetime.now(UTC),
                    "idempotency_key": p["idempotency_key"],
                }
            )
            return FakeCursor([(event_id,)])
        if q.startswith("SELECT") and "FROM cortex_hosted.review_rollout_events" in q:
            rows = [
                event for event in self.events if event["repo_full_name"] == p["repo_full_name"]
            ]
            rows.sort(
                key=lambda event: (
                    event["occurred_at"],
                    event["recorded_at"],
                    event["review_rollout_event_id"],
                ),
                reverse=True,
            )
            if not rows:
                return FakeCursor([])
            event = rows[0]
            return FakeCursor(
                [
                    (
                        event["review_rollout_event_id"],
                        event["repo_full_name"],
                        event["enabled"],
                        event["actor"],
                        event["reason"],
                        event["occurred_at"],
                        event["recorded_at"],
                    )
                ]
            )
        raise AssertionError(f"FakeRolloutDb saw unexpected SQL: {q[:120]}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _event(**overrides: object) -> ReviewRolloutEvent:
    kwargs: dict[str, object] = {
        "repo_full_name": "AutumnGarage/Cortex",
        "enabled": True,
        "actor": "henry",
        "reason": "dogfood rollout",
        "occurred_at": WHEN,
    }
    kwargs.update(overrides)
    return ReviewRolloutEvent(**kwargs)  # type: ignore[arg-type]


def test_repo_full_name_normalizes_case_and_rejects_ambiguous_values() -> None:
    assert normalize_repo_full_name("AutumnGarage/Cortex") == "autumngarage/cortex"
    for bad in ("", "autumngarage", "autumngarage/cortex/extra", "owner with space/repo"):
        with pytest.raises(ReviewRolloutError):
            normalize_repo_full_name(bad)


def test_fresh_repo_defaults_disabled_without_a_config_event() -> None:
    status = ReviewRolloutStore(FakeRolloutDb()).status_for("autumngarage/cortex")
    assert status.enabled is False
    assert status.configured is False
    assert status.reason == "no_rollout_event"
    assert "default off" in render_review_rollout_status(status)


def test_latest_rollout_event_controls_current_state_without_mutation() -> None:
    db = FakeRolloutDb()
    store = ReviewRolloutStore(db)
    first = _event(enabled=True, reason="start")
    second = _event(
        enabled=False,
        reason="pause",
        occurred_at=WHEN + timedelta(minutes=5),
    )
    assert store.record(first) is not None
    assert store.record(second) is not None
    status = store.status_for("autumngarage/cortex")
    assert status.configured is True
    assert status.enabled is False
    assert status.reason == "pause"
    assert len(db.events) == 2


def test_rollout_event_insert_is_idempotent_by_key() -> None:
    db = FakeRolloutDb()
    event = _event(idempotency_key="rollout:one")
    store = ReviewRolloutStore(db)
    assert store.record(event) is not None
    assert store.record(event) is None
    assert len(db.events) == 1


def test_rollout_sql_shapes_are_safe_and_default_off_by_caller_contract() -> None:
    insert_sql = review_rollout_insert_sql()
    status_sql = review_rollout_status_sql()
    assert "INSERT INTO cortex_hosted.review_rollout_events" in insert_sql
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in insert_sql
    assert "RETURNING review_rollout_event_id" in insert_sql
    assert "ORDER BY occurred_at DESC" in status_sql
    assert "LIMIT 1" in status_sql
    with pytest.raises(ReviewRolloutError):
        review_rollout_insert_sql("cortex; DROP TABLE x")
    with pytest.raises(ReviewRolloutError):
        review_rollout_status_sql("cortex; DROP TABLE x")


def test_schema_defines_append_only_rollout_events() -> None:
    sql = create_schema_sql()
    assert HOSTED_SCHEMA_VERSION == 13
    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.review_rollout_events" in sql
    assert "CONSTRAINT review_rollout_events_idempotency_key_unique UNIQUE" in sql
    assert "CHECK (repo_full_name ~ '^[a-z0-9_.-]+/[a-z0-9_.-]+$')" in sql
    assert "prevent_review_rollout_mutation" in sql
    assert "BEFORE UPDATE ON cortex_hosted.review_rollout_events" in sql
    assert "BEFORE DELETE ON cortex_hosted.review_rollout_events" in sql
    assert "Absence means disabled" in sql


def test_review_rollout_command_errors_without_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = CliRunner().invoke(review_rollout_group, ["status", "autumngarage/cortex"])
    assert result.exit_code == 1
    assert "DATABASE_URL is not set" in result.output


def test_review_rollout_command_appends_event_and_reports_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FakeRolloutDb()
    monkeypatch.setenv("DATABASE_URL", "postgres://user@host/db")
    monkeypatch.setattr("cortex.commands.review_rollout.connect", lambda _dsn: db)

    set_result = CliRunner().invoke(
        review_rollout_group,
        [
            "set",
            "AutumnGarage/Cortex",
            "enabled",
            "--actor",
            "henry",
            "--reason",
            "dogfood rollout",
            "--idempotency-key",
            "rollout:test",
            "--json",
        ],
    )
    assert set_result.exit_code == 0
    payload = json.loads(set_result.output)
    assert payload["repo_full_name"] == "autumngarage/cortex"
    assert payload["enabled"] is True
    assert payload["inserted"] is True
    assert db.commits == 1

    status_result = CliRunner().invoke(
        review_rollout_group, ["status", "autumngarage/cortex", "--json"]
    )
    assert status_result.exit_code == 0
    status = json.loads(status_result.output)
    assert status["enabled"] is True
    assert status["configured"] is True
