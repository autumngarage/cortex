"""`cortex promote <id>` — promote a Journal candidate into Doctrine.

Promotion depends on ``.cortex/.index.json``, which is populated by the v0.6.0
lifecycle layer. Until that ships, this command is an honest stub that tells
the user what's missing instead of failing silently.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cortex.compat import warn_if_incompatible


@click.command("promote")
@click.argument("candidate_id")
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def promote_command(*, candidate_id: str, target_path: Path) -> None:
    """Promote a queued candidate into a new Doctrine entry.

    This slice only surfaces the state of the promotion queue — the actual
    promotion write path lands alongside the v0.6.0 lifecycle commands that
    populate ``.cortex/.index.json``.
    """
    cortex_dir = Path(target_path).resolve() / ".cortex"
    if not cortex_dir.exists():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    warn_if_incompatible(cortex_dir)

    index_path = cortex_dir / ".index.json"
    if not index_path.exists():
        click.echo(
            f"error: `.cortex/.index.json` is absent; the promotion queue is not "
            f"populated yet. v0.6.0 lifecycle commands will emit the "
            f"index. Cannot promote {candidate_id!r}.",
            err=True,
        )
        sys.exit(2)

    try:
        data = json.loads(index_path.read_text())
    except json.JSONDecodeError as exc:
        click.echo(
            f"error: `.cortex/.index.json` is not valid JSON ({exc}).",
            err=True,
        )
        sys.exit(2)

    if not isinstance(data, dict) or (
        "candidates" not in data and "promotion_queue" not in data
    ):
        click.echo(
            "error: `.cortex/.index.json` is malformed (missing top-level "
            "`candidates`). Repair or regenerate before promoting.",
            err=True,
        )
        sys.exit(2)
    queue = data["candidates"] if "candidates" in data else data["promotion_queue"]
    if not isinstance(queue, list):
        click.echo(
            "error: `.cortex/.index.json` is malformed (`candidates` is "
            "not a list). Repair or regenerate before promoting.",
            err=True,
        )
        sys.exit(2)
    match = next(
        (c for c in queue if isinstance(c, dict) and c.get("id") == candidate_id),
        None,
    )
    if match is None:
        click.echo(
            f"error: no promotion candidate with id {candidate_id!r} in queue.",
            err=True,
        )
        sys.exit(2)

    click.echo(
        f"note: candidate {candidate_id!r} found (state={match.get('state')!r}) but the "
        "promotion writer is not yet implemented. Run `cortex doctor` for validation "
        "and open an issue when you hit this path in a real project; tracked as a "
        "v0.6.0 follow-up to the `.index.json` lifecycle commands."
    )
    sys.exit(3)
