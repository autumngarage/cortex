"""`cortex migrate-state` — make legacy hand-authored State refreshable."""

from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path

import click

from cortex.compat import warn_if_incompatible
from cortex.config import load_refresh_index_config
from cortex.index import refresh_index
from cortex.state_migration import is_legacy_hand_authored_state, render_migrated_state


@click.command("migrate-state")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the migration diff without writing `.cortex/state.md`.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Apply the migration without prompting after showing the diff.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def migrate_state_command(*, dry_run: bool, yes: bool, target_path: Path) -> None:
    """Migrate pre-v0.4 hand-authored `state.md` to marker-preserved State."""

    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    state_path = cortex_dir / "state.md"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)
    if not state_path.exists():
        click.echo(f"error: {state_path} does not exist; run `cortex init` first.", err=True)
        sys.exit(2)

    warn_if_incompatible(cortex_dir)
    before = state_path.read_text()
    if not is_legacy_hand_authored_state(before):
        click.echo(f"{state_path} is already refreshable or is not a legacy hand-authored State file.")
        return

    deterministic = os.environ.get("CORTEX_DETERMINISTIC") == "1"
    after = render_migrated_state(
        project_root,
        deterministic=deterministic,
        assume_index_present=True,
    )
    click.echo(_unified_diff(before, after), nl=False)

    if dry_run:
        click.echo("dry-run: no files written.")
        return
    if not yes:
        click.confirm(f"Apply migration to {state_path}?", default=False, abort=True)
    _refresh_index_after_write(project_root)
    state_path.write_text(after)
    click.echo(str(state_path))


def _unified_diff(before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=".cortex/state.md (before)",
            tofile=".cortex/state.md (after)",
        )
    )


def _refresh_index_after_write(project_root: Path) -> None:
    config = load_refresh_index_config(project_root)
    try:
        result = refresh_index(project_root, config)
    except Exception as exc:
        click.echo(f"warning: could not refresh .cortex/.index.json: {exc}", err=True)
        return
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)
