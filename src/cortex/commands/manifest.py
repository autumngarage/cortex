"""`cortex manifest` — print the session-start manifest per Protocol § 1."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import click

from cortex.compat import warn_if_incompatible
from cortex.manifest import MANIFEST_PROFILES, ManifestProfileName, build_manifest


@click.command("manifest")
@click.option(
    "--budget",
    type=int,
    default=None,
    show_default=False,
    help="Approximate token budget (≈4 chars/token). Below 2000 the manifest "
    "degrades to state-only; at or above 15000 the journal window widens from 72h to 7d. "
    "Defaults to the selected profile's budget. Override when piping into a tight context window.",
)
@click.option(
    "--profile",
    type=click.Choice(sorted(MANIFEST_PROFILES)),
    default="default",
    show_default=True,
    help="Content profile. `default` loads state, doctrine, plans, and recent journal entries (~8k tokens). "
    "`delegation` trims to the pickup pointer and key invariants for agent-to-agent handoffs (~4k tokens).",
)
@click.option(
    "--show-budget",
    is_flag=True,
    help="Show estimated token count for each rendered section. Useful for tuning --budget.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit per-section token-usage diagnostics as JSON instead of the manifest text.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def manifest_command(
    *,
    budget: int | None,
    profile: str,
    show_budget: bool,
    as_json: bool,
    target_path: Path,
) -> None:
    """Print the session-start manifest: state, recent journal, doctrine, and active plans.

    Output is markdown written to stdout so agents can pipe it into their context
    window or humans can redirect it to a file. Use ``--profile delegation`` for
    compact agent-to-agent handoffs; use ``--show-budget`` to see where tokens go.
    """
    target_path = Path(target_path).resolve()
    cortex_dir = target_path / ".cortex"
    if not cortex_dir.exists():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    warn_if_incompatible(cortex_dir)

    profile_name = cast(ManifestProfileName, profile)
    profile_config = MANIFEST_PROFILES[profile_name]
    manifest = build_manifest(
        target_path,
        budget if budget is not None else profile_config.default_budget_tokens,
        profile=profile_name,
    )
    if as_json:
        click.echo(json.dumps(manifest.diagnostics(), indent=2, sort_keys=True))
        return
    click.echo(manifest.render(show_budget=show_budget), nl=False)
