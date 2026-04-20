"""Seeders for `cortex init` — turn scan findings into ``.cortex/`` entries.

Each seeder takes a source file (already classified by ``init_scan``) and
writes one entry into the matching layer of ``.cortex/``. The brief's core
rules apply throughout:

- **Mirror source shape.** One source file → one entry. We never split a
  multi-section principles doc into N Doctrine entries; the source remains
  canonical and the imported entry's job is only to make the source visible
  to ``cortex grep`` and the promotion queue.
- **Cite, never hallucinate.** Every imported entry carries an
  ``Imported-from:`` frontmatter field pointing at the relative source path.
  Plans extract what's structurable (the H1 → ``Goal:``) and stub what isn't
  (Success Criteria becomes a ``[ ] Hand-author from <path>`` checklist).
- **Don't backfill the Journal.** This module deliberately has no journal
  seeder. CHANGELOGs and journal/*.md files are time-anchored; importing
  them into Cortex Journal would lie about when events actually happened
  (Protocol § 4.1 — Journal is append-only AND time-anchored).
- **Idempotent.** Re-running init on an already-imported repo never
  duplicates an entry. We detect duplicates by ``Imported-from:`` value:
  if any existing Doctrine/Plan entry already cites the same source, we
  skip without prompting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from cortex.frontmatter import parse_frontmatter

# --- Shared helpers ---------------------------------------------------------


_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_DOCTRINE_FILENAME_RE = re.compile(r"^(\d{4})-")
_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _slugify(text: str) -> str:
    """Filename-safe slug: lowercase ASCII, dashes for separators, no leading/trailing dashes."""
    lowered = text.lower()
    cleaned = _SLUG_NON_ALNUM_RE.sub("-", lowered).strip("-")
    return cleaned or "untitled"


def _today_iso() -> str:
    """Today's date as YYYY-MM-DD — used in frontmatter ``Date:`` fields.

    We use date.today() (not a UTC timestamp) because Doctrine and Plan
    frontmatter store calendar dates per SPEC §§ 3.1 and 3.4, not instants.
    """
    return date.today().isoformat()


def _extract_h1(path: Path) -> str | None:
    """Return the first H1 from the source file, or None if absent.

    We tolerate frontmatter fences before the H1; the H1 must still be the
    *first* H1 in the body text. Returns the title text (without the ``#``)
    stripped of trailing whitespace.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    _, body = parse_frontmatter(text)
    match = _H1_RE.search(body)
    if match is None:
        return None
    return match.group(1).strip()


def _existing_imported_sources(cortex_layer_dir: Path) -> set[str]:
    """Read every *.md in the layer dir and collect existing ``Imported-from:`` values.

    Used by the seeders to skip sources that have already been imported,
    so re-running init never duplicates an entry. Comparison is by relative
    POSIX path string (the same shape we write into the frontmatter).
    """
    imported: set[str] = set()
    if not cortex_layer_dir.is_dir():
        return imported
    for entry in cortex_layer_dir.glob("*.md"):
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, _ = parse_frontmatter(text)
        value = frontmatter.get("Imported-from")
        if isinstance(value, str) and value:
            imported.add(value)
    return imported


# --- Doctrine seeder --------------------------------------------------------


def _next_doctrine_id(doctrine_dir: Path) -> int:
    """Return the next 4-digit Doctrine ID, scanning existing filenames.

    Doctrine filenames are ``NNNN-<slug>.md`` per SPEC § 3.1. We pick the
    highest existing NNNN and add 1 so imports continue the numbering
    sequence rather than colliding with existing entries.
    """
    highest = 0
    if not doctrine_dir.is_dir():
        return 1
    for entry in doctrine_dir.iterdir():
        if not entry.is_file() or entry.suffix != ".md":
            continue
        match = _DOCTRINE_FILENAME_RE.match(entry.name)
        if match is None:
            continue
        try:
            value = int(match.group(1))
        except ValueError:
            continue
        highest = max(highest, value)
    return highest + 1


def _doctrine_body(title: str, source_relative: str) -> str:
    """Render the imported Doctrine entry body.

    The body deliberately does NOT extract the source's content — the source
    remains canonical (mirror source shape principle). This stub gives
    ``cortex grep`` and the promotion queue something to reason about while
    pointing the reader at the real text.
    """
    return f"""# {title}

> Imported by `cortex init` from `{source_relative}`. The source remains the canonical text — this Doctrine entry exists so cortex's promotion queue and `cortex grep` can reason about it.

See [`{source_relative}`](../../{source_relative}) for the full content.

<!--
When this Doctrine evolves, supersede this entry by writing a new entry with
`supersedes: <NNNN>` frontmatter (per protocol § 4.2). Doctrine is
immutable — never edit this file in place; write a successor instead.
-->
"""


def _doctrine_frontmatter(*, source_relative: str) -> str:
    """Render frontmatter for an imported Doctrine entry.

    We use the YAML-frontmatter form (not the bold-inline form some
    older entries use) because it's the form ``cortex doctor`` validates
    most strictly and the form the templates ship with.
    """
    return (
        "---\n"
        "Status: Active\n"
        f"Date: {_today_iso()}\n"
        "Load-priority: contextual\n"
        f"Imported-from: {source_relative}\n"
        "---\n\n"
    )


def seed_doctrine(
    project_root: Path,
    sources: Iterable[Path],
) -> list[Path]:
    """Mint one Doctrine entry per source file. Returns the list of files written.

    Idempotency: any source whose relative path already appears in an
    existing Doctrine entry's ``Imported-from:`` is skipped silently. This
    makes re-running init safe (the brief's "today's behavior preserved"
    contract for ``--force``).
    """
    project_root = project_root.resolve()
    doctrine_dir = project_root / ".cortex" / "doctrine"
    doctrine_dir.mkdir(parents=True, exist_ok=True)
    already_imported = _existing_imported_sources(doctrine_dir)
    next_id = _next_doctrine_id(doctrine_dir)

    written: list[Path] = []
    for source in sources:
        rel = source.resolve().relative_to(project_root).as_posix()
        if rel in already_imported:
            continue
        title = _extract_h1(source) or source.stem.replace("-", " ").replace("_", " ").title()
        slug = _slugify(source.stem)
        filename = f"{next_id:04d}-{slug}.md"
        target = doctrine_dir / filename
        # Defensive: if a filename collision exists despite the next_id walk
        # (e.g. a hand-authored entry without an Imported-from frontmatter),
        # bump the id until we find a free slot. Better than overwriting.
        while target.exists():
            next_id += 1
            filename = f"{next_id:04d}-{slug}.md"
            target = doctrine_dir / filename
        target.write_text(_doctrine_frontmatter(source_relative=rel) + _doctrine_body(title, rel))
        written.append(target)
        next_id += 1

    return written
