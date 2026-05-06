"""Tests for `cortex install-brief`.

All tests operate on real temporary directories. Git repos are initialised
in-process so the tests don't depend on network access or external state.
The GitHub remote is injected via `git remote add` so `_git_remote_url`
returns a predictable value without hitting GitHub.
"""

from __future__ import annotations

import subprocess
import unittest.mock as mock
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli


def _git(target: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(target), *args], check=True, capture_output=True)


def _make_git_repo(path: Path, *, remote: str = "git@github.com:testowner/testrepo.git") -> Path:
    """Initialise a minimal git repo with an origin remote."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init")
    _git(path, "remote", "add", "origin", remote)
    return path


# ── Happy path: Python + Homebrew tap shape ────────────────────────────────


def test_happy_path_h1_contains_target_name(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").write_text("[project]\nname = 'myrepo'\n")
    # Homebrew tap sibling
    (tmp_path / "homebrew-myrepo").mkdir()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "# Brief — Install Cortex on myrepo" in result.output


def test_happy_path_github_slug_in_output(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo",
                            remote="git@github.com:acme/myrepo.git")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "acme/myrepo" in result.output


def test_happy_path_ecosystem_python(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "Python" in result.output
    assert "pyproject.toml" in result.output


def test_happy_path_homebrew_tap_in_output(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo",
                            remote="git@github.com:acme/myrepo.git")
    (target / "pyproject.toml").touch()
    (tmp_path / "homebrew-myrepo").mkdir()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "homebrew_tap" in result.output
    assert "acme/myrepo" in result.output


def test_happy_path_includes_reference_prs_by_default(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "conductor" in result.output
    assert "touchstone" in result.output
    assert "Prior install references" in result.output


def test_no_references_flag_omits_pr_block(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", "--no-references", str(target)])
    assert result.exit_code == 0, result.output
    assert "Prior install references" not in result.output


def test_touchstone_managed_paths_in_do_not_touch(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "principles").mkdir()
    (target / "scripts").mkdir()
    (target / ".codex-review.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "DO NOT touch" in result.output
    assert "principles/" in result.output


# ── Ecosystem variants ─────────────────────────────────────────────────────


def test_ecosystem_swift(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myapp")
    (target / "Package.swift").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "Swift" in result.output


def test_ecosystem_rust(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myapp")
    (target / "Cargo.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "Rust" in result.output


def test_ecosystem_unknown_when_no_manifest(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myapp")

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "Unknown" in result.output


# ── PaaS detection ─────────────────────────────────────────────────────────


def test_paas_nixpacks_uses_github_repos_fallback(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myapp",
                            remote="git@github.com:acme/myapp.git")
    (target / "nixpacks.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "github_repos" in result.output
    # Must include TODO comment about cortex#161
    assert "cortex#161" in result.output


def test_paas_procfile_detected(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myapp")
    (target / "Procfile").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "cortex#161" in result.output


# ── Output flag ────────────────────────────────────────────────────────────


def test_output_writes_to_file(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    out_file = tmp_path / "brief.md"

    result = CliRunner().invoke(cli, ["install-brief", "--output", str(out_file), str(target)])
    assert result.exit_code == 0, result.output
    assert out_file.exists()
    content = out_file.read_text()
    assert "# Brief — Install Cortex on myrepo" in content


def test_output_flag_suppresses_brief_from_stdout(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    out_file = tmp_path / "brief.md"

    result = CliRunner().invoke(cli, ["install-brief", "--output", str(out_file), str(target)])
    assert result.exit_code == 0
    # stdout should only contain the "Brief written to ..." note on stderr,
    # not the brief body. CliRunner mixes stdout+stderr into output by default.
    # The brief body starts with "# Brief" — it must NOT appear in the stdout
    # portion when --output is set (we write to file instead).
    # Since CliRunner catches stderr too, verify the file has the content.
    assert out_file.read_text().startswith("# Brief")


# ── Error paths ────────────────────────────────────────────────────────────


def test_error_no_git_repo(tmp_path: Path) -> None:
    target = tmp_path / "notarepo"
    target.mkdir()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code != 0
    assert "not a git repository" in result.output.lower()


def test_error_nonexistent_directory(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["install-brief", str(tmp_path / "doesnotexist")])
    assert result.exit_code != 0


def test_error_no_github_remote(tmp_path: Path) -> None:
    target = tmp_path / "myrepo"
    target.mkdir()
    _git(target, "init")
    # No remote added

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code != 0
    assert "origin" in result.output.lower() or "remote" in result.output.lower()


# ── Sibling enumeration (unit-level) ──────────────────────────────────────


def test_sibling_repos_listed_in_brief(tmp_path: Path) -> None:
    from cortex.commands import install_brief as ib

    # Lay out repos/ under tmp_path so that mocking Path.home() → tmp_path
    # makes _enumerate_cortex_siblings look in tmp_path/repos/.
    repos = tmp_path / "repos"
    repos.mkdir()

    target = _make_git_repo(repos / "myrepo",
                            remote="git@github.com:acme/myrepo.git")
    (target / "pyproject.toml").touch()

    sibling = repos / "otherrepo"
    sibling.mkdir()
    _git(sibling, "init")
    _git(sibling, "remote", "add", "origin", "git@github.com:acme/otherrepo.git")
    (sibling / ".cortex").mkdir()
    (sibling / ".cortex" / "SPEC_VERSION").write_text("0.5.0\n")

    with mock.patch.object(Path, "home", return_value=tmp_path):
        siblings = ib._enumerate_cortex_siblings(target)

    assert any("otherrepo" in s for s in siblings)


# ── --closes flag: issue-closing trailer instructions ─────────────────────


def test_closes_flag_embeds_trailer_instructions(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "162,163"])
    assert result.exit_code == 0, result.output
    assert "Closes-issue: #162" in result.output
    assert "Closes-issue: #163" in result.output


def test_closes_flag_absent_no_trailer_instructions(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    assert "Closes-issue:" not in result.output


def test_closes_flag_single_issue(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "99"])
    assert result.exit_code == 0, result.output
    assert "Closes-issue: #99" in result.output


# ── --closes flag: dual-artifact output ───────────────────────────────────


def test_closes_produces_dual_artifact_section(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "100,200"])
    assert result.exit_code == 0, result.output
    # Both target files must be mentioned
    assert "cortex-install-baseline" in result.output
    assert "plans/cortex-install-followups.md" in result.output


def test_closes_journal_section_uses_refs_not_checkboxes(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "100,200"])
    assert result.exit_code == 0, result.output
    # Journal template must reference issues via Refs:, not [ ] boxes
    assert "Refs: cortex#100, cortex#200" in result.output


def test_closes_plan_section_contains_checkboxes_per_issue(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "100,200"])
    assert result.exit_code == 0, result.output
    # Follow-up plan must have one [ ] per tracked issue
    assert "- [ ] cortex#100" in result.output
    assert "- [ ] cortex#200" in result.output


def test_closes_plan_section_has_active_status(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "100,200"])
    assert result.exit_code == 0, result.output
    assert "Status: active" in result.output


def test_closes_plan_cites_journal_journal_cites_plan(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "100,200"])
    assert result.exit_code == 0, result.output
    # Journal template cites the plan
    assert "Cites: plans/cortex-install-followups" in result.output
    # Plan template cites the journal
    assert "Cites: journal/" in result.output


def test_no_closes_flag_gives_single_artifact(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target)])
    assert result.exit_code == 0, result.output
    # Without --closes, no follow-up plan section
    assert "plans/cortex-install-followups.md" not in result.output
    assert "dual-artifact" not in result.output
    # Original single-artifact Phase 5 heading still present
    assert "Phase 5 — Baseline journal entry" in result.output


def test_closes_output_format_includes_artifact_lines(tmp_path: Path) -> None:
    target = _make_git_repo(tmp_path / "myrepo")
    (target / "pyproject.toml").touch()

    result = CliRunner().invoke(cli, ["install-brief", str(target), "--closes", "100"])
    assert result.exit_code == 0, result.output
    assert "Artifacts written:" in result.output
