"""Structural checks for `.cortex/` used by `cortex doctor`.

Each ``check_*`` function takes a path rooted at the project and returns a
list of :class:`Issue` objects. Checks are additive and independent — the
command orchestrator aggregates and sorts.

Checks implemented here (first slice — SPEC.md v0.3.1-dev):

- Scaffold structure (SPEC_VERSION, protocol.md, templates/, subdirs)
- Seven-field metadata contract on derived layers (map.md, state.md) per § 4.5
- Doctrine frontmatter: Status, Date, Load-priority per § 3.1
- Plan frontmatter: Status, Written, Author, Goal-hash (+ recomputation),
  Updated-by; required sections; grounding citation per §§ 3.4, 4.1, 4.3, 4.9
- Journal filename ``YYYY-MM-DD-<slug>.md`` per § 3.5

Deferred (tracked in PLAN.md Phase B): promotion-queue invariants
(`.index.json` not yet emitted), single-authority-rule drift detection
(§ 4.8), and the Tier-1 audit (`cortex doctor --audit`) which requires git
traversal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from cortex import SUPPORTED_SPEC_VERSIONS
from cortex.frontmatter import FrontmatterValue, parse_frontmatter
from cortex.goal_hash import normalize_goal_hash

SEVEN_FIELDS = (
    "Generated",
    "Generator",
    "Sources",
    "Corpus",
    "Omitted",
    "Incomplete",
    "Conflicts-preserved",
)

SCAFFOLD_SUBDIRS = ("doctrine", "plans", "journal", "procedures")

DOCTRINE_REQUIRED_FIELDS = ("Status", "Date", "Load-priority")
DOCTRINE_LOAD_PRIORITY_VALUES = ("default", "always")
DOCTRINE_STATUS_RE = re.compile(r"^(Proposed|Accepted|Superseded-by\s+\d+)\s*$")

PLAN_REQUIRED_FIELDS = ("Status", "Written", "Author", "Goal-hash", "Updated-by")
PLAN_STATUS_VALUES = ("active", "shipped", "cancelled", "deferred", "blocked")
PLAN_REQUIRED_SECTIONS = (
    "## Why (grounding)",
    "## Success Criteria",
    "## Approach",
    "## Work items",
)
PLAN_GROUNDING_LINK_RE = re.compile(
    r"(doctrine/|state\.md|journal/)",
    re.IGNORECASE,
)

JOURNAL_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9._-]*\.md$")
DOCTRINE_FILENAME_RE = re.compile(r"^\d{4}-[a-z0-9][a-z0-9._-]*\.md$")


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    path: str
    message: str


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _iter_markdown(dir_path: Path) -> list[Path]:
    if not dir_path.exists():
        return []
    return sorted(p for p in dir_path.glob("*.md") if p.is_file())


def _list_field(data: dict[str, FrontmatterValue], key: str) -> list[str] | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, list):
        return value
    return None


def _extract_h1(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def check_scaffold(project_root: Path) -> list[Issue]:
    """Validate the scaffold-level files written by ``cortex init``."""
    issues: list[Issue] = []
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.exists():
        issues.append(
            Issue(
                Severity.ERROR,
                _rel(cortex_dir, project_root),
                "`.cortex/` directory does not exist; run `cortex init`.",
            )
        )
        return issues

    spec_version_file = cortex_dir / "SPEC_VERSION"
    if not spec_version_file.exists():
        issues.append(
            Issue(
                Severity.ERROR,
                _rel(spec_version_file, project_root),
                "`.cortex/SPEC_VERSION` missing; re-run `cortex init` or write the version manually.",
            )
        )
    else:
        declared = spec_version_file.read_text().strip()
        declared_major_minor = ".".join(declared.split("-", 1)[0].split(".")[:2])
        if declared_major_minor not in SUPPORTED_SPEC_VERSIONS:
            issues.append(
                Issue(
                    Severity.ERROR,
                    _rel(spec_version_file, project_root),
                    f"declared spec {declared!r} is not supported by this CLI "
                    f"(supports {', '.join(SUPPORTED_SPEC_VERSIONS)}).",
                )
            )

    protocol_file = cortex_dir / "protocol.md"
    if not protocol_file.exists():
        issues.append(
            Issue(
                Severity.ERROR,
                _rel(protocol_file, project_root),
                "`.cortex/protocol.md` missing; Protocol § 1 session-start load will fall back to degraded mode.",
            )
        )

    templates_dir = cortex_dir / "templates"
    if not templates_dir.exists() or not any(templates_dir.rglob("*.md")):
        issues.append(
            Issue(
                Severity.WARNING,
                _rel(templates_dir, project_root),
                "`.cortex/templates/` missing or empty; re-run `cortex init --force`.",
            )
        )

    for sub in SCAFFOLD_SUBDIRS:
        sub_dir = cortex_dir / sub
        if not sub_dir.exists():
            issues.append(
                Issue(
                    Severity.ERROR,
                    _rel(sub_dir, project_root),
                    f"`.cortex/{sub}/` missing; re-run `cortex init`.",
                )
            )
    return issues


def check_derived_layer(layer_path: Path, project_root: Path) -> list[Issue]:
    """Validate the seven-field metadata contract on a derived layer (SPEC § 4.5)."""
    issues: list[Issue] = []
    rel = _rel(layer_path, project_root)
    if not layer_path.exists():
        # Scaffold check emits the "missing .cortex/" error; the derived-layer
        # stub is only expected to exist once .cortex/ does.
        if layer_path.parent.exists():
            issues.append(
                Issue(Severity.ERROR, rel, f"derived layer `{layer_path.name}` missing.")
            )
        return issues

    frontmatter, _body = parse_frontmatter(layer_path.read_text())
    if not frontmatter:
        issues.append(
            Issue(
                Severity.ERROR,
                rel,
                f"derived layer has no YAML frontmatter; SPEC § 4.5 requires the seven metadata fields {list(SEVEN_FIELDS)}.",
            )
        )
        return issues

    for field in SEVEN_FIELDS:
        if field not in frontmatter:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    f"missing required metadata field `{field}` (SPEC § 4.5).",
                )
            )
    return issues


def check_doctrine(project_root: Path) -> list[Issue]:
    """Validate every ``.cortex/doctrine/NNNN-*.md`` entry (SPEC § 3.1)."""
    issues: list[Issue] = []
    doctrine_dir = project_root / ".cortex" / "doctrine"
    for entry in _iter_markdown(doctrine_dir):
        rel = _rel(entry, project_root)

        if not DOCTRINE_FILENAME_RE.match(entry.name):
            issues.append(
                Issue(
                    Severity.WARNING,
                    rel,
                    "Doctrine filename does not follow `NNNN-<slug>.md` convention.",
                )
            )

        text = entry.read_text()
        issues.extend(_check_doctrine_entry_body(rel, text))
    return issues


def _read_doctrine_field(field: str, frontmatter: dict[str, FrontmatterValue], header: str) -> str | None:
    """Return the value of ``field`` from YAML frontmatter or bold-inline markup.

    Per SPEC § 6: Doctrine parsers must accept either form. YAML is checked
    first; bold-inline falls back.
    """
    if field in frontmatter:
        fm_value = frontmatter[field]
        if isinstance(fm_value, str):
            return fm_value.strip()
    match = re.search(rf"\*\*{re.escape(field)}:\*\*\s*(.+)", header)
    if match:
        return match.group(1).strip()
    return None


def _check_doctrine_entry_body(rel: str, text: str) -> list[Issue]:
    """Validate Status / Date / Load-priority on a Doctrine entry.

    Doctrine is immutable-with-supersede (SPEC § 3.1). Historical entries
    whose Status is ``Superseded-by <n>`` predate any post-hoc requirement
    such as ``Load-priority:`` (added in v0.3.1-dev); retrofitting them would
    violate immutability. The validator therefore exempts superseded entries
    from ``Load-priority`` but still requires Status and Date.
    """
    issues: list[Issue] = []
    frontmatter, _body = parse_frontmatter(text)
    header = "\n".join(text.splitlines()[:40])

    status = _read_doctrine_field("Status", frontmatter, header)
    is_superseded = bool(status and status.lower().startswith("superseded-by"))
    if status and not DOCTRINE_STATUS_RE.match(status):
        issues.append(
            Issue(
                Severity.ERROR,
                rel,
                f"Doctrine `Status: {status}` is invalid; SPEC § 3.1 requires "
                "`Proposed`, `Accepted`, or `Superseded-by <n>`.",
            )
        )

    for field in DOCTRINE_REQUIRED_FIELDS:
        if field == "Load-priority" and is_superseded:
            continue

        value = _read_doctrine_field(field, frontmatter, header)
        if value is None:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    f"Doctrine entry missing `{field}` (SPEC § 3.1); set as YAML "
                    f"frontmatter or a bold-inline `**{field}:**` line per SPEC § 6.",
                )
            )
            continue

        if field == "Load-priority" and value not in DOCTRINE_LOAD_PRIORITY_VALUES:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    f"`Load-priority: {value}` is invalid; expected one of "
                    f"{list(DOCTRINE_LOAD_PRIORITY_VALUES)} (SPEC § 3.1).",
                )
            )
    return issues


def check_plans(project_root: Path) -> list[Issue]:
    """Validate every Plan under ``.cortex/plans/`` (SPEC §§ 3.4, 4.1, 4.3, 4.9)."""
    issues: list[Issue] = []
    plans_dir = project_root / ".cortex" / "plans"
    goal_hashes: dict[str, list[str]] = {}

    for plan in _iter_markdown(plans_dir):
        rel = _rel(plan, project_root)
        text = plan.read_text()
        frontmatter, body = parse_frontmatter(text)

        if not frontmatter:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    "Plan missing YAML frontmatter (SPEC § 3.4).",
                )
            )
            continue

        scalar_plan_fields = tuple(f for f in PLAN_REQUIRED_FIELDS if f != "Updated-by")
        for field in scalar_plan_fields:
            value = frontmatter.get(field)
            if value is None:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        f"Plan missing required frontmatter field `{field}` (SPEC § 3.4).",
                    )
                )
                continue
            if not isinstance(value, str) or not value.strip():
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        f"Plan frontmatter field `{field}` must be a non-empty scalar (SPEC § 3.4).",
                    )
                )

        status = frontmatter.get("Status")
        if isinstance(status, str) and status.strip() and status.strip() not in PLAN_STATUS_VALUES:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    f"`Status: {status}` is invalid; expected one of {list(PLAN_STATUS_VALUES)}.",
                )
            )

        declared_hash = frontmatter.get("Goal-hash")
        title = _extract_h1(body)
        if isinstance(declared_hash, str) and declared_hash.strip() and title:
            expected = normalize_goal_hash(title)
            if declared_hash.strip() != expected:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        f"`Goal-hash: {declared_hash}` does not match recomputed value "
                        f"`{expected}` for title {title!r} (SPEC § 4.9).",
                    )
                )
            goal_hashes.setdefault(expected, []).append(rel)

        updated_by = _list_field(frontmatter, "Updated-by")
        if "Updated-by" in frontmatter and updated_by is None:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    "`Updated-by` must be a block-sequence list (SPEC § 3.4).",
                )
            )

        heading_lines = _collect_h2_headings(body)
        for section in PLAN_REQUIRED_SECTIONS:
            if section not in heading_lines:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        f"Plan missing required section `{section}` (SPEC § 3.4).",
                    )
                )

        grounding_section = _extract_section(body, "## Why (grounding)")
        if grounding_section is not None and not PLAN_GROUNDING_LINK_RE.search(grounding_section):
            issues.append(
                Issue(
                    Severity.WARNING,
                    rel,
                    "Plan `Why (grounding)` does not cite doctrine/, state.md, or journal/ (SPEC § 4.1).",
                )
            )

        success = _extract_section(body, "## Success Criteria")
        if success is not None and not success.strip():
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    "Plan `Success Criteria` is empty; must name a concrete signal (SPEC § 4.3).",
                )
            )

    for goal_hash, plans in goal_hashes.items():
        if len(plans) > 1:
            issues.append(
                Issue(
                    Severity.WARNING,
                    ", ".join(plans),
                    f"Multiple Plans share `Goal-hash: {goal_hash}` — multi-writer collision "
                    "(SPEC § 4.9); resolve manually (merge, supersede, or retitle).",
                )
            )
    return issues


def _collect_h2_headings(body: str) -> set[str]:
    """Return the set of ``## `` headings in ``body``, excluding lines inside
    fenced code blocks (```` ``` ```` or ``~~~``). Matches CommonMark fences.
    """
    headings: set[str] = set()
    fence: str | None = None
    for raw_line in body.splitlines():
        stripped = raw_line.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                continue
            if raw_line.startswith("## "):
                headings.add(raw_line.strip())
        elif stripped.startswith(fence):
            fence = None
    return headings


def _extract_section(body: str, heading: str) -> str | None:
    """Return the text between ``heading`` and the next ``## `` heading, or None.

    Fence-aware: ignores ``## `` lines that appear inside ```` ``` ```` or
    ``~~~`` fenced code blocks, so a fenced mention of ``heading`` does not
    register as the start of a section and a fenced ``## X`` inside the
    target section does not terminate it early.
    """
    lines = body.splitlines()
    start: int | None = None
    fence: str | None = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                continue
            if line.strip() == heading:
                start = i
                break
        elif stripped.startswith(fence):
            fence = None
    if start is None:
        return None

    collected: list[str] = []
    fence = None
    for line in lines[start + 1 :]:
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                collected.append(line)
                continue
            if line.startswith("## "):
                break
        elif stripped.startswith(fence):
            fence = None
        collected.append(line)
    return "\n".join(collected)


def check_journal(project_root: Path) -> list[Issue]:
    """Validate journal filenames against the ``YYYY-MM-DD-<slug>.md`` pattern."""
    issues: list[Issue] = []
    journal_dir = project_root / ".cortex" / "journal"
    if not journal_dir.exists():
        return issues
    for entry in sorted(journal_dir.glob("*.md")):
        rel = _rel(entry, project_root)
        if not JOURNAL_FILENAME_RE.match(entry.name):
            issues.append(
                Issue(
                    Severity.WARNING,
                    rel,
                    "Journal filename does not match `YYYY-MM-DD-<slug>.md` (SPEC § 3.5).",
                )
            )
    return issues


def run_all_checks(project_root: Path) -> list[Issue]:
    """Run every check and return aggregated issues sorted by severity then path."""
    issues: list[Issue] = []
    issues.extend(check_scaffold(project_root))
    cortex_dir = project_root / ".cortex"
    if cortex_dir.exists():
        issues.extend(check_derived_layer(cortex_dir / "map.md", project_root))
        issues.extend(check_derived_layer(cortex_dir / "state.md", project_root))
        issues.extend(check_doctrine(project_root))
        issues.extend(check_plans(project_root))
        issues.extend(check_journal(project_root))
    return sorted(issues, key=lambda i: (i.severity.value, i.path, i.message))
