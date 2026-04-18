"""`cortex doctor` — validate a project's `.cortex/` against the SPEC.

First-slice scope (SPEC v0.3.1-dev):

- Scaffold structure (SPEC_VERSION, protocol.md, templates/, subdirs)
- Seven-field metadata contract on derived layers (§ 4.5)
- Doctrine entry frontmatter (§ 3.1)
- Plan frontmatter + Goal-hash recomputation + required sections (§§ 3.4, 4.1, 4.3, 4.9)
- Journal filenames (§ 3.5)

The ``--audit`` variants (session-window Tier-1 compliance, digest claim
sampling) ship in a follow-up PR and are deliberately not wired here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cortex.validation import Issue, Severity, run_all_checks


def _format_issue(issue: Issue) -> str:
    tag = issue.severity.value.upper()
    prefix = f"{tag:<7}"
    if issue.path:
        return f"{prefix} {issue.path}: {issue.message}"
    return f"{prefix} {issue.message}"


@click.command("doctor")
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def doctor_command(*, target_path: Path) -> None:
    """Validate a project's `.cortex/` directory against SPEC.md.

    Exits 0 on clean, 1 if any issue has severity ``error``. Warnings are
    surfaced but do not fail the exit code — use ``--strict`` in CI scripts
    to treat warnings as errors (Phase B follow-up; not implemented yet).
    """
    target_path = Path(target_path).resolve()
    issues = run_all_checks(target_path)

    if not issues:
        click.echo(f"cortex doctor: .cortex/ looks healthy ({target_path})")
        return

    errors = [i for i in issues if i.severity is Severity.ERROR]
    warnings = [i for i in issues if i.severity is Severity.WARNING]

    for issue in issues:
        stream = sys.stderr if issue.severity is Severity.ERROR else sys.stdout
        click.echo(_format_issue(issue), file=stream)

    summary = f"{len(errors)} error{'s' if len(errors) != 1 else ''}, {len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
    click.echo(f"\ncortex doctor: {summary}", err=bool(errors))

    if errors:
        sys.exit(1)
