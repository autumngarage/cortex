"""`cortex plan status` — report Plan completion and staleness."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cortex.compat import warn_if_incompatible
from cortex.plans import PlanStatus, collect_plan_statuses


def _format_done(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def format_plan_statuses(plans: list[PlanStatus]) -> str:
    lines: list[str] = []
    for idx, plan in enumerate(plans):
        if idx:
            lines.append("")
        last_update = "unknown"
        if plan.last_update is not None and plan.last_update_age_days is not None:
            last_update = f"{plan.last_update.isoformat()} ({plan.last_update_age_days} days ago)"
        lines.extend(
            [
                plan.relative_path,
                f"  Status:      {plan.status or 'unknown'}",
                f"  Goal-hash:   {plan.goal_hash or 'unknown'}",
                "  Completion:  "
                f"{plan.counts.completion_percent}% "
                f"({_format_done(plan.counts.done_equivalent)} of "
                f"{plan.counts.total_for_completion} items)",
                f"  Last update: {last_update}",
                f"  Stale:       {'yes' if plan.stale else 'no'}",
            ]
        )
    return "\n".join(lines) + ("\n" if lines else "")


def run_plan_status(project_root: Path, *, as_json: bool, stale_only: bool) -> None:
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.exists():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    warn_if_incompatible(cortex_dir)
    plans = collect_plan_statuses(project_root)
    if stale_only:
        plans = [plan for plan in plans if plan.stale]

    if as_json:
        click.echo(json.dumps([plan.to_dict() for plan in plans], indent=2))
    else:
        click.echo(format_plan_statuses(plans), nl=False)


@click.command("status")
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a machine-readable JSON list instead of human-readable text.",
)
@click.option(
    "--stale-only",
    is_flag=True,
    default=False,
    help="Only include Plans flagged stale.",
)
def status_command(*, target_path: Path, as_json: bool, stale_only: bool) -> None:
    """Print per-Plan completion and staleness."""
    run_plan_status(Path(target_path).resolve(), as_json=as_json, stale_only=stale_only)
