"""Tests for the interactive follow-ups in `cortex init` (doctrine 0002).

`cortex init` is now a first-run wizard: when run on a TTY, after scaffolding
`.cortex/` it prompts to (1) append Cortex imports to CLAUDE.md, (2) same for
AGENTS.md, (3) add Cortex transient paths to `.gitignore`. Flags override
prompts; `--yes` accepts defaults; non-TTY without `--yes` skips all three
silently (preserving the pre-interactive scaffolding behavior). All behaviors
below are tested with the real filesystem (`tmp_path`) — no monkeypatched IO.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli


def _invoke(target: Path, *extra_args: str) -> str:
    """Run `cortex init --path <target> [flags]` and return stdout on success."""
    result = CliRunner().invoke(cli, ["init", "--path", str(target), *extra_args])
    assert result.exit_code == 0, result.output
    return result.output


def _write_claude_md(target: Path, content: str = "# Project\n\nSome intro.\n") -> Path:
    p = target / "CLAUDE.md"
    p.write_text(content)
    return p


def _write_agents_md(target: Path, content: str = "# Project\n\nSome intro.\n") -> Path:
    p = target / "AGENTS.md"
    p.write_text(content)
    return p


# --- --yes path -------------------------------------------------------------


def test_yes_appends_imports_to_claude_md(tmp_path: Path) -> None:
    claude = _write_claude_md(tmp_path)
    _invoke(tmp_path, "--yes")
    text = claude.read_text()
    assert "@.cortex/protocol.md" in text
    assert "@.cortex/state.md" in text
    # Prior content preserved.
    assert "Some intro." in text


def test_yes_appends_imports_to_agents_md(tmp_path: Path) -> None:
    agents = _write_agents_md(tmp_path)
    _invoke(tmp_path, "--yes")
    text = agents.read_text()
    assert "@.cortex/protocol.md" in text
    assert "@.cortex/state.md" in text


def test_yes_creates_or_updates_gitignore(tmp_path: Path) -> None:
    _invoke(tmp_path, "--yes")
    gi = (tmp_path / ".gitignore").read_text()
    assert ".cortex/.index.json" in gi
    assert ".cortex/pending/" in gi


def test_yes_is_idempotent_for_imports(tmp_path: Path) -> None:
    """Running twice must not duplicate the import block. This is the
    contract that makes re-running `cortex init --yes` safe."""
    claude = _write_claude_md(tmp_path)
    _invoke(tmp_path, "--yes")
    first_text = claude.read_text()
    # Rerun — use --force because SPEC_VERSION now exists.
    _invoke(tmp_path, "--yes", "--force")
    second_text = claude.read_text()
    # Second run notes "already imports" — no change.
    assert first_text == second_text
    assert second_text.count("@.cortex/protocol.md") == 1
    assert second_text.count("@.cortex/state.md") == 1


def test_yes_is_idempotent_for_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text(".cortex/.index.json\n")
    _invoke(tmp_path, "--yes")
    after_first = (tmp_path / ".gitignore").read_text()
    _invoke(tmp_path, "--yes", "--force")
    after_second = (tmp_path / ".gitignore").read_text()
    # Two lines total after both entries are ensured; pending/ added once.
    assert after_first.count(".cortex/.index.json") == 1
    assert after_second.count(".cortex/.index.json") == 1
    assert after_second.count(".cortex/pending/") == 1


def test_yes_notes_when_claude_md_already_imports(tmp_path: Path) -> None:
    """When CLAUDE.md already imports the protocol we print a note rather
    than silently re-appending or incorrectly claiming we modified the file."""
    content = "# Project\n\n@.cortex/protocol.md\n\n@.cortex/state.md\n"
    _write_claude_md(tmp_path, content)
    out = _invoke(tmp_path, "--yes")
    assert "already imports Cortex protocol" in out


# --- flag overrides ---------------------------------------------------------


def test_no_add_imports_claude_skips_claude(tmp_path: Path) -> None:
    claude = _write_claude_md(tmp_path)
    _invoke(tmp_path, "--yes", "--no-add-imports-claude")
    # CLAUDE.md untouched.
    assert "@.cortex/protocol.md" not in claude.read_text()


def test_no_gitignore_skips_gitignore(tmp_path: Path) -> None:
    _invoke(tmp_path, "--yes", "--no-gitignore")
    assert not (tmp_path / ".gitignore").exists()


def test_add_imports_claude_without_yes_still_runs(tmp_path: Path) -> None:
    """Explicit `--add-imports-claude` overrides the prompt even without
    `--yes` and even in a non-TTY environment."""
    claude = _write_claude_md(tmp_path)
    _invoke(tmp_path, "--add-imports-claude")
    assert "@.cortex/protocol.md" in claude.read_text()


# --- non-TTY preserves existing silent-scaffold behavior --------------------


def test_non_tty_without_yes_leaves_claude_untouched(tmp_path: Path) -> None:
    """CliRunner invokes with a non-TTY stdin; without --yes the wizard
    must not modify CLAUDE.md. This preserves the pre-doctrine-0002 contract
    for scripted / CI use."""
    claude = _write_claude_md(tmp_path)
    original = claude.read_text()
    _invoke(tmp_path)
    assert claude.read_text() == original


def test_non_tty_without_yes_leaves_agents_untouched(tmp_path: Path) -> None:
    agents = _write_agents_md(tmp_path)
    original = agents.read_text()
    _invoke(tmp_path)
    assert agents.read_text() == original


def test_non_tty_without_yes_leaves_gitignore_untouched(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    _invoke(tmp_path)
    assert (tmp_path / ".gitignore").read_text() == "node_modules/\n"


def test_non_tty_without_yes_does_not_create_gitignore(tmp_path: Path) -> None:
    _invoke(tmp_path)
    assert not (tmp_path / ".gitignore").exists()


# --- missing-file gating ---------------------------------------------------


def test_yes_does_not_create_claude_md_when_absent(tmp_path: Path) -> None:
    """If CLAUDE.md doesn't exist we do not prompt, and we don't create it.
    The wizard's job is to thread existing project config into Cortex, not
    to author CLAUDE.md from scratch."""
    _invoke(tmp_path, "--yes")
    assert not (tmp_path / "CLAUDE.md").exists()


def test_yes_does_not_create_agents_md_when_absent(tmp_path: Path) -> None:
    _invoke(tmp_path, "--yes")
    assert not (tmp_path / "AGENTS.md").exists()


# --- import placement ------------------------------------------------------


def test_imports_placed_after_existing_import_block(tmp_path: Path) -> None:
    """When CLAUDE.md already has `@<path>` imports, the Cortex imports land
    after the last one so they cluster with the existing block instead of
    scattering at the bottom of a long file."""
    content = (
        "# Project\n\n"
        "## Principles\n\n"
        "@principles/engineering-principles.md\n\n"
        "## Unrelated trailing section\n\n"
        "body.\n"
    )
    claude = _write_claude_md(tmp_path, content)
    _invoke(tmp_path, "--yes")
    text = claude.read_text()
    # Imports are present and the trailing section is still below them.
    protocol_idx = text.index("@.cortex/protocol.md")
    existing_idx = text.index("@principles/engineering-principles.md")
    trailing_idx = text.index("Unrelated trailing section")
    assert existing_idx < protocol_idx < trailing_idx


def test_imports_appended_at_end_when_no_existing_imports(tmp_path: Path) -> None:
    content = "# Project\n\nDescription.\n"
    claude = _write_claude_md(tmp_path, content)
    _invoke(tmp_path, "--yes")
    text = claude.read_text()
    # Original content still at the top; imports appended after.
    assert text.startswith("# Project")
    assert "@.cortex/protocol.md" in text
    assert text.index("Description.") < text.index("@.cortex/protocol.md")


# --- equivalent-command line -----------------------------------------------


def test_prints_equivalent_command(tmp_path: Path) -> None:
    out = _invoke(tmp_path, "--yes")
    assert "Equivalent to rerun:" in out
    assert "cortex init" in out
    assert "--yes" in out


def test_equivalent_command_reflects_no_flag(tmp_path: Path) -> None:
    _write_claude_md(tmp_path)
    out = _invoke(tmp_path, "--yes", "--no-add-imports-claude")
    # The rerun form must include --no-add-imports-claude so a scripter
    # copy-pasting it reproduces the same result.
    assert "--no-add-imports-claude" in out


# --- --local-only ----------------------------------------------------------


def test_local_only_gitignores_entire_cortex_dir(tmp_path: Path) -> None:
    """With --local-only the whole `.cortex/` directory is gitignored so a
    solo developer's journals, plans, and state stay off the shared repo."""
    _invoke(tmp_path, "--yes", "--local-only")
    gi = (tmp_path / ".gitignore").read_text()
    lines = {line.strip() for line in gi.splitlines()}
    assert ".cortex/" in lines
    # Default transient entries are NOT written — .cortex/ already subsumes
    # them and duplicating would be noise.
    assert ".cortex/.index.json" not in lines
    assert ".cortex/pending/" not in lines


