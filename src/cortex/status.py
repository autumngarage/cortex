"""Compute the status summary shown by bare ``cortex`` / ``cortex status``.

The status surface is deliberately narrow in this first slice — it shows
what a human or agent would ask "where is this project?" to learn:

- project name (from the directory), spec version, protocol version
- active Plan count and their paths
- Journal activity in the last 7 days
- latest digest and its age in days (flagged overdue if >45 days)
- promotion-queue summary from ``.cortex/.index.json`` when present

The README shows a richer interactive flow (promotion review prompts, digest
generation prompts); those depend on the v0.6.0 lifecycle layer populating
``.index.json`` and the follow-up retention/digest work. This module emits a
structured payload so both the human-readable formatter and a future JSON
emitter can share the same source of truth.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cortex.audit import _journal_header_fields, load_journal_entries
from cortex.frontmatter import parse_frontmatter

OVERDUE_DIGEST_DAYS = 45
RECENT_JOURNAL_DAYS = 7


@dataclass
class PlanSummary:
    path: str
    title: str
    status: str


@dataclass
class Status:
    project_root: Path
    spec_version: str | None
    protocol_version: str | None
    active_plans: list[PlanSummary] = field(default_factory=list)
    recent_journal_count: int = 0
    journal_window_days: int = RECENT_JOURNAL_DAYS
    latest_digest_path: Path | None = None
    latest_digest_age_days: int | None = None
    digest_overdue: bool = False
    promotion_proposed: int | None = None
    promotion_stale: int | None = None
    promotion_index_present: bool = False
    promotion_index_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        queue: dict[str, object]
        if not self.promotion_index_present:
            queue = {"index_present": False}
        elif self.promotion_index_error:
            queue = {"index_present": True, "error": self.promotion_index_error}
        else:
            queue = {
                "proposed": self.promotion_proposed,
                "stale": self.promotion_stale,
                "index_present": True,
            }
        return {
            "project_root": str(self.project_root),
            "spec_version": self.spec_version,
            "protocol_version": self.protocol_version,
            "active_plans": [
                {"path": p.path, "title": p.title, "status": p.status}
                for p in self.active_plans
            ],
            "recent_journal_count": self.recent_journal_count,
            "journal_window_days": self.journal_window_days,
            "latest_digest": (
                {
                    "path": str(self.latest_digest_path),
                    "age_days": self.latest_digest_age_days,
                    "overdue": self.digest_overdue,
                }
                if self.latest_digest_path
                else None
            ),
            "promotion_queue": queue,
        }


def _read_scalar(path: Path) -> str | None:
    if not path.exists():
        return None
    text = path.read_text().strip()
    return text or None


def _read_protocol_version(cortex_dir: Path) -> str | None:
    protocol = cortex_dir / "protocol.md"
    if not protocol.exists():
        return None
    for line in protocol.read_text().splitlines():
        m = re.match(r"^\*\*Protocol version:\*\*\s*([^\s]+)", line)
        if m:
            return m.group(1).strip()
    return None


def _collect_active_plans(cortex_dir: Path) -> list[PlanSummary]:
    plans_dir = cortex_dir / "plans"
    if not plans_dir.exists():
        return []
    summaries: list[PlanSummary] = []
    for path in sorted(plans_dir.glob("*.md")):
        frontmatter, body = parse_frontmatter(path.read_text())
        status = frontmatter.get("Status")
        if not isinstance(status, str) or status.strip() != "active":
            continue
        title = "(untitled)"
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        summaries.append(
            PlanSummary(path=str(path.relative_to(cortex_dir.parent)), title=title, status="active")
        )
    return summaries


def _count_recent_journal(cortex_dir: Path, days: int, now: datetime) -> int:
    journal_dir = cortex_dir / "journal"
    if not journal_dir.exists():
        return 0
    cutoff = now - timedelta(days=days)
    count = 0
    for path in journal_dir.glob("*.md"):
        m = re.match(r"^(\d{4}-\d{2}-\d{2})-", path.name)
        if not m:
            continue
        entry_date = datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
        if entry_date >= cutoff:
            count += 1
    return count


def _latest_digest(project_root: Path, now: datetime) -> tuple[Path, int] | None:
    entries = load_journal_entries(project_root)
    digests: list[tuple[Path, datetime]] = []
    for entry in entries:
        type_, _trigger, _tag = _journal_header_fields(entry.path)
        if type_ == "digest":
            digests.append((entry.path, entry.date))
    if not digests:
        return None
    digests.sort(key=lambda row: row[1], reverse=True)
    latest_path, latest_date = digests[0]
    age_days = (now - latest_date).days
    return latest_path, age_days


@dataclass(frozen=True)
class _PromotionIndexRead:
    present: bool
    proposed: int | None
    stale: int | None
    error: str | None


def _read_promotion_index(cortex_dir: Path) -> _PromotionIndexRead:
    index_path = cortex_dir / ".index.json"
    if not index_path.exists():
        return _PromotionIndexRead(present=False, proposed=None, stale=None, error=None)
    try:
        data = json.loads(index_path.read_text())
    except json.JSONDecodeError as exc:
        return _PromotionIndexRead(
            present=True, proposed=None, stale=None, error=f"JSON decode error: {exc}"
        )
    if not isinstance(data, dict):
        return _PromotionIndexRead(
            present=True, proposed=None, stale=None,
            error="top-level JSON value is not an object",
        )
    if "candidates" in data:
        queue = data["candidates"]
        legacy_queue = False
    elif "promotion_queue" in data:
        queue = data["promotion_queue"]
        legacy_queue = True
    else:
        return _PromotionIndexRead(
            present=True, proposed=None, stale=None,
            error="`candidates` field missing",
        )
    if not isinstance(queue, list):
        return _PromotionIndexRead(
            present=True, proposed=None, stale=None,
            error="`candidates` is not a list",
        )
    if legacy_queue:
        proposed = sum(1 for c in queue if isinstance(c, dict) and c.get("state") == "proposed")
        stale = sum(
            1 for c in queue if isinstance(c, dict) and c.get("state") == "stale-proposed"
        )
        return _PromotionIndexRead(present=True, proposed=proposed, stale=stale, error=None)
    proposed = sum(
        1
        for c in queue
        if isinstance(c, dict)
        and c.get("promoted_to") is None
        and (
            not isinstance(c.get("age_days"), int)
            or c["age_days"] <= 14
        )
    )
    stale = sum(
        1
        for c in queue
        if isinstance(c, dict)
        and c.get("promoted_to") is None
        and isinstance(c.get("age_days"), int)
        and c["age_days"] > 14
    )
    return _PromotionIndexRead(present=True, proposed=proposed, stale=stale, error=None)


def compute_status(project_root: Path, *, now: datetime | None = None) -> Status:
    now = now or datetime.now(UTC)
    cortex_dir = project_root / ".cortex"
    spec_version = _read_scalar(cortex_dir / "SPEC_VERSION")
    protocol_version = _read_protocol_version(cortex_dir)
    active_plans = _collect_active_plans(cortex_dir)
    recent = _count_recent_journal(cortex_dir, RECENT_JOURNAL_DAYS, now)
    digest = _latest_digest(project_root, now)
    index = _read_promotion_index(cortex_dir)

    status = Status(
        project_root=project_root,
        spec_version=spec_version,
        protocol_version=protocol_version,
        active_plans=active_plans,
        recent_journal_count=recent,
        promotion_proposed=index.proposed,
        promotion_stale=index.stale,
        promotion_index_present=index.present,
        promotion_index_error=index.error,
    )
    if digest is not None:
        status.latest_digest_path = digest[0]
        status.latest_digest_age_days = digest[1]
        status.digest_overdue = digest[1] > OVERDUE_DIGEST_DAYS
    return status


def format_status(status: Status) -> str:
    """Render the status as human-readable text."""
    lines = [f"Project: {status.project_root.name}"]
    version_bits = []
    if status.spec_version:
        version_bits.append(f"spec {status.spec_version}")
    if status.protocol_version:
        version_bits.append(f"protocol {status.protocol_version}")
    if version_bits:
        lines.append("Versions: " + ", ".join(version_bits))

    if status.active_plans:
        lines.append(f"Active plans ({len(status.active_plans)}):")
        for plan in status.active_plans:
            lines.append(f"  - {plan.path} — {plan.title}")
    else:
        lines.append("Active plans: none")

    lines.append(
        f"Journal: {status.recent_journal_count} "
        f"entr{'y' if status.recent_journal_count == 1 else 'ies'} in last "
        f"{status.journal_window_days} days"
    )

    if status.latest_digest_path is not None:
        tag = " (overdue)" if status.digest_overdue else ""
        lines.append(
            f"Latest digest: {status.latest_digest_path.name} — "
            f"{status.latest_digest_age_days} days old{tag}"
        )
    else:
        lines.append("Latest digest: none")

    if not status.promotion_index_present:
        lines.append(
            "Promotion queue: not yet initialised (`.cortex/.index.json` absent; "
            "populated by the v0.6.0 lifecycle commands)"
        )
    elif status.promotion_index_error:
        lines.append(
            f"Promotion queue: UNREADABLE (`.cortex/.index.json` is present but "
            f"cannot be parsed — {status.promotion_index_error}). "
            "Repair or remove the file before trusting the counts."
        )
    else:
        lines.append(
            f"Promotion queue: {status.promotion_proposed} proposed, "
            f"{status.promotion_stale} stale"
        )
    return "\n".join(lines) + "\n"
