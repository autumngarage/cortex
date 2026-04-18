"""Shared spec-version compatibility check for reader commands.

SPEC § 7: "Readers encountering an unknown major version should refuse to
write and warn on read." All read-only commands (`manifest`, `grep`, and
future query commands) route through :func:`warn_if_incompatible` so the
reader contract is enforced in one place.
"""

from __future__ import annotations

from pathlib import Path

import click

from cortex import SUPPORTED_SPEC_VERSIONS


def warn_if_incompatible(cortex_dir: Path) -> None:
    """Print a stderr warning when `.cortex/SPEC_VERSION` is missing or unsupported.

    Never raises; readers must keep operating so users can at least run
    ``cortex doctor`` to diagnose the store. Writers should gate on this
    separately if stricter behavior is desired.
    """
    spec_version_file = cortex_dir / "SPEC_VERSION"
    if not spec_version_file.exists():
        click.echo(
            f"warning: {spec_version_file} missing; reading without compatibility check. "
            "Run `cortex doctor` for details.",
            err=True,
        )
        return

    declared = spec_version_file.read_text().strip()
    declared_major_minor = ".".join(declared.split("-", 1)[0].split(".")[:2])
    if declared_major_minor not in SUPPORTED_SPEC_VERSIONS:
        click.echo(
            f"warning: `.cortex/SPEC_VERSION` is {declared!r}; this CLI supports "
            f"{', '.join(SUPPORTED_SPEC_VERSIONS)}. Output may miss or misparse "
            "fields introduced or removed in other versions.",
            err=True,
        )
