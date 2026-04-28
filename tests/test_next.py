"""Tests for `cortex next`."""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

from click.testing import CliRunner, Result

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.goal_hash import normalize_goal_hash
from cortex.ranking import is_placeholder_text


def _init_cortex(project: Path) -> None:
    cortex_dir = project / ".cortex"
    (cortex_dir / "plans").mkdir(parents=True)
    (project / "docs" / "case-studies").mkdir(parents=True)
    (cortex_dir / "SPEC_VERSION").write_text("0.4.0-dev\n")
    (cortex_dir / "protocol.md").write_text("**Protocol version:** 0.4.0-dev\n")
    (cortex_dir / "state.md").write_text("# Project State\n")


def _write_state(project: Path, current_work: str = "", open_questions: str = "") -> None:
    (project / ".cortex" / "state.md").write_text(
        "# Project State\n\n"
        "<!-- cortex:hand -->\n"
        "## Current work\n\n"
        f"{current_work}"
        "<!-- cortex:end-hand -->\n\n"
        "## Open questions\n\n"
        f"{open_questions}"
    )


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


def _write_case_study(project: Path, filename: str, summary: str, *, days_ago: int = 0) -> Path:
    path = project / "docs" / "case-studies" / filename
    path.write_text(f"# Case {filename}\n\n{summary}\n\n## Details\n\nBody.\n")
    mtime = time.time() - timedelta(days=days_ago).total_seconds()
    os.utime(path, (mtime, mtime))
    return path


def _next(project: Path, *extra: str) -> Result:
    result = CliRunner().invoke(cli, ["next", "--path", str(project), *extra])
    assert result.exit_code == 0, result.output
    return result


def _band_texts(data: dict[str, list[dict[str, object]]], band: str) -> list[str]:
    return [str(item["text"]) for item in data[band]]


def test_stable_ordering_produces_byte_identical_output(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "b-plan", work_items="- [ ] beta\n")
    _write_plan(tmp_path, "a-plan", work_items="- [ ] alpha\n")
    _write_state(tmp_path, current_work="- state work\n", open_questions="- state question\n")
    _write_case_study(tmp_path, "case.md", "A recent case.")

    first = _next(tmp_path).output
    second = _next(tmp_path).output

    assert first == second
    assert first.index("plans/a-plan.md") < first.index("plans/b-plan.md")


def test_p0_active_plans_excludes_shipped_and_sends_stale_to_p1(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "fresh", work_items="- [ ] fresh open\n")
    _write_plan(tmp_path, "shipped", status="shipped", work_items="- [ ] shipped open\n")
    _write_plan(tmp_path, "stale", updated_days_ago=15, work_items="- [ ] stale open\n")

    data = json.loads(_next(tmp_path, "--json").output)

    assert [item["text"] for item in data["p0"]] == ["fresh open"]
    assert [item["text"] for item in data["p1"]] == ["stale open"]