def test_local_only_creates_gitignore_when_absent(tmp_path: Path) -> None:
    assert not (tmp_path / ".gitignore").exists()
    _invoke(tmp_path, "--yes", "--local-only")
    assert (tmp_path / ".gitignore").exists()


def test_local_only_preserves_existing_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("node_modules/\n")
    _invoke(tmp_path, "--yes", "--local-only")
    gi = (tmp_path / ".gitignore").read_text()
    assert "node_modules/" in gi
    assert ".cortex/" in gi


def test_local_only_is_idempotent(tmp_path: Path) -> None:
    _invoke(tmp_path, "--yes", "--local-only")
    first = (tmp_path / ".gitignore").read_text()
    _invoke(tmp_path, "--yes", "--local-only", "--force")
    second = (tmp_path / ".gitignore").read_text()
    assert first == second
    assert second.count(".cortex/") == 1


def test_local_only_short_circuits_gitignore_prompt(tmp_path: Path) -> None:
    """--local-only without --yes on a non-TTY must still write .gitignore.
    The flag itself is the affirmative answer; the prompt gating from
    --gitignore/--no-gitignore does not apply."""
    _invoke(tmp_path, "--local-only")
    gi = (tmp_path / ".gitignore").read_text()
    assert ".cortex/" in gi


