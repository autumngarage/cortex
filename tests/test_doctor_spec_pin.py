"""cortex#500: generated-layer `Spec:` frontmatter vs `.cortex/SPEC_VERSION`.

Evidence class: SPEC_VERSION was bumped 0.5.0 -> 1.1.0 (PR #493) while map.md
kept declaring `Spec: 0.3.1`; `cortex doctor` was silent and a probabilistic
merge reviewer caught the deterministic invariant. These tests pin the doctor
warning that closes that gap.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from cortex.commands.init import init_command
from cortex.doctor_checks import check_spec_version_drift, run_plain_checks
from cortex.validation import Severity


def _scaffold(project: Path) -> None:
    result = CliRunner().invoke(init_command, ["--path", str(project)])
    assert result.exit_code == 0, result.output


def _write_map(project: Path, spec_line: str | None) -> None:
    spec_field = f"{spec_line}\n" if spec_line is not None else ""
    (project / ".cortex" / "map.md").write_text(
        "---\n"
        "Generated: 2026-06-09T00:00:00+00:00\n"
        "Generator: hand-authored\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        f"{spec_field}"
        "---\n\n# Project Map\n"
    )


def _spec_issues(project: Path) -> list[str]:
    return [
        f"{issue.path}: {issue.message}"
        for issue in check_spec_version_drift(project)
    ]


def test_mismatched_map_spec_warns_naming_both_values_and_file(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "SPEC_VERSION").write_text("1.1.0\n")
    _write_map(tmp_path, "Spec: 0.3.1")

    issues = check_spec_version_drift(tmp_path)
    map_issues = [issue for issue in issues if issue.path == ".cortex/map.md"]
    assert len(map_issues) == 1, _spec_issues(tmp_path)
    issue = map_issues[0]
    assert issue.severity is Severity.WARNING
    assert "0.3.1" in issue.message
    assert "1.1.0" in issue.message
    assert ".cortex/map.md" in issue.message
    assert "(pinned:" in issue.message  # remediation names the pin escape hatch


def test_matched_spec_is_silent(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    current = (tmp_path / ".cortex" / "SPEC_VERSION").read_text().strip()
    _write_map(tmp_path, f"Spec: {current}")

    assert _spec_issues(tmp_path) == []


def test_pinned_spec_is_silent(tmp_path: Path) -> None:
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "SPEC_VERSION").write_text("1.1.0\n")
    _write_map(tmp_path, "Spec: 0.3.1 (pinned: corpus frozen for dogfood replay)")

    assert _spec_issues(tmp_path) == []


def test_layer_without_spec_field_is_silent(tmp_path: Path) -> None:
    """Compat: layers predating the Spec: field never warn (it is not one of
    the seven provenance fields)."""
    _scaffold(tmp_path)
    _write_map(tmp_path, None)

    assert _spec_issues(tmp_path) == []


def test_missing_spec_version_file_is_silent(tmp_path: Path) -> None:
    """check_scaffold already errors on a missing SPEC_VERSION; this check
    must not duplicate that failure."""
    _scaffold(tmp_path)
    _write_map(tmp_path, "Spec: 0.3.1")
    (tmp_path / ".cortex" / "SPEC_VERSION").unlink()

    assert _spec_issues(tmp_path) == []


def test_mismatched_state_spec_warns(tmp_path: Path) -> None:
    """The invariant covers every generated layer, not just map.md."""
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "SPEC_VERSION").write_text("1.1.0\n")
    (tmp_path / ".cortex" / "state.md").write_text(
        "---\n"
        "Generated: 2026-06-09T00:00:00+00:00\n"
        "Generator: cortex refresh-state v1.6.4\n"
        "Sources: []\n"
        "Corpus: 0\n"
        "Omitted: []\n"
        "Incomplete: []\n"
        "Conflicts-preserved: []\n"
        "Spec: 0.5.0\n"
        "---\n\n# Project State\n"
    )

    issues = check_spec_version_drift(tmp_path)
    assert any(
        issue.path == ".cortex/state.md" and "0.5.0" in issue.message and "1.1.0" in issue.message
        for issue in issues
    ), _spec_issues(tmp_path)


def test_spec_drift_runs_on_plain_doctor(tmp_path: Path) -> None:
    """The check must be wired into run_plain_checks, not just importable."""
    _scaffold(tmp_path)
    (tmp_path / ".cortex" / "SPEC_VERSION").write_text("1.1.0\n")
    _write_map(tmp_path, "Spec: 0.3.1")

    issues = run_plain_checks(tmp_path)
    assert any(
        issue.path == ".cortex/map.md"
        and "Spec: 0.3.1" in issue.message
        and "1.1.0" in issue.message
        for issue in issues
    ), [f"{issue.path}: {issue.message}" for issue in issues]
