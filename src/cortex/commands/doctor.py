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

from cortex.audit import DEFAULT_WINDOW_DAYS, EXPECTED_TYPE, audit, audit_digests
from cortex.siblings import detect_siblings, format_sibling_block
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
@click.option(
    "--audit",
    "run_audit",
    is_flag=True,
    default=False,
    help="Also walk recent git history and check that every Tier-1 Protocol "
    "trigger has a matching Journal entry (Protocol § 2).",
)
@click.option(
    "--audit-digests",
    "run_audit_digests",
    is_flag=True,
    default=False,
    help="Also sample each Journal digest and warn when its claims lack "
    "`journal/...` citations (SPEC § 5.4).",
)
@click.option(
    "--since-days",
    type=int,
    default=DEFAULT_WINDOW_DAYS,
    show_default=True,
    help="Audit window in days (only used with --audit).",
)
def doctor_command(
    *,
    target_path: Path,
    run_audit: bool,
    run_audit_digests: bool,
    since_days: int,
) -> None:
    """Validate a project's `.cortex/` directory against SPEC.md.

    Exits 0 on clean, 1 if any issue has severity ``error``. Warnings are
    surfaced but do not fail the exit code.

    ``--audit`` and ``--audit-digests`` run independent Protocol checks on
    top of the structural validation; neither currently escalates exit
    codes on failure — they are informational so you can retrofit Journal
    entries without being blocked from shipping.
    """
    target_path = Path(target_path).resolve()
    issues = run_all_checks(target_path)

    errors = [i for i in issues if i.severity is Severity.ERROR]
    warnings = [i for i in issues if i.severity is Severity.WARNING]

    for issue in issues:
        stream = sys.stderr if issue.severity is Severity.ERROR else sys.stdout
        click.echo(_format_issue(issue), file=stream)

    if issues:
        summary = f"{len(errors)} error{'s' if len(errors) != 1 else ''}, {len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
        click.echo(f"\ncortex doctor: {summary}", err=bool(errors))
    else:
        click.echo(f"cortex doctor: .cortex/ looks healthy ({target_path})")

    if run_audit:
        _print_audit(target_path, since_days)
    if run_audit_digests:
        _print_audit_digests(target_path)

    _print_siblings(target_path)

    if errors:
        sys.exit(1)


def _print_siblings(project_root: Path) -> None:
    """Surface Autumn Garage sibling tools (Doctrine 0002 composition).

    Informational only — presence/absence never escalates exit code or
    warn-severity. See `cortex.siblings` for the detection contract.
    """
    statuses = detect_siblings(project_root)
    click.echo("")
    click.echo(format_sibling_block(statuses))


def _print_audit(project_root: Path, since_days: int) -> None:
    report = audit(project_root, since_days=since_days)
    click.echo(
        f"\ncortex doctor --audit: {report.commits_examined} commit(s) in the last "
        f"{since_days} days; {len(report.fires)} trigger fires, "
        f"{len(report.unmatched)} unmatched."
    )
    for fire in report.unmatched:
        click.echo(
            f"WARNING  {fire.trigger} {fire.commit.sha[:8]} "
            f"({fire.commit.date.date()}) — no Journal entry "
            f"of Type `{EXPECTED_TYPE[fire.trigger]}` within 72h. "
            f"Subject: {fire.commit.subject}",
            err=True,
        )


def _print_audit_digests(project_root: Path) -> None:
    warnings = audit_digests(project_root)
    if not warnings:
        click.echo("\ncortex doctor --audit-digests: all digests appear to cite their sources.")
        return
    click.echo("\ncortex doctor --audit-digests:")
    for line in warnings:
        click.echo(f"WARNING  {line}", err=True)
