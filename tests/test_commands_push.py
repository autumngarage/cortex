"""Tests for the `cortex push` CLI surface (cortex#513).

The command's visible modes mirror `cortex ask`: no DATABASE_URL degrades
visibly with a non-zero exit, hosted-connection failures surface the
`cortex.hosted.db` policy error, and the per-stage arithmetic (events,
provenance, projections, snapshot) prints on every successful run. The
hosted side is the transactional `FakeHostedDb` from
`tests.test_hosted_push`; live-Postgres coverage stays env-gated in
`tests/test_hosted_push_integration.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands import push as push_module
from cortex.commands.confirm import CandidateRow, build_decision_event
from cortex.commands.push import HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE
from cortex.hosted.derive_store import DeriveEventStore, derive_store_path
from cortex.hosted.ledger_events import LedgerEventType
from tests.test_hosted_push import RULES_MARKDOWN, FakeHostedDb

DSN = "postgresql://cortex:secret@db.example.test:5432/cortex?sslmode=require"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cortex").mkdir(parents=True)
    (root / "CLAUDE.md").write_text(RULES_MARKDOWN, encoding="utf-8")
    return root


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> FakeHostedDb:
    db = FakeHostedDb()
    monkeypatch.setattr(push_module, "connect", lambda dsn: db)
    return db


def _combined_output(result: object) -> str:
    output = getattr(result, "output", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return output + stderr


def _derive(root: Path) -> None:
    result = CliRunner().invoke(cli, ["derive", "--path", str(root)])
    assert result.exit_code == 0, _combined_output(result)


def test_push_degrades_visibly_without_database_url(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = CliRunner().invoke(cli, ["push", "--path", str(project)])
    assert result.exit_code == 2
    combined = _combined_output(result)
    assert HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE in combined
    assert "degraded_capability" in combined


def test_push_requires_a_derive_store(project: Path) -> None:
    result = CliRunner().invoke(
        cli, ["push", "--database-url", DSN, "--path", str(project)]
    )
    assert result.exit_code == 1
    assert "run `cortex derive` first" in _combined_output(result)


def test_push_reports_an_empty_store_without_touching_the_ledger(project: Path) -> None:
    DeriveEventStore(derive_store_path(project)).close()
    result = CliRunner().invoke(
        cli, ["push", "--database-url", DSN, "--path", str(project)]
    )
    assert result.exit_code == 0
    assert "no events in the local derive store" in result.output


def test_push_surfaces_connection_policy_errors_visibly(project: Path) -> None:
    _derive(project)
    result = CliRunner().invoke(
        cli, ["push", "--database-url", "mysql://nope/db", "--path", str(project)]
    )
    assert result.exit_code == 1
    assert "not supported" in _combined_output(result)


def test_push_prints_per_stage_arithmetic_and_is_idempotent(
    project: Path, fake_db: FakeHostedDb
) -> None:
    _derive(project)
    runner = CliRunner()

    first = runner.invoke(cli, ["push", "--database-url", DSN, "--path", str(project)])
    assert first.exit_code == 0, _combined_output(first)
    assert "push: " in first.output
    assert "events: " in first.output
    assert "0 replayed" in first.output
    assert "0 failed" in first.output
    assert "provenance: 1 document(s)" in first.output
    assert "projections: " in first.output
    assert "candidate node(s)" in first.output
    assert "snapshot: " in first.output
    assert "registered" in first.output
    assert "projection.rebuilt appended" in first.output
    assert fake_db.state.nodes, "first push must project candidate nodes"

    second = runner.invoke(cli, ["push", "--database-url", DSN, "--path", str(project)])
    assert second.exit_code == 0, _combined_output(second)
    assert "0 appended" in second.output
    assert "0 failed" in second.output
    assert "already registered" in second.output
    assert "projection.rebuilt replayed" in second.output
    assert len(fake_db.state.snapshots) == 1


def test_push_uses_database_url_from_the_environment(
    project: Path, fake_db: FakeHostedDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    _derive(project)
    monkeypatch.setenv("DATABASE_URL", DSN)
    result = CliRunner().invoke(cli, ["push", "--path", str(project)])
    assert result.exit_code == 0, _combined_output(result)
    assert fake_db.state.nodes


def test_push_names_drifted_files_in_visible_skips(
    project: Path, fake_db: FakeHostedDb
) -> None:
    _derive(project)
    (project / "CLAUDE.md").write_text("# Rewritten after derive\n", encoding="utf-8")
    result = CliRunner().invoke(
        cli, ["push", "--database-url", DSN, "--path", str(project)]
    )
    assert result.exit_code == 0, _combined_output(result)
    assert "skipped: " in result.output
    assert "CLAUDE.md" in result.output
    assert "content drift" in result.output
    assert "candidate excluded" in result.output
    assert fake_db.state.nodes == {}


def test_push_exits_nonzero_when_events_fail(
    project: Path, fake_db: FakeHostedDb
) -> None:
    orphan = CandidateRow(
        event_hash="ef" * 32,
        tenant_id="50fcc4ec-3d35-4b53-a7f7-0e153680eaae",
        source_id="9a07a1e6-25a8-4c5a-9b3c-1f1d54a0b111",
        idempotency_key="orphan-candidate",
        external_id="CLAUDE.md",
        span_hashes=("ab" * 32,),
        payload={"decision_text": "orphaned decision"},
    )
    confirm_event = build_decision_event(
        orphan,
        event_type=LedgerEventType.DECISION_CONFIRMED,
        actor_id="reviewer",
        occurred_at=datetime.now(tz=UTC),
    )
    with DeriveEventStore(derive_store_path(project)) as store:
        store.append_events([confirm_event])

    result = CliRunner().invoke(
        cli, ["push", "--database-url", DSN, "--path", str(project)]
    )
    assert result.exit_code == 1
    combined = _combined_output(result)
    assert "failed: " in combined
    assert confirm_event.idempotency_key[:12] in combined
    assert fake_db.state.nodes == {}
