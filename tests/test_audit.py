"""Tests for `cortex doctor --audit` and `--audit-digests`.

Each audit test builds a real git repo in tmp_path so the command exercises
actual ``git log`` output instead of mocked diffs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.audit import Trigger, audit, audit_digests, classify, load_commits
from cortex.cli import cli
from cortex.commands.init import init_command


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(path: Path) -> None:
    _run(path, "init", "-b", "main")
    _run(path, "config", "user.email", "t@example.com")
    _run(path, "config", "user.name", "Test")
    _run(path, "commit", "--allow-empty", "-m", "initial")


def _commit(path: Path, subject: str, files: dict[str, str]) -> None:
    for rel, body in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    _run(path, "add", "-A")
    _run(path, "commit", "-m", subject)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    _git_init(tmp_path)
    _run(tmp_path, "add", "-A")
    _run(tmp_path, "commit", "-m", "initial cortex scaffold")
    return tmp_path


def test_t1_1_fires_on_doctrine_touch(git_project: Path) -> None:
    _commit(git_project, "docs: add doctrine", {".cortex/doctrine/0001-why.md": "# 0001 — Why\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"})
    commits = load_commits(git_project, since_days=30)
    assert commits, "commits must be loadable"
    hit = next(c for c in commits if "add doctrine" in c.subject)
    assert Trigger.T1_1 in classify(hit)


def test_t1_5_fires_on_dep_manifest_change(git_project: Path) -> None:
    _commit(git_project, "chore: bump deps", {"pyproject.toml": "[project]\nname = 'x'\n"})
    commits = load_commits(git_project, since_days=30)
    hit = next(c for c in commits if "bump deps" in c.subject)
    assert Trigger.T1_5 in classify(hit)


def test_t1_8_fires_on_regression_fix(git_project: Path) -> None:
    _commit(git_project, "fix: prevent regression in retry backoff", {"notes.md": "x"})
    commits = load_commits(git_project, since_days=30)
    hit = next(c for c in commits if "regression" in c.subject)
    assert Trigger.T1_8 in classify(hit)


def test_audit_matches_when_journal_has_matching_type(git_project: Path) -> None:
    # Doctrine touch + same-day journal entry of Type: decision.
    _commit(
        git_project,
        "feat: add doctrine entry",
        {".cortex/doctrine/0001-why.md": "# 0001\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"},
    )
    commits = load_commits(git_project, since_days=30)
    date = commits[0].date.date().isoformat()
    (git_project / ".cortex" / "journal" / f"{date}-add-doctrine.md").write_text(
        f"# Add doctrine\n\n**Date:** {date}\n**Type:** decision\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_1_fires = [f for f in report.fires if f.trigger == Trigger.T1_1]
    assert t1_1_fires
    assert all(f.matched for f in t1_1_fires)


def test_audit_warns_when_no_matching_journal(git_project: Path) -> None:
    _commit(
        git_project,
        "fix: regression in pipeline",
        {"src/pipeline.py": "pass\n"},
    )
    report = audit(git_project, since_days=30)
    unmatched = [f for f in report.fires if not f.matched]
    assert any(f.trigger == Trigger.T1_8 for f in unmatched)


def test_audit_digests_flags_missing_citations(git_project: Path) -> None:
    date = "2026-04-17"
    (git_project / ".cortex" / "journal" / f"{date}-march-digest.md").write_text(
        f"# March digest\n\n**Date:** {date}\n**Type:** digest\n\n"
        "- first claim with no citation\n"
        "- second claim, also no citation\n"
        "- third claim, again no citation\n"
    )
    warnings = audit_digests(git_project)
    assert any("march-digest" in w for w in warnings)


def test_audit_digests_clean_when_citations_present(git_project: Path) -> None:
    date = "2026-04-17"
    (git_project / ".cortex" / "journal" / f"{date}-clean-digest.md").write_text(
        f"# Clean digest\n\n**Date:** {date}\n**Type:** digest\n\n"
        "- first — see `journal/2026-04-01-foo.md`\n"
        "- second per journal/2026-04-02-bar.md\n"
        "- third, journal/2026-04-03-baz.md\n"
    )
    warnings = audit_digests(git_project)
    assert not any("clean-digest" in w for w in warnings)


def test_cli_audit_flag_runs_without_error(git_project: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--audit", "--path", str(git_project), "--since-days", "30"])
    # Clean scaffold still passes the structural check; audit warnings on
    # stderr don't change exit code.
    assert result.exit_code == 0, result.output + (getattr(result, "stderr", "") or "")
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "--audit" in combined


def test_cli_audit_digests_flag(git_project: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--audit-digests", "--path", str(git_project)])
    assert result.exit_code == 0, result.output
    assert "--audit-digests" in result.output
