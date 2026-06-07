"""`cortex doctor` — validate a project's `.cortex/` against the SPEC.

Current structural scope (SPEC v0.5.0):

- Scaffold structure (SPEC_VERSION, protocol.md, templates/, subdirs)
- Seven-field metadata contract on derived layers (§ 4.5)
- Doctrine entry frontmatter (§ 3.1)
- Plan frontmatter + Goal-hash recomputation + required sections (§§ 3.4, 4.1, 4.3, 4.9)
- Journal filenames (§ 3.5)

The ``--audit`` variants run independent Tier-1 and digest checks on top of
the structural pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cortex.audit import DEFAULT_WINDOW_DAYS, EXPECTED_TYPE, audit, audit_digests
from cortex.audit_instructions import (
    audit_instructions,
    format_audit_instructions_human,
)
from cortex.banner import SUBTITLE_DOCTOR, cortex_version, print_banner
from cortex.doctor_checks import (
    run_audit_checks,
    run_issue_ref_checks,
    run_plain_checks,
    run_pr_trailer_checks,
)
from cortex.production_doctor import production_report
from cortex.siblings import detect_siblings, format_sibling_block
from cortex.usage import read_usage
from cortex.validation import Issue, Severity, run_all_checks


def _run_production_doctor(project_root: Path, *, strict: bool, as_json: bool) -> None:
    report = production_report(project_root)
    if as_json:
        click.echo(json.dumps(report, sort_keys=True))
    else:
        errors = int(report["errors"])
        warnings = int(report["warnings"])
        click.echo(
            f"cortex doctor --production: {errors} error{'s' if errors != 1 else ''}, "
            f"{warnings} warning{'s' if warnings != 1 else ''} ({project_root})"
        )
        diagnostics = report.get("diagnostics")
        if isinstance(diagnostics, list):
            for item in diagnostics:
                if not isinstance(item, dict):
                    continue
                severity = str(item.get("severity", "info"))
                path = str(item.get("path", ""))
                message = str(item.get("message", ""))
                code = str(item.get("code", ""))
                repair = item.get("repair_command")
                prefix = f"{severity.upper():<7}"
                location = f"{path}: " if path else ""
                line = f"{prefix} [{code}] {location}{message}"
                stream = sys.stderr if severity == "error" else sys.stdout
                click.echo(line, file=stream)
                if isinstance(repair, str) and repair:
                    click.echo(f"         repair: {repair}", file=stream)
        usage = report.get("usage")
        if isinstance(usage, dict) and usage.get("grep_to_retrieve_ratio") is not None:
            click.echo(
                "info: lookup usage "
                f"grep={usage.get('grep')} retrieve_total={usage.get('retrieve_total')} "
                f"ratio={usage.get('grep_to_retrieve_ratio')}"
            )

    errors = int(report["errors"])
    warnings = int(report["warnings"])
    if errors or (strict and warnings):
        sys.exit(1)


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
    help="Also scan recent git history and warn when required Journal entries are missing "
    "for merges, releases, and other tracked events. Informational: does not change the exit code.",
)
@click.option(
    "--audit-digests",
    "run_audit_digests",
    is_flag=True,
    default=False,
    help="Also sample each Journal digest and warn when its claims cannot be traced "
    "back to a source Journal entry. Informational: does not change the exit code.",
)
@click.option(
    "--audit-instructions",
    "run_audit_instructions",
    is_flag=True,
    default=False,
    help="Also verify filesystem paths, GitHub releases, Homebrew formulas, PyPI packages, "
    "and URLs cited in CLAUDE.md, AGENTS.md, and README.md. Use with --strict to fail on warnings.",
)
@click.option(
    "--audit-pr-trailers",
    "run_audit_pr_trailers",
    is_flag=True,
    default=False,
    help="Warn when commits or the open PR reference an issue without a "
    "Closes-issue: / Closes: / Fixes: trailer. "
    "Use Refs: #N to explicitly opt out of auto-close for a mention.",
)
@click.option(
    "--audit-issue-refs",
    "run_audit_issue_refs",
    is_flag=True,
    default=False,
    help="Cross-reference open [ ] checkboxes in .cortex/ content with GitHub issue state. "
    "Warns when a checkbox references a closed issue. Requires the `gh` CLI. "
    "Results cache for 24h in .cortex/.cache/issue-state.json. "
    "Use with --strict to fail on warnings.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help=(
        "Exit with non-zero status if any warnings are present, in addition to errors. "
        "Use in CI / merge gates. Without this flag, warnings are reported but exit "
        "code stays 0 unless errors exist."
    ),
)
@click.option(
    "--production",
    is_flag=True,
    default=False,
    help="Run the production Context CI profile with stable diagnostic codes.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON (supported with --production or --audit-instructions).",
)
@click.option(
    "--since-days",
    type=int,
    default=DEFAULT_WINDOW_DAYS,
    show_default=True,
    help="How many days of git history to scan (only used with --audit).",
)
def doctor_command(
    *,
    target_path: Path,
    run_audit: bool,
    run_audit_digests: bool,
    run_audit_instructions: bool,
    run_audit_pr_trailers: bool,
    run_audit_issue_refs: bool,
    production: bool,
    strict: bool,
    as_json: bool,
    since_days: int,
) -> None:
    """Check a project's `.cortex/` directory for SPEC conformance.

    Exits 0 when clean, 1 on any structural error. Warnings are printed but
    do not affect the exit code unless ``--strict`` is passed.

    The ``--audit*`` flags add optional Protocol-compliance and external-claim
    checks on top of the structural pass. Run them independently or together.
    """
    target_path = Path(target_path).resolve()
    if production:
        _run_production_doctor(target_path, strict=strict, as_json=as_json)
        return
    if as_json and not run_audit_instructions:
        raise click.UsageError("--json is currently supported with --audit-instructions or --production")
    if as_json:
        instruction_warnings = _print_audit_instructions(target_path, as_json=True)
        if strict and instruction_warnings:
            sys.exit(1)
        return
    if run_audit_instructions and not run_audit and not run_audit_digests:
        instruction_warnings = _print_audit_instructions(target_path, as_json=False)
        if strict and instruction_warnings:
            sys.exit(1)
        return

    print_banner(SUBTITLE_DOCTOR, cortex_version())

    issues = run_all_checks(target_path)
    issues.extend(run_plain_checks(target_path))
    if run_audit:
        issues.extend(run_audit_checks(target_path, since_days=since_days))
    if run_audit_pr_trailers:
        issues.extend(run_pr_trailer_checks(target_path))
    if run_audit_issue_refs:
        issues.extend(run_issue_ref_checks(target_path))
    issues = sorted(issues, key=lambda i: (i.severity.value, i.path, i.message))

    errors = [i for i in issues if i.severity is Severity.ERROR]
    warnings = [i for i in issues if i.severity is Severity.WARNING]

    for issue in issues:
        stream = sys.stderr if issue.severity is Severity.ERROR else sys.stdout
        click.echo(_format_issue(issue), file=stream)

    if issues:
        summary = f"{len(errors)} error{'s' if len(errors) != 1 else ''}, {len(warnings)} warning{'s' if len(warnings) != 1 else ''}"
        click.echo(f"\ncortex doctor: {summary}", err=bool(errors))
        if strict and warnings and not errors:
            click.echo("(strict mode: exiting non-zero because warnings exist)")
    else:
        click.echo(f"cortex doctor: .cortex/ looks healthy ({target_path})")

    if run_audit:
        _print_audit(target_path, since_days)
    if run_audit_digests:
        _print_audit_digests(target_path)
    instruction_warnings = 0
    if run_audit_instructions:
        click.echo("")
        instruction_warnings = _print_audit_instructions(target_path, as_json=as_json)

    if not as_json:
        _print_usage_ratio(target_path)
        _print_siblings(target_path)

    if errors or (strict and (warnings or instruction_warnings)):
        sys.exit(1)


def _print_usage_ratio(project_root: Path) -> None:
    """Print local grep:retrieve usage ratio once both sides have data.

    Threshold invariant (derive limits from domain): at least one grep hit and
    at least one retrieve hit are required before we surface ratio math.
    """

    usage = read_usage(project_root)
    counts_raw = usage.get("counts")
    since_raw = usage.get("since")
    if not isinstance(counts_raw, dict):
        return
    grep_count = counts_raw.get("grep")
    retrieve_bm25 = counts_raw.get("retrieve_bm25")
    retrieve_semantic = counts_raw.get("retrieve_semantic")
    retrieve_hybrid = counts_raw.get("retrieve_hybrid")
    if not (
        isinstance(grep_count, int)
        and isinstance(retrieve_bm25, int)
        and isinstance(retrieve_semantic, int)
        and isinstance(retrieve_hybrid, int)
    ):
        return

    retrieve_total = retrieve_bm25 + retrieve_semantic + retrieve_hybrid
    # Threshold constant: one successful call on each side.
    min_side_hits = 1
    if grep_count < min_side_hits or retrieve_total < min_side_hits:
        return

    ratio = grep_count / retrieve_total
    since = since_raw if isinstance(since_raw, str) and since_raw else "unknown"
    click.echo(
        "info: lookup usage since "
        f"{since}: grep={grep_count} retrieve(bm25/semantic/hybrid)="
        f"{retrieve_bm25}/{retrieve_semantic}/{retrieve_hybrid}; "
        f"grep:retrieve ratio {grep_count}:{retrieve_total} ({ratio:.1f})"
    )


def _print_siblings(project_root: Path) -> None:
    """Surface Autumn Garage sibling tools (Doctrine 0002 composition).

    Informational only — presence/absence never escalates exit code or
    warn-severity. See `cortex.siblings` for the detection contract.
    """
    # TODO(cortex#272 / Phase C synthesis): when Cortex-synthesis ships,
    # add a conditional peer check here for the `claude` CLI on PATH.
    # Per Cortex Doctrine 0002 and CLAUDE.md's synthesis runtime rule,
    # synthesis shells out directly to `claude -p`; Cortex must not add a
    # Conductor provider layer. A project that has *enabled synthesis*
    # should be told when `claude` is missing. The check MUST stay dormant
    # for core-only users (init/doctor/manifest/journal/doctrine I/O carry
    # no quartet dependency today): gate it on detecting enabled Phase C
    # synthesis in `.cortex/config.toml`, not on mere `claude` absence.
    # Until synthesis is default-enabled rather than opt-in, the Homebrew
    # formula should stay dependency-free — see the brew rule in CLAUDE.md
    # § "Release & Distribution".
    statuses = detect_siblings(project_root)
    click.echo("")
    click.echo(format_sibling_block(statuses))


def _print_audit(project_root: Path, since_days: int) -> None:
    report = audit(project_root, since_days=since_days)
    click.echo(
        f"\ncortex doctor --audit: {report.commits_examined} commit(s), "
        f"{report.tags_examined} tag(s) in the last {since_days} days; "
        f"{len(report.fires)} trigger fires, {len(report.unmatched)} unmatched."
    )
    for warning in report.warnings:
        click.echo(f"WARNING  {warning}", err=True)
    for fire in report.unmatched:
        click.echo(
            f"WARNING  {fire.trigger} {fire.short_sha} "
            f"({fire.source_date.date()}) — no Journal entry "
            f"of Type `{EXPECTED_TYPE[fire.trigger]}` within 72h. "
            f"{fire.label}",
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


def _print_audit_instructions(project_root: Path, *, as_json: bool) -> int:
    report = audit_instructions(project_root)
    if as_json:
        click.echo(json.dumps(report.to_json(project_root), sort_keys=True))
    else:
        click.echo(format_audit_instructions_human(report, project_root))
    return len(report.warnings)
