"""Tests for `cortex doctor --audit` and `--audit-digests`.

Each audit test builds a real git repo in tmp_path so the command exercises
actual ``git log`` output instead of mocked diffs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.audit import Trigger, audit, audit_digests, classify, load_commits, load_tags
from cortex.cli import cli
from cortex.commands.init import init_command


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(path: Path) -> None:
    _run(path, "init", "-b", "main")
    _run(path, "config", "user.email", "t@example.com")
    _run(path, "config", "user.name", "Test")
    _run(path, "commit", "--allow-empty", "-m", "initial")


def _commit(path: Path, subject: str, files: dict[str, str]) -> None:
    for rel, body in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    _run(path, "add", "-A")
    _run(path, "commit", "-m", subject)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    _git_init(tmp_path)
    _run(tmp_path, "add", "-A")
    _run(tmp_path, "commit", "-m", "initial cortex scaffold")
    return tmp_path


def test_t1_1_fires_on_doctrine_touch(git_project: Path) -> None:
    _commit(git_project, "docs: add doctrine", {".cortex/doctrine/0001-why.md": "# 0001 — Why\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"})
    commits = load_commits(git_project, since_days=30)
    assert commits, "commits must be loadable"
    hit = next(c for c in commits if "add doctrine" in c.subject)
    assert Trigger.T1_1 in classify(hit)


def test_t1_5_fires_on_dep_manifest_change(git_project: Path) -> None:
    _commit(git_project, "chore: bump deps", {"pyproject.toml": "[project]\nname = 'x'\n"})
    commits = load_commits(git_project, since_days=30)
    hit = next(c for c in commits if "bump deps" in c.subject)
    assert Trigger.T1_5 in classify(hit)


def test_t1_8_fires_on_regression_fix(git_project: Path) -> None:
    _commit(git_project, "fix: prevent regression in retry backoff", {"notes.md": "x"})
    commits = load_commits(git_project, since_days=30)
    hit = next(c for c in commits if "regression" in c.subject)
    assert Trigger.T1_8 in classify(hit)


def test_audit_matches_when_journal_has_matching_type(git_project: Path) -> None:
    # Doctrine touch + one decision entry per firing commit. The scaffold
    # commit also touches .cortex/doctrine/.gitkeep and fires T1.1, so both
    # fires need their own Journal entry under the one-entry-per-fire rule.
    _commit(
        git_project,
        "feat: add doctrine entry",
        {".cortex/doctrine/0001-why.md": "# 0001\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"},
    )
    commits = load_commits(git_project, since_days=30)
    date = commits[0].date.date().isoformat()
    journal_dir = git_project / ".cortex" / "journal"
    (journal_dir / f"{date}-scaffold-decision.md").write_text(
        f"# Scaffold decision\n\n**Date:** {date}\n**Type:** decision\n\nbody\n"
    )
    (journal_dir / f"{date}-add-doctrine-decision.md").write_text(
        f"# Add doctrine decision\n\n**Date:** {date}\n**Type:** decision\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_1_fires = [f for f in report.fires if f.trigger == Trigger.T1_1]
    assert t1_1_fires
    assert all(f.matched for f in t1_1_fires)


def test_journal_entry_satisfies_at_most_one_fire(git_project: Path) -> None:
    # Two doctrine-touching commits, only one decision entry — exactly one
    # T1.1 fire should match; the other must remain unmatched.
    _commit(
        git_project,
        "feat: first doctrine entry",
        {".cortex/doctrine/0001-one.md": "# 0001\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"},
    )
    _commit(
        git_project,
        "feat: second doctrine entry",
        {".cortex/doctrine/0002-two.md": "# 0002\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"},
    )
    commits = load_commits(git_project, since_days=30)
    date = commits[0].date.date().isoformat()
    (git_project / ".cortex" / "journal" / f"{date}-one-decision.md").write_text(
        f"# Only decision\n\n**Date:** {date}\n**Type:** decision\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_1_fires = [f for f in report.fires if f.trigger == Trigger.T1_1]
    matched = [f for f in t1_1_fires if f.matched]
    unmatched = [f for f in t1_1_fires if not f.matched]
    assert len(matched) == 1, (matched, unmatched)
    assert len(unmatched) >= 1


def test_audit_warns_when_no_matching_journal(git_project: Path) -> None:
    _commit(
        git_project,
        "fix: regression in pipeline",
        {"src/pipeline.py": "pass\n"},
    )
    report = audit(git_project, since_days=30)
    unmatched = [f for f in report.fires if not f.matched]
    assert any(f.trigger == Trigger.T1_8 for f in unmatched)


def test_mismatched_trigger_does_not_satisfy_fire(git_project: Path) -> None:
    # A Journal entry declares Trigger: T1.8 but the fire is a T1.1 fire.
    # Entry should not satisfy the T1.1 fire even though Type matches.
    _commit(
        git_project,
        "feat: add doctrine entry",
        {".cortex/doctrine/0001-why.md": "# 0001\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"},
    )
    commits = load_commits(git_project, since_days=30)
    date = commits[0].date.date().isoformat()
    (git_project / ".cortex" / "journal" / f"{date}-wrong-trigger.md").write_text(
        f"# Wrong trigger\n\n**Date:** {date}\n**Type:** decision\n"
        "**Trigger:** T1.8 (commit message pattern)\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_1_fires = [f for f in report.fires if f.trigger == Trigger.T1_1]
    matched = [f for f in t1_1_fires if f.matched]
    assert not matched, "Trigger T1.8 journal entry must not satisfy a T1.1 fire"


def test_human_authored_entry_without_trigger_still_matches(git_project: Path) -> None:
    _commit(
        git_project,
        "feat: add doctrine entry",
        {".cortex/doctrine/0001-why.md": "# 0001\n\n**Status:** Accepted\n**Date:** 2026-04-17\n**Load-priority:** default\n"},
    )
    commits = load_commits(git_project, since_days=30)
    date = commits[0].date.date().isoformat()
    journal_dir = git_project / ".cortex" / "journal"
    # Two decision entries without Trigger: both fires (scaffold + doctrine)
    # should match.
    (journal_dir / f"{date}-decision-a.md").write_text(
        f"# Decision A\n\n**Date:** {date}\n**Type:** decision\n\nbody\n"
    )
    (journal_dir / f"{date}-decision-b.md").write_text(
        f"# Decision B\n\n**Date:** {date}\n**Type:** decision\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_1_fires = [f for f in report.fires if f.trigger == Trigger.T1_1]
    assert t1_1_fires
    assert all(f.matched for f in t1_1_fires)


def test_audit_digests_ignores_frontmatter_lists(git_project: Path) -> None:
    # Digest has uncited body bullets but its frontmatter Sources list has
    # journal/... references. The fix excludes frontmatter list items from
    # the sample so the body claims alone drive the warning.
    date = "2026-04-17"
    (git_project / ".cortex" / "journal" / f"{date}-digest.md").write_text(
        "---\n"
        f"Date: {date}\n"
        "Type: digest\n"
        "Sources:\n"
        "  - journal/2026-04-01-foo.md\n"
        "  - journal/2026-04-02-bar.md\n"
        "---\n\n"
        "# Digest\n\n"
        "- uncited claim one\n"
        "- uncited claim two\n"
        "- uncited claim three\n"
    )
    warnings = audit_digests(git_project)
    assert any("digest.md" in w for w in warnings)


def test_audit_digests_flags_missing_citations(git_project: Path) -> None:
    date = "2026-04-17"
    (git_project / ".cortex" / "journal" / f"{date}-march-digest.md").write_text(
        f"# March digest\n\n**Date:** {date}\n**Type:** digest\n\n"
        "- first claim with no citation\n"
        "- second claim, also no citation\n"
        "- third claim, again no citation\n"
    )
    warnings = audit_digests(git_project)
    assert any("march-digest" in w for w in warnings)


def test_audit_digests_clean_when_citations_present(git_project: Path) -> None:
    date = "2026-04-17"
    (git_project / ".cortex" / "journal" / f"{date}-clean-digest.md").write_text(
        f"# Clean digest\n\n**Date:** {date}\n**Type:** digest\n\n"
        "- first — see `journal/2026-04-01-foo.md`\n"
        "- second per journal/2026-04-02-bar.md\n"
        "- third, journal/2026-04-03-baz.md\n"
    )
    warnings = audit_digests(git_project)
    assert not any("clean-digest" in w for w in warnings)


def test_t1_9_does_not_fire_on_feature_branch_commits(git_project: Path) -> None:
    # Create a commit on a feature branch; T1.9 should not fire against it
    # because the feature-branch commit isn't reachable from main's history.
    _run(git_project, "checkout", "-b", "feat/x")
    _commit(git_project, "docs: feature branch wip", {"notes.md": "wip\n"})
    feature_sha = _run(git_project, "rev-parse", "HEAD").stdout.strip()
    _run(git_project, "checkout", "main")
    commits = load_commits(git_project, since_days=30)
    assert all(c.sha != feature_sha for c in commits), "feature-branch commit must not be audited"


def test_merge_commit_fan_out_uses_first_parent(git_project: Path) -> None:
    # Non-squash merge flow: feature branch with N commits, then a real
    # merge commit on main. Audit must see only the mainline merge commit,
    # not one entry per feature-branch WIP commit.
    _run(git_project, "checkout", "-b", "feat/multi")
    _commit(git_project, "wip: step one", {"notes.md": "1\n"})
    _commit(git_project, "wip: step two", {"notes.md": "2\n"})
    _commit(git_project, "wip: step three", {"notes.md": "3\n"})
    _run(git_project, "checkout", "main")
    _run(git_project, "merge", "--no-ff", "feat/multi", "-m", "Merge feat/multi")
    commits = load_commits(git_project, since_days=30)
    subjects = [c.subject for c in commits]
    assert "Merge feat/multi" in subjects
    # The WIP commits must not appear — first-parent stops at the merge.
    assert not any(s.startswith("wip: step ") for s in subjects), subjects


def test_t1_10_load_tags_filters_by_pattern(git_project: Path) -> None:
    # Tag two refs: one matching v0.3.0 (should fire), one not (should not).
    _run(git_project, "tag", "v0.3.0", "-m", "Release 0.3.0")
    _run(git_project, "tag", "experimental")
    tags = load_tags(git_project, since_days=30)
    names = [t.name for t in tags]
    assert "v0.3.0" in names
    assert "experimental" not in names


def test_t1_10_fires_per_release_tag(git_project: Path) -> None:
    _run(git_project, "tag", "v0.3.0", "-m", "Release 0.3.0")
    report = audit(git_project, since_days=30)
    t1_10_fires = [f for f in report.fires if f.trigger == Trigger.T1_10]
    assert len(t1_10_fires) == 1
    assert t1_10_fires[0].tag is not None
    assert t1_10_fires[0].tag.name == "v0.3.0"


def test_t1_10_matches_release_journal_entry_within_window(git_project: Path) -> None:
    _run(git_project, "tag", "v0.3.0", "-m", "Release 0.3.0")
    # Journal entry dated today with Type: release should match the tag.
    from datetime import datetime
    date = datetime.now().date().isoformat()
    (git_project / ".cortex" / "journal" / f"{date}-v0.3.0-released.md").write_text(
        f"# Release v0.3.0\n\n**Date:** {date}\n**Type:** release\n**Trigger:** T1.10\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_10_fires = [f for f in report.fires if f.trigger == Trigger.T1_10]
    assert t1_10_fires
    assert all(f.matched for f in t1_10_fires)


def test_t1_10_unmatched_when_no_release_journal(git_project: Path) -> None:
    _run(git_project, "tag", "v0.3.0", "-m", "Release 0.3.0")
    report = audit(git_project, since_days=30)
    t1_10_unmatched = [
        f for f in report.fires if f.trigger == Trigger.T1_10 and not f.matched
    ]
    assert t1_10_unmatched, "tag without release entry should remain unmatched"


def test_t1_10_journal_must_name_the_tag(git_project: Path) -> None:
    """One Type: release entry must not satisfy every nearby release tag —
    the audit requires the entry's filename to contain the specific tag
    name. Otherwise a v0.3.0 release entry could falsely match a v0.2.4
    tag created in the same window."""
    _run(git_project, "tag", "v0.3.0", "-m", "Release 0.3.0")
    _run(git_project, "tag", "v0.2.4", "-m", "Release 0.2.4")
    from datetime import datetime
    date = datetime.now().date().isoformat()
    # One release entry naming v0.3.0 only.
    (git_project / ".cortex" / "journal" / f"{date}-v0.3.0-released.md").write_text(
        f"# Release v0.3.0\n\n**Date:** {date}\n**Type:** release\n**Trigger:** T1.10\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_10 = {f.tag.name: f.matched for f in report.fires if f.trigger == Trigger.T1_10}
    assert t1_10 == {"v0.3.0": True, "v0.2.4": False}, t1_10


def test_t1_10_decision_journal_does_not_satisfy_release_fire(git_project: Path) -> None:
    _run(git_project, "tag", "v0.3.0", "-m", "Release 0.3.0")
    from datetime import datetime
    date = datetime.now().date().isoformat()
    # A decision entry — wrong Type, must not satisfy a T1.10 release fire.
    (git_project / ".cortex" / "journal" / f"{date}-decision.md").write_text(
        f"# Decision\n\n**Date:** {date}\n**Type:** decision\n\nbody\n"
    )
    report = audit(git_project, since_days=30)
    t1_10_fires = [f for f in report.fires if f.trigger == Trigger.T1_10]
    assert t1_10_fires and not any(f.matched for f in t1_10_fires)


def test_cli_audit_flag_runs_without_error(git_project: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--audit", "--path", str(git_project), "--since-days", "30"])
    # Clean scaffold still passes the structural check; audit warnings on
    # stderr don't change exit code.
    assert result.exit_code == 0, result.output + (getattr(result, "stderr", "") or "")
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "--audit" in combined


def test_cli_audit_digests_flag(git_project: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--audit-digests", "--path", str(git_project)])
    assert result.exit_code == 0, result.output
    assert "--audit-digests" in result.output
