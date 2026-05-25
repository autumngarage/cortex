"""Tests for `cortex fleet check` and `cortex fleet update`.

Builds real temp git repos with varying `.cortex/` shapes (full via
`init`, partial, missing SPEC_VERSION, no `.cortex/`) so the in-process
freshness/doctor reuse and the never-commit-to-main invariant are
exercised against real stores — following the temp-git-repo conventions
in tests/test_sync.py and tests/test_audit.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands import fleet as fleet_mod
from cortex.commands.fleet import (
    CLASS_GREEN,
    SHAPE_FULL,
    SHAPE_MISSING,
    SHAPE_MISSING_SPEC,
    SHAPE_PARTIAL,
    classify_install_shape,
    discover_repos,
)
from cortex.commands.init import init_command


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_init(path: Path) -> None:
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")


def _make_full_repo(root: Path, name: str) -> Path:
    """Fresh `cortex init` + committed scaffold.

    A freshly-init'd store is structurally valid (0 doctor errors) but has
    stale generated layers (no Sources-hash on state.md, no .index.json) —
    i.e. the natural "stale but updateable" fixture.
    """
    repo = root / name
    repo.mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(repo)])
    assert result.exit_code == 0, result.output
    _git_init(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial cortex scaffold")
    return repo


def _make_current_full_repo(root: Path, name: str) -> Path:
    """Full repo brought current via `cortex update` (green fixture)."""
    repo = _make_full_repo(root, name)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["update", "--path", str(repo)], env={"CORTEX_DETERMINISTIC": "1"}
    )
    assert result.exit_code == 0, result.output
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "cortex update")
    return repo


def _make_partial_repo(root: Path, name: str) -> Path:
    """A .cortex/ with SPEC_VERSION but missing protocol.md + subdirs."""
    repo = root / name
    cortex = repo / ".cortex"
    cortex.mkdir(parents=True)
    (cortex / "SPEC_VERSION").write_text("1.1.0\n")
    return repo


def _make_missing_spec_repo(root: Path, name: str) -> Path:
    """A .cortex/ directory with no SPEC_VERSION."""
    repo = root / name
    cortex = repo / ".cortex"
    cortex.mkdir(parents=True)
    (cortex / "protocol.md").write_text("# protocol\n")
    return repo


def _make_no_cortex_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("# nothing here\n")
    return repo


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_full(tmp_path: Path) -> None:
    repo = _make_full_repo(tmp_path, "full")
    shape, spec = classify_install_shape(repo)
    assert shape == SHAPE_FULL
    assert spec is not None


def test_classify_partial(tmp_path: Path) -> None:
    repo = _make_partial_repo(tmp_path, "partial")
    shape, spec = classify_install_shape(repo)
    assert shape == SHAPE_PARTIAL
    assert spec == "1.1.0"


def test_classify_missing_spec(tmp_path: Path) -> None:
    repo = _make_missing_spec_repo(tmp_path, "nospec")
    shape, spec = classify_install_shape(repo)
    assert shape == SHAPE_MISSING_SPEC
    assert spec is None


def test_classify_no_cortex(tmp_path: Path) -> None:
    repo = _make_no_cortex_repo(tmp_path, "bare")
    shape, spec = classify_install_shape(repo)
    assert shape == SHAPE_MISSING
    assert spec is None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_explicit_paths_short_circuit(tmp_path: Path) -> None:
    a = _make_full_repo(tmp_path, "a")
    b = _make_full_repo(tmp_path, "b")
    found = discover_repos(
        explicit_paths=(a, b),
        paths_file=None,
        scan_roots=("/nonexistent",),
        cwd=tmp_path,
    )
    assert found == [a.resolve(), b.resolve()]


def test_discover_paths_file_json(tmp_path: Path) -> None:
    a = _make_full_repo(tmp_path, "a")
    pf = tmp_path / "paths.json"
    pf.write_text(json.dumps([str(a)]))
    found = discover_repos(
        explicit_paths=(),
        paths_file=pf,
        scan_roots=("/nonexistent",),
        cwd=tmp_path,
    )
    assert found == [a.resolve()]


def test_discover_paths_file_newline(tmp_path: Path) -> None:
    a = _make_full_repo(tmp_path, "a")
    b = _make_full_repo(tmp_path, "b")
    pf = tmp_path / "paths.txt"
    pf.write_text(f"# comment\n{a}\n{b}\n")
    found = discover_repos(
        explicit_paths=(),
        paths_file=pf,
        scan_roots=("/nonexistent",),
        cwd=tmp_path,
    )
    assert found == [a.resolve(), b.resolve()]


def test_discover_touchstone_projects(tmp_path: Path) -> None:
    a = _make_full_repo(tmp_path, "a")
    ts = tmp_path / ".touchstone-projects"
    ts.write_text(f"{a}\n")
    found = discover_repos(
        explicit_paths=(),
        paths_file=None,
        scan_roots=("/nonexistent",),
        cwd=tmp_path,
        touchstone_projects_file=ts,
    )
    assert found == [a.resolve()]


def test_discover_sibling_scan(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    a = _make_full_repo(root, "a")
    _make_no_cortex_repo(root, "bare")  # should be skipped
    found = discover_repos(
        explicit_paths=(),
        paths_file=None,
        scan_roots=(str(root),),
        cwd=tmp_path,
        # Isolate from the developer's real ~/.touchstone-projects.
        touchstone_projects_file=tmp_path / "no-such-touchstone-projects",
    )
    assert found == [a.resolve()]


def test_discover_cwd_fallback(tmp_path: Path) -> None:
    found = discover_repos(
        explicit_paths=(),
        paths_file=None,
        scan_roots=("/nonexistent",),
        cwd=tmp_path,
        touchstone_projects_file=tmp_path / "no-such-touchstone-projects",
    )
    assert found == [tmp_path.resolve()]


# ---------------------------------------------------------------------------
# `cortex fleet check`
# ---------------------------------------------------------------------------


def test_fleet_check_json_shape(tmp_path: Path) -> None:
    current = _make_current_full_repo(tmp_path, "current")
    stale = _make_full_repo(tmp_path, "stale")
    partial = _make_partial_repo(tmp_path, "partial")
    bare = _make_no_cortex_repo(tmp_path, "bare")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "fleet", "check", "--json",
            "--path", str(current),
            "--path", str(stale),
            "--path", str(partial),
            "--path", str(bare),
        ],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    # red repo present (partial) -> exit 1, but JSON still emitted on stdout.
    payload = json.loads(result.output)
    assert "repos" in payload
    records = {r["repo"]: r for r in payload["repos"]}
    assert set(records) == {"current", "stale", "partial", "bare"}

    # Stable contract: every documented field present on every record.
    expected_fields = {
        "path", "repo", "spec_version", "install_shape", "update_status",
        "update_reasons", "doctor_errors", "doctor_warnings", "audit_warnings",
        "classification", "next_command", "error",
    }
    for rec in payload["repos"]:
        assert set(rec) == expected_fields

    assert records["current"]["install_shape"] == SHAPE_FULL
    assert records["partial"]["install_shape"] == SHAPE_PARTIAL
    assert records["bare"]["install_shape"] == SHAPE_MISSING
    assert records["bare"]["classification"] == "skipped"
    assert records["partial"]["classification"] == "red"
    # A repo brought current via `cortex update` is green; a fresh init is stale/yellow.
    assert records["current"]["classification"] == CLASS_GREEN
    assert records["current"]["update_status"] == "current"
    assert records["stale"]["classification"] == "yellow"
    assert records["stale"]["update_status"] == "stale"
    assert records["stale"]["update_reasons"]  # non-empty reasons
    assert result.exit_code == 1  # partial is red


def test_fleet_check_human_groups(tmp_path: Path) -> None:
    current = _make_current_full_repo(tmp_path, "current")
    partial = _make_partial_repo(tmp_path, "partial")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "check", "--path", str(current), "--path", str(partial)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert "Current (green)" in result.output
    assert "Structurally invalid / partial (red)" in result.output
    assert "Fleet:" in result.output


def test_fleet_check_unclassifiable_repo_not_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = _make_full_repo(tmp_path, "full")

    def boom(_root: Path) -> tuple[int, int]:
        raise RuntimeError("doctor exploded")

    monkeypatch.setattr(fleet_mod, "_run_doctor_in_process", boom)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "check", "--json", "--path", str(full)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    payload = json.loads(result.output)
    rec = payload["repos"][0]
    assert rec["error"] is not None
    assert "doctor exploded" in rec["error"]
    assert rec["classification"] == "red"


# ---------------------------------------------------------------------------
# `cortex fleet update`
# ---------------------------------------------------------------------------


def test_fleet_update_dry_run_writes_nothing(tmp_path: Path) -> None:
    # A fresh full repo is structurally valid but stale (no Sources-hash,
    # no .index.json) — the natural eligible-for-update fixture.
    repo = _make_full_repo(tmp_path, "stale")
    before = (repo / ".cortex" / "state.md").read_text()
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "update", "--dry-run", "--path", str(repo)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "would-update" in result.output
    # Nothing rewritten — state.md unchanged and no index materialized.
    assert (repo / ".cortex" / "state.md").read_text() == before
    assert not (repo / ".cortex" / ".index.json").exists()


def test_fleet_update_skips_structurally_invalid(tmp_path: Path) -> None:
    partial = _make_partial_repo(tmp_path, "partial")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "update", "--path", str(partial)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "structurally invalid" in result.output


def test_fleet_update_in_place_refreshes(tmp_path: Path) -> None:
    repo = _make_full_repo(tmp_path, "stale")  # fresh = stale but valid; worktree clean
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "update", "--path", str(repo)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "updated" in result.output
    # state.md now has a regenerated provenance header.
    assert "Generator: cortex refresh-state" in (repo / ".cortex" / "state.md").read_text()


def test_fleet_update_in_place_skips_dirty_worktree(tmp_path: Path) -> None:
    repo = _make_full_repo(tmp_path, "stale")
    (repo / "uncommitted.txt").write_text("dirty\n")  # make worktree dirty
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "update", "--path", str(repo)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "dirty" in result.output
    # No refresh happened.
    assert not (repo / ".cortex" / ".index.json").exists()


def test_fleet_update_pr_never_commits_to_main(tmp_path: Path) -> None:
    repo = _make_full_repo(tmp_path, "stale")
    main_head_before = _git(repo, "rev-parse", "main").stdout.strip()

    runner = CliRunner()
    runner.invoke(
        cli,
        ["fleet", "update", "--pr", "--path", str(repo)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    # main must be untouched regardless of whether push/gh succeeded.
    main_head_after = _git(repo, "rev-parse", "main").stdout.strip()
    assert main_head_after == main_head_before

    # The refresh commit, if any, lives on the scoped branch — not on main.
    branches = _git(repo, "branch", "--list", "cortex/fleet-update").stdout
    if "cortex/fleet-update" in branches:
        # The scoped branch has the new commit, main does not.
        scoped_head = _git(repo, "rev-parse", "cortex/fleet-update").stdout.strip()
        assert scoped_head != main_head_before


def test_fleet_update_pr_refuses_existing_scoped_branch(tmp_path: Path) -> None:
    repo = _make_full_repo(tmp_path, "stale")
    _git(repo, "checkout", "-b", "cortex/fleet-update")
    (repo / "branch-note.txt").write_text("inspection state\n")
    _git(repo, "add", "branch-note.txt")
    _git(repo, "commit", "-m", "inspection state")
    scoped_head_before = _git(repo, "rev-parse", "cortex/fleet-update").stdout.strip()
    _git(repo, "checkout", "main")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "update", "--pr", "--path", str(repo)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "already exists" in result.output
    assert _git(repo, "rev-parse", "cortex/fleet-update").stdout.strip() == scoped_head_before
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"


def test_fleet_update_pr_skips_dirty_generated_layers(tmp_path: Path) -> None:
    repo = _make_full_repo(tmp_path, "stale")
    state_path = repo / ".cortex" / "state.md"
    state_before = state_path.read_text()
    state_path.write_text(f"{state_before}\nlocal edit\n")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["fleet", "update", "--pr", "--path", str(repo)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )

    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "generated layer paths are dirty" in result.output
    assert state_path.read_text() == f"{state_before}\nlocal edit\n"
    assert not (repo / ".cortex" / ".index.json").exists()
    assert "cortex/fleet-update" not in _git(repo, "branch", "--list").stdout
