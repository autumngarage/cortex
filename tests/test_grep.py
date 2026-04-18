"""Tests for `cortex grep` — monkeypatches `subprocess.run` so tests don't
require ripgrep on PATH."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands import grep as grep_module
from cortex.commands.init import init_command


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _fake_rg(stdout: str, returncode: int = 0, stderr: str = "") -> object:
    class FakeCompleted:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    return FakeCompleted()


def _install_fake_rg(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> list[list[str]]:
    monkeypatch.setattr(grep_module, "_find_rg", lambda: "/usr/bin/rg")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> object:
        calls.append(cmd)
        return _fake_rg(stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_missing_cortex_dir_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "pattern", "--path", str(tmp_path)])
    assert result.exit_code == 2


def test_missing_ripgrep_errors(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grep_module, "_find_rg", lambda: None)
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "pattern", "--path", str(scaffolded_project)])
    assert result.exit_code == 3
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "ripgrep" in combined


def test_matches_annotated_with_frontmatter(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = scaffolded_project / ".cortex" / "doctrine" / "0001-why.md"
    entry.write_text(
        "# 0001 — Why\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-17\n"
        "**Load-priority:** always\n\n"
        "## Context\nhello world\n"
    )
    fake_stdout = f"{entry}:5:hello world\n"
    _install_fake_rg(monkeypatch, fake_stdout)

    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "hello", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert "Status: Accepted" in result.output
    assert "Load-priority: always" in result.output
    assert "hello world" in result.output


def test_no_matches_message(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rg(monkeypatch, "", returncode=1)
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "missing", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert "no matches" in result.output


def test_layer_restricts_search_root(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_rg(monkeypatch, "")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "x", "--layer", "journal", "--path", str(scaffolded_project)],
    )
    assert result.exit_code == 0
    assert calls
    invoked = calls[0]
    assert invoked[-1].endswith(".cortex/journal")


def test_rg_error_returncode_propagates(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rg(monkeypatch, "", returncode=2, stderr="rg: bad pattern\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "[", "--path", str(scaffolded_project)])
    assert result.exit_code == 2
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "bad pattern" in combined
