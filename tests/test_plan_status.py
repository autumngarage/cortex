"""Tests for `cortex plan status`."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from click.testing import CliRunner, Result

from cortex.cli import cli
from cortex.goal_hash import normalize_goal_hash


def _init_cortex(project: Path) -> None:
    cortex_dir = project / ".cortex"
    (cortex_dir / "plans").mkdir(parents=True)
    (cortex_dir / "SPEC_VERSION").write_text("0.4.0-dev\n")
    (cortex_dir / "protocol.md").write_text("**Protocol version:** 0.4.0-dev\n")


def _write_plan(
    project: Path,
    slug: str,
    *,
    title: str | None = None,
    status: str = "active",
    updated_days_ago: int = 0,
    work_items: str = "",
) -> Path:
    title = title or slug.replace("-", " ").title()
    updated = date.today() - timedelta(days=updated_days_ago)
    path = project / ".cortex" / "plans" / f"{slug}.md"
    path.write_text(
        "---\n"
        f"Status: {status}\n"
        "Written: 2026-04-01\n"
        "Author: human\n"
        f"Goal-hash: {normalize_goal_hash(title)}\n"
        "Updated-by:\n"
        "  - 2026-04-01T09:00 human (created)\n"
        f"  - {updated.isoformat()}T10:00 human (updated)\n"
        "Cites: doctrine/0001\n"
        "---\n\n"
        f"# {title}\n\n"
        "## Why (grounding)\n"
        "doctrine/0001\n\n"
        "## Success Criteria\n"
        "`pytest` passes.\n\n"
        "## Approach\n"
        "Ship it.\n\n"
        "## Work items\n"
        f"{work_items}"
        "\n## Follow-ups (deferred)\n"
    )
    return path


def _status(project: Path, *extra: str) -> Result:
    result = CliRunner().invoke(cli, ["plan", "status", "--path", str(project), *extra])
    assert result.exit_code == 0, result.output
    return result


def test_zero_work_items_reports_zero_completion_and_not_stale(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "zero", work_items="")
    result = _status(tmp_path)
    assert "Completion:  0% (0 of 0 items)" in result.output
    assert "Stale:       no" in result.output


def test_all_complete_reports_100_and_not_stale(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(
        tmp_path,
        "done",
        updated_days_ago=30,
        work_items="- [x] one\n- [x] two\n",
    )
    data = json.loads(_status(tmp_path, "--json").output)
    assert data[0]["completion_percent"] == 100
    assert data[0]["stale"] is False


def test_mixed_items_count_in_progress_as_half_done(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(
        tmp_path,
        "mixed",
        work_items=(
            "- [x] one\n"
            "- [x] two\n"
            "- [x] three\n"
            "### Bucket\n"
            "- [ ] four\n"
            "- [ ] five\n"
            "- [~] six\n"
        ),
    )
    data = json.loads(_status(tmp_path, "--json").output)
    assert data[0]["completion_percent"] == 70
    assert data[0]["items"] == {
        "done": 3,
        "open": 2,
        "in_progress": 1,
        "done_equivalent": 3.5,
        "total": 5,
    }


def test_in_progress_items_cannot_push_completion_over_100(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(
        tmp_path,
        "only-in-progress-closed",
        work_items="- [x] one\n- [x] two\n- [~] three\n- [~] four\n",
    )
    data = json.loads(_status(tmp_path, "--json").output)
    assert data[0]["completion_percent"] == 100
    assert data[0]["items"] == {
        "done": 2,
        "open": 0,
        "in_progress": 2,
        "done_equivalent": 2.0,
        "total": 2,
    }


def test_stale_active_plan_flags_when_old_with_open_item(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "stale", updated_days_ago=30, work_items="- [ ] open\n")
    data = json.loads(_status(tmp_path, "--json").output)
    assert data[0]["stale"] is True
    assert data[0]["last_update_age_days"] == 30


def test_fresh_active_plan_is_not_stale(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "fresh", updated_days_ago=0, work_items="- [ ] open\n")
    data = json.loads(_status(tmp_path, "--json").output)
    assert data[0]["stale"] is False


def test_shipped_plan_is_never_stale(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(
        tmp_path,
        "shipped",
        status="shipped",
        updated_days_ago=30,
        work_items="- [ ] open\n",
    )
    data = json.loads(_status(tmp_path, "--json").output)
    assert data[0]["stale"] is False


def test_cancelled_plan_is_never_stale(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(
        tmp_path,
        "cancelled",
        status="cancelled",
        updated_days_ago=30,
        work_items="- [ ] open\n",
    )
    data = json.loads(_status(tmp_path, "--json").output)
    assert data[0]["stale"] is False


def test_json_output_shape(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "alpha", title="Alpha Plan", work_items="- [x] done\n- [ ] open\n")
    data = json.loads(_status(tmp_path, "--json").output)
    assert data == [
        {
            "path": "plans/alpha.md",
            "status": "active",
            "written": "2026-04-01",
            "author": "human",
            "goal_hash": normalize_goal_hash("Alpha Plan"),
            "updated_by": [
                "2026-04-01T09:00 human (created)",
                f"{date.today().isoformat()}T10:00 human (updated)",
            ],
            "cites": "doctrine/0001",
            "completion_percent": 50,
            "items": {
                "done": 1,
                "open": 1,
                "in_progress": 0,
                "done_equivalent": 1.0,
                "total": 2,
            },
            "last_update": date.today().isoformat(),
            "last_update_age_days": 0,
            "stale": False,
        }
    ]


def test_stale_only_filters_correctly(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "stale", updated_days_ago=30, work_items="- [ ] open\n")
    _write_plan(tmp_path, "fresh", updated_days_ago=0, work_items="- [ ] open\n")
    data = json.loads(_status(tmp_path, "--json", "--stale-only").output)
    assert [plan["path"] for plan in data] == ["plans/stale.md"]
