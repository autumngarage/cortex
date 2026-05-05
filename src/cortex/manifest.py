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
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from cortex.frontmatter import parse_frontmatter
from cortex.index import read_index
from cortex.plans import iter_plan_files
from cortex.verified import VERIFIED_RE, bullet_age_days, format_warning, parse_verified

CHARS_PER_TOKEN = 4

DEFAULT_BUDGET_TOKENS = 8000
DELEGATION_BUDGET_TOKENS = DEFAULT_BUDGET_TOKENS // 2

DEGRADED_BUDGET = 2000
WIDE_JOURNAL_BUDGET = 15000

DEFAULT_JOURNAL_HOURS = 72
WIDE_JOURNAL_HOURS = 24 * 7
DEFAULT_VERIFIED_THRESHOLD_DAYS = 90

JOURNAL_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
DOCTRINE_FILENAME_RE = re.compile(r"^\d{4}-[a-z0-9][a-z0-9._-]*\.md$")
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

ManifestProfileName = Literal["default", "delegation"]


@dataclass(frozen=True)
class ManifestProfile:
    name: ManifestProfileName
    default_budget_tokens: int
    include_full_state: bool
    include_corpus_sections: bool
    include_delegation_brief: bool


MANIFEST_PROFILES: dict[ManifestProfileName, ManifestProfile] = {
    "default": ManifestProfile(
        name="default",
        default_budget_tokens=DEFAULT_BUDGET_TOKENS,
        include_full_state=True,
        include_corpus_sections=True,
        include_delegation_brief=False,
    ),
    "delegation": ManifestProfile(
        name="delegation",
        # Derived from the domain boundary: handoffs should consume at most
        # half of the normal session-start manifest unless the caller opts in.
        default_budget_tokens=DELEGATION_BUDGET_TOKENS,
        include_full_state=False,
        include_corpus_sections=False,
        include_delegation_brief=True,
    ),
}


@dataclass
class ManifestSection:
    title: str
    body: str
    # Number of whole entries actually included (post-budget truncation).
    included: int = 0
    # Entries considered but dropped for budget reasons.
    truncated: int = 0
    included_paths: list[str] = field(default_factory=list)
    truncated_paths: list[str] = field(default_factory=list)
    omitted: bool = False
    retrieval: str = ""


@dataclass
class Manifest:
    project_root: Path
    budget_tokens: int
    profile: ManifestProfile
    degraded: bool
    journal_hours: int
    sections: list[ManifestSection] = field(default_factory=list)
    promotion_summary: str = ""

    def render(self, *, show_budget: bool = False) -> str:
        header = [
            "# Cortex Session Manifest",
            "",
            f"Generated: {datetime.now(UTC).astimezone().isoformat(timespec='seconds')}",
            f"Project: {self.project_root}",
            f"Profile: {self.profile.name}",
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
            if show_budget:
                out += f"> Tokens: ~{estimate_tokens(section.body)} used.\n\n"
            if section.truncated:
                out += (
                    f"> Included {section.included} of "
                    f"{section.included + section.truncated} entries; "
                    f"{section.truncated} truncated by budget.\n\n"
                )
            if section.omitted:
                out += f"> Omitted: {section.truncated} entries. {section.retrieval}\n\n"
            out += section.body
            if not section.body.endswith("\n"):
                out += "\n"
            out += "\n---\n\n"
        if self.promotion_summary:
            out += self.promotion_summary + "\n"
        return out

    def total_estimated_tokens(self) -> int:
        return estimate_tokens(self.render(show_budget=True))


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def _read_files_newest_first(dir_path: Path) -> list[Path]:
    """Return ``*.md`` files sorted by filename descending (date-prefixed layers)."""
    if not dir_path.exists():
        return []
    return sorted((p for p in dir_path.glob("*.md") if p.is_file()), reverse=True)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


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
    if not (cortex_dir / "plans").exists():
        return []
    result: list[Path] = []
    for plan in iter_plan_files(cortex_dir.parent):
        frontmatter, _body = parse_frontmatter(plan.read_text())
        status = frontmatter.get("Status")
        if isinstance(status, str) and status.strip() == "active":
            result.append(plan)
    return result


def _project_summary(project_root: Path) -> str:
    readme = project_root / "README.md"
    if not readme.exists():
        return f"{project_root.name}: no README summary found."
    for line in readme.read_text().splitlines():
        stripped = line.strip().strip(">")
        if stripped.startswith("**") and stripped.endswith("**"):
            return stripped.strip("*")
    for line in readme.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "```", ">")):
            return stripped.strip("*")
    return f"{project_root.name}: README has no one-line summary."


