"""Tests for the `cortex ask` CLI surface (cortex#381 + #382).

The hosted SQL path is non-executing locally by design; these tests cover
the visible degraded modes, the no-browsable-index query guard at the CLI
boundary, and the identity defaults shared with `cortex derive`. Live-DB
integration stays env-gated on DATABASE_URL elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands import ask as ask_module
from cortex.commands.ask import (
    HOSTED_EXTRA_MISSING_MESSAGE,
    HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE,
    HostedAskError,
    latest_graph_snapshot_sql,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cortex").mkdir(parents=True)
    return root


def _combined_output(result: object) -> str:
    output = getattr(result, "output", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return output + stderr


def test_ask_requires_a_question_argument(project: Path) -> None:
    """No browse affordance: invoking without a question is a usage error."""

    result = CliRunner().invoke(cli, ["ask", "--path", str(project)])
    assert result.exit_code == 2
    combined = _combined_output(result)
    assert "QUESTION" in combined or "Missing argument" in combined


def test_ask_rejects_empty_question_visibly(project: Path) -> None:
    result = CliRunner().invoke(cli, ["ask", "   ", "--path", str(project)])
    assert result.exit_code == 2
    assert "a question is required" in _combined_output(result)


def test_ask_rejects_browse_shaped_question_visibly(project: Path) -> None:
    result = CliRunner().invoke(cli, ["ask", "list all decisions", "--path", str(project)])
    assert result.exit_code == 2
    combined = _combined_output(result)
    assert "cortex#382" in combined
    assert "browsable index" in combined


def test_ask_degrades_visibly_without_database_url(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = CliRunner().invoke(
        cli, ["ask", "what did we decide about retries?", "--path", str(project)]
    )
    assert result.exit_code == 2
    combined = _combined_output(result)
    assert HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE in combined
    assert "degraded_capability" in combined


def test_ask_names_missing_hosted_extra_visibly(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.test/cortex")
    monkeypatch.setattr(ask_module, "hosted_extra_installed", lambda: False)
    result = CliRunner().invoke(
        cli, ["ask", "what did we decide about retries?", "--path", str(project)]
    )
    assert result.exit_code == 2
    assert HOSTED_EXTRA_MISSING_MESSAGE in _combined_output(result)


def test_ask_rejects_malformed_tenant_id(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.test/cortex")
    monkeypatch.setattr(ask_module, "hosted_extra_installed", lambda: True)
    result = CliRunner().invoke(
        cli,
        [
            "ask",
            "what did we decide about retries?",
            "--tenant-id",
            "not-a-uuid",
            "--path",
            str(project),
        ],
    )
    assert result.exit_code == 2
    assert "not a UUID" in _combined_output(result)


def test_ask_surfaces_hosted_errors_visibly(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.test/cortex")
    monkeypatch.setattr(ask_module, "hosted_extra_installed", lambda: True)

    def _boom(**_: object) -> object:
        raise HostedAskError("no graph snapshot registered for tenant probe")

    monkeypatch.setattr(ask_module, "run_hosted_ask", _boom)
    result = CliRunner().invoke(
        cli, ["ask", "what did we decide about retries?", "--path", str(project)]
    )
    assert result.exit_code == 1
    assert "no graph snapshot registered" in _combined_output(result)


def test_latest_graph_snapshot_sql_is_tenant_scoped_and_capped() -> None:
    sql = latest_graph_snapshot_sql()
    assert "tenant_id = %(tenant_id)s" in sql
    assert "LIMIT 1" in sql
    assert "cortex_hosted.graph_snapshots" in sql


def test_latest_graph_snapshot_sql_rejects_bad_identifier() -> None:
    with pytest.raises(HostedAskError, match="invalid SQL identifier"):
        latest_graph_snapshot_sql("bad-schema; drop table")
