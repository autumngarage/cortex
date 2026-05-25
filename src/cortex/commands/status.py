"""`cortex status` — print a summary of where the project is.

Also the implementation called by bare ``cortex`` and by the top-level
``--status-only`` flag on the ``cli`` group.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cortex.commands._auto_sync import (
    auto_sync_disabled_from_context,
    maybe_auto_sync_stale_inputs,
)
from cortex.compat import warn_if_incompatible
from cortex.status import compute_status, format_status


def run_status(project_root: Path, *, as_json: bool) -> None:
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.exists():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    warn_if_incompatible(cortex_dir)
    status = compute_status(project_root)
    if as_json:
        click.echo(json.dumps(status.to_dict(), indent=2, default=str))
    else:
        click.echo(format_status(status), nl=False)


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
    help="Emit a machine-readable JSON document instead of human-readable text.",
)
def status_command(*, target_path: Path, as_json: bool) -> None:
    """Print active plans, journal activity, digest age, and promotion-queue counts."""
    project_root = Path(target_path).resolve()
    # Stale-input auto-update (cortex#261) scoped to THIS command's --path, so
    # `cortex status --path OTHER` from an unrelated cwd updates OTHER. In
    # --json mode the sync narrative is routed to stderr to keep stdout pure.
    maybe_auto_sync_stale_inputs(
        project_root,
        "status",
        disabled=auto_sync_disabled_from_context(),
        json_mode=as_json,
    )
    run_status(project_root, as_json=as_json)
