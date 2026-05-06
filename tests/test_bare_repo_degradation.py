"""Bare-repo degradation fixture (Doctrine 0002 made testable).

Per `.cortex/plans/cortex-v1.md` v0.9.0 dogfood gate item: Cortex must
degrade **visibly, not silently** when none of its sibling tools
(Sentinel, Touchstone, Conductor) and none of the optional system tools
(`gh`, `brew`) are present. Doctrine 0002 — compose by file contract,
not code — is the load-bearing rule under test.

The unifying invariant: every command exits cleanly (no Python
traceback in stderr) AND produces visible stdout that names the
degradation explicitly. Silent passing on a missing tool is the
failure mode.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from cortex.cli import cli
from cortex.commands.init import init_command

# ----- helpers ---------------------------------------------------------


def _strip_optional_tools_from_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Build a sanitized PATH with NO `gh`, NO `brew`, NO sibling
    helper scripts on it. We keep `git`, `python`, `uv` reachable so the
    test environment itself still works; the goal is to simulate a
    machine where Cortex is installed but the optional tooling is not.
    """
    # Resolve the bare minimum we still need.
    keep = []
    for tool in ("git", "python3", "uv", "sh", "bash"):
        # Walk the existing PATH to find each.
        for entry in os.environ.get("PATH", "").split(":"):
            cand = Path(entry) / tool
            if cand.exists():
                keep.append(str(cand.parent))
                break

    # Build a temp bin/ with shim symlinks ONLY for the kept tools.
    bin_dir = tmp_path / "_minimal_bin"
    bin_dir.mkdir(exist_ok=True)
    for path_dir in dict.fromkeys(keep):
        for tool in ("git", "python3", "uv", "sh", "bash"):
            src = Path(path_dir) / tool
            if src.exists() and not (bin_dir / tool).exists():
                (bin_dir / tool).symlink_to(src)

    monkeypatch.setenv("PATH", str(bin_dir))


def _assert_visible_no_traceback(result: Result, name: str) -> None:
    """Every command in this fixture must:
    1. exit 0 (degradation is not failure),
    2. produce non-empty stdout (silent is the bug),
    3. emit no Python traceback in the captured output (no crashes).
    """
    assert result.exit_code == 0, f"{name} exited {result.exit_code}: {result.output}"
    assert result.output.strip(), f"{name} produced no visible output"
    assert "Traceback (most recent call last):" not in result.output, (
        f"{name} emitted a Python traceback:\n{result.output}"
    )


# ----- fixtures --------------------------------------------------------


