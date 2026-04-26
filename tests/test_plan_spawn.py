"""Tests for `cortex plan spawn <slug>`.

Each test scaffolds a real `.cortex/` via ``cortex init`` and exercises
the spawn command against the bundled plan template, real filesystem,
and the existing validation pipeline (`run_all_checks`). A scaffolded
plan must pass `cortex doctor` cleanly so the spawn UX produces a
SPEC-conformant Plan out of the box.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from cortex.cli import cli
from cortex.commands.init import init_command
from cortex.goal_hash import normalize_goal_hash
from cortex.validation import Severity, run_all_checks


@pytest.fixture
def cortex_project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    return tmp_path


def _spawn(project: Path, slug: str, *, title: str, cites: str | None = None) -> Result:
    runner = CliRunner()
    args = ["plan", "spawn", slug, "--title", title, "--path", str(project)]
    if cites is not None:
        args.extend(["--cites", cites])
    return runner.invoke(cli, args)


def test_spawn_writes_plan_with_computed_goal_hash(cortex_project: Path) -> None:
    title = "Pin retry backoff to 5 seconds"
    expected_hash = normalize_goal_hash(title)
    result = _spawn(cortex_project, "pin-retry-backoff", title=title)
    assert result.exit_code == 0, result.output
    plan = cortex_project / ".cortex" / "plans" / "pin-retry-backoff.md"
    assert plan.exists()
    text = plan.read_text()
    assert f"Goal-hash: {expected_hash}" in text
    assert text.count(f"# {title}") == 1
    today = date.today().isoformat()
    assert f"Written: {today}" in text


def test_spawn_passes_doctor(cortex_project: Path) -> None:
    """Plan scaffolded by `plan spawn` must pass cortex doctor cleanly —
    the whole point of the command is that authors don't fight required-
    sections / Goal-hash / measurable-success-criteria errors before they
    can even start writing."""
    result = _spawn(
        cortex_project,
        "ship-feature-x",
        title="Ship feature X",
        cites="doctrine/0001-why-cortex-exists, state.md § P0",
    )
    assert result.exit_code == 0, result.output
    issues = run_all_checks(cortex_project)
    plan_issues = [i for i in issues if i.path and "ship-feature-x.md" in i.path]
    errors = [i for i in plan_issues if i.severity is Severity.ERROR]
    assert not errors, [f"{i.path}: {i.message}" for i in errors]


def test_spawn_without_cites_still_passes_doctor(cortex_project: Path) -> None:
    """Default flow (no --cites) must produce a SPEC-conformant Plan.

    Cites is a required scalar (validation.PLAN_REQUIRED_FIELDS); a bare
    `Cites:` parses as None and fails the non-empty-scalar check. The
    default value should be a placeholder string so doctor stays clean
    while flagging the TODO in human-readable form."""
    result = _spawn(cortex_project, "no-cites", title="Plan without explicit cites")
    assert result.exit_code == 0, result.output
    text = (cortex_project / ".cortex" / "plans" / "no-cites.md").read_text()
    # Cites line is non-empty (passes validation).
    assert "Cites: (fill in:" in text
    issues = run_all_checks(cortex_project)
    plan_issues = [i for i in issues if i.path and "no-cites.md" in i.path]
    errors = [i for i in plan_issues if i.severity is Severity.ERROR]
    assert not errors, [f"{i.path}: {i.message}" for i in errors]


def test_spawn_cites_populated_from_flag(cortex_project: Path) -> None:
    result = _spawn(
        cortex_project,
        "with-cites",
        title="Plan with citations",
        cites="doctrine/0001-why-cortex-exists, journal/2026-04-25-foo",
    )
    assert result.exit_code == 0, result.output
    text = (
        cortex_project / ".cortex" / "plans" / "with-cites.md"
    ).read_text()
    assert (
        "Cites: doctrine/0001-why-cortex-exists, journal/2026-04-25-foo" in text
    )


def test_spawn_refuses_overwrite(cortex_project: Path) -> None:
    a = _spawn(cortex_project, "duplicate", title="Plan one")
    assert a.exit_code == 0
    b = _spawn(cortex_project, "duplicate", title="Plan two")
    assert b.exit_code == 2, b.output
    combined = b.output + (getattr(b, "stderr", "") or "")
    assert "already exists" in combined


def test_spawn_invalid_slug_rejected(cortex_project: Path) -> None:
    for bad in ("../escape", "Plan", "-leading", "with/slash", ""):
        result = _spawn(cortex_project, bad, title="Whatever")
        assert result.exit_code == 2, (bad, result.output)


def test_spawn_outside_cortex_project_errors(tmp_path: Path) -> None:
    result = _spawn(tmp_path, "no-cortex", title="Anything")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "does not exist" in combined


def test_spawn_refuses_missing_spec_version(cortex_project: Path) -> None:
    (cortex_project / ".cortex" / "SPEC_VERSION").unlink()
    result = _spawn(cortex_project, "no-spec", title="Plan title")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "refusing to write" in combined
    assert not (cortex_project / ".cortex" / "plans" / "no-spec.md").exists()


def test_spawn_refuses_unsupported_spec_version(cortex_project: Path) -> None:
    (cortex_project / ".cortex" / "SPEC_VERSION").write_text("9.9.9-future\n")
    result = _spawn(cortex_project, "future", title="Plan title")
    assert result.exit_code == 2, result.output
    combined = result.output + (getattr(result, "stderr", "") or "")
    assert "Refusing to write" in combined
    assert not (cortex_project / ".cortex" / "plans" / "future.md").exists()


def test_spawn_author_from_session_id(cortex_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_SESSION_ID", "claude-session-2026-04-25T20:00")
    result = _spawn(cortex_project, "by-agent", title="Agent-spawned plan")
    assert result.exit_code == 0, result.output
    text = (cortex_project / ".cortex" / "plans" / "by-agent.md").read_text()
    assert "Author: claude-session-2026-04-25T20:00" in text
    assert "claude-session-2026-04-25T20:00 (created via cortex plan spawn)" in text


def test_spawn_default_author_is_human(cortex_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORTEX_SESSION_ID", raising=False)
    result = _spawn(cortex_project, "human-spawn", title="Human-spawned plan")
    assert result.exit_code == 0, result.output
    text = (cortex_project / ".cortex" / "plans" / "human-spawn.md").read_text()
    assert "Author: human" in text
    assert "human (created via cortex plan spawn)" in text
