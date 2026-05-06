"""v0.6.0 `cortex doctor` invariant checks."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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
    check_legacy_state_migration_needed,
    check_promotion_queue,
    check_retention_visibility,
    check_stale_pickup_pointers,
    check_stale_plan_checkboxes,
    check_stale_state_current_work,
    check_state_journal_staleness,
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


def _commit_at(project: Path, message: str, when: datetime, *paths: str) -> None:
    _run(project, "add", *paths)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": when.isoformat(),
        "GIT_COMMITTER_DATE": when.isoformat(),
    }
    subprocess.run(
        ["git", "-C", str(project), "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


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


def test_append_only_journal_no_false_positive_via_template_rename(tmp_path: Path) -> None:
    """Regression for cortex#103: `git log --follow` traced template-identical
    journal content back to commits that modified the template, falsely
    accusing pristine journal entries of mutation.

    Reproduces the fix-required scenario: a template is modified in C1,
    then a journal entry with byte-identical content is created in C2.
    Old code (with `--follow`) warned citing C1 as a "modification" of the
    journal entry. New code (without `--follow`) ignores rename traces, so
    no warning fires."""
    _git_cortex_project(tmp_path)
    template_dir = tmp_path / ".cortex" / "templates" / "journal"
    template_dir.mkdir(parents=True, exist_ok=True)
    template = template_dir / "pr-merged.md"
    template_v1 = "# PR #{{ nnn }} merged — {{ short title }}\n\n**Original template body.**\n"
    template_v2 = "# PR #{{ nnn }} merged — {{ short title }}\n\n**Updated template body.**\n"
    template.write_text(template_v1)
    _commit(tmp_path, "docs: add pr-merged template", ".cortex/templates/journal/pr-merged.md")
    template.write_text(template_v2)
    _commit(tmp_path, "docs: tweak pr-merged template", ".cortex/templates/journal/pr-merged.md")

    # Journal entry created with byte-identical content to template_v2 — this
    # is what triggers git's rename heuristic if --follow is used.
    entry = tmp_path / ".cortex" / "journal" / "2026-05-03-pr-merged-canary.md"
    entry.write_text(template_v2)
    _commit(tmp_path, "docs: auto-draft pr-merged canary", ".cortex/journal/2026-05-03-pr-merged-canary.md")

    issues = check_append_only_journal(tmp_path)
    assert not any(
        "pr-merged-canary.md" in (issue.path or "") for issue in issues
    ), [f"{i.path}: {i.message}" for i in issues]


def test_append_only_journal_real_mutation_still_detected(tmp_path: Path) -> None:
    """Negative case for the cortex#103 fix: dropping `--follow` must not
    mask actual append-only violations. Same template-rename setup, then
    we ACTUALLY modify the journal entry — the check still warns."""
    _git_cortex_project(tmp_path)
    template_dir = tmp_path / ".cortex" / "templates" / "journal"
    template_dir.mkdir(parents=True, exist_ok=True)
    template = template_dir / "pr-merged.md"
    template_body = "# PR #{{ nnn }} merged — {{ short title }}\n\n**Body.**\n"
    template.write_text(template_body)
    _commit(tmp_path, "docs: add pr-merged template", ".cortex/templates/journal/pr-merged.md")

    entry = tmp_path / ".cortex" / "journal" / "2026-05-03-pr-merged-real.md"
    entry.write_text(template_body)
    _commit(tmp_path, "docs: auto-draft pr-merged entry", ".cortex/journal/2026-05-03-pr-merged-real.md")

    # Now actually modify the journal entry — this MUST still warn.
    entry.write_text(template_body + "\nActual hand-edit after creation.\n")
    _commit(tmp_path, "docs: hand-edit journal entry", ".cortex/journal/2026-05-03-pr-merged-real.md")

    issues = check_append_only_journal(tmp_path)
    assert any(
        "pr-merged-real.md" in (issue.path or "")
        and "append-only invariant violated" in issue.message
        for issue in issues
    ), [f"{i.path}: {i.message}" for i in issues]


def test_immutable_doctrine_no_false_positive_via_rename(tmp_path: Path) -> None:
    """Same shape as the journal regression but on the doctrine check.
    `check_immutable_doctrine` shares `_modified_commits` so the fix lands
    here too — and we test it independently to catch any future divergence."""
    _git_cortex_project(tmp_path)
    # An "external" doctrine-shaped file that the doctrine entry will end up
    # byte-identical to. Anywhere outside .cortex/doctrine/ works.
    sibling = tmp_path / "EXTERNAL.md"
    body_v1 = "---\nStatus: Accepted\nDate: 2026-05-02\nLoad-priority: default\n---\n\n# Rule\n\nOriginal v1.\n"
    body_v2 = "---\nStatus: Accepted\nDate: 2026-05-02\nLoad-priority: default\n---\n\n# Rule\n\nOriginal v2.\n"
    sibling.write_text(body_v1)
    _commit(tmp_path, "docs: add external sibling", "EXTERNAL.md")
    sibling.write_text(body_v2)
    _commit(tmp_path, "docs: tweak external sibling", "EXTERNAL.md")

    doctrine = tmp_path / ".cortex" / "doctrine" / "0008-rule.md"
    doctrine.write_text(body_v2)
    _commit(tmp_path, "docs: add doctrine identical to sibling", ".cortex/doctrine/0008-rule.md")

    issues = check_immutable_doctrine(tmp_path)
    assert not any(
        "0008-rule.md" in (issue.path or "") for issue in issues
    ), [f"{i.path}: {i.message}" for i in issues]


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


# --- cortex#172: doctrine append-only baseline at file-introduction commit ---


def test_immutable_doctrine_introduction_only_no_warning(tmp_path: Path) -> None:
    """A doctrine entry whose only commit is its introduction must not warn.

    Regression for cortex#172 Layer 1: if the check incorrectly treats the
    introduction commit as a modification, this test will fail."""
    _git_cortex_project(tmp_path)
    entry = tmp_path / ".cortex" / "doctrine" / "0009-rule.md"
    entry.write_text(
        "---\nStatus: Accepted\nDate: 2026-05-07\nLoad-priority: always\n---\n\n# Rule\n\nBody.\n"
    )
    _commit(tmp_path, "docs: add doctrine 0009", ".cortex/doctrine/0009-rule.md")

    issues = check_immutable_doctrine(tmp_path)
    assert not any(
        "0009-rule.md" in (issue.path or "") for issue in issues
    ), [f"{i.path}: {i.message}" for i in issues]


def test_immutable_doctrine_real_modification_warns_with_sha(tmp_path: Path) -> None:
    """A doctrine entry with a real post-intro M commit warns, and the message
    names the offending commit SHA (first 12 chars)."""
    _git_cortex_project(tmp_path)
    entry = tmp_path / ".cortex" / "doctrine" / "0009-rule.md"
    body = "---\nStatus: Accepted\nDate: 2026-05-07\nLoad-priority: always\n---\n\n# Rule\n\nOriginal.\n"
    entry.write_text(body)
    _commit(tmp_path, "docs: add doctrine 0009", ".cortex/doctrine/0009-rule.md")

    entry.write_text(body.replace("Original.", "Mutated body."))
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", ".cortex/doctrine/0009-rule.md"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "docs: mutate doctrine"],
        check=True, capture_output=True, text=True,
    )
    sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    issues = check_immutable_doctrine(tmp_path)
    assert any(
        "0009-rule.md" in (issue.path or "")
        and "immutable Doctrine invariant violated" in issue.message
        and sha[:12] in issue.message
        for issue in issues
    ), [f"{i.path}: {i.message}" for i in issues]


def test_immutable_doctrine_grandfather_commit_silences_warning(tmp_path: Path) -> None:
    """A real M commit whose SHA is in [doctrine.append-only] grandfather-commits
    must not produce a warning — this is the cortex#172 Layer 2 fix."""
    _git_cortex_project(tmp_path)
    entry = tmp_path / ".cortex" / "doctrine" / "0009-rule.md"
    body = "---\nStatus: Accepted\nDate: 2026-05-07\nLoad-priority: always\n---\n\n# Rule\n\nOriginal.\n"
    entry.write_text(body)
    _commit(tmp_path, "docs: add doctrine 0009", ".cortex/doctrine/0009-rule.md")

    entry.write_text(body.replace("Original.", "Pre-invariant drift."))
    _run(tmp_path, "add", ".cortex/doctrine/0009-rule.md")
    _run(tmp_path, "commit", "-m", "docs: pre-invariant backfill")
    sha = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()

    (tmp_path / ".cortex" / "config.toml").write_text(
        f'[doctrine.append-only]\ngrandfather-commits = ["{sha}"]\n'
    )

    issues = check_immutable_doctrine(tmp_path)
    assert not any(
        "0009-rule.md" in (issue.path or "") for issue in issues
    ), [f"{i.path}: {i.message}" for i in issues]


