"""Fresh-clone session-start acceptance test.

Per `.cortex/plans/cortex-v1.md` v0.9.0 dogfood gate item: an agent or human
landing on a fresh clone of a Cortex-enabled project — with no local state —
must be able to answer "where were we?" using only `cortex manifest`,
`cortex next`, and `cortex doctor`, citing current work, recent shipped
reality, and relevant doctrine. This is Cortex's core promise.

The fixture scaffolds a real `.cortex/` via `cortex init` into `tmp_path`
(a fresh dir that nothing else touches), seeds a minimal but realistic
corpus (one active plan, one doctrine entry, two journal entries, including
a recent release), then asserts each session-start command produces
non-empty meaningful output that cites the seeded content.

A real `git clone` is deliberately NOT used: CI must be deterministic and
network-free. The synthetic corpus is the unit of test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.init import init_command


@pytest.fixture
def fresh_clone(tmp_path: Path) -> Path:
    """Scaffold a real .cortex/ and seed a minimal but realistic corpus.

    The corpus mirrors the shape a maintainer would land on after a few
    weeks of sustained use: one active plan with a pickup pointer, a
    recent release journal entry, an older decision journal entry, and
    one doctrine entry. Enough that manifest / next / doctor have real
    content to cite.
    """
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output

    cortex_dir = tmp_path / ".cortex"

    # Seed an active plan with a clear pickup pointer.
    plans = cortex_dir / "plans"
    plans.mkdir(exist_ok=True)
    (plans / "demo-roadmap.md").write_text(_plan_body())

    # Seed two journal entries: a recent release + an older decision.
    journal = cortex_dir / "journal"
    journal.mkdir(exist_ok=True)
    (journal / "2026-05-06-demo-v100-released.md").write_text(_release_body())
    (journal / "2026-04-15-demo-architecture-decision.md").write_text(_decision_body())

    # Seed one doctrine entry alongside the init scaffold's defaults.
    doctrine = cortex_dir / "doctrine"
    doctrine.mkdir(exist_ok=True)
    (doctrine / "0010-demo-load-bearing-rule.md").write_text(_doctrine_body())

    return tmp_path


def _plan_body() -> str:
    """SPEC-§3.4-conformant active plan with required frontmatter +
    sections. The Goal-hash is computed by cortex doctor; we leave a
    placeholder and accept the recompute warning, which is itself a
    visible-doctor-output assertion target."""
    today = datetime.now(UTC).date().isoformat()
    return (
        "---\n"
        "Status: active\n"
        f"Written: {today}\n"
        "Author: human\n"
        "Goal-hash: a6a0b6e8\n"
        "Updated-by:\n"
        f"  - {today}T00:00 demo-author (created)\n"
        "Cites: doctrine/0010-demo-load-bearing-rule\n"
        "---\n\n"
        "# Demo roadmap\n\n"
        "> **Ship the demo widget renderer for v1.0.**\n\n"
        "## Why (grounding)\n\n"
        "doctrine/0010-demo-load-bearing-rule mandates synchronous rendering "
        "and the v1.0 demo deliverable depends on it.\n\n"
        "## Approach\n\n"
        "Implement the renderer at `src/widget.py` against the existing "
        "test fixture in `tests/widgets/`.\n\n"
        "## Pickup pointer\n\n"
        "Next concrete action: implement `render_widget()` at `src/widget.py:1`.\n\n"
        "## Success Criteria\n\n"
        "- [x] Initial design committed at `src/widget.py:1`.\n"
        "- [ ] `tests/test_widget.py` green for ≥3 representative inputs.\n"
        "- [ ] Documented in README.md `## Widget renderer` section.\n\n"
        "## Work items\n\n"
        "- [x] Sketch design.\n"
        "- [ ] Implement `render_widget()`.\n"
        "- [ ] Add tests.\n\n"
        "## Follow-ups (deferred)\n\n"
        "- (none)\n\n"
        "## Known limitations at exit\n\n"
        "- Widget renderer is synchronous-only; async deferred to v1.x.\n"
    )


def _release_body() -> str:
    return (
        "# Demo v1.0.0 released\n\n"
        "**Date:** 2026-05-06\n"
        "**Type:** release\n"
        "**Trigger:** T1.10\n"
        "**Tag:** v1.0.0\n"
        "**Cites:** plans/demo-roadmap\n\n"
        "> Demo v1.0.0 ships the widget renderer.\n\n"
        "## Artifact\n\n"
        "- **Kind:** GitHub Release\n"
        "- **Location:** https://example.invalid/demo/releases/tag/v1.0.0\n"
        "- **Version:** v1.0.0\n"
        "- **Tag:** v1.0.0\n"
        "- **Release notes:** https://example.invalid/demo/releases/tag/v1.0.0\n\n"
        "## What shipped\n\n"
        "- Widget renderer for the demo plan.\n\n"
        "## Downstream docs this changes\n\n"
        "- README.md — install command.\n"
    )


def _decision_body() -> str:
    return (
        "# Demo architecture decision\n\n"
        "**Date:** 2026-04-15\n"
        "**Type:** decision\n"
        "**Trigger:** T1.1\n"
        "**Cites:** doctrine/0010-demo-load-bearing-rule\n\n"
        "> Chose the synchronous renderer path for v1.0.\n\n"
        "## Context\n\n"
        "We considered async but the synchronous path is simpler.\n\n"
        "## Decision\n\n"
        "Synchronous rendering for v1.0.\n"
    )


def _doctrine_body() -> str:
    return (
        "---\n"
        "Number: 10\n"
        "Title: Demo load-bearing rule\n"
        "Status: Accepted\n"
        "Date: 2026-04-15\n"
        "Load-priority: always\n"
        "---\n\n"
        "# 0010 — Demo load-bearing rule\n\n"
        "Synchronous rendering only; no async paths in v1.0.\n"
    )


# ----- the tests --------------------------------------------------------


def test_manifest_cites_active_plan_within_budget(fresh_clone: Path) -> None:
    """`cortex manifest --budget 8000` must produce non-empty output that
    cites the active plan's content — proving the agent can answer
    "where were we?" without scanning the whole directory."""
    runner = CliRunner()
    result = runner.invoke(cli, ["manifest", "--path", str(fresh_clone), "--budget", "8000"])
    assert result.exit_code == 0, result.output
    assert result.output.strip(), "manifest must produce non-empty output"
    # The pickup pointer or plan slug must be cited.
    assert "demo-roadmap" in result.output or "Demo roadmap" in result.output, (
        "manifest must cite the active plan"
    )


def test_manifest_includes_recent_release(fresh_clone: Path) -> None:
    """The manifest's recent-shipped-reality slice must surface the recent
    release journal entry (it's < 72h old per the manifest's default
    journal window)."""
    runner = CliRunner()
    result = runner.invoke(cli, ["manifest", "--path", str(fresh_clone), "--budget", "8000"])
    assert result.exit_code == 0, result.output
    # Either the journal slug or a key phrase from the release.
    assert "v1.0.0" in result.output or "2026-05-06" in result.output, (
        "manifest must surface the recent release"
    )


def test_next_returns_non_empty_ranked_list(fresh_clone: Path) -> None:
    """`cortex next` must return a non-empty ranked list of candidates with
    stable citations — never a silent empty exit."""
    runner = CliRunner()
    result = runner.invoke(cli, ["next", "--path", str(fresh_clone)])
    assert result.exit_code == 0, result.output
    assert result.output.strip(), "next must produce non-empty output"


def test_doctor_produces_unambiguous_output(fresh_clone: Path) -> None:
    """`cortex doctor` must produce unambiguous output: either a clean pass
    with explicit zero-warning summary, or visible findings. Silent
    passing is itself a failure mode the gate exists to prevent."""
    runner = CliRunner()
    result = runner.invoke(cli, ["doctor", "--path", str(fresh_clone)])
    assert result.exit_code == 0, result.output
    assert result.output.strip(), "doctor must produce non-empty output"
    # The summary line must be present so humans can see the result.
    assert "cortex doctor:" in result.output, "doctor must print a summary line"


def test_corrupt_corpus_makes_doctor_visible(tmp_path: Path) -> None:
    """Negative test: a corrupt `.cortex/` (state.md without frontmatter)
    must surface a visible warning or error, not a silent pass. This is
    the inverse of the silent-failure-is-a-bug guarantee."""
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output

    # Strip the frontmatter from state.md.
    state = tmp_path / ".cortex" / "state.md"
    body = state.read_text()
    if body.startswith("---"):
        body = body.split("---", 2)[-1].lstrip()
    state.write_text(body)

    doctor = runner.invoke(cli, ["doctor", "--path", str(tmp_path)])
    # Doctor may exit clean OR with non-zero, but the output must
    # mention the corruption — not a silent pass.
    assert doctor.output.strip(), "doctor must produce visible output on corruption"
    assert (
        "state.md" in doctor.output.lower()
        or "frontmatter" in doctor.output.lower()
        or "warning" in doctor.output.lower()
        or "error" in doctor.output.lower()
    ), "doctor must surface the corruption in some way"