def _spec_version(cortex_dir: Path) -> str:
    version_file = cortex_dir / "SPEC_VERSION"
    if version_file.exists():
        value = version_file.read_text().strip()
        if value:
            return value
    return "unknown"


def _section_text(text: str, heading_prefix: str) -> str:
    matches = list(HEADING_RE.finditer(text))
    for idx, match in enumerate(matches):
        if not match.group(1).startswith(heading_prefix):
            continue
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def _active_task_pointer(project_root: Path, cortex_dir: Path) -> tuple[str, list[str]]:
    active_plans = _active_plans(cortex_dir)
    if not active_plans:
        return "No active Plan found. Run `cortex plan status` or inspect `.cortex/plans/`.", []
    primary = active_plans[0]
    pickup = _section_text(primary.read_text(), "Pickup pointer")
    rel = _rel(primary, project_root)
    if not pickup:
        return f"Active Plan: `{rel}`. No `## Pickup pointer` section found.", [rel]
    return f"Active Plan: `{rel}` `## Pickup pointer`\n\n{pickup}", [rel]


def _delegation_sections(project_root: Path, cortex_dir: Path) -> list[ManifestSection]:
    doctrine_paths = [_rel(p, project_root) for p in _doctrine_order(_doctrine_entries(cortex_dir))]
    active_plan_paths = [_rel(p, project_root) for p in _active_plans(cortex_dir)]
    journal_paths = [_rel(p, project_root) for p in _read_files_newest_first(cortex_dir / "journal")]
    pointer, pointer_paths = _active_task_pointer(project_root, cortex_dir)
    identity = (
        f"- Repo: `{project_root.name}`\n"
        f"- Summary: {_project_summary(project_root)}\n"
        f"- SPEC target: `{_spec_version(cortex_dir)}`\n"
    )
    invariants = (
        "Load-bearing invariants live in `.cortex/protocol.md` §4:\n"
        "- Journal is append-only.\n"
        "- Doctrine is immutable-with-supersede.\n"
        "- Generated layers declare provenance with the seven metadata fields.\n"
    )
    retrieval = (
        "Retrieve omitted context with targeted reads, for example:\n"
        "- `rg \"<term>\" .cortex/doctrine .cortex/plans .cortex/journal`\n"
        "- `cortex retrieve \"<query>\" --mode bm25 --json` when the CLI index is available\n"
        "- read `.cortex/protocol.md` §4 before changing Journal, Doctrine, Map, or State\n"
    )
    omitted = (
        f"- Doctrine: {len(doctrine_paths)} entries omitted; grep `.cortex/doctrine/` "
        "or run `cortex retrieve --mode bm25`.\n"
        f"- Plans: {len(active_plan_paths)} active Plan file(s) omitted except the pickup pointer; "
        "read `.cortex/plans/` when scope details matter.\n"
        f"- Journal: {len(journal_paths)} entries omitted; grep `.cortex/journal/` "
        "or retrieve by query when history matters.\n"
    )
    return [
        ManifestSection("Project Identity", identity, included=1),
        ManifestSection("Protocol Invariants", invariants, included=1),
        ManifestSection(
            "Active Task Pointer",
            pointer,
            included=len(pointer_paths),
            included_paths=pointer_paths,
        ),
        ManifestSection("Targeted Retrieval", retrieval, included=1),
        ManifestSection(
            "Omitted Corpus",
            omitted,
            truncated=len(doctrine_paths) + len(active_plan_paths) + len(journal_paths),
            truncated_paths=[*doctrine_paths, *active_plan_paths, *journal_paths],
            omitted=True,
            retrieval="Use the commands in Targeted Retrieval before relying on omitted history.",
        ),
    ]


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
        data = read_index(index_path)
    except json.JSONDecodeError as exc:
        return f"Promotion-queue: unreadable (`.cortex/.index.json` JSON error: {exc})."
    except ValueError as exc:
        message = str(exc).split(": ", 1)[-1].replace("top-level JSON value", "top-level value")
        return f"Promotion-queue: unreadable (`.cortex/.index.json` {message})."
    if "candidates" in data:
        queue = data["candidates"]
        legacy_queue = False
    else:
        queue = data.get("promotion_queue", [])
        legacy_queue = True
    if not isinstance(queue, list):
        return "Promotion-queue: unreadable (`.cortex/.index.json` `candidates` is not a list)."
    bad_item_count = sum(1 for c in queue if not isinstance(c, dict))
    if bad_item_count:
        return (
            "Promotion-queue: unreadable (`.cortex/.index.json` "
            f"contains {bad_item_count} non-object queue item"
            f"{'s' if bad_item_count != 1 else ''})."
        )
    if legacy_queue:
        proposed = sum(1 for c in queue if c.get("state") == "proposed")
        stale = sum(1 for c in queue if c.get("state") == "stale-proposed")
        return f"Promotion-queue: {proposed} proposed, {stale} stale."
    proposed = sum(
        1
        for c in queue
        if c.get("promoted_to") is None
        and (
            not isinstance(c.get("age_days"), int)
            or c["age_days"] <= 14
        )
    )
    stale = sum(
        1
        for c in queue
        if c.get("promoted_to") is None
        and isinstance(c.get("age_days"), int)
        and c["age_days"] > 14
    )
    return f"Promotion-queue: {proposed} proposed, {stale} stale."


