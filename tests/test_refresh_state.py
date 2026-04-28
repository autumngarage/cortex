"""Integration tests for `cortex refresh-state`."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner, Result

from cortex.cli import cli


def _write_fixture(project: Path) -> None:
    cortex = project / ".cortex"
    for rel in ("plans", "journal", "doctrine", "templates/journal", "templates/plans"):
        (cortex / rel).mkdir(parents=True, exist_ok=True)
    (project / "docs" / "case-studies").mkdir(parents=True, exist_ok=True)
    (cortex / "SPEC_VERSION").write_text("0.5.0\n")
    (project / "pyproject.toml").write_text('[project]\nversion = "9.9.9"\n')
    (cortex / "doctrine" / "0001-test.md").write_text("# Doctrine\n")
    (cortex / "templates" / "journal" / "decision.md").write_text("# Template\n")
    (project / "docs" / "case-studies" / "example.md").write_text("# Case\n")
    (cortex / "state.md").write_text(
        "---\n"
        "Generated: old\n"
        "Generator: hand\n"
        "Sources:\n  []\n"
        "Corpus: old\n"
        "Omitted:\n  []\n"
        "Incomplete:\n  []\n"
        "Conflicts-preserved: []\n"
        "Spec: 0.5.0\n"
        "---\n\n"
        "# Project State\n\n"
        "<!-- cortex:hand -->\n"
        "## Current work\n\n"
        "- keep this exact line\n"
        "<!-- cortex:end-hand -->\n"
    )
    _write_plan(project, "alpha", "Alpha Plan", completed=1, total=2, updated="2000-01-01T00:00")
    _write_plan(project, "beta", "Beta Plan", completed=0, total=2, updated="2026-04-27T00:00")
    _write_journal(project, "2026-04-20-release.md", "release", "Release shipped")
    _write_journal(project, "2026-04-21-stale.md", "decision", "Needs revisit", stale_by="2026-05-01")


def _write_plan(
    project: Path,
    slug: str,
    title: str,
    *,
    completed: int,
    total: int,
    updated: str,
) -> None:
    marks = ["x"] * completed + [" "] * (total - completed)
    items = "\n".join(f"- [{mark}] item {idx}" for idx, mark in enumerate(marks, start=1))
    (project / ".cortex" / "plans" / f"{slug}.md").write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-04-01\n"
        "Author: human\n"
        f"Goal-hash: {slug}hash\n"
        "Updated-by:\n"
        f"  - {updated} human\n"
        "Cites: doctrine/0001\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Why (grounding)\ndoctrine/0001.\n\n"
        "## Success Criteria\npytest passes.\n\n"
        "## Approach\nDo it.\n\n"
        f"## Work items\n{items}\n"
    )


def _write_journal(
    project: Path,
    filename: str,
    type_: str,
    title: str,
    *,
    stale_by: str | None = None,
) -> None:
    stale_line = f"**Stale-by:** {stale_by}\n" if stale_by else ""
    (project / ".cortex" / "journal" / filename).write_text(
        f"# {title}\n\n"
        f"**Date:** {filename[:10]}\n"
        f"**Type:** {type_}\n"
        f"{stale_line}\n"
        "Body.\n"
    )


def _run_refresh(project: Path, *args: str) -> Result:
    runner = CliRunner()
    return runner.invoke(
        cli,
        ["refresh-state", "--path", str(project), *args],
        env={"CORTEX_DETERMINISTIC": "1"},
    )


def test_refresh_state_is_idempotent(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    first = _run_refresh(tmp_path)
    assert first.exit_code == 0, first.output
    first_text = (tmp_path / ".cortex" / "state.md").read_text()

    second = _run_refresh(tmp_path)
    assert second.exit_code == 0, second.output
    assert (tmp_path / ".cortex" / "state.md").read_text() == first_text


def test_refresh_state_preserves_marker_region_verbatim(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    state = tmp_path / ".cortex" / "state.md"
    state.write_text(state.read_text().replace("- keep this exact line\n", "- keep this exact line\n- added by human\n"))

    result = _run_refresh(tmp_path)
    assert result.exit_code == 0, result.output
    assert "<!-- cortex:hand -->\n## Current work\n\n- keep this exact line\n- added by human\n<!-- cortex:end-hand -->" in state.read_text()


def test_refresh_state_preserves_multiple_marker_pairs(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    state = tmp_path / ".cortex" / "state.md"
    state.write_text(
        state.read_text()
        + "\n<!-- cortex:hand -->\n## Open questions\n\n- human question\n<!-- cortex:end-hand -->\n"
    )

    result = _run_refresh(tmp_path)
    assert result.exit_code == 0, result.output
    text = state.read_text()
    assert "## Current work" in text
    assert "## Open questions" in text
    assert "- human question" in text


def test_refresh_state_auto_walked_sections(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    result = _run_refresh(tmp_path)
    assert result.exit_code == 0, result.output
    text = (tmp_path / ".cortex" / "state.md").read_text()

    assert "## Active plans" in text
    assert "`alpha` — Alpha Plan; Goal-hash `alphahash`; 50% complete" in text
    assert "`beta` — Beta Plan; Goal-hash `betahash`; 0% complete" in text
    assert "## Shipped recently" in text
    assert "Release shipped (`.cortex/journal/2026-04-20-release.md`, Type: release)" in text
    assert "## Stale-now / handle-later" in text
    assert "`alpha` — active plan stale since 2000-01-01; open checkboxes remain" in text
    assert "`.cortex/journal/2026-04-21-stale.md` — Stale-by: 2026-05-01" in text


def test_refresh_state_seven_field_header_complete(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    result = _run_refresh(tmp_path)
    assert result.exit_code == 0, result.output
    text = (tmp_path / ".cortex" / "state.md").read_text()

    assert "Generated: 2000-01-01T00:00:00+00:00" in text
    assert "Generator: cortex refresh-state v" in text
    assert "Sources:\n  - HEAD sha:" in text
    assert "Corpus: 2 Journal entries, 2 Plans, 1 Doctrine entries, 1 Templates, 1 Case studies" in text
    assert "Omitted:\n  []" in text
    assert "Incomplete:\n  []" in text
    assert "Conflicts-preserved: []" in text
    assert "Spec: 0.5.0" in text


def test_refresh_state_dry_run_does_not_write(tmp_path: Path) -> None:
    _write_fixture(tmp_path)
    state = tmp_path / ".cortex" / "state.md"
    before = state.read_text()
    result = _run_refresh(tmp_path, "--dry-run")

    assert result.exit_code == 0, result.output
    assert "Generated: 2000-01-01T00:00:00+00:00" in result.output
    assert state.read_text() == before


def test_refresh_state_path_targets_arbitrary_project(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    _write_fixture(project)

    result = _run_refresh(project)
    assert result.exit_code == 0, result.output
    assert "Generated: 2000-01-01T00:00:00+00:00" in (project / ".cortex" / "state.md").read_text()
