"""Tests for cortex#162 — pre-existing scaffold analysis output.

When `cortex init` runs on a project that already has doctrine, plan, or
journal content in `.cortex/`, it emits a one-line summary telling the
user how many pre-existing files were found and how many doctor errors /
warnings those files already have.  This keeps "did the install work?"
separate from "is your pre-existing content SPEC-conformant?"

On a truly fresh install (no `.cortex/` at all), the summary is suppressed.
"""

from __future__ import annotations

import re
from pathlib import Path

from click.testing import CliRunner, Result

from cortex.cli import cli
from cortex.goal_hash import normalize_goal_hash

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_init(target: Path, *extra_args: str) -> Result:
    return CliRunner().invoke(cli, ["init", "--path", str(target), *extra_args])


def _valid_plan(title: str) -> str:
    """Return a SPEC-conformant plan file body for the given title."""
    goal_hash = normalize_goal_hash(title)
    return f"""\
---
Status: active
Written: 2026-05-01
Author: Test
Goal-hash: {goal_hash}
Updated-by:
  - "2026-05-01 Test"
Cites: state.md
---

# {title}

## Why (grounding)

See state.md for context.

## Approach

Implement the feature.

## Success Criteria

All tests pass (`uv run pytest -x`).

## Work items

- [ ] Implement feature
"""


# ---------------------------------------------------------------------------
# Test 1 — fresh install: no pre-existing summary
# ---------------------------------------------------------------------------


def test_fresh_install_does_not_emit_pre_existing_summary(tmp_path: Path) -> None:
    """A truly fresh install (no .cortex/) must NOT show the pre-existing summary."""
    result = _run_init(tmp_path, "--yes")
    assert result.exit_code == 0, result.output
    assert "pre-existing" not in result.output.lower(), (
        "Fresh install should not mention pre-existing content.\n"
        f"Output:\n{result.output}"
    )


# ---------------------------------------------------------------------------
# Test 2 — v0.3-shape scaffold with a stale plan: summary with errors
# ---------------------------------------------------------------------------


def test_pre_existing_v03_shape_stale_plan_shows_summary(tmp_path: Path) -> None:
    """A v0.3-shape scaffold with an invalid plan triggers the pre-existing
    summary and reports a non-zero doctor error count so the user knows the
    errors are pre-existing, not install-induced.
    """
    # Seed a partial v0.3-style scaffold.
    cortex = tmp_path / ".cortex"
    cortex.mkdir()
    (cortex / "SPEC_VERSION").write_text("0.3.1-dev\n")

    plans_dir = cortex / "plans"
    plans_dir.mkdir()
    # A plan with no frontmatter — doctor will report errors.
    (plans_dir / "old-plan.md").write_text(
        "# Old Plan\n\n## Why (grounding)\n\nSome context.\n"
    )

    result = _run_init(tmp_path, "--force", "--yes")
    assert result.exit_code == 0, result.output

    output = result.output
    assert "pre-existing" in output.lower(), (
        "Output must mention pre-existing content.\n"
        f"Output:\n{output}"
    )
    # Must mention at least 1 plan found.
    assert "1 pre-existing plan" in output, (
        "Output must report the pre-existing plan count.\n"
        f"Output:\n{output}"
    )
    # Must include a doctor error count reference (non-zero errors from the stale plan).
    assert "cortex doctor` reports" in output, (
        "Output must include a doctor reports clause.\n"
        f"Output:\n{output}"
    )
    # The stale plan (missing frontmatter) generates errors — the count must be > 0.
    # We accept any "N error" pattern where N > 0.
    match = re.search(r"(\d+) error", output)
    assert match is not None, f"Expected error count in output.\nOutput:\n{output}"
    assert int(match.group(1)) > 0, (
        f"Expected > 0 errors from the stale plan, got {match.group(1)}.\n"
        f"Output:\n{output}"
    )
    # Must explain these errors are not install-induced.
    assert "not introduced by this install" in output, (
        "Output must clarify errors are pre-existing.\n"
        f"Output:\n{output}"
    )


# ---------------------------------------------------------------------------
# Test 3 — v0.5-shape clean scaffold: summary with 0 errors
# ---------------------------------------------------------------------------


def test_pre_existing_v05_shape_clean_plan_shows_summary_zero_errors(tmp_path: Path) -> None:
    """A SPEC-conformant pre-existing plan shows the pre-existing summary
    but reports 0 errors so the user knows the install didn't introduce any.
    """
    cortex = tmp_path / ".cortex"
    cortex.mkdir()
    (cortex / "SPEC_VERSION").write_text("0.5.0\n")

    plans_dir = cortex / "plans"
    plans_dir.mkdir()
    (plans_dir / "clean-plan.md").write_text(_valid_plan("My Clean Test Plan"))

    result = _run_init(tmp_path, "--force", "--yes")
    assert result.exit_code == 0, result.output

    output = result.output
    assert "pre-existing" in output.lower(), (
        "Output must mention pre-existing content.\n"
        f"Output:\n{output}"
    )
    assert "1 pre-existing plan" in output, (
        "Output must report the pre-existing plan count.\n"
        f"Output:\n{output}"
    )
    assert "0 errors" in output, (
        "A conformant plan should report 0 errors.\n"
        f"Output:\n{output}"
    )
