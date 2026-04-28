"""Deterministic work-item ranking for `cortex next`."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from cortex.plans import PlanStatus, collect_plan_statuses
from cortex.state_render import HAND_CLOSE, HAND_OPEN

PriorityBand = Literal["p0", "p1", "p2"]

_CHECKBOX_LINE_RE = re.compile(r"^(?P<prefix>\s*[-*]\s+\[(?P<mark>[ xX~])\]\s+)(?P<text>.+?)\s*$")
_BULLET_LINE_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
_H1_RE = re.compile(r"^#\s+")
_H2_RE = re.compile(r"^##\s+")

SECTION_ANCHORS = {
    "## Current work": "state.md#current-work",
    "## Open questions": "state.md#open-questions",
}


@dataclass(frozen=True)
class RankedItem:
    text: str
    source: str
    line_start: int | None = None
    line_end: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "source": self.source,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass(frozen=True)
class RankedNext:
    p0: list[RankedItem]
    p1: list[RankedItem]
    p2: list[RankedItem]

    def limited(self, limit: int | None) -> RankedNext:
        if limit is None:
            return self
        return RankedNext(p0=self.p0[:limit], p1=self.p1[:limit], p2=self.p2[:limit])

    def to_dict(self) -> dict[str, list[dict[str, object]]]:
        return {
            "p0": [item.to_dict() for item in self.p0],
            "p1": [item.to_dict() for item in self.p1],
            "p2": [item.to_dict() for item in self.p2],
        }


def collect_next_items(project_root: Path, *, since_days: int = 30) -> RankedNext:
    """Collect ranked work candidates from Cortex primary sources."""
    project_root = project_root.resolve()
    plans = collect_plan_statuses(project_root)

    p0 = [
        item
        for plan in plans
        if plan.status == "active" and not plan.stale
        for item in _open_plan_checkboxes(plan, project_root)
    ]
    p1 = [
        item
        for plan in plans
        if plan.status == "active" and plan.stale
        for item in _open_plan_checkboxes(plan, project_root)
    ]

    state_path = project_root / ".cortex" / "state.md"
    if state_path.exists():
        state_text = state_path.read_text()
        p0.extend(_section_items(state_text, "## Current work", SECTION_ANCHORS["## Current work"]))
        p1.extend(_section_items(state_text, "## Open questions", SECTION_ANCHORS["## Open questions"]))

    p2 = _recent_case_studies(project_root, since_days=since_days)

    return RankedNext(
        p0=_stable_sort(p0),
        p1=_stable_sort(p1),
        p2=_stable_sort(p2),
    )


def format_next_human(items: RankedNext) -> str:
    lines: list[str] = []
    _append_band(lines, "P0 — Active work", items.p0)
    lines.append("")
    _append_band(lines, "P1 — Open questions and stale debt", items.p1)
    lines.append("")
    _append_band(lines, "P2 — Recent context to consider", items.p2, multiline=True)
    return "\n".join(lines) + "\n"


def _append_band(
    lines: list[str],
    heading: str,
    items: list[RankedItem],
    *,
    multiline: bool = False,
) -> None:
    lines.append(heading)
    if not items:
        lines.append("  (none)")
        return
    for item in items:
        source = _human_source(item)
        if multiline:
            lines.append(f"  • {source}")
            lines.extend(f"     {line}" for line in textwrap.wrap(item.text, width=72))
        else:
            lines.append(f"  • {source:<24} {item.text}")


def _human_source(item: RankedItem) -> str:
    if item.line_start is None:
        return item.source
    if item.line_end is not None and item.line_end != item.line_start:
        return f"{item.source}:{item.line_start}-{item.line_end}"
    return f"{item.source}:{item.line_start}"


def _stable_sort(items: list[RankedItem]) -> list[RankedItem]:
    return sorted(items, key=lambda item: (item.source, item.line_start or 0, item.text))


def _open_plan_checkboxes(plan: PlanStatus, project_root: Path) -> list[RankedItem]:
    lines = plan.path.read_text().splitlines()
    items: list[RankedItem] = []
    in_work_items = False
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "## Work items":
            in_work_items = True
            continue
        if in_work_items and _H2_RE.match(line):
            break
        if not in_work_items:
            continue
        match = _CHECKBOX_LINE_RE.match(line)
        if not match or match.group("mark") != " ":
            continue
        items.append(
            RankedItem(
                text=_clean_item_text(match.group("text")),
                source=plan.path.relative_to(project_root / ".cortex").as_posix(),
                line_start=idx,
                line_end=idx,
            )
        )
    return items


def _section_items(text: str, heading: str, source: str) -> list[RankedItem]:
    section = _extract_section(text, heading)
    if not section:
        return []

    items: list[RankedItem] = []
    for _, line in section:
        if line.strip() in {HAND_OPEN, HAND_CLOSE}:
            continue
        checkbox = _CHECKBOX_LINE_RE.match(line)
        if checkbox:
            items.append(RankedItem(text=_clean_item_text(checkbox.group("text")), source=source))
            continue
        bullet = _BULLET_LINE_RE.match(line)
        if bullet:
            text_value = _clean_item_text(bullet.group("text"))
            if text_value.lower() != "none":
                items.append(RankedItem(text=text_value, source=source))
    return items


def _extract_section(text: str, heading: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    section: list[tuple[int, str]] = []
    in_section = False
    for idx, line in enumerate(lines, start=1):
        if not in_section:
            if line.strip() == heading:
                in_section = True
            continue
        if _H2_RE.match(line) or line.strip() == HAND_CLOSE:
            break
        section.append((idx, line))
    return section


def _recent_case_studies(project_root: Path, *, since_days: int) -> list[RankedItem]:
    case_dir = project_root / "docs" / "case-studies"
    if not case_dir.exists():
        return []
    cutoff = datetime.now().timestamp() - timedelta(days=since_days).total_seconds()
    items: list[RankedItem] = []
    for path in sorted(case_dir.glob("*.md")):
        if not path.is_file() or path.stat().st_mtime < cutoff:
            continue
        summary = _case_study_summary(path.read_text())
        if not summary:
            continue
        items.append(
            RankedItem(
                text=summary,
                source=path.relative_to(project_root).as_posix(),
            )
        )
    return items


def _case_study_summary(text: str) -> str:
    lines = text.splitlines()
    try:
        h1_index = next(idx for idx, line in enumerate(lines) if _H1_RE.match(line))
    except StopIteration:
        return ""

    paragraph: list[str] = []
    for line in lines[h1_index + 1 :]:
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            break
        paragraph.append(stripped)
    return _clean_item_text(" ".join(paragraph))


def _clean_item_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    return re.sub(r"\s+", " ", cleaned)
