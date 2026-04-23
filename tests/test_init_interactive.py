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


def test_default_init_still_commits_cortex_dir(tmp_path: Path) -> None:
    """Regression guard: the SPEC default is that `.cortex/` is committed
    team-shared memory. Without --local-only, the whole directory must NOT
    appear in .gitignore."""
    _invoke(tmp_path, "--yes")
    lines = {line.strip() for line in (tmp_path / ".gitignore").read_text().splitlines()}
    assert ".cortex/" not in lines
    assert ".cortex/.index.json" in lines
    assert ".cortex/pending/" in lines
