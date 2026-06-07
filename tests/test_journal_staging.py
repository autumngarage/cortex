"""Tests for source-PR journal staging commands."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.journal_staging import STAGED_FOR_PR_FIELD


@pytest.fixture
def project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write_clean_pr_merged(project: Path, pr_number: int) -> Path:
    today = date.today().isoformat()
    path = project / ".cortex" / "journal" / f"{today}-pr-merged-test.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"# PR #{pr_number} merged — test staging",
                "",
                f"**Date:** {today}",
                "**Type:** pr-merged",
                "**Trigger:** T1.9",
                "",
                "Clean staged entry body.",
                "",
            ]
        )
    )
    return path


def test_stage_annotates_staged_for_pr(project: Path, tmp_path: Path) -> None:
    facts = tmp_path / "facts.json"
    facts.write_text(
        json.dumps(
            {
                "type": "pr-merged",
                "title": "feat(staging): source-PR journal staging",
                "pr_number": 42,
                "branch": "feat/staging",
                "commit_range": "aaaaaaa..bbbbbbb",
                "changed_files": ["src/cortex/journal_staging.py"],
                "diffstat": "1 file changed, 40 insertions(+)",
                "behavior_summary": "Stages pr-merged entries before merge.",
                "tests_run": ["uv run pytest tests/test_journal_staging.py"],
                "cortex_refs": {"plans": ["context-integrity-production"]},
                "followups": [],
            }
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "journal",
            "stage",
            "--type",
            "pr-merged",
            "--pr",
            "42",
            "--facts-file",
            str(facts),
            "--path",
            str(project),
        ],
    )
    assert result.exit_code == 0, result.output
    path = find_staged_entry(project, 42)
    assert path is not None
    assert f"**{STAGED_FOR_PR_FIELD}:** 42" in path.read_text()


def test_verify_passes_for_clean_staged_entry(project: Path) -> None:
    path = _write_clean_pr_merged(project, 7)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "journal",
            "verify",
            "--type",
            "pr-merged",
            "--pr",
            "7",
            "--path",
            str(project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert str(path.resolve()) in result.output


def test_verify_fails_when_entry_missing(project: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "journal",
            "verify",
            "--type",
            "pr-merged",
            "--pr",
            "99",
            "--path",
            str(project),
        ],
    )
    assert result.exit_code == 1
    assert "no staged pr-merged journal entry" in result.output


def test_verify_fails_on_template_pollution(project: Path) -> None:
    path = _write_clean_pr_merged(project, 8)
    path.write_text(path.read_text() + "\n_(none recorded — fill on edit)_\n")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "journal",
            "verify",
            "--type",
            "pr-merged",
            "--pr",
            "8",
            "--path",
            str(project),
        ],
    )
    assert result.exit_code == 1
    assert "unresolved template markers" in result.output


def test_post_merge_stage_mode_verifies_only(project: Path) -> None:
    config = project / ".cortex" / "config.toml"
    config.write_text('[journal.t1_9]\nmode = "stage"\n')
    path = _write_clean_pr_merged(project, 12)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "journal",
            "post-merge",
            "--type",
            "pr-merged",
            "--pr",
            "12",
            "--path",
            str(project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert str(path.resolve()) in result.output


def test_post_merge_stage_mode_requires_pr(project: Path) -> None:
    (project / ".cortex" / "config.toml").write_text('[journal.t1_9]\nmode = "stage"\n')
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["journal", "post-merge", "--type", "pr-merged", "--path", str(project)],
    )
    assert result.exit_code == 2
    assert "--pr is required" in result.output


def test_facts_validate_emits_structured_error(project: Path, tmp_path: Path) -> None:
    facts = tmp_path / "facts.json"
    facts.write_text('{"type":"pr-merged","title":"x"}')
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "journal",
            "facts",
            "validate",
            "pr-merged",
            "--facts-file",
            str(facts),
        ],
    )
    assert result.exit_code == 2
    assert "journal-facts-file-invalid" in result.output


def find_staged_entry(project: Path, pr_number: int) -> Path | None:
    from cortex.journal_staging import find_pr_merged_entry

    return find_pr_merged_entry(project, pr_number)