def test_local_only_conflicts_with_no_gitignore(tmp_path: Path) -> None:
    """The two flags contradict: --local-only says "gitignore everything" and
    --no-gitignore says "touch nothing". Fail loudly rather than silently
    preferring one."""
    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--local-only", "--no-gitignore"]
    )
    assert result.exit_code == 2
    assert "conflict" in result.output.lower()
    assert not (tmp_path / ".cortex").exists()


def test_local_only_announces_in_output(tmp_path: Path) -> None:
    """The success message must state that `.cortex/` is now gitignored so
    the user understands the tradeoff they opted into (doctrine 0002 §5 —
    surface the consequence of the choice)."""
    out = _invoke(tmp_path, "--yes", "--local-only")
    assert "local-only" in out.lower()
    assert ".cortex/" in out


def test_equivalent_command_includes_local_only(tmp_path: Path) -> None:
    out = _invoke(tmp_path, "--yes", "--local-only")
    assert "--local-only" in out
    # --gitignore/--no-gitignore is suppressed because --local-only implies
    # the gitignore step; the rerun command stays minimal.
    assert "--gitignore" not in out
    assert "--no-gitignore" not in out


def test_local_only_skips_claude_imports_by_default(tmp_path: Path) -> None:
    """`--local-only` gitignores `.cortex/`; committing `@.cortex/...` imports
    into CLAUDE.md would leave downstream clones with dangling references.
    Default to skipping imports in local-only mode."""
    claude = _write_claude_md(tmp_path)
    _invoke(tmp_path, "--yes", "--local-only")
    assert "@.cortex/protocol.md" not in claude.read_text()
    assert "@.cortex/state.md" not in claude.read_text()


def test_local_only_skips_agents_imports_by_default(tmp_path: Path) -> None:
    agents = _write_agents_md(tmp_path)
    _invoke(tmp_path, "--yes", "--local-only")
    assert "@.cortex/protocol.md" not in agents.read_text()