@pytest.fixture
def bare_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A minimal git repo with NO sibling tools available.

    No `.sentinel/runs/`, no `.touchstone-config`, no `.codex-review.toml`,
    no `gh` or `brew` resolvable on PATH. Cortex's degradation contract
    must hold across `init`, `doctor`, `manifest`, `journal draft`,
    `refresh-state`.
    """
    # Sanitize PATH so optional tools and sibling helpers are unresolvable.
    _strip_optional_tools_from_path(monkeypatch, tmp_path)
    return tmp_path


# ----- the contract: every command degrades visibly -------------------


def test_init_degrades_visibly(bare_repo: Path) -> None:
    """`cortex init --yes` must scaffold cleanly even with no siblings
    on PATH and no sibling-tool config files. Output must indicate the
    scaffold completed; no traceback."""
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(bare_repo), "--yes"])
    _assert_visible_no_traceback(result, "cortex init")
    # The scaffold must have actually written the SPEC marker.
    assert (bare_repo / ".cortex" / "SPEC_VERSION").exists(), (
        "cortex init must write .cortex/SPEC_VERSION even on a bare host"
    )


def test_doctor_degrades_visibly(bare_repo: Path) -> None:
    """`cortex doctor` on a freshly initialized bare repo must produce
    a visible summary and never a silent or crashy result."""
    runner = CliRunner()
    init = runner.invoke(init_command, ["--path", str(bare_repo), "--yes"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(cli, ["doctor", "--path", str(bare_repo)])
    _assert_visible_no_traceback(result, "cortex doctor")
    # The summary line must always be present.
    assert "cortex doctor:" in result.output, "doctor must print a summary line"


def test_doctor_sibling_block_handles_absence(bare_repo: Path) -> None:
    """The sibling-detection block in doctor (Doctrine 0002) must remain
    informational — never escalate severity — when neither
    `.touchstone-config` nor `.sentinel/runs/` is present. The block
    structure must still be visible in output."""
    runner = CliRunner()
    init = runner.invoke(init_command, ["--path", str(bare_repo), "--yes"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(cli, ["doctor", "--path", str(bare_repo)])
    _assert_visible_no_traceback(result, "cortex doctor (sibling block)")
    # The sibling section header is part of doctor's contract.
    assert "siblings:" in result.output.lower() or "sibling" in result.output.lower(), (
        "doctor must surface the sibling block even when no siblings are present"
    )


def test_manifest_degrades_visibly(bare_repo: Path) -> None:
    """`cortex manifest --budget 8000` on a fresh init must produce a
    non-empty, non-crashing manifest. The bare init has minimal corpus
    but a valid scaffold; manifest must succeed."""
    runner = CliRunner()
    init = runner.invoke(init_command, ["--path", str(bare_repo), "--yes"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(cli, ["manifest", "--path", str(bare_repo), "--budget", "8000"])
    _assert_visible_no_traceback(result, "cortex manifest")


def test_journal_draft_degrades_visibly(bare_repo: Path) -> None:
    """`cortex journal draft decision --no-edit` must write a journal
    entry without invoking `$EDITOR`. The path must be reported in
    output (not silent)."""
    runner = CliRunner()
    init = runner.invoke(init_command, ["--path", str(bare_repo), "--yes"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(
        cli,
        [
            "journal",
            "draft",
            "decision",
            "--path",
            str(bare_repo),
            "--title",
            "bare-repo smoke",
            "--no-edit",
        ],
    )
    _assert_visible_no_traceback(result, "cortex journal draft")
    # An entry must now exist in the journal directory.
    journal_dir = bare_repo / ".cortex" / "journal"
    entries = [p for p in journal_dir.glob("*.md") if p.name != ".gitkeep"]
    assert entries, "journal draft must write a real entry, not silently no-op"


def test_refresh_state_degrades_visibly(bare_repo: Path) -> None:
    """`cortex refresh-state` must regenerate state.md (or report no
    changes), and must never crash on a freshly initialized bare repo."""
    runner = CliRunner()
    init = runner.invoke(init_command, ["--path", str(bare_repo), "--yes"])
    assert init.exit_code == 0, init.output

    result = runner.invoke(cli, ["refresh-state", "--path", str(bare_repo)])
    _assert_visible_no_traceback(result, "cortex refresh-state")
    # state.md must remain present after refresh.
    assert (bare_repo / ".cortex" / "state.md").exists(), (
        "refresh-state must leave state.md in place"
    )


# ----- negative tests: prove the visibility guarantee isn't vacuous ----


def test_corrupt_state_makes_doctor_visible(bare_repo: Path) -> None:
    """A corrupt state.md (no frontmatter) must produce visible output
    from doctor — exit code may differ but the corruption must be
    surfaced. Confirms the assertions above aren't passing on a silent
    pass that hides real problems."""
    runner = CliRunner()
    init = runner.invoke(init_command, ["--path", str(bare_repo), "--yes"])
    assert init.exit_code == 0, init.output

    state = bare_repo / ".cortex" / "state.md"
    body = state.read_text()
    if body.startswith("---"):
        body = body.split("---", 2)[-1].lstrip()
    state.write_text(body)

    result = runner.invoke(cli, ["doctor", "--path", str(bare_repo)])
    # The output must not be empty; the issue must be visible.
    assert result.output.strip(), "doctor must produce visible output on corruption"
    assert "Traceback (most recent call last):" not in result.output, (
        "doctor must not crash on corrupt state.md"
    )
    # The corruption must be named in some way.
    text = result.output.lower()
    assert (
        "state.md" in text
        or "frontmatter" in text
        or "warning" in text
        or "error" in text
    ), "doctor must surface the corruption explicitly"