def test_immutable_doctrine_non_grandfathered_modification_still_warns(tmp_path: Path) -> None:
    """A real M commit NOT in grandfather-commits must still warn — the
    grandfather list must not be a blanket suppressor."""
    _git_cortex_project(tmp_path)
    entry = tmp_path / ".cortex" / "doctrine" / "0009-rule.md"
    body = "---\nStatus: Accepted\nDate: 2026-05-07\nLoad-priority: always\n---\n\n# Rule\n\nOriginal.\n"
    entry.write_text(body)
    _commit(tmp_path, "docs: add doctrine 0009", ".cortex/doctrine/0009-rule.md")

    entry.write_text(body.replace("Original.", "Unauthorized mutation."))
    _run(tmp_path, "add", ".cortex/doctrine/0009-rule.md")
    _run(tmp_path, "commit", "-m", "docs: unauthorized mutation")

    (tmp_path / ".cortex" / "config.toml").write_text(
        '[doctrine.append-only]\ngrandfather-commits = ["0000000000000000000000000000000000000000"]\n'
    )

    issues = check_immutable_doctrine(tmp_path)
    assert any(
        "0009-rule.md" in (issue.path or "")
        and "immutable Doctrine invariant violated" in issue.message
        for issue in issues
    ), [f"{i.path}: {i.message}" for i in issues]


