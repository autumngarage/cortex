"""Cache path helpers for the derived retrieve index."""

from __future__ import annotations

import os
from pathlib import Path

INDEX_RELATIVE_PATH = Path(".cortex") / ".index" / "chunks.sqlite"


def index_path(project_root: Path) -> Path:
    """Return the SQLite chunks index path for ``project_root``.

    By default the index lives under the project as `.cortex/.index/` so it
    is clearly derived from local markdown. `CORTEX_CACHE_DIR` is an explicit
    escape hatch for read-only worktrees or tool-managed cache placement.
    """

    override = os.environ.get("CORTEX_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve() / "chunks.sqlite"
    return project_root / INDEX_RELATIVE_PATH


def temp_index_path(path: Path) -> Path:
    """Return the temp path used for atomic index rebuilds."""

    return path.with_name(f"{path.name}.tmp")
