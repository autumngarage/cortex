"""Tests for `cortex manifest` — session-start slice per Protocol § 1."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.manifest import build_manifest


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _run_manifest(project: Path, budget: int) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(cli, ["manifest", "--path", str(project), "--budget", str(budget)])
    return result.exit_code, result.output


def _write_doctrine(project: Path, number: int, *, priority: str = "default", date: str = "2026-04-01") -> Path:
    path = project / ".cortex" / "doctrine" / f"{number:04d}-example-{number}.md"
    path.write_text(
        f"# {number:04d} — Example {number}\n\n"
        f"**Status:** Accepted\n"
        f"**Date:** {date}\n"
        f"**Load-priority:** {priority}\n\n"
        f"## Context\nc\n## Decision\nd\n## Consequences\ne\n"
    )
    return path


def _write_plan(project: Path, name: str, *, status: str = "active") -> Path:
    from cortex.goal_hash import normalize_goal_hash

    path = project / ".cortex" / "plans" / f"{name}.md"
    title = name.replace("-", " ").title()
    path.write_text(
        "---\n"
        f"Status: {status}\n"
        "Written: 2026-04-17\n"
        "Author: human\n"
        f"Goal-hash: {normalize_goal_hash(title)}\n"
        "Updated-by:\n  - 2026-04-17T10:00 human\n"
        "Cites: doctrine/0001\n"
        "---\n\n"
        f"# {title}\n\n## Why (grounding)\ndoctrine/0001\n\n"
        "## Success Criteria\nAll `tests/` pass.\n\n## Approach\n.\n\n## Work items\n- [ ] x\n"
    )
    return path


def _write_journal(project: Path, date: str, slug: str) -> Path:
    path = project / ".cortex" / "journal" / f"{date}-{slug}.md"
    path.write_text(f"# {slug}\n\n**Date:** {date}\n\nentry body.\n")
    return path


def test_manifest_includes_state(scaffolded_project: Path) -> None:
    exit_code, output = _run_manifest(scaffolded_project, 8000)
    assert exit_code == 0
    assert "## state.md" in output
    assert "Project State" in output  # from the stub body


def test_degraded_mode_state_only(scaffolded_project: Path) -> None:
    _write_doctrine(scaffolded_project, 1)
    exit_code, output = _run_manifest(scaffolded_project, 500)
    assert exit_code == 0
    assert "degraded (state-only)" in output
    assert "## Doctrine" not in output
    # Protocol § 1: degraded fallback is state-only — no promotion summary.
    assert "Promotion-queue" not in output


def test_load_priority_always_listed_first(scaffolded_project: Path) -> None:
    _write_doctrine(scaffolded_project, 1, priority="default", date="2026-04-20")
    _write_doctrine(scaffolded_project, 2, priority="always", date="2026-01-01")
    exit_code, output = _run_manifest(scaffolded_project, 8000)
    assert exit_code == 0
    idx_always = output.index("0002-example-2.md")
    idx_default = output.index("0001-example-1.md")
    assert idx_always < idx_default


def test_only_active_plans_included(scaffolded_project: Path) -> None:
    _write_plan(scaffolded_project, "active-one", status="active")
    _write_plan(scaffolded_project, "shipped-one", status="shipped")
    exit_code, output = _run_manifest(scaffolded_project, 8000)
    assert exit_code == 0
    assert "active-one.md" in output
    assert "shipped-one.md" not in output


def test_journal_window_respected(scaffolded_project: Path) -> None:
    now = datetime(2026, 4, 17, tzinfo=UTC)
    _write_journal(scaffolded_project, "2026-04-17", "fresh")
    _write_journal(scaffolded_project, "2026-01-01", "ancient")
    manifest = build_manifest(scaffolded_project, 8000, now=now)
    rendered = manifest.render()
    assert "2026-04-17-fresh.md" in rendered
    assert "2026-01-01-ancient.md" not in rendered


def test_promotion_summary_without_index(scaffolded_project: Path) -> None:
    exit_code, output = _run_manifest(scaffolded_project, 8000)
    assert exit_code == 0
    assert "Promotion-queue: unavailable" in output


def test_promotion_summary_with_index(scaffolded_project: Path) -> None:
    (scaffolded_project / ".cortex" / ".index.json").write_text(
        '{"promotion_queue": ['
        '{"id": "a", "state": "proposed"},'
        '{"id": "b", "state": "stale-proposed"},'
        '{"id": "c", "state": "approved"}'
        "]}"
    )
    exit_code, output = _run_manifest(scaffolded_project, 8000)
    assert exit_code == 0
    assert "Promotion-queue: 1 proposed, 1 stale." in output


def test_missing_cortex_dir_errors(tmp_path: Path) -> None:
    exit_code, output = _run_manifest(tmp_path, 8000)
    assert exit_code == 2
    assert "does not exist" in output


def test_superseded_doctrine_excluded(scaffolded_project: Path) -> None:
    _write_doctrine(scaffolded_project, 1, priority="always")
    superseded = scaffolded_project / ".cortex" / "doctrine" / "0002-old.md"
    superseded.write_text(
        "# 0002 — Old\n\n"
        "**Status:** Superseded-by 3\n"
        "**Date:** 2026-04-01\n\n"
        "## Context\nx\n## Decision\ny\n## Consequences\nz\n"
    )
    exit_code, output = _run_manifest(scaffolded_project, 8000)
    assert exit_code == 0
    assert "0001-example-1.md" in output
    assert "0002-old.md" not in output


def test_digest_detected_by_type_not_filename(scaffolded_project: Path) -> None:
    # A Journal entry whose slug happens to contain "digest" but has
    # `Type: decision` must NOT be picked up as the digest, and a real
    # digest without "digest" in the slug must be found.
    j = scaffolded_project / ".cortex" / "journal"
    (j / "2026-01-01-digest-misnamed.md").write_text(
        "# Title\n\n**Date:** 2026-01-01\n**Type:** decision\n\nbody\n"
    )
    (j / "2026-02-01-march-summary.md").write_text(
        "# Title\n\n**Date:** 2026-02-01\n**Type:** digest\n\nbody\n"
    )
    now = datetime(2026, 4, 17, tzinfo=UTC)
    rendered = build_manifest(scaffolded_project, 8000, now=now).render()
    # The real digest is loaded as "latest digest" even though it's outside
    # the 72h window; the misnamed decision is not.
    assert "2026-02-01-march-summary.md" in rendered
    assert "2026-01-01-digest-misnamed.md" not in rendered


def test_budget_strictly_enforced(scaffolded_project: Path) -> None:
    # Big Doctrine entry + tiny budget — previously the first entry was
    # always included even when over budget. Now it must be truncated.
    big_body = "filler " * 5000  # ~35000 chars
    path = scaffolded_project / ".cortex" / "doctrine" / "0001-big.md"
    path.write_text(
        "# 0001 — Big\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-01\n"
        "**Load-priority:** default\n\n"
        f"## Context\n{big_body}\n"
    )
    # 8000 tokens ≈ 32000 chars total; the big entry alone is ~35000, so
    # even with the whole budget it shouldn't fit.
    manifest = build_manifest(scaffolded_project, 8000)
    rendered = manifest.render()
    assert "0001-big.md" not in rendered
    assert "truncated by budget" in rendered


def test_wide_journal_at_high_budget(scaffolded_project: Path) -> None:
    now = datetime(2026, 4, 17, tzinfo=UTC)
    _write_journal(scaffolded_project, "2026-04-14", "six-days-ago")
    manifest = build_manifest(scaffolded_project, 20000, now=now)
    rendered = manifest.render()
    assert "last 168h" in rendered
    assert "2026-04-14-six-days-ago.md" in rendered
