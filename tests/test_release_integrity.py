"""Release metadata integrity checks."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cortex.release_integrity import (
    main,
    read_ref_metadata,
    release_integrity_errors,
)


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _write_project(repo: Path, version: str) -> None:
    (repo / "src" / "cortex").mkdir(parents=True, exist_ok=True)
    (repo / "pyproject.toml").write_text(
        "[project]\n"
        'name = "cortex"\n'
        f'version = "{version}"\n'
    )
    (repo / "src" / "cortex" / "__init__.py").write_text(
        '"""Package."""\n\n'
        f'__version__ = "{version}"\n'
    )


def _commit_all(repo: Path, message: str) -> None:
    _run(repo, "add", ".")
    _run(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        message,
    )


def test_release_integrity_detects_tag_metadata_mismatch(tmp_path: Path) -> None:
    _run(tmp_path, "init", "-b", "main")
    _write_project(tmp_path, "0.8.0")
    _commit_all(tmp_path, "initial")
    _run(tmp_path, "tag", "v0.8.1")

    metadata = read_ref_metadata(tmp_path, "v0.8.1")

    assert release_integrity_errors(metadata, "0.8.1") == [
        "v0.8.1: pyproject.toml version is 0.8.0, expected 0.8.1",
        "v0.8.1: src/cortex/__init__.py __version__ is 0.8.0, expected 0.8.1",
    ]


def test_release_integrity_accepts_matching_tag_metadata(tmp_path: Path) -> None:
    _run(tmp_path, "init", "-b", "main")
    _write_project(tmp_path, "0.8.2")
    _commit_all(tmp_path, "release")
    _run(tmp_path, "tag", "v0.8.2")

    metadata = read_ref_metadata(tmp_path, "v0.8.2")

    assert release_integrity_errors(metadata, "0.8.2") == []


def test_release_integrity_cli_reports_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, "init", "-b", "main")
    _write_project(tmp_path, "0.8.0")
    _commit_all(tmp_path, "initial")
    _run(tmp_path, "tag", "v0.8.1")

    exit_code = main(["--repo", str(tmp_path), "v0.8.1", "0.8.1"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "pyproject.toml version is 0.8.0, expected 0.8.1" in captured.err
