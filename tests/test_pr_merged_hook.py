"""Tests for ``scripts/cortex-pr-merged-hook.sh``.

The hook is a bash script, so the tests drive it through ``subprocess`` against
a real on-disk git repo (``tmp_path``). No bash mocking — the harness mirrors
``test_shell.py``'s "real git" pattern so regressions surface against actual
shell behavior, not against a mock contract.

Two companion bugs the hook must defend against (both surfaced together in
the vesper 2026-05-06 / 2026-05-07 session):

* cortex#193 — the hook fires on every default-branch merge, including
  merges of the auto-draft PRs the hook itself produces. Without a
  recursion guard the resulting chain has no terminator.
* cortex#194 — the hook used to commit on local ``main`` and push directly
  to ``origin/main``. In projects enforcing ``no-commit-to-branch`` (the
  autumn-garage default) the push was rejected, the commit stranded on
  local main, and ``main`` ended up diverged from ``origin``. The fix is
  feature-branch + PR shipping.

The fixtures wire ``cortex`` (and, where needed, ``gh``) onto PATH as small
shell shims that record calls and produce the stdout the hook expects.
"""

from __future__ import annotations

import os
import shutil
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


# Default NDJSON for the cortex shim's ``check-triggers`` branch. One
# T1.1 hit so the substantive-merge gate (cortex#206) trips and the
# hook proceeds to draft a journal entry. Existing tests written
# before the gate landed assume the writer always runs; defaulting to
# "trigger fired" keeps them green without per-test fixture churn.
_DEFAULT_CHECK_TRIGGERS_NDJSON = (
    '{"trigger":"T1.1","reason":"diff touches `principles/`",'
    '"template":".cortex/templates/journal/decision.md",'
    '"ref":"HEAD~1..HEAD","files":["principles/foo.md"]}'
)


def _make_cortex_shim(
    bin_dir: Path,
    journal_path: Path,
    *,
    check_triggers_ndjson: str = _DEFAULT_CHECK_TRIGGERS_NDJSON,
    check_triggers_status: int = 0,
    check_triggers_stderr: str = "",
) -> Path:
    """Write a ``cortex`` shim that simulates the two subcommands the
    pr-merged hook calls:

    * ``cortex check-triggers --since HEAD~1`` — the substantive-merge
      gate (cortex#206). The shim emits ``check_triggers_ndjson`` on
      stdout, ``check_triggers_stderr`` on stderr, and exits with
      ``check_triggers_status``. Default is one fired T1.1 hit so the
      gate trips green and the hook proceeds; pass ``""`` to
      simulate "no triggers fired" (silent-skip path).
    * ``cortex journal draft pr-merged --no-edit`` — creates the
      journal file at ``journal_path`` and prints the absolute path on
      stdout, matching the real CLI contract.

    Records every invocation to a sidecar log so tests can assert the
    shim was (or wasn't) called and with what subcommand."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = bin_dir / "cortex.calls.log"
    log_file.write_text("", encoding="utf-8")
    shim = bin_dir / "cortex"
    # Encode any literal `'` in shim output so the heredoc-free
    # template stays valid. Keep newlines as `\n` so NDJSON multi-line
    # payloads survive the trip through PATH dispatch.
    ndjson_escaped = check_triggers_ndjson.replace("'", "'\\''")
    stderr_escaped = check_triggers_stderr.replace("'", "'\\''")
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log_file!s}
            if [ "${{1:-}}" = "--no-auto-sync" ]; then
              shift
            fi
            if [ "$1" = "check-triggers" ]; then
              if [ -n '{stderr_escaped}' ]; then
                printf '%s' '{stderr_escaped}' >&2
              fi
              if [ -n '{ndjson_escaped}' ]; then
                printf '%s\\n' '{ndjson_escaped}'
              fi
              exit {check_triggers_status}
            fi
            mkdir -p {journal_path.parent!s}
            printf 'placeholder\\n' > {journal_path!s}
            printf '%s\\n' {journal_path!s}
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log_file


def _make_git_shim(
    bin_dir: Path,
    *,
    fail_checkout_branch: bool = False,
    fail_add: bool = False,
) -> Path:
    """Write a `git` shim that delegates to the real binary except for
    targeted failure injection. Used to simulate hook-local git failures
    without mocking the bash script."""

    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = bin_dir / "git.calls.log"
    log_file.write_text("", encoding="utf-8")
    shim = bin_dir / "git"
    checkout_block = (
        "if [ \"${cmd_args[0]:-}\" = \"checkout\" ] && [ \"${cmd_args[1]:-}\" = \"-q\" ] "
        "&& [ \"${cmd_args[2]:-}\" = \"-b\" ]; then\n"
        "  printf 'git shim: forced checkout -b failure\\n' >&2\n"
        "  exit 42\n"
        "fi\n"
        if fail_checkout_branch
        else ""
    )
    add_block = (
        "if [ \"${cmd_args[0]:-}\" = \"add\" ]; then\n"
        "  printf 'git shim: forced add failure\\n' >&2\n"
        "  exit 43\n"
        "fi\n"
        if fail_add
        else ""
    )
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log_file!s}
            cmd_args=("$@")
            if [ "${{cmd_args[0]:-}}" = "-C" ]; then
              cmd_args=("${{cmd_args[@]:2}}")
            fi
            {checkout_block}{add_block}exec {real_git!s} "$@"
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log_file


def _make_gh_blocking_shim(bin_dir: Path) -> Path:
    """Write a ``gh`` shim that fails for any subcommand, so the hook
    has to take its degraded path. Records calls."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = bin_dir / "gh.calls.log"
    log_file.write_text("", encoding="utf-8")
    shim = bin_dir / "gh"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log_file!s}
            printf 'gh shim: forced failure\\n' >&2
            exit 4
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log_file