def _verified_threshold_days(cortex_dir: Path) -> int:
    config_path = cortex_dir / "config.toml"
    if not config_path.exists():
        return DEFAULT_VERIFIED_THRESHOLD_DAYS
    data = tomllib.loads(config_path.read_text())
    manifest_config = data.get("manifest", {})
    if not isinstance(manifest_config, dict):
        raise ValueError(f"{config_path}: [manifest] must be a table")
    threshold = manifest_config.get("verified_threshold_days", DEFAULT_VERIFIED_THRESHOLD_DAYS)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise ValueError(
            f"{config_path}: [manifest].verified_threshold_days must be a non-negative integer"
        )
    return int(threshold)


def _annotate_verified_tags(text: str, *, today: date, threshold_days: int) -> str:
    lines: list[str] = []
    for line in text.splitlines(keepends=True):
        content = line.removesuffix("\n")
        newline = "\n" if line.endswith("\n") else ""
        verified = parse_verified(content)
        if verified is None:
            lines.append(line)
            continue
        warning = format_warning(bullet_age_days(verified, today), threshold_days)
        if warning is None:
            lines.append(line)
            continue
        match = VERIFIED_RE.search(content)
        if match is None:
            lines.append(line)
            continue
        prefix = content[: match.start()].rstrip()
        marker = content[match.start() :].strip()
        lines.append(f"{prefix} {warning} {marker}{newline}")
    return "".join(lines)


def _concat_files(
    entries: list[Path],
    budget_chars: int,
    *,
    verified_today: date | None = None,
    verified_threshold_days: int = DEFAULT_VERIFIED_THRESHOLD_DAYS,
) -> tuple[str, int, int, list[str], list[str]]:
    """Return ``(body, included_count, truncated_count)`` for the given entries.

    Strict budget enforcement: an entry is only included if it fits. If the
    very first entry does not fit, the section is rendered empty and every
    entry is counted as truncated. Callers decide whether to raise the
    budget or accept the empty section.
    """
    body_parts: list[str] = []
    used = 0
    included = 0
    included_paths: list[str] = []
    for entry in entries:
        text = entry.read_text()
        if verified_today is not None:
            text = _annotate_verified_tags(
                text,
                today=verified_today,
                threshold_days=verified_threshold_days,
            )
        size = len(text) + 16  # separator and heading overhead
        if used + size > budget_chars:
            break
        body_parts.append(f"### `{entry.name}`\n\n{text.rstrip()}\n")
        used += size
        included += 1
        included_paths.append(entry.as_posix())
    truncated_paths = [entry.as_posix() for entry in entries[included:]]
    return "\n".join(body_parts), included, len(entries) - included, included_paths, truncated_paths


