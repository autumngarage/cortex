"""Shared Doctrine filename helpers."""

from __future__ import annotations

import re
from pathlib import Path

from cortex.frontmatter import parse_frontmatter

AUTO_IMPORT_DOCTRINE_FLOOR = 100
DOCTRINE_FILENAME_RE = re.compile(r"^(\d{4})-(.+)\.md$")
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def doctrine_number(path: Path) -> int | None:
    """Return the four-digit Doctrine filename prefix, if present."""
    match = DOCTRINE_FILENAME_RE.match(path.name)
    if match is None:
        return None
    return int(match.group(1))


def doctrine_slug(path: Path) -> str | None:
    """Return the slug portion of a Doctrine filename, if present."""
    match = DOCTRINE_FILENAME_RE.match(path.name)
    if match is None:
        return None
    return match.group(2)


def slugify(text: str) -> str:
    """Filename-safe slug: lowercase ASCII, dashes for separators."""
    lowered = text.lower()
    cleaned = SLUG_NON_ALNUM_RE.sub("-", lowered).strip("-")
    return cleaned or "untitled"


def extract_h1(path: Path) -> str | None:
    """Return the first Markdown H1 from ``path``, tolerating frontmatter."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    _, body = parse_frontmatter(text)
    match = H1_RE.search(body)
    if match is None:
        return None
    return match.group(1).strip()


def next_doctrine_number(doctrine_dir: Path) -> int:
    """Return the next Doctrine number, preserving the 0100 import floor."""
    highest = 0
    if doctrine_dir.is_dir():
        for entry in doctrine_dir.iterdir():
            if not entry.is_file() or entry.suffix != ".md":
                continue
            number = doctrine_number(entry)
            if number is not None:
                highest = max(highest, number)
    if highest >= AUTO_IMPORT_DOCTRINE_FLOOR:
        return highest + 1
    return AUTO_IMPORT_DOCTRINE_FLOOR