def _make_gh_no_label_success_shim(bin_dir: Path) -> Path:
    """Write a ``gh`` shim for the production happy path where the
    optional ``cortex-auto-draft`` label is absent. ``gh label list``
    returns zero rows, while PR creation and auto-merge succeed."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = bin_dir / "gh.calls.log"
    log_file.write_text("", encoding="utf-8")
    shim = bin_dir / "gh"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log_file!s}
            case "$1 $2" in
              "label list")
                exit 0
                ;;
              "pr create")
                printf 'https://github.com/autumngarage/cortex/pull/777\\n'
                exit 0
                ;;
              "pr merge")
                exit 0
                ;;
            esac
            printf 'unexpected gh invocation: %s\\n' "$*" >&2
            exit 4
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
    # SPEC_VERSION marker is the real-repo signal that this store has
    # opted into Cortex writer paths. The hook's #220 gate (added 2026-05-08)
    # silently skips the auto-draft when this file is missing, so every
    # test that expects the writer to RUN must have it present. The
    # missing-marker behavior gets its own dedicated test below.
    (project / ".cortex" / "SPEC_VERSION").write_text(
        "1.1.0\n", encoding="utf-8"
    )
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


# ---------------------------------------------------------------------------
# cortex#220 — SPEC_VERSION-missing skip
# ---------------------------------------------------------------------------


def test_hook_skips_cleanly_when_spec_version_missing(
    project_repo: tuple[Path, Path],
) -> None:
    """A repo can have a ``.cortex/`` directory (e.g. created by a
    hook scaffolder) without yet committing to the writer paths — the
    canonical signal for that is the absence of ``.cortex/SPEC_VERSION``.

    In that state the hook MUST exit 0 with one informational stderr
    line and MUST NOT invoke ``cortex journal draft`` (which itself
    refuses on missing SPEC_VERSION and would surface as exit 2 — the
    cortex#220 failure mode).
    """
    project, bin_dir = project_repo
    # Remove the marker the fixture committed.
    spec_version = project / ".cortex" / "SPEC_VERSION"
    spec_version.unlink()
    _git("add", "-A", cwd=project)
    _git("commit", "-q", "-m", "remove SPEC_VERSION", cwd=project)
    # Loud-failure shim — proves the writer never runs.
    log_file = _make_failing_cortex_shim(bin_dir)
    head_before = _git("rev-parse", "main", cwd=project).stdout.strip()

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    assert "SPEC_VERSION missing" in result.stderr, result.stderr
    # Default branch unchanged (no auto-commit).
    head_after = _git("rev-parse", "main", cwd=project).stdout.strip()
    assert head_after == head_before
    # cortex shim was never invoked — gate fired before the writer.
    assert log_file.read_text(encoding="utf-8") == ""


def test_hook_proceeds_when_spec_version_present(
    project_repo: tuple[Path, Path],
) -> None:
    """The default fixture has ``.cortex/SPEC_VERSION`` — confirm the
    gate is a real lookup (not a constant short-circuit) by verifying
    that the writer DOES run when the marker is present."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "with-marker.md"
    log_file = _make_cortex_shim(bin_dir, journal_path)

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    assert "journal draft pr-merged --no-edit" in log_file.read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# cortex#194 — feature-branch shipping
# ---------------------------------------------------------------------------


def test_hook_creates_feature_branch_for_auto_draft(
    project_repo: tuple[Path, Path],
) -> None:
    """Skip-push mode preserves the new commit on a ``docs/journal-pr-*``
    feature branch, NOT on the default branch. The branch name embeds
    the source-PR number when available."""
    project, bin_dir = project_repo
    journal_path = (
        project / ".cortex" / "journal" / "2026-05-07-pr-merged.md"
    )
    _make_cortex_shim(bin_dir, journal_path)
    main_head_before = _git("rev-parse", "main", cwd=project).stdout.strip()

    result = _run_hook(
        project,
        bin_dir,
        extra_env={
            "TOUCHSTONE_MERGED_PR": "175",
            "TOUCHSTONE_CORTEX_HOOK_SKIP_PUSH": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    # Default branch UNCHANGED.
    main_head_after = _git("rev-parse", "main", cwd=project).stdout.strip()
    assert main_head_after == main_head_before, (
        "main moved; the hook must not commit to the default branch."
    )
    # Feature branch exists with the expected name.
    expected_branch = "docs/journal-pr-175"
    branches = _git("branch", "--list", expected_branch, cwd=project).stdout
    assert expected_branch in branches, (
        f"expected branch '{expected_branch}' to exist; got: {branches!r}"
    )
    # The feature-branch HEAD's commit subject matches the hook's contract.
    feature_subject = _git(
        "log", "-1", "--format=%s", expected_branch, cwd=project
    ).stdout.strip()
    assert feature_subject == "docs(journal): auto-draft pr-merged entry for #175"
    # The feature branch's tree contains the journal file. The hook
    # appends a `## Triggers fired` section after the draft writes it
    # (cortex#206), so assert prefix + the triggers stanza separately.
    files = _git(
        "show", f"{expected_branch}:.cortex/journal/2026-05-07-pr-merged.md",
        cwd=project,
    ).stdout
    assert files.startswith("placeholder\n"), files
    assert "## Triggers fired" in files


def test_hook_does_not_commit_to_default_branch(
    project_repo: tuple[Path, Path],
) -> None:
    """Direct invariant of cortex#194: after the hook runs, the default
    branch's tip must equal what it was before. This is asserted
    independently of the feature-branch existence check so a future
    refactor that drops the branch creation but reintroduces the
    commit-on-main step is still caught."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    _make_cortex_shim(bin_dir, journal_path)
    head_before = _git("rev-parse", "main", cwd=project).stdout.strip()

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    head_after = _git("rev-parse", "main", cwd=project).stdout.strip()
    assert head_after == head_before


def test_hook_creates_feature_branch_before_journal_draft(
    project_repo: tuple[Path, Path],
) -> None:
    """Regression for cortex#247: branch creation must happen before
    `cortex journal draft` writes into `.cortex/journal/`. If branch
    creation fails, the draft command must not run and `main` stays clean."""

    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    cortex_log = _make_cortex_shim(bin_dir, journal_path)
    _make_git_shim(bin_dir, fail_checkout_branch=True)
    main_head_before = _git("rev-parse", "main", cwd=project).stdout.strip()

    result = _run_hook(
        project,
        bin_dir,
        extra_env={"TOUCHSTONE_MERGED_PR": "247"},
    )

    assert result.returncode == 1, result.stderr
    assert "failed before journal draft" in result.stderr
    assert not journal_path.exists()
    cortex_calls = cortex_log.read_text(encoding="utf-8")
    assert "--no-auto-sync check-triggers --since HEAD~1" in cortex_calls
    assert "journal draft pr-merged --no-edit" not in cortex_calls
    assert _git("rev-parse", "main", cwd=project).stdout.strip() == main_head_before
    assert _git("branch", "--show-current", cwd=project).stdout.strip() == "main"
    assert _git("status", "--porcelain", cwd=project).stdout.strip() == ""


def test_hook_add_failure_preserves_draft_on_recovery_branch(
    project_repo: tuple[Path, Path],
) -> None:
    """If a local git failure happens after drafting, the hook must not
    return to `main` with surprise journal dirt. It leaves the operator on
    the named recovery branch with explicit instructions."""

    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    _make_cortex_shim(bin_dir, journal_path)
    _make_git_shim(bin_dir, fail_add=True)
    main_head_before = _git("rev-parse", "main", cwd=project).stdout.strip()

    result = _run_hook(
        project,
        bin_dir,
        extra_env={"TOUCHSTONE_MERGED_PR": "248"},
    )

    assert result.returncode == 1, result.stderr
    assert "git add" in result.stderr
    assert "docs/journal-pr-248" in result.stderr
    assert "do not commit this journal file to main" in result.stderr
    assert _git("rev-parse", "main", cwd=project).stdout.strip() == main_head_before
    assert _git("branch", "--show-current", cwd=project).stdout.strip() == "docs/journal-pr-248"
    assert journal_path.exists()
    assert "## Triggers fired" in journal_path.read_text(encoding="utf-8")


def test_hook_branch_slug_falls_back_to_timestamp_when_no_pr_number(
    project_repo: tuple[Path, Path],
) -> None:
    """When ``TOUCHSTONE_MERGED_PR`` isn't set the branch name must still
    be unique (a timestamp slug, by convention). We don't pin the exact
    timestamp, only the prefix and that *some* uniquifying slug appears."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    _make_cortex_shim(bin_dir, journal_path)

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    branches = _git("branch", "--list", "docs/journal-pr-*", cwd=project).stdout
    matched = [
        b.strip().lstrip("* ").strip() for b in branches.splitlines() if b.strip()
    ]
    assert matched, f"expected at least one docs/journal-pr-* branch; got: {branches!r}"
    # Slug must be non-empty.
    for branch in matched:
        slug = branch.removeprefix("docs/journal-pr-")
        assert slug, f"branch '{branch}' has no slug"


def test_hook_handles_gh_pr_create_failure_gracefully(
    project_repo: tuple[Path, Path],
) -> None:
    """If ``gh pr create`` fails (gh missing, label wrong, branch
    protection refusing auto-merge) the hook MUST:

      * preserve the journal commit on the feature branch,
      * print a stderr line telling the operator how to ship it manually,
      * exit 0 (the source PR has merged; this is the journal step, not
        the merge step — failing here would noisily fail the whole merge
        pipeline for a recoverable problem).
    """
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    _make_cortex_shim(bin_dir, journal_path)
    _make_gh_blocking_shim(bin_dir)
    # Add a bare local remote so the push step can succeed without a
    # network call. The branch will get pushed to this remote; gh pr
    # create then fails (forced) and the hook degrades gracefully.
    remote_dir = project.parent / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote_dir)],
        check=True,
        capture_output=True,
    )
    _git("remote", "add", "origin", str(remote_dir), cwd=project)
    _git("push", "-u", "--quiet", "origin", "main", cwd=project)

    result = _run_hook(
        project,
        bin_dir,
        extra_env={
            "TOUCHSTONE_MERGED_PR": "200",
            # Allow the push step to actually run.
            "TOUCHSTONE_CORTEX_HOOK_SKIP_PUSH": "0",
        },
    )

    # Exit 0 — gh failure is recoverable, not a hard fail.
    assert result.returncode == 0, (
        f"hook exited {result.returncode}; stderr:\n{result.stderr}"
    )
    # Default branch unchanged on origin.
    main_head_after_remote = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    main_head_initial = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "origin/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert main_head_after_remote == main_head_initial
    # Feature branch with the commit still exists locally.
    expected_branch = "docs/journal-pr-200"
    branches = _git("branch", "--list", expected_branch, cwd=project).stdout
    assert expected_branch in branches, (
        f"expected '{expected_branch}' to be preserved on degraded path; "
        f"got: {branches!r}"
    )
    # Operator-actionable stderr names the branch.
    assert expected_branch in result.stderr


