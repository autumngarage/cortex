"""`cortex journal draft <type>` — scaffold a Journal entry from a template.

Templates resolve in this order:
1. ``<project>/.cortex/templates/journal/<type>.md`` (project override)
2. Bundled ``src/cortex/_data/templates/journal/<type>.md`` (shipped with the CLI)

Pre-fills today's date in the ``**Date:**`` field, optionally rewrites the
H1 from ``--title``, and appends an HTML-comment block with recent
``git log`` and ``gh pr view`` context to inform the user as they fill in
the body. Default flow opens ``$EDITOR`` on a temp file and moves the result
to ``.cortex/journal/<filename>`` on editor close; ``--no-edit`` writes
directly and prints the path.

``gh`` is optional. If it's not installed, not authenticated, or there is
no open PR for the current branch, the PR-context block degrades to a note;
the command never blocks on missing optional tooling.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime
from importlib.resources import files
from pathlib import Path

import click

from cortex.compat import warn_if_incompatible

_DATE_PLACEHOLDER = "{{ YYYY-MM-DD }}"
_H1_TEMPLATE_RE = re.compile(r"^# \{\{[^}]+\}\}.*$", re.MULTILINE)
_SLUG_MAX_CHARS = 50


def _normalize_slug(text: str) -> str:
    """Lowercase, strip non-[a-z0-9 -], collapse spaces to dashes, cap at 50 chars."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    s = re.sub(r"-+", "-", s)
    return s[:_SLUG_MAX_CHARS] or "untitled"


def _resolve_template(cortex_dir: Path, journal_type: str) -> str:
    """Return template text for ``journal_type``; raises FileNotFoundError if absent.

    Project override under ``.cortex/templates/journal/`` wins over the
    bundled fallback so customizations don't get masked.
    """
    project_template = cortex_dir / "templates" / "journal" / f"{journal_type}.md"
    if project_template.exists():
        return project_template.read_text()
    bundle_resource = files("cortex._data").joinpath("templates", "journal", f"{journal_type}.md")
    if bundle_resource.is_file():
        return bundle_resource.read_text()
    raise FileNotFoundError(journal_type)


def _list_known_types() -> list[str]:
    """Return bundled journal-template type names (filename stems)."""
    bundle_dir = files("cortex._data").joinpath("templates", "journal")
    if not bundle_dir.is_dir():
        return []
    return sorted(
        p.name.removesuffix(".md")
        for p in bundle_dir.iterdir()
        if p.name.endswith(".md")
    )


def _gather_git_context(project_root: Path) -> list[str]:
    """Return up to 5 recent commit ``%h %s`` lines, or empty on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "log", "-5", "--pretty=format:%h %s"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _gather_gh_pr_context(project_root: Path) -> tuple[str | None, str | None]:
    """Return ``(pr_text, degradation_reason)``.

    On success ``pr_text`` is non-None and ``degradation_reason`` is None.
    On any non-success path ``pr_text`` is None and ``degradation_reason``
    explains why so the user sees something specific in the context block.
    """
    if shutil.which("gh") is None:
        return None, "gh not installed"
    auth = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    if auth.returncode != 0:
        return None, "gh not authenticated (run `gh auth login`)"
    pr = subprocess.run(
        ["gh", "pr", "view", "--json", "number,title,url"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if pr.returncode != 0 or not pr.stdout.strip():
        return None, "no open PR for this branch"
    return pr.stdout.strip(), None


def _render_context_block(
    commits: list[str], pr_text: str | None, gh_reason: str | None
) -> str:
    """Render the auto-context HTML comment appended to the draft body."""
    lines = [
        "",
        "<!--",
        "Context auto-pulled at draft time. Remove this block before saving.",
        "",
    ]
    if commits:
        lines.append("Recent commits:")
        lines.extend(f"- {c}" for c in commits)
    else:
        lines.append("Recent commits: (git log unavailable)")
    lines.append("")
    if pr_text:
        lines.append("Active-branch PR:")
        lines.append(pr_text)
    else:
        lines.append(f"Active-branch PR: ({gh_reason or 'unavailable'})")
    lines.append("-->")
    lines.append("")
    return "\n".join(lines)


@click.command("draft")
@click.argument("journal_type")
@click.option(
    "--title",
    "title",
    default=None,
    help="Override the H1 title; also seeds the slug if --slug is omitted.",
)
@click.option(
    "--slug",
    "slug_override",
    default=None,
    help="Override the filename slug; defaults to a normalization of --title.",
)
@click.option(
    "--no-edit",
    "no_edit",
    is_flag=True,
    default=False,
    help="Write directly to .cortex/journal/ without opening $EDITOR.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def draft_command(
    *,
    journal_type: str,
    title: str | None,
    slug_override: str | None,
    no_edit: bool,
    target_path: Path,
) -> None:
    """Scaffold a Journal entry of TYPE (e.g. decision, release, incident)."""
    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)
    warn_if_incompatible(cortex_dir)

    try:
        template = _resolve_template(cortex_dir, journal_type)
    except FileNotFoundError:
        known = _list_known_types()
        listing = ", ".join(known) if known else "(no bundled templates found)"
        click.echo(
            f"error: no template for journal type {journal_type!r}.\n"
            f"  Available types: {listing}\n"
            f"  Add a project override at .cortex/templates/journal/{journal_type}.md "
            f"to define a new type.",
            err=True,
        )
        sys.exit(2)

    today = date.today().isoformat()
    body = template.replace(_DATE_PLACEHOLDER, today)
    if title is not None:
        body = _H1_TEMPLATE_RE.sub(f"# {title}", body, count=1)

    if slug_override:
        slug = _normalize_slug(slug_override)
    elif title:
        slug = _normalize_slug(title)
    else:
        # Use the type + HHMM so multiple drafts of the same type on the
        # same day don't collide before the user gives them real names.
        slug = f"{journal_type}-{datetime.now().strftime('%H%M')}"

    filename = f"{today}-{slug}.md"
    target = cortex_dir / "journal" / filename
    if target.exists():
        click.echo(
            f"error: {target} already exists; pass --slug to differentiate "
            f"or remove the existing entry.",
            err=True,
        )
        sys.exit(2)

    commits = _gather_git_context(project_root)
    pr_text, gh_reason = _gather_gh_pr_context(project_root)
    body += _render_context_block(commits, pr_text, gh_reason)

    if no_edit:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
        click.echo(str(target))
        return

    editor = os.environ.get("EDITOR") or shutil.which("vi") or shutil.which("nano")
    if editor is None:
        click.echo(
            "error: $EDITOR is unset and neither `vi` nor `nano` is on PATH. "
            "Set $EDITOR or pass --no-edit.",
            err=True,
        )
        sys.exit(2)

    fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix=f"cortex-{journal_type}-")
    tmp = Path(tmp_path)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        result = subprocess.run([editor, str(tmp)], check=False)
        if result.returncode != 0:
            click.echo(
                f"error: editor exited with code {result.returncode}; draft "
                f"preserved at {tmp}.",
                err=True,
            )
            sys.exit(2)
        edited = tmp.read_text()
    finally:
        if tmp.exists():
            tmp.unlink()

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(edited)
    click.echo(str(target))


@click.group("journal")
def journal_group() -> None:
    """Author Journal entries from templates."""


journal_group.add_command(draft_command)
