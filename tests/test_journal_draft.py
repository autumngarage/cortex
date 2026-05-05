"""Tests for `cortex journal draft <type>`.

Each test scaffolds a real `.cortex/` via ``cortex init`` and exercises
the draft command against the real templates, real filesystem, and a real
``git init``-d temp repo. No mocked subprocess; tests run inside an
environment that may or may not have ``gh`` installed and either path
must work.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

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


def _draft(project: Path, *args: str) -> Result:
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


def test_editor_command_with_args_is_split(git_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `EDITOR="bash -c true"` exits 0 — verifies shlex-splitting the env value
    # rather than trying to exec the whole string as one binary.
    monkeypatch.setenv("EDITOR", "bash -c true")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["journal", "draft", "decision", "--path", str(git_project), "--slug", "split-editor"],
    )
    assert result.exit_code == 0, result.output + (getattr(result, "stderr", "") or "")
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-split-editor.md"
    assert target.exists()


def test_editor_failure_preserves_temp_draft(git_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `EDITOR=false` exits 1; the command must error AND keep the temp file
    # so the user can recover. The prior version unlinked it unconditionally.
    monkeypatch.setenv("EDITOR", "false")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["journal", "draft", "decision", "--path", str(git_project), "--slug", "ed-fails"],
    )
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "draft preserved at" in combined
    # Pull the path out of the message and assert the file is still there.
    match = re.search(r"draft preserved at (\S+\.md)", combined)
    assert match, combined
    preserved = Path(match.group(1))
    try:
        assert preserved.exists()
    finally:
        if preserved.exists():
            preserved.unlink()


def test_editor_missing_binary_preserves_temp_draft(git_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "this-editor-does-not-exist-anywhere")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["journal", "draft", "decision", "--path", str(git_project), "--slug", "ed-missing"],
    )
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "draft preserved at" in combined
    match = re.search(r"draft preserved at (\S+\.md)", combined)
    assert match, combined
    preserved = Path(match.group(1))
    try:
        assert preserved.exists()
    finally:
        if preserved.exists():
            preserved.unlink()


def test_no_edit_early_check_blocks_existing_target(git_project: Path) -> None:
    """Common-case overwrite check: pre-existing target hits the early
    ``target.exists()`` guard. Asserts the user-facing 'already exists'
    message points at --slug for differentiation."""
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-overwrite-test.md"
    target.write_text("# Pre-existing entry — must not be overwritten\n")
    result = _draft(git_project, "decision", "--slug", "overwrite-test")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "already exists" in combined
    assert "--slug" in combined
    assert "Pre-existing" in target.read_text()


def test_no_edit_race_after_early_check_caught_by_exclusive_create(
    git_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Race scenario the prior test didn't cover: target file appears
    *after* the early ``target.exists()`` guard but before ``target.open("x")``.
    Without the exclusive-create fix this would silently overwrite an
    append-only Journal entry. We simulate the race by monkeypatching
    ``_gather_git_context`` (which is called between the check and the
    write) to create the target as a side effect."""
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-race-after-check.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    pre_existing_body = "# Pre-existing entry — must not be overwritten\n"

    def _racing_gather(_project_root: Path) -> list[str]:
        # Simulate a concurrent writer landing the entry between the early
        # check and the post-context exclusive-create write.
        target.write_text(pre_existing_body)
        return []

    import cortex.commands.journal as journal_mod
    monkeypatch.setattr(journal_mod, "_gather_git_context", _racing_gather)

    result = _draft(git_project, "decision", "--slug", "race-after-check")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "appeared between the existence check" in combined
    # Append-only invariant: the racer's content survives intact.
    assert target.read_text() == pre_existing_body


