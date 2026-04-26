"""`cortex plan spawn <slug>` — scaffold a new active Plan from the bundled template.

Writes ``.cortex/plans/<slug>.md`` with seven-field frontmatter (Status,
Written, Author, Goal-hash, Updated-by, Cites) per SPEC § 3.4 and the
five required sections (Why grounding, Approach, Success Criteria, Work
items, Follow-ups deferred). The bundled template at
``src/cortex/_data/templates/plans/template.md`` is the source — projects
may override under ``.cortex/templates/plans/template.md`` for custom
prose.

Goal-hash is computed at spawn time from ``--title`` per SPEC § 4.9, so
``cortex doctor`` is green on the new file without a manual recompute step.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path

import click

from cortex.compat import require_compatible
from cortex.goal_hash import normalize_goal_hash

_DATE_PLACEHOLDER = "{{ YYYY-MM-DD }}"
_UPDATED_BY_PLACEHOLDER = "{{ YYYY-MM-DDTHH:MM human (created) }}"
_GOAL_HASH_PLACEHOLDER = "(recompute with cortex doctor)"
_CITES_LINE_RE = re.compile(r"^Cites: \{\{[^}]+\}\}$", re.MULTILINE)
_TITLE_LINE_RE = re.compile(r"^# \{\{[^}]+\}\}.*$", re.MULTILINE)
# Same shape as journal types — closes the path-traversal hole when ``slug``
# is interpolated into the filename.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _resolve_template(cortex_dir: Path) -> str:
    """Project override wins over the bundled template, mirroring journal draft."""
    project_template = cortex_dir / "templates" / "plans" / "template.md"
    if project_template.exists():
        return project_template.read_text()
    bundle = files("cortex._data").joinpath("templates", "plans", "template.md")
    if bundle.is_file():
        return bundle.read_text()
    raise FileNotFoundError("templates/plans/template.md")


def _detect_author() -> str:
    """Best-effort author label.

    ``$CORTEX_SESSION_ID`` lets agent runtimes claim a stable identity across
    spawns; absent that, fall back to ``human``. Matches the convention
    already in this repo's plans (e.g. ``claude-session-2026-04-25``).
    """
    import os

    session_id = os.environ.get("CORTEX_SESSION_ID")
    if session_id:
        return session_id
    return "human"


@click.command("spawn")
@click.argument("slug")
@click.option(
    "--title",
    "title",
    required=True,
    help="Plan title (drives the H1 and Goal-hash; required).",
)
@click.option(
    "--cites",
    "cites",
    default=None,
    help="Comma-separated initial citations (e.g. "
    "'doctrine/0001-why-cortex-exists, state.md § P0').",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def spawn_command(
    *,
    slug: str,
    title: str,
    cites: str | None,
    target_path: Path,
) -> None:
    """Scaffold a new active Plan at .cortex/plans/<SLUG>.md."""
    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    if not _SLUG_RE.match(slug):
        click.echo(
            f"error: invalid slug {slug!r}; slugs are lowercase identifiers "
            f"with optional dashes (e.g. `cortex-v0.3.0`, `init-ux-fixes`).",
            err=True,
        )
        sys.exit(2)

    # `plan spawn` is a writer; refuse on missing/unsupported SPEC_VERSION
    # per SPEC § 7. Same gate as `journal draft`.
    require_compatible(cortex_dir)

    try:
        template = _resolve_template(cortex_dir)
    except FileNotFoundError:
        click.echo(
            "error: bundled plan template missing — reinstall the cortex CLI.",
            err=True,
        )
        sys.exit(2)

    today_iso_date = datetime.now().date().isoformat()
    today_iso_minute = datetime.now().strftime("%Y-%m-%dT%H:%M")
    author = _detect_author()
    goal_hash = normalize_goal_hash(title)

    body = template
    body = body.replace(_DATE_PLACEHOLDER, today_iso_date)
    body = body.replace(
        _UPDATED_BY_PLACEHOLDER,
        f"{today_iso_minute} {author} (created via cortex plan spawn)",
    )
    body = body.replace(_GOAL_HASH_PLACEHOLDER, goal_hash)
    # Author line is "Author: human" by default in the template — replace with
    # the detected author when it isn't `human` so agent-spawned plans carry
    # the correct identity.
    if author != "human":
        body = body.replace("Author: human", f"Author: {author}", 1)

    body = _TITLE_LINE_RE.sub(f"# {title}", body, count=1)

    if cites is not None:
        cites_value = ", ".join(c.strip() for c in cites.split(",") if c.strip())
    else:
        cites_value = ""
    body = _CITES_LINE_RE.sub(f"Cites: {cites_value}", body, count=1)

    target = cortex_dir / "plans" / f"{slug}.md"
    if target.exists():
        click.echo(
            f"error: {target} already exists; pick a different slug or remove "
            f"the existing plan.",
            err=True,
        )
        sys.exit(2)

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Exclusive-create closes the TOCTOU race between the early
        # existence check and the write — mirrors the journal-draft fix.
        with target.open("x") as f:
            f.write(body)
    except FileExistsError:
        click.echo(
            f"error: {target} appeared between the existence check and the "
            f"write (race or duplicate run); not overwriting.",
            err=True,
        )
        sys.exit(2)

    click.echo(str(target))


@click.group("plan")
def plan_group() -> None:
    """Author and inspect Plans."""


plan_group.add_command(spawn_command)
