"""`cortex refresh-state` — regenerate `.cortex/state.md` deterministically.

Set ``CORTEX_DETERMINISTIC=1`` in tests to freeze ``Generated:`` to
``2000-01-01T00:00:00+00:00``. The renderer otherwise uses the current
timestamp while keeping ordering deterministic.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from cortex.compat import require_compatible
from cortex.state_render import build_state_inputs, render_state


@click.command("refresh-state")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the regenerated state.md to stdout without writing it.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def refresh_state_command(*, dry_run: bool, target_path: Path) -> None:
    """Regenerate `.cortex/state.md` from primary Cortex sources."""
    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    require_compatible(cortex_dir)
    deterministic = os.environ.get("CORTEX_DETERMINISTIC") == "1"
    rendered = render_state(build_state_inputs(project_root, deterministic=deterministic))

    if dry_run:
        click.echo(rendered, nl=False)
        return

    (cortex_dir / "state.md").write_text(rendered)
    click.echo(str(cortex_dir / "state.md"))
