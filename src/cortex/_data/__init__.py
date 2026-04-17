"""Package-data resources shipped with the Cortex CLI.

Holds a copy of the canonical `.cortex/protocol.md` and `.cortex/templates/`
tree so `cortex init` can scaffold a SPEC-v0.3.1-dev-conformant project
without depending on a specific source layout at install time.

The repo's `.cortex/protocol.md` is the single source of truth; this
directory is kept in sync by a test (see `tests/test_data_sync.py`).
"""