def test_hook_succeeds_when_cortex_auto_draft_label_is_absent(
    project_repo: tuple[Path, Path],
) -> None:
    """Regression for cortex#203: under ``set -u``, an absent optional
    label must not leave ``label_args`` unbound before ``gh pr create``."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    _make_cortex_shim(bin_dir, journal_path)
    gh_log = _make_gh_no_label_success_shim(bin_dir)
    remote_dir = project.parent / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(remote_dir)],
        check=True,
        capture_output=True,
    )
    _git("remote", "add", "origin", str(remote_dir), cwd=project)
    _git("push", "-u", "--quiet", "origin", "main", cwd=project)

    result = _run_hook(
        project,
        bin_dir,
        extra_env={
            "TOUCHSTONE_MERGED_PR": "203",
            "TOUCHSTONE_CORTEX_HOOK_SKIP_PUSH": "0",
        },
    )

    assert result.returncode == 0, (
        f"hook exited {result.returncode}; stderr:\n{result.stderr}"
    )
    expected_branch = "docs/journal-pr-203"
    branches = _git("branch", "--list", expected_branch, cwd=project).stdout
    assert expected_branch in branches, (
        f"expected '{expected_branch}' to exist; got: {branches!r}"
    )
    feature_subject = _git(
        "log", "-1", "--format=%s", expected_branch, cwd=project
    ).stdout.strip()
    assert feature_subject == "docs(journal): auto-draft pr-merged entry for #203"
    files = _git("show", f"{expected_branch}:.cortex/journal/auto.md", cwd=project)
    assert files.stdout.startswith("placeholder\n"), files.stdout
    assert "## Triggers fired" in files.stdout
    gh_calls = gh_log.read_text(encoding="utf-8")
    assert "label list" in gh_calls
    assert "pr create" in gh_calls
    assert "--label" not in gh_calls
    assert "pr merge 777 --squash --delete-branch --auto" in gh_calls


def test_hook_silent_skip_when_off_in_config(
    project_repo: tuple[Path, Path],
) -> None:
    """``cortex_pr_merged_hook=off`` is the documented kill-switch and
    must short-circuit before invoking ``cortex``. Re-asserts a property
    that pre-existed the bug fixes; included so the test file owns the
    full activation contract, not just the new behavior."""
    project, bin_dir = project_repo
    log_file = _make_failing_cortex_shim(bin_dir)
    (project / ".touchstone-config").write_text(
        "cortex_pr_merged_hook=off\n", encoding="utf-8"
    )

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    assert log_file.read_text(encoding="utf-8") == ""


def test_hook_silent_skip_when_disable_env_set(
    project_repo: tuple[Path, Path],
) -> None:
    """``TOUCHSTONE_CORTEX_HOOK_DISABLE`` is the per-invocation
    short-circuit; same activation-contract regression check."""
    project, bin_dir = project_repo
    log_file = _make_failing_cortex_shim(bin_dir)

    result = _run_hook(
        project,
        bin_dir,
        extra_env={"TOUCHSTONE_CORTEX_HOOK_DISABLE": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert log_file.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# cortex#206 — substantive-merge gate via `cortex check-triggers`
# ---------------------------------------------------------------------------


def test_hook_skips_when_check_triggers_returns_empty(
    project_repo: tuple[Path, Path],
) -> None:
    """When ``cortex check-triggers --since HEAD~1`` exits 0 with empty
    stdout, the merge wasn't substantive enough to warrant a Journal
    entry: the hook must silent-skip with no feature branch, no
    journal file, no commit. This is the core gate behavior — half
    the merges to a healthy trunk are typo fixes that don't need
    their own meta-PR."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    log_file = _make_cortex_shim(
        bin_dir,
        journal_path,
        check_triggers_ndjson="",  # gate trips: no triggers fired
    )
    main_head_before = _git("rev-parse", "main", cwd=project).stdout.strip()

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    # No feature branch was created.
    branches = _git("branch", "--list", "docs/journal-pr-*", cwd=project).stdout
    assert branches.strip() == "", (
        f"expected no docs/journal-pr-* branch when gate trips; got: {branches!r}"
    )
    # Default branch unchanged.
    main_head_after = _git("rev-parse", "main", cwd=project).stdout.strip()
    assert main_head_after == main_head_before
    # Journal file was never written by the draft path.
    assert not journal_path.exists()
    # The shim recorded only the check-triggers call — never `journal draft`.
    calls = log_file.read_text(encoding="utf-8")
    assert "check-triggers" in calls
    assert "journal draft" not in calls


