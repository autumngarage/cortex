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
        "All `tests/test_doctor.py` pass (signal: `pytest -q` exit 0).\n\n"
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
    plan.write_text(
        plan.read_text().replace(
            "## Success Criteria\nAll `tests/test_doctor.py` pass (signal: `pytest -q` exit 0).\n\n",
            "",
        )
    )
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


def test_empty_plan_frontmatter_value_rejected(scaffolded_project: Path) -> None:
    plans_dir = scaffolded_project / ".cortex" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan = plans_dir / "empty-field.md"
    plan.write_text(
        "---\n"
        "Status: \n"
        "Written: 2026-04-17\n"
        "Author: human\n"
        f"Goal-hash: {normalize_goal_hash('Empty Plan')}\n"
        "Updated-by:\n"
        "  - 2026-04-17T10:00 human\n"
        "---\n\n"
        "# Empty Plan\n\n## Why (grounding)\ndoctrine/0001.\n\n"
        "## Success Criteria\nyes\n\n## Approach\n.\n\n## Work items\n- [ ] a\n"
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Status" in stderr


def test_invalid_doctrine_status_rejected(scaffolded_project: Path) -> None:
    entry = scaffolded_project / ".cortex" / "doctrine" / "0002-weird.md"
    entry.write_text(
        "# 0002 — Weird\n\n"
        "**Status:** Draft\n"
        "**Date:** 2026-04-17\n"
        "**Load-priority:** default\n\n"
        "## Context\nx\n## Decision\ny\n## Consequences\nz\n"
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Status" in stderr


def test_fenced_success_criteria_does_not_satisfy_empty_check(scaffolded_project: Path) -> None:
    # Plan has a real `## Success Criteria` heading but its body is empty; a
    # fenced `## Success Criteria` earlier in the file must not satisfy the
    # empty-section check.
    plans_dir = scaffolded_project / ".cortex" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan = plans_dir / "fenced-empty.md"
    plan.write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-04-17\n"
        "Author: human\n"
        f"Goal-hash: {normalize_goal_hash('Fenced Empty')}\n"
        "Updated-by:\n"
        "  - 2026-04-17T10:00 human\n"
        "---\n\n"
        "# Fenced Empty\n\n"
        "## Why (grounding)\ndoctrine/0001.\n\n"
        "## Approach\n```\n## Success Criteria\nfilled (fenced)\n```\n\n"
        "## Success Criteria\n\n"
        "## Work items\n- [ ] item\n"
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Success Criteria" in stderr


def test_prose_only_success_criteria_rejected(scaffolded_project: Path) -> None:
    plan = _write_valid_plan(scaffolded_project, "Prose Criteria Plan")
    plan.write_text(
        plan.read_text().replace(
            "All `tests/test_doctor.py` pass (signal: `pytest -q` exit 0).",
            "It works well and feels good.",
        )
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "concrete signal" in stderr


def test_plan_missing_cites_rejected(scaffolded_project: Path) -> None:
    plan = _write_valid_plan(scaffolded_project, "No Cites Plan")
    plan.write_text(plan.read_text().replace("Cites: doctrine/0001\n", ""))
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Cites" in stderr


def test_plan_missing_updated_by_rejected(scaffolded_project: Path) -> None:
    plan = _write_valid_plan(scaffolded_project, "No Updated-by Plan")
    plan.write_text(
        plan.read_text().replace(
            "Updated-by:\n  - 2026-04-17T10:00 human\n",
            "",
        )
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "Updated-by" in stderr


def test_plan_missing_h1_title_rejected(scaffolded_project: Path) -> None:
    plan = _write_valid_plan(scaffolded_project, "Has Title")
    plan.write_text(plan.read_text().replace("# Has Title\n", ""))
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "H1 title" in stderr


def test_fenced_h1_does_not_satisfy_title_check(scaffolded_project: Path) -> None:
    # A fenced `# Title` inside a code block must not satisfy the H1
    # requirement — otherwise Goal-hash is never actually verified.
    plans_dir = scaffolded_project / ".cortex" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    plan = plans_dir / "fenced-h1.md"
    plan.write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-04-17\n"
        "Author: human\n"
        "Goal-hash: deadbeef\n"
        "Updated-by:\n  - 2026-04-17T10:00 human\n"
        "Cites: doctrine/0001\n"
        "---\n\n"
        "```\n# Fake Title\n```\n\n"
        "## Why (grounding)\ndoctrine/0001.\n\n"
        "## Success Criteria\nSignal: `tests/` pass.\n\n"
        "## Approach\nx\n\n## Work items\n- [ ] y\n"
    )
    exit_code, _stdout, stderr = _run_doctor(scaffolded_project)
    assert exit_code == 1
    assert "H1 title" in stderr


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


# ---------------------------------------------------------------------------
# CLAUDE.md / AGENTS.md unscoped-constraint warnings (autumngarage F2,
# 2026-04-19) — sentinel applies "no cloud LLMs" globally when CLAUDE.md
# omits a `(applies to: runtime|toolchain|both)` scope qualifier.
# ---------------------------------------------------------------------------


def test_claude_md_unscoped_llm_constraint_warns(scaffolded_project: Path) -> None:
    (scaffolded_project / "CLAUDE.md").write_text("# Project\n\nNo cloud LLMs.\n")
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "applies to:" in stdout
    assert "CLAUDE.md" in stdout


def test_claude_md_scoped_llm_constraint_clean(scaffolded_project: Path) -> None:
    (scaffolded_project / "CLAUDE.md").write_text(
        "# Project\n\nNo cloud LLMs (applies to: runtime).\n"
    )
    exit_code, stdout, combined = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "applies to:" not in combined or "looks healthy" in stdout


def test_agents_md_independently_checked(scaffolded_project: Path) -> None:
    (scaffolded_project / "AGENTS.md").write_text("# Agents\n\nNever call cloud LLM APIs.\n")
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "AGENTS.md" in stdout
    assert "applies to:" in stdout


def test_constraint_in_code_fence_ignored(scaffolded_project: Path) -> None:
    (scaffolded_project / "CLAUDE.md").write_text(
        "# Project\n\nExample of a forbidden line:\n\n```\nNo cloud LLMs.\n```\n"
    )
    exit_code, _stdout, combined = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "scope qualifier" not in combined


def test_unrelated_imperative_not_flagged(scaffolded_project: Path) -> None:
    # "Always test before merging." has a constraint keyword but no
    # LLM/API/provider keyword — conservative heuristic should let it pass.
    (scaffolded_project / "CLAUDE.md").write_text("# Project\n\nAlways test before merging.\n")
    exit_code, _stdout, combined = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "scope qualifier" not in combined


def test_no_claude_or_agents_md_no_warning(scaffolded_project: Path) -> None:
    # Fresh scaffold has no CLAUDE.md / AGENTS.md at the project root.
    assert not (scaffolded_project / "CLAUDE.md").exists()
    assert not (scaffolded_project / "AGENTS.md").exists()
    exit_code, stdout, combined = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "scope qualifier" not in combined
    assert "looks healthy" in stdout


@pytest.mark.parametrize(
    "constraint",
    [
        "No LLMs.",
        "Never use APIs.",
        "No providers allowed.",
    ],
)
def test_plural_llm_keywords_are_flagged(scaffolded_project: Path, constraint: str) -> None:
    # `\b(llm)\b` doesn't match `LLMs` because `s` is a word character, so
    # the heuristic must allow optional plural suffixes on the noun-shaped
    # keywords. Regression for codex review feedback.
    (scaffolded_project / "CLAUDE.md").write_text(f"# Project\n\n{constraint}\n")
    exit_code, stdout, _ = _run_doctor(scaffolded_project)
    assert exit_code == 0
    assert "scope qualifier" in stdout, (
        f"plural-form constraint {constraint!r} should warn but did not"
    )