def test_local_only_with_explicit_import_flag_honors_and_warns(tmp_path: Path) -> None:
    """Explicit `--add-imports-claude` wins over the local-only default, but
    emits a warning so the dangling-import tradeoff is visible."""
    claude = _write_claude_md(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "init",
            "--path",
            str(tmp_path),
            "--yes",
            "--local-only",
            "--add-imports-claude",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "@.cortex/protocol.md" in claude.read_text()
    # Warning is on stderr; CliRunner merges stderr into output by default.
    assert "dangling" in result.output.lower()


def test_local_only_suppresses_import_next_step_hint(tmp_path: Path) -> None:
    """The "Next steps" block suggests importing `@.cortex/...` into
    AGENTS.md/CLAUDE.md when those files don't exist. Under --local-only
    that suggestion would reintroduce the dangling-import problem — suppress
    it so the next-steps output stays consistent with the flag's intent."""
    out = _invoke(tmp_path, "--yes", "--local-only")
    # No CLAUDE.md or AGENTS.md exists in tmp_path; default would print the
    # import suggestion as step 2. Under --local-only it must be gone.
    assert "Import `@.cortex/protocol.md`" not in out


def test_default_next_step_hint_still_present(tmp_path: Path) -> None:
    """Regression guard: without --local-only, the import suggestion still
    prints when CLAUDE.md/AGENTS.md aren't imported. This is the contract
    that introduces new users to the imports."""
    out = _invoke(tmp_path, "--yes")
    assert "Import `@.cortex/protocol.md`" in out


def _git_init(target: Path) -> None:
    """Initialise a minimal git repo at `target` so `git ls-files` can run."""
    import subprocess  # noqa: PLC0415  local to keep top-level imports minimal

    for cmd in (
        ["git", "init", "-q"],
        ["git", "-C", str(target), "config", "user.email", "test@example.com"],
        ["git", "-C", str(target), "config", "user.name", "Test"],
        ["git", "-C", str(target), "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(target), check=True, capture_output=True)


def _git_add_commit(target: Path, *paths: str) -> None:
    import subprocess  # noqa: PLC0415

    subprocess.run(
        ["git", "-C", str(target), "add", *paths],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(target), "commit", "-q", "-m", "seed"],
        check=True,
        capture_output=True,
    )


def test_local_only_warns_when_cortex_already_tracked(tmp_path: Path) -> None:
    """If `.cortex/` is already in git's index, appending `.cortex/` to
    .gitignore does NOT untrack those files — git continues to publish
    them. Warn with the exact `git rm --cached` remediation so the user
    doesn't believe the "not published" promise falsely.

    CliRunner doesn't cd into tmp_path, so `--path <tmp_path>` always
    differs from cwd — the emitted remediation is the path-aware form."""
    _git_init(tmp_path)
    # Pre-create a .cortex/ file and commit it so the tree mirrors the
    # "existing repo adopting Cortex" scenario.
    (tmp_path / ".cortex").mkdir()
    (tmp_path / ".cortex" / "state.md").write_text("# seed\n")
    _git_add_commit(tmp_path, ".cortex/state.md")

    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--yes", "--local-only", "--force"]
    )
    assert result.exit_code == 0, result.output
    assert "already tracked" in result.output
    assert f"git -C {tmp_path.resolve()} rm --cached -r .cortex/" in result.output


def test_local_only_remediation_is_plain_when_target_equals_cwd(tmp_path: Path) -> None:
    """When `--path` is not set (or equals cwd), the remediation command
    should be the plain `git rm --cached -r .cortex/` without the `-C`
    prefix — the common case deserves the readable form."""
    import os  # noqa: PLC0415

    _git_init(tmp_path)
    (tmp_path / ".cortex").mkdir()
    (tmp_path / ".cortex" / "state.md").write_text("# seed\n")
    _git_add_commit(tmp_path, ".cortex/state.md")

    # chdir into tmp_path so Path.cwd() matches the default --path target.
    # Restore cwd after the test regardless of outcome.
    prior_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = CliRunner().invoke(
            cli, ["init", "--yes", "--local-only", "--force"]
        )
    finally:
        os.chdir(prior_cwd)

    assert result.exit_code == 0, result.output
    assert "already tracked" in result.output
    # Plain form (no `-C`).
    assert "      git rm --cached -r .cortex/" in result.output
    assert "git -C" not in result.output


def test_local_only_no_warning_when_cortex_untracked(tmp_path: Path) -> None:
    """Fresh-init case: `.cortex/` was created by `cortex init` and never
    committed, so there's nothing to untrack. The warning must stay
    silent — false positives here would train users to ignore it."""
    _git_init(tmp_path)
    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--yes", "--local-only"]
    )
    assert result.exit_code == 0, result.output
    assert "already tracked" not in result.output
    assert "will not be published" in result.output


