"""Tests for local grep/retrieve/manifest usage telemetry."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command


def _init_project(tmp_path: Path) -> Path:
    result = CliRunner().invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _usage_path(project: Path) -> Path:
    return project / ".cortex" / ".index" / "usage.json"


def _read_usage(project: Path) -> dict[str, object]:
    payload: dict[str, object] = json.loads(_usage_path(project).read_text())
    return payload


def test_manifest_creates_usage_file_and_increments_manifest(tmp_path: Path) -> None:
    project = _init_project(tmp_path)

    result = CliRunner().invoke(cli, ["manifest", "--path", str(project), "--budget", "8000"])

    assert result.exit_code == 0, result.output
    usage = _read_usage(project)
    assert usage["schema_version"] == 1
    assert usage["since"]
    assert usage["counts"] == {
        "grep": 0,
        "retrieve_bm25": 0,
        "retrieve_semantic": 0,
        "retrieve_hybrid": 0,
        "manifest": 1,
    }


def test_manifest_corrupt_usage_resets_with_stderr_warning(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    path = _usage_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json")

    result = CliRunner().invoke(cli, ["manifest", "--path", str(project), "--budget", "8000"])

    assert result.exit_code == 0, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "usage.json is corrupt" in combined
    usage = _read_usage(project)
    counts = usage["counts"]
    assert isinstance(counts, dict)
    assert counts["manifest"] == 1


def test_refresh_index_resets_usage_counters(tmp_path: Path) -> None:
    project = _init_project(tmp_path)
    path = _usage_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "since": "2026-01-01T00:00:00Z",
                "counts": {
                    "grep": 8,
                    "retrieve_bm25": 5,
                    "retrieve_semantic": 3,
                    "retrieve_hybrid": 2,
                    "manifest": 4,
                },
            }
        )
    )

    result = CliRunner().invoke(cli, ["refresh-index", "--path", str(project)])

    assert result.exit_code == 0, result.output
    usage = _read_usage(project)
    assert usage["since"] != "2026-01-01T00:00:00Z"
    assert usage["counts"] == {
        "grep": 0,
        "retrieve_bm25": 0,
        "retrieve_semantic": 0,
        "retrieve_hybrid": 0,
        "manifest": 0,
    }