def test_config_toml_schema_validates_doctrine_append_only_section(tmp_path: Path) -> None:
    """Schema validator catches typos and type errors in [doctrine.append-only]."""
    _scaffold(tmp_path)

    # Valid value — no warnings.
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[doctrine.append-only]\ngrandfather-commits = ["abc123"]\n'
    )
    assert check_config_toml_schema(tmp_path) == []

    # Unknown key — warning.
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[doctrine.append-only]\ngrandfathered-shas = ["abc123"]\n'
    )
    issues = check_config_toml_schema(tmp_path)
    assert any(
        issue.severity == "warning" and "grandfathered-shas" in issue.message for issue in issues
    )

    # Wrong type (string instead of list) — error.
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[doctrine.append-only]\ngrandfather-commits = "abc123"\n'
    )
    issues = check_config_toml_schema(tmp_path)
    assert any(
        issue.severity == "error" and "grandfather-commits" in issue.message for issue in issues
    )


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


def _write_generated_state(project: Path, generated: datetime) -> None:
    (project / ".cortex" / "state.md").write_text(
        "---\n"
        f"Generated: {generated.isoformat()}\n"
        "Generator: cortex refresh-state v0.8.2\n"
        "Sources:\n"
        "  - .cortex/journal/*.md\n"
        "Corpus: test\n"
        "Omitted:\n"
        "  []\n"
        "Incomplete:\n"
        "  []\n"
        "Conflicts-preserved: []\n"
        "---\n\n"
        "# Project State\n"
    )


def test_state_source_freshness_allows_same_commit_state_and_source(tmp_path: Path) -> None:
    """Regression for cortex#112: State regenerated with its source in one commit is fresh."""
    _git_cortex_project(tmp_path)
    generated = datetime.now(UTC) - timedelta(minutes=3)
    committed_at = generated + timedelta(minutes=2)

    (tmp_path / ".cortex" / "journal" / "2026-05-04-release.md").write_text(
        "# Release\n\n**Date:** 2026-05-04\n**Type:** release\n\nBody.\n"
    )
    _write_generated_state(tmp_path, generated)
    _commit_at(
        tmp_path,
        "docs: regenerate state with release journal",
        committed_at,
        ".cortex/journal/2026-05-04-release.md",
        ".cortex/state.md",
    )

    issues = check_generated_layers(tmp_path)
    assert not any(
        issue.path == ".cortex/state.md"
        and "state.md generated before source changed" in issue.message
        for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]


