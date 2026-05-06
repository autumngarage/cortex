"""Tests for `cortex doctor --audit-pr-trailers` (check_pr_trailers)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.doctor_checks import check_pr_trailers


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(project: Path) -> None:
    _run(project, "init", "-b", "main")
    _run(project, "config", "user.email", "t@example.com")
    _run(project, "config", "user.name", "Test")


def _commit(project: Path, message: str, *paths: str) -> None:
    _run(project, "add", *paths)
    subprocess.run(
        ["git", "-C", str(project), "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
    )


def _setup_branch_with_origin_main(project: Path) -> None:
    """Init a git repo, create an initial commit, then pin origin/main to it.

    After this, new commits on main (or a feature branch) are "unique"
    relative to origin/main, which is how check_pr_trailers knows what
    to scan.
    """
    project.mkdir(parents=True, exist_ok=True)
    _git_init(project)
    # Initial commit on main (becomes origin/main)
    (project / "readme.txt").write_text("init\n")
    _commit(project, "chore: initial commit", "readme.txt")
    initial_sha = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # Create origin/main tracking ref pointing at the initial commit.
    _run(project, "update-ref", "refs/remotes/origin/main", initial_sha)


def _add_commit(project: Path, message: str, content: str = "change\n") -> None:
    path = project / "change.txt"
    path.write_text(content)
    _commit(project, message, "change.txt")


# ── Trailer present — check passes ────────────────────────────────────────


def test_pr_trailers_passes_when_close_trailer_present(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "feat: implement thing\n\nCloses-issue: #42")

    issues = check_pr_trailers(tmp_path)
    # #42 is referenced (via the trailer itself) AND closed — no warning.
    assert not any("#42" in i.message for i in issues)


def test_pr_trailers_passes_with_closes_trailer(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "fix: resolve #99\n\nCloses: #99")

    issues = check_pr_trailers(tmp_path)
    assert not any("#99" in i.message for i in issues)


def test_pr_trailers_passes_with_fixes_trailer(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "fix: resolve #77\n\nFixes: #77")

    issues = check_pr_trailers(tmp_path)
    assert not any("#77" in i.message for i in issues)


# ── Trailer missing — check warns ─────────────────────────────────────────


def test_pr_trailers_warns_when_issue_ref_without_trailer(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "feat: fix issue #42")

    issues = check_pr_trailers(tmp_path)
    assert any("#42" in i.message for i in issues)


def test_pr_trailers_warns_for_issue_in_body_without_trailer(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "feat: do work\n\nSee also #55 for context.")

    issues = check_pr_trailers(tmp_path)
    assert any("#55" in i.message for i in issues)


# ── Refs: opt-out — no warning ────────────────────────────────────────────


def test_pr_trailers_refs_opt_out_suppresses_warning(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "feat: do work related to #42\n\nRefs: #42")

    issues = check_pr_trailers(tmp_path)
    assert not any("#42" in i.message for i in issues)


# ── Multiple references — partial trailers ────────────────────────────────


def test_pr_trailers_warns_only_for_untrailered_issue(tmp_path: Path) -> None:
    """#1 has a close trailer; #2 does not. Only #2 should warn."""
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "feat: fix #1 and mention #2\n\nCloses-issue: #1")

    issues = check_pr_trailers(tmp_path)
    msgs = [i.message for i in issues]
    assert any("#2" in m for m in msgs)
    assert not any(m for m in msgs if "#1" in m and "no Closes" in m)


# ── No issue references — silent pass ─────────────────────────────────────


def test_pr_trailers_passes_when_no_issue_references(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "chore: clean up whitespace")

    issues = check_pr_trailers(tmp_path)
    assert issues == []


# ── No origin/main — graceful skip ────────────────────────────────────────


def test_pr_trailers_skips_gracefully_when_no_origin_main(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git_init(tmp_path)
    (tmp_path / "readme.txt").write_text("init\n")
    _commit(tmp_path, "feat: fix #42", "readme.txt")
    # No origin/main ref set — check must return [] silently.

    issues = check_pr_trailers(tmp_path)
    assert issues == []


def test_pr_trailers_skips_gracefully_on_main_with_no_unique_commits(tmp_path: Path) -> None:
    """When HEAD == origin/main (no unique branch commits), return []."""
    _setup_branch_with_origin_main(tmp_path)
    # No additional commits — HEAD IS origin/main.

    issues = check_pr_trailers(tmp_path)
    assert issues == []


# ── CLI integration ────────────────────────────────────────────────────────


def test_cli_audit_pr_trailers_flag_warns(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "feat: fix issue #42")

    # Scaffold .cortex/ so doctor structural checks pass.
    CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])

    result = CliRunner().invoke(
        cli, ["doctor", "--path", str(tmp_path), "--audit-pr-trailers"]
    )
    assert "#42" in result.output or "#42" in (result.stderr or "")


def test_cli_audit_pr_trailers_flag_passes_with_trailer(tmp_path: Path) -> None:
    _setup_branch_with_origin_main(tmp_path)
    _add_commit(tmp_path, "feat: implement thing\n\nCloses-issue: #42")

    CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])

    result = CliRunner().invoke(
        cli, ["doctor", "--path", str(tmp_path), "--audit-pr-trailers"]
    )
    assert result.exit_code == 0
    # No warning about #42 missing a trailer.
    combined = (result.output or "") + (result.stderr or "")
    assert "no Closes" not in combined
