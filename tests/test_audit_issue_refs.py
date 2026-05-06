"""Tests for `cortex doctor --audit-issue-refs` (check_stale_issue_references)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cortex.cli import cli
from cortex.doctor_checks import (
    ISSUE_STATE_CACHE_TTL_SECONDS,
    check_stale_issue_references,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _scaffold(project: Path) -> None:
    """Create a minimal .cortex/ scaffold so the check has something to scan."""
    (project / ".cortex" / "journal").mkdir(parents=True, exist_ok=True)
    (project / ".cortex" / "plans").mkdir(parents=True, exist_ok=True)
    (project / ".cortex" / "doctrine").mkdir(parents=True, exist_ok=True)


def _journal(project: Path, name: str, content: str) -> Path:
    path = project / ".cortex" / "journal" / name
    path.write_text(content)
    return path


def _plan(project: Path, name: str, content: str) -> Path:
    path = project / ".cortex" / "plans" / name
    path.write_text(content)
    return path


def _set_self_repo(project: Path, repo: str) -> None:
    config_path = project / ".cortex" / "config.toml"
    config_path.write_text(f'[audit-instructions]\nself_repo = "{repo}"\n')


def _gh_returns(state: str) -> MagicMock:
    """Return a mock subprocess.run result that reports the given state."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = f"{state}\n"
    return result


def _gh_error() -> MagicMock:
    result = MagicMock()
    result.returncode = 1
    result.stdout = ""
    result.stderr = "not found"
    return result


# ── Open [ ] + open issue: no warning ─────────────────────────────────────────