def test_state_source_freshness_warns_for_source_commit_after_state(tmp_path: Path) -> None:
    generated = datetime.now(UTC) - timedelta(minutes=5)
    state_committed_at = generated + timedelta(minutes=1)
    source_committed_at = generated + timedelta(minutes=3)
    _scaffold(tmp_path)
    _git_init(tmp_path)
    _write_generated_state(tmp_path, generated)
    _commit_at(tmp_path, "docs: add generated state", state_committed_at, ".cortex")

    (tmp_path / ".cortex" / "journal" / "2026-05-04-release.md").write_text(
        "# Release\n\n**Date:** 2026-05-04\n**Type:** release\n\nBody.\n"
    )
    _commit_at(
        tmp_path,
        "docs: add release journal after state",
        source_committed_at,
        ".cortex/journal/2026-05-04-release.md",
    )

    issues = check_generated_layers(tmp_path)
    assert any(
        issue.path == ".cortex/state.md"
        and "state.md generated before source changed" in issue.message
        and ".cortex/journal/2026-05-04-release.md" in issue.message
        for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]


def test_cli_less_fallback_threshold_warns(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "CLAUDE.md").write_text(
        "# Agent guidance\n\n@.cortex/protocol.md\n@.cortex/state.md\n"
    )
    doctrine = tmp_path / ".cortex" / "doctrine"
    for i in range(21):
        (doctrine / f"{i + 1:04d}-entry.md").write_text("# Entry\n")

    issues = check_cli_less_fallback(tmp_path)
    assert any(
        issue.path == "CLAUDE.md"
        and "fallback-only Cortex imports" in issue.message
        and "cortex manifest --budget <N>" in issue.message
        for issue in issues
    )


def test_cli_less_fallback_threshold_accepts_manifest_guidance(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / "AGENTS.md").write_text(
        "# Agent guidance\n\n"
        "Run `cortex manifest --budget <N>` first.\n"
        "Fallback when the CLI is unavailable:\n"
        "@.cortex/protocol.md\n"
        "@.cortex/state.md\n"
    )
    doctrine = tmp_path / ".cortex" / "doctrine"
    for i in range(21):
        (doctrine / f"{i + 1:04d}-entry.md").write_text("# Entry\n")

    assert check_cli_less_fallback(tmp_path) == []


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


def test_state_warns_when_source_changed_after_generated_timestamp(tmp_path: Path) -> None:
    """Regression for dogfood drift: State generated recently enough by age
    can still be stale if newer Journal/Plan/Doctrine sources exist."""
    _scaffold(tmp_path)
    generated = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    state = tmp_path / ".cortex" / "state.md"
    state.write_text(
        f"---\n"
        f"Generated: {generated}\n"
        "Generator: cortex refresh-state\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "---\n\n# State\n"
    )
    (tmp_path / ".cortex" / "journal" / "2026-05-04-release.md").write_text(
        "# Release\n\n**Date:** 2026-05-04\n**Type:** release\n\nNewer source.\n"
    )

    issues = check_generated_layers(tmp_path)
    assert any(
        "state.md generated before source changed" in issue.message
        and ".cortex/journal/2026-05-04-release.md" in issue.message
        for issue in issues
    ), [issue.message for issue in issues]


def _write_state_with_sources_hash(
    project: Path,
    generated: datetime,
    source_hashes: dict[str, str],
) -> None:
    """Write a state.md that includes a Sources-hash: block (SPEC v1.1.0+)."""
    hash_lines = "".join(f"  {p}: {h}\n" for p, h in sorted(source_hashes.items()))
    (project / ".cortex" / "state.md").write_text(
        "---\n"
        f"Generated: {generated.isoformat()}\n"
        "Generator: cortex refresh-state v1.0.0\n"
        "Sources:\n"
        "  - .cortex/journal/*.md\n"
        "Sources-hash:\n"
        f"{hash_lines}"
        "Corpus: test\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "Spec: 1.1.0\n"
        "---\n\n"
        "# Project State\n"
    )


