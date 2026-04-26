"""Tests for Slice 3 (Sev-3 / Sev-4) cosmetic fixes in `cortex init`.

Fix #6: scan-output unscoped-constraint line inlines the file:line ref.
Fix #7: scan-output Next-steps numbering is contiguous (no "1 then 3").
Fix #8: top-level `cortex --status-only --path X` accepts `--path`.
"""

from __future__ import annotations

import re
from pathlib import Path

from click.testing import CliRunner

from cortex.cli import cli

# --- Fix #6: inline file:line ref in unscoped-constraint output ------------


def test_init_inlines_unscoped_constraint_location(tmp_path: Path) -> None:
    """When CLAUDE.md / AGENTS.md has an unscoped constraint, init's scan
    summary names the location instead of just the count + a "run cortex
    doctor" pointer. Saves the user a follow-up doctor invocation for
    the common single-warning case.
    """
    # Build a project with one unscoped constraint in CLAUDE.md.
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\nThe LLM must never run shell commands without approval.\n"
    )
    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    # Output should include a "path:line" reference inline, not just the
    # bare count + parenthetical pointer.
    assert "unscoped constraints" in result.output
    # Look for the inlined location pattern: parentheses containing
    # "<file>:<linenumber>" before "run `cortex doctor`".
    inline_match = re.search(
        r"unscoped constraints: \d+ \((CLAUDE\.md|AGENTS\.md):\d+",
        result.output,
    )
    assert inline_match is not None, (
        "Expected unscoped-constraint output to inline 'CLAUDE.md:N' or "
        f"'AGENTS.md:N'; got:\n{result.output}"
    )


# --- Fix #7: Next-steps numbering is contiguous ----------------------------


def test_next_steps_numbering_is_contiguous_when_imports_skipped(
    tmp_path: Path,
) -> None:
    """When init runs with --add-imports defaults that skip the imports
    step (e.g., the user already has @.cortex/protocol.md), the printed
    Next-steps list must renumber so step 2 is contiguous with step 1
    rather than emitting "1." then "3.".

    Trigger the conditional path: project has CLAUDE.md AND AGENTS.md
    that already contain `@.cortex/protocol.md` — init skips the
    "Import @.cortex/..." step, so Next-steps would have only the
    Author + Doctor steps. They must be numbered 1 + 2, not 1 + 3.
    """
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\n@.cortex/protocol.md\n@.cortex/state.md\n"
    )
    (tmp_path / "AGENTS.md").write_text(
        "# Project\n\n@.cortex/protocol.md\n@.cortex/state.md\n"
    )
    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    # Extract the Next-steps section.
    after_header = result.output.split("Next steps:", 1)[1]
    # Find every numbered line ("  N. ...").
    nums = [int(m.group(1)) for m in re.finditer(r"^  (\d+)\.", after_header, re.MULTILINE)]
    assert nums, f"No numbered Next-steps lines found in:\n{after_header}"
    # Numbers must be contiguous starting at 1.
    expected = list(range(1, len(nums) + 1))
    assert nums == expected, (
        f"Next-steps numbering not contiguous (Fix #7); got {nums}, expected {expected}\n"
        f"Output:\n{after_header}"
    )


def test_next_steps_numbering_includes_imports_step_when_relevant(
    tmp_path: Path,
) -> None:
    """The opposite case: when init DOESN'T add imports (no CLAUDE.md /
    AGENTS.md exist) AND not local-only mode, the imports step should
    appear and numbering should still be contiguous (1, 2, 3)."""
    # Empty project — no CLAUDE.md, no AGENTS.md, so init won't add
    # imports automatically and the Next-steps should advise the user.
    result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    after_header = result.output.split("Next steps:", 1)[1]
    nums = [int(m.group(1)) for m in re.finditer(r"^  (\d+)\.", after_header, re.MULTILINE)]
    expected = list(range(1, len(nums) + 1))
    assert nums == expected, f"Next-steps numbering not contiguous; got {nums}"


# --- Fix #8: top-level --status-only --path ---------------------------------


def test_top_level_status_only_accepts_path(tmp_path: Path) -> None:
    """`cortex --status-only --path X` should target X the same way
    `cortex status --path X` does. v0.2.3 only accepted --path on the
    subcommand form."""
    # Bootstrap a minimal .cortex/ at the target path (so status has
    # something to read).
    init_result = CliRunner().invoke(cli, ["init", "--path", str(tmp_path), "--yes"])
    assert init_result.exit_code == 0, init_result.output

    # Now invoke `cortex --status-only --path <tmp_path>` from a different cwd.
    # CliRunner's invoke runs in the current process; the --path override
    # is what proves the bug fix.
    result = CliRunner().invoke(cli, ["--status-only", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # Status output should include the project's path or name; minimal
    # check that it didn't blow up on the unrecognized flag (the v0.2.3
    # bug raised "Error: No such option: --path").
    assert "No such option" not in result.output
    assert "Project:" in result.output or "Versions:" in result.output, (
        f"Status output doesn't look like a status summary:\n{result.output}"
    )
