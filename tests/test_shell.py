"""Tests for ``cortex.shell`` — run_git tri-state + git_remediation_cmd builder.

Both patterns emerged as load-bearing during the PR #27 ``--local-only``
review loop. The tests exercise real ``git`` and a real filesystem
(``tmp_path``) — no monkeypatched subprocess — so regressions against
actual shell behavior surface, not against a mock contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cortex.shell import GitRun, git_remediation_cmd, run_git


def _git_init(target: Path) -> None:
    """Create a minimal git repo at ``target``.

    Commit signing is disabled so the tests run on developer machines that
    have it configured globally without asking for a key.
    """
    for cmd in (
        ["git", "init", "-q"],
        ["git", "-C", str(target), "config", "user.email", "t@e.co"],
        ["git", "-C", str(target), "config", "user.name", "T"],
        ["git", "-C", str(target), "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(target), check=True, capture_output=True)


# --- run_git ---------------------------------------------------------------


def test_run_git_ok_on_successful_command(tmp_path: Path) -> None:
    _git_init(tmp_path)
    result = run_git("ls-files", cwd=tmp_path)
    assert isinstance(result, GitRun)
    assert result.ok
    assert not result.not_a_repo
    assert result.returncode == 0
    assert result.reason is None


def test_run_git_stdout_contains_tracked_files(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "a.md").write_text("a\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "a.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    result = run_git("ls-files", cwd=tmp_path)
    assert result.ok
    assert "a.md" in result.stdout


def test_run_git_not_a_repo_surfaces_as_tri_state(tmp_path: Path) -> None:
    """No `git init` performed — git should report "not a git repository"
    and :class:`GitRun` must expose that as a distinct branch from a
    clean-but-empty result. Collapsing them was the PR #27 round-6 bug."""
    result = run_git("ls-files", cwd=tmp_path)
    assert not result.ok
    assert result.not_a_repo
    # reason is None because git itself answered — the subprocess layer
    # didn't fail; only the git command did. Distinguishes this case from
    # git-missing / OS-level failure.
    assert result.reason is None


def test_run_git_missing_binary_surfaces_as_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the ``git`` binary is not on PATH the helper must surface the
    third state: ``ok=False, not_a_repo=False, reason=...``. Callers use
    this distinction to choose between "treat as safe" (``not_a_repo``)
    and "warn, do not claim success" (everything else)."""
    # Override PATH so `git` cannot be found.
    monkeypatch.setenv("PATH", str(tmp_path))
    result = run_git("ls-files", cwd=tmp_path)
    assert not result.ok
    assert not result.not_a_repo
    assert result.reason is not None
    assert "not installed" in result.reason.lower()


def test_run_git_missing_cwd_does_not_masquerade_as_missing_git(
    tmp_path: Path,
) -> None:
    """subprocess.run raises FileNotFoundError for both "executable missing"
    AND "cwd does not exist". Collapsing the latter into "git not installed"
    would mislead diagnostics (a caller who reinstalled git would still
    fail silently). Disambiguate so the reason names the actual cause."""
    nonexistent = tmp_path / "definitely-not-here"
    assert not nonexistent.exists()
    result = run_git("ls-files", cwd=nonexistent)
    assert not result.ok
    assert not result.not_a_repo
    assert result.reason is not None
    # The reason must name the cwd problem, NOT git-not-installed — otherwise
    # a user with git installed would chase the wrong lead.
    assert "not installed" not in result.reason.lower()
    assert "working directory" in result.reason.lower()
    assert str(nonexistent) in result.reason


def test_run_git_other_nonzero_exits_treated_as_unknown(tmp_path: Path) -> None:
    """An unknown git subcommand exits non-zero with a message that does
    NOT match "not a git repository". Must land in the tri-state unknown
    branch — neither clean-safe nor known-empty."""
    _git_init(tmp_path)
    result = run_git("this-is-not-a-real-command", cwd=tmp_path)
    assert not result.ok
    assert not result.not_a_repo
    # reason stays None (subprocess itself ran cleanly; git just said no).
    # The caller distinguishes by checking ok + not_a_repo explicitly.
    assert result.reason is None


def test_run_git_without_cwd_uses_current_directory(tmp_path: Path) -> None:
    """Missing ``cwd`` must not crash — subprocess inherits the caller's
    cwd. We verify by running against a tmp_path via -C instead of cwd=."""
    _git_init(tmp_path)
    result = run_git("-C", str(tmp_path), "status", "--porcelain")
    assert result.ok


# --- git_remediation_cmd ---------------------------------------------------


def test_remediation_plain_form_when_cwd_matches_target() -> None:
    """When cwd == target the prefix is redundant and noisy; the readable
    plain form wins."""
    cmd = git_remediation_cmd(
        "rm", "--cached", "-r", ".cortex/",
        target=Path("/ignored"),
        anchor_to_target=False,
    )
    assert cmd == "git rm --cached -r .cortex/"


def test_remediation_anchors_to_target_when_cwd_differs() -> None:
    """When cwd differs from the target project (monorepo / --path), the
    remediation anchors to the target via ``git -C <path>`` so a user
    copy-pasting from any cwd still affects the right project."""
    cmd = git_remediation_cmd(
        "rm", "--cached", "-r", ".cortex/",
        target=Path("/workspace/packages/sub"),
        anchor_to_target=True,
    )
    assert cmd == "git -C /workspace/packages/sub rm --cached -r .cortex/"


def test_remediation_shell_quotes_target_with_spaces() -> None:
    """Paths containing spaces must survive copy-paste: ``shlex.quote``
    wraps them in single quotes so the shell doesn't re-tokenize."""
    cmd = git_remediation_cmd(
        "rm", "--cached", "-r", ".cortex/",
        target=Path("/tmp/My Project"),
        anchor_to_target=True,
    )
    assert cmd == "git -C '/tmp/My Project' rm --cached -r .cortex/"


def test_remediation_shell_quotes_arguments_with_whitespace() -> None:
    """A commit message with spaces and punctuation must be quoted so
    ``-m`` still receives a single argument after shell re-parsing."""
    cmd = git_remediation_cmd(
        "commit", "-m", "chore: untrack .cortex/ (local-only)",
        target=Path("/repo"),
        anchor_to_target=True,
    )
    assert cmd == (
        "git -C /repo commit -m 'chore: untrack .cortex/ (local-only)'"
    )


def test_remediation_passes_safe_tokens_unchanged() -> None:
    """``shlex.quote`` is a no-op for tokens without metacharacters, so
    ``--cached`` stays ``--cached`` (not ``'--cached'``). Verifies the
    builder doesn't add gratuitous quotes that would make output harder
    to read."""
    cmd = git_remediation_cmd(
        "ls-files", ".cortex",
        target=Path("/repo"),
        anchor_to_target=False,
    )
    assert cmd == "git ls-files .cortex"
