"""Tests for the orphan-deferral check (SPEC § 4.2).

Active plans whose ``## Follow-ups (deferred)`` bullets lack a citation to
a durable-layer entry (`plans/`, `journal/`, or `doctrine/`) surface as
warnings. Shipped/cancelled plans are skipped — their resolutions live in
git history rather than as in-tree citations, so warning would be noise.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.commands.init import init_command
from cortex.goal_hash import normalize_goal_hash
from cortex.validation import Severity, run_all_checks


@pytest.fixture
def cortex_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write_plan(project: Path, slug: str, status: str, followups_section: str) -> Path:
    """Helper: write a syntactically-valid Plan with the given Follow-ups body."""
    title = f"Test plan {slug}"
    goal_hash = normalize_goal_hash(title)
    plan = project / ".cortex" / "plans" / f"{slug}.md"
    plan.write_text(
        f"---\n"
        f"Status: {status}\n"
        f"Written: 2026-04-25\n"
        f"Author: human\n"
        f"Goal-hash: {goal_hash}\n"
        f"Updated-by:\n"
        f"  - 2026-04-25T22:00 human (test fixture)\n"
        f"Cites: doctrine/0001\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"> Body\n\n"
        f"## Why (grounding)\n\n"
        f"Grounded in `doctrine/0001`.\n\n"
        f"## Approach\n\nApproach prose.\n\n"
        f"## Success Criteria\n\n- Test exits 0.\n\n"
        f"## Work items\n\n- [ ] item\n\n"
        f"## Follow-ups (deferred)\n\n"
        f"{followups_section}\n"
    )
    return plan


def _orphan_warnings(project: Path, plan_path: Path) -> list[str]:
    rel = str(plan_path.relative_to(project))
    issues = run_all_checks(project)
    return [
        i.message
        for i in issues
        if i.path == rel
        and i.severity is Severity.WARNING
        and "Follow-ups (deferred)" in i.message
    ]


def test_orphan_warns_on_uncited_bullet(cortex_project: Path) -> None:
    plan = _write_plan(
        cortex_project,
        "orphan-uncited",
        "active",
        "- This bullet has no resolution citation at all.\n",
    )
    warnings = _orphan_warnings(cortex_project, plan)
    assert len(warnings) == 1, warnings
    assert "lacks resolution citation" in warnings[0]


def _create_journal_entry(project: Path, name: str) -> None:
    (project / ".cortex" / "journal" / f"{name}.md").write_text(
        f"# {name}\n\n**Date:** 2026-04-25\n**Type:** decision\n\nbody\n"
    )


def _create_doctrine_entry(project: Path, name: str) -> None:
    (project / ".cortex" / "doctrine" / f"{name}.md").write_text(
        f"# 0005 — {name}\n\n**Status:** Accepted\n**Date:** 2026-04-25\n**Load-priority:** default\n"
    )


def _create_plan_entry(project: Path, slug: str, status: str = "shipped") -> None:
    (project / ".cortex" / "plans" / f"{slug}.md").write_text(
        f"---\n"
        f"Status: {status}\n"
        f"Written: 2026-04-25\n"
        f"Author: human\n"
        f"Goal-hash: 12345678\n"
        f"Updated-by:\n  - 2026-04-25T22:00 human (test)\n"
        f"Cites: doctrine/0001\n"
        f"---\n\n# {slug}\n\nplaceholder\n"
    )


def test_orphan_clean_when_bullet_cites_existing_journal(cortex_project: Path) -> None:
    _create_journal_entry(cortex_project, "2026-04-25-foo")
    plan = _write_plan(
        cortex_project,
        "orphan-journal",
        "active",
        "- Resolved by `journal/2026-04-25-foo.md` per SPEC § 4.2.\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_clean_when_bullet_cites_existing_plan(cortex_project: Path) -> None:
    _create_plan_entry(cortex_project, "successor-plan")
    plan = _write_plan(
        cortex_project,
        "orphan-plan",
        "active",
        "- Moved to plans/successor-plan.md.\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_clean_when_bullet_cites_existing_doctrine(cortex_project: Path) -> None:
    _create_doctrine_entry(cortex_project, "0005-scope-boundaries")
    plan = _write_plan(
        cortex_project,
        "orphan-doctrine",
        "active",
        "- Resolved by doctrine/0005-scope-boundaries (out of scope).\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_warns_when_cited_target_does_not_exist(cortex_project: Path) -> None:
    """SPEC § 4.2 requires resolution to a real durable-layer entry, not a
    citation-shaped string. Dangling citations are still orphans."""
    plan = _write_plan(
        cortex_project,
        "orphan-dangling",
        "active",
        "- Resolved by journal/2026-04-25-does-not-exist.\n",
    )
    warnings = _orphan_warnings(cortex_project, plan)
    assert len(warnings) == 1, warnings
    assert "non-existent target" in warnings[0]


def test_orphan_clean_when_one_of_multiple_citations_exists(cortex_project: Path) -> None:
    """If a bullet cites multiple targets and at least one resolves to an
    existing file, the bullet passes — humans often write 'see X or Y' in
    a deferral note and either resolution suffices."""
    _create_journal_entry(cortex_project, "2026-04-25-real")
    plan = _write_plan(
        cortex_project,
        "orphan-mixed",
        "active",
        "- See journal/2026-04-25-real or journal/2026-04-25-fake for context.\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_clean_when_journal_in_archive(cortex_project: Path, tmp_path: Path) -> None:
    archive_dir = cortex_project / ".cortex" / "journal" / "archive" / "2025"
    archive_dir.mkdir(parents=True)
    (archive_dir / "2025-12-31-old.md").write_text(
        "# Old\n\n**Date:** 2025-12-31\n**Type:** decision\n\nbody\n"
    )
    plan = _write_plan(
        cortex_project,
        "orphan-archived",
        "active",
        "- Resolved by journal/2025-12-31-old (in archive).\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_skipped_on_shipped_plan(cortex_project: Path) -> None:
    plan = _write_plan(
        cortex_project,
        "orphan-shipped",
        "shipped",
        "- This is shipped, no citation needed (resolution in git history).\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_skipped_on_cancelled_plan(cortex_project: Path) -> None:
    plan = _write_plan(
        cortex_project,
        "orphan-cancelled",
        "cancelled",
        "- Cancelled scope.\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_skipped_when_no_followups_section(cortex_project: Path) -> None:
    """A plan with no `## Follow-ups (deferred)` section at all triggers
    no warning — the check only fires on bullets within an existing
    section, not on its absence."""
    title = "Plan without follow-ups"
    goal_hash = normalize_goal_hash(title)
    plan = cortex_project / ".cortex" / "plans" / "no-followups.md"
    plan.write_text(
        f"---\n"
        f"Status: active\n"
        f"Written: 2026-04-25\n"
        f"Author: human\n"
        f"Goal-hash: {goal_hash}\n"
        f"Updated-by:\n  - 2026-04-25T22:00 human (test)\n"
        f"Cites: doctrine/0001\n"
        f"---\n\n"
        f"# {title}\n\n> Body\n\n"
        f"## Why (grounding)\n\nGrounded in `doctrine/0001`.\n\n"
        f"## Approach\n\nApproach prose.\n\n"
        f"## Success Criteria\n\n- Test exits 0.\n\n"
        f"## Work items\n\n- [ ] item\n"
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_skipped_on_empty_followups(cortex_project: Path) -> None:
    """Active plan with the section heading but no bullets — no warnings."""
    plan = _write_plan(
        cortex_project,
        "orphan-empty",
        "active",
        "_(none)_\n",  # prose, no bullets
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_warns_per_uncited_bullet_independently(cortex_project: Path) -> None:
    """Each uncited bullet generates its own warning so the user sees the
    full list of items needing attention, not just the first."""
    _create_journal_entry(cortex_project, "2026-04-25-foo")
    plan = _write_plan(
        cortex_project,
        "orphan-multi",
        "active",
        "- Uncited item A.\n- Resolved by journal/2026-04-25-foo.\n- Uncited item B.\n",
    )
    warnings = _orphan_warnings(cortex_project, plan)
    assert len(warnings) == 2, warnings


def test_orphan_warns_when_path_pattern_appears_without_slash(cortex_project: Path) -> None:
    """Defensive: the word `plan` or `journal` in prose without a slash
    must NOT count as a citation — otherwise the check is gameable by
    accident (e.g., 'see the plan we made earlier' would falsely pass)."""
    plan = _write_plan(
        cortex_project,
        "orphan-no-slash",
        "active",
        "- Resolved with the plan we discussed (no path slash).\n",
    )
    assert len(_orphan_warnings(cortex_project, plan)) == 1


def test_orphan_handles_asterisk_bullets(cortex_project: Path) -> None:
    plan = _write_plan(
        cortex_project,
        "orphan-asterisk",
        "active",
        "* Uncited asterisk-style bullet.\n",
    )
    warnings = _orphan_warnings(cortex_project, plan)
    assert len(warnings) == 1


def test_orphan_warns_on_malformed_journal_citation(cortex_project: Path) -> None:
    """`journal/foo` is the wrong shape — SPEC § 3.5 requires
    `journal/YYYY-MM-DD-<slug>`. The regex must reject typos so the
    check does its job (catching humans who half-write a citation)."""
    plan = _write_plan(
        cortex_project,
        "orphan-malformed-journal",
        "active",
        "- Resolved by journal/foo.md (missing date prefix).\n",
    )
    assert len(_orphan_warnings(cortex_project, plan)) == 1


def test_orphan_warns_on_malformed_doctrine_citation(cortex_project: Path) -> None:
    """`doctrine/scope-boundary` is missing the 4-digit prefix per SPEC § 2."""
    plan = _write_plan(
        cortex_project,
        "orphan-malformed-doctrine",
        "active",
        "- Out of scope per doctrine/scope-boundary (missing 0005- prefix).\n",
    )
    assert len(_orphan_warnings(cortex_project, plan)) == 1


def test_orphan_clean_on_well_formed_existing_journal(cortex_project: Path) -> None:
    _create_journal_entry(cortex_project, "2026-04-25-init-ux-fixes-plan-shipped")
    plan = _write_plan(
        cortex_project,
        "orphan-good-journal",
        "active",
        "- Resolved by `journal/2026-04-25-init-ux-fixes-plan-shipped`.\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []


def test_orphan_clean_on_well_formed_existing_doctrine(cortex_project: Path) -> None:
    _create_doctrine_entry(cortex_project, "0005-scope-boundaries-v2")
    plan = _write_plan(
        cortex_project,
        "orphan-good-doctrine",
        "active",
        "- Out of scope per `doctrine/0005-scope-boundaries-v2`.\n",
    )
    assert _orphan_warnings(cortex_project, plan) == []
