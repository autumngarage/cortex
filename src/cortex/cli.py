"""Cortex CLI entrypoint.

Phase B scaffold: only `cortex version` is implemented here. Subsequent
commands (`init`, `doctor`, `manifest`, `grep`, the interactive flow) arrive
in follow-up PRs per .cortex/plans/phase-b-walking-skeleton.md.
"""

from __future__ import annotations

import sys

import click

from cortex import SUPPORTED_PROTOCOL_VERSIONS, SUPPORTED_SPEC_VERSIONS, __version__
from cortex.commands.doctor import doctor_command
from cortex.commands.init import init_command


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
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Cortex — project memory protocol and reference CLI.

    The interactive `cortex` entrypoint (status + promotion queue + digest
    prompts) is not yet implemented; see .cortex/plans/phase-b-walking-skeleton.md.
    Run `cortex --help` to list subcommands.
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


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
cli.add_command(doctor_command)


if __name__ == "__main__":
    cli()