def test_editor_path_exclusive_create_blocks_race_overwrite(
    git_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Race coverage for the editor flow: a third process creates the
    target after the early check + during the edit; the final write must
    refuse to overwrite (Journal append-only invariant)."""
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-editor-race.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    # Custom $EDITOR: a shell snippet that simulates the race by creating
    # the final target file mid-edit (between the early existence check
    # and the post-editor exclusive write).
    editor_script = tmp_path / "racing-editor.sh"
    editor_script.write_text(
        "#!/bin/bash\n"
        f'echo "# Pre-existing — must not be overwritten" > "{target}"\n'
        "exit 0\n"
    )
    editor_script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(editor_script))

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["journal", "draft", "decision", "--path", str(git_project), "--slug", "editor-race"],
    )
    assert result.exit_code == 2, result.output + (getattr(result, "stderr", "") or "")
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "appeared while editing" in combined
    # The pre-existing content must survive — append-only invariant.
    assert "Pre-existing" in target.read_text()
    # The edited temp file is preserved for the user.
    match = re.search(r"draft preserved at (\S+\.md)", combined)
    assert match, combined
    Path(match.group(1)).unlink(missing_ok=True)


def test_writer_refuses_missing_spec_version(git_project: Path) -> None:
    # SPEC § 7: writers refuse, readers warn. Removing SPEC_VERSION must
    # trigger refuse-to-write before any Journal entry is created.
    (git_project / ".cortex" / "SPEC_VERSION").unlink()
    result = _draft(git_project, "decision", "--slug", "no-spec-version")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "refusing to write" in combined
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-no-spec-version.md"
    assert not target.exists()


def test_writer_refuses_unsupported_spec_version(git_project: Path) -> None:
    (git_project / ".cortex" / "SPEC_VERSION").write_text("9.9.9-future\n")
    result = _draft(git_project, "decision", "--slug", "future-spec")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "Refusing to write" in combined
    today = date.today().isoformat()
    target = git_project / ".cortex" / "journal" / f"{today}-future-spec.md"
    assert not target.exists()


def test_invalid_type_name_rejected(git_project: Path) -> None:
    # Path-traversal attempt: ../../etc/passwd as the type would resolve
    # outside .cortex/templates/journal/. Validator must reject before any
    # path joining happens.
    result = _draft(git_project, "../../etc/passwd")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "invalid journal type" in combined
    # Slash + uppercase + leading-dash all rejected.
    for bad in ("foo/bar", "Decision", "-leading-dash", "..", ""):
        r = _draft(git_project, bad)
        assert r.exit_code == 2, (bad, r.output)


# --- cortex#101 pr-merged placeholder substitution --------------------------


def _stub_gh_in_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict[str, object]
) -> Path:
    """Install a fake `gh` on PATH that returns ``payload`` for `pr view`.

    Returns the bin dir so the caller can also drop other shims if needed.
    The shim handles the two `gh` invocations the journal-draft path makes
    today: ``gh auth status`` (must exit 0) and ``gh pr view N --json ...``
    (prints the payload as JSON and exits 0).
    """
    import json as _json

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    payload_path = bin_dir / "pr-payload.json"
    payload_path.write_text(_json.dumps(payload))
    shim = bin_dir / "gh"
    shim.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        "  auth)\n"
        "    exit 0\n"
        "    ;;\n"
        "  pr)\n"
        f'    cat "{payload_path}"\n'
        "    exit 0\n"
        "    ;;\n"
        "  *)\n"
        "    echo \"unexpected gh invocation: $@\" >&2\n"
        "    exit 2\n"
        "    ;;\n"
        "esac\n"
    )
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


def test_pr_merged_explicit_pr_substitutes_placeholders(
    git_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--pr N` resolves PR via stubbed gh and fills the four canonical fields."""
    _stub_gh_in_path(
        monkeypatch,
        tmp_path,
        {
            "number": 99,
            "title": "feat(foo): bar baz",
            "headRefName": "feat/foo-bar",
            "mergeCommit": {"oid": "deadbeefcafef00d" * 2 + "12345678"},
        },
    )
    result = _draft(git_project, "pr-merged", "--pr", "99")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-pr-merged-*.md"))
    assert files, list((git_project / ".cortex" / "journal").iterdir())
    body = files[0].read_text()
    # The four substitutable header fields all have real values now.
    assert body.startswith("# PR #99 merged — feat(foo): bar baz"), body[:120]
    assert "**Branch:** feat/foo-bar" in body
    # HEAD sha is from the test repo, not the gh payload — non-empty hex.
    assert re.search(r"\*\*Merge-commit:\*\* [0-9a-f]{40}", body), body
    # No raw header placeholders survive for the substitutable fields.
    assert "{{ nnn }}" not in body
    assert "{{ short title }}" not in body
    assert "{{ full sha }}" not in body
    assert "{{ <type>/<slug> }}" not in body


def test_pr_merged_no_edit_strips_all_template_placeholders(
    git_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No-edit pr-merged drafts must not auto-commit raw template prompts."""
    title = "fix(journal): strip pr-merged placeholders"
    _stub_gh_in_path(
        monkeypatch,
        tmp_path,
        {
            "number": 99,
            "title": title,
            "body": "\n".join(
                [
                    "## Summary",
                    "- Filled the lede from PR metadata",
                    "- Extracted shipped bullets from the PR body",
                    "* Removed the bogus deferred follow-up checkbox",
                ]
            ),
            "headRefName": "fix/journal-placeholders",
            "mergeCommit": {"oid": "deadbeefcafef00d" * 2 + "12345678"},
        },
    )
    result = _draft(git_project, "pr-merged", "--pr", "99")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-pr-merged-*.md"))
    assert files, list((git_project / ".cortex" / "journal").iterdir())
    body = files[0].read_text()

    assert "{{" not in body, f"Unfilled placeholders survived: {body}"
    assert f"> {title}." in body
    assert "- Filled the lede from PR metadata" in body
    assert "- Extracted shipped bullets from the PR body" in body
    assert "- Removed the bogus deferred follow-up checkbox" in body
    followups = body.split("## Follow-ups (deferred to future work)", 1)[1].split(
        "(Per SPEC", 1
    )[0]
    assert "- [ ]" not in followups
    assert "_None._" in followups


def test_pr_merged_no_edit_with_no_pr_body_uses_title_fallback(
    git_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    title = "fix(journal): fall back to PR title"
    _stub_gh_in_path(
        monkeypatch,
        tmp_path,
        {
            "number": 99,
            "title": title,
            "body": "",
            "headRefName": "fix/journal-title-fallback",
            "mergeCommit": {"oid": "deadbeefcafef00d" * 2 + "12345678"},
        },
    )
    result = _draft(git_project, "pr-merged", "--pr", "99")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-pr-merged-*.md"))
    assert files, list((git_project / ".cortex" / "journal").iterdir())
    body = files[0].read_text()

    assert "{{" not in body, f"Unfilled placeholders survived: {body}"
    assert f"- {title} (#99)" in body


def test_pr_merged_infers_pr_from_merge_commit_subject(
    git_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without `--pr`, parses HEAD's `(#NNN)` squash-merge subject for the PR."""
    _stub_gh_in_path(
        monkeypatch,
        tmp_path,
        {
            "number": 42,
            "title": "fix: thing",
            "headRefName": "fix/thing",
            "mergeCommit": {"oid": "abc123" * 6 + "abcd"},
        },
    )
    # Add a commit whose subject ends in `(#42)` — the squash-merge convention.
    extra = git_project / "EXTRA.md"
    extra.write_text("trigger\n")
    _run(git_project, "add", "EXTRA.md")
    _run(git_project, "commit", "-m", "fix: thing (#42)")

    result = _draft(git_project, "pr-merged")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-pr-merged-*.md"))
    body = files[0].read_text()
    assert "PR #42 merged — fix: thing" in body
    assert "**Branch:** fix/thing" in body
    assert "{{ nnn }}" not in body


def test_pr_merged_falls_back_to_template_without_pr_or_merge_subject(
    git_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `--pr`, no `(#NNN)` merge subject in window → raw template + warning.

    Backwards compat: the prior behavior wrote the raw template. We keep
    that (no crash) but emit a stderr `warning:` so the silent failure is
    closed (engineering principle: no silent failures)."""
    # Hide gh entirely so even if a CI runner has it, the path is "no PR".
    bin_dir = git_project / "isolated-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))

    result = _draft(git_project, "pr-merged")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    files = list((git_project / ".cortex" / "journal").glob(f"{today}-pr-merged-*.md"))
    body = files[0].read_text()
    # Raw template placeholders survive — no substitution context available.
    assert "{{ nnn }}" in body
    assert "{{ short title }}" in body
    # Stderr surfaces the missing context so the failure is visible.
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "warning" in combined.lower()
    assert "could not resolve a PR number" in combined


def test_decision_template_substitution_unchanged(
    git_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: pr-merged substitution must not bleed into other types.

    The decision template's H1 placeholder (`# {{ One-line summary }}`)
    stays as-is unless `--title` is passed. Verifies the substitution path
    is `pr-merged`-gated, not blanket."""
    _stub_gh_in_path(
        monkeypatch,
        tmp_path,
        {
            "number": 99,
            "title": "feat: thing",
            "headRefName": "feat/thing",
            "mergeCommit": {"oid": "0" * 40},
        },
    )
    result = _draft(git_project, "decision", "--pr", "99", "--slug", "decision-test")
    assert result.exit_code == 0, result.output
    today = date.today().isoformat()
    body = (git_project / ".cortex" / "journal" / f"{today}-decision-test.md").read_text()
    # Decision template's `{{ One-line summary }}` H1 placeholder survives —
    # no `--pr`-driven rewrite for non-pr-merged types.
    assert re.search(r"^# \{\{[^}]+\}\}", body, re.MULTILINE), body[:200]


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
