"""Tests for checkout vs state.md snapshot integrity."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.commands.init import init_command
from cortex.commands.manifest import manifest_command
from cortex.commands.status import status_command
from cortex.doctor_checks import (
    MAP_LAYER_REMEDIATION,
    check_generated_layers,
    check_snapshot_integrity,
)
from cortex.snapshot_integrity import assess_snapshot_integrity, read_recorded_head_sha


def _run(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(path: Path) -> None:
    _run(path, "init", "-b", "main")
    _run(path, "config", "user.email", "t@example.com")
    _run(path, "config", "user.name", "Test")


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    _git_init(tmp_path)
    _run(tmp_path, "add", "-A")
    _run(tmp_path, "commit", "-m", "initial cortex scaffold")
    return tmp_path


def _write_state_with_head(project: Path, head_sha: str) -> None:
    state_path = project / ".cortex" / "state.md"
    state_path.write_text(
        f"""---
Generated: 2026-06-01T00:00:00+00:00
Generator: cortex refresh-state v1.6.4
Sources:
  - HEAD sha: {head_sha}
Sources-hash:
  .cortex/plans/example.md: abc
Corpus: 1 files
Omitted: []
Incomplete: []
Conflicts-preserved: []
Spec: 1.1.0
---

# Project State

## Active plans

- none
"""
    )


def test_read_recorded_head_sha_from_state_sources() -> None:
    text = """---
Generated: 2026-05-25T00:00:00+00:00
Sources:
  - HEAD sha: abcdef0123456789
---
# Project State
"""
    assert read_recorded_head_sha(text) == "abcdef0123456789"


def test_snapshot_integrity_warns_on_head_mismatch(git_project: Path) -> None:
    _write_state_with_head(
        git_project,
        "deadbeef00000000000000000000000000000000",
    )
    _run(git_project, "checkout", "-b", "feat/wip-branch")
    (git_project / "wip.txt").write_text("wip\n")
    _run(git_project, "add", "wip.txt")
    _run(git_project, "commit", "-m", "wip: branch-only work")

    report = assess_snapshot_integrity(git_project)
    assert report.warnings
    assert any("generated against HEAD" in warning for warning in report.warnings)
    assert any("ahead of" in warning and "WIP" in warning for warning in report.warnings)


def test_status_surfaces_snapshot_warning(git_project: Path) -> None:
    _write_state_with_head(
        git_project,
        "deadbeef00000000000000000000000000000000",
    )
    _run(git_project, "checkout", "-b", "feat/status-warning")
    (git_project / "note.txt").write_text("note\n")
    _run(git_project, "add", "note.txt")
    _run(git_project, "commit", "-m", "docs: branch note")

    runner = CliRunner()
    result = runner.invoke(status_command, ["--path", str(git_project)])
    assert result.exit_code == 0, result.output
    assert "Snapshot:" in result.output
    assert "generated against HEAD" in result.output


def test_manifest_includes_snapshot_warning(git_project: Path) -> None:
    _write_state_with_head(
        git_project,
        "deadbeef00000000000000000000000000000000000000",
    )
    _run(git_project, "checkout", "-b", "feat/manifest-warning")
    (git_project / "note2.txt").write_text("note\n")
    _run(git_project, "add", "note2.txt")
    _run(git_project, "commit", "-m", "docs: more branch work")

    runner = CliRunner()
    result = runner.invoke(
        manifest_command,
        ["--path", str(git_project), "--budget", "4000"],
    )
    assert result.exit_code == 0, result.output
    assert "**Snapshot warning:**" in result.output


def test_doctor_snapshot_integrity_check(git_project: Path) -> None:
    _write_state_with_head(
        git_project,
        "deadbeef00000000000000000000000000000000000000",
    )
    issues = check_snapshot_integrity(git_project)
    assert issues
    assert "generated against HEAD" in issues[0].message


def test_snapshot_integrity_warns_when_state_unreadable(git_project: Path) -> None:
    state_path = git_project / ".cortex" / "state.md"
    os.chmod(state_path, 0)
    try:
        report = assess_snapshot_integrity(git_project)
    finally:
        os.chmod(state_path, 0o600)

    assert any("could not read state.md" in warning for warning in report.warnings)


def test_map_staleness_points_to_hand_maintenance(git_project: Path) -> None:
    map_path = git_project / ".cortex" / "map.md"
    text = map_path.read_text()
    map_path.write_text(
        re.sub(
            r"^Generated: .+$",
            "Generated: 2000-01-01T00:00:00+00:00",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    )
    issues = check_generated_layers(git_project)
    map_issues = [issue for issue in issues if issue.path.endswith("map.md")]
    assert map_issues
    assert MAP_LAYER_REMEDIATION in map_issues[0].message
