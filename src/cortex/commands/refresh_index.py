"""`cortex refresh-index` — rebuild `.cortex/.index.json`."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cortex.compat import require_compatible
from cortex.config import load_refresh_index_config
from cortex.index import refresh_index


@click.command("refresh-index")
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def refresh_index_command(*, target_path: Path) -> None:
    """Regenerate `.cortex/.index.json` from primary Cortex sources."""

    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    require_compatible(cortex_dir)
    config = load_refresh_index_config(project_root)
    result = refresh_index(project_root, config)
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)
    click.echo(str(result.path))
