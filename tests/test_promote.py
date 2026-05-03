"""Tests for `cortex promote <id>`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from cortex.cli import cli
from cortex.index import write_index


def _project(tmp_path: Path) -> Path:
    cortex = tmp_path / ".cortex"
    for rel in ("journal", "doctrine", "templates/journal", "templates/doctrine"):
        (cortex / rel).mkdir(parents=True, exist_ok=True)
    (cortex / "SPEC_VERSION").write_text("0.5.0\n")
    (cortex / "templates" / "doctrine" / "candidate.md").write_text(
        "# {{ nnnn }} - {{ Title - active-voice claim }}\n\n"
        "> {{ One-sentence claim in active voice. This is the summary that loads "
        "into context when an agent grep-hits this entry. Make it readable standalone. }}\n\n"
        "**Status:** Proposed\n"
        "**Date:** {{ YYYY-MM-DD }}\n"
        "**Promoted-from:** {{ journal/<date>-<slug> or plans/<slug> or - (direct authoring) }}\n"
        "**Cites:** {{ Cites }}\n"
        "**Load-priority:** {{ always | default }}\n\n"
        "## Context\n\n"
        "{{ What situation or pattern produced this claim? What alternatives were weighed? "
        "Link to the supporting Journal entries, Plans, or Procedures. An editor reviewing "
        "this candidate should be able to judge from Context alone whether the claim generalizes. }}\n\n"
        "## Decision\n\n"
        "{{ We will / we won't - stated as a claim, not a recommendation. Include the "
        "specific boundary: what falls inside this decision and what falls outside. }}\n\n"
        "## Consequences\n\n"
        "- **What becomes easier:** {{ ... }}\n"
    )
    (cortex / "templates" / "journal" / "promotion.md").write_text(
        "# {{ Title }}\n\n"
        "**Date:** {{ YYYY-MM-DD }}\n"
        "**Type:** promotion\n"
        "**Cites:** {{ Cites }}\n\n"
        "> {{ Summary }}\n\n"
        "## Context\n\n"
        "{{ Source }} was accepted into Doctrine as {{ Doctrine }}.\n"
    )
    return tmp_path


def _write_candidate(project: Path, slug: str = "load-bearing-lesson") -> str:
    candidate_id = f"2026-04-23-{slug}"
    path = project / ".cortex" / "journal" / f"{candidate_id}.md"
    path.write_text(
        "---\n"
        "Date: 2026-04-23\n"
        "Type: decision\n"
        "Cites: [plans/cortex-v1, doctrine/0001]\n"
        "Tags: [candidate-doctrine]\n"
        "---\n\n"
        "# Load-bearing lesson\n\n"
        "This decision should be promoted.\n"
    )
    write_index(
        project / ".cortex" / ".index.json",
        {
            "spec": "0.5.0",
            "generated": "2026-04-23T00:00:00-07:00",
            "candidates": [
                {
                    "id": candidate_id,
                    "source": f".cortex/journal/{candidate_id}.md",
                    "type": "decision",
                    "last_touched": "2026-04-23",
                    "age_days": 1,
                    "tags": ["candidate-doctrine"],
                    "supersedes": None,
                    "promoted_to": None,
                }
            ],
        },
    )
    return candidate_id


@pytest.fixture(autouse=True)
def no_external_context(monkeypatch: pytest.MonkeyPatch) -> None:
    import cortex.commands.promote as promote_mod

    monkeypatch.setattr(promote_mod, "_gather_git_context", lambda _project: [])
    monkeypatch.setattr(promote_mod, "_gather_gh_pr_context", lambda _project: (None, "test"))


def _promote(project: Path, *args: str) -> Result:
    return CliRunner().invoke(cli, ["promote", *args, "--path", str(project)])


def _combined(result: Result) -> str:
    return result.output + (getattr(result, "stderr", "") or "")


def test_promote_round_trip_writes_doctrine_index_and_journal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)

    result = _promote(project, candidate_id)

    assert result.exit_code == 0, _combined(result)
    doctrine = project / ".cortex" / "doctrine" / "0100-load-bearing-lesson.md"
    assert doctrine.exists()
    doctrine_text = doctrine.read_text()
    assert "**Status:** Accepted" in doctrine_text
    assert "**Load-priority:** default" in doctrine_text
    assert "**Promoted-from:** journal/2026-04-23-load-bearing-lesson" in doctrine_text
    assert "**Cites:** plans/cortex-v1, doctrine/0001" in doctrine_text

    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert data["candidates"][0]["promoted_to"] == "doctrine/0100-load-bearing-lesson"

    journals = list((project / ".cortex" / "journal").glob("*promotion-0100-load-bearing-lesson.md"))
    assert len(journals) == 1
    journal_text = journals[0].read_text()
    assert "**Type:** promotion" in journal_text
    assert "journal/2026-04-23-load-bearing-lesson, doctrine/0100-load-bearing-lesson" in journal_text
    assert str(doctrine) in result.output
    assert str(journals[0]) in result.output


def test_promote_unknown_id_exits_one_with_clear_message(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write_candidate(project)

    result = _promote(project, "missing-id")

    assert result.exit_code == 1, _combined(result)
    assert "no promotion candidate" in _combined(result)
    assert "missing-id" in _combined(result)


def test_promote_already_promoted_refuses_and_force_yes_proceeds(tmp_path: Path) -> None:
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)
    data = json.loads((project / ".cortex" / ".index.json").read_text())
    data["candidates"][0]["promoted_to"] = "doctrine/0100-existing"
    write_index(project / ".cortex" / ".index.json", data)
    (project / ".cortex" / "doctrine" / "0100-existing.md").write_text(
        "# Existing\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-23\n"
        "**Load-priority:** default\n"
    )

    refused = _promote(project, candidate_id)
    assert refused.exit_code == 1, _combined(refused)
    assert "already promoted" in _combined(refused)

    forced = _promote(project, candidate_id, "--force", "--yes")
    assert forced.exit_code == 0, _combined(forced)
    assert (project / ".cortex" / "doctrine" / "0101-load-bearing-lesson.md").exists()
    updated = json.loads((project / ".cortex" / ".index.json").read_text())
    assert updated["candidates"][0]["promoted_to"] == "doctrine/0101-load-bearing-lesson"


def test_promote_slug_numbering_reserves_low_range(tmp_path: Path) -> None:
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)
    (project / ".cortex" / "doctrine" / "0001-canonical.md").write_text(
        "# Canonical\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-23\n"
        "**Load-priority:** default\n"
    )
    (project / ".cortex" / "doctrine" / "0099-reserved.md").write_text(
        "# Reserved\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-23\n"
        "**Load-priority:** default\n"
    )

    result = _promote(project, candidate_id)

    assert result.exit_code == 0, _combined(result)
    assert (project / ".cortex" / "doctrine" / "0100-load-bearing-lesson.md").exists()


def test_promote_dry_run_does_not_modify_files(tmp_path: Path) -> None:
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)
    before = (project / ".cortex" / ".index.json").read_bytes()

    result = _promote(project, candidate_id, "--dry-run")

    assert result.exit_code == 0, _combined(result)
    assert "would write" in result.output
    assert not list((project / ".cortex" / "doctrine").glob("*.md"))
    assert len(list((project / ".cortex" / "journal").glob("*.md"))) == 1
    assert (project / ".cortex" / ".index.json").read_bytes() == before


def test_promote_missing_promotion_template_exits_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)

    def _missing(_cortex_dir: Path, journal_type: str) -> str:
        if journal_type == "promotion":
            raise FileNotFoundError(journal_type)
        raise AssertionError(journal_type)

    import cortex.commands.promote as promote_mod

    monkeypatch.setattr(promote_mod, "_resolve_template", _missing)

    result = _promote(project, candidate_id)

    assert result.exit_code == 2, _combined(result)
    assert "no template for journal type 'promotion'" in _combined(result)
    assert not list((project / ".cortex" / "doctrine").glob("*.md"))
