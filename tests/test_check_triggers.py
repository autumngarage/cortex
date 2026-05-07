"""Tests for `cortex check-triggers` (issue #195 step 1).

Each test builds a real git repo in tmp_path so the command exercises the
same git-diff plumbing the runtime hooks will hit. NDJSON parsing is done
by the test rather than by a helper to keep failures legible.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

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


def _delete(path: Path, subject: str, rel: str) -> None:
    target = path / rel
    target.unlink()
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


def _run_check_triggers(project: Path, *extra: str) -> tuple[int, str, str]:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["check-triggers", "--target-path", str(project), *extra],
    )
    stderr = getattr(result, "stderr", "") or ""
    return result.exit_code, result.output, stderr


def _parse_ndjson(stdout: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Trigger firing
# ---------------------------------------------------------------------------


def test_t1_1_fires_on_principles_touch(git_project: Path) -> None:
    _commit(
        git_project,
        "docs: tweak principles",
        {"principles/new-rule.md": "# new rule\n"},
    )
    code, stdout, stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0, stderr
    hits = _parse_ndjson(stdout)
    t1_1 = [h for h in hits if h["trigger"] == "T1.1"]
    assert t1_1, hits
    assert "principles/new-rule.md" in t1_1[0]["files"]
    assert t1_1[0]["template"] == "journal/decision.md"
    assert t1_1[0]["ref"] == "HEAD~1..HEAD"


def test_t1_4_fires_on_large_deletion(git_project: Path) -> None:
    big = "\n".join(f"line {i}" for i in range(150)) + "\n"
    _commit(git_project, "feat: add big file", {"src/big.py": big})
    _delete(git_project, "chore: drop big file", "src/big.py")

    code, stdout, stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0, stderr
    hits = _parse_ndjson(stdout)
    t1_4 = [h for h in hits if h["trigger"] == "T1.4"]
    assert t1_4, hits
    assert t1_4[0]["files"] == ["src/big.py"]
    assert t1_4[0]["lines_deleted"] == 150


def test_t1_4_does_not_fire_below_threshold(git_project: Path) -> None:
    small = "\n".join(f"line {i}" for i in range(50)) + "\n"
    _commit(git_project, "feat: add small file", {"src/small.py": small})
    _delete(git_project, "chore: drop small file", "src/small.py")

    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    hits = _parse_ndjson(stdout)
    assert not [h for h in hits if h["trigger"] == "T1.4"], hits


def test_t1_4_threshold_override_via_protocol(git_project: Path) -> None:
    protocol = git_project / ".cortex" / "protocol.md"
    text = protocol.read_text()
    protocol.write_text("T1.4.line-threshold: 30\n\n" + text)

    small = "\n".join(f"line {i}" for i in range(50)) + "\n"
    _commit(git_project, "feat: add file", {"src/x.py": small})
    _delete(git_project, "chore: drop file", "src/x.py")

    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    hits = _parse_ndjson(stdout)
    t1_4 = [h for h in hits if h["trigger"] == "T1.4"]
    assert t1_4, hits
    assert t1_4[0]["lines_deleted"] == 50


def test_t1_4_invalid_override_emits_stderr_warning(git_project: Path) -> None:
    protocol = git_project / ".cortex" / "protocol.md"
    text = protocol.read_text()
    protocol.write_text("T1.4.line-threshold: not-a-number\n\n" + text)

    code, _stdout, stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    assert "T1.4.line-threshold" in stderr
    assert "invalid" in stderr.lower()


def test_t1_5_fires_on_dep_manifest_change(git_project: Path) -> None:
    _commit(git_project, "chore: bump deps", {"pyproject.toml": "[project]\nname='x'\n"})

    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    hits = _parse_ndjson(stdout)
    t1_5 = [h for h in hits if h["trigger"] == "T1.5"]
    assert t1_5, hits
    assert "pyproject.toml" in t1_5[0]["files"]


def test_t1_8_fires_on_breaking_feat(git_project: Path) -> None:
    # Per Protocol § 2 the regex is `feat: ... (breaking|replaces)` —
    # literal `feat:` (no scope), matching the audit-side ``T1_8_RE``.
    _commit(
        git_project,
        "feat: breaking change to /users",
        {"src/api.py": "pass\n"},
    )
    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    hits = _parse_ndjson(stdout)
    t1_8 = [h for h in hits if h["trigger"] == "T1.8"]
    assert t1_8, hits
    assert t1_8[0]["subject"].startswith("feat: breaking")
    assert t1_8[0]["commit"]


def test_t1_8_fires_on_regression_fix(git_project: Path) -> None:
    _commit(
        git_project,
        "fix: prevent regression in retry backoff",
        {"src/retry.py": "pass\n"},
    )
    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    hits = _parse_ndjson(stdout)
    t1_8 = [h for h in hits if h["trigger"] == "T1.8"]
    assert t1_8, hits


def test_t1_8_does_not_fire_on_plain_feat(git_project: Path) -> None:
    _commit(
        git_project,
        "feat: add quiet helper",
        {"src/helper.py": "pass\n"},
    )
    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    hits = _parse_ndjson(stdout)
    assert not [h for h in hits if h["trigger"] == "T1.8"], hits


# ---------------------------------------------------------------------------
# No-fire silence
# ---------------------------------------------------------------------------


def test_no_triggers_emits_no_output(git_project: Path) -> None:
    _commit(git_project, "docs: tweak readme", {"NOTES.md": "hello\n"})
    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    assert stdout.strip() == ""
    # Stderr may carry a warning about a fenced-code mention but shouldn't
    # carry hits — there are none.
    assert "T1." not in stdout


# ---------------------------------------------------------------------------
# --staged mode
# ---------------------------------------------------------------------------


def test_staged_mode_picks_up_t1_1_changes(git_project: Path) -> None:
    target = git_project / "principles" / "new.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# new\n")
    _run(git_project, "add", "principles/new.md")

    code, stdout, _stderr = _run_check_triggers(git_project, "--staged")
    assert code == 0
    hits = _parse_ndjson(stdout)
    t1_1 = [h for h in hits if h["trigger"] == "T1.1"]
    assert t1_1, hits
    assert t1_1[0]["ref"] == "staged"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_invalid_ref_exits_2_with_stderr(git_project: Path) -> None:
    code, _stdout, stderr = _run_check_triggers(git_project, "--since", "nonexistent")
    assert code == 2, stderr
    assert "nonexistent" in stderr or "unknown" in stderr.lower()


def test_mutually_exclusive_flags_exit_2(git_project: Path) -> None:
    code, _stdout, stderr = _run_check_triggers(
        git_project,
        "--since",
        "HEAD~1",
        "--staged",
    )
    assert code == 2, stderr
    assert "mutually exclusive" in stderr.lower() or "exclusive" in stderr.lower()


def test_missing_cortex_dir_exits_2(tmp_path: Path) -> None:
    _git_init(tmp_path)
    code, _stdout, stderr = _run_check_triggers(tmp_path, "--since", "HEAD~1")
    assert code == 2, stderr
    assert ".cortex" in stderr


# ---------------------------------------------------------------------------
# Combined diff (T1.1 + T1.8)
# ---------------------------------------------------------------------------


def test_t1_1_and_t1_8_co_fire(git_project: Path) -> None:
    # `feat: ... breaking` matches the T1.8 regex; touching `principles/`
    # fires T1.1 — both must show up in the NDJSON.
    _commit(
        git_project,
        "feat: breaking principles update",
        {"principles/x.md": "rule\n"},
    )
    code, stdout, _stderr = _run_check_triggers(git_project, "--since", "HEAD~1")
    assert code == 0
    hits = _parse_ndjson(stdout)
    triggers = {h["trigger"] for h in hits}
    assert {"T1.1", "T1.8"}.issubset(triggers), triggers
