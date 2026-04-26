"""Shared spec-version compatibility checks.

SPEC § 7: "Readers encountering an unknown major version should refuse to
write and warn on read."

This module exposes both flavors so each command picks the right one:

- :func:`warn_if_incompatible` — non-fatal warning, used by readers
  (``manifest``, ``grep``, ``status``).
- :func:`require_compatible` — refuses with exit 2, used by writers
  (``journal draft`` and any future write-path commands).

``init`` does not use either — it creates ``SPEC_VERSION`` rather than
consuming an existing one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cortex import SUPPORTED_SPEC_VERSIONS


def _declared_major_minor(cortex_dir: Path) -> tuple[Path, str | None]:
    """Return (path, declared_major_minor_or_None_if_missing).

    The major.minor extraction strips a ``-dev`` suffix and keeps only the
    first two dotted parts, matching how SUPPORTED_SPEC_VERSIONS is shaped.
    """
    spec_version_file = cortex_dir / "SPEC_VERSION"
    if not spec_version_file.exists():
        return spec_version_file, None
    declared = spec_version_file.read_text().strip()
    return spec_version_file, ".".join(declared.split("-", 1)[0].split(".")[:2])


def warn_if_incompatible(cortex_dir: Path) -> None:
    """Print a stderr warning when ``.cortex/SPEC_VERSION`` is missing or unsupported.

    Never raises; readers must keep operating so users can at least run
    ``cortex doctor`` to diagnose the store.
    """
    spec_version_file, major_minor = _declared_major_minor(cortex_dir)
    if major_minor is None:
        click.echo(
            f"warning: {spec_version_file} missing; reading without compatibility check. "
            "Run `cortex doctor` for details.",
            err=True,
        )
        return
    if major_minor not in SUPPORTED_SPEC_VERSIONS:
        declared = spec_version_file.read_text().strip()
        click.echo(
            f"warning: `.cortex/SPEC_VERSION` is {declared!r}; this CLI supports "
            f"{', '.join(SUPPORTED_SPEC_VERSIONS)}. Output may miss or misparse "
            "fields introduced or removed in other versions.",
            err=True,
        )


def require_compatible(cortex_dir: Path) -> None:
    """Exit 2 when ``.cortex/SPEC_VERSION`` is missing or unsupported.

    SPEC § 7 mandates that writers refuse incompatible stores rather than
    appending entries that may use unrecognized fields. Used by ``journal
    draft`` and any future write-path commands. Readers should keep using
    :func:`warn_if_incompatible` so diagnostic commands stay usable.
    """
    spec_version_file, major_minor = _declared_major_minor(cortex_dir)
    if major_minor is None:
        click.echo(
            f"error: {spec_version_file} missing; refusing to write to a store "
            f"of unknown spec version. Run `cortex init` (in an empty directory) "
            f"or write the version manually.",
            err=True,
        )
        sys.exit(2)
    if major_minor not in SUPPORTED_SPEC_VERSIONS:
        declared = spec_version_file.read_text().strip()
        click.echo(
            f"error: `.cortex/SPEC_VERSION` is {declared!r}; this CLI supports "
            f"{', '.join(SUPPORTED_SPEC_VERSIONS)}. Refusing to write — "
            f"upgrade the CLI or migrate the store before continuing.",
            err=True,
        )
        sys.exit(2)
