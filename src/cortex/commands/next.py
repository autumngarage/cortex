"""`cortex next` — deterministic ranked work-item list."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cortex.compat import warn_if_incompatible
from cortex.ranking import collect_next_items, format_next_human


def run_next(project_root: Path, *, as_json: bool, limit: int | None, since_days: int) -> None:
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.exists():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    warn_if_incompatible(cortex_dir)
    ranked = collect_next_items(project_root, since_days=since_days).limited(limit)
    if as_json:
        click.echo(json.dumps(ranked.to_dict(), indent=2))
    else:
        click.echo(format_next_human(ranked), nl=False)


@click.command("next")
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
    help="Emit a machine-readable JSON object instead of human-readable text.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=0),
    default=None,
    help="Limit each priority band to at most N items.",
)
@click.option(
    "--since",
    "since_days",
    type=click.IntRange(min=0),
    default=30,
    show_default=True,
    help="Include case studies modified in the last N days.",
)
def next_command(
    *,
    target_path: Path,
    as_json: bool,
    limit: int | None,
    since_days: int,
) -> None:
    """Print deterministic next-work candidates grouped by priority."""
    run_next(Path(target_path).resolve(), as_json=as_json, limit=limit, since_days=since_days)
