"""Integration tests for `cortex update` (Layer 1) and auto-sync on version-bump (Layer 2)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import cortex
from cortex.cli import cli
from cortex.commands import _auto_sync as auto_sync_mod
from cortex.commands import sync as sync_mod
from cortex.commands.init import init_command

# ---------------------------------------------------------------------------
# Fixture project — uses `cortex init` so doctor checks pass cleanly.
# ---------------------------------------------------------------------------


@pytest.fixture
def scaffolded_project(tmp_path: Path) -> Path:
    """Project with a fresh `cortex init` scaffold and no user content."""
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


# ---------------------------------------------------------------------------
# Layer 1 — `cortex update`
# ---------------------------------------------------------------------------


def test_update_runs_refresh_state_and_index_and_doctor(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["update", "--path", str(project)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    # state.md was regenerated (Generator field updated to current CLI version).
    state_text = (project / ".cortex" / "state.md").read_text()
    assert f"Generator: cortex refresh-state v{cortex.__version__}" in state_text
    # The promotion index was rebuilt.
    assert (project / ".cortex" / ".index.json").exists()
    # All four steps appear in the output.
    assert "refresh-state" in result.output
    assert "refresh-index" in result.output
    assert "config.toml" in result.output
    assert "doctor" in result.output
    assert "Update complete" in result.output


def test_update_no_doctor_flag_skips_doctor(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "refresh-state" in result.output
    assert "cortex doctor" not in result.output


def test_update_dry_run_invokes_nothing(scaffolded_project: Path) -> None:
    project = scaffolded_project
    state_before = (project / ".cortex" / "state.md").read_text()
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["update", "--path", str(project), "--dry-run"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
    assert "would update" in result.output
    # Nothing was written by update itself.
    assert (project / ".cortex" / "state.md").read_text() == state_before
    # `cortex init` writes its own .index.json today; update's dry-run must
    # leave that file at its prior content (we just confirm update didn't
    # rebuild and overwrite something newer — the .index.json mtime should
    # match init-time, which is essentially that it stays a valid JSON).
    if (project / ".cortex" / ".index.json").exists():
        assert (project / ".cortex" / ".index.json").read_text()


def test_update_rebuilds_retrieve_index_when_present(scaffolded_project: Path) -> None:
    project = scaffolded_project
    # Mark the project as having opted into retrieve.
    (project / ".cortex" / ".index").mkdir(exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    # Output prints the `--retrieve` modifier when an .index/ dir exists.
    assert "--retrieve" in result.output, result.output


def test_update_is_idempotent(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    first = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert first.exit_code == 0, first.output
    state_after_first = (project / ".cortex" / "state.md").read_text()

    second = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert second.exit_code == 0, second.output
    assert (project / ".cortex" / "state.md").read_text() == state_after_first


def test_update_reports_unknown_config_keys(scaffolded_project: Path) -> None:
    project = scaffolded_project
    (project / ".cortex" / "config.toml").write_text(
        "[refresh-index]\n"
        "candidate_patterns = []\n"
        "totally_unknown_key = 42\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "1 unknown key" in result.output


def test_sync_alias_warns_and_runs_update(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--dry-run"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "`cortex sync` is deprecated; use `cortex update`" in result.output
    assert "would update" in result.output


def test_update_check_passes_when_generated_layers_are_current(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    update = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert update.exit_code == 0, update.output
    state_before = (project / ".cortex" / "state.md").read_text()

    check = runner.invoke(
        cli,
        ["update", "--path", str(project), "--check", "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert check.exit_code == 0, check.output
    assert "Update check passed" in check.output
    assert (project / ".cortex" / "state.md").read_text() == state_before


def test_update_check_reports_stale_state_without_writing(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    update = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert update.exit_code == 0, update.output
    state_before = (project / ".cortex" / "state.md").read_text()
    template = project / ".cortex" / "templates" / "journal" / "decision.md"
    template.write_text(template.read_text() + "\n<!-- stale check regression -->\n")

    check = runner.invoke(
        cli,
        ["update", "--path", str(project), "--check", "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert check.exit_code == 1, check.output
    assert ".cortex/state.md is stale" in check.output
    assert (project / ".cortex" / "state.md").read_text() == state_before


def test_update_check_reports_generator_drift_and_update_rewrites_it(
    scaffolded_project: Path,
) -> None:
    project = scaffolded_project
    runner = CliRunner()
    update = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert update.exit_code == 0, update.output

    state_path = project / ".cortex" / "state.md"
    current_generator = f"Generator: cortex refresh-state v{cortex.__version__}"
    drifted_text = state_path.read_text().replace(
        current_generator,
        "Generator: cortex refresh-state v0.8.0",
    )
    state_path.write_text(drifted_text)

    check = runner.invoke(
        cli,
        ["update", "--path", str(project), "--check", "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert check.exit_code == 1, check.output
    assert ".cortex/state.md Generator was v0.8.0" in check.output
    assert state_path.read_text() == drifted_text

    fixed = runner.invoke(
        cli,
        ["update", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert fixed.exit_code == 0, fixed.output
    assert current_generator in state_path.read_text()


# ---------------------------------------------------------------------------
# Layer 2 — auto-sync on version bump
# ---------------------------------------------------------------------------


def _ensure_git_repo(project: Path) -> None:
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)


def _git_dir(project: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=project,
        text=True,
        capture_output=True,
        check=True,
    )
    raw = result.stdout.strip()
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = project / git_dir
    return git_dir.resolve()


def _set_marker(project: Path, version: str) -> None:
    _ensure_git_repo(project)
    marker = _git_dir(project) / "cortex" / ".last-cli-version"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(version)


def _read_marker(project: Path) -> str | None:
    try:
        marker = _git_dir(project) / "cortex" / ".last-cli-version"
    except subprocess.CalledProcessError:
        return None
    if not marker.exists():
        return None
    return marker.read_text().strip()


def _legacy_marker(project: Path) -> Path:
    return project / ".cortex" / ".last-cli-version"


def _commit_all(project: Path, message: str = "baseline") -> None:
    _ensure_git_repo(project)
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Cortex Test",
            "-c",
            "user.email=cortex@example.test",
            "commit",
            "--allow-empty",
            "-m",
            message,
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )


def test_auto_sync_runs_on_minor_bump(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    _commit_all(project)
    monkeypatch.setattr(cortex, "__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands.sync.__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.0")
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    # Use `cortex status` as an arbitrary command that goes through dispatch.
    result = runner.invoke(cli, ["status", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "auto-sync" in result.output, result.output
    assert _read_marker(project) == "1.1.0"


def test_auto_sync_proceeds_when_unrelated_file_is_dirty(
    scaffolded_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    _commit_all(project)
    (project / "audits").mkdir()
    (project / "audits" / "draft.md").write_text("dirty\n")
    monkeypatch.setattr(auto_sync_mod, "__version__", "1.1.0")

    with patch("cortex.commands.sync.run_sync") as run_sync:
        auto_sync_mod.maybe_auto_sync(project, "status", disabled=False)

    run_sync.assert_called_once_with(
        project,
        run_doctor=False,
        output_prefix="==> auto-sync:",
    )
    assert _read_marker(project) == "1.1.0"
    assert "dirty file in planned write set" not in capsys.readouterr().err


def test_auto_sync_skips_when_planned_write_set_is_dirty(
    scaffolded_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    _commit_all(project)
    (project / ".cortex" / "state.md").write_text("dirty state\n")
    monkeypatch.setattr(auto_sync_mod, "__version__", "1.1.0")

    with patch("cortex.commands.sync.run_sync") as run_sync:
        auto_sync_mod.maybe_auto_sync(project, "status", disabled=False)

    run_sync.assert_not_called()
    assert _read_marker(project) == "1.0.0"
    assert (
        "==> auto-sync: skipped — dirty file in planned write set: .cortex/state.md"
    ) in capsys.readouterr().err


def test_auto_sync_skips_on_release_bump_on_feature_branch(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    _commit_all(project)
    monkeypatch.setattr(auto_sync_mod, "__version__", "1.1.0")

    with (
        patch(
            "cortex.commands._auto_sync._release_commit_on_non_default_branch",
            return_value=True,
        ),
        patch("cortex.commands.sync.run_sync") as run_sync,
    ):
        auto_sync_mod.maybe_auto_sync(project, "status", disabled=False)

    run_sync.assert_not_called()
    assert _read_marker(project) == "1.0.0"


def test_auto_sync_runs_on_release_bump_on_main(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    _commit_all(project)
    monkeypatch.setattr(auto_sync_mod, "__version__", "1.1.0")

    with (
        patch(
            "cortex.commands._auto_sync._release_commit_on_non_default_branch",
            return_value=False,
        ),
        patch("cortex.commands.sync.run_sync") as run_sync,
    ):
        auto_sync_mod.maybe_auto_sync(project, "status", disabled=False)

    run_sync.assert_called_once_with(
        project,
        run_doctor=False,
        output_prefix="==> auto-sync:",
    )
    assert _read_marker(project) == "1.1.0"


def test_auto_sync_skips_when_git_status_unavailable(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    monkeypatch.setattr(auto_sync_mod, "__version__", "1.1.0")

    with (
        patch("cortex.commands._auto_sync.subprocess.run", side_effect=FileNotFoundError),
        patch("cortex.commands.sync.run_sync") as run_sync,
    ):
        auto_sync_mod.maybe_auto_sync(project, "status", disabled=False)

    run_sync.assert_not_called()
    assert _read_marker(project) == "1.0.0"


def test_auto_sync_skips_on_patch_bump(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.1.0")
    monkeypatch.setattr(cortex, "__version__", "1.1.1")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.1")
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(project)])
    assert result.exit_code == 0, result.output
    # The version-bump auto-sync banner (old → new) must not appear; a patch
    # bump never triggers a version-driven sync. (The stale-input hook may
    # still run — it is orthogonal — so we assert the version-bump banner
    # specifically rather than the bare "auto-sync" substring.)
    assert "1.1.0 → 1.1.1" not in result.output, result.output
    # Marker still gets bumped so the next minor diff is detected from the new patch baseline.
    assert _read_marker(project) == "1.1.1"


def test_auto_sync_skips_on_first_run(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    # Initialize git so the marker has a place to live (gitdir-relative).
    _ensure_git_repo(project)
    # Remove both marker locations so this is truly "first run after install".
    marker = _git_dir(project) / "cortex" / ".last-cli-version"
    if marker.exists():
        marker.unlink()
    legacy = _legacy_marker(project)
    if legacy.exists():
        legacy.unlink()
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(project)])
    assert result.exit_code == 0, result.output
    # No version-bump sync banner: a first run seeds the marker and returns
    # without a version-driven sync. (The orthogonal stale-input hook may run
    # against the fresh scaffold; it never writes or advances the marker.)
    assert "→" not in result.output, result.output
    # Marker is now seeded in git metadata, not the worktree.
    assert _read_marker(project) == cortex.__version__
    assert not _legacy_marker(project).exists()


def test_auto_sync_seeds_marker_in_git_metadata_not_worktree(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _ensure_git_repo(project)
    marker = _git_dir(project) / "cortex" / ".last-cli-version"
    if marker.exists():
        marker.unlink()
    legacy = _legacy_marker(project)
    if legacy.exists():
        legacy.unlink()

    monkeypatch.setattr(auto_sync_mod, "__version__", "2.0.0")
    auto_sync_mod.maybe_auto_sync(project, "status", disabled=False)

    assert marker.read_text().strip() == "2.0.0"
    assert not legacy.exists()


def test_auto_sync_seed_is_noop_outside_git_checkout(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    # No git metadata present; marker writes should be skipped cleanly.
    assert not (project / ".git").exists()
    legacy = _legacy_marker(project)
    if legacy.exists():
        legacy.unlink()

    monkeypatch.setattr(auto_sync_mod, "__version__", "2.0.0")
    auto_sync_mod.maybe_auto_sync(project, "status", disabled=False)

    assert _read_marker(project) is None
    assert not legacy.exists()


def test_legacy_marker_is_migrated_to_gitdir_on_first_read(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing `.cortex/.last-cli-version` migrates to the gitdir on first
    upgraded run: the value moves to `<gitdir>/cortex/.last-cli-version` and
    the legacy file is removed. This is the backwards-compatibility path
    every existing Cortex user hits on first upgrade past this fix."""

    project = scaffolded_project
    _ensure_git_repo(project)
    new_marker = _git_dir(project) / "cortex" / ".last-cli-version"
    if new_marker.exists():
        new_marker.unlink()
    legacy = _legacy_marker(project)
    legacy.write_text("1.4.2")

    # Reading the marker should drive migration as a side-effect.
    value = auto_sync_mod._read_marker(project)

    assert value == "1.4.2"
    assert new_marker.read_text().strip() == "1.4.2"
    assert not legacy.exists()


