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
    help="Approximate token budget for the manifest (≈4 chars/token). Below 2000 "
    "the manifest degrades to state-only; at or above 15000 the Journal window "
    "widens from 72h to 7d. Defaults to the selected profile budget.",
)
@click.option(
    "--profile",
    type=click.Choice(sorted(MANIFEST_PROFILES)),
    default="default",
    show_default=True,
    help="Manifest profile. Use `delegation` for compact agent handoffs.",
)
@click.option(
    "--show-budget",
    is_flag=True,
    help="Show estimated tokens used by each rendered section.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit manifest budget diagnostics as machine-readable JSON.",
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
    """Emit the token-budgeted session manifest.

    Written to stdout as markdown so agents can pipe it directly into their
    context window, or humans can redirect it to a file for inspection.
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
