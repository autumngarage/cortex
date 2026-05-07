"""Integration tests for `cortex sync` (Layer 1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import cortex
from cortex.cli import cli
from cortex.commands.init import init_command


# ---------------------------------------------------------------------------
# Fixture project — uses `cortex init` so doctor checks pass cleanly.
# ---------------------------------------------------------------------------


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    """Project with a fresh `cortex init` scaffold and no user content."""
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


# ---------------------------------------------------------------------------
# Layer 1 — `cortex sync`
# ---------------------------------------------------------------------------


def test_sync_runs_refresh_state_and_index_and_doctor(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    # state.md was regenerated (Generator field updated to current CLI version).
    state_text = (project / ".cortex" / "state.md").read_text()
    assert f"Generator: cortex refresh-state v{cortex.__version__}" in state_text
    # The promotion index was rebuilt.
    assert (project / ".cortex" / ".index.json").exists()
    # All four steps appear in the output.
    assert "refresh-state" in result.output
    assert "refresh-index" in result.output
    assert "config.toml" in result.output
    assert "doctor" in result.output
    assert "Sync complete" in result.output


def test_sync_no_doctor_flag_skips_doctor(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "refresh-state" in result.output
    assert "cortex doctor" not in result.output


def test_sync_dry_run_invokes_nothing(scaffolded_project: Path) -> None:
    project = scaffolded_project
    state_before = (project / ".cortex" / "state.md").read_text()
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--dry-run"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
    # Nothing was written by sync itself.
    assert (project / ".cortex" / "state.md").read_text() == state_before


def test_sync_rebuilds_retrieve_index_when_present(scaffolded_project: Path) -> None:
    project = scaffolded_project
    # Mark the project as having opted into retrieve.
    (project / ".cortex" / ".index").mkdir(exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    # Output prints the `--retrieve` modifier when an .index/ dir exists.
    assert "--retrieve" in result.output, result.output


def test_sync_is_idempotent(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    first = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert first.exit_code == 0, first.output
    state_after_first = (project / ".cortex" / "state.md").read_text()

    second = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert second.exit_code == 0, second.output
    assert (project / ".cortex" / "state.md").read_text() == state_after_first


def test_sync_reports_unknown_config_keys(scaffolded_project: Path) -> None:
    project = scaffolded_project
    (project / ".cortex" / "config.toml").write_text(
        "[refresh-index]\n"
        "candidate_patterns = []\n"
        "totally_unknown_key = 42\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "1 unknown key" in result.output
