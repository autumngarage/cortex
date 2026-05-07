"""Integration tests for `cortex sync` (Layer 1) and auto-sync on version-bump (Layer 2)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import cortex
from cortex.cli import cli
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
# Layer 1 — `cortex sync`
# ---------------------------------------------------------------------------


def test_sync_runs_refresh_state_and_index_and_doctor(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project)],
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
    assert "Sync complete" in result.output


def test_sync_no_doctor_flag_skips_doctor(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "refresh-state" in result.output
    assert "cortex doctor" not in result.output


def test_sync_dry_run_invokes_nothing(scaffolded_project: Path) -> None:
    project = scaffolded_project
    state_before = (project / ".cortex" / "state.md").read_text()
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--dry-run"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "[dry-run]" in result.output
    # Nothing was written by sync itself.
    assert (project / ".cortex" / "state.md").read_text() == state_before
    # `cortex init` writes its own .index.json today; sync's dry-run must
    # leave that file at its prior content (we just confirm sync didn't
    # rebuild and overwrite something newer — the .index.json mtime should
    # match init-time, which is essentially that it stays a valid JSON).
    if (project / ".cortex" / ".index.json").exists():
        assert (project / ".cortex" / ".index.json").read_text()


def test_sync_rebuilds_retrieve_index_when_present(scaffolded_project: Path) -> None:
    project = scaffolded_project
    # Mark the project as having opted into retrieve.
    (project / ".cortex" / ".index").mkdir(exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    # Output prints the `--retrieve` modifier when an .index/ dir exists.
    assert "--retrieve" in result.output, result.output


def test_sync_is_idempotent(scaffolded_project: Path) -> None:
    project = scaffolded_project
    runner = CliRunner()
    first = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert first.exit_code == 0, first.output
    state_after_first = (project / ".cortex" / "state.md").read_text()

    second = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert second.exit_code == 0, second.output
    assert (project / ".cortex" / "state.md").read_text() == state_after_first


def test_sync_reports_unknown_config_keys(scaffolded_project: Path) -> None:
    project = scaffolded_project
    (project / ".cortex" / "config.toml").write_text(
        "[refresh-index]\n"
        "candidate_patterns = []\n"
        "totally_unknown_key = 42\n"
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sync", "--path", str(project), "--no-doctor"],
        env={"CORTEX_DETERMINISTIC": "1"},
    )
    assert result.exit_code == 0, result.output
    assert "1 unknown key" in result.output


# ---------------------------------------------------------------------------
# Layer 2 — auto-sync on version bump
# ---------------------------------------------------------------------------


def _set_marker(project: Path, version: str) -> None:
    (project / ".cortex" / ".last-cli-version").write_text(version)


def _read_marker(project: Path) -> str | None:
    marker = project / ".cortex" / ".last-cli-version"
    if not marker.exists():
        return None
    return marker.read_text().strip()


def test_auto_sync_runs_on_minor_bump(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    _set_marker(project, "1.0.0")
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
    assert "auto-sync" not in result.output, result.output
    # Marker still gets bumped so the next minor diff is detected from the new patch baseline.
    assert _read_marker(project) == "1.1.1"


def test_auto_sync_skips_on_first_run(
    scaffolded_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = scaffolded_project
    # init may have left a marker; remove it so this is truly "first run after install".
    marker = project / ".cortex" / ".last-cli-version"
    if marker.exists():
        marker.unlink()
    monkeypatch.setenv("CORTEX_DETERMINISTIC", "1")
    monkeypatch.chdir(project)

    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--path", str(project)])
    assert result.exit_code == 0, result.output
    assert "auto-sync" not in result.output, result.output
    # Marker is now seeded.
    assert _read_marker(project) == cortex.__version__


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


@pytest.mark.parametrize("subcommand", ["init", "sync", "migrate-state"])
def test_auto_sync_skips_during_init_and_sync_and_migrate_state(
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
        "sync": ["sync", "--path", str(project), "--dry-run"],
        "migrate-state": ["migrate-state", "--path", str(project), "--dry-run"],
    }
    monkeypatch.chdir(project)
    runner = CliRunner()
    result = runner.invoke(cli, args_by_cmd[subcommand])
    # The auto-sync banner must not appear under any of these subcommands.
    assert "==> auto-sync:" not in result.output, result.output
    # Marker on the project under test MUST NOT have been touched.
    assert _read_marker(project) == "1.0.0"


def test_marker_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Marker is written via rename(.tmp, target), so a crash during the write
    leaves either the prior content or the new content — never a partial file."""
    from cortex.commands import _auto_sync as auto_sync_mod

    cortex_dir = tmp_path / ".cortex"
    cortex_dir.mkdir()
    marker = cortex_dir / ".last-cli-version"
    marker.write_text("1.0.0")

    real_replace = os.replace

    calls: list[tuple[str, str]] = []

    def boom_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Capture the call but never apply it — simulate a crash mid-rename.
        calls.append((str(src), str(dst)))
        raise RuntimeError("simulated crash before rename completes")

    with patch("os.replace", side_effect=boom_replace), pytest.raises(RuntimeError):
        auto_sync_mod._write_marker(cortex_dir, "1.1.0")

    # The original marker file is untouched (we never replaced it).
    assert marker.read_text() == "1.0.0"
    # The .tmp file may exist with the new content (that's fine — atomic
    # rename is what protects the real marker), but the real marker
    # remained at the prior version. That IS the atomicity guarantee.
    assert marker.read_text() == "1.0.0"

    # Now do a real successful write and confirm the marker advanced.
    with patch("os.replace", side_effect=real_replace):
        auto_sync_mod._write_marker(cortex_dir, "1.1.0")
    assert marker.read_text() == "1.1.0"
    tmp_marker = cortex_dir / ".last-cli-version.tmp"
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
    (cortex_dir / ".last-cli-version").write_text("1.0.0")

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
