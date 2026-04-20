"""Tests for `cortex init` scan-and-absorb upgrade.

Each test operates on a real ``tmp_path`` (no mocked filesystem) and either
calls ``scan_project`` directly to assert classifier behavior or invokes the
full ``cortex init`` CLI via ``CliRunner`` to exercise the absorb pipeline
end-to-end. The CliRunner runs in a non-TTY environment, so absorb-step
behavior under TTY is exercised via ``--yes`` (which the wizard accepts as
"all defaults") rather than feeding stdin to the prompts directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.frontmatter import parse_frontmatter
from cortex.init_scan import scan_project


def _git_init(path: Path) -> None:
    """Initialise a real git repo at ``path`` so ``git check-ignore`` can run.

    We need a real repo (not a marker file) because ``git check-ignore``
    refuses to operate outside of one. Tests that depend on .gitignore-aware
    skipping must call this on their tmp_path.
    """
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    # Touchstone discipline: configure git identity locally so any commit
    # operations during test setup don't fall over on uninitialised global
    # config. Tests don't actually commit but having identity set is cheap.
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test User"], check=True)


# --- scan_project classifier tests ----------------------------------------


def test_scan_finds_principles_dir(tmp_path: Path) -> None:
    """Anything under ``principles/*.md`` is classified as Doctrine."""
    (tmp_path / "principles").mkdir()
    (tmp_path / "principles" / "engineering.md").write_text("# Engineering\n\n## A\n")
    (tmp_path / "principles" / "git-workflow.md").write_text("# Git workflow\n")

    result = scan_project(tmp_path)
    doctrine = result.by_category("doctrine")
    assert {f.relative for f in doctrine} == {
        "principles/engineering.md",
        "principles/git-workflow.md",
    }


def test_scan_skips_node_modules(tmp_path: Path) -> None:
    """Files under ``node_modules/`` are never surfaced — protects against
    npm packages literally named "doctrine" being absorbed as Cortex
    Doctrine entries (vesper site/node_modules/doctrine regression)."""
    nested = tmp_path / "site" / "node_modules" / "doctrine"
    nested.mkdir(parents=True)
    (nested / "decisions.md").write_text("# JS doctrine package readme\n")

    result = scan_project(tmp_path)
    assert all("node_modules" not in f.relative for f in result.findings)


def test_scan_respects_gitignore(tmp_path: Path) -> None:
    """Files git already ignores are not Cortex's business — vendor/ etc."""
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("vendor/\n")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "decisions").mkdir()
    (tmp_path / "vendor" / "decisions" / "x.md").write_text("# vendored decision\n")

    result = scan_project(tmp_path)
    assert all("vendor/" not in f.relative for f in result.findings)


def test_claude_and_agents_md_not_in_unknowns(tmp_path: Path) -> None:
    """Top-level CLAUDE.md and AGENTS.md are handled by the import-injection
    and unscoped-constraint flows in ``commands/init.py`` /
    ``validation.py``. Listing them as "unknown" makes the wizard
    double-prompt (sigint/vesper dogfood regression). Real unknowns
    (e.g. ``MYSTERY_DOC.md``) still surface as before.
    """
    (tmp_path / "CLAUDE.md").write_text("# Claude instructions\n\n## Section\n\n" + "x" * 1024)
    (tmp_path / "AGENTS.md").write_text("# Agents instructions\n\n## Section\n\n" + "x" * 1024)
    (tmp_path / "MYSTERY_DOC.md").write_text("# Mystery\n\n## Body\n\n" + "x" * 1024)

    result = scan_project(tmp_path)
    unknowns = {f.relative for f in result.by_category("unknown")}
    assert unknowns == {"MYSTERY_DOC.md"}, (
        f"expected only MYSTERY_DOC.md as unknown, got {unknowns}"
    )


def test_toolchain_config_dirs_skipped(tmp_path: Path) -> None:
    """Markdown files inside toolchain/agent config directories
    (``.sentinel/``, ``.cortex/``, ``.claude/``, ``.github/``) are
    config for OTHER tools, not project content. The sigint scan
    surfaced ``.sentinel/backlog.md``, ``.claude/loop.md``, and
    ``.github/pull_request_template.md`` as unknown candidates — they
    must be excluded entirely (no Doctrine, Plan, Map, Reference, or
    Unknown finding for any of them).
    """
    for d in (".sentinel", ".cortex", ".claude", ".github"):
        (tmp_path / d).mkdir()
    (tmp_path / ".sentinel" / "foo.md").write_text("# foo\n\n## body\n\n" + "x" * 1024)
    (tmp_path / ".cortex" / "bar.md").write_text("# bar\n\n## body\n\n" + "x" * 1024)
    (tmp_path / ".claude" / "baz.md").write_text("# baz\n\n## body\n\n" + "x" * 1024)
    (tmp_path / ".github" / "qux.md").write_text("# qux\n\n## body\n\n" + "x" * 1024)

    result = scan_project(tmp_path)
    surfaced = {f.relative for f in result.findings}
    forbidden = {
        ".sentinel/foo.md",
        ".cortex/bar.md",
        ".claude/baz.md",
        ".github/qux.md",
    }
    leaked = surfaced & forbidden
    assert not leaked, f"toolchain config files leaked into scan: {leaked}"


def test_migration_md_classified_as_plan(tmp_path: Path) -> None:
    """Migration docs (sigint's ``agent/COLLECTOR_MIGRATION.md``) are
    typically transient plans — one-shot work to move between systems.
    Without a ``Status: shipped`` marker they should land in the Plan
    bucket, not Unknown.
    """
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "COLLECTOR_MIGRATION.md").write_text(
        "# Collector Migration\n\nMove the collector from system A to system B.\n"
    )

    result = scan_project(tmp_path)
    plans = result.by_category("plan")
    assert any(f.relative == "agent/COLLECTOR_MIGRATION.md" for f in plans), (
        f"expected migration doc as Plan, got {[f.relative for f in plans]}; "
        f"unknowns: {[f.relative for f in result.by_category('unknown')]}"
    )


def test_migration_md_with_shipped_marker_demoted_to_reference(tmp_path: Path) -> None:
    """A migration doc with ``Status: shipped`` in its first 30 lines
    flows through the existing ``_looks_shipped`` heuristic and lands in
    reference, not plan. Belt-and-braces: adding the *MIGRATION*.md
    pattern doesn't risk wrongly importing closed work as an active plan.
    """
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "COLLECTOR_MIGRATION.md").write_text(
        "---\nStatus: shipped\n---\n\n# Collector Migration\n\nDone.\n"
    )

    result = scan_project(tmp_path)
    plans = result.by_category("plan")
    references = result.by_category("reference")
    assert not any(f.relative == "agent/COLLECTOR_MIGRATION.md" for f in plans), (
        f"shipped migration leaked into Plan: {[f.relative for f in plans]}"
    )
    demoted = [f for f in references if f.relative == "agent/COLLECTOR_MIGRATION.md"]
    assert demoted, "expected shipped migration in reference category"
    assert demoted[0].is_demoted_plan, "demoted_from metadata not preserved"


def test_scan_demotes_shipped_plan(tmp_path: Path) -> None:
    """A Plan candidate with ``Status: shipped`` in its head is demoted to reference."""
    (tmp_path / "ROADMAP.md").write_text(
        "---\nStatus: shipped\n---\n\n# Roadmap\n\nDone-and-dusted plan.\n"
    )

    result = scan_project(tmp_path)
    plans = result.by_category("plan")
    references = result.by_category("reference")
    assert not plans, f"expected ROADMAP.md to be demoted, found {[f.relative for f in plans]}"
    demoted = [f for f in references if f.relative == "ROADMAP.md"]
    assert demoted, "expected ROADMAP.md to land in reference category"
    assert demoted[0].is_demoted_plan, "demoted_from metadata not preserved"


# --- Doctrine seeder tests ------------------------------------------------


def test_doctrine_seeder_one_per_source(tmp_path: Path) -> None:
    """A multi-section principles file produces ONE Doctrine entry, not N.

    The brief's mirror-source-shape rule: an 8-principle file is one source
    file in the project, so the imported Doctrine has one entry pointing at
    the source. The source remains canonical text; we don't extract H2s as
    individual Doctrine entries.
    """
    (tmp_path / "principles").mkdir()
    body = "# Engineering Principles\n\n"
    for i in range(1, 9):
        body += f"## Principle {i}\n\nDescription.\n\n"
    (tmp_path / "principles" / "engineering-principles.md").write_text(body)

    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output

    doctrine_files = sorted((tmp_path / ".cortex" / "doctrine").glob("*.md"))
    # Exactly one Doctrine entry, even though the source has 8 H2s.
    assert len(doctrine_files) == 1
    text = doctrine_files[0].read_text()
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["Imported-from"] == "principles/engineering-principles.md"


# --- Plan seeder tests ----------------------------------------------------


def test_plan_seeder_extracts_h1_stubs_rest(tmp_path: Path) -> None:
    """Plan source with H1 ``Build the thing`` produces an entry with that
    Goal, Goal-hash recomputed via SPEC § 4.9, and a stubbed Success Criteria
    body with a ``[ ] Hand-author from <source>`` checklist."""
    (tmp_path / "ROADMAP.md").write_text("# Build the thing\n\nSome content.\n")

    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output

    plan_path = tmp_path / ".cortex" / "plans" / "roadmap.md"
    assert plan_path.exists(), result.output
    text = plan_path.read_text()
    frontmatter, body = parse_frontmatter(text)
    assert frontmatter["Goal"] == "Build the thing"
    assert frontmatter["Imported-from"] == "ROADMAP.md"
    # Goal-hash matches the spec normalization for "build the thing".
    from cortex.goal_hash import normalize_goal_hash
    assert frontmatter["Goal-hash"] == normalize_goal_hash("Build the thing")
    # Success Criteria section is stubbed as a TODO referencing the source.
    assert "## Success Criteria" in body
    assert "Hand-author measurable success criteria from `ROADMAP.md`" in body
    # Body H1 echoes the imported title so cortex doctor's recompute aligns.
    assert "# Build the thing" in body


# --- state.md Sources enrichment tests ------------------------------------


def test_state_md_sources_populated(tmp_path: Path) -> None:
    """Scan finds README.md + SETUP.md → state.md Sources contains both."""
    (tmp_path / "README.md").write_text("# Repo\n")
    (tmp_path / "SETUP.md").write_text("# Setup\n")

    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output

    state_text = (tmp_path / ".cortex" / "state.md").read_text()
    frontmatter, _ = parse_frontmatter(state_text)
    sources = frontmatter.get("Sources")
    assert isinstance(sources, list), f"Sources is not a list: {sources}"
    assert "README.md" in sources
    assert "SETUP.md" in sources


# --- Custom-pattern teaching tests ----------------------------------------


def test_custom_pattern_taught_persists(tmp_path: Path) -> None:
    """Simulating the user answering a classification (via --yes default of
    map_ref) writes ``.cortex/.discover.toml``; the second scan recognizes
    the file via that user pattern instead of re-classifying as unknown.
    """
    (tmp_path / "INVESTMENT_THESIS.md").write_text(
        "# Investment Thesis\n\n## Premise\n\n## Strategy\n\n" + "x" * 1024
    )
    runner = CliRunner()
    first = runner.invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert first.exit_code == 0, first.output

    discover = tmp_path / ".cortex" / ".discover.toml"
    assert discover.is_file(), "expected .cortex/.discover.toml to be written"
    assert "INVESTMENT_THESIS.md" in discover.read_text()

    # Second scan picks up the user pattern and surfaces the file in the
    # taught category (map_ref) instead of unknown.
    second_scan = scan_project(tmp_path)
    matched = [f for f in second_scan.findings if f.relative == "INVESTMENT_THESIS.md"]
    assert matched, "second scan didn't surface the taught file"
    assert matched[0].category == "map_ref"


# --- Non-TTY behavior preservation ----------------------------------------


def test_non_tty_skips_prompts_silently(tmp_path: Path) -> None:
    """Non-TTY init runs the scan + prints the summary, but never imports
    anything (preserves today's silent-scaffold contract for CI/hooks)."""
    (tmp_path / "principles").mkdir()
    (tmp_path / "principles" / "engineering.md").write_text("# Engineering\n")
    (tmp_path / "ROADMAP.md").write_text("# Roadmap\n")

    # CliRunner provides a non-TTY stdin; without --yes nothing is imported.
    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # Scan summary printed.
    assert "Scanning" in result.output
    assert "Doctrine candidates" in result.output
    # But nothing was imported into .cortex/.
    assert not list((tmp_path / ".cortex" / "doctrine").glob("*.md"))
    assert not list((tmp_path / ".cortex" / "plans").glob("*.md"))


# --- Idempotency / refusal tests ------------------------------------------


def test_init_idempotent_with_existing_cortex(tmp_path: Path) -> None:
    """Running scan-init on a repo where ``.cortex/`` already exists and has
    SPEC_VERSION refuses without --force; with --force, doctrine and plan
    contents are preserved (today's behavior preserved by the brief)."""
    (tmp_path / "principles").mkdir()
    (tmp_path / "principles" / "engineering.md").write_text("# Engineering\n")
    runner = CliRunner()
    first = runner.invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert first.exit_code == 0, first.output
    # First run produced one Doctrine entry.
    first_doctrine = sorted((tmp_path / ".cortex" / "doctrine").glob("*.md"))
    assert len(first_doctrine) == 1
    first_text = first_doctrine[0].read_text()

    # Second run without --force refuses.
    second = runner.invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert second.exit_code != 0, second.output
    assert "already exists" in second.output

    # Second run WITH --force succeeds, but Doctrine content is preserved
    # (the source already has Imported-from: principles/engineering.md so
    # the seeder skips it on idempotency grounds).
    third = runner.invoke(cli, ["init", "--path", str(tmp_path), "--yes", "--force"])
    assert third.exit_code == 0, third.output
    after_doctrine = sorted((tmp_path / ".cortex" / "doctrine").glob("*.md"))
    assert [p.name for p in after_doctrine] == [p.name for p in first_doctrine]
    assert after_doctrine[0].read_text() == first_text
