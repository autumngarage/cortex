"""Shared Plan parsing helpers.

This module is intentionally read-only: command code can inspect Plans without
knowing frontmatter storage details or checkbox parsing rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cortex.frontmatter import FrontmatterValue, parse_frontmatter

# SPEC § 4.7 uses the same 14-day aging threshold for promotion-queue review;
# Plan staleness intentionally reuses that domain limit.
STALENESS_DAYS = 14

_CHECKBOX_RE = re.compile(r"^\s*-\s+\[(?P<state>[ xX~])\]\s+")
_H2_RE = re.compile(r"^##\s+")
_UPDATED_BY_DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class WorkItemCounts:
    done: int
    open: int
    in_progress: int

    @property
    def total_for_completion(self) -> int:
        """Completion denominator: `[~]` is closed and contributes only to numerator."""
        return self.done + self.open

    @property
    def done_equivalent(self) -> float:
        """Weighted numerator, capped so completion never exceeds 100%."""
        raw_done = self.done + (self.in_progress * 0.5)
        return min(raw_done, float(self.total_for_completion))

    @property
    def completion_percent(self) -> int:
        total = self.total_for_completion
        if total == 0:
            return 0
        return round((self.done_equivalent / total) * 100)


@dataclass(frozen=True)
class PlanStatus:
    path: Path
    relative_path: str
    frontmatter: dict[str, FrontmatterValue]
    status: str | None
    written: str | None
    author: str | None
    goal_hash: str | None
    updated_by: list[str]
    cites: str | list[str] | None
    counts: WorkItemCounts
    last_update: date | None
    last_update_age_days: int | None
    stale: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.relative_path,
            "status": self.status,
            "written": self.written,
            "author": self.author,
            "goal_hash": self.goal_hash,
            "updated_by": self.updated_by,
            "cites": self.cites,
            "completion_percent": self.counts.completion_percent,
            "items": {
                "done": self.counts.done,
                "open": self.counts.open,
                "in_progress": self.counts.in_progress,
                "done_equivalent": self.counts.done_equivalent,
                "total": self.counts.total_for_completion,
            },
            "last_update": self.last_update.isoformat() if self.last_update else None,
            "last_update_age_days": self.last_update_age_days,
            "stale": self.stale,
        }


def iter_plan_files(project_root: Path) -> list[Path]:
    """Return non-archived Plan files under `.cortex/plans/`."""
    plans_dir = project_root / ".cortex" / "plans"
    if not plans_dir.exists():
        return []
    return [
        path
        for path in sorted(plans_dir.glob("*.md"))
        if path.name != "template.md" and "_archived" not in path.relative_to(plans_dir).parts
    ]


def _scalar(frontmatter: dict[str, FrontmatterValue], key: str) -> str | None:
    value = frontmatter.get(key)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _updated_by(frontmatter: dict[str, FrontmatterValue]) -> list[str]:
    value = frontmatter.get("Updated-by")
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _extract_section(body: str, heading: str) -> str:
    lines = body.splitlines()
    start: int | None = None
    collected: list[str] = []
    for line in lines:
        if start is None:
            if line.strip() == heading:
                start = 1
            continue
        if _H2_RE.match(line):
            break
        collected.append(line)
    return "\n".join(collected)


def count_work_items(body: str) -> WorkItemCounts:
    section = _extract_section(body, "## Work items")
    done = 0
    open_ = 0
    in_progress = 0
    for line in section.splitlines():
        match = _CHECKBOX_RE.match(line)
        if not match:
            continue
        state = match.group("state").lower()
        if state == "x":
            done += 1
        elif state == "~":
            in_progress += 1
        else:
            open_ += 1
    return WorkItemCounts(done=done, open=open_, in_progress=in_progress)


def _parse_update_date(entry: str) -> date | None:
    match = _UPDATED_BY_DATE_RE.search(entry)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group("date"))
    except ValueError:
        return None


def parse_plan_status(path: Path, project_root: Path, *, today: date | None = None) -> PlanStatus:
    """Parse one Plan file into the structured status used by CLI consumers."""
    today = today or date.today()
    frontmatter, body = parse_frontmatter(path.read_text())
    counts = count_work_items(body)
    updates = _updated_by(frontmatter)
    last_update = _parse_update_date(updates[-1]) if updates else None
    age_days = (today - last_update).days if last_update is not None else None
    status = _scalar(frontmatter, "Status")
    stale = (
        status == "active"
        and age_days is not None
        and age_days > STALENESS_DAYS
        and counts.open > 0
    )

    return PlanStatus(
        path=path,
        relative_path=path.relative_to(project_root / ".cortex").as_posix(),
        frontmatter=frontmatter,
        status=status,
        written=_scalar(frontmatter, "Written"),
        author=_scalar(frontmatter, "Author"),
        goal_hash=_scalar(frontmatter, "Goal-hash"),
        updated_by=updates,
        cites=frontmatter.get("Cites"),
        counts=counts,
        last_update=last_update,
        last_update_age_days=age_days,
        stale=stale,
    )


def collect_plan_statuses(project_root: Path, *, today: date | None = None) -> list[PlanStatus]:
    return [parse_plan_status(path, project_root, today=today) for path in iter_plan_files(project_root)]