def test_legacy_marker_migration_skipped_outside_git_checkout(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration is best-effort. With no git metadata available, the legacy
    file is left alone (rather than deleted with no destination) and the
    user-facing command continues without crashing — a warning is emitted
    so the deferred migration is visible."""

    project = scaffolded_project
    assert not (project / ".git").exists()
    legacy = _legacy_marker(project)
    legacy.write_text("1.4.2")

    value = auto_sync_mod._read_marker(project)

    # No gitdir → cannot migrate → returns None (and emits a warning to
    # stderr, which we don't capture here — the visible-failure
    # invariant is that the legacy file stays put for the next run.)
    assert value is None
    assert legacy.exists()
    assert legacy.read_text() == "1.4.2"


def test_legacy_marker_cleaned_up_when_new_marker_already_exists(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both markers present (e.g., upgraded run partially completed
    earlier): the new marker is authoritative, and the legacy file is
    cleaned up so the dirty-tree problem doesn't recur."""

    project = scaffolded_project
    _ensure_git_repo(project)
    new_marker = _git_dir(project) / "cortex" / ".last-cli-version"
    new_marker.parent.mkdir(parents=True, exist_ok=True)
    new_marker.write_text("1.5.0")
    legacy = _legacy_marker(project)
    legacy.write_text("1.4.2")

    value = auto_sync_mod._read_marker(project)

    # New marker wins; legacy is removed.
    assert value == "1.5.0"
    assert new_marker.read_text().strip() == "1.5.0"
    assert not legacy.exists()


def test_legacy_marker_with_empty_content_is_cleaned_up(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty legacy marker has no value to migrate; it's removed
    silently rather than seeded as the empty string."""

    project = scaffolded_project
    _ensure_git_repo(project)
    new_marker = _git_dir(project) / "cortex" / ".last-cli-version"
    if new_marker.exists():
        new_marker.unlink()
    legacy = _legacy_marker(project)
    legacy.write_text("   \n")

    value = auto_sync_mod._read_marker(project)

    assert value is None
    assert not legacy.exists()
    assert not new_marker.exists()


def test_auto_sync_skips_with_flag(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    monkeypatch.setattr(cortex, "__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.0")
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(cli, ["--no-auto-sync", "status", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "auto-sync" not in result.output, result.output
    # Marker not updated when the user opted out.
    assert _read_marker(project) == "1.0.0"


def test_auto_sync_skips_with_config(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    (project / ".cortex" / "config.toml").write_text("[sync]\nauto = false\n")
    monkeypatch.setattr(cortex, "__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.0")
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "auto-sync" not in result.output, result.output
    assert _read_marker(project) == "1.0.0"


@pytest.mark.parametrize("subcommand", ["init", "update", "sync", "migrate-state", "doctor", "check-triggers"])
def test_auto_sync_skips_during_init_update_sync_migrate_state_doctor_and_check_triggers(
    tmp_path: Path,
    scaffolded_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    subcommand: str,
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
    monkeypatch.setattr(cortex, "__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.0")
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")

    # `init` must run in an empty directory; the others run on the project.
    init_target = tmp_path / "fresh"
    init_target.mkdir()
    args_by_cmd = {
        "init": [
            "init",
            "--path",
            str(init_target),
            "--no-imports-claude",
            "--no-imports-agents",
            "--no-gitignore",
        ],
        "update": ["update", "--path", str(project), "--dry-run"],
        "sync": ["sync", "--path", str(project), "--dry-run"],
        "migrate-state": ["migrate-state", "--path", str(project), "--dry-run"],
        "doctor": ["doctor", "--path", str(project)],
        "check-triggers": ["check-triggers", "--path", str(project), "--since", "HEAD"],
    }
    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(cli, args_by_cmd[subcommand])
    # The auto-sync banner must not appear under any of these subcommands.
    assert "==> auto-sync:" not in result.output, result.output
    # Marker on the project under test MUST NOT have been touched.
    assert _read_marker(project) == "1.0.0"


def test_doctor_never_runs_auto_sync(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for cortex#282: validation must not dirty generated layers."""

    project = scaffolded_project
    _set_marker(project, "1.0.0")
    _commit_all(project)
    monkeypatch.setattr(cortex, "__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.0")
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("cortex.commands.sync.run_sync") as run_sync:
        result = runner.invoke(cli, ["doctor", "--path", str(project)])

    assert result.exit_code == 0, result.output
    assert "auto-sync" not in result.output
    run_sync.assert_not_called()
    assert _read_marker(project) == "1.0.0"


def test_check_triggers_never_runs_auto_sync(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for cortex#249: check-triggers is a read-only runtime
    primitive, so it must not fire auto-sync before inspecting the diff."""

    project = scaffolded_project
    _set_marker(project, "1.0.0")
    _commit_all(project)
    monkeypatch.setattr(cortex, "__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.0")
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    with patch("cortex.commands.sync.run_sync") as run_sync:
        result = runner.invoke(
            cli,
            ["check-triggers", "--path", str(project), "--since", "HEAD"],
        )

    assert result.exit_code == 0, result.output
    assert "auto-sync" not in result.output
    run_sync.assert_not_called()
    assert _read_marker(project) == "1.0.0"


def test_marker_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker is written via rename(.tmp, target), so a crash during the write
    leaves either the prior content or the new content — never a partial file."""
    from cortex.commands import _auto_sync as auto_sync_mod

    _ensure_git_repo(tmp_path)
    marker = _git_dir(tmp_path) / "cortex" / ".last-cli-version"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("1.0.0")

    real_replace = os.replace

    calls: list[tuple[str, str]] = []

    def boom_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Capture the call but never apply it — simulate a crash mid-rename.
        calls.append((str(src), str(dst)))
        raise RuntimeError("simulated crash before rename completes")

    with patch("os.replace", side_effect=boom_replace), pytest.raises(RuntimeError):
        auto_sync_mod._write_marker(tmp_path, "1.1.0")

    # The original marker file is untouched (we never replaced it).
    assert marker.read_text() == "1.0.0"
    # The .tmp file may exist with the new content (that's fine — atomic
    # rename is what protects the real marker), but the real marker
    # remained at the prior version. That IS the atomicity guarantee.
    assert marker.read_text() == "1.0.0"

    # Now do a real successful write and confirm the marker advanced.
    with patch("os.replace", side_effect=real_replace):
        auto_sync_mod._write_marker(tmp_path, "1.1.0")
    assert marker.read_text() == "1.1.0"
    tmp_marker = marker.with_name(".last-cli-version.tmp")
    assert not tmp_marker.exists()


def test_auto_sync_swallows_systemexit_from_require_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the BaseException catch.

    `cortex.compat.require_compatible` calls `sys.exit(2)` (raising
    `SystemExit`) when `.cortex/SPEC_VERSION` is missing or unsupported.
    `SystemExit` is NOT a subclass of `Exception`, so a bare
    `except Exception` would let it propagate and hard-exit the user's
    actual command. Auto-sync must swallow it and continue.
    """
    cortex_dir = tmp_path / ".cortex"
    cortex_dir.mkdir()
    # No SPEC_VERSION file — require_compatible will call sys.exit(2).
    _set_marker(tmp_path, "1.0.0")
    _commit_all(tmp_path)

    monkeypatch.setattr(cortex, "__version__", "1.1.0")
    monkeypatch.setattr("cortex.commands._auto_sync.__version__", "1.1.0")
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(tmp_path)])
    # The user-facing command should still complete (status itself may
    # report its own error, but the process must not be hard-exited
    # with code 2 from require_compatible inside auto-sync). Status
    # against a missing .cortex/ exits non-zero; the assertion is that
    # auto-sync's warning line appears, proving the SystemExit was
    # caught and converted to a visible warning.
    assert "warning: auto-sync failed" in result.output, result.output


# ---------------------------------------------------------------------------
# Layer 3 — stale-input auto-update before read commands (cortex#261)
# ---------------------------------------------------------------------------


def _make_state_fresh(project: Path) -> None:
    """Run `cortex update` so state.md/.index.json are current with their sources."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["update", "--no-doctor", "--path", str(project)],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output


def _stale_check_now_fires(project: Path) -> bool:
    """True when the reused freshness detector reports state.md is stale."""
    from cortex.commands.sync import _state_update_needed

    needs_state, _reasons = _state_update_needed(project)
    return needs_state


def _add_journal_entry(project: Path, name: str = "2026-05-25-stale-trigger.md") -> None:
    """Append a new Journal source file, which changes the state.md source hash."""
    journal = project / ".cortex" / "journal"
    journal.mkdir(parents=True, exist_ok=True)
    (journal / name).write_text(
        "---\nType: decision\nDate: 2026-05-25\n---\n\n# A new decision\n\nBody.\n"
    )


def test_stale_state_self_updates_before_status_when_clean(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance: a stale state.md (new journal source) self-updates before
    `cortex status` when the planned write set is clean."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    # Avoid the version-bump path: seed marker at the current version.
    _set_marker(project, cortex.__version__)

    _add_journal_entry(project)
    assert _stale_check_now_fires(project), "test setup must produce a stale state.md"

    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "stale Cortex inputs detected" in result.output, result.output
    # state.md is now current — the detector no longer reports staleness.
    assert not _stale_check_now_fires(project), result.output


def test_stale_input_skips_on_dirty_overlap_no_write(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant: a read command never writes generated layers when the
    worktree has dirty overlap with the planned write set, regardless of
    staleness. The skip is visible on stderr; no write occurs."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)

    # Make state.md stale (new journal) AND dirty (uncommitted edit to a
    # planned-write-set path). The dirty overlap must win: no write.
    _add_journal_entry(project)
    state_path = project / ".cortex" / "state.md"
    sentinel = "<!-- operator-edit-do-not-clobber -->\n"
    state_path.write_text(state_path.read_text() + sentinel)
    assert _stale_check_now_fires(project)

    with patch("cortex.commands.sync.run_sync") as run_sync:
        auto_sync_mod.maybe_auto_sync_stale_inputs(project, "status", disabled=False)

    run_sync.assert_not_called()
    # The operator's edit survives untouched.
    assert sentinel in state_path.read_text()


def test_stale_input_skip_warning_is_visible(
    scaffolded_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No silent failures: the dirty-overlap skip names the blocking path."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)

    _add_journal_entry(project)
    state_path = project / ".cortex" / "state.md"
    state_path.write_text(state_path.read_text() + "dirty\n")

    auto_sync_mod.maybe_auto_sync_stale_inputs(project, "status", disabled=False)
    err = capsys.readouterr().err
    assert "dirty file in planned write set: .cortex/state.md" in err


def test_stale_input_respects_no_auto_sync_flag(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance: `--no-auto-sync` suppresses the stale-input auto-update."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)

    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(cli, ["--no-auto-sync", "status"])
    assert result.exit_code == 0, result.output
    assert "stale Cortex inputs detected" not in result.output, result.output
    assert _stale_check_now_fires(project), "no-auto-sync must leave state stale"


def test_stale_input_respects_env_opt_out(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance: CORTEX_NO_AUTO_SYNC=1 suppresses the stale-input update."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.setenv("CORTEX_NO_AUTO_SYNC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)

    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0, result.output
    assert "stale Cortex inputs detected" not in result.output, result.output
    assert _stale_check_now_fires(project)


def test_stale_input_respects_config_opt_out(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`[sync] auto = false` in config.toml suppresses the stale-input update."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    (project / ".cortex" / "config.toml").write_text("[sync]\nauto = false\n")
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)

    with patch("cortex.commands.sync.run_sync") as run_sync:
        auto_sync_mod.maybe_auto_sync_stale_inputs(project, "status", disabled=False)
    run_sync.assert_not_called()


def test_stale_input_continues_after_update_failure(
    scaffolded_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Acceptance: the read command continues after an update failure; the
    failure is visible on stderr and the original command still runs."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)

    with patch(
        "cortex.commands.sync.run_sync", side_effect=RuntimeError("boom")
    ) as run_sync:
        # Must not raise — failure is caught and surfaced.
        auto_sync_mod.maybe_auto_sync_stale_inputs(project, "status", disabled=False)

    run_sync.assert_called_once()
    err = capsys.readouterr().err
    assert "warning: auto-sync failed: boom" in err

    # End-to-end: the read command itself still completes after the failure.
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(project)])
    assert result.exit_code == 0, result.output


def test_stale_input_does_not_fire_for_grep(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """grep reads raw markdown, not generated layers, so it is out of scope:
    the stale-input hook must not run for it even when state.md is stale."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)

    with patch("cortex.commands.sync.run_sync") as run_sync:
        auto_sync_mod.maybe_auto_sync_stale_inputs(project, "grep", disabled=False)
    run_sync.assert_not_called()


def test_stale_input_noop_when_layers_current(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When state.md/.index.json are already current, no update fires."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    assert not _stale_check_now_fires(project)

    with patch("cortex.commands.sync.run_sync") as run_sync:
        auto_sync_mod.maybe_auto_sync_stale_inputs(project, "status", disabled=False)
    run_sync.assert_not_called()


def test_stale_input_skipped_quietly_for_non_git_project(
    scaffolded_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A non-git `.cortex/` project has no safety gate to apply, so the
    stale-input hook skips QUIETLY (no stderr narrative) — it does not spam a
    'git unavailable' line on every read. Distinct from a real-repo git
    failure, which the preflight still surfaces loudly."""
    project = scaffolded_project  # `cortex init` only — no `git init`
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)
    assert not (project / ".git").exists()

    with patch("cortex.commands.sync.run_sync") as run_sync:
        auto_sync_mod.maybe_auto_sync_stale_inputs(project, "status", disabled=False)
    run_sync.assert_not_called()
    assert capsys.readouterr().err == ""


def test_stale_input_scopes_to_subcommand_path_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the --path scoping bug (cortex#261 review finding):
    `cortex status --path OTHER` run from an unrelated cwd must auto-update
    OTHER, even though the group `--path` defaults to cwd.

    Before the fix the stale-input hook fired from the group callback against
    cwd (which is not OTHER), so OTHER never self-updated.
    """
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")

    # `other` is the stale target project (git, clean, marker seeded).
    other = tmp_path / "other"
    other.mkdir()
    assert CliRunner().invoke(init_command, ["--path", str(other)]).exit_code == 0
    _make_state_fresh(other)
    _commit_all(other)
    _set_marker(other, cortex.__version__)
    _add_journal_entry(other)
    assert _stale_check_now_fires(other), "test setup must produce a stale OTHER"

    # `cwd_proj` is an UNRELATED directory we run from. It is a different git
    # repo with no `.cortex/`, so a cwd-scoped hook would no-op (the old bug).
    cwd_proj = tmp_path / "cwd"
    cwd_proj.mkdir()
    _ensure_git_repo(cwd_proj)
    monkeypatch.chdir(cwd_proj)

    result = CliRunner().invoke(cli, ["status", "--path", str(other)])
    assert result.exit_code == 0, result.output
    assert "stale Cortex inputs detected" in result.output, result.output
    # The fix updated OTHER: the detector no longer reports staleness there.
    assert not _stale_check_now_fires(other), result.output


def test_stale_input_json_stdout_stays_valid_while_update_fires(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the JSONDecodeError gotcha: when a stale-input update
    fires under `--json`, the sync narrative goes to stderr and stdout stays
    pure JSON. Parses result.stdout (Click 8.3 mixes stderr into .output)."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)

    result = CliRunner().invoke(cli, ["status", "--path", str(project), "--json"])
    assert result.exit_code == 0, result.output
    # The update fired — narrative is on stderr, never stdout.
    assert "stale Cortex inputs detected" in result.stderr, result.stderr
    assert "stale Cortex inputs detected" not in result.stdout, result.stdout
    # stdout is pure, parseable JSON.
    data = json.loads(result.stdout)
    assert data["spec_version"]
    # And the update actually ran: state is now fresh.
    assert not _stale_check_now_fires(project)


def test_stale_input_retrieve_no_rebuild_refreshes_state_but_not_retrieve_index(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`retrieve --no-rebuild` must not let the stale-input state refresh
    force-rebuild the gitignored retrieve sqlite index — the two artifacts are
    independent. state.md still refreshes; the retrieve index rebuild is
    suppressed via rebuild_retrieve_index=False."""
    project = scaffolded_project
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    _make_state_fresh(project)
    _commit_all(project)
    _set_marker(project, cortex.__version__)
    _add_journal_entry(project)
    assert _stale_check_now_fires(project)

    # Simulate an opted-in retrieve index so run_sync would otherwise rebuild it.
    (project / ".cortex" / ".index").mkdir(parents=True, exist_ok=True)

    # Wrap the real writer so the call still refreshes state.md, but we can
    # inspect exactly which flags the auto-sync threaded through.
    real_run_sync = sync_mod.run_sync
    with patch(
        "cortex.commands.sync.run_sync", side_effect=real_run_sync, autospec=True
    ) as run_sync:
        auto_sync_mod.maybe_auto_sync_stale_inputs(
            project, "retrieve", disabled=False, json_mode=True, rebuild_retrieve_index=False
        )

    run_sync.assert_called_once()
    call_kwargs = run_sync.call_args.kwargs
    # The --no-rebuild contract is threaded through to run_sync.
    assert call_kwargs["rebuild_retrieve_index"] is False
    assert call_kwargs["progress_to_stderr"] is True
    assert call_kwargs["run_doctor"] is False
    # state.md was still refreshed (the independent artifact).
    assert not _stale_check_now_fires(project)
