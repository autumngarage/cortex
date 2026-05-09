"""Cortex CLI entrypoint.

Phase B scaffold: only `cortex version` is implemented here. Subsequent
commands (`init`, `doctor`, `manifest`, `grep`, the interactive flow) arrive
in follow-up PRs per .cortex/plans/phase-b-walking-skeleton.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cortex import SUPPORTED_PROTOCOL_VERSIONS, SUPPORTED_SPEC_VERSIONS, __version__
from cortex.commands._auto_sync import (
    auto_sync_via_env_disabled,
    maybe_auto_sync,
    project_root_from_path_override,
)
from cortex.commands.check_triggers import check_triggers_command
from cortex.commands.doctor import doctor_command
from cortex.commands.grep import grep_command
from cortex.commands.init import init_command
from cortex.commands.install_brief import install_brief_command
from cortex.commands.journal import journal_group
from cortex.commands.manifest import manifest_command
from cortex.commands.migrate_state import migrate_state_command
from cortex.commands.next import next_command
from cortex.commands.plan import plan_group
from cortex.commands.promote import promote_command
from cortex.commands.refresh_index import refresh_index_command
from cortex.commands.refresh_state import refresh_state_command
from cortex.commands.retrieve import retrieve_command
from cortex.commands.status import run_status, status_command
from cortex.commands.sync import sync_command, update_command


def _detect_install_method() -> str:
    """Return a best-effort label for how this CLI was installed.

    Not authoritative — the label is informational for `cortex version` output
    and for bug reports. Order: Homebrew prefix in sys.executable, then editable
    (path contains the repo), else unknown.
    """
    exe = sys.executable
    if "/Cellar/" in exe or exe.startswith("/opt/homebrew") or exe.startswith("/usr/local"):
        return "homebrew"
    if "/.venv/" in exe or "/site-packages/" not in exe:
        return "source (editable or venv)"
    return "unknown"


@click.group(
    name="cortex",
    context_settings={"help_option_names": ["-h", "--help"]},
    invoke_without_command=True,
)
@click.version_option(__version__, "-V", "--version", prog_name="cortex", message="%(prog)s %(version)s")
@click.option(
    "--status-only",
    is_flag=True,
    default=False,
    help="Print the status summary and exit without entering the interactive flow.",
)
@click.option(
    "--path",
    "path_override",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=None,
    help=(
        "Project root to inspect. Defaults to the current working directory. "
        "Equivalent to `cortex status --path X`."
    ),
)
@click.option(
    "--no-auto-sync",
    is_flag=True,
    default=False,
    help=(
        "Skip the auto-sync hook that fires after a minor version bump. "
        "Equivalent to `[sync] auto = false` in `.cortex/config.toml` or "
        "`CORTEX_NO_AUTO_SYNC=1` in the environment."
    ),
)
@click.pass_context
def cli(
    ctx: click.Context,
    status_only: bool,
    path_override: Path | None,
    no_auto_sync: bool,
) -> None:
    """Cortex — project memory protocol and reference CLI.

    Running ``cortex`` with no subcommand prints the project status: active
    plans, recent journal activity, digest age, and promotion-queue counts.
    Use ``cortex status --json`` for machine-readable output, or ``--path``
    to target a project other than the current directory.
    """
    # Auto-sync hook (Layer 2 of cortex#190). Fires before the dispatched
    # subcommand body runs. Skipped during init/update/sync/migrate-state, when
    # opt-out is set, or when the marker indicates only a patch bump.
    project_root = project_root_from_path_override(path_override)
    auto_sync_disabled = no_auto_sync or auto_sync_via_env_disabled()
    maybe_auto_sync(
        project_root,
        ctx.invoked_subcommand,
        disabled=auto_sync_disabled,
    )

    if ctx.invoked_subcommand is None:
        target = path_override if path_override is not None else Path.cwd()
        run_status(target, as_json=False)
        _ = status_only  # flag currently redundant since the default is already non-interactive


@cli.command("version")
def version_command() -> None:
    """Print CLI version, supported spec versions, supported protocol versions, and install method."""
    supported_spec = ", ".join(SUPPORTED_SPEC_VERSIONS)
    supported_protocol = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
    install = _detect_install_method()

    click.echo(f"cortex {__version__}")
    click.echo(f"  supported spec versions:     {supported_spec}")
    click.echo(f"  supported protocol versions: {supported_protocol}")
    click.echo(f"  install method:              {install}")


cli.add_command(init_command)
cli.add_command(install_brief_command)
cli.add_command(check_triggers_command)
cli.add_command(doctor_command)
cli.add_command(manifest_command)
cli.add_command(migrate_state_command)
cli.add_command(grep_command)
cli.add_command(status_command)
cli.add_command(promote_command)
cli.add_command(refresh_index_command)
cli.add_command(refresh_state_command)
cli.add_command(retrieve_command)
cli.add_command(next_command)
cli.add_command(journal_group)
cli.add_command(plan_group)
cli.add_command(update_command)
cli.add_command(sync_command)


if __name__ == "__main__":
    cli()
