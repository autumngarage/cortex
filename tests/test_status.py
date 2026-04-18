"""Tests for `cortex status`, bare `cortex`, and `cortex promote`."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.goal_hash import normalize_goal_hash
from cortex.status import compute_status


@pytest.fixture
def scaffolded(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write_active_plan(project: Path, title: str) -> Path:
    path = project / ".cortex" / "plans" / f"{title.lower().replace(' ', '-')}.md"
    path.write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-04-18\n"
        "Author: human\n"
        f"Goal-hash: {normalize_goal_hash(title)}\n"
        "Updated-by:\n  - 2026-04-18T10:00 human\n"
        "Cites: doctrine/0001\n"
        "---\n\n"
        f"# {title}\n\n## Why (grounding)\ndoctrine/0001\n\n"
        "## Success Criteria\n`tests/` pass.\n\n## Approach\n.\n\n## Work items\n- [ ] x\n"
    )
    return path


def _write_digest(project: Path, date: str) -> Path:
    path = project / ".cortex" / "journal" / f"{date}-digest.md"
    path.write_text(
        f"# {date} digest\n\n**Date:** {date}\n**Type:** digest\n\n- x per journal/y\n"
    )
    return path


def test_status_reports_scaffold_metadata(scaffolded: Path) -> None:
    status = compute_status(scaffolded)
    assert status.spec_version is not None
    assert status.protocol_version is not None
    assert status.active_plans == []
    assert status.promotion_index_present is False


def test_status_finds_active_plan(scaffolded: Path) -> None:
    _write_active_plan(scaffolded, "Plan Alpha")
    status = compute_status(scaffolded)
    assert len(status.active_plans) == 1
    assert status.active_plans[0].title == "Plan Alpha"


def test_status_digest_overdue_flag(scaffolded: Path) -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    date = (now - timedelta(days=60)).date().isoformat()
    _write_digest(scaffolded, date)
    status = compute_status(scaffolded, now=now)
    assert status.latest_digest_path is not None
    assert status.latest_digest_age_days == 60
    assert status.digest_overdue is True


def test_status_digest_fresh(scaffolded: Path) -> None:
    now = datetime(2026, 6, 30, tzinfo=UTC)
    date = (now - timedelta(days=10)).date().isoformat()
    _write_digest(scaffolded, date)
    status = compute_status(scaffolded, now=now)
    assert status.digest_overdue is False


def test_status_reads_promotion_queue(scaffolded: Path) -> None:
    (scaffolded / ".cortex" / ".index.json").write_text(
        json.dumps(
            {
                "promotion_queue": [
                    {"id": "a", "state": "proposed"},
                    {"id": "b", "state": "stale-proposed"},
                    {"id": "c", "state": "approved"},
                ]
            }
        )
    )
    status = compute_status(scaffolded)
    assert status.promotion_index_present is True
    assert status.promotion_proposed == 1
    assert status.promotion_stale == 1


def test_cli_bare_runs_status(scaffolded: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(scaffolded)
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0, result.output
    assert "Project:" in result.output
    assert "Active plans" in result.output


def test_cli_status_subcommand_json(scaffolded: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(scaffolded), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["spec_version"]
    assert data["promotion_queue"]["index_present"] is False


def test_cli_status_missing_cortex(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(tmp_path)])
    assert result.exit_code == 2


def test_promote_without_index_errors(scaffolded: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["promote", "j-abc", "--path", str(scaffolded)])
    assert result.exit_code == 2
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert ".index.json" in combined


def test_promote_unknown_candidate_errors(scaffolded: Path) -> None:
    (scaffolded / ".cortex" / ".index.json").write_text(
        '{"promotion_queue": [{"id": "x", "state": "proposed"}]}'
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["promote", "j-missing", "--path", str(scaffolded)])
    assert result.exit_code == 2
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "j-missing" in combined


def test_promote_known_candidate_is_stub(scaffolded: Path) -> None:
    (scaffolded / ".cortex" / ".index.json").write_text(
        '{"promotion_queue": [{"id": "j-abc", "state": "proposed"}]}'
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["promote", "j-abc", "--path", str(scaffolded)])
    assert result.exit_code == 3
    assert "not yet implemented" in result.output