def test_sources_hash_touch_does_not_warn(tmp_path: Path) -> None:
    """Regression for cortex#171: touching a source's mtime without changing
    content must not trigger a staleness warning when Sources-hash: is present."""
    from cortex.state_render import build_state_inputs, render_state

    _scaffold(tmp_path)
    journal_path = tmp_path / ".cortex" / "journal" / "2026-05-06-decision.md"
    journal_path.write_text(
        "# Decision\n\n**Date:** 2026-05-06\n**Type:** decision\n\nBody.\n"
    )
    # Generate state.md via the real pipeline so Sources-hash: covers all sources.
    inputs = build_state_inputs(tmp_path)
    (tmp_path / ".cortex" / "state.md").write_text(render_state(inputs))

    # Touch mtime on the journal file — content unchanged.
    import os
    future = (datetime.now(UTC) + timedelta(seconds=10)).timestamp()
    os.utime(journal_path, (future, future))

    issues = check_generated_layers(tmp_path)
    assert not any(
        issue.path == ".cortex/state.md"
        and ("state.md generated before source changed" in issue.message
             or "hash mismatch" in issue.message)
        for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]


def test_sources_hash_content_change_warns(tmp_path: Path) -> None:
    """When a source's content actually changes, doctor must warn using
    the hash-mismatch message (not the mtime message)."""
    from cortex.state_render import build_state_inputs, render_state

    _scaffold(tmp_path)
    journal_path = tmp_path / ".cortex" / "journal" / "2026-05-06-decision.md"
    journal_path.write_text(
        "# Decision\n\n**Date:** 2026-05-06\n**Type:** decision\n\nOriginal.\n"
    )
    inputs = build_state_inputs(tmp_path)
    (tmp_path / ".cortex" / "state.md").write_text(render_state(inputs))

    # Modify the source content.
    journal_path.write_text(
        "# Decision\n\n**Date:** 2026-05-06\n**Type:** decision\n\nModified.\n"
    )

    issues = check_generated_layers(tmp_path)
    assert any(
        issue.path == ".cortex/state.md"
        and "state.md source content changed" in issue.message
        and "hash mismatch" in issue.message
        for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]


def test_sources_hash_absent_falls_back_to_mtime(tmp_path: Path) -> None:
    """Pre-v1.1 state.md without Sources-hash: uses the existing mtime-based
    fallback so old scaffolds keep working."""
    _scaffold(tmp_path)
    generated = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    (tmp_path / ".cortex" / "state.md").write_text(
        f"---\n"
        f"Generated: {generated}\n"
        "Generator: cortex refresh-state v0.9.0\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "Spec: 1.0.0\n"
        "---\n\n# State\n"
    )
    (tmp_path / ".cortex" / "journal" / "2026-05-06-release.md").write_text(
        "# Release\n\n**Date:** 2026-05-06\n**Type:** release\n\nBody.\n"
    )

    issues = check_generated_layers(tmp_path)
    assert any(
        issue.path == ".cortex/state.md"
        and "state.md generated before source changed" in issue.message
        and ".cortex/journal/2026-05-06-release.md" in issue.message
        for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]


