"""Tests for `cortex doctor`'s Autumn Garage sibling detection block.

Contract (per Doctrine 0002):
- Detection is CLI-shell-out + filesystem-presence only; no imports from
  touchstone or sentinel packages.
- Absence is normal — the block never escalates exit code or severity.
- `<tool> version` shell-out is bounded by a 3-second timeout and parses
  the first semver (``\\d+\\.\\d+\\.\\d+``) from the combined stdout/stderr.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex import siblings as siblings_module
from cortex.cli import cli
from cortex.commands.init import init_command


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _run_doctor(project: Path) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--path", str(project)])
    stderr = getattr(result, "stderr", "") or ""
    return result.exit_code, result.output + stderr


def test_siblings_block_rendered_when_both_absent(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With neither sibling on PATH, doctor still succeeds and prints
    the sibling block with informational 'not installed' lines."""
    monkeypatch.setattr("cortex.siblings.shutil.which", lambda _name: None)
    exit_code, output = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "Autumn Garage siblings:" in output
    assert "touchstone not installed" in output
    assert "sentinel not installed" in output
    # Absence rendered with the em-dash glyph.
    assert "— touchstone" in output
    assert "— sentinel" in output


def test_siblings_block_rendered_when_both_installed(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With both siblings mocked as installed, doctor prints their
    parsed version numbers and the ✓ glyph."""
    fake_paths = {
        "touchstone": "/fake/bin/touchstone",
        "sentinel": "/fake/bin/sentinel",
    }
    version_outputs = {
        "/fake/bin/touchstone": "touchstone 1.1.0\n",
        "/fake/bin/sentinel": "sentinel version 0.2.0 (python)\n",
    }
    monkeypatch.setattr("cortex.siblings.shutil.which", lambda name: fake_paths.get(name))

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = version_outputs[cmd[0]]
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=stdout, stderr=""
        )

    monkeypatch.setattr("cortex.siblings.subprocess.run", fake_run)

    exit_code, output = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "✓ touchstone 1.1.0 (installed)" in output
    assert "✓ sentinel 0.2.0 (installed)" in output


def test_project_marker_present_reported(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a project-local marker file exists, the block says 'present'."""
    (scaffolded_project / ".touchstone-config").write_text("profile=default\n")
    (scaffolded_project / ".sentinel").mkdir()
    (scaffolded_project / ".sentinel" / "config.toml").write_text("# sentinel\n")
    monkeypatch.setattr("cortex.siblings.shutil.which", lambda _name: None)

    _exit_code, output = _run_doctor(scaffolded_project)
    assert ".touchstone-config present" in output
    assert ".sentinel/config.toml present" in output


def test_version_timeout_is_non_fatal(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hanging `<tool> version` invocation must not fail doctor; the
    sibling is reported as installed with 'version unknown'."""
    monkeypatch.setattr(
        "cortex.siblings.shutil.which",
        lambda name: "/fake/bin/" + name if name == "touchstone" else None,
    )

    def fake_run(cmd: list[str], timeout: float | None = None, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 0.0)

    monkeypatch.setattr("cortex.siblings.subprocess.run", fake_run)

    exit_code, output = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "! touchstone" in output
    assert "version unknown" in output
    assert "timed out" in output


def test_version_parsed_from_stderr(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some CLIs (older bash-based) print the banner to stderr. We
    parse the semver from combined output so those still surface."""
    monkeypatch.setattr(
        "cortex.siblings.shutil.which",
        lambda name: "/fake/bin/touchstone" if name == "touchstone" else None,
    )

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="", stderr="touchstone 1.1.0\n"
        )

    monkeypatch.setattr("cortex.siblings.subprocess.run", fake_run)

    _exit_code, output = _run_doctor(scaffolded_project)
    assert "✓ touchstone 1.1.0 (installed)" in output


def test_detect_siblings_walks_up_to_git_root(tmp_path: Path) -> None:
    """Project marker discovery walks up from cwd to the git root."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    (root / ".touchstone-config").write_text("profile=default\n")
    nested = root / "subdir" / "deeper"
    nested.mkdir(parents=True)

    # From `nested` looking for `.touchstone-config` at `root`, the
    # walk-up must find it.
    statuses = siblings_module.detect_siblings(nested)
    by_name = {s.name: s for s in statuses}
    assert by_name["touchstone"].project_marker_present is True
    assert by_name["sentinel"].project_marker_present is False


def test_detect_siblings_stops_at_git_root(tmp_path: Path) -> None:
    """A marker above the git root must NOT be picked up — the walk
    bounds at the git root to avoid false positives from the user's
    home directory."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / ".touchstone-config").write_text("stray=yes\n")
    inner = outer / "project"
    inner.mkdir()
    (inner / ".git").mkdir()

    statuses = siblings_module.detect_siblings(inner)
    by_name = {s.name: s for s in statuses}
    assert by_name["touchstone"].project_marker_present is False
