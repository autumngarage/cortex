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


def test_promote_stale_index_refuses_when_doctrine_reverse_link_exists(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)
    (project / ".cortex" / "doctrine" / "0100-existing.md").write_text(
        "# Existing\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-23\n"
        "**Promoted-from:** journal/2026-04-23-load-bearing-lesson\n"
        "**Load-priority:** default\n"
    )

    result = _promote(project, candidate_id)

    assert result.exit_code == 1, _combined(result)
    assert "already promoted to doctrine/0100-existing" in _combined(result)
    assert not (project / ".cortex" / "doctrine" / "0101-load-bearing-lesson.md").exists()


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


def test_promote_partial_failure_preserves_doctrine_when_index_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the index mutation fails after the Doctrine entry is written,
    the Doctrine entry MUST be preserved (Doctrine immutable per
    SPEC.md §4.2). With the corrected write order (Doctrine → index →
    Journal), an index failure leaves Doctrine on disk and no Journal
    entry — so any preserved artifact remains truthful.
    """
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)

    import cortex.commands.promote as promote_mod

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ValueError("simulated index corruption")

    monkeypatch.setattr(promote_mod, "_mark_promoted", _boom)

    result = _promote(project, candidate_id)

    assert result.exit_code == 2, _combined(result)
    combined = _combined(result)
    assert "promotion failed mid-write" in combined
    assert "Journal is append-only" in combined
    assert "Doctrine is immutable" in combined

    doctrine = project / ".cortex" / "doctrine" / "0100-load-bearing-lesson.md"
    assert doctrine.exists(), "Doctrine entry must NOT be deleted on rollback"

    # Journal must NOT exist yet — its prose claims the index was updated,
    # which would be a lie if persisted before the index actually changed.
    journals = list(
        (project / ".cortex" / "journal").glob("*promotion-0100-load-bearing-lesson.md")
    )
    assert journals == [], (
        "Journal entry must not be written before the index mutation succeeds"
    )

    assert str(doctrine) in combined


def test_promote_partial_failure_preserves_doctrine_and_index_when_journal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the Journal write fails after Doctrine + index already changed,
    both Doctrine and the updated index MUST be preserved. The operator
    is told to finish the Journal by hand.
    """
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)

    import cortex.commands.promote as promote_mod

    def _boom(_journal: object) -> None:
        raise OSError("simulated journal-write failure")

    monkeypatch.setattr(promote_mod, "_write_journal", _boom)

    result = _promote(project, candidate_id)

    assert result.exit_code == 2, _combined(result)
    combined = _combined(result)
    assert "promotion failed mid-write" in combined

    doctrine = project / ".cortex" / "doctrine" / "0100-load-bearing-lesson.md"
    assert doctrine.exists(), "Doctrine entry must NOT be deleted on rollback"

    # Index reflects the promotion so a follow-up retry sees the candidate
    # already promoted and refuses to double-write Doctrine.
    data = json.loads((project / ".cortex" / ".index.json").read_text())
    assert data["candidates"][0]["promoted_to"] == "doctrine/0100-load-bearing-lesson"

    assert str(doctrine) in combined


