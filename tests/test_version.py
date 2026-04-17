"""Tests for `cortex version`.

Uses click's CliRunner against the real entrypoint — no mocks. Verifies the
version string and the declared supported spec/protocol version lists.
"""

from __future__ import annotations

from click.testing import CliRunner

from cortex import SUPPORTED_PROTOCOL_VERSIONS, SUPPORTED_SPEC_VERSIONS, __version__
from cortex.cli import cli


def test_version_prints_cli_version() -> None:
    result = CliRunner().invoke(cli, ["version"])
    assert result.exit_code == 0, result.output
    assert f"cortex {__version__}" in result.output


def test_version_prints_supported_spec_versions() -> None:
    result = CliRunner().invoke(cli, ["version"])
    assert result.exit_code == 0
    for v in SUPPORTED_SPEC_VERSIONS:
        assert v in result.output


def test_version_prints_supported_protocol_versions() -> None:
    result = CliRunner().invoke(cli, ["version"])
    assert result.exit_code == 0
    for v in SUPPORTED_PROTOCOL_VERSIONS:
        assert v in result.output


def test_version_prints_install_method() -> None:
    result = CliRunner().invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "install method:" in result.output


def test_invoking_cortex_without_subcommand_prints_help() -> None:
    result = CliRunner().invoke(cli, [])
    assert result.exit_code == 0
    assert "Usage:" in result.output
    assert "version" in result.output
