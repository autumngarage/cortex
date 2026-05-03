"""`cortex promote <id>` — promote a Journal candidate into Doctrine."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import click

from cortex.commands.journal import (
    _gather_gh_pr_context,
    _gather_git_context,
    _normalize_slug,
    _render_context_block,
    _resolve_template,
)
from cortex.compat import require_compatible
from cortex.doctrine import (
    DoctrinePromotion,
    promoted_doctrine_for_source,
    render_promoted_doctrine,
    write_doctrine_entry,
)
from cortex.frontmatter import FrontmatterValue, parse_frontmatter
from cortex.index import read_index, write_index


@dataclass(frozen=True)
class PromotionJournal:
    path: Path
    text: str


@click.command("promote")
@click.argument("candidate_id")
@click.option(
    "--force",
    "force",
    is_flag=True,
    default=False,
    help="Promote even when the candidate already has promoted_to set.",
)
@click.option(
    "--yes",
    "yes",
    is_flag=True,
    default=False,
    help="Skip the interactive confirmation required by --force.",
)
@click.option(
    "--dry-run",
    "dry_run",
    is_flag=True,
    default=False,
    help="Print the planned promotion without writing files.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def promote_command(
    *,
    candidate_id: str,
    force: bool,
    yes: bool,
    dry_run: bool,
    target_path: Path,
) -> None:
    """Promote a queued candidate into a new Doctrine entry."""

    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.exists():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    require_compatible(cortex_dir)

    index_path = cortex_dir / ".index.json"
    try:
        data = read_index(index_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        click.echo(
            f"error: could not read `.cortex/.index.json`: {exc}. "
            "Run `cortex refresh-index` before promoting.",
            err=True,
        )
        sys.exit(2)
    if not data:
        click.echo(
            "error: `.cortex/.index.json` is absent; run `cortex refresh-index` "
            f"before promoting {candidate_id!r}.",
            err=True,
        )
        sys.exit(2)

    queue = data.get("candidates")
    if not isinstance(queue, list):
        click.echo(
            "error: `.cortex/.index.json` is malformed (`candidates` is not a list). "
            "Repair or regenerate before promoting.",
            err=True,
        )
        sys.exit(2)

    candidate = _find_candidate(queue, candidate_id)
    if candidate is None:
        click.echo(
            f"error: no promotion candidate with id {candidate_id!r} in `.cortex/.index.json`.",
            err=True,
        )
        sys.exit(1)

    source_path, source_rel = _candidate_source(project_root, candidate)
    try:
        existing_promotion = promoted_doctrine_for_source(cortex_dir, source_rel)
    except OSError as exc:
        click.echo(f"error: could not scan existing Doctrine promotions: {exc}", err=True)
        sys.exit(2)

    promoted_to = candidate.get("promoted_to") or existing_promotion
    if promoted_to and not force:
        click.echo(
            f"error: candidate {candidate_id!r} is already promoted to {promoted_to}; "
            "rerun with --force to promote again.",
            err=True,
        )
        sys.exit(1)
    if promoted_to and force and not yes:
        click.confirm(
            f"Candidate {candidate_id!r} is already promoted to {promoted_to}. "
            "Promote again?",
            abort=True,
        )

    source_text = _read_source(source_path)
    cites = _source_cites(source_text)
    doctrine = render_promoted_doctrine(
        cortex_dir=cortex_dir,
        source_path=source_path,
        source_rel=source_rel,
        cites=cites,
    )
    journal = _render_promotion_journal(
        project_root=project_root,
        cortex_dir=cortex_dir,
        candidate_id=candidate_id,
        source_rel=source_rel,
        doctrine=doctrine,
    )

    if doctrine.path.exists():
        click.echo(
            f"error: {doctrine.path} already exists; Doctrine is immutable, not overwriting.",
            err=True,
        )
        sys.exit(2)
    if journal.path.exists():
        click.echo(
            f"error: {journal.path} already exists; Journal is append-only, not overwriting.",
            err=True,
        )
        sys.exit(2)

    if dry_run:
        click.echo(f"would write: {doctrine.path}")
        click.echo(f"would write: {journal.path}")
        click.echo(
            f"would update: {index_path} ({candidate_id!r} promoted_to={doctrine.rel})"
        )
        return

    created: list[Path] = []
    try:
        write_doctrine_entry(doctrine)
        created.append(doctrine.path)
        _write_journal(journal)
        created.append(journal.path)
        _mark_promoted(data, candidate_id, doctrine.rel)
        write_index(index_path, data)
    except FileExistsError as exc:
        _rollback_created(created)
        click.echo(f"error: refusing to overwrite existing Cortex entry: {exc}", err=True)
        sys.exit(2)
    except OSError as exc:
        _rollback_created(created)
        click.echo(f"error: promotion write failed: {exc}", err=True)
        sys.exit(2)
    except ValueError as exc:
        _rollback_created(created)
        click.echo(f"error: promotion index update failed: {exc}", err=True)
        sys.exit(2)

    click.echo(str(doctrine.path))
    click.echo(str(journal.path))


def _find_candidate(queue: list[Any], candidate_id: str) -> dict[str, Any] | None:
    for candidate in queue:
        if isinstance(candidate, dict) and candidate.get("id") == candidate_id:
            return candidate
    return None


def _mark_promoted(data: dict[str, Any], candidate_id: str, promoted_to: str) -> None:
    queue = data["candidates"]
    for candidate in queue:
        if isinstance(candidate, dict) and candidate.get("id") == candidate_id:
            candidate["promoted_to"] = promoted_to
            return
    raise ValueError(f"candidate {candidate_id!r} disappeared before index update")


def _candidate_source(project_root: Path, candidate: dict[str, Any]) -> tuple[Path, str]:
    raw = candidate.get("source")
    if not isinstance(raw, str) or not raw.strip():
        click.echo(
            "error: promotion candidate is malformed (missing `source`). "
            "Run `cortex refresh-index` before promoting.",
            err=True,
        )
        sys.exit(2)

    rel = raw.strip()
    if rel.startswith(".cortex/"):
        path = project_root / rel
        source_ref = rel.removeprefix(".cortex/").removesuffix(".md")
    else:
        path = project_root / ".cortex" / rel
        source_ref = rel.removesuffix(".md")
    return path, source_ref


def _read_source(source_path: Path) -> str:
    try:
        return source_path.read_text()
    except OSError as exc:
        click.echo(f"error: could not read promotion source {source_path}: {exc}", err=True)
        sys.exit(2)


def _source_cites(text: str) -> str | None:
    frontmatter, _body = parse_frontmatter(text)
    value = _field_value(frontmatter, "Cites")
    if value:
        return value
    for line in text.splitlines()[:60]:
        stripped = line.strip()
        if stripped.startswith("**Cites:**"):
            value = stripped.removeprefix("**Cites:**").strip()
            return value or None
    return None


def _field_value(frontmatter: dict[str, FrontmatterValue], field: str) -> str | None:
    value = frontmatter.get(field)
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        return ", ".join(item for item in value if item.strip()) or None
    return None


def _render_promotion_journal(
    *,
    project_root: Path,
    cortex_dir: Path,
    candidate_id: str,
    source_rel: str,
    doctrine: DoctrinePromotion,
) -> PromotionJournal:
    try:
        template = _resolve_template(cortex_dir, "promotion")
    except FileNotFoundError:
        click.echo(
            "error: no template for journal type 'promotion'. Add "
            ".cortex/templates/journal/promotion.md or reinstall Cortex templates.",
            err=True,
        )
        sys.exit(2)

    today = date.today().isoformat()
    title = f"Promoted {candidate_id} to {doctrine.rel}"
    body = template.replace("{{ YYYY-MM-DD }}", today)
    body = body.replace("{{ Title }}", title)
    body = body.replace("{{ Cites }}", f"{source_rel}, {doctrine.rel}")
    body = body.replace("{{ Source }}", source_rel)
    body = body.replace("{{ Doctrine }}", doctrine.rel)
    body = body.replace("{{ Summary }}", f"{source_rel} was promoted to {doctrine.rel}.")
    body += _render_context_block(
        _gather_git_context(project_root),
        *_gather_gh_pr_context(project_root),
    )

    slug = _normalize_slug(f"promotion-{doctrine.path.stem}")
    target = cortex_dir / "journal" / f"{today}-{slug}.md"
    return PromotionJournal(path=target, text=body)


def _write_journal(journal: PromotionJournal) -> None:
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    with journal.path.open("x") as f:
        f.write(journal.text)


def _rollback_created(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            click.echo(
                f"warning: promotion rollback could not remove {path}: {exc}",
                err=True,
            )
