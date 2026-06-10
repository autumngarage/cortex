"""cortex#501: fast sources-hash drift guard (`cortex doctor --check sources-hash`).

Evidence class: a `.cortex/` source edited after the last `cortex update` and
before commit silently invalidates state.md's recorded Sources-hash claims
(PR #493 round 1). These tests pin the exit-code-clean pre-commit guard that
converts that reviewer-caught class into a deterministic block.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.doctor_checks import SOURCES_HASH_REMEDIATION, check_sources_hash_drift
from cortex.state_render import build_state_inputs, render_state

PLAN_REL = ".cortex/plans/hosted-decision-reviewer.md"


def _scaffold(project: Path) -> None:
    result = CliRunner().invoke(init_command, ["--path", str(project)])
    assert result.exit_code == 0, result.output


def _refresh_state(project: Path) -> None:
    """Regenerate state.md via the real pipeline so Sources-hash is recorded."""
    inputs = build_state_inputs(project)
    (project / ".cortex" / "state.md").write_text(render_state(inputs))


def _project_with_recorded_plan(project: Path) -> Path:
    _scaffold(project)
    plan = project / PLAN_REL
    plan.write_text("# Hosted decision reviewer\n\nOriginal plan body.\n")
    _refresh_state(project)
    return plan


def _run_check(project: Path) -> tuple[int, str]:
    result = CliRunner().invoke(
        cli, ["doctor", "--path", str(project), "--check", "sources-hash"]
    )
    stderr = getattr(result, "stderr", "") or ""
    return result.exit_code, result.output + stderr


def test_clean_tree_passes(tmp_path: Path) -> None:
    _project_with_recorded_plan(tmp_path)

    exit_code, output = _run_check(tmp_path)
    assert exit_code == 0, output
    assert "sources-hash: OK" in output


def test_modified_plan_file_fails_naming_path_and_remediation(tmp_path: Path) -> None:
    plan = _project_with_recorded_plan(tmp_path)
    plan.write_text("# Hosted decision reviewer\n\nEdited after cortex update.\n")

    exit_code, output = _run_check(tmp_path)
    assert exit_code == 1, output
    assert PLAN_REL in output
    assert SOURCES_HASH_REMEDIATION in output
    assert "run 'cortex update' and restage" in output


def test_recorded_source_deleted_fails_naming_path(tmp_path: Path) -> None:
    plan = _project_with_recorded_plan(tmp_path)
    plan.unlink()

    exit_code, output = _run_check(tmp_path)
    assert exit_code == 1, output
    assert PLAN_REL in output
    assert "missing on disk" in output
    assert SOURCES_HASH_REMEDIATION in output


def test_missing_state_md_degrades_to_visible_skip(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "state.md").unlink()

    exit_code, output = _run_check(tmp_path)
    assert exit_code == 0, output
    assert "skipped" in output
    assert ".cortex/state.md not found" in output


def test_state_without_sources_hash_block_degrades_to_visible_skip(tmp_path: Path) -> None:
    """Pre-v1.1 scaffolds never recorded Sources-hash; the guard must not
    block their commits, but the skip must be printed (no silence)."""
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "state.md").write_text(
        "---\n"
        "Generated: 2026-06-09T00:00:00+00:00\n"
        "Generator: cortex refresh-state v0.9.0\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "---\n\n# Project State\n"
    )

    exit_code, output = _run_check(tmp_path)
    assert exit_code == 0, output
    assert "skipped" in output
    assert "no Sources-hash" in output


def test_unchanged_content_with_touched_mtime_passes(tmp_path: Path) -> None:
    """Invariant: the guard compares content hashes, never mtimes — a
    checkout/touch must not produce a false drift failure."""
    import os
    from datetime import UTC, datetime, timedelta

    plan = _project_with_recorded_plan(tmp_path)
    future = (datetime.now(UTC) + timedelta(seconds=30)).timestamp()
    os.utime(plan, (future, future))

    exit_code, output = _run_check(tmp_path)
    assert exit_code == 0, output
    assert "sources-hash: OK" in output


def test_check_rejects_audit_flag_combination(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    result = CliRunner().invoke(
        cli, ["doctor", "--path", str(tmp_path), "--check", "sources-hash", "--audit"]
    )
    assert result.exit_code == 2
    stderr = getattr(result, "stderr", "") or ""
    assert "--check runs a single fast check" in (result.output + stderr)


def test_check_function_reports_all_offending_paths(tmp_path: Path) -> None:
    """Drift output lists every offending path, not just the first one found."""
    plan = _project_with_recorded_plan(tmp_path)
    journal = tmp_path / ".cortex" / "journal" / "2026-06-09-decision.md"
    journal.write_text("# Decision\n\n**Date:** 2026-06-09\n**Type:** decision\n\nBody.\n")
    _refresh_state(tmp_path)

    plan.write_text("# Hosted decision reviewer\n\nDrift one.\n")
    journal.write_text("# Decision\n\n**Date:** 2026-06-09\n**Type:** decision\n\nDrift two.\n")

    result = check_sources_hash_drift(tmp_path)
    assert not result.ok
    assert not result.skipped
    joined = "\n".join(result.lines)
    assert PLAN_REL in joined
    assert ".cortex/journal/2026-06-09-decision.md" in joined
    assert result.lines[-1] == SOURCES_HASH_REMEDIATION
