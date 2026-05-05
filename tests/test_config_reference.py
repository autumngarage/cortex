"""Tests for the documented `.cortex/config.toml` reference."""

from __future__ import annotations

from pathlib import Path

DOCS_ROOT = Path(__file__).resolve().parents[1] / "docs"


def test_audit_instructions_siblings_are_documented_as_filesystem_paths() -> None:
    text = (DOCS_ROOT / "config-reference.md").read_text()

    assert '`siblings` | list of strings \\| null | `[]` | Local filesystem paths' in text
    assert 'siblings = ["~/repos/example-helper"]' in text
    assert 'siblings = ["autumngarage/example-helper"]' not in text
