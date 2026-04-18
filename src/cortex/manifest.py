"""Build the session-start manifest per Cortex Protocol § 1.

The manifest is a token-budgeted concatenation of:

1. ``state.md`` — always loaded in full.
2. Doctrine — every entry with ``Load-priority: always`` first, then the
   remaining entries by ``Date:`` descending, until the Doctrine sub-budget
   is exhausted.
3. Active Plans — every Plan with ``Status: active``, oldest first.
4. Journal — entries from a window (default: last 72 hours) plus the latest
   digest if present.
5. Promotion-queue summary — a line such as
   ``Promotion-queue: 3 proposed, 1 stale`` read from ``.cortex/.index.json``.
   When the index is absent, the summary records that explicitly.

Token counts are estimated at ~4 chars/token (conservative rule of thumb);
the exact tokenizer lives with whichever agent runtime consumes the
manifest, not with Cortex. Low-budget degradation:

- ``budget < 2000`` — State only.
- ``2000 <= budget < 15000`` — full default slice.
- ``budget >= 15000`` — default slice + Journal window widened to 7 days.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cortex.frontmatter import parse_frontmatter

CHARS_PER_TOKEN = 4

DEGRADED_BUDGET = 2000
WIDE_JOURNAL_BUDGET = 15000

DEFAULT_JOURNAL_HOURS = 72
WIDE_JOURNAL_HOURS = 24 * 7

JOURNAL_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
DOCTRINE_FILENAME_RE = re.compile(r"^\d{4}-[a-z0-9][a-z0-9._-]*\.md$")


@dataclass
class ManifestSection:
    title: str
    body: str
    # Number of whole entries actually included (post-budget truncation).
    included: int = 0
    # Entries considered but dropped for budget reasons.
    truncated: int = 0


@dataclass
class Manifest:
    project_root: Path
    budget_tokens: int
    degraded: bool
    journal_hours: int
    sections: list[ManifestSection] = field(default_factory=list)
    promotion_summary: str = ""

    def render(self) -> str:
        header = [
            "# Cortex Session Manifest",
            "",
            f"Generated: {datetime.now(UTC).astimezone().isoformat(timespec='seconds')}",
            f"Project: {self.project_root}",
            f"Budget: {self.budget_tokens} tokens (~{self.budget_tokens * CHARS_PER_TOKEN} chars)",
            f"Mode: {'degraded (state-only)' if self.degraded else 'full'}",
            f"Journal window: last {self.journal_hours}h",
            "",
            "---",
            "",
        ]
        out = "\n".join(header)
        for section in self.sections:
            out += f"## {section.title}\n\n"
            if section.truncated:
                out += (
                    f"> Included {section.included} of "
                    f"{section.included + section.truncated} entries; "
                    f"{section.truncated} truncated by budget.\n\n"
                )
            out += section.body
            if not section.body.endswith("\n"):
                out += "\n"
            out += "\n---\n\n"
        out += self.promotion_summary + "\n"
        return out


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _read_files_newest_first(dir_path: Path) -> list[Path]:
    """Return ``*.md`` files sorted by filename descending (date-prefixed layers)."""
    if not dir_path.exists():
        return []
    return sorted((p for p in dir_path.glob("*.md") if p.is_file()), reverse=True)


def _doctrine_entries(cortex_dir: Path) -> list[Path]:
    doctrine_dir = cortex_dir / "doctrine"
    if not doctrine_dir.exists():
        return []
    return sorted(p for p in doctrine_dir.glob("*.md") if DOCTRINE_FILENAME_RE.match(p.name))


def _doctrine_field(entry: Path, field_name: str) -> str | None:
    """Read ``field_name`` from a Doctrine entry, accepting either YAML
    frontmatter or the bold-inline form (SPEC § 6)."""
    text = entry.read_text()
    frontmatter, _body = parse_frontmatter(text)
    if field_name in frontmatter:
        fm_value = frontmatter[field_name]
        if isinstance(fm_value, str):
            return fm_value.strip()
    header = "\n".join(text.splitlines()[:40])
    match = re.search(rf"\*\*{re.escape(field_name)}:\*\*\s*(.+)", header)
    if match:
        return match.group(1).strip()
    return None


def _doctrine_order(entries: list[Path]) -> list[Path]:
    """Order Doctrine for the default manifest per SPEC §§ 5.1 and Protocol § 1.

    Excludes entries whose ``Status`` begins with ``Superseded-by`` (SPEC § 5.1
    drops superseded entries from the default load). Among the remainder,
    ``Load-priority: always`` entries come first (filename order preserved),
    then everything else sorted by ``Date:`` descending with filename as tie-break.
    """
    always: list[Path] = []
    others: list[tuple[str, str, Path]] = []
    for entry in entries:
        status = _doctrine_field(entry, "Status") or ""
        if status.strip().lower().startswith("superseded-by"):
            continue
        priority = _doctrine_field(entry, "Load-priority") or "default"
        if priority.strip().lower() == "always":
            always.append(entry)
            continue
        date = _doctrine_field(entry, "Date") or ""
        others.append((date, entry.name, entry))
    others.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return always + [entry for _, _, entry in others]


def _active_plans(cortex_dir: Path) -> list[Path]:
    plans_dir = cortex_dir / "plans"
    if not plans_dir.exists():
        return []
    result: list[Path] = []
    for plan in sorted(plans_dir.glob("*.md")):
        frontmatter, _body = parse_frontmatter(plan.read_text())
        status = frontmatter.get("Status")
        if isinstance(status, str) and status.strip() == "active":
            result.append(plan)
    return result


def _journal_window(cortex_dir: Path, hours: int, now: datetime) -> list[Path]:
    journal_dir = cortex_dir / "journal"
    if not journal_dir.exists():
        return []
    cutoff = now - timedelta(hours=hours)
    result: list[Path] = []
    for entry in _read_files_newest_first(journal_dir):
        match = JOURNAL_DATE_RE.match(entry.name)
        if not match:
            continue
        entry_date = datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC)
        if entry_date >= cutoff.replace(hour=0, minute=0, second=0, microsecond=0):
            result.append(entry)
    return result


def _journal_type(entry: Path) -> str | None:
    """Return the ``Type:`` of a Journal entry per SPEC § 3.5.

    Journal entries ship with bold-inline scalar fields by default (``**Type:**
    digest``) but may use YAML frontmatter under SPEC § 6; both are accepted.
    """
    text = entry.read_text()
    frontmatter, _body = parse_frontmatter(text)
    value = frontmatter.get("Type")
    if isinstance(value, str) and value.strip():
        return value.strip()
    header = "\n".join(text.splitlines()[:40])
    match = re.search(r"\*\*Type:\*\*\s*([^\n]+)", header)
    if match:
        return match.group(1).strip()
    return None


def _latest_digest(cortex_dir: Path) -> Path | None:
    """Return the most recent Journal entry with ``Type: digest``, or None."""
    journal_dir = cortex_dir / "journal"
    if not journal_dir.exists():
        return None
    digests = [p for p in journal_dir.glob("*.md") if _journal_type(p) == "digest"]
    if not digests:
        return None
    return sorted(digests, reverse=True)[0]


def _promotion_summary(cortex_dir: Path) -> str:
    index_path = cortex_dir / ".index.json"
    if not index_path.exists():
        return "Promotion-queue: unavailable (no `.cortex/.index.json`; ship a CLI run to refresh it)."
    try:
        data = json.loads(index_path.read_text())
    except json.JSONDecodeError as exc:
        return f"Promotion-queue: unreadable (`.cortex/.index.json` JSON error: {exc})."
    queue = data.get("promotion_queue", [])
    proposed = sum(1 for c in queue if c.get("state") == "proposed")
    stale = sum(1 for c in queue if c.get("state") == "stale-proposed")
    return f"Promotion-queue: {proposed} proposed, {stale} stale."


def _concat_files(entries: list[Path], budget_chars: int) -> tuple[str, int, int]:
    """Return ``(body, included_count, truncated_count)`` for the given entries.

    Strict budget enforcement: an entry is only included if it fits. If the
    very first entry does not fit, the section is rendered empty and every
    entry is counted as truncated. Callers decide whether to raise the
    budget or accept the empty section.
    """
    body_parts: list[str] = []
    used = 0
    included = 0
    for entry in entries:
        text = entry.read_text()
        size = len(text) + 16  # separator and heading overhead
        if used + size > budget_chars:
            break
        body_parts.append(f"### `{entry.name}`\n\n{text.rstrip()}\n")
        used += size
        included += 1
    return "\n".join(body_parts), included, len(entries) - included


def build_manifest(project_root: Path, budget_tokens: int, *, now: datetime | None = None) -> Manifest:
    """Assemble the session manifest per Protocol § 1 with graceful degradation."""
    now = now or datetime.now(UTC)
    cortex_dir = project_root / ".cortex"
    degraded = budget_tokens < DEGRADED_BUDGET
    journal_hours = WIDE_JOURNAL_HOURS if budget_tokens >= WIDE_JOURNAL_BUDGET else DEFAULT_JOURNAL_HOURS

    state_file = cortex_dir / "state.md"
    state_body = state_file.read_text() if state_file.exists() else "> state.md missing — run `cortex init`.\n"
    state_section = ManifestSection(title="state.md", body=state_body, included=1, truncated=0)

    manifest = Manifest(
        project_root=project_root,
        budget_tokens=budget_tokens,
        degraded=degraded,
        journal_hours=journal_hours,
        sections=[state_section],
    )

    if degraded:
        manifest.promotion_summary = _promotion_summary(cortex_dir)
        return manifest

    remaining_chars = max(0, budget_tokens * CHARS_PER_TOKEN - estimate_tokens(state_body) * CHARS_PER_TOKEN)

    doctrine_budget = int(remaining_chars * 0.40)
    doctrine_body, doctrine_in, doctrine_cut = _concat_files(_doctrine_order(_doctrine_entries(cortex_dir)), doctrine_budget)
    manifest.sections.append(
        ManifestSection(
            title="Doctrine (Load-priority `always` first; then recency)",
            body=doctrine_body or "_no Doctrine entries_\n",
            included=doctrine_in,
            truncated=doctrine_cut,
        )
    )
    used_chars = len(doctrine_body)

    plan_budget = int(remaining_chars * 0.25)
    plans_body, plans_in, plans_cut = _concat_files(_active_plans(cortex_dir), plan_budget)
    manifest.sections.append(
        ManifestSection(
            title="Active Plans",
            body=plans_body or "_no active Plans_\n",
            included=plans_in,
            truncated=plans_cut,
        )
    )
    used_chars += len(plans_body)

    journal_budget = remaining_chars - used_chars
    journal_entries = _journal_window(cortex_dir, journal_hours, now)
    digest = _latest_digest(cortex_dir)
    if digest is not None and digest not in journal_entries:
        journal_entries = [digest, *journal_entries]
    journal_body, journal_in, journal_cut = _concat_files(journal_entries, max(0, journal_budget))
    manifest.sections.append(
        ManifestSection(
            title=f"Journal — last {journal_hours}h + latest digest",
            body=journal_body or "_no Journal entries in window_\n",
            included=journal_in,
            truncated=journal_cut,
        )
    )

    manifest.promotion_summary = _promotion_summary(cortex_dir)
    return manifest
