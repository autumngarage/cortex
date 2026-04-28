"""Tests for `cortex grep` — monkeypatches `subprocess.run` so tests don't
require ripgrep on PATH."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands import grep as grep_module
from cortex.commands.init import CURRENT_SPEC_VERSION, init_command


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


@pytest.fixture
def minimal_cortex_project(tmp_path: Path) -> Path:
    cortex = tmp_path / ".cortex"
    for layer in ("doctrine", "plans", "journal", "procedures", "templates"):
        (cortex / layer).mkdir(parents=True)
    (cortex / "SPEC_VERSION").write_text(CURRENT_SPEC_VERSION + "\n")
    return tmp_path


def _fake_rg(stdout: str, returncode: int = 0, stderr: str = "") -> object:
    class FakeCompleted:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    return FakeCompleted()


def _install_fake_rg(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> list[list[str]]:
    monkeypatch.setattr(grep_module, "_find_rg", lambda: "/usr/bin/rg")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> object:
        calls.append(cmd)
        return _fake_rg(stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _write_cortex_file(project: Path, relative: str, content: str) -> Path:
    path = project / ".cortex" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_missing_cortex_dir_errors(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "pattern", "--path", str(tmp_path)])
    assert result.exit_code == 2


def test_missing_ripgrep_errors(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grep_module, "_find_rg", lambda: None)
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "pattern", "--path", str(scaffolded_project)])
    assert result.exit_code == 3
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "ripgrep" in combined


def _rg_match_record(path: str, line_number: int, text: str) -> str:
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "lines": {"text": text},
                "line_number": line_number,
                "absolute_offset": 0,
                "submatches": [],
            },
        }
    )


def _rg_context_record(path: str, line_number: int, text: str) -> str:
    return json.dumps(
        {
            "type": "context",
            "data": {
                "path": {"text": path},
                "lines": {"text": text},
                "line_number": line_number,
                "absolute_offset": 0,
                "submatches": [],
            },
        }
    )


def test_matches_annotated_with_frontmatter(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = scaffolded_project / ".cortex" / "doctrine" / "0001-why.md"
    entry.write_text(
        "# 0001 — Why\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-17\n"
        "**Load-priority:** always\n\n"
        "## Context\nhello world\n"
    )
    fake_stdout = _rg_match_record(str(entry), 5, "hello world\n") + "\n"
    _install_fake_rg(monkeypatch, fake_stdout)

    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "hello", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert "Status: Accepted" in result.output
    assert "Load-priority: always" in result.output
    assert "hello world" in result.output


def test_context_lines_rendered_with_dash_separator(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = scaffolded_project / ".cortex" / "doctrine" / "0001-why.md"
    entry.write_text(
        "# 0001 — Why\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-17\n"
        "**Load-priority:** default\n\n"
        "line before\nmatch here\nline after\n"
    )
    stdout = "\n".join([
        _rg_context_record(str(entry), 7, "line before\n"),
        _rg_match_record(str(entry), 8, "match here\n"),
        _rg_context_record(str(entry), 9, "line after\n"),
    ]) + "\n"
    _install_fake_rg(monkeypatch, stdout)

    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "match", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert "7-line before" in result.output
    assert "8:match here" in result.output
    assert "9-line after" in result.output


def test_no_matches_message(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rg(monkeypatch, "", returncode=1)
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "missing", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert "no matches" in result.output


def test_layer_restricts_search_root(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_rg(monkeypatch, "")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "x", "--layer", "journal", "--path", str(scaffolded_project)],
    )
    assert result.exit_code == 0
    assert calls
    invoked = calls[0]
    assert invoked[-1].endswith(".cortex/journal")


def test_malformed_json_surfaces_warning(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_rg(monkeypatch, "not-valid-json\n" + "{also bad\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "x", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "could not be decoded" in combined


def test_spec_version_guard_warns_on_unsupported(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (scaffolded_project / ".cortex" / "SPEC_VERSION").write_text("9.9.0\n")
    _install_fake_rg(monkeypatch, "")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "x", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "9.9.0" in combined


def test_spec_version_guard_warns_on_missing(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (scaffolded_project / ".cortex" / "SPEC_VERSION").unlink()
    _install_fake_rg(monkeypatch, "")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "x", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "SPEC_VERSION" in combined


def test_unreadable_file_surfaces_warning_and_still_emits_matches(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a match against a file that can't be read by pointing rg at a
    # non-existent path — `_summarize_file` must not silently drop the match.
    phantom = scaffolded_project / ".cortex" / "doctrine" / "ghost.md"
    fake_stdout = _rg_match_record(str(phantom), 1, "ghost line\n") + "\n"
    _install_fake_rg(monkeypatch, fake_stdout)
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "ghost", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "could not read" in combined
    assert "ghost line" in result.output


def test_pattern_with_leading_dash_not_parsed_as_flag(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_fake_rg(monkeypatch, "")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "- [ ]", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert calls
    invoked = calls[0]
    # The `--` terminator must appear immediately before the pattern.
    dash_dash_idx = invoked.index("--")
    assert invoked[dash_dash_idx + 1] == "- [ ]"


def test_rg_error_returncode_propagates(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_rg(monkeypatch, "", returncode=2, stderr="rg: bad pattern\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "[", "--path", str(scaffolded_project)])
    assert result.exit_code == 2
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "bad pattern" in combined


def test_frontmatter_single_filter_matches_yaml_and_bold_inline(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "plans/active-yaml.md",
        "---\nStatus: active\nType: plan\n---\n# Active YAML\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "journal/active-bold.md",
        "# Active Bold\n\n**Status:** active\n**Type:** note\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "plans/shipped.md",
        "---\nStatus: shipped\nType: plan\n---\n# Shipped\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "", "--frontmatter", "status:active", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "plans/active-yaml.md" in result.output
    assert "journal/active-bold.md" in result.output
    assert "plans/shipped.md" not in result.output


def test_frontmatter_filter_skips_templates_by_default(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "journal/decision.md",
        "---\nType: decision\n---\n# Real Decision\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "templates/journal/decision.md",
        "---\nType: decision\n---\n# Decision Template\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "", "--frontmatter", "Type:decision", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "journal/decision.md" in result.output
    assert "templates/journal/decision.md" not in result.output


def test_include_templates_allows_frontmatter_template_matches(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "templates/journal/decision.md",
        "---\nType: decision\n---\n# Decision Template\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "grep",
            "",
            "--frontmatter",
            "Type:decision",
            "--include-templates",
            "--path",
            str(minimal_cortex_project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "templates/journal/decision.md" in result.output


def test_layer_templates_overrides_default_template_exclusion(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "journal/decision.md",
        "---\nType: decision\n---\n# Real Decision\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "templates/journal/decision.md",
        "---\nType: decision\n---\n# Decision Template\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "grep",
            "",
            "--layer",
            "templates",
            "--frontmatter",
            "Type:decision",
            "--path",
            str(minimal_cortex_project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "templates/journal/decision.md" in result.output
    assert ".cortex/journal/decision.md" not in result.output


def test_rg_search_excludes_templates_by_default(scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_rg(monkeypatch, "")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "decision", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert calls
    assert "--glob" in calls[0]
    assert "!.cortex/templates/**" in calls[0]
    assert "!templates/**" in calls[0]
    assert "!**/templates/**" in calls[0]


def test_rg_search_include_templates_skips_template_exclusion(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _install_fake_rg(monkeypatch, "")
    runner = CliRunner()
    result = runner.invoke(cli, ["grep", "decision", "--include-templates", "--path", str(scaffolded_project)])
    assert result.exit_code == 0
    assert calls
    assert "!.cortex/templates/**" not in calls[0]


def test_frontmatter_filters_are_conjunctive(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "plans/active-plan.md",
        "---\nStatus: active\nType: plan\n---\n# Active Plan\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "journal/active-note.md",
        "---\nStatus: active\nType: note\n---\n# Active Note\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "grep",
            "--frontmatter",
            "status:active",
            "--frontmatter",
            "type:plan",
            "--path",
            str(minimal_cortex_project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "plans/active-plan.md" in result.output
    assert "journal/active-note.md" not in result.output


def test_frontmatter_wildcard_matches_non_empty_presence(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "doctrine/superseded.md",
        "---\nStatus: superseded\nSuperseded-by: doctrine/0002-new.md\n---\n# Old\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "doctrine/current.md",
        "---\nStatus: accepted\n---\n# Current\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "--frontmatter", "superseded-by:*", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "doctrine/superseded.md" in result.output
    assert "doctrine/current.md" not in result.output


def test_frontmatter_negation_excludes_matching_values(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "journal/incident.md",
        "---\nType: incident\nStatus: active\n---\n# Incident\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "journal/release.md",
        "---\nType: release\nStatus: active\n---\n# Release\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "--frontmatter", "!type:incident", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "journal/release.md" in result.output
    assert "journal/incident.md" not in result.output


def test_frontmatter_list_values_match_any_element(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "journal/tagged.md",
        "---\nTags: [a, b, c]\nStatus: active\n---\n# Tagged\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "journal/untagged.md",
        "---\nTags: [x, y]\nStatus: active\n---\n# Untagged\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "--frontmatter", "tags:b", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "journal/tagged.md" in result.output
    assert "journal/untagged.md" not in result.output


def test_empty_pattern_with_filter_lists_files_without_body_lines_or_rg(
    minimal_cortex_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "plans/active.md",
        "---\nStatus: active\nType: plan\n---\n# Active\n\nneedle in body\n",
    )

    def fail_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("filter-only grep must not run rg")

    monkeypatch.setattr(subprocess, "run", fail_run)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "", "--frontmatter", "status:active", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "plans/active.md" in result.output
    assert "needle in body" not in result.output


def test_pattern_and_frontmatter_filter_must_both_match(
    minimal_cortex_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = _write_cortex_file(
        minimal_cortex_project,
        "plans/active.md",
        "---\nStatus: active\nType: plan\n---\n# Active\n\nneedle\n",
    )
    shipped = _write_cortex_file(
        minimal_cortex_project,
        "plans/shipped.md",
        "---\nStatus: shipped\nType: plan\n---\n# Shipped\n\nneedle\n",
    )
    stdout = "\n".join([
        _rg_match_record(str(active), 6, "needle\n"),
        _rg_match_record(str(shipped), 6, "needle\n"),
    ]) + "\n"
    _install_fake_rg(monkeypatch, stdout)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "needle", "--frontmatter", "status:active", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "plans/active.md" in result.output
    assert "plans/shipped.md" not in result.output
    assert "needle" in result.output


def test_frontmatter_keys_are_case_insensitive(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "plans/active.md",
        "---\nStatus: active\nType: plan\n---\n# Active\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["grep", "--frontmatter", "status:active", "--path", str(minimal_cortex_project)],
    )
    assert result.exit_code == 0, result.output
    assert "plans/active.md" in result.output


def test_frontmatter_filter_respects_layer_scope(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "doctrine/always.md",
        "# Doctrine\n\n**Status:** Accepted\n**Load-priority:** always\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "plans/always.md",
        "---\nStatus: active\nLoad-priority: always\n---\n# Plan\n",
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "grep",
            "--layer",
            "doctrine",
            "--frontmatter",
            "Load-priority:always",
            "--path",
            str(minimal_cortex_project),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "doctrine/always.md" in result.output
    assert "plans/always.md" not in result.output


def test_sentinel_motivating_frontmatter_queries(minimal_cortex_project: Path) -> None:
    _write_cortex_file(
        minimal_cortex_project,
        "plans/active-plan.md",
        "---\nStatus: active\nType: plan\nRejected: false\n---\n# Active Plan\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "plans/rejected-plan.md",
        "---\nStatus: active\nType: plan\nRejected: true\n---\n# Rejected Plan\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "doctrine/superseded.md",
        "# Superseded\n\n**Status:** Superseded\n**Superseded-by:** doctrine/0002-new.md\n",
    )
    _write_cortex_file(
        minimal_cortex_project,
        "doctrine/always.md",
        "# Always\n\n**Status:** Accepted\n**Load-priority:** always\n",
    )

    runner = CliRunner()
    rejected = runner.invoke(
        cli,
        ["grep", "--frontmatter", "rejected:true", "--path", str(minimal_cortex_project)],
    )
    active_plan = runner.invoke(
        cli,
        [
            "grep",
            "--frontmatter",
            "status:active",
            "--frontmatter",
            "type:plan",
            "--path",
            str(minimal_cortex_project),
        ],
    )
    superseded = runner.invoke(
        cli,
        ["grep", "--frontmatter", "superseded-by:*", "--path", str(minimal_cortex_project)],
    )
    always = runner.invoke(
        cli,
        ["grep", "--frontmatter", "Load-priority:always", "--path", str(minimal_cortex_project)],
    )

    assert rejected.exit_code == 0, rejected.output
    assert "plans/rejected-plan.md" in rejected.output
    assert "plans/active-plan.md" not in rejected.output

    assert active_plan.exit_code == 0, active_plan.output
    assert "plans/active-plan.md" in active_plan.output
    assert "plans/rejected-plan.md" in active_plan.output
    assert "doctrine/superseded.md" not in active_plan.output

    assert superseded.exit_code == 0, superseded.output
    assert "doctrine/superseded.md" in superseded.output
    assert "doctrine/always.md" not in superseded.output

    assert always.exit_code == 0, always.output
    assert "doctrine/always.md" in always.output
    assert "doctrine/superseded.md" not in always.output