def build_manifest(
    project_root: Path,
    budget_tokens: int,
    *,
    profile: ManifestProfileName = "default",
    now: datetime | None = None,
) -> Manifest:
    """Assemble the session manifest per Protocol § 1 with graceful degradation."""
    now = now or datetime.now(UTC)
    manifest_profile = MANIFEST_PROFILES[profile]
    cortex_dir = project_root / ".cortex"
    degraded = budget_tokens < DEGRADED_BUDGET
    journal_hours = WIDE_JOURNAL_HOURS if budget_tokens >= WIDE_JOURNAL_BUDGET else DEFAULT_JOURNAL_HOURS
    verified_threshold_days = _verified_threshold_days(cortex_dir)
    verified_today = now.date()

    sections: list[ManifestSection]
    if manifest_profile.include_delegation_brief:
        sections = _delegation_sections(project_root, cortex_dir)
    else:
        state_file = cortex_dir / "state.md"
        state_body = state_file.read_text() if state_file.exists() else "> state.md missing — run `cortex init`.\n"
        state_body = _annotate_verified_tags(
            state_body,
            today=verified_today,
            threshold_days=verified_threshold_days,
        )
        sections = [
            ManifestSection(
                title="state.md",
                body=state_body,
                included=1,
                included_paths=[_rel(state_file, project_root)] if state_file.exists() else [],
            )
        ]

    manifest = Manifest(
        project_root=project_root,
        budget_tokens=budget_tokens,
        profile=manifest_profile,
        degraded=degraded,
        journal_hours=journal_hours,
        sections=sections,
    )

    if degraded or not manifest_profile.include_corpus_sections:
        # Protocol § 1 degraded fallback is state-only; do not append the
        # promotion-queue summary even though it's small.
        return manifest

    state_body = sections[0].body
    remaining_chars = max(0, budget_tokens * CHARS_PER_TOKEN - estimate_tokens(state_body) * CHARS_PER_TOKEN)

    doctrine_budget = int(remaining_chars * 0.40)
    doctrine_entries = _doctrine_order(_doctrine_entries(cortex_dir))
    doctrine_body, doctrine_in, doctrine_cut, doctrine_in_paths, doctrine_cut_paths = _concat_files(
        doctrine_entries,
        doctrine_budget,
        verified_today=verified_today,
        verified_threshold_days=verified_threshold_days,
    )
    manifest.sections.append(
        ManifestSection(
            title="Doctrine (Load-priority `always` first; then recency)",
            body=doctrine_body or "_no Doctrine entries_\n",
            included=doctrine_in,
            truncated=doctrine_cut,
            included_paths=[_rel(Path(p), project_root) for p in doctrine_in_paths],
            truncated_paths=[_rel(Path(p), project_root) for p in doctrine_cut_paths],
            retrieval="grep `.cortex/doctrine/` or run `cortex retrieve --mode bm25`.",
        )
    )
    used_chars = len(doctrine_body)

    plan_budget = int(remaining_chars * 0.25)
    plan_entries = _active_plans(cortex_dir)
    plans_body, plans_in, plans_cut, plans_in_paths, plans_cut_paths = _concat_files(plan_entries, plan_budget)
    manifest.sections.append(
        ManifestSection(
            title="Active Plans",
            body=plans_body or "_no active Plans_\n",
            included=plans_in,
            truncated=plans_cut,
            included_paths=[_rel(Path(p), project_root) for p in plans_in_paths],
            truncated_paths=[_rel(Path(p), project_root) for p in plans_cut_paths],
            retrieval="read `.cortex/plans/` for omitted active plans.",
        )
    )
    used_chars += len(plans_body)

    journal_budget = remaining_chars - used_chars
    journal_entries = _journal_window(cortex_dir, journal_hours, now)
    digest = _latest_digest(cortex_dir)
    if digest is not None and digest not in journal_entries:
        journal_entries = [digest, *journal_entries]
    journal_body, journal_in, journal_cut, journal_in_paths, journal_cut_paths = _concat_files(
        journal_entries,
        max(0, journal_budget),
    )
    manifest.sections.append(
        ManifestSection(
            title=f"Journal — last {journal_hours}h + latest digest",
            body=journal_body or "_no Journal entries in window_\n",
            included=journal_in,
            truncated=journal_cut,
            included_paths=[_rel(Path(p), project_root) for p in journal_in_paths],
            truncated_paths=[_rel(Path(p), project_root) for p in journal_cut_paths],
            retrieval="grep `.cortex/journal/` or run `cortex retrieve --mode bm25`.",
        )
    )

    manifest.promotion_summary = _promotion_summary(cortex_dir)
    return manifest
