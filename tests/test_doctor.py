"""Integration tests for `cortex doctor` — temp-dir fixtures, real filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.goal_hash import normalize_goal_hash


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    """Project with a fresh `cortex init` scaffold and no user content."""
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _run_doctor(project: Path) -> tuple[int, str, str]:
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--path", str(project)])
    # Click 8.2+ separates stdout/stderr; we flatten to a single combined
    # stream for substring assertions and keep `result.output` (stdout) for
    # positive checks like "looks healthy".
    stderr = getattr(result, "stderr", "") or ""
    combined = result.output + stderr
    return result.exit_code, result.output, combined


def test_fresh_scaffold_is_clean(scaffolded_project: Path) -> None:
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "looks healthy" in stdout


def test_missing_cortex_dir_reports_error(tmp_path: Path) -> None:
    exit_code, _stdout, stderr = _run_doctor(tmp_path)
    assert exit_code == 1
    assert ".cortex/" in stderr


def test_missing_spec_version_reports_error(scaffolded_project: Path) -> None:
    (scaffolded_project / ".cortex" / "SPEC_VERSION").unlink()
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "SPEC_VERSION" in stderr


def test_unsupported_spec_version_reports_error(scaffolded_project: Path) -> None:
    (scaffolded_project / ".cortex" / "SPEC_VERSION").write_text("9.9.0\n")
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "not supported" in stderr


def test_derived_layer_missing_field_reports_error(scaffolded_project: Path) -> None:
    state = scaffolded_project / ".cortex" / "state.md"
    text = state.read_text()
    # Drop the Corpus field — easiest way is to rewrite the frontmatter missing it.
    broken = text.replace("Corpus:", "Dropped-field:")
    state.write_text(broken)
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Corpus" in stderr


def _write_valid_plan(project: Path, title: str, *, goal_hash: str | None = None) -> Path:
    plans_dir = project / ".cortex" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    path = plans_dir / "example.md"
    hash_value = goal_hash if goal_hash is not None else normalize_goal_hash(title)
    path.write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-04-17\n"
        "Author: human\n"
        f"Goal-hash: {hash_value}\n"
        "Updated-by:\n"
        "  - 2026-04-17T10:00 human\n"
        "Cites: doctrine/0001\n"
        "---\n"
        f"\n# {title}\n\n"
        "> Summary.\n\n"
        "## Why (grounding)\n"
        "Links to doctrine/0001.\n\n"
        "## Success Criteria\n"
        "Specific signal: tests pass.\n\n"
        "## Approach\n"
        "Do the thing.\n\n"
        "## Work items\n"
        "- [ ] Ship it.\n"
    )
    return path


def test_valid_plan_is_clean(scaffolded_project: Path) -> None:
    _write_valid_plan(scaffolded_project, "Ship the Thing")
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0, stdout


def test_goal_hash_mismatch_reports_error(scaffolded_project: Path) -> None:
    _write_valid_plan(scaffolded_project, "Ship the Thing", goal_hash="deadbeef")
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Goal-hash" in stderr


def test_plan_missing_success_criteria_reports_error(scaffolded_project: Path) -> None:
    plan = _write_valid_plan(scaffolded_project, "Ship the Thing")
    plan.write_text(plan.read_text().replace("## Success Criteria\nSpecific signal: tests pass.\n\n", ""))
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Success Criteria" in stderr


def test_prose_mention_does_not_satisfy_required_section(scaffolded_project: Path) -> None:
    # Plan mentions "## Success Criteria" in a code fence / bullet list but
    # has no actual heading; doctor must reject it.
    plans_dir = scaffolded_project / ".cortex" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan = plans_dir / "prose-mention.md"
    plan.write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-04-17\n"
        "Author: human\n"
        f"Goal-hash: {normalize_goal_hash('Prose Mention Plan')}\n"
        "Updated-by:\n"
        "  - 2026-04-17T10:00 human\n"
        "---\n\n"
        "# Prose Mention Plan\n\n"
        "> Summary.\n\n"
        "## Why (grounding)\ndoctrine/0001.\n\n"
        "## Approach\n```\n## Success Criteria\nthis is inside a fence\n```\n\n"
        "## Work items\n- [ ] item\n"
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Success Criteria" in stderr


def test_superseded_doctrine_exempt_from_load_priority(scaffolded_project: Path) -> None:
    # Doctrine is immutable-with-supersede; entries already marked
    # Superseded-by cannot be retrofitted with Load-priority, so doctor must
    # not require it for them.
    entry = scaffolded_project / ".cortex" / "doctrine" / "0001-legacy.md"
    entry.write_text(
        "# 0001 — Legacy\n\n"
        "**Status:** Superseded-by 0005\n"
        "**Date:** 2026-04-17\n\n"
        "## Context\nx\n## Decision\ny\n## Consequences\nz\n"
    )
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0, stdout


def test_plan_without_grounding_link_warns(scaffolded_project: Path) -> None:
    plan = _write_valid_plan(scaffolded_project, "Ship the Thing")
    plan.write_text(plan.read_text().replace("Links to doctrine/0001.", "Prose-only grounding."))
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0  # warning only
    assert "grounding" in stdout


def test_goal_hash_collision_warns(scaffolded_project: Path) -> None:
    first = _write_valid_plan(scaffolded_project, "Ship the Thing")
    other = scaffolded_project / ".cortex" / "plans" / "other.md"
    other.write_text(first.read_text().replace("# Ship the Thing", "# Ship the Thing"))
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "collision" in stdout


def test_invalid_journal_filename_warns(scaffolded_project: Path) -> None:
    journal = scaffolded_project / ".cortex" / "journal" / "not-a-valid-name.md"
    journal.write_text("# Title\n")
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "Journal filename" in stdout


def test_doctrine_yaml_frontmatter_accepted(scaffolded_project: Path) -> None:
    # SPEC § 6: parsers must accept either bold-inline OR YAML frontmatter.
    entry = scaffolded_project / ".cortex" / "doctrine" / "0001-example.md"
    entry.write_text(
        "---\n"
        "Status: Accepted\n"
        "Date: 2026-04-17\n"
        "Load-priority: always\n"
        "---\n\n"
        "# 0001 — Example\n\n"
        "## Context\nx\n## Decision\ny\n## Consequences\nz\n"
    )
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0, stdout


def test_doctrine_entry_missing_load_priority_reports_error(scaffolded_project: Path) -> None:
    entry = scaffolded_project / ".cortex" / "doctrine" / "0001-example.md"
    entry.write_text(
        "# 0001 — Example\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-17\n\n"
        "## Context\nx\n## Decision\ny\n## Consequences\nz\n"
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Load-priority" in stderr