def test_legacy_hand_authored_state_warns_to_migrate(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    state = tmp_path / ".cortex" / "state.md"
    state.write_text(
        "---\n"
        "Generated: 2026-04-18T22:00:00-07:00\n"
        "Generator: hand-authored (regeneration infrastructure ships in Cortex Phase C)\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "Spec: 0.3.1\n"
        "---\n\n"
        "# Project State\n\n"
        "Hand-authored content.\n"
    )

    issues = check_legacy_state_migration_needed(tmp_path)
    assert any("cortex migrate-state" in issue.message for issue in issues)


def test_state_journal_staleness_warns_past_default_window(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    state = tmp_path / ".cortex" / "state.md"
    state.write_text(
        "---\n"
        "Generated: 2026-04-01T00:00:00+00:00\n"
        "Generator: cortex refresh-state\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "---\n\n"
        "# State\n"
    )
    (tmp_path / ".cortex" / "journal" / "2026-04-10-release.md").write_text(
        "# Release\n\n**Date:** 2026-04-10\n**Type:** release\n"
    )

    issues = check_state_journal_staleness(tmp_path)
    assert any("older than latest journal entry" in issue.message for issue in issues)


def test_state_journal_staleness_honors_window_config(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "config.toml").write_text(
        "[doctor.state-staleness]\nwindow_days = 30\n"
    )
    state = tmp_path / ".cortex" / "state.md"
    state.write_text(
        "---\n"
        "Generated: 2026-04-01T00:00:00+00:00\n"
        "Generator: cortex refresh-state\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "---\n\n"
        "# State\n"
    )
    (tmp_path / ".cortex" / "journal" / "2026-04-10-release.md").write_text(
        "# Release\n\n**Date:** 2026-04-10\n**Type:** release\n"
    )

    assert check_state_journal_staleness(tmp_path) == []


def test_config_toml_schema_type_and_unknown_key(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[audit-instructions]\nhomebrew_tap = ["a", "b"]\nunknown_key = "x"\n'
    )

    issues = check_config_toml_schema(tmp_path)
    assert any(issue.severity == "error" and "homebrew_tap" in issue.message for issue in issues)
    assert any(issue.severity == "warning" and "unknown_key" in issue.message for issue in issues)


def test_config_toml_schema_gh_release_no_longer_silently_accepted(tmp_path: Path) -> None:
    """Regression for cortex#93 — `gh_release` was schema-validated but never
    parsed by config.AuditInstructionsConfig. Now it warns as unknown so users
    move to `github_repos` instead of silently misconfiguring."""

    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[audit-instructions]\ngh_release = "https://example.com/releases"\n'
    )
    issues = check_config_toml_schema(tmp_path)
    assert any(issue.severity == "warning" and "gh_release" in issue.message for issue in issues)


def test_config_toml_schema_validates_refresh_index_section(tmp_path: Path) -> None:
    """Regression for cortex#94 — `[refresh-index]` was consumed by
    config.RefreshIndexConfig but absent from the doctor schema, so unknown
    keys silently passed and typos went undetected."""

    _scaffold(tmp_path)
    # Valid known key — no warnings.
    (tmp_path / ".cortex" / "config.toml").write_text(
        "[refresh-index]\ncandidate_patterns = [\"decision\", \"incident\"]\n"
    )
    issues = check_config_toml_schema(tmp_path)
    assert not any("refresh-index" in issue.message for issue in issues)

    # Typo on the known key — must surface as unknown.
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[refresh-index]\ncandidate_pattern = ["decision"]\n'
    )
    issues = check_config_toml_schema(tmp_path)
    assert any(
        issue.severity == "warning" and "candidate_pattern" in issue.message
        for issue in issues
    )

    # Wrong type — must surface as error.
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[refresh-index]\ncandidate_patterns = "not-a-list"\n'
    )
    issues = check_config_toml_schema(tmp_path)
    assert any(
        issue.severity == "error" and "candidate_patterns" in issue.message
        for issue in issues
    )


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


# --- cortex#100 stale-plan-checkbox check -----------------------------------


def _write_active_plan_with_items(project: Path, items: list[str], slug: str = "active") -> None:
    """Write a SPEC-valid active plan whose ## Work items section is `items`."""
    title = "Active Plan"
    body_items = "\n".join(items)
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
        f"## Work items\n{body_items}\n"
    )


def _write_active_plan_with_pickup(project: Path, pickup: str, slug: str = "active") -> None:
    """Write a SPEC-valid active plan with high-authority pickup prose."""
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
        f"## Pickup pointer\n\n{pickup}\n\n"
        "## Why (grounding)\nLinks to doctrine/0001.\n\n"
        "## Success Criteria\nAll `tests/test_doctor_invariants.py` pass (signal: `pytest -q` exit 0).\n\n"
        "## Approach\nImplement the invariant.\n\n"
        "## Work items\n- [ ] Future work that does not overlap.\n"
    )


def _write_release_journal(
    project: Path,
    *,
    slug: str,
    iso_date: str,
    what_shipped: str,
) -> Path:
    """Write a `Type: release` journal entry with a `## What shipped` section."""
    path = project / ".cortex" / "journal" / f"{iso_date}-{slug}.md"
    path.write_text(
        f"# {slug}\n\n"
        f"**Date:** {iso_date}\n"
        "**Type:** release\n"
        "**Trigger:** T1.10\n"
        f"**Tag:** v0.7.0\n\n"
        "## Artifact\n\nA thing.\n\n"
        f"## What shipped\n\n{what_shipped}\n"
    )
    return path


