"""Doctrine pack seeding for ``cortex init --seed-from``."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from cortex.doctrine import (
    DOCTRINE_FILENAME_RE,
    doctrine_number,
    doctrine_slug,
    extract_h1,
    next_doctrine_number,
    slugify,
)


@dataclass(frozen=True)
class SeedResult:
    copied: list[Path]
    skipped: list[Path]
    conflicts: list[Path]


class SeedSourceError(ValueError):
    """Raised when the requested seed source is not a directory."""


class SeedConflictError(RuntimeError):
    """Raised when default seeding would overwrite existing Doctrine."""

    def __init__(self, result: SeedResult) -> None:
        self.result = result
        super().__init__("doctrine seed conflicts")


@dataclass(frozen=True)
class _SeedPlan:
    source: Path
    destination: Path
    conflict: Path | None


def seed_doctrine_from(
    source_dir: Path,
    project_root: Path,
    *,
    merge_mode: str | None,
) -> SeedResult:
    """Copy one-level Markdown Doctrine pack entries into a project's Doctrine layer."""
    resolved_source = source_dir.expanduser().resolve()
    if not resolved_source.is_dir():
        raise SeedSourceError(f"seed source is not a directory: {resolved_source}")
    if merge_mode not in (None, "skip-existing"):
        raise ValueError(f"unsupported merge mode: {merge_mode}")

    doctrine_dir = project_root.resolve() / ".cortex" / "doctrine"
    doctrine_dir.mkdir(parents=True, exist_ok=True)

    sources = sorted(
        p for p in resolved_source.glob("*.md") if p.is_file() and not p.name.startswith("_")
    )
    if not sources:
        return SeedResult(copied=[], skipped=[], conflicts=[])

    plans = _plan_seed(sources, doctrine_dir)
    conflicts = [plan.conflict for plan in plans if plan.conflict is not None]
    if conflicts and merge_mode is None:
        raise SeedConflictError(SeedResult(copied=[], skipped=[], conflicts=conflicts))

    copied: list[Path] = []
    skipped: list[Path] = []
    for plan in plans:
        if plan.conflict is not None:
            skipped.append(plan.conflict)
            continue
        shutil.copyfile(plan.source, plan.destination)
        copied.append(plan.destination)

    return SeedResult(copied=copied, skipped=skipped, conflicts=conflicts)


def _plan_seed(sources: list[Path], doctrine_dir: Path) -> list[_SeedPlan]:
    existing_by_number: dict[int, Path] = {}
    existing_by_slug: dict[str, Path] = {}
    for entry in sorted(doctrine_dir.glob("*.md")):
        number = doctrine_number(entry)
        if number is not None:
            existing_by_number.setdefault(number, entry)
        slug = doctrine_slug(entry)
        if slug is not None:
            existing_by_slug.setdefault(slug, entry)

    allocated_numbers = set(existing_by_number)
    allocated_names = {entry.name for entry in doctrine_dir.glob("*.md")}
    planned_by_number: dict[int, Path] = {}
    next_number = next_doctrine_number(doctrine_dir)
    plans: list[_SeedPlan] = []

    for source in sources:
        numbered = DOCTRINE_FILENAME_RE.match(source.name)
        if numbered is not None:
            requested_number = int(numbered.group(1))
            conflict = existing_by_number.get(requested_number) or planned_by_number.get(
                requested_number
            )
            destination = doctrine_dir / source.name
            if conflict is None and destination.name in allocated_names:
                conflict = destination
            plans.append(_SeedPlan(source=source, destination=destination, conflict=conflict))
            allocated_numbers.add(requested_number)
            allocated_names.add(destination.name)
            planned_by_number.setdefault(requested_number, destination)
            continue

        slug = slugify(extract_h1(source) or source.stem)
        existing_same_slug = existing_by_slug.get(slug)
        if existing_same_slug is not None:
            plans.append(
                _SeedPlan(
                    source=source, destination=existing_same_slug, conflict=existing_same_slug
                )
            )
            continue

        while next_number in allocated_numbers:
            next_number += 1
        destination = doctrine_dir / f"{next_number:04d}-{slug}.md"
        while destination.name in allocated_names:
            next_number += 1
            while next_number in allocated_numbers:
                next_number += 1
            destination = doctrine_dir / f"{next_number:04d}-{slug}.md"
        plans.append(_SeedPlan(source=source, destination=destination, conflict=None))
        allocated_numbers.add(next_number)
        allocated_names.add(destination.name)
        next_number += 1

    return plans
