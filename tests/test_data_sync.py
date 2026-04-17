"""Enforce that `src/cortex/_data/` stays in sync with the canonical `.cortex/`.

The `.cortex/protocol.md` and `.cortex/templates/` tree in the repo root are
the single source of truth. The copies under `src/cortex/_data/` are what
`cortex init` ships to downstream projects. A drift between the two means
downstream projects get a stale Protocol or missing template.

This test runs against the repo checkout (not an installed wheel); it locates
the repo root by walking up from this test file and skips gracefully if the
canonical `.cortex/` cannot be found (e.g. if the test is ever run from an
unpacked installation).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _repo_root() -> Path | None:
    """Return the repo root (contains both `src/cortex/_data/` and `.cortex/`), or None."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / ".cortex").is_dir() and (candidate / "src" / "cortex" / "_data").is_dir():
            return candidate
    return None


def _collect_tree(root: Path) -> dict[str, str]:
    """Return {relative_path: text_contents} for every .md file under root."""
    return {
        str(p.relative_to(root)): p.read_text()
        for p in sorted(root.rglob("*.md"))
    }


@pytest.fixture(scope="module")
def repo_root() -> Path:
    root = _repo_root()
    if root is None:
        pytest.skip("canonical .cortex/ not found — skipping sync test (likely running outside repo)")
    return root


def test_protocol_md_matches_canonical(repo_root: Path) -> None:
    canonical = (repo_root / ".cortex" / "protocol.md").read_text()
    shipped = (repo_root / "src" / "cortex" / "_data" / "protocol.md").read_text()
    assert canonical == shipped, (
        "src/cortex/_data/protocol.md is out of sync with .cortex/protocol.md. "
        "Run: cp .cortex/protocol.md src/cortex/_data/protocol.md"
    )


def test_templates_tree_matches_canonical(repo_root: Path) -> None:
    canonical = _collect_tree(repo_root / ".cortex" / "templates")
    shipped = _collect_tree(repo_root / "src" / "cortex" / "_data" / "templates")
    assert canonical == shipped, (
        "src/cortex/_data/templates/ is out of sync with .cortex/templates/. "
        "Run: rm -rf src/cortex/_data/templates && cp -R .cortex/templates src/cortex/_data/templates"
    )
