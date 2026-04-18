"""`cortex manifest` — print the session-start manifest per Protocol § 1."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cortex import SUPPORTED_SPEC_VERSIONS
from cortex.manifest import build_manifest


@click.command("manifest")
@click.option(
    "--budget",
    type=int,
    default=8000,
    show_default=True,
    help="Approximate token budget for the manifest (≈4 chars/token). Below 2000 "
    "the manifest degrades to state-only; at or above 15000 the Journal window "
    "widens from 72h to 7d.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def manifest_command(*, budget: int, target_path: Path) -> None:
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

    # SPEC § 7: readers warn when the store declares an unsupported major.
    spec_version_file = cortex_dir / "SPEC_VERSION"
    if not spec_version_file.exists():
        click.echo(
            f"warning: {spec_version_file} missing; reading without compatibility check. "
            "Run `cortex doctor` for details.",
            err=True,
        )
    else:
        declared = spec_version_file.read_text().strip()
        declared_major_minor = ".".join(declared.split("-", 1)[0].split(".")[:2])
        if declared_major_minor not in SUPPORTED_SPEC_VERSIONS:
            click.echo(
                f"warning: `.cortex/SPEC_VERSION` is {declared!r}; this CLI supports "
                f"{', '.join(SUPPORTED_SPEC_VERSIONS)}. Manifest may miss or misparse "
                "fields introduced or removed in other versions.",
                err=True,
            )

    manifest = build_manifest(target_path, budget)
    click.echo(manifest.render(), nl=False)
