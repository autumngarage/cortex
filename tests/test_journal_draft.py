"""Tests for `cortex journal draft <type>`.

Each test scaffolds a real `.cortex/` via ``cortex init`` and exercises
the draft command against the real templates, real filesystem, and a real
``git init``-d temp repo. No mocked subprocess; tests run inside an
environment that may or may not have ``gh`` installed and either path
must work.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.commands.journal import _normalize_slug


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    _run(tmp_path, "init", "-b", "main")
    _run(tmp_path, "config", "user.email", "t@example.com")
    _run(tmp_path, "config", "user.name", "Test")
    _run(tmp_path, "add", "-A")
    _run(tmp_path, "commit", "-m", "initial cortex scaffold")
    return tmp_path


def _draft(project: Path, *args: str) -> "subprocess.CompletedProcess[str] | object":
    runner = CliRunner()
    return runner.invoke(
        cli, ["journal", "draft", *args, "--path", str(project), "--no-edit"]
    )


def test_draft_decision_writes_file_with_today(git_project: Path) -> None:
    result = _draft(git_project, "decision")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    written = git_project / ".cortex" / "journal"
    files = list(written.glob(f"{today}-*.md"))
    assert len(files) == 1, [p.name for p in files]
    body = files[0].read_text()
    assert f"**Date:** {today}" in body
    assert "**Type:** decision" in body
    # The auto-context block is present.
    assert "Context auto-pulled at draft time" in body


def test_draft_release_uses_release_template(git_project: Path) -> None:
    result = _draft(git_project, "release")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-release-*.md"))
    assert files, "release draft should land under a release-*.md filename"
    body = files[0].read_text()
    assert "**Type:** release" in body
    assert "**Trigger:** T1.10" in body


def test_draft_title_replaces_h1(git_project: Path) -> None:
    result = _draft(git_project, "decision", "--title", "Pin retry backoff to 5s")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-*.md"))
    body = files[0].read_text()
    assert body.startswith("# Pin retry backoff to 5s")


def test_draft_title_drives_slug(git_project: Path) -> None:
    result = _draft(git_project, "decision", "--title", "Pin Retry Backoff to 5s")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-pin-retry-backoff-to-5s.md"
    assert target.exists(), list((git_project / ".cortex" / "journal").iterdir())


def test_draft_slug_override_wins(git_project: Path) -> None:
    result = _draft(
        git_project, "decision", "--title", "anything", "--slug", "custom-slug"
    )
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-custom-slug.md"
    assert target.exists()


def test_draft_unknown_type_lists_known(git_project: Path) -> None:
    result = _draft(git_project, "this-type-does-not-exist")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "no template" in combined
    assert "Available types" in combined
    assert "decision" in combined
    assert "release" in combined


def test_draft_refuses_overwrite(git_project: Path) -> None:
    a = _draft(git_project, "decision", "--slug", "same")
    assert a.exit_code == 0, a.output
    b = _draft(git_project, "decision", "--slug", "same")
    assert b.exit_code == 2, b.output
    combined = b.output + (getattr(b, "stderr", "") or "")
    assert "already exists" in combined


def test_draft_outside_cortex_project_errors(tmp_path: Path) -> None:
    # No `cortex init` run — `.cortex/` is absent.
    result = _draft(tmp_path, "decision")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "does not exist" in combined


def test_draft_default_slug_uses_type_and_time(git_project: Path) -> None:
    # No --title and no --slug → fallback slug starts with the type.
    result = _draft(git_project, "decision")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-decision-*.md"))
    assert files, list((git_project / ".cortex" / "journal").iterdir())


def test_normalize_slug_handles_unicode_and_punctuation() -> None:
    assert _normalize_slug("Pin retry backoff to 5s") == "pin-retry-backoff-to-5s"
    assert _normalize_slug("Café — résumé") == "cafe-resume"
    assert _normalize_slug("!!!") == "untitled"
    assert _normalize_slug("a" * 100) == "a" * 50


def test_project_template_override_wins(git_project: Path) -> None:
    # Drop a custom decision.md template under the project; draft should use it.
    custom = git_project / ".cortex" / "templates" / "journal" / "decision.md"
    custom.write_text(
        "# {{ Custom title placeholder }}\n\n"
        "**Date:** {{ YYYY-MM-DD }}\n"
        "**Type:** decision\n"
        "**ProjectMarker:** unique-project-string\n\n"
        "> body\n"
    )
    result = _draft(git_project, "decision", "--slug", "custom-test")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    body = (
        git_project / ".cortex" / "journal" / f"{today}-custom-test.md"
    ).read_text()
    assert "**ProjectMarker:** unique-project-string" in body
