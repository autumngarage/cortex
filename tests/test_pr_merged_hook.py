"""Tests for ``scripts/cortex-pr-merged-hook.sh``.

The hook is a bash script, so the tests drive it through ``subprocess`` against
a real on-disk git repo (``tmp_path``). No bash mocking — the harness mirrors
``test_shell.py``'s "real git" pattern so regressions surface against actual
shell behavior, not against a mock contract.

cortex#193 — the hook fires on every default-branch merge, including merges
of the auto-draft PRs the hook itself produces. Without a recursion guard
the resulting chain has no terminator.

The fixtures wire ``cortex`` onto PATH as a small shell shim that records
calls and produces the stdout the hook expects.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "cortex-pr-merged-hook.sh"


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(target: Path) -> None:
    """Create a minimal git repo at ``target`` with a default-branch
    commit so ``log -1`` has something to inspect."""
    _git("init", "-q", "--initial-branch=main", cwd=target)
    _git("config", "user.email", "t@e.co", cwd=target)
    _git("config", "user.name", "T", cwd=target)
    _git("config", "commit.gpgsign", "false", cwd=target)
    (target / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=target)
    _git("commit", "-q", "-m", "initial", cwd=target)


def _make_cortex_shim(bin_dir: Path, journal_path: Path) -> Path:
    """Write a ``cortex`` shim that simulates ``cortex journal draft
    pr-merged --no-edit``: it creates the journal file and prints the
    absolute path on stdout. Records every invocation to a sidecar log so
    a test can assert the shim was (or wasn't) called."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = bin_dir / "cortex.calls.log"
    log_file.write_text("", encoding="utf-8")
    shim = bin_dir / "cortex"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log_file!s}
            mkdir -p {journal_path.parent!s}
            printf 'placeholder\\n' > {journal_path!s}
            printf '%s\\n' {journal_path!s}
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log_file


def _make_failing_cortex_shim(bin_dir: Path) -> Path:
    """Write a ``cortex`` shim that fails loudly if invoked. Used by the
    recursion-guard test to assert the writer never fires."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = bin_dir / "cortex.calls.log"
    log_file.write_text("", encoding="utf-8")
    shim = bin_dir / "cortex"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log_file!s}
            printf 'cortex shim was called when it should not have been\\n' >&2
            exit 99
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log_file


def _run_hook(
    project: Path,
    bin_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook with PATH = bin_dir + system PATH (so git works,
    but cortex resolves to our shim unless the test deliberately omits
    it)."""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["TOUCHSTONE_DEFAULT_BRANCH"] = "main"
    # Default to skipping the push so tests don't try to talk to a real
    # remote. Tests that exercise the push path override this explicitly.
    env.setdefault("TOUCHSTONE_CORTEX_HOOK_SKIP_PUSH", "1")
    # Don't inherit the developer's TOUCHSTONE_CORTEX_HOOK_DISABLE.
    env.pop("TOUCHSTONE_CORTEX_HOOK_DISABLE", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def project_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A tmp git repo with a ``.cortex/`` dir, a ``.touchstone-config``
    that activates the hook, and a sibling ``bin/`` for shims."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    (project / ".cortex" / "journal").mkdir(parents=True)
    (project / ".cortex" / "state.md").write_text("# state\n", encoding="utf-8")
    (project / ".touchstone-config").write_text(
        "cortex_pr_merged_hook=auto\n", encoding="utf-8"
    )
    # Commit the scaffold so the hook's dirty-tree gate doesn't see
    # untracked fixture files as a real working-tree concern. The hook
    # is documented to refuse to run on a dirty tree (it would fold
    # uncommitted user work into the auto-commit), so the fixture has
    # to mirror the real post-merge state: clean tree on default branch.
    _git("add", ".cortex", ".touchstone-config", cwd=project)
    _git("commit", "-q", "-m", "scaffold cortex + touchstone config", cwd=project)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return project, bin_dir


# ---------------------------------------------------------------------------
# cortex#193 — recursion guard
# ---------------------------------------------------------------------------


def test_hook_skips_when_recent_commit_is_auto_draft(
    project_repo: tuple[Path, Path],
) -> None:
    """If HEAD's subject already matches the auto-draft prefix the hook
    must exit silently without invoking ``cortex``. This is the cortex#193
    recursion terminator: a merge of an auto-draft PR carries the
    auto-draft subject through the squash, and re-firing on it would
    chain forever."""
    project, bin_dir = project_repo
    # Loud-failure shim — proves the writer never runs.
    log_file = _make_failing_cortex_shim(bin_dir)
    # Make HEAD look like a previous auto-draft squash-merge.
    (project / ".cortex" / "journal" / "auto-draft.md").write_text(
        "x\n", encoding="utf-8"
    )
    _git("add", ".cortex/journal/auto-draft.md", cwd=project)
    _git(
        "commit", "-q",
        "-m", "docs(journal): auto-draft pr-merged entry for #42",
        cwd=project,
    )
    head_before = _git("rev-parse", "HEAD", cwd=project).stdout.strip()

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    # No new commit.
    head_after = _git("rev-parse", "HEAD", cwd=project).stdout.strip()
    assert head_after == head_before
    # cortex shim was never invoked.
    assert log_file.read_text(encoding="utf-8") == ""


def test_recursion_guard_uses_real_git_log(
    project_repo: tuple[Path, Path],
) -> None:
    """Regression guard: the recursion check must consult ``git log -1
    --format=%s HEAD`` against the real repo, not a hardcoded subject.
    We prove this by giving HEAD a non-matching subject and confirming
    the hook DOES run (i.e. the guard is a real lookup, not a constant
    short-circuit)."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "draft.md"
    log_file = _make_cortex_shim(bin_dir, journal_path)
    # HEAD has the seed commit ('initial') — does NOT match the prefix.

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    # cortex WAS invoked: the guard lets non-auto-draft heads through.
    assert "journal draft pr-merged --no-edit" in log_file.read_text(
        encoding="utf-8"
    )