def test_hook_proceeds_when_t1_4_fires(
    project_repo: tuple[Path, Path],
) -> None:
    """A T1.4 (file-deletion >100 lines) hit must produce a journal
    entry whose body lists the trigger and its files."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    ndjson = (
        '{"trigger":"T1.4",'
        '"reason":"file deletion exceeds 100 lines (deleted 142 from src/foo.py)",'
        '"template":".cortex/templates/journal/decision.md",'
        '"ref":"HEAD~1..HEAD","files":["src/foo.py"],"lines_deleted":142}'
    )
    _make_cortex_shim(bin_dir, journal_path, check_triggers_ndjson=ndjson)

    result = _run_hook(
        project,
        bin_dir,
        extra_env={"TOUCHSTONE_MERGED_PR": "300"},
    )

    assert result.returncode == 0, result.stderr
    expected_branch = "docs/journal-pr-300"
    branches = _git("branch", "--list", expected_branch, cwd=project).stdout
    assert expected_branch in branches
    body = _git(
        "show", f"{expected_branch}:.cortex/journal/auto.md", cwd=project
    ).stdout
    assert "## Triggers fired" in body
    assert "T1.4" in body
    assert "src/foo.py" in body


def test_hook_proceeds_when_t1_1_fires(
    project_repo: tuple[Path, Path],
) -> None:
    """A T1.1 (diff touches principles/) hit must produce a journal
    entry whose body lists the trigger and its files."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    ndjson = (
        '{"trigger":"T1.1",'
        '"reason":"diff touches `.cortex/doctrine/`, `.cortex/plans/`, '
        '`principles/`, or `SPEC.md`",'
        '"template":".cortex/templates/journal/decision.md",'
        '"ref":"HEAD~1..HEAD","files":["principles/foo.md"]}'
    )
    _make_cortex_shim(bin_dir, journal_path, check_triggers_ndjson=ndjson)

    result = _run_hook(
        project,
        bin_dir,
        extra_env={"TOUCHSTONE_MERGED_PR": "301"},
    )

    assert result.returncode == 0, result.stderr
    expected_branch = "docs/journal-pr-301"
    body = _git(
        "show", f"{expected_branch}:.cortex/journal/auto.md", cwd=project
    ).stdout
    assert "## Triggers fired" in body
    assert "T1.1" in body
    assert "principles/foo.md" in body


