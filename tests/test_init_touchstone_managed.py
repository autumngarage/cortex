"""Tests for Touchstone-managed Doctrine skip in `cortex init`.

When `cortex init` runs on a project with Touchstone integration detected
(any of `.touchstone-config`, `.touchstone-manifest`, `.touchstone-version`
present at the project root), files under `principles/` and `docs/principles/`
are owned by Touchstone (synced via `touchstone update`) and already
imported into the agent's context via `@<path>` directives in CLAUDE.md /
AGENTS.md. Importing them as Cortex Doctrine creates stub-pointer entries
that displace real Doctrine in the session-start manifest budget — the
shallow-Doctrine bug surfaced by the touchstone dogfood UX test on
2026-04-24.

Fix #1 in `.cortex/plans/init-ux-fixes-from-touchstone.md`: detect
Touchstone integration during scan and reclassify these candidates as
`touchstone_managed` so they are surfaced in scan output (informational)
but not seeded as Doctrine.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.init_scan import scan_project


def _make_touchstone_project(tmp_path: Path) -> Path:
    """Build a temp dir that mimics a Touchstone-managed project's
    detection signals plus a couple of Doctrine-shaped principle files."""
    (tmp_path / ".touchstone-config").write_text("# touchstone config\n")
    (tmp_path / ".touchstone-version").write_text("2.1.1\n")
    principles = tmp_path / "principles"
    principles.mkdir()
    (principles / "engineering-principles.md").write_text(
        "# Engineering Principles\n\nNo silent failures.\n"
    )
    (principles / "git-workflow.md").write_text(
        "# Git Workflow\n\nBranch first, then commit.\n"
    )
    return tmp_path


def _make_non_touchstone_project_with_principles(tmp_path: Path) -> Path:
    """Same shape but WITHOUT any touchstone-detection files — Fix #1's
    skip rule should not apply here, so the principles get absorbed
    normally as Doctrine."""
    principles = tmp_path / "principles"
    principles.mkdir()
    (principles / "engineering-principles.md").write_text(
        "# Engineering Principles\n\nNo silent failures.\n"
    )
    (principles / "git-workflow.md").write_text(
        "# Git Workflow\n\nBranch first, then commit.\n"
    )
    return tmp_path


# --- scanner-level reclassification ----------------------------------------


def test_scan_reclassifies_principles_as_touchstone_managed_when_touchstone_present(
    tmp_path: Path,
) -> None:
    project = _make_touchstone_project(tmp_path)
    scan = scan_project(project)
    # No principles/* files should appear under Doctrine.
    doctrine_paths = {f.relative for f in scan.by_category("doctrine")}
    assert not any(p.startswith("principles/") for p in doctrine_paths), (
        f"Touchstone-managed principles/* leaked into Doctrine candidates: {doctrine_paths}"
    )
    # They should appear under touchstone_managed instead.
    tm_paths = {f.relative for f in scan.by_category("touchstone_managed")}
    assert tm_paths == {
        "principles/engineering-principles.md",
        "principles/git-workflow.md",
    }, f"Expected both principles/* under touchstone_managed; got {tm_paths}"


def test_scan_keeps_principles_in_doctrine_when_touchstone_absent(tmp_path: Path) -> None:
    """Non-Touchstone projects with a `principles/` directory still absorb
    those files as Doctrine — Fix #1's skip rule is gated on Touchstone
    integration, not on the directory name."""
    project = _make_non_touchstone_project_with_principles(tmp_path)
    scan = scan_project(project)
    doctrine_paths = {f.relative for f in scan.by_category("doctrine")}
    assert doctrine_paths == {
        "principles/engineering-principles.md",
        "principles/git-workflow.md",
    }, f"Non-Touchstone principles/* should be Doctrine candidates; got {doctrine_paths}"
    # And nothing in the touchstone_managed bucket.
    assert scan.by_category("touchstone_managed") == []


# --- end-to-end via cortex init --------------------------------------------


def test_init_does_not_import_touchstone_managed_principles_as_doctrine(
    tmp_path: Path,
) -> None:
    project = _make_touchstone_project(tmp_path)
    result = CliRunner().invoke(cli, ["init", "--path", str(project), "--yes"])
    assert result.exit_code == 0, result.output

    # No Doctrine entry should claim `Imported-from: principles/...`.
    doctrine_dir = project / ".cortex" / "doctrine"
    leaked: list[str] = []
    for entry in sorted(doctrine_dir.iterdir()):
        if not entry.is_file() or entry.name == ".gitkeep":
            continue
        text = entry.read_text()
        for principle_file in ("principles/engineering-principles.md", "principles/git-workflow.md"):
            if f"Imported-from: {principle_file}" in text:
                leaked.append(f"{entry.name} → {principle_file}")
    assert not leaked, (
        f"Touchstone-managed principles/* were imported as Doctrine (Fix #1 broken): {leaked}"
    )


def test_init_surfaces_touchstone_managed_skip_in_scan_output(tmp_path: Path) -> None:
    project = _make_touchstone_project(tmp_path)
    result = CliRunner().invoke(cli, ["init", "--path", str(project), "--yes"])
    assert result.exit_code == 0, result.output
    # The scan-summary section header for touchstone-managed skips must
    # appear, naming each skipped file.
    assert "Detected Touchstone-managed" in result.output, (
        f"Expected 'Detected Touchstone-managed' header in init output:\n{result.output}"
    )
    assert "principles/engineering-principles.md" in result.output
    assert "principles/git-workflow.md" in result.output
    # Negative: those files must NOT appear under "Doctrine candidates".
    # (We assert by structural locality: the touchstone-managed mention
    # comes after "Doctrine candidates" if both existed; instead, with no
    # remaining Doctrine candidates here, the Doctrine header is absent.)
    assert "Doctrine candidates" not in result.output, (
        "Touchstone-managed reclassification left no Doctrine candidates, "
        "so the Doctrine candidates header should be suppressed.\n"
        f"Output:\n{result.output}"
    )


def test_init_idempotent_on_touchstone_project(tmp_path: Path) -> None:
    """Running init twice on a Touchstone project produces the same state."""
    project = _make_touchstone_project(tmp_path)
    runner = CliRunner()
    first = runner.invoke(cli, ["init", "--path", str(project), "--yes"])
    assert first.exit_code == 0, first.output
    first_listing = sorted((project / ".cortex" / "doctrine").iterdir())

    second = runner.invoke(cli, ["init", "--path", str(project), "--yes"])
    # Second invocation may be a no-op or may re-scaffold (depending on the
    # idempotency contract); either way no NEW Doctrine entries appear.
    second_listing = sorted((project / ".cortex" / "doctrine").iterdir())
    assert [p.name for p in second_listing] == [p.name for p in first_listing], (
        "Re-running init on a Touchstone project changed the Doctrine listing — "
        f"first={[p.name for p in first_listing]}, second={[p.name for p in second_listing]}"
    )
