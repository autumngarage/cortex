"""Tests for `cortex init` — scaffolding a SPEC-v0.5.0-conformant `.cortex/`.

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
        "README.md",
        "journal/decision.md",
        "journal/incident.md",
        "journal/plan-transition.md",
        "journal/sentinel-cycle.md",
        "journal/pr-merged.md",
        "journal/release.md",
        "doctrine/candidate.md",
        "digest/monthly.md",
        "digest/quarterly.md",
        "plans/template.md",
    }
    found = {str(p.relative_to(templates_dir)) for p in templates_dir.rglob("*.md")}
    assert expected == found


def test_init_scaffolds_plans_template(tmp_path: Path) -> None:
    """A first-class plan template ships under templates/plans/ so authors
    don't have to infer required frontmatter (Goal-hash, Updated-by, Cites)
    and required sections (Why grounding / Approach / Success Criteria /
    Work items) from SPEC.md or other plans by reading."""
    _run_init(tmp_path)
    template_path = tmp_path / ".cortex" / "templates" / "plans" / "template.md"
    assert template_path.is_file()
    text = template_path.read_text()
    # Required Plan frontmatter fields (SPEC § 3.4) are present as placeholders.
    for field in ("Status:", "Written:", "Author:", "Goal-hash:", "Updated-by:", "Cites:"):
        assert field in text, f"plan template missing frontmatter field {field}"
    # Required section headings (SPEC § 3.4) — exact literals, since
    # `cortex doctor`'s section check is exact-match.
    for heading in (
        "## Why (grounding)",
        "## Approach",
        "## Success Criteria",
        "## Work items",
        "## Follow-ups (deferred)",
        "## Known limitations at exit",
    ):
        assert heading in text, f"plan template missing section heading {heading!r}"


def test_init_plans_template_frontmatter_parses(tmp_path: Path) -> None:
    """The plan template's YAML-ish frontmatter is valid input for the
    in-repo frontmatter parser used by `cortex doctor`."""
    from cortex.frontmatter import parse_frontmatter

    _run_init(tmp_path)
    text = (tmp_path / ".cortex" / "templates" / "plans" / "template.md").read_text()
    frontmatter, body = parse_frontmatter(text)
    assert frontmatter is not None, "plan template frontmatter failed to parse"
    # Frontmatter must be non-empty and contain the Plan-required scalars.
    for field in ("Status", "Written", "Author", "Goal-hash", "Updated-by", "Cites"):
        assert field in frontmatter, f"parsed frontmatter missing {field}"
    # And the body must carry an H1 title so `cortex doctor`'s Goal-hash
    # recompute has something to hash.
    assert body.lstrip().startswith("#"), "plan template body missing H1"


def test_init_creates_cortex_readme(tmp_path: Path) -> None:
    """The `.cortex/README.md` orientation doc lands at the top level so
    humans arriving via a file browser have a map of the six layers and
    the hand-edit rules before they need to open SPEC.md."""
    _run_init(tmp_path)
    readme = tmp_path / ".cortex" / "README.md"
    assert readme.is_file()
    text = readme.read_text()
    # It names each of the six layers and points at the Protocol + doctor.
    for layer in ("doctrine/", "journal/", "plans/", "map.md", "state.md", "procedures/", "templates/"):
        assert layer in text, f"README missing reference to {layer}"
    assert "@.cortex/protocol.md" in text
    assert "cortex doctor" in text


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
        # Stub body steers the user toward the relevant refresh command while
        # keeping the opening phrase consistent across both stubs.
        assert "Hand-authored placeholder" in content, f"{layer}.md missing hand-editable guidance"
        assert f"cortex refresh-{layer}" in content, f"{layer}.md missing pointer at refresh-{layer}"


def test_init_stub_generator_tracks_current_version(tmp_path: Path) -> None:
    # Regression for v0.2.0 release review: the Generator: line in
    # scaffolded map.md / state.md used to hardcode "cortex init v0.1.0",
    # which silently lied whenever __version__ advanced. Derive from the
    # live package version so every release's stubs are truthful.
    from cortex import __version__ as current_version

    _run_init(tmp_path)
    expected = f"Generator: cortex init v{current_version}"
    for layer in ("map", "state"):
        content = (tmp_path / ".cortex" / f"{layer}.md").read_text()
        assert expected in content, (
            f"{layer}.md Generator field does not reflect cortex.__version__ ({current_version}); "
            f"got:\n{content}"
        )


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


def test_init_refuses_when_cortex_has_content_but_no_spec_version(tmp_path: Path) -> None:
    """Partial/hand-authored .cortex/ without SPEC_VERSION is ambiguous state.

    Writing a fresh scaffold on top would leave a mix of shipped files and
    pre-existing content under a 'conformant' SPEC_VERSION marker — false
    advertising. init refuses unless --force is passed.
    """
    cortex = tmp_path / ".cortex"
    cortex.mkdir()
    (cortex / "some-preexisting-file.md").write_text("# not a scaffold file")

    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "already contains content" in result.output
    # The preexisting file is untouched.
    assert (cortex / "some-preexisting-file.md").read_text() == "# not a scaffold file"


def test_init_force_proceeds_when_cortex_has_content_but_no_spec_version(tmp_path: Path) -> None:
    cortex = tmp_path / ".cortex"
    cortex.mkdir()
    preexisting = cortex / "some-preexisting-file.md"
    preexisting.write_text("# not a scaffold file")

    _run_init(tmp_path, "--force")
    # Scaffold is now populated.
    assert (cortex / "SPEC_VERSION").read_text().strip() == CURRENT_SPEC_VERSION
    # Preexisting non-scaffold content at the top level is left alone.
    assert preexisting.read_text() == "# not a scaffold file"


def test_init_is_listed_as_subcommand(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
