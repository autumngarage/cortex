"""Regression tests for cortex#139, #142, #143 — init scaffolding fixes."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli

# ---------------------------------------------------------------------------
# #142 — --yes must not claim "will prompt for classification"
# ---------------------------------------------------------------------------


def _unknown_file_content() -> str:
    """A markdown file with H1+H2 structure (triggers _looks_load_bearing)."""
    return "# Performance notes\n\n## Overview\n\nLoad-bearing content for test.\n"


def test_init_yes_does_not_say_will_prompt_for_unknown_pattern(tmp_path: Path) -> None:
    """Regression for cortex#142: when --yes is passed and unknown-pattern
    files are present, the scan summary must not say 'will prompt for
    classification'. The user has already opted into accepting defaults;
    the output should reflect the actual decision (auto-classified).
    """
    # PERFORMANCE.md with H1+H2 passes _looks_load_bearing → classified unknown.
    (tmp_path / "PERFORMANCE.md").write_text(_unknown_file_content())

    result = CliRunner().invoke(cli, ["init", "--yes", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output

    # The false promise must not appear.
    assert "will prompt" not in result.output.lower(), (
        "init --yes output should not contain 'will prompt' when --yes is passed.\n"
        f"Output:\n{result.output}"
    )


def test_init_yes_mentions_auto_classified_for_unknown_pattern(tmp_path: Path) -> None:
    """When --yes auto-classifies an unknown file, the summary should say
    so explicitly so the user understands the decision was automatic.
    """
    (tmp_path / "PERFORMANCE.md").write_text(_unknown_file_content())

    result = CliRunner().invoke(cli, ["init", "--yes", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output

    assert "auto-classified" in result.output.lower() or "map_ref" in result.output.lower(), (
        "init --yes output should indicate the file was auto-classified.\n"
        f"Output:\n{result.output}"
    )


def test_init_interactive_still_says_will_prompt(tmp_path: Path) -> None:
    """On a non-TTY invocation without --yes, no interactive steps run.
    The scan summary should still print the 'will prompt' label (though
    the prompts never fire) — so the phrasing difference is visible to
    authors reviewing the two code paths.

    Non-TTY + no --yes = scan summary printed, absorb steps silently skipped.
    The 'will prompt' message comes from _print_scan_summary which always runs.
    """
    (tmp_path / "PERFORMANCE.md").write_text(_unknown_file_content())

    # No --yes, no TTY (CliRunner is non-TTY by default)
    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output

    # Non-TTY without --yes: will_prompt is False AND assume_yes is False,
    # so the original "will prompt for classification" label should appear.
    assert "will prompt for classification" in result.output, (
        "Non-TTY init without --yes should still show 'will prompt for classification'.\n"
        f"Output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# #143 — config.toml must be scaffolded
# ---------------------------------------------------------------------------


def test_init_scaffolds_config_toml(tmp_path: Path) -> None:
    """Regression for cortex#143: cortex init must write .cortex/config.toml."""
    result = CliRunner().invoke(cli, ["init", "--yes", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output

    config_toml = tmp_path / ".cortex" / "config.toml"
    assert config_toml.exists(), (
        ".cortex/config.toml was not created by cortex init.\n"
        f"Output:\n{result.output}"
    )


def test_init_config_toml_has_audit_instructions_section(tmp_path: Path) -> None:
    """The scaffolded config.toml must reference [audit-instructions] so the
    feature is discoverable without prior knowledge of the docs.
    """
    CliRunner().invoke(cli, ["init", "--yes", "--path", str(tmp_path)])

    config_text = (tmp_path / ".cortex" / "config.toml").read_text()
    assert "audit-instructions" in config_text, (
        "Scaffolded config.toml must mention [audit-instructions].\n"
        f"Content:\n{config_text}"
    )


def test_init_config_toml_not_overwritten_on_force(tmp_path: Path) -> None:
    """--force re-scaffolds protocol.md / templates but must NOT overwrite a
    user-customised config.toml (they may have added real keys).
    """
    CliRunner().invoke(cli, ["init", "--yes", "--path", str(tmp_path)])

    config_toml = tmp_path / ".cortex" / "config.toml"
    sentinel = "# my custom config\n"
    config_toml.write_text(sentinel)

    CliRunner().invoke(cli, ["init", "--yes", "--force", "--path", str(tmp_path)])

    assert config_toml.read_text() == sentinel, (
        "--force should not overwrite an existing .cortex/config.toml."
    )


def test_init_next_steps_mentions_config_toml(tmp_path: Path) -> None:
    """cortex init Next-steps output should point the user at config.toml so
    the [audit-instructions] feature is discoverable without prior knowledge.
    """
    result = CliRunner().invoke(cli, ["init", "--yes", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output

    assert "config.toml" in result.output, (
        "init Next-steps should mention config.toml.\n"
        f"Output:\n{result.output}"
    )
