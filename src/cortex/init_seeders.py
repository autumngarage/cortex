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
from cortex.goal_hash import normalize_goal_hash

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


def _git_user_email(project_root: Path) -> str:
    """Best-effort git user.email lookup; falls back to "unknown".

    We deliberately avoid raising — init must not crash because the user
    hasn't configured git yet. The fallback string is what shows up in the
    generated Plan's ``Updated-by:`` field, which the user can edit.
    Reading from the project root respects per-project ``git config`` overrides.
    """
    import shutil
    import subprocess

    git_path = shutil.which("git")
    if git_path is None:
        return "unknown"
    try:
        completed = subprocess.run(
            [git_path, "-C", str(project_root), "config", "user.email"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"
    value = completed.stdout.strip()
    return value or "unknown"


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


_AUTO_IMPORT_DOCTRINE_FLOOR: int = 100
"""Auto-imported Doctrine entries start at 0100, not 0001.

Reserved range 0001-0099 is for **human-authored** Doctrine — the canonical
"why X exists" foundational entry the user is expected to write at
``doctrine/0001-why-<project>-exists.md`` (Next-steps prompt) plus any
other hand-authored Doctrine the user wants to reserve a low number for.
Auto-imports start past that range so the printed Next-steps guidance
("Author doctrine/0001-why-<project>-exists.md") never collides with a
freshly-imported entry.

Fix #4 from plans/init-ux-fixes-from-touchstone — the v0.2.3 numbering
allocated auto-imports starting at 0001, taking the slot the Next-steps
prompt told the user to author. The conflict was confusing on the
touchstone dogfood (the imported principles/README.md became
0001-readme.md while the user was simultaneously instructed to author
0001-why-<project>-exists.md).
"""


def _next_doctrine_id(doctrine_dir: Path) -> int:
    """Return the next 4-digit Doctrine ID for an auto-imported entry.

    Doctrine filenames are ``NNNN-<slug>.md`` per SPEC § 3.1. Auto-imports
    start at ``_AUTO_IMPORT_DOCTRINE_FLOOR`` (0100) so the 0001-0099 range
    stays reserved for human-authored Doctrine. Among existing imports,
    we pick the highest existing NNNN and add 1 to continue the sequence.
    Hand-authored entries below the floor never block auto-import
    numbering — they're tracked but the floor is the lower bound.
    """
    highest = 0
    if doctrine_dir.is_dir():
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
    # If existing IDs include any at or above the floor, continue from the
    # highest. Otherwise (empty doctrine_dir or only hand-authored entries
    # below the floor), start from the floor itself so we don't allocate
    # below 0100 for an auto-import.
    if highest >= _AUTO_IMPORT_DOCTRINE_FLOOR:
        return highest + 1
    return _AUTO_IMPORT_DOCTRINE_FLOOR


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

    Per SPEC § 3.1, valid ``Status:`` values are ``Proposed`` / ``Accepted``
    / ``Superseded-by N`` and valid ``Load-priority:`` values are
    ``default`` / ``always``. Imported entries land as ``Proposed`` because
    the user has not yet reviewed them as canonical Doctrine — they came
    in via import, not via the promotion queue, and ``cortex doctor``
    should surface them as proposals awaiting promotion. ``Load-priority:
    default`` keeps them out of the always-loaded session-start manifest
    until the user explicitly upgrades the entry.

    We deliberately use the YAML-frontmatter form (not the bold-inline form
    some older entries use) because it's the form ``cortex doctor`` validates
    most strictly and the form the templates ship with.
    """
    return (
        "---\n"
        "Status: Proposed\n"
        f"Date: {_today_iso()}\n"
        "Load-priority: default\n"
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


# --- Plan seeder ------------------------------------------------------------


def _plan_frontmatter(*, title: str, source_relative: str, project_root: Path) -> str:
    """Render frontmatter for an imported Plan entry.

    We populate Goal-hash from the source H1 (or filename) so
    ``cortex doctor``'s recompute check passes immediately, Updated-by
    from git config, and Cites with a placeholder pointing at the source
    file (per SPEC § 3.4 + validation.py, ``Cites:`` must be a non-empty
    scalar). Status defaults to ``active`` — the user can flip it to
    ``shipped|blocked|etc`` after import.

    The ``Cites:`` placeholder satisfies the non-empty-scalar contract
    while pointing the user at the source they need to flesh out into
    real grounding citations (doctrine/, state.md, or journal/ refs).
    Doctor will still warn on missing grounding citation in the body
    text until the user hand-authors the ``## Why (grounding)`` section,
    which is the intended forcing function — imported plans must be
    grounded before they're trusted.
    """
    today = _today_iso()
    return (
        "---\n"
        "Status: active\n"
        f"Written: {today}\n"
        "Author: human\n"
        f"Goal: {title}\n"
        f"Goal-hash: {normalize_goal_hash(title)}\n"
        "Updated-by:\n"
        f"  - {today} {_git_user_email(project_root)} (imported by cortex init)\n"
        f"Cites: imported from {source_relative} — hand-author citations to doctrine/, state.md, or journal/\n"
        f"Imported-from: {source_relative}\n"
        "---\n\n"
    )


def _plan_body(title: str, source_relative: str) -> str:
    """Render the imported Plan body — every required section is stubbed."""
    return f"""# {title}

> **Imported plan.** Hand-author this body from [`{source_relative}`](../../{source_relative}); cortex init only structures the file so `cortex doctor` validates the shape.

## Why (grounding)

Imported from [`{source_relative}`](../../{source_relative}). Hand-author the grounding here, citing relevant journal entries or doctrine (SPEC § 4.1).

## Approach

- [ ] Hand-author from `{source_relative}` — name the modules touched, the dependencies, the rough shape of the work.

## Success Criteria

- [ ] Hand-author measurable success criteria from `{source_relative}`. Cortex doctor enforces measurable signal per SPEC § 4.3 (numeric threshold, test/dashboard link, or code/path reference).

## Work items

- [ ] Hand-author from `{source_relative}`.

## Follow-ups (deferred)

- [ ] Hand-author from `{source_relative}` if applicable. Per SPEC § 4.2, every deferral resolves to another Plan or Journal entry in the same commit.

## Known limitations at exit

- [ ] Hand-author from `{source_relative}` if applicable.
"""


def seed_plan(
    project_root: Path,
    source: Path,
) -> Path | None:
    """Mint a single Plan from one source. Returns the written path or None if skipped.

    Returns None when an existing Plan already cites the same source via
    ``Imported-from:`` — keeps re-runs idempotent. Filename derives from the
    source stem so two imports of the same source produce stable names.
    """
    project_root = project_root.resolve()
    plans_dir = project_root / ".cortex" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    rel = source.resolve().relative_to(project_root).as_posix()
    if rel in _existing_imported_sources(plans_dir):
        return None

    title = _extract_h1(source) or source.stem.replace("-", " ").replace("_", " ").title()
    slug = _slugify(source.stem)
    filename = f"{slug}.md"
    target = plans_dir / filename
    # Defensive: if a hand-authored plan with this slug already exists,
    # disambiguate with `-imported` rather than overwriting.
    if target.exists():
        target = plans_dir / f"{slug}-imported.md"
    target.write_text(
        _plan_frontmatter(title=title, source_relative=rel, project_root=project_root)
        + _plan_body(title, rel)
    )
    return target


def seed_plans(
    project_root: Path,
    sources: Iterable[Path],
) -> list[Path]:
    """Mint Plans for each source. Returns the list of files actually written."""
    written: list[Path] = []
    for source in sources:
        result = seed_plan(project_root, source)
        if result is not None:
            written.append(result)
    return written