def test_local_only_warns_when_claude_md_already_imports(tmp_path: Path) -> None:
    """If CLAUDE.md already imports `@.cortex/protocol.md` from a prior
    team-shared init, converting to local-only leaves those imports in the
    published file pointing at a now-gitignored directory. Warn with
    specific remediation so the user knows to remove the lines."""
    content = "# Project\n\n@.cortex/protocol.md\n\n@.cortex/state.md\n"
    _write_claude_md(tmp_path, content)
    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--yes", "--local-only"]
    )
    assert result.exit_code == 0, result.output
    assert "CLAUDE.md already import" in result.output
    assert "Remove the" in result.output
    # Success claim must NOT fire when dangling-import remediation is outstanding.
    assert "will not be published" not in result.output


def test_local_only_warns_when_agents_md_already_imports(tmp_path: Path) -> None:
    content = "# Agents\n\n@.cortex/protocol.md\n"
    _write_agents_md(tmp_path, content)
    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--yes", "--local-only"]
    )
    assert result.exit_code == 0, result.output
    assert "AGENTS.md already import" in result.output


def test_local_only_no_import_warning_when_files_clean(tmp_path: Path) -> None:
    """Regression: a CLAUDE.md without `@.cortex/...` imports must not trip
    the warning — false positives would train users to ignore it."""
    _write_claude_md(tmp_path, "# Project\n\n## Overview\n\nNo imports.\n")
    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--yes", "--local-only"]
    )
    assert result.exit_code == 0, result.output
    assert "already import" not in result.output
    assert "will not be published" in result.output


def test_local_only_detects_tracked_files_in_monorepo_subdir(tmp_path: Path) -> None:
    """Monorepo case: `.git` lives at the parent repo root, `cortex init`
    runs against a subdirectory. The tracked-files check must still fire —
    `git ls-files` is authoritative because it walks up on its own."""
    import subprocess  # noqa: PLC0415

    # Parent repo at tmp_path, subdir at tmp_path/packages/sub which already
    # has a committed `.cortex/` file (simulating an existing shared mode).
    _git_init(tmp_path)
    subdir = tmp_path / "packages" / "sub"
    subdir.mkdir(parents=True)
    (subdir / ".cortex").mkdir()
    (subdir / ".cortex" / "state.md").write_text("# seed\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "packages/sub/.cortex/state.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed subdir"],
        check=True,
        capture_output=True,
    )
    # Sanity: the subdir does NOT have its own .git — this is the case the
    # prior implementation (which short-circuited on `.git` existence) missed.
    assert not (subdir / ".git").exists()

    result = CliRunner().invoke(
        cli, ["init", "--path", str(subdir), "--yes", "--local-only", "--force"]
    )
    assert result.exit_code == 0, result.output
    assert "already tracked" in result.output
    # Path-aware remediation: when --path differs from cwd, the untrack
    # command must anchor to the target (via `git -C <target>`), not rely
    # on the caller's cwd. Otherwise a user who runs `cortex init --path
    # packages/sub` from the repo root and copy-pastes the remediation
    # would untrack the wrong (or nonexistent) `.cortex/`.
    assert f"git -C {subdir.resolve()} rm --cached -r .cortex/" in result.output