def test_stale_checkbox_warns_on_pr_ref_overlap(tmp_path: Path) -> None:
    """Happy path — checkbox with PR #95 + journal mentioning PR #95 fires."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **v0.7.0 retrieve interface (PR #95)** — `cortex retrieve --mode bm25`.",
        ],
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=today,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    issues = check_stale_plan_checkboxes(tmp_path)
    assert any(
        "likely shipped per .cortex/journal/" in issue.message
        and "v0.7.0 retrieve interface" in issue.message
        for issue in issues
    ), [issue.message for issue in issues]


@pytest.mark.parametrize("root_filename", ["ROADMAP.md", "STATUS.md", "PLAN.md", "NEXT.md", "TODO.md"])
def test_stale_checkbox_does_not_scan_repo_root_duplicates(
    tmp_path: Path, root_filename: str
) -> None:
    """Doctrine 0007 root duplicates are not stale-checkbox scan inputs."""
    _scaffold(tmp_path)
    stale_root_checkbox = "- [ ] v0.7.0 retrieve interface (PR #95)"
    (tmp_path / root_filename).write_text(f"# Duplicate\n\n{stale_root_checkbox}\n")
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **Future v2.0 multi-agent orchestration layer** — entirely future scope.",
        ],
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=today,
        what_shipped="What shipped: cortex retrieve --mode bm25 (PR #95)",
    )

    issues = check_stale_plan_checkboxes(tmp_path)
    assert not any(
        root_filename in (issue.path or "") or root_filename in issue.message
        for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]
    assert not any(
        "v0.7.0 retrieve interface" in issue.message for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]


def test_stale_checkbox_no_warn_when_already_flipped(tmp_path: Path) -> None:
    """Same fixture but the checkbox is `- [x]` — no warning."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [x] **v0.7.0 retrieve interface (PR #95)** — `cortex retrieve --mode bm25`.",
        ],
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=today,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    assert check_stale_plan_checkboxes(tmp_path) == []


def test_stale_checkbox_bypass_annotation_suppresses(tmp_path: Path) -> None:
    """Checkbox with `<!-- cortex:no-stale-check -->` is exempt."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **Sustained-work period (PR #95).** <!-- cortex:no-stale-check --> "
            "Aspirational item that overlaps release prose without being shipped.",
        ],
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=today,
        what_shipped="### Slice (PR #95)\n\nClosed sustained-work period item.",
    )

    assert check_stale_plan_checkboxes(tmp_path) == []


def test_stale_checkbox_outside_window_no_warn(tmp_path: Path) -> None:
    """Matching journal whose Date is older than window_days — no warning."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **v0.7.0 retrieve interface (PR #95)** — `cortex retrieve --mode bm25`.",
        ],
    )
    old = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=old,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    assert check_stale_plan_checkboxes(tmp_path, window_days=14) == []


def test_stale_checkbox_config_window_override_warns(tmp_path: Path) -> None:
    """Matching journal at 30 days + window_days=60 in config — warns."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **v0.7.0 retrieve interface (PR #95)** — `cortex retrieve --mode bm25`.",
        ],
    )
    old = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=old,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )
    (tmp_path / ".cortex" / "config.toml").write_text(
        "[doctor.stale-checkbox]\nwindow_days = 60\n"
    )

    issues = check_stale_plan_checkboxes(tmp_path)
    assert any("likely shipped per" in issue.message for issue in issues)


def test_stale_checkbox_brief_path_overlap_warns(tmp_path: Path) -> None:
    """Strong signal #2 — shared `briefs/<name>.md` reference fires the check."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **Real promote writer** — see `briefs/v0.6.0-T2-promote-real-writer.md`.",
        ],
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v060-released",
        iso_date=today,
        what_shipped="Closed `briefs/v0.6.0-T2-promote-real-writer.md` — real promote writer landed.",
    )

    issues = check_stale_plan_checkboxes(tmp_path)
    assert any("likely shipped per" in issue.message for issue in issues)


def test_stale_checkbox_unrelated_checkbox_no_warn(tmp_path: Path) -> None:
    """A genuinely unrelated checkbox does not fire — guards against false positives."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **Future v2.0 multi-agent orchestration layer** — entirely future scope.",
        ],
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=today,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    assert check_stale_plan_checkboxes(tmp_path) == []


def test_stale_checkbox_inactive_plan_skipped(tmp_path: Path) -> None:
    """A `Status: shipped` plan's checkboxes are not scanned — only active plans."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **v0.7.0 retrieve interface (PR #95)** — shipped item.",
        ],
        slug="active",
    )
    # Mark the plan shipped so the check skips it.
    plan = tmp_path / ".cortex" / "plans" / "active.md"
    plan.write_text(plan.read_text().replace("Status: active", "Status: shipped"))
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=today,
        what_shipped="### Slice (PR #95)\n\nClosed.",
    )

    assert check_stale_plan_checkboxes(tmp_path) == []


