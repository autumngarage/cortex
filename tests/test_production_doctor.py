"""Tests for `cortex doctor --production`."""

from __future__ import annotations

import json
import re
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.production_doctor import issue_to_diagnostic
from cortex.validation import Issue, Severity


def _init_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def test_production_doctor_passes_on_fresh_scaffold(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--production", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "cortex doctor --production" in result.output


def test_production_doctor_json_has_stable_codes(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    (project / ".cortex" / "SPEC_VERSION").unlink()
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor", "--production", "--json", "--path", str(project)],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "production"
    assert payload["errors"] >= 1
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, list)
    assert diagnostics
    assert "code" in diagnostics[0]
    assert "severity" in diagnostics[0]
    assert any(item["code"] == "missing-source" for item in diagnostics)


def test_production_doctor_maps_promised_diagnostic_classes() -> None:
    cases = {
        "missing-source": Issue(
            Severity.ERROR,
            ".cortex/SPEC_VERSION",
            "`.cortex/SPEC_VERSION` missing; re-run `cortex init` or write the version manually.",
        ),
        "unresolved-provenance": Issue(
            Severity.WARNING,
            ".cortex/state.md",
            "state.md was generated against HEAD abc123, but the checkout is at def456",
        ),
        "budget-exceeded": Issue(
            Severity.WARNING,
            "",
            "journal draft is ~1200 tokens / ~800 words; target is <=1000 tokens.",
        ),
        "policy-violation": Issue(
            Severity.ERROR,
            ".cortex/plans/example.md",
            "Plan `Success Criteria` is empty; must name a concrete signal (SPEC § 4.3).",
        ),
        "manual-edit-to-generated": Issue(
            Severity.ERROR,
            ".cortex/state.md",
            "generated layer missing `Sources` provenance field (SPEC § 4.5)",
        ),
        "stale-derived": Issue(
            Severity.WARNING,
            ".cortex/state.md",
            "state.md generated before source changed (.cortex/plans/x.md at 2026-06-01T00:00:00+00:00); rerun `cortex refresh-state`",
        ),
    }

    for expected_code, issue in cases.items():
        assert issue_to_diagnostic(issue).code == expected_code


def test_production_doctor_reports_manifest_budget_exceeded(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    path = project / ".cortex" / "doctrine" / "0001-large.md"
    path.write_text(
        "# 0001 — Large\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-01\n"
        "**Load-priority:** default\n\n"
        + ("doctrine body " * 1000)
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor", "--production", "--json", "--path", str(project)],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    codes = {item["code"] for item in payload["diagnostics"]}
    assert "budget-exceeded" in codes


def test_production_doctor_json_reports_manifest_build_failures(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    (project / ".cortex" / "config.toml").write_text("[manifest\n")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["doctor", "--production", "--json", "--path", str(project)],
    )

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    diagnostics = payload["diagnostics"]
    assert any(item["code"] == "manifest-build-failed" for item in diagnostics)
    assert any(item["path"] == ".cortex" for item in diagnostics)


def test_production_doctor_exits_nonzero_on_warning(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    map_path = project / ".cortex" / "map.md"
    map_path.write_text(
        re.sub(
            r"^Generated: .+$",
            "Generated: 2000-01-01T00:00:00+00:00",
            map_path.read_text(),
            count=1,
            flags=re.MULTILINE,
        )
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--production", "--path", str(project)])

    assert result.exit_code == 1, result.output
    assert "0 errors" in result.output
    assert "warning" in result.output


def test_usage_command_reports_empty_counters(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["usage", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "grep=0" in result.output
