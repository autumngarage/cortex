"""Tests for `cortex doctor --production`."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command


def _init_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def test_production_doctor_passes_on_fresh_scaffold(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--production", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "cortex doctor --production" in result.output


def test_production_doctor_json_has_stable_codes(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    (project / ".cortex" / "SPEC_VERSION").unlink()
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor", "--production", "--json", "--path", str(project)],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "production"
    assert payload["errors"] >= 1
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics
    assert "code" in diagnostics[0]
    assert "severity" in diagnostics[0]


def test_usage_command_reports_empty_counters(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["usage", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "grep=0" in result.output