def test_local_only_warns_when_git_check_fails(tmp_path: Path, monkeypatch) -> None:
    """When `git ls-files` fails for an unexpected reason (not a "not a
    repo" exit — that's a known-safe state — but a genuine subprocess
    error or corrupted repo), we cannot know whether `.cortex/` is
    tracked. Emit an explicit uncertainty warning rather than falling
    through to the "will not be published" success path — false
    assurance is worse than no answer.

    CliRunner doesn't cd into tmp_path so `--path <tmp_path>` differs
    from cwd; the emitted remediation must use the path-aware form
    (`git -C <target>`) so a monorepo user running from the repo root
    doesn't inspect or untrack the wrong `.cortex/`."""
    from cortex.commands import init as init_mod  # noqa: PLC0415

    def fake_tracked(_path):  # pragma: no cover - simple stub
        return None  # simulate the "check failed" branch

    monkeypatch.setattr(init_mod, "_tracked_cortex_files", fake_tracked)
    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--yes", "--local-only"]
    )
    assert result.exit_code == 0, result.output
    assert "could not verify" in result.output
    # Path-aware form: shell-quoted `git -C <target> ls-files .cortex`.
    assert f"git -C {tmp_path.resolve()} ls-files .cortex" in result.output
    assert f"git -C {tmp_path.resolve()} rm --cached -r .cortex/" in result.output
    # No false "will not be published" claim when we don't know the truth.
    assert "will not be published" not in result.output


def test_local_only_check_failed_uses_plain_form_when_target_equals_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: when cwd matches the target, the uncertainty warning
    drops the `-C` prefix for readability — parallel to the tracked
    branch's plain-form behavior."""
    import os  # noqa: PLC0415
    from cortex.commands import init as init_mod  # noqa: PLC0415

    monkeypatch.setattr(init_mod, "_tracked_cortex_files", lambda _p: None)

    prior_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = CliRunner().invoke(
            cli, ["init", "--yes", "--local-only"]
        )
    finally:
        os.chdir(prior_cwd)

    assert result.exit_code == 0, result.output
    assert "could not verify" in result.output
    assert "      git ls-files .cortex" not in result.output  # no leading indent form
    assert "`git ls-files .cortex`" in result.output  # plain form, backticks
    assert "git -C" not in result.output


def test_local_only_remediation_quotes_paths_with_spaces(tmp_path: Path) -> None:
    """If `--path` contains spaces, the `git -C <path>` remediation must
    shell-quote the path so a copy-paste into a terminal still untracks
    the right directory instead of parsing the path as multiple args."""
    import subprocess  # noqa: PLC0415

    parent = tmp_path / "My Project"
    parent.mkdir()
    _git_init(parent)
    (parent / ".cortex").mkdir()
    (parent / ".cortex" / "state.md").write_text("# seed\n")
    subprocess.run(
        ["git", "-C", str(parent), "add", ".cortex/state.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(parent), "commit", "-q", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    result = CliRunner().invoke(
        cli, ["init", "--path", str(parent), "--yes", "--local-only", "--force"]
    )
    assert result.exit_code == 0, result.output
    # shlex.quote wraps paths containing spaces in single quotes.
    assert f"'{parent.resolve()}'" in result.output
    # And the quoted path is used in the remediation command, not a raw
    # space-containing string that would be mis-parsed by the shell.
    assert f"git -C '{parent.resolve()}' rm --cached -r .cortex/" in result.output


def test_local_only_survives_non_git_project(tmp_path: Path) -> None:
    """`--local-only` must not fail when the project isn't a git repo —
    the tracked-files check is best-effort advice, not a prerequisite."""
    # No `git init`; plain tmp_path.
    result = CliRunner().invoke(
        cli, ["init", "--path", str(tmp_path), "--yes", "--local-only"]
    )
    assert result.exit_code == 0, result.output
    assert "already tracked" not in result.output


def test_default_init_still_commits_cortex_dir(tmp_path: Path) -> None:
    """Regression guard: the SPEC default is that `.cortex/` is committed
    team-shared memory. Without --local-only, the whole directory must NOT
    appear in .gitignore."""
    _invoke(tmp_path, "--yes")
    lines = {line.strip() for line in (tmp_path / ".gitignore").read_text().splitlines()}
    assert ".cortex/" not in lines
    assert ".cortex/.index.json" in lines
    assert ".cortex/pending/" in lines
