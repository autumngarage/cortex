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


def test_spawn_passes_structural_doctor_checks(cortex_project: Path) -> None:
    """The scaffolded plan satisfies structural doctor checks: required
    frontmatter is present and non-empty, Goal-hash matches the title,
    required sections exist. Semantic checks (measurable success criteria,
    grounding citation) honestly fail because the body still has
    placeholders — that is the point of spawn (it scaffolds; the user
    fills in real content)."""
    result = _spawn(
        cortex_project,
        "ship-feature-x",
        title="Ship feature X",
        cites="doctrine/0001-why-cortex-exists, state.md § P0",
    )
    assert result.exit_code == 0, result.output
    issues = run_all_checks(cortex_project)
    plan_issues = [i for i in issues if i.path and "ship-feature-x.md" in i.path]
    error_messages = [i.message for i in plan_issues if i.severity is Severity.ERROR]
    # Structural errors must NOT appear: missing required field, missing
    # required section, mismatched Goal-hash.
    for forbidden in (
        "Plan frontmatter field",
        "missing required Plan section",
        "Goal-hash",
        "Plan filename",
    ):
        assert not any(forbidden in m for m in error_messages), (forbidden, error_messages)


def test_spawn_body_honestly_fails_semantic_doctor_checks(cortex_project: Path) -> None:
    """The scaffolded body must NOT pass cortex doctor's measurable-criteria
    or grounding-citation checks by accident — placeholder text in earlier
    versions of the bundled template happened to contain the magic strings
    (`doctrine/`, `cortex doctor`) that the validators look for, so the
    scaffold validated as a complete Plan even with every section
    un-filled. The fix removes those magic strings from placeholder text
    so the validators honestly fire and tell the author what to fill in."""
    result = _spawn(
        cortex_project,
        "ship-feature-y",
        title="Ship feature Y",
        cites="doctrine/0001-why-cortex-exists",
    )
    assert result.exit_code == 0, result.output
    issues = run_all_checks(cortex_project)
    plan_issues = [i for i in issues if i.path and "ship-feature-y.md" in i.path]
    messages = [(i.severity, i.message) for i in plan_issues]
    # Measurable Success Criteria check (an ERROR) must fire on the placeholder body.
    assert any(
        sev is Severity.ERROR and "Success Criteria" in m for sev, m in messages
    ), messages
    # Grounding citation check (a WARNING) must fire — placeholder body
    # has no doctrine/ / state.md / journal/ link in `## Why (grounding)`.
    assert any(
        sev is Severity.WARNING and "grounding" in m.lower() for sev, m in messages
    ), messages


def test_spawn_without_cites_still_satisfies_scalar_check(cortex_project: Path) -> None:
    """Cites is a required non-empty scalar (validation.PLAN_REQUIRED_FIELDS);
    a bare `Cites:` parses as None and fails. Default and empty-input
    cases substitute a placeholder so the scalar check passes while the
    TODO stays human-readable."""
    result = _spawn(cortex_project, "no-cites", title="Plan without explicit cites")
    assert result.exit_code == 0, result.output
    text = (cortex_project / ".cortex" / "plans" / "no-cites.md").read_text()
    assert "Cites: (fill in:" in text
    issues = run_all_checks(cortex_project)
    plan_issues = [i for i in issues if i.path and "no-cites.md" in i.path]
    # Specifically: no "Cites must be non-empty" error.
    cites_errors = [
        i for i in plan_issues
        if i.severity is Severity.ERROR and "Cites" in i.message
    ]
    assert not cites_errors, [i.message for i in cites_errors]


def test_spawn_empty_cites_string_falls_back_to_placeholder(cortex_project: Path) -> None:
    """Defensive: --cites '' (or comma-only / whitespace-only) must fall back
    to the placeholder rather than writing an empty Cites: scalar."""
    for bad_input in ("", ", ,", "   ", ","):
        slug = f"empty-cites-{abs(hash(bad_input)) % 10000}"
        result = _spawn(cortex_project, slug, title="Plan", cites=bad_input)
        assert result.exit_code == 0, (bad_input, result.output)
        text = (cortex_project / ".cortex" / "plans" / f"{slug}.md").read_text()
        assert "Cites: (fill in:" in text, (bad_input, text)


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
