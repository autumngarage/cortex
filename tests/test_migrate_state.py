"""Tests for `cortex migrate-state`."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner, Result

from cortex.cli import cli
from cortex.state_render import HAND_CLOSE, HAND_OPEN

FIXTURE = Path(__file__).parent / "fixtures" / "autumn-garage-pre-v0.4-state.md"


def _write_project(project: Path) -> None:
    cortex = project / ".cortex"
    for rel in ("plans", "journal", "doctrine", "templates/journal", "templates/plans"):
        (cortex / rel).mkdir(parents=True, exist_ok=True)
    (cortex / "SPEC_VERSION").write_text("0.5.0\n")
    (cortex / "state.md").write_text(FIXTURE.read_text())
    (project / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n')
    (cortex / "doctrine" / "0001-test.md").write_text("# Doctrine\n")
    (cortex / "templates" / "journal" / "decision.md").write_text("# Template\n")
    (cortex / "plans" / "active.md").write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-04-01\n"
        "Author: human\n"
        "Goal-hash: activehash\n"
        "Updated-by:\n"
        "  - 2026-04-27T00:00 human\n"
        "Cites: doctrine/0001\n"
        "---\n\n"
        "# Active Plan\n\n"
        "## Why (grounding)\ndoctrine/0001.\n\n"
        "## Success Criteria\npytest passes.\n\n"
        "## Approach\nDo it.\n\n"
        "## Work items\n- [ ] item 1\n"
    )
    (cortex / "journal" / "2026-04-28-release.md").write_text(
        "# Release\n\n**Date:** 2026-04-28\n**Type:** release\n\nBody.\n"
    )


def _invoke(project: Path, *args: str) -> Result:
    return CliRunner().invoke(
        cli,
        ["migrate-state", "--path", str(project), *args],
        env={"CORTEX_DETERMINISTIC": "1"},
    )


def test_migrate_state_preserves_autumn_garage_content_and_round_trips(tmp_path: Path) -> None:
    _write_project(tmp_path)
    original = (tmp_path / ".cortex" / "state.md").read_text()
    curated_line = "Coordination repo for the Touchstone/Cortex/Sentinel/**Conductor** quartet"

    result = _invoke(tmp_path, "--yes")
    assert result.exit_code == 0, result.output
    assert "--- .cortex/state.md (before)" in result.output
    assert "+++ .cortex/state.md (after)" in result.output

    migrated = (tmp_path / ".cortex" / "state.md").read_text()
    assert HAND_OPEN in migrated
    assert HAND_CLOSE in migrated
    assert curated_line in migrated
    assert original.split("# Project State — Autumn Garage", 1)[1] in migrated
    assert (tmp_path / ".cortex" / ".index.json").exists()

    second = CliRunner().invoke(
        cli,
        ["refresh-state", "--path", str(tmp_path)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert second.exit_code == 0, second.output
    assert (tmp_path / ".cortex" / "state.md").read_text() == migrated


def test_migrate_state_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_project(tmp_path)
    before = (tmp_path / ".cortex" / "state.md").read_text()

    result = _invoke(tmp_path, "--dry-run")
    assert result.exit_code == 0, result.output
    assert "dry-run: no files written." in result.output
    assert (tmp_path / ".cortex" / "state.md").read_text() == before
    assert not (tmp_path / ".cortex" / ".index.json").exists()
