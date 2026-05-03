"""Shared Doctrine filename helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path

from cortex.frontmatter import parse_frontmatter

AUTO_IMPORT_DOCTRINE_FLOOR = 100
DOCTRINE_FILENAME_RE = re.compile(r"^(\d{4})-(.+)\.md$")
H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
JOURNAL_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(.+)$")


@dataclass(frozen=True)
class DoctrinePromotion:
    """A rendered Doctrine promotion ready to write."""

    path: Path
    rel: str
    text: str


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
    lowered = (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
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


def source_slug(source: Path) -> str:
    """Return the promotion slug derived from a source Journal filename."""

    match = JOURNAL_DATE_PREFIX_RE.match(source.stem)
    if match is not None:
        return slugify(match.group(1))
    return slugify(source.stem)


def render_promoted_doctrine(
    *,
    cortex_dir: Path,
    source_path: Path,
    source_rel: str,
    cites: str | None,
    today: date | None = None,
) -> DoctrinePromotion:
    """Render a Doctrine entry promoted from ``source_path``.

    The filename invariant is ``NNNN-<short-slug>.md`` where ``NNNN`` is one
    greater than the highest existing Doctrine number and never below 0100.
    """

    doctrine_dir = cortex_dir / "doctrine"
    number = next_doctrine_number(doctrine_dir)
    short_slug = source_slug(source_path)
    filename = f"{number:04d}-{short_slug}.md"
    target = doctrine_dir / filename
    rel = f"doctrine/{filename.removesuffix('.md')}"
    title = _source_title(source_path, short_slug)
    written = (today or date.today()).isoformat()
    text = _render_candidate_template(
        cortex_dir=cortex_dir,
        number=number,
        title=title,
        source_rel=source_rel,
        cites=cites,
        written=written,
    )
    return DoctrinePromotion(path=target, rel=rel, text=text)


def write_doctrine_entry(promotion: DoctrinePromotion) -> None:
    """Write a Doctrine entry without overwriting an existing immutable entry."""

    promotion.path.parent.mkdir(parents=True, exist_ok=True)
    with promotion.path.open("x") as f:
        f.write(promotion.text)


def _source_title(source_path: Path, fallback_slug: str) -> str:
    h1 = extract_h1(source_path)
    if h1:
        return h1.removeprefix("#").strip()
    return fallback_slug.replace("-", " ").title()


def _resolve_candidate_template(cortex_dir: Path) -> str:
    project_template = cortex_dir / "templates" / "doctrine" / "candidate.md"
    if project_template.exists():
        return project_template.read_text()
    bundle = files("cortex._data").joinpath("templates", "doctrine", "candidate.md")
    if bundle.is_file():
        return bundle.read_text()
    raise FileNotFoundError("templates/doctrine/candidate.md")


def _render_candidate_template(
    *,
    cortex_dir: Path,
    number: int,
    title: str,
    source_rel: str,
    cites: str | None,
    written: str,
) -> str:
    template = _resolve_candidate_template(cortex_dir)
    summary = f"Promoted from {source_rel}."
    source_ref = source_rel.removesuffix(".md")
    replacements = {
        "{{ nnnn }}": f"{number:04d}",
        "{{ Title - active-voice claim }}": title,
        "{{ Title — active-voice claim }}": title,
        "{{ One-sentence claim in active voice. This is the summary that loads into context when an agent grep-hits this entry. Make it readable standalone. }}": summary,
        "{{ YYYY-MM-DD }}": written,
        "{{ journal/<date>-<slug> or plans/<slug> or — (direct authoring) }}": source_ref,
        "{{ journal/<date>-<slug> or plans/<slug> or - (direct authoring) }}": source_ref,
        "{{ touchstone/principles/<file>.md#<section> — omit if not applicable }}": "-",
        "{{ touchstone/principles/<file>.md#<section> - omit if not applicable }}": "-",
        "{{ always | default }}": "default",
        "{{ Cites }}": cites or "-",
        "{{ What situation or pattern produced this claim? What alternatives were weighed? Link to the supporting Journal entries, Plans, or Procedures. An editor reviewing this candidate should be able to judge from Context alone whether the claim generalizes. }}": f"Promotion source: {source_ref}.",
        "{{ We will / we won't — stated as a claim, not a recommendation. Include the specific boundary: what falls inside this decision and what falls outside. }}": title,
        "{{ We will / we won't - stated as a claim, not a recommendation. Include the specific boundary: what falls inside this decision and what falls outside. }}": title,
        "{{ ... }}": "See promotion source.",
        "{{ decisions or patterns this entry makes inadmissible }}": "See promotion source.",
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    rendered = rendered.replace("**Status:** Proposed", "**Status:** Accepted")
    if "**Cites:**" not in rendered:
        rendered = rendered.replace(
            f"**Promoted-from:** {source_ref}\n",
            f"**Promoted-from:** {source_ref}\n**Cites:** {cites or '-'}\n",
        )
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered
