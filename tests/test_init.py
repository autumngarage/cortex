"""Tests for `cortex init` — scaffolding a SPEC-v0.3.1-dev-conformant `.cortex/`.

All tests operate on a `tmp_path` fixture (real filesystem, no mocks). They
invoke the real click entrypoint via `CliRunner`, then assert on file-system
state of the scaffolded directory.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import CURRENT_SPEC_VERSION, SCAFFOLD_SUBDIRS


def _run_init(target: Path, *extra_args: str) -> None:
    result = CliRunner().invoke(cli, ["init", "--path", str(target), *extra_args])
    assert result.exit_code == 0, result.output


def test_init_creates_spec_version_file(tmp_path: Path) -> None:
    _run_init(tmp_path)
    spec_version = (tmp_path / ".cortex" / "SPEC_VERSION").read_text().strip()
    assert spec_version == CURRENT_SPEC_VERSION


def test_init_copies_protocol_markdown(tmp_path: Path) -> None:
    _run_init(tmp_path)
    protocol = (tmp_path / ".cortex" / "protocol.md").read_text()
    assert protocol.startswith("# Cortex Protocol")
    assert "Tier 1" in protocol


def test_init_copies_full_templates_tree(tmp_path: Path) -> None:
    _run_init(tmp_path)
    templates_dir = tmp_path / ".cortex" / "templates"
    expected = {
        "journal/decision.md",
        "journal/incident.md",
        "journal/plan-transition.md",
        "journal/sentinel-cycle.md",
        "journal/pr-merged.md",
        "doctrine/candidate.md",
        "digest/monthly.md",
        "digest/quarterly.md",
    }
    found = {str(p.relative_to(templates_dir)) for p in templates_dir.rglob("*.md")}
    assert expected == found


def test_init_creates_all_required_subdirs(tmp_path: Path) -> None:
    _run_init(tmp_path)
    for sub in SCAFFOLD_SUBDIRS:
        subdir = tmp_path / ".cortex" / sub
        assert subdir.is_dir(), f"missing subdir: {sub}"
        assert (subdir / ".gitkeep").exists(), f"missing .gitkeep in {sub}"


def test_init_stubs_map_and_state_with_seven_fields(tmp_path: Path) -> None:
    _run_init(tmp_path)
    for layer in ("map", "state"):
        content = (tmp_path / ".cortex" / f"{layer}.md").read_text()
        for field in ("Generated:", "Generator:", "Sources:", "Corpus:", "Omitted:", "Incomplete:", "Conflicts-preserved:"):
            assert field in content, f"missing {field} in {layer}.md"
        assert "pending Phase C synthesis" in content


def test_init_refuses_second_invocation_without_force(tmp_path: Path) -> None:
    _run_init(tmp_path)
    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_init_force_overwrites_scaffold_files(tmp_path: Path) -> None:
    _run_init(tmp_path)
    # Simulate user tampering with a scaffold file.
    spec_version_file = tmp_path / ".cortex" / "SPEC_VERSION"
    spec_version_file.write_text("tampered\n")

    _run_init(tmp_path, "--force")
    assert spec_version_file.read_text().strip() == CURRENT_SPEC_VERSION


def test_init_force_preserves_user_authored_doctrine(tmp_path: Path) -> None:
    _run_init(tmp_path)
    doctrine_entry = tmp_path / ".cortex" / "doctrine" / "0001-why-this-project-exists.md"
    user_content = "# 0001 — Why this project exists\n\n> hand-authored by the user"
    doctrine_entry.write_text(user_content)

    _run_init(tmp_path, "--force")
    # User content under doctrine/ is never touched by init, with or without --force.
    assert doctrine_entry.read_text() == user_content


def test_init_fails_for_nonexistent_target(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    result = CliRunner().invoke(cli, ["init", "--path", str(missing)])
    assert result.exit_code == 2
    assert "does not exist" in result.output


def test_init_is_listed_as_subcommand(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