def test_hook_force_flag_bypasses_gate_via_env(
    project_repo: tuple[Path, Path],
) -> None:
    """``TOUCHSTONE_CORTEX_HOOK_FORCE=1`` must bypass the gate entirely:
    even when ``cortex check-triggers`` returns empty (no triggers),
    the hook still produces a journal entry."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    log_file = _make_cortex_shim(
        bin_dir,
        journal_path,
        check_triggers_ndjson="",  # gate would trip without --force
    )

    result = _run_hook(
        project,
        bin_dir,
        extra_env={
            "TOUCHSTONE_MERGED_PR": "400",
            "TOUCHSTONE_CORTEX_HOOK_FORCE": "1",
        },
    )

    assert result.returncode == 0, result.stderr
    expected_branch = "docs/journal-pr-400"
    branches = _git("branch", "--list", expected_branch, cwd=project).stdout
    assert expected_branch in branches, (
        f"force flag must bypass gate; expected '{expected_branch}', "
        f"got: {branches!r}"
    )
    # Force-bypass also skips the check-triggers call entirely (cheap
    # bypass, no point in evaluating). The shim log shows only the
    # journal-draft invocation.
    calls = log_file.read_text(encoding="utf-8")
    assert "journal draft pr-merged --no-edit" in calls
    assert "check-triggers" not in calls


def test_hook_force_via_config_value(
    project_repo: tuple[Path, Path],
) -> None:
    """``cortex_pr_merged_hook=force`` in ``.touchstone-config`` must
    have the same bypass effect as the env var. Lets a project pin
    the behavior without a per-invocation flag."""
    project, bin_dir = project_repo
    (project / ".touchstone-config").write_text(
        "cortex_pr_merged_hook=force\n", encoding="utf-8"
    )
    # Commit the config edit so the hook's dirty-tree gate doesn't
    # see it as uncommitted user work — mirrors the fixture's
    # post-merge cleanliness invariant.
    _git("add", ".touchstone-config", cwd=project)
    _git("commit", "-q", "-m", "config: pin cortex_pr_merged_hook=force", cwd=project)
    journal_path = project / ".cortex" / "journal" / "auto.md"
    _make_cortex_shim(bin_dir, journal_path, check_triggers_ndjson="")

    result = _run_hook(
        project,
        bin_dir,
        extra_env={"TOUCHSTONE_MERGED_PR": "401"},
    )

    assert result.returncode == 0, result.stderr
    expected_branch = "docs/journal-pr-401"
    branches = _git("branch", "--list", expected_branch, cwd=project).stdout
    assert expected_branch in branches


def test_hook_falls_back_when_check_triggers_missing(
    project_repo: tuple[Path, Path],
) -> None:
    """When the cortex CLI is on PATH but the ``check-triggers``
    subcommand is unavailable (older cortex), the hook must fall back
    to journal-every-merge AND print the documented one-line stderr
    notice. A spurious entry is recoverable; a silently-skipped one
    is not."""
    project, bin_dir = project_repo
    journal_path = project / ".cortex" / "journal" / "auto.md"
    _make_cortex_shim(
        bin_dir,
        journal_path,
        # Simulate the legacy "unknown subcommand" error: non-zero
        # exit and a stderr message that names the missing
        # subcommand. The hook must surface both.
        check_triggers_status=2,
        check_triggers_stderr="Error: No such command 'check-triggers'.\n",
    )

    result = _run_hook(
        project,
        bin_dir,
        extra_env={"TOUCHSTONE_MERGED_PR": "500"},
    )

    assert result.returncode == 0, result.stderr
    # The fall-back path produced a journal entry.
    expected_branch = "docs/journal-pr-500"
    branches = _git("branch", "--list", expected_branch, cwd=project).stdout
    assert expected_branch in branches
    # The one-line fall-back notice is present, naming the gate that
    # failed open. This is the "every degradation visible" contract.
    assert (
        "cortex check-triggers unavailable; falling back to journal-every-merge"
        in result.stderr
    )
    # And the verbatim cortex-side stderr is preserved so the
    # operator can tell *why* it was unavailable.
    assert "No such command 'check-triggers'" in result.stderr


def test_recursion_guard_runs_before_check_triggers(
    project_repo: tuple[Path, Path],
) -> None:
    """The recursion guard (cortex#193) MUST short-circuit before any
    cortex invocation, including ``check-triggers``. A meta-PR's
    squash-merge subject already matches the auto-draft prefix; if the
    guard didn't run first we'd both pay the check-triggers
    round-trip AND risk firing the writer on our own output."""
    project, bin_dir = project_repo
    # Loud-failure shim — any cortex invocation (including
    # check-triggers) makes the test fail.
    log_file = _make_failing_cortex_shim(bin_dir)
    # HEAD subject matches the auto-draft recursion prefix.
    (project / ".cortex" / "journal" / "auto-draft.md").write_text(
        "x\n", encoding="utf-8"
    )
    _git("add", ".cortex/journal/auto-draft.md", cwd=project)
    _git(
        "commit", "-q",
        "-m", "docs(journal): auto-draft pr-merged entry for #99",
        cwd=project,
    )
    head_before = _git("rev-parse", "HEAD", cwd=project).stdout.strip()

    result = _run_hook(project, bin_dir)

    assert result.returncode == 0, result.stderr
    # Default branch unmoved AND cortex was never invoked — proving
    # the recursion guard short-circuits before the gate.
    head_after = _git("rev-parse", "HEAD", cwd=project).stdout.strip()
    assert head_after == head_before
    assert log_file.read_text(encoding="utf-8") == "", (
        "recursion guard must run before any cortex invocation; "
        f"shim was called: {log_file.read_text(encoding='utf-8')!r}"
    )
