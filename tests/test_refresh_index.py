"""Tests for `cortex refresh-index`."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli
from cortex.index import read_index, write_index


def _project(tmp_path: Path) -> Path:
    cortex = tmp_path / ".cortex"
    for rel in ("journal", "doctrine", "templates/journal"):
        (cortex / rel).mkdir(parents=True, exist_ok=True)
    (cortex / "SPEC_VERSION").write_text("0.5.0-dev\n")
    return tmp_path


def _write_journal(
    project: Path,
    slug: str,
    *,
    type_: str = "decision",
    tags: str | None = None,
    body: str = "Body.\n",
    promoted_to: str | None = None,
) -> Path:
    tags_line = f"Tags: {tags}\n" if tags is not None else ""
    promoted_line = f"Promoted-to: {promoted_to}\n" if promoted_to is not None else ""
    path = project / ".cortex" / "journal" / f"2026-04-23-{slug}.md"
    path.write_text(
        "---\n"
        "Date: 2026-04-23\n"
        f"Type: {type_}\n"
        f"{tags_line}"
        f"{promoted_line}"
        "---\n\n"
        f"# {slug}\n\n"
        f"{body}"
    )
    return path


def _run_refresh(project: Path) -> tuple[int, str]:
    result = CliRunner().invoke(cli, ["refresh-index", "--path", str(project)])
    return result.exit_code, result.output


def test_refresh_index_empty_journal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    code, output = _run_refresh(project)
    assert code == 0, output

    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert data["spec"] == "0.5.0-dev"
    assert data["candidates"] == []


def test_refresh_index_tagged_candidate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_journal(project, "tagged", tags="[candidate-doctrine]")

    code, output = _run_refresh(project)
    assert code == 0, output

    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert [c["id"] for c in data["candidates"]] == ["2026-04-23-tagged"]
    assert data["candidates"][0]["tags"] == ["candidate-doctrine"]


def test_refresh_index_pattern_matched_candidate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".cortex" / "config.toml").write_text(
        "[refresh-index]\n"
        'candidate_patterns = ["this is load-bearing"]\n'
    )
    _write_journal(project, "pattern", body="This is load-bearing for the protocol.\n")

    code, output = _run_refresh(project)
    assert code == 0, output

    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert [c["id"] for c in data["candidates"]] == ["2026-04-23-pattern"]


def test_refresh_index_decision_without_tag_or_pattern_is_not_candidate(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_journal(project, "ordinary", body="An ordinary decision.\n")

    code, output = _run_refresh(project)
    assert code == 0, output

    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert data["candidates"] == []


def test_refresh_index_promoted_candidate_retained(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_journal(project, "promoted", tags="[candidate-doctrine]")
    (project / ".cortex" / "doctrine" / "0007-promoted.md").write_text(
        "# Promoted\n\n"
        "**Date:** 2026-04-24\n"
        "**Promoted-from:** journal/2026-04-23-promoted\n"
    )

    code, output = _run_refresh(project)
    assert code == 0, output

    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["promoted_to"] == "doctrine/0007-promoted"


def test_write_index_atomic_write_ignores_partial_tempfile(tmp_path: Path) -> None:
    target = tmp_path / ".cortex" / ".index.json"
    target.parent.mkdir()
    partial = target.parent / ".index.json.partial.tmp"
    partial.write_text('{"candidates": [')

    write_index(target, {"spec": "0.5.0-dev", "generated": "now", "candidates": []})

    assert partial.read_text() == '{"candidates": ['
    assert read_index(target)["candidates"] == []


def test_refresh_index_idempotent_on_unchanged_inputs(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_journal(project, "tagged", tags="[candidate-doctrine]")

    first_code, first_output = _run_refresh(project)
    assert first_code == 0, first_output
    first = (project / ".cortex" / ".index.json").read_bytes()

    second_code, second_output = _run_refresh(project)
    assert second_code == 0, second_output
    assert (project / ".cortex" / ".index.json").read_bytes() == first


def test_journal_draft_refreshes_index_inline(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / ".cortex" / "config.toml").write_text(
        "[refresh-index]\n"
        'candidate_patterns = ["this is load-bearing"]\n'
    )
    (project / ".cortex" / "templates" / "journal" / "decision.md").write_text(
        "# {{ Title }}\n\n"
        "**Date:** {{ YYYY-MM-DD }}\n"
        "**Type:** decision\n\n"
        "This is load-bearing.\n"
    )

    result = CliRunner().invoke(
        cli,
        [
            "journal",
            "draft",
            "decision",
            "--title",
            "Inline refresh candidate",
            "--no-edit",
            "--path",
            str(project),
        ],
    )
    assert result.exit_code == 0, result.output

    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert len(data["candidates"]) == 1
    assert data["candidates"][0]["id"].endswith("inline-refresh-candidate")