def test_open_checkbox_open_issue_no_warning(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Fix autumngarage/cortex#99\n")

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("open")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    assert not any("cortex#99" in i.message for i in issues)


# ── Open [ ] + closed issue: warning with file:line ───────────────────────────


def test_open_checkbox_closed_issue_warns(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Fix autumngarage/cortex#141\n")

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("closed")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    assert any("autumngarage/cortex#141" in i.message for i in issues)
    assert any("line 1" in i.message for i in issues)


def test_open_checkbox_closed_issue_warning_names_line(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    content = "# Some journal\n\nPreamble.\n\n- [ ] Track autumngarage/cortex#200\n"
    _journal(tmp_path, "2026-01-01-test.md", content)

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("closed")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    msgs = [i.message for i in issues]
    assert any("autumngarage/cortex#200" in m for m in msgs)
    assert any("line 5" in m for m in msgs)


# ── Checked [x] + closed issue: no warning ────────────────────────────────────


def test_checked_checkbox_closed_issue_no_warning(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(tmp_path, "2026-01-01-test.md", "- [x] Fixed autumngarage/cortex#141\n")

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("closed")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    assert issues == []


# ── Plans and doctrine are also scanned ───────────────────────────────────────


def test_closed_ref_in_plan_warns(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _plan(tmp_path, "cortex-v1.md", "- [ ] Close autumngarage/cortex#42\n")

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("closed")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    assert any("autumngarage/cortex#42" in i.message for i in issues)


# ── Bare #N + self_repo configured ────────────────────────────────────────────


def test_bare_ref_with_self_repo_resolves_and_warns(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _set_self_repo(tmp_path, "autumngarage/cortex")
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Track #141\n")

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("closed")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    assert any("autumngarage/cortex#141" in i.message for i in issues)


def test_bare_ref_with_self_repo_open_no_warning(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _set_self_repo(tmp_path, "autumngarage/cortex")
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Watch #999\n")

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("open")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    assert issues == []


# ── Bare #N without self_repo: silently skipped ───────────────────────────────


def test_bare_ref_without_self_repo_skipped_silently(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    # No config.toml → no self_repo
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Track #141\n")

    mock_run = MagicMock()
    with (
        patch("cortex.doctor_checks.subprocess.run", mock_run),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    # No gh api call should have been made (nothing to resolve)
    mock_run.assert_not_called()
    assert issues == []


# ── <!-- watch --> marker silences the line ───────────────────────────────────


def test_watch_marker_silences_closed_ref(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(
        tmp_path,
        "2026-01-01-test.md",
        "- [ ] Track autumngarage/cortex#141 <!-- watch -->\n",
    )

    mock_run = MagicMock()
    with (
        patch("cortex.doctor_checks.subprocess.run", mock_run),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    mock_run.assert_not_called()
    assert issues == []


# ── gh not installed: warning instead of crash ───────────────────────────────


def test_gh_not_installed_returns_warning(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Fix autumngarage/cortex#1\n")

    with patch("cortex.doctor_checks.shutil.which", return_value=None):
        issues = check_stale_issue_references(tmp_path)

    assert len(issues) == 1
    assert "gh not installed" in issues[0].message


# ── gh api error: ref silently skipped (no false positive) ───────────────────


def test_gh_api_error_skips_ref(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Fix autumngarage/cortex#500\n")

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_error()),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    assert issues == []


# ── Cache: second call within TTL does not re-query gh ───────────────────────


def test_cache_hit_does_not_requery_gh(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Fix autumngarage/cortex#77\n")

    # Pre-populate a fresh cache entry
    cache_dir = tmp_path / ".cortex" / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_data = {
        "autumngarage/cortex#77": {
            "state": "closed",
            "fetched_at": datetime.now(UTC).isoformat(),
        }
    }
    (cache_dir / "issue-state.json").write_text(json.dumps(cache_data))

    mock_run = MagicMock()
    with (
        patch("cortex.doctor_checks.subprocess.run", mock_run),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    # gh api must NOT have been called — cache was fresh
    mock_run.assert_not_called()
    assert any("autumngarage/cortex#77" in i.message for i in issues)


def test_expired_cache_entry_requeries_gh(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _journal(tmp_path, "2026-01-01-test.md", "- [ ] Fix autumngarage/cortex#88\n")

    # Pre-populate a STALE cache entry (older than TTL)
    cache_dir = tmp_path / ".cortex" / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stale_time = datetime.now(UTC) - timedelta(seconds=ISSUE_STATE_CACHE_TTL_SECONDS + 60)
    cache_data = {
        "autumngarage/cortex#88": {
            "state": "open",  # stale data says open — fresh data will say closed
            "fetched_at": stale_time.isoformat(),
        }
    }
    (cache_dir / "issue-state.json").write_text(json.dumps(cache_data))

    with (
        patch("cortex.doctor_checks.subprocess.run", return_value=_gh_returns("closed")),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        issues = check_stale_issue_references(tmp_path)

    # Fresh fetch saw "closed" — should warn
    assert any("autumngarage/cortex#88" in i.message for i in issues)


# ── No .cortex/ directory: graceful return ────────────────────────────────────


def test_no_cortex_dir_returns_empty(tmp_path: Path) -> None:
    with patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"):
        issues = check_stale_issue_references(tmp_path)
    assert issues == []


# ── CLI integration ────────────────────────────────────────────────────────────
# Patch _gh_issue_state directly (not subprocess.run) so that git calls
# made by other doctor checks are not intercepted.


def test_cli_audit_issue_refs_flag_warns(tmp_path: Path) -> None:
    CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    _journal(tmp_path, "2026-01-01-flag-test.md", "- [ ] Fix autumngarage/cortex#55\n")

    with (
        patch("cortex.doctor_checks._gh_issue_state", return_value="closed"),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        result = CliRunner().invoke(
            cli, ["doctor", "--path", str(tmp_path), "--audit-issue-refs"]
        )

    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "autumngarage/cortex#55" in combined


def test_cli_audit_issue_refs_clean_on_open_issue(tmp_path: Path) -> None:
    CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    _journal(tmp_path, "2026-01-01-flag-test.md", "- [ ] Fix autumngarage/cortex#55\n")

    with (
        patch("cortex.doctor_checks._gh_issue_state", return_value="open"),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        result = CliRunner().invoke(
            cli, ["doctor", "--path", str(tmp_path), "--audit-issue-refs"]
        )

    assert result.exit_code == 0
    combined = (result.output or "") + (getattr(result, "stderr", "") or "")
    assert "stale-issue-ref" not in combined


def test_cli_strict_exits_nonzero_on_stale_ref(tmp_path: Path) -> None:
    CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    _journal(tmp_path, "2026-01-01-strict.md", "- [ ] Close autumngarage/cortex#10\n")

    with (
        patch("cortex.doctor_checks._gh_issue_state", return_value="closed"),
        patch("cortex.doctor_checks.shutil.which", return_value="/usr/bin/gh"),
    ):
        result = CliRunner().invoke(
            cli,
            ["doctor", "--path", str(tmp_path), "--audit-issue-refs", "--strict"],
        )

    assert result.exit_code == 1
