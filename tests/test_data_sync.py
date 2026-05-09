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


def test_bundled_protocol_guidance_prefers_manifest_then_fallback(repo_root: Path) -> None:
    shipped = (repo_root / "src" / "cortex" / "_data" / "protocol.md").read_text()

    manifest_idx = shipped.index("cortex manifest --budget <N>")
    delegation_idx = shipped.index("cortex manifest --profile delegation")
    fallback_idx = shipped.index("Fallback when the CLI is unavailable")
    protocol_import_idx = shipped.index("@.cortex/protocol.md", fallback_idx)
    state_import_idx = shipped.index("@.cortex/state.md", fallback_idx)

    assert manifest_idx < fallback_idx
    assert delegation_idx < fallback_idx
    assert fallback_idx < protocol_import_idx < state_import_idx
    assert "Direct `@path` imports are the fallback path" in shipped
    assert "currently 4k tokens" in shipped


def test_bundled_protocol_guidance_is_lookup_first(repo_root: Path) -> None:
    shipped = (repo_root / "src" / "cortex" / "_data" / "protocol.md").read_text()
    normalized = " ".join(shipped.split())

    hot_policy_idx = shipped.index("Default hot/cold policy")
    manifest_idx = shipped.index("Use the bounded manifest and hot files", hot_policy_idx)
    grep_idx = shipped.index("Use `cortex grep`", hot_policy_idx)
    retrieve_idx = shipped.index("Use `cortex retrieve --mode bm25|semantic|hybrid`", hot_policy_idx)
    open_idx = shipped.index("Open only the files or snippets", hot_policy_idx)

    assert hot_policy_idx < manifest_idx < grep_idx < retrieve_idx < open_idx
    assert "Agents MUST NOT bulk-read `.cortex/journal/**`" in normalized
    assert "Grep MUST work for every Cortex project" in shipped
    assert "Retrieve MAY work" in shipped


def test_templates_tree_matches_canonical(repo_root: Path) -> None:
    canonical = _collect_tree(repo_root / ".cortex" / "templates")
    shipped = _collect_tree(repo_root / "src" / "cortex" / "_data" / "templates")
    assert canonical == shipped, (
        "src/cortex/_data/templates/ is out of sync with .cortex/templates/. "
        "Run: rm -rf src/cortex/_data/templates && cp -R .cortex/templates src/cortex/_data/templates"
    )
