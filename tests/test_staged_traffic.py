"""Staged-traffic registry tests (cortex#575).

Invariant under test: staged demo traffic and organic traffic never blend.
A PR matching the staged convention produces exactly one registry row
(idempotent on redelivery), an organic PR produces none, and the registry
itself is append-only — the exclusion set is stable history that precision
metrics can reproduce.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from cortex.hosted.staged_pr import (
    STAGED_LABEL,
    STAGED_REASON_BACKFILL,
    STAGED_REASON_LABEL,
    STAGED_REASON_TITLE,
    STAGED_TITLE_TOKEN,
    StagedPrError,
    StagedPrRecord,
    detect_staged_reason,
    staged_pr_insert_sql,
)

_OWNER = "acme"
_REPO = "widgets"
_PR_NUMBER = 7


def _payload(
    *,
    title: str | None = None,
    labels: tuple[str, ...] = (),
) -> dict[str, Any]:
    pull_request: dict[str, Any] = {
        "number": _PR_NUMBER,
        "base": {"sha": "1111111"},
        "head": {"sha": "2222222"},
    }
    if title is not None:
        pull_request["title"] = title
    if labels:
        pull_request["labels"] = [{"name": name} for name in labels]
    return {
        "event": "pull_request",
        "body": {
            "action": "opened",
            "installation": {"id": 424242},
            "repository": {"name": _REPO, "owner": {"login": _OWNER}},
            "pull_request": pull_request,
        },
    }


# ---------------------------------------------------------------------------
# Detection: the documented convention, tolerant of anything else
# ---------------------------------------------------------------------------


def test_title_token_marks_staged_case_insensitively() -> None:
    assert (
        detect_staged_reason(_payload(title="chore: [CORTEX-DEMO] catch fixture"))
        == STAGED_REASON_TITLE
    )
    assert (
        detect_staged_reason(_payload(title=f"prefix {STAGED_TITLE_TOKEN} suffix"))
        == STAGED_REASON_TITLE
    )


def test_label_marks_staged() -> None:
    assert (
        detect_staged_reason(_payload(title="ordinary title", labels=(STAGED_LABEL,)))
        == STAGED_REASON_LABEL
    )
    # Label comparison tolerates case and padding; other labels never match.
    assert (
        detect_staged_reason(_payload(labels=(" Cortex-Demo-Fixture ",)))
        == STAGED_REASON_LABEL
    )
    assert detect_staged_reason(_payload(labels=("bug", "enhancement"))) is None


def test_title_wins_when_both_conventions_match() -> None:
    payload = _payload(title=f"{STAGED_TITLE_TOKEN} demo", labels=(STAGED_LABEL,))
    assert detect_staged_reason(payload) == STAGED_REASON_TITLE


def test_organic_and_malformed_payloads_are_not_staged() -> None:
    assert detect_staged_reason(_payload(title="fix: real work")) is None
    assert detect_staged_reason(_payload()) is None  # no title, no labels
    assert detect_staged_reason({}) is None
    assert detect_staged_reason({"body": {"pull_request": "not-a-mapping"}}) is None
    assert detect_staged_reason("not-a-mapping") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Record + SQL invariants
# ---------------------------------------------------------------------------


def _record(reason: str = STAGED_REASON_TITLE) -> StagedPrRecord:
    return StagedPrRecord(
        tenant_id=str(uuid4()),
        repo_full_name=f"{_OWNER}/{_REPO}",
        pr_number=_PR_NUMBER,
        reason=reason,
        recorded_at=datetime.now(UTC),
    )


def test_record_rejects_invalid_fields() -> None:
    with pytest.raises(StagedPrError, match="reason"):
        _record(reason="vibes")
    with pytest.raises(StagedPrError, match="pr_number"):
        StagedPrRecord(
            tenant_id=str(uuid4()),
            repo_full_name="a/b",
            pr_number=0,
            reason=STAGED_REASON_BACKFILL,
            recorded_at=datetime.now(UTC),
        )
    with pytest.raises(StagedPrError, match="timezone-aware"):
        StagedPrRecord(
            tenant_id=str(uuid4()),
            repo_full_name="a/b",
            pr_number=1,
            reason=STAGED_REASON_BACKFILL,
            recorded_at=datetime(2026, 6, 11, 12, 0, 0),  # naive
        )


def test_insert_sql_is_idempotent_on_pr_identity_and_binds_parameters() -> None:
    sql = staged_pr_insert_sql()
    assert "ON CONFLICT ON CONSTRAINT review_staged_prs_pr_unique DO NOTHING" in sql
    assert "RETURNING staged_pr_id" in sql
    for param in ("tenant_id", "repo_full_name", "pr_number", "reason", "recorded_at"):
        assert f"%({param})s" in sql
    with pytest.raises(StagedPrError, match="identifier"):
        staged_pr_insert_sql("drop table;--")


def test_idempotency_key_is_stable_over_pr_identity() -> None:
    a = _record()
    b = StagedPrRecord(
        tenant_id=a.tenant_id,
        repo_full_name=a.repo_full_name,
        pr_number=a.pr_number,
        reason=STAGED_REASON_LABEL,  # different reason...
        recorded_at=datetime.now(UTC),  # ...different time
    )
    assert a.idempotency_key == b.idempotency_key  # ...same PR -> same key


# ---------------------------------------------------------------------------
# Worker: one registry row per staged PR, none for organic, idempotent
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _StagedWorkerDb:
    """Fake DB emulating the jobs queue plus the review_staged_prs insert."""

    def __init__(self, payload: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        self._payload = dict(payload)
        self._result = dict(result)
        self.staged_rows: dict[tuple[str, str, int], dict[str, Any]] = {}
        self.claimed = False
        self.completed = False

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> _Cursor:
        q = query.strip()
        p = dict(params or {})
        if "FOR UPDATE SKIP LOCKED" in q:
            if self.claimed:
                return _Cursor([])
            self.claimed = True
            return _Cursor(
                [("job-1", "github.pull_request", "idem-1", json.dumps(self._payload), 1, 3)]
            )
        if q.startswith("INSERT INTO cortex_hosted.review_staged_prs"):
            key = (str(p["tenant_id"]), str(p["repo_full_name"]), int(p["pr_number"]))
            if key in self.staged_rows:
                return _Cursor([])  # ON CONFLICT DO NOTHING
            self.staged_rows[key] = dict(p)
            return _Cursor([(str(uuid4()),)])
        if "SET status = 'succeeded'" in q:
            self.completed = True
            return _Cursor([("job-1",)])
        raise AssertionError(f"unexpected SQL: {q[:80]}")

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def stub_result(self) -> Mapping[str, Any]:
        return self._result


def _stateless_result() -> dict[str, Any]:
    # No "cost" block on purpose: the cost recorder logs a visible skip and
    # writes nothing, keeping this fake focused on the staged registry.
    return {"handled": True, "review_mode": "stateless", "dry_run": True}


def _worker(db: _StagedWorkerDb) -> Any:
    from cortex.hosted.worker import HandlerRegistry, Worker

    registry = HandlerRegistry()
    registry.register("github.pull_request", lambda job: db.stub_result())
    return Worker(conn=db, registry=registry, worker_id="w-test")


def test_worker_registers_staged_pr_once_with_visible_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = _StagedWorkerDb(
        _payload(title="chore: [cortex-demo] catch walkthrough"), _stateless_result()
    )
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert _worker(db).run_once() is True
    assert len(db.staged_rows) == 1
    (row,) = db.staged_rows.values()
    assert row["repo_full_name"] == f"{_OWNER}/{_REPO}"
    assert row["pr_number"] == _PR_NUMBER
    assert row["reason"] == STAGED_REASON_TITLE
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "review.staged_pr" in events
    assert db.completed is True


def test_worker_writes_nothing_for_organic_prs(caplog: pytest.LogCaptureFixture) -> None:
    db = _StagedWorkerDb(_payload(title="fix: real organic work"), _stateless_result())
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert _worker(db).run_once() is True
    assert db.staged_rows == {}
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "review.staged_pr" not in events  # organic traffic is not narrated
    assert db.completed is True


def test_worker_staged_registration_is_idempotent_on_redelivery() -> None:
    payload = _payload(title=f"{STAGED_TITLE_TOKEN} demo")
    db = _StagedWorkerDb(payload, _stateless_result())
    worker = _worker(db)
    assert worker.run_once() is True
    db.claimed = False  # simulate redelivery of the same job
    db.completed = False
    assert worker.run_once() is True
    assert len(db.staged_rows) == 1  # ON CONFLICT collapsed the second insert


# ---------------------------------------------------------------------------
# Round-trip over a real Postgres (DATABASE_URL-gated)
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


@pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL not set; live round-trip skipped")
def test_staged_registry_round_trip_is_append_only_and_idempotent() -> None:
    import psycopg

    from cortex.hosted.db import connect
    from cortex.hosted.migrations import apply_schema

    connection = connect(DATABASE_URL)
    try:
        apply_schema(connection)
        record = StagedPrRecord(
            tenant_id=str(uuid4()),
            repo_full_name=f"test/{uuid4().hex[:8]}",
            pr_number=561,
            reason=STAGED_REASON_BACKFILL,
            recorded_at=datetime.now(UTC),
        )
        inserted = connection.execute(
            staged_pr_insert_sql(), record.as_insert_parameters()
        ).fetchone()
        assert inserted is not None
        duplicate = connection.execute(
            staged_pr_insert_sql(), record.as_insert_parameters()
        ).fetchone()
        assert duplicate is None  # idempotent on PR identity
        with pytest.raises(psycopg.Error):
            connection.execute(
                "UPDATE cortex_hosted.review_staged_prs SET reason = 'label' "
                "WHERE staged_pr_id = %(id)s",
                {"id": inserted[0]},
            )
        connection.rollback()
        with pytest.raises(psycopg.Error):
            connection.execute(
                "DELETE FROM cortex_hosted.review_staged_prs WHERE staged_pr_id = %(id)s",
                {"id": inserted[0]},
            )
    finally:
        connection.rollback()
        connection.close()