def test_p1_open_questions_and_stale_plan_checkboxes(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_state(tmp_path, open_questions="- Marker convention?\n")
    _write_plan(tmp_path, "stale", updated_days_ago=30, work_items="- [ ] re-scope stale plan\n")

    data = json.loads(_next(tmp_path, "--json").output)

    assert [item["text"] for item in data["p1"]] == ["re-scope stale plan", "Marker convention?"]
    assert all(item["text"] != "re-scope stale plan" for item in data["p0"])


def test_p2_case_studies_respect_since_and_use_first_paragraph(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_case_study(tmp_path, "new.md", "Recent summary line one\ncontinues here.", days_ago=3)
    _write_case_study(tmp_path, "old.md", "Old summary.", days_ago=40)

    data = json.loads(_next(tmp_path, "--json", "--since", "30").output)

    assert data["p2"] == [
        {
            "text": "Recent summary line one continues here.",
            "source": "docs/case-studies/new.md",
            "line_start": None,
            "line_end": None,
        }
    ]


def test_citation_line_numbers_match_source_files(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    plan = _write_plan(
        tmp_path,
        "fresh",
        work_items="- [x] done\n- [ ] fresh open\n- [ ] second open\n",
    )

    data = json.loads(_next(tmp_path, "--json").output)
    first = data["p0"][0]
    source_line = plan.read_text().splitlines()[first["line_start"] - 1]

    assert first["source"] == "plans/fresh.md"
    assert source_line == "- [ ] fresh open"
    assert first["line_end"] == first["line_start"]


def test_empty_case_prints_three_headers_with_none(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_state(tmp_path)

    output = _next(tmp_path).output

    assert output == (
        "P0 — Active work\n"
        "  (none)\n"
        "\n"
        "P1 — Open questions and stale debt\n"
        "  (none)\n"
        "\n"
        "P2 — Recent context to consider\n"
        "  (none)\n"
    )


def test_json_output_is_parseable_and_structural(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "fresh", work_items="- [ ] fresh open\n")
    _write_state(tmp_path, current_work="- current state\n", open_questions="- question\n")
    _write_case_study(tmp_path, "case.md", "Case summary.")

    data = json.loads(_next(tmp_path, "--json").output)

    assert set(data) == {"p0", "p1", "p2"}
    assert set(data["p0"][0]) == {"text", "source", "line_start", "line_end"}
    assert data["p0"][0]["source"] == "plans/fresh.md"
    assert data["p1"][0]["source"] == "state.md#open-questions"
    assert data["p2"][0]["source"] == "docs/case-studies/case.md"


def test_limit_truncates_each_band(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "fresh", work_items="- [ ] one\n- [ ] two\n")
    _write_state(tmp_path, open_questions="- q1\n- q2\n")
    _write_case_study(tmp_path, "a.md", "A.")
    _write_case_study(tmp_path, "b.md", "B.")

    data = json.loads(_next(tmp_path, "--json", "--limit", "1").output)

    assert [len(data[band]) for band in ("p0", "p1", "p2")] == [1, 1, 1]


def test_path_targets_arbitrary_project(tmp_path: Path) -> None:
    project = tmp_path / "target"
    project.mkdir()
    _init_cortex(project)
    _write_plan(project, "fresh", work_items="- [ ] arbitrary path item\n")

    result = CliRunner().invoke(cli, ["next", "--path", str(project), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["p0"][0]["text"] == "arbitrary path item"


def test_plan_placeholder_items_do_not_enter_p0(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(tmp_path, "placeholder", work_items="- [ ] {{ task }}\n")

    data = json.loads(_next(tmp_path, "--json").output)

    assert "task" not in _band_texts(data, "p0")
    assert all(item["source"] != "plans/placeholder.md" for item in data["p0"])


def test_plan_mixed_real_and_placeholder_items_only_ranks_real_work(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(
        tmp_path,
        "mixed",
        work_items="- [ ] Implement retry logic\n- [ ] {{ first concrete task }}\n",
    )

    data = json.loads(_next(tmp_path, "--json").output)

    assert _band_texts(data, "p0") == ["Implement retry logic"]


def test_state_open_questions_placeholder_bullets_are_filtered(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_state(
        tmp_path,
        open_questions="- {{ unresolved question }}\n- Confirm release ordering\n",
    )

    data = json.loads(_next(tmp_path, "--json").output)

    assert _band_texts(data, "p1") == ["Confirm release ordering"]


def test_plan_with_only_placeholders_warns_in_human_output_only(tmp_path: Path) -> None:
    _init_cortex(tmp_path)
    _write_plan(
        tmp_path,
        "placeholder-only",
        work_items="- [ ] {{ first concrete task }}\n- [ ] {{ second concrete task }}\n",
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["next", "--path", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "plans/placeholder-only.md" in result.stderr
    assert "all are {{ placeholder }}" in result.stderr

    json_result = runner.invoke(cli, ["next", "--path", str(tmp_path), "--json"])
    assert json_result.exit_code == 0, json_result.output
    assert json_result.stderr == ""


def test_spawned_plan_placeholders_do_not_enter_next_json(tmp_path: Path) -> None:
    init_result = CliRunner().invoke(init_command, ["--path", str(tmp_path)])
    assert init_result.exit_code == 0, init_result.output
    spawn_result = CliRunner().invoke(
        cli,
        [
            "plan",
            "spawn",
            "dogfood-test",
            "--title",
            "Dogfood placeholder filtering",
            "--path",
            str(tmp_path),
        ],
    )
    assert spawn_result.exit_code == 0, spawn_result.output

    data = json.loads(_next(tmp_path, "--json").output)

    assert not any(
        item["source"] == "plans/dogfood-test.md" and "{{" in item["text"]
        for band in ("p0", "p1", "p2")
        for item in data[band]
    )


def test_is_placeholder_text() -> None:
    cases = {
        "{{ task }}": True,
        "{{ first concrete task — link to issue/PR when filed }}": True,
        "Implement retry logic with {{ optional flag }}": False,
        "plain task": False,
        "": False,
        "  ": False,
        "{{ }} {{ }}": True,
        "foo{{x}}bar": False,
    }

    for text, expected in cases.items():
        assert is_placeholder_text(text) is expected