def test_stale_checkbox_runs_on_plain_doctor(tmp_path: Path) -> None:
    """End-to-end — `cortex doctor` surfaces the warning in its CLI output."""
    _scaffold(tmp_path)
    _write_active_plan_with_items(
        tmp_path,
        items=[
            "- [ ] **v0.7.0 retrieve interface (PR #95)** — `cortex retrieve --mode bm25`.",
        ],
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v070-released",
        iso_date=today,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    result = CliRunner().invoke(cli, ["doctor", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "likely shipped per .cortex/journal/" in result.output


def test_stale_pickup_pointer_warns_on_recent_shipped_overlap(tmp_path: Path) -> None:
    """Pickup pointers are checked too; they are what fresh agents read first."""
    _scaffold(tmp_path)
    _write_active_plan_with_pickup(
        tmp_path,
        "The next action is v0.7.0 retrieve interface work from PR #95.",
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v080-released",
        iso_date=today,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    issues = check_stale_pickup_pointers(tmp_path)
    assert any(
        "pickup pointer likely stale per .cortex/journal/" in issue.message
        for issue in issues
    ), [issue.message for issue in issues]


def test_stale_pickup_pointer_runs_on_plain_doctor(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    _write_active_plan_with_pickup(
        tmp_path,
        "The next action is v0.7.0 retrieve interface work from PR #95.",
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v080-released",
        iso_date=today,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    result = CliRunner().invoke(cli, ["doctor", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "pickup pointer likely stale per .cortex/journal/" in result.output


def test_stale_state_current_work_warns_on_recent_shipped_overlap(tmp_path: Path) -> None:
    """State hand regions can survive refresh; current-work prose needs its own guard."""
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "state.md").write_text(
        "---\n"
        f"Generated: {datetime.now(UTC).isoformat()}\n"
        "Generator: cortex refresh-state\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "---\n\n"
        "# Project State\n\n"
        "## Current work\n\n"
        "- v0.7.0 retrieve interface work from PR #95 is next.\n"
    )
    today = datetime.now(UTC).date().isoformat()
    _write_release_journal(
        tmp_path,
        slug="v080-released",
        iso_date=today,
        what_shipped="### Slice S2 of `cortex retrieve` (PR #95)\n\nClosed v0.7.0 retrieve interface.",
    )

    issues = check_stale_state_current_work(tmp_path)
    assert any(
        "state current work likely stale per .cortex/journal/" in issue.message
        for issue in issues
    ), [issue.message for issue in issues]


def test_stale_checkbox_config_schema_typo_warns(tmp_path: Path) -> None:
    """Schema-validator regression — `[doctor.stale-checkbox]` typo surfaces.

    Mirrors the cortex#94 pattern: a config section consumed at runtime must
    also be schema-validated, otherwise typos silently fall back to defaults.
    """
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "config.toml").write_text(
        "[doctor.stale-checkbox]\nwindow_day = 30\n"
    )

    issues = check_config_toml_schema(tmp_path)
    assert any(
        issue.severity == "warning" and "window_day" in issue.message for issue in issues
    )

    # Wrong type (string, not int) — error.
    (tmp_path / ".cortex" / "config.toml").write_text(
        '[doctor.stale-checkbox]\nwindow_days = "fourteen"\n'
    )
    issues = check_config_toml_schema(tmp_path)
    assert any(
        issue.severity == "error" and "window_days" in issue.message for issue in issues
    )

    # Negative int — error (must be positive).
    (tmp_path / ".cortex" / "config.toml").write_text(
        "[doctor.stale-checkbox]\nwindow_days = -1\n"
    )
    issues = check_config_toml_schema(tmp_path)
    assert any(
        issue.severity == "error" and "window_days" in issue.message for issue in issues
    )

    # Valid value — no warnings/errors for this section.
    (tmp_path / ".cortex" / "config.toml").write_text(
        "[doctor.stale-checkbox]\nwindow_days = 30\n"
    )
    issues = check_config_toml_schema(tmp_path)
    assert not any("stale-checkbox" in issue.message for issue in issues)