def test_promote_failure_before_any_write_reports_safe_to_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the very first write (Doctrine) fails, no partial artifacts exist;
    the operator should be told it's safe to retry.
    """
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)

    import cortex.commands.promote as promote_mod

    def _boom(_doctrine: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(promote_mod, "write_doctrine_entry", _boom)

    result = _promote(project, candidate_id)

    assert result.exit_code == 2, _combined(result)
    combined = _combined(result)
    assert "promotion failed mid-write" in combined
    assert "safe to retry" in combined
    assert not list((project / ".cortex" / "doctrine").glob("*.md"))


def test_promote_refuses_source_outside_journal(tmp_path: Path) -> None:
    """A stale or malformed `.index.json` whose candidate `source` points
    outside `.cortex/journal/` must refuse to promote — never silently
    promote a Doctrine entry, plan, template, or path-traversal target.
    """
    project = _project(tmp_path)
    # Write an index with a candidate whose source escapes journal/.
    write_index(
        project / ".cortex" / ".index.json",
        {
            "spec": "0.5.0",
            "generated": "2026-04-23T00:00:00-07:00",
            "candidates": [
                {
                    "id": "2026-04-23-evil",
                    "source": ".cortex/doctrine/0001-existing.md",
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
    result = _promote(project, "2026-04-23-evil")

    assert result.exit_code == 2, _combined(result)
    assert "not under .cortex/journal/" in _combined(result)
    # No Doctrine entry was created.
    assert not list((project / ".cortex" / "doctrine").glob("*.md"))


def test_promote_refuses_path_traversal_source(tmp_path: Path) -> None:
    """Candidate sources that try to traverse out of `.cortex/journal/`
    via `..` must be refused.
    """
    project = _project(tmp_path)
    write_index(
        project / ".cortex" / ".index.json",
        {
            "spec": "0.5.0",
            "generated": "2026-04-23T00:00:00-07:00",
            "candidates": [
                {
                    "id": "2026-04-23-traverse",
                    "source": ".cortex/journal/../../escape.md",
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
    result = _promote(project, "2026-04-23-traverse")

    assert result.exit_code == 2, _combined(result)
    assert "not under .cortex/journal/" in _combined(result)


def test_promote_canonicalizes_source_ref_for_promoted_from_link(
    tmp_path: Path,
) -> None:
    """A candidate `source` containing non-canonical components (`./`, `../`)
    must NOT propagate into Doctrine's `Promoted-from:` field. The reverse
    link is what `cortex refresh-index` and the duplicate-promotion check
    compare against; if it differs by a single `./`, the same Journal entry
    could be promoted twice. The `Promoted-from:` value must be derived
    from the canonical (resolved) path.
    """
    project = _project(tmp_path)
    # Create the real Journal file at the canonical location.
    canonical_id = "2026-04-23-load-bearing-lesson"
    canonical_path = project / ".cortex" / "journal" / f"{canonical_id}.md"
    canonical_path.write_text(
        "---\n"
        "Date: 2026-04-23\n"
        "Type: decision\n"
        "Cites: [plans/cortex-v1, doctrine/0001]\n"
        "Tags: [candidate-doctrine]\n"
        "---\n\n"
        "# Load-bearing lesson\n\n"
        "This decision should be promoted.\n"
    )
    # Index lists the source with a non-canonical `./` component.
    write_index(
        project / ".cortex" / ".index.json",
        {
            "spec": "0.5.0",
            "generated": "2026-04-23T00:00:00-07:00",
            "candidates": [
                {
                    "id": canonical_id,
                    "source": f".cortex/journal/./{canonical_id}.md",
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

    result = _promote(project, canonical_id)
    assert result.exit_code == 0, _combined(result)

    doctrine = project / ".cortex" / "doctrine" / "0100-load-bearing-lesson.md"
    text = doctrine.read_text()
    # Promoted-from MUST be the canonical journal/<id> form, no `./`.
    assert f"**Promoted-from:** journal/{canonical_id}" in text
    assert "./" not in text.split("**Promoted-from:**", 1)[1].split("\n", 1)[0]


def test_promote_partial_write_inside_function_is_still_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a write helper creates the file on disk and THEN fails (e.g. an
    OSError during write or close after the file was opened), the partial
    artifact must still be surfaced to the operator. The previous tracker
    only recorded paths after the helper returned successfully, so a
    crash mid-write would falsely report "safe to retry" and the orphan
    file would silently linger.
    """
    project = _project(tmp_path)
    candidate_id = _write_candidate(project)

    import cortex.commands.promote as promote_mod

    real_doctrine_dir = project / ".cortex" / "doctrine"

    def _half_write(promotion: object) -> None:
        # Simulate write-then-fail: create the target file, then raise
        # before the wrapper returns. This is the exact race the simple
        # `created.append(path)` pattern misses.
        target = real_doctrine_dir / "0100-load-bearing-lesson.md"
        target.write_text("partially written doctrine\n")
        raise OSError("disk full mid-write")

    monkeypatch.setattr(promote_mod, "write_doctrine_entry", _half_write)

    result = _promote(project, candidate_id)

    assert result.exit_code == 2, _combined(result)
    combined = _combined(result)
    assert "promotion failed mid-write" in combined

    orphan = real_doctrine_dir / "0100-load-bearing-lesson.md"
    assert orphan.exists(), "the partially-written file is still on disk"

    # Must NOT say it's safe to retry — there's an orphan to deal with.
    assert "safe to retry" not in combined
    assert str(orphan) in combined
