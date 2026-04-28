"""Regression tests for the SPEC_VERSION value written by `cortex init`."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import cortex
from cortex.cli import cli


def test_init_yes_writes_canonical_spec_version_literal(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli, ["init", "--yes", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output

    spec_version = (tmp_path / ".cortex" / "SPEC_VERSION").read_text().strip()
    assert spec_version == cortex.SPEC_VERSION_LITERAL
    if "-dev" not in cortex.__version__:
        assert not spec_version.endswith("-dev")
