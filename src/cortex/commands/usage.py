"""`cortex usage` — read local lookup telemetry counters."""

from __future__ import annotations

import json
from pathlib import Path

import click

from cortex.production_doctor import usage_summary


@click.command("usage")
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
    help="Emit machine-readable JSON.",
)
def usage_command(*, target_path: Path, as_json: bool) -> None:
    """Show local grep/retrieve/manifest usage counters for this project."""
    project_root = Path(target_path).resolve()
    summary = usage_summary(project_root)
    if summary is None:
        if as_json:
            click.echo(json.dumps({"usage": None}, sort_keys=True))
        else:
            click.echo("info: no usage telemetry recorded yet")
        return

    if as_json:
        click.echo(json.dumps({"usage": summary}, sort_keys=True))
        return

    since = summary.get("since", "unknown")
    click.echo(
        "lookup usage since "
        f"{since}: grep={summary.get('grep')} "
        f"retrieve_total={summary.get('retrieve_total')} "
        f"(bm25={summary.get('retrieve_bm25')} "
        f"semantic={summary.get('retrieve_semantic')} "
        f"hybrid={summary.get('retrieve_hybrid')}) "
        f"manifest={summary.get('manifest')}"
    )
    ratio = summary.get("grep_to_retrieve_ratio")
    if ratio is not None:
        click.echo(f"grep:retrieve ratio {ratio}")
