"""v0.6.0 `cortex doctor` invariant checks."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.doctor_checks import (
    check_append_only_journal,
    check_canonical_ownership,
    check_cli_less_fallback,
    check_config_toml_schema,
    check_generated_layers,
    check_immutable_doctrine,
    check_promotion_queue,
    check_retention_visibility,
    check_t1_4_deletions,
)
from cortex.goal_hash import normalize_goal_hash


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(project: Path) -> None:
    _run(project, "init", "-b", "main")
    _run(project, "config", "user.email", "t@example.com")
    _run(project, "config", "user.name", "Test")


def _commit(project: Path, message: str, *paths: str) -> None:
    _run(project, "add", *paths)
    _run(project, "commit", "-m", message)


def _scaffold(project: Path) -> None:
    result = CliRunner().invoke(init_command, ["--path", str(project)])
    assert result.exit_code == 0, result.output


def _git_cortex_project(project: Path) -> None:
    _scaffold(project)
    _git_init(project)
    _run(project, "add", ".cortex")
    _run(project, "commit", "-m", "initial cortex scaffold")


def _write_valid_active_plan(project: Path, slug: str = "active") -> None:
    title = "Active Plan"
    (project / ".cortex" / "plans" / f"{slug}.md").write_text(
        "---\n"
        "Status: active\n"
        "Written: 2026-05-02\n"
        "Author: human\n"
        f"Goal-hash: {normalize_goal_hash(title)}\n"
        "Updated-by:\n"
        "  - 2026-05-02T10:00 human\n"
        "Cites: doctrine/0001\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Why (grounding)\nLinks to doctrine/0001.\n\n"
        "## Success Criteria\nAll `tests/test_doctor_invariants.py` pass (signal: `pytest -q` exit 0).\n\n"
        "## Approach\nImplement the invariant.\n\n"
        "## Work items\n- [ ] Ship it.\n"
    )


def test_append_only_journal_violation_detected(tmp_path: Path) -> None:
    _git_cortex_project(tmp_path)
    entry = tmp_path / ".cortex" / "journal" / "2026-05-02-decision.md"
    entry.write_text("# Decision\n\n**Date:** 2026-05-02\n**Type:** decision\n\nOriginal.\n")
    _commit(tmp_path, "docs: add journal entry", ".cortex/journal/2026-05-02-decision.md")

    entry.write_text(entry.read_text() + "\nMutated after creation.\n")
    _commit(tmp_path, "docs: mutate journal entry", ".cortex/journal/2026-05-02-decision.md")

    issues = check_append_only_journal(tmp_path)
    assert any("append-only invariant violated" in issue.message for issue in issues)


def test_journal_updated_by_frontmatter_diff_allowed(tmp_path: Path) -> None:
    _git_cortex_project(tmp_path)
    entry = tmp_path / ".cortex" / "journal" / "2026-05-02-decision.md"
    entry.write_text(
        "---\n"
        "Date: 2026-05-02\n"
        "Type: decision\n"
        "Updated-by:\n"
        "  - 2026-05-02T10:00 human\n"
        "---\n\n"
        "# Decision\n\nBody.\n"
    )
    _commit(tmp_path, "docs: add journal entry", ".cortex/journal/2026-05-02-decision.md")

    entry.write_text(entry.read_text().replace("2026-05-02T10:00", "2026-05-02T11:00"))
    _commit(tmp_path, "docs: update journal metadata", ".cortex/journal/2026-05-02-decision.md")

    assert check_append_only_journal(tmp_path) == []


def test_doctrine_mutation_detected_but_supersede_status_allowed(tmp_path: Path) -> None:
    _git_cortex_project(tmp_path)
    entry = tmp_path / ".cortex" / "doctrine" / "0008-rule.md"
    entry.write_text(
        "---\nStatus: Accepted\nDate: 2026-05-02\nLoad-priority: default\n---\n\n# Rule\n\nOriginal.\n"
    )
    _commit(tmp_path, "docs: add doctrine", ".cortex/doctrine/0008-rule.md")

    entry.write_text(entry.read_text().replace("Original.", "Mutated body."))
    _commit(tmp_path, "docs: mutate doctrine body", ".cortex/doctrine/0008-rule.md")
    issues = check_immutable_doctrine(tmp_path)
    assert any("immutable Doctrine invariant violated" in issue.message for issue in issues)

    clean = tmp_path / "clean"
    clean.mkdir()
    _git_cortex_project(clean)
    clean_entry = clean / ".cortex" / "doctrine" / "0008-rule.md"
    clean_entry.write_text(entry.read_text().replace("Mutated body.", "Original."))
    _commit(clean, "docs: add doctrine", ".cortex/doctrine/0008-rule.md")
    clean_entry.write_text(
        clean_entry.read_text().replace("Status: Accepted", "Status: Superseded-by 0009")
    )
    _commit(clean, "docs: supersede doctrine", ".cortex/doctrine/0008-rule.md")
    assert check_immutable_doctrine(clean) == []


def test_promotion_queue_dangling_source_warns(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / ".index.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "id": "missing",
                        "source": ".cortex/journal/2026-05-02-missing.md",
                        "last_touched": datetime.now(UTC).date().isoformat(),
                        "age_days": 0,
                    }
                ]
            }
        )
    )

    issues = check_promotion_queue(tmp_path)
    assert any("does not exist" in issue.message for issue in issues)


def test_cli_less_fallback_threshold_warns(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    doctrine = tmp_path / ".cortex" / "doctrine"
    for i in range(21):
        (doctrine / f"{i + 1:04d}-entry.md").write_text("# Entry\n")

    issues = check_cli_less_fallback(tmp_path)
    assert any("fallback threshold" in issue.message for issue in issues)


def test_t1_4_large_deletion_without_journal_warns(tmp_path: Path) -> None:
    _git_cortex_project(tmp_path)
    target = tmp_path / "large.txt"
    target.write_text("line\n" * 101)
    _run(tmp_path, "add", "large.txt")
    _run(tmp_path, "commit", "-m", "docs: add large file")
    target.unlink()
    _run(tmp_path, "add", "large.txt")
    _run(tmp_path, "commit", "-m", "docs: delete large file")

    issues = check_t1_4_deletions(tmp_path, since_days=30)
    assert any("T1.4 deletion audit" in issue.message for issue in issues)


def test_generated_layer_contract_warnings(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    state = tmp_path / ".cortex" / "state.md"

    state.write_text(
        "---\nGenerator: cortex refresh-state\nSources: []\nCorpus: 0\nOmitted: []\nIncomplete: []\nConflicts-preserved: []\n---\n\n# State\n"
    )
    assert any("Generated" in issue.message for issue in check_generated_layers(tmp_path))

    state.write_text(
        f"---\nGenerated: {old}\nGenerator: cortex refresh-state\nCorpus: 0\nOmitted: []\nIncomplete: []\nConflicts-preserved: []\n---\n\n# State\n"
    )
    issues = check_generated_layers(tmp_path)
    assert any("Sources" in issue.message for issue in issues)
    assert any("layer is stale" in issue.message for issue in issues)


def test_config_toml_schema_type_and_unknown_key(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[audit-instructions]\nhomebrew_tap = ["a", "b"]\nunknown_key = "x"\n'
    )

    issues = check_config_toml_schema(tmp_path)
    assert any(issue.severity == "error" and "homebrew_tap" in issue.message for issue in issues)
    assert any(issue.severity == "warning" and "unknown_key" in issue.message for issue in issues)


def test_retention_visibility_plan_and_warm_journal(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    plan = tmp_path / ".cortex" / "plans" / "old.md"
    plan.write_text("---\nStatus: shipped\nDate: 2025-01-01\n---\n\n# Old\n")
    journal = tmp_path / ".cortex" / "journal"
    for i in range(201):
        (journal / f"2025-01-{(i % 28) + 1:02d}-old-{i}.md").write_text("# Old\n")

    issues = check_retention_visibility(tmp_path)
    assert any("eligible for archive" in issue.message for issue in issues)
    assert any("warm journal threshold exceeded" in issue.message for issue in issues)


def test_canonical_ownership_warning_and_overrides(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _write_valid_active_plan(tmp_path)
    (tmp_path / "ROADMAP.md").write_text("# Roadmap\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "roadmap.md").write_text("# Docs roadmap\n")

    issues = check_canonical_ownership(tmp_path)
    assert any("Doctrine 0007" in issue.message and issue.path == "ROADMAP.md" for issue in issues)
    assert not any(issue.path == "CHANGELOG.md" for issue in issues)
    assert not any("docs/roadmap.md" in issue.path for issue in issues)

    (tmp_path / ".cortex" / "config.toml").write_text(
        '[doctrine.0007]\nallowed_root_files = ["ROADMAP.md"]\n'
    )
    assert check_canonical_ownership(tmp_path) == []

    lower = tmp_path / "lowercase"
    lower.mkdir()
    _scaffold(lower)
    _write_valid_active_plan(lower)
    (lower / "roadmap.md").write_text("# lowercase roadmap\n")
    issues = check_canonical_ownership(lower)
    assert any(issue.path == "roadmap.md" for issue in issues)


def test_generated_layers_scans_digest_journal_entries_per_spec_5_2(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    digest = tmp_path / ".cortex" / "journal" / "2026-05-01-monthly-digest.md"
    digest.write_text(
        "---\n"
        "Date: 2026-05-01\n"
        "Type: digest\n"
        "---\n\n"
        "# April digest\n\nMissing all seven provenance fields.\n"
    )
    plain = tmp_path / ".cortex" / "journal" / "2026-05-02-decision.md"
    plain.write_text(
        "---\nDate: 2026-05-02\nType: decision\n---\n\n# Decision\n\nNot a digest.\n"
    )
    legacy_dir = tmp_path / ".cortex" / "digests"
    legacy_dir.mkdir()
    (legacy_dir / "ignored.md").write_text("# legacy path\n")

    issues = check_generated_layers(tmp_path)
    flagged_paths = {issue.path for issue in issues}
    assert any(p.endswith("2026-05-01-monthly-digest.md") for p in flagged_paths)
    assert not any(p.endswith("2026-05-02-decision.md") for p in flagged_paths)
    assert not any("digests/ignored.md" in p for p in flagged_paths)


def test_canonical_ownership_runs_on_plain_doctor(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _write_valid_active_plan(tmp_path)
    (tmp_path / "PLAN.md").write_text("# Plan\n")

    result = CliRunner().invoke(cli, ["doctor", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "canonical-ownership" in result.output
