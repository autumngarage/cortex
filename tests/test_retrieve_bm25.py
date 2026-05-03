"""Tests for `cortex retrieve --mode bm25`."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.retrieve.chunker import chunk_markdown
from cortex.retrieve.index import rebuild_index, retrieve_index_path
from cortex.retrieve.query import query_bm25


def _project(tmp_path: Path) -> Path:
    cortex = tmp_path / ".cortex"
    for rel in ("doctrine", "journal", "plans", "templates/journal"):
        (cortex / rel).mkdir(parents=True, exist_ok=True)
    (cortex / "SPEC_VERSION").write_text("0.5.0\n")
    return tmp_path


def _write(project: Path, rel: str, body: str, *, frontmatter: str | None = None) -> Path:
    path = project / ".cortex" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"---\n{frontmatter}---\n\n" if frontmatter is not None else ""
    path.write_text(prefix + body)
    return path


def _rows(index: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(index)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM chunks ORDER BY path, chunk_idx").fetchall()
    finally:
        conn.close()


def test_build_index_from_tiny_corpus_and_query(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "doctrine/0001-why.md", "## Context\nDoctrine memory root cause.\n")
    _write(project, "journal/2026-05-01-note.md", "## Note\nJournal entry about retrieval.\n")
    _write(project, "plans/retrieve.md", "## Plan\nBM25 retrieval plan.\n")

    result = rebuild_index(project)
    hits = query_bm25(project, "doctrine", top_k=5)

    assert result.indexed_chunks == 3
    assert hits
    assert hits[0].path.startswith("doctrine/0001-why.md:")


def test_index_excludes_support_docs_and_templates(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "doctrine/0001.md", "## Doctrine\ncanonical memory token\n")
    _write(project, "protocol.md", "## Protocol\nsupport doc token\n")
    _write(project, "templates/journal/decision.md", "## Template\nsupport template token\n")

    rebuild_index(project)

    paths = {row["path"] for row in _rows(retrieve_index_path(project))}
    assert paths == {"doctrine/0001.md"}
    assert query_bm25(project, "support", top_k=5) == []


def test_incremental_rebuild_replaces_only_edited_file_chunks(tmp_path: Path) -> None:
    project = _project(tmp_path)
    edited = _write(project, "doctrine/0001.md", "## One\nalpha only\n")
    _write(project, "doctrine/0002.md", "## Two\nstable beta\n")
    rebuild_index(project)
    before = {(row["path"], row["chunk_idx"]): row["id"] for row in _rows(retrieve_index_path(project))}

    time.sleep(0.01)
    edited.write_text("## One\nalpha changed with gamma\n")
    rebuild_index(project)
    after = {(row["path"], row["chunk_idx"]): row["id"] for row in _rows(retrieve_index_path(project))}

    assert after[("doctrine/0002.md", 0)] == before[("doctrine/0002.md", 0)]
    assert after[("doctrine/0001.md", 0)] != before[("doctrine/0001.md", 0)]


def test_uncommitted_edit_invalidates_and_query_reflects_new_content(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = _write(project, "journal/2026-05-01-edit.md", "## Entry\noldterm only\n")
    rebuild_index(project)

    time.sleep(0.01)
    path.write_text("## Entry\nnewterm appears before commit\n")
    runner = CliRunner()
    result = runner.invoke(cli, ["retrieve", "newterm", "--json", "--path", str(project)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data[0]["path"].startswith("journal/2026-05-01-edit.md:")
    assert "newterm" in data[0]["excerpt"]


def test_file_deletion_removes_chunks(tmp_path: Path) -> None:
    project = _project(tmp_path)
    victim = _write(project, "doctrine/delete-me.md", "## Delete\nremove me\n")
    _write(project, "doctrine/keep.md", "## Keep\nkeep me\n")
    rebuild_index(project)

    victim.unlink()
    rebuild_index(project)

    paths = {row["path"] for row in _rows(retrieve_index_path(project))}
    assert "doctrine/delete-me.md" not in paths
    assert "doctrine/keep.md" in paths


def test_frontmatter_preserved_per_chunk(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(
        project,
        "plans/with-frontmatter.md",
        "## Plan\nfrontmatter token\n",
        frontmatter="Status: active\nTags: [retrieve, bm25]\n",
    )
    _write(project, "journal/no-frontmatter.md", "## Journal\nplain token\n")
    rebuild_index(project)

    rows = {row["path"]: row for row in _rows(retrieve_index_path(project))}
    assert json.loads(rows["plans/with-frontmatter.md"]["frontmatter_json"]) == {
        "Status": "active",
        "Tags": ["retrieve", "bm25"],
    }
    assert rows["journal/no-frontmatter.md"]["frontmatter_json"] is None


def test_frontmatter_line_offset_preserved_in_result_paths(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(
        project,
        "plans/offset.md",
        "## Plan\nneedle line\n",
        frontmatter="Status: active\nWritten: 2026-05-01\n",
    )
    rebuild_index(project)

    hits = query_bm25(project, "needle", top_k=1)

    assert hits[0].path == "plans/offset.md:6"


def test_section_aware_chunking_and_long_section_split() -> None:
    small = "## A\nalpha\n\n## B\nbeta\n"
    assert [chunk.content.splitlines()[0] for chunk in chunk_markdown(small)] == ["## A", "## B"]

    paragraphs = "\n\n".join(f"paragraph {idx} " + ("word " * 90) for idx in range(12))
    chunks = chunk_markdown("## Big\n\n" + paragraphs)
    assert len(chunks) > 1
    assert all(chunk.start_line <= chunk.end_line for chunk in chunks)


def test_overlap_behavior_includes_next_chunk_context() -> None:
    paragraphs = [
        f"para{idx} " + ("word " * 180)
        for idx in range(8)
    ]
    chunks = chunk_markdown("## Big\n\n" + "\n\n".join(paragraphs))
    assert len(chunks) > 1
    assert any(
        marker in chunks[0].content and marker in chunks[1].content
        for marker in ("para2", "para3", "para4")
    )


def test_atomic_write_keeps_existing_index_when_rebuild_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    path = _write(project, "doctrine/0001.md", "## One\nstable\n")
    rebuild_index(project)
    before = retrieve_index_path(project).read_bytes()
    path.write_text("## One\nchanged\n")

    import cortex.retrieve.index as index_mod

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated rebuild interruption")

    monkeypatch.setattr(index_mod, "_replace_file_chunks", fail_replace)
    with pytest.raises(RuntimeError):
        rebuild_index(project)

    assert retrieve_index_path(project).read_bytes() == before
    assert not retrieve_index_path(project).with_name("chunks.sqlite.tmp").exists()


def test_no_rebuild_skips_stale_refresh_and_warns(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = _write(project, "journal/2026-05-01-note.md", "## Note\noldtoken\n")
    rebuild_index(project)
    time.sleep(0.01)
    path.write_text("## Note\nnewtoken\n")

    result = CliRunner().invoke(
        cli,
        ["retrieve", "newtoken", "--no-rebuild", "--json", "--path", str(project)],
    )

    assert result.exit_code == 0, result.output
    assert "may be stale" in (result.output + (getattr(result, "stderr", "") or ""))
    assert json.loads(result.stdout) == []


def test_json_output_schema(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "doctrine/0001.md", "## One\nschema doctrine\n", frontmatter="Status: Accepted\n")

    result = CliRunner().invoke(cli, ["retrieve", "schema", "--json", "--path", str(project)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert set(data[0]) == {"path", "score", "frontmatter", "excerpt"}
    assert isinstance(data[0]["path"], str)
    assert isinstance(data[0]["score"], float)
    assert isinstance(data[0]["frontmatter"], dict)
    assert isinstance(data[0]["excerpt"], str)


def test_punctuation_query_is_treated_as_literal_text(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "journal/2026-05-01-release.md", "## Release\nv0.3.0 shipped on 2026-05-01.\n")
    _write(project, "plans/cache.md", "## Cache\n.cortex/.index stores derived chunks.\n")
    rebuild_index(project)

    version = CliRunner().invoke(cli, ["retrieve", "v0.3.0", "--json", "--path", str(project)])
    date_result = CliRunner().invoke(cli, ["retrieve", "2026-05-01", "--json", "--path", str(project)])
    path_result = CliRunner().invoke(cli, ["retrieve", ".cortex/.index", "--json", "--path", str(project)])

    assert version.exit_code == 0, version.output
    assert date_result.exit_code == 0, date_result.output
    assert path_result.exit_code == 0, path_result.output
    assert json.loads(version.output)
    assert json.loads(date_result.output)
    assert json.loads(path_result.output)


def test_fts5_missing_falls_back_to_grep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    import cortex.retrieve.index as index_mod

    monkeypatch.setattr(
        index_mod,
        "ensure_fts5_available",
        lambda: (_ for _ in ()).throw(index_mod.FTS5UnavailableError("missing")),
    )

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> object:
        calls.append(cmd)

        class Completed:
            stdout = "grep fallback output\n"
            stderr = ""
            returncode = 0

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = CliRunner().invoke(cli, ["retrieve", "needle", "--path", str(project)])

    assert result.exit_code == 0, result.output
    assert "FTS5 extension not available" in (result.output + (getattr(result, "stderr", "") or ""))
    assert "grep fallback output" in result.output
    assert calls and "grep" in calls[0]


def test_fts5_missing_json_fallback_preserves_json_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    import cortex.retrieve.index as index_mod

    monkeypatch.setattr(
        index_mod,
        "ensure_fts5_available",
        lambda: (_ for _ in ()).throw(index_mod.FTS5UnavailableError("missing")),
    )

    def fake_run(_cmd: list[str], **_kwargs: object) -> object:
        class Completed:
            stdout = "grep fallback output\n"
            stderr = ""
            returncode = 0

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = CliRunner().invoke(cli, ["retrieve", "needle", "--json", "--path", str(project)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert set(data[0]) == {"path", "score", "frontmatter", "excerpt"}
    assert data[0]["excerpt"] == "grep fallback output"


def test_grep_fallback_failure_exits_nonzero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    import cortex.retrieve.index as index_mod

    monkeypatch.setattr(
        index_mod,
        "ensure_fts5_available",
        lambda: (_ for _ in ()).throw(index_mod.FTS5UnavailableError("missing")),
    )

    def fake_run(_cmd: list[str], **_kwargs: object) -> object:
        class Completed:
            stdout = ""
            stderr = "grep failed\n"
            returncode = 3

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = CliRunner().invoke(cli, ["retrieve", "needle", "--json", "--path", str(project)])

    assert result.exit_code == 3
    assert "grep failed" in (result.output + (getattr(result, "stderr", "") or ""))


def test_cortex_grep_unaffected_by_retrieve_index_and_sqlite_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, "doctrine/0001.md", "## One\nneedle\n")

    import cortex.commands.grep as grep_module

    monkeypatch.setattr(grep_module, "_find_rg", lambda: "/usr/bin/rg")

    def fake_run(_cmd: list[str], *_args: object, **_kwargs: object) -> object:
        class Completed:
            stdout = ""
            stderr = ""
            returncode = 1

        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    sys.modules.pop("sqlite3", None)
    first = CliRunner().invoke(cli, ["grep", "needle", "--path", str(project)])
    rebuild_index(project)
    sys.modules.pop("sqlite3", None)
    second = CliRunner().invoke(cli, ["grep", "needle", "--path", str(project)])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert first.output == second.output
    assert "sqlite3" not in sys.modules


def test_refresh_index_retrieve_flag_builds_both_indexes(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "journal/2026-05-01-note.md", "## Note\nretrieve flag\n")

    plain = CliRunner().invoke(cli, ["refresh-index", "--path", str(project)])
    assert plain.exit_code == 0, plain.output
    assert (project / ".cortex" / ".index.json").exists()
    assert not retrieve_index_path(project).exists()

    with_retrieve = CliRunner().invoke(cli, ["refresh-index", "--retrieve", "--path", str(project)])
    assert with_retrieve.exit_code == 0, with_retrieve.output
    assert (project / ".cortex" / ".index.json").exists()
    assert retrieve_index_path(project).exists()


def test_cortex_cache_dir_controls_index_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path / "project")
    _write(project, "doctrine/0001.md", "## One\ncache override token\n")
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("CORTEX_CACHE_DIR", str(cache_dir))

    result = rebuild_index(project)

    assert result.path == cache_dir / "chunks.sqlite"
    assert (cache_dir / "chunks.sqlite").exists()
    assert not (project / ".cortex" / ".index" / "chunks.sqlite").exists()


def test_journal_draft_auto_rebuilds_existing_retrieve_index(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "doctrine/0001.md", "## One\nseed\n")
    (project / ".cortex" / "templates" / "journal" / "decision.md").write_text(
        "# {{ Title }}\n\n**Date:** {{ YYYY-MM-DD }}\n**Type:** decision\n\nnew journal token\n"
    )
    rebuild_index(project)

    result = CliRunner().invoke(
        cli,
        [
            "journal",
            "draft",
            "decision",
            "--title",
            "Retrieve Hook",
            "--no-edit",
            "--path",
            str(project),
        ],
    )

    assert result.exit_code == 0, result.output
    paths = {row["path"] for row in _rows(retrieve_index_path(project))}
    today = date.today().isoformat()
    assert f"journal/{today}-retrieve-hook.md" in paths


def test_refresh_state_auto_rebuilds_existing_retrieve_index(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _write(project, "state.md", "## Old\nold state token\n")
    rebuild_index(project)

    result = CliRunner().invoke(
        cli,
        ["refresh-state", "--path", str(project)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )

    assert result.exit_code == 0, result.output
    hits = query_bm25(project, "Project", top_k=5)
    assert any(hit.path.startswith("state.md:") for hit in hits)


def test_warm_query_latency_under_ci_budget(tmp_path: Path) -> None:
    project = _project(tmp_path)
    for idx in range(200):
        _write(project, f"journal/2026-05-01-entry-{idx:03}.md", f"## Entry\nlatency token {idx}\n")
    rebuild_index(project)

    start = time.perf_counter()
    hits = query_bm25(project, "latency", top_k=10)
    elapsed = time.perf_counter() - start

    assert hits
    assert elapsed < 2.0
