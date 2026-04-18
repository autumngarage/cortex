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


def _check_doctrine_entry_body(rel: str, text: str) -> list[Issue]:
    """Validate the Status / Date / Load-priority fields of a Doctrine entry.

    Per SPEC § 6: Doctrine, Journal, and Procedures take their scalar fields
    as **either** bold-inline markdown (``**Status:** Accepted``) **or** YAML
    frontmatter. Parsers MUST accept either form. YAML frontmatter is checked
    first because it's structurally unambiguous; if absent we fall back to
    scanning for bold-inline patterns.
    """
    issues: list[Issue] = []
    frontmatter, _body = parse_frontmatter(text)
    header = "\n".join(text.splitlines()[:40])

    for field in DOCTRINE_REQUIRED_FIELDS:
        value: str | None = None
        if field in frontmatter:
            fm_value = frontmatter[field]
            if isinstance(fm_value, str):
                value = fm_value.strip()
        if value is None:
            match = re.search(rf"\*\*{re.escape(field)}:\*\*\s*(.+)", header)
            if match:
                value = match.group(1).strip()

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

        for field in PLAN_REQUIRED_FIELDS:
            if field not in frontmatter:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        f"Plan missing required frontmatter field `{field}` (SPEC § 3.4).",
                    )
                )

        status = frontmatter.get("Status")
        if isinstance(status, str) and status not in PLAN_STATUS_VALUES:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    f"`Status: {status}` is invalid; expected one of {list(PLAN_STATUS_VALUES)}.",
                )
            )

        declared_hash = frontmatter.get("Goal-hash")
        title = _extract_h1(body)
        if isinstance(declared_hash, str) and title:
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

        for section in PLAN_REQUIRED_SECTIONS:
            if section not in body:
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


def _extract_section(body: str, heading: str) -> str | None:
    """Return the text between ``heading`` and the next ``## `` heading, or None."""
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return None
    collected: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
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
