"""Structural checks for `.cortex/` used by `cortex doctor`.

Each ``check_*`` function takes a path rooted at the project and returns a
list of :class:`Issue` objects. Checks are additive and independent — the
command orchestrator aggregates and sorts.

Checks implemented here (current slice — SPEC.md v0.5.0):

- Scaffold structure (SPEC_VERSION, protocol.md, templates/, subdirs)
- Seven-field metadata contract on derived layers (map.md, state.md) per § 4.5
- Doctrine frontmatter: Status, Date, Load-priority per § 3.1
- Plan frontmatter: Status, Written, Author, Goal-hash (+ recomputation),
  Updated-by; required sections; grounding citation per §§ 3.4, 4.1, 4.3, 4.9
- Journal filename ``YYYY-MM-DD-<slug>.md`` per § 3.5

Deferred on the v1.0 release path:
promotion-queue invariants (`.index.json` lands in v0.6.0), single-authority-rule
drift detection (§ 4.8), claim-trace audit expansion, and remaining git-derived
Tier-1 checks. Runtime-state Tier-1 triggers stay deferred to v1.x.
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

PLAN_REQUIRED_FIELDS = ("Status", "Written", "Author", "Goal-hash", "Updated-by", "Cites")
PLAN_STATUS_VALUES = ("active", "shipped", "cancelled", "deferred", "blocked")
PLAN_REQUIRED_SECTIONS = (
    "## Why (grounding)",
    "## Success Criteria",
    "## Approach",
    "## Work items",
)

# SPEC § 4.2: every item moved out of a Plan's scope must resolve to a
# durable-layer entry within the same commit. The orphan-deferral check
# scans `## Follow-ups (deferred)` bullets on active plans and warns when
# an item lacks a citation to one of the three durable layers. Filename
# shapes are enforced per SPEC § 2 / § 3.5:
# - `plans/<slug>(.md)` — plain slug, no required prefix
# - `journal/YYYY-MM-DD-<slug>(.md)` — date-prefixed
# - `doctrine/<nnnn>-<slug>(.md)` — 4-digit prefix
# Tightening the shapes prevents typos like `journal/foo` or
# `doctrine/scope` from accidentally clearing the check.
PLAN_FOLLOWUP_CITATION_RE = re.compile(
    r"plans/[A-Za-z0-9._-]+"
    r"|journal/\d{4}-\d{2}-\d{2}-[A-Za-z0-9._-]+"
    r"|doctrine/\d{4}-[A-Za-z0-9._-]+"
)
PLAN_GROUNDING_LINK_RE = re.compile(
    r"(doctrine/|state\.md|journal/)",
    re.IGNORECASE,
)

JOURNAL_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9._-]*\.md$")
DOCTRINE_FILENAME_RE = re.compile(r"^\d{4}-[a-z0-9][a-z0-9._-]*\.md$")

# Conservative heuristic for unscoped CLAUDE.md / AGENTS.md constraints
# (autumngarage cycle-4 finding F2, 2026-04-19): downstream tools like
# sentinel apply CLAUDE.md statements like "No cloud LLMs." globally,
# including to dev-toolchain config, when the original intent was
# app-runtime-only. Constraints that mention LLMs/APIs/providers should
# carry a `(applies to: runtime|toolchain|both)` qualifier so consumers
# can disambiguate. False positives are worse than false negatives here,
# so we require both a constraint keyword AND an LLM/API/provider keyword
# on the same line before flagging.
CONSTRAINT_KEYWORD_RE = re.compile(
    r"\b(no|never|always|forbidden|only|must|don't|do not)\b",
    re.IGNORECASE,
)
LLM_KEYWORD_RE = re.compile(
    # Singular and plural forms — `\b(llm)\b` does not match `LLMs` because
    # `s` is a word character, so common phrasings like "No LLMs." or
    # "Never use APIs." would slip past without the explicit `s?`.
    r"\b(llms?|apis?|providers?|cloud|inference|drafting)\b",
    re.IGNORECASE,
)
SCOPE_QUALIFIER_RE = re.compile(r"\(applies to:", re.IGNORECASE)
CLAUDE_AGENTS_FILES = ("CLAUDE.md", "AGENTS.md")
SCOPE_LOOKAHEAD_LINES = 2


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
    """Return the body's first real ``# Title`` heading, skipping fenced code."""
    fence: str | None = None
    for line in body.splitlines():
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                continue
            if line.startswith("# "):
                return line[2:].strip()
        elif stripped.startswith(fence):
            fence = None
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
    such as ``Load-priority:`` (added in v0.4.0); retrofitting them would
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
        if title is None:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    "Plan missing H1 title (`# <Title>`); required to recompute Goal-hash (SPEC § 4.9).",
                )
            )
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

        if "Updated-by" not in frontmatter:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    "Plan missing required `Updated-by` writer history (SPEC § 3.4).",
                )
            )
        else:
            updated_by = _list_field(frontmatter, "Updated-by")
            if updated_by is None:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        "`Updated-by` must be a block-sequence list (SPEC § 3.4).",
                    )
                )
            elif not updated_by:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        "`Updated-by` must contain at least one writer entry (SPEC § 3.4).",
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
        if success is not None:
            if not success.strip():
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        "Plan `Success Criteria` is empty; must name a concrete signal (SPEC § 4.3).",
                    )
                )
            elif not _success_criteria_is_measurable(success):
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        "Plan `Success Criteria` lacks a concrete signal — needs a numeric "
                        "threshold, a link to a test/dashboard, or a code/path reference "
                        "(SPEC § 4.3).",
                    )
                )

        # Orphan-deferral check (SPEC § 4.2). Only run on active plans —
        # shipped/cancelled plans may have follow-up bullets whose
        # resolution is now in git history rather than a citation, and
        # warning on those would generate noise on every doctor run.
        status_str = frontmatter.get("Status")
        is_active = isinstance(status_str, str) and status_str.strip() == "active"
        if is_active:
            followups = _extract_section(body, "## Follow-ups (deferred)")
            if followups is not None:
                cortex_dir = project_root / ".cortex"
                for raw_line in followups.splitlines():
                    stripped = raw_line.lstrip()
                    if not stripped.startswith(("- ", "* ")):
                        continue
                    bullet_text = stripped[2:].strip()
                    if not bullet_text:
                        continue
                    matches = list(
                        PLAN_FOLLOWUP_CITATION_RE.finditer(bullet_text)
                    )
                    snippet = bullet_text[:80]
                    if len(bullet_text) > 80:
                        snippet += "…"
                    if not matches:
                        issues.append(
                            Issue(
                                Severity.WARNING,
                                rel,
                                f"Plan `Follow-ups (deferred)` item lacks "
                                f"resolution citation per SPEC § 4.2 (needs "
                                f"`plans/<slug>`, `journal/<date>-<slug>`, or "
                                f"`doctrine/<nnnn>-<slug>`): {snippet!r}",
                            )
                        )
                        continue
                    # Citation-shape match found; verify at least one cited
                    # target actually exists AND is not the plan itself.
                    # SPEC § 4.2 requires resolution to *another* durable-
                    # layer entry; a plan citing its own slug is still an
                    # orphan deferral disguised as a self-reference.
                    plan_self_path = f"plans/{plan.stem}"
                    resolutions = [
                        m.group(0)
                        for m in matches
                        if _normalize_layer_path(m.group(0)) != plan_self_path
                        and _resolves_to_existing_layer_entry(cortex_dir, m.group(0))
                    ]
                    if not resolutions:
                        cited = ", ".join(m.group(0) for m in matches)
                        issues.append(
                            Issue(
                                Severity.WARNING,
                                rel,
                                f"Plan `Follow-ups (deferred)` item cites a "
                                f"non-existent target (no matching file "
                                f"under .cortex/) per SPEC § 4.2: cited "
                                f"{cited!r} in {snippet!r}",
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


def _normalize_layer_path(citation: str) -> str:
    """Normalize ``layer/slug(.md)`` to ``layer/slug`` (no extension, no
    sentence-final period). Used for self-reference detection so a plan
    can't pass the orphan-deferral check by citing itself.
    """
    layer, sep, slug = citation.partition("/")
    if not sep:
        return citation
    slug = slug.rstrip(".")
    slug = slug[:-3] if slug.endswith(".md") else slug
    return f"{layer}/{slug}"


def _resolves_to_existing_layer_entry(cortex_dir: Path, citation: str) -> bool:
    """Return True if ``citation`` (e.g. ``plans/foo``, ``journal/2026-04-25-bar``,
    ``doctrine/0005-baz``) resolves to a real file under ``cortex_dir``.

    Tolerates ``.md`` suffix and looks in archive subdirs for Plans and
    Journals (per SPEC § 5.1 retention tiers). Doctrine has no archive —
    superseded entries stay in place.
    """
    layer, sep, slug = citation.partition("/")
    if not sep or not slug:
        return False
    # Strip trailing punctuation that the regex character class greedily
    # consumed from prose (e.g. "see plans/foo.md." has a sentence-final
    # period). Then strip a trailing `.md` suffix if present.
    slug = slug.rstrip(".")
    slug = slug[:-3] if slug.endswith(".md") else slug
    if not slug:
        return False
    filename = f"{slug}.md"
    candidates: tuple[Path, ...]
    if layer == "plans":
        candidates = (
            cortex_dir / "plans" / filename,
            cortex_dir / "plans" / "archive" / filename,
        )
    elif layer == "doctrine":
        candidates = (cortex_dir / "doctrine" / filename,)
    elif layer == "journal":
        archive_root = cortex_dir / "journal" / "archive"
        archive_candidates = tuple(archive_root.glob(f"*/{filename}")) if archive_root.exists() else ()
        candidates = (cortex_dir / "journal" / filename, *archive_candidates)
    else:
        return False
    return any(c.exists() for c in candidates)


_MEASURABLE_SIGNAL_RE = re.compile(
    r"""
    (?:\[[^\]]+\]\([^)]+\))       # markdown link [text](url) — citation of a test/dashboard
    | `[^`]+`                     # inline code / file reference
    | \b\d+                       # any numeric value (thresholds, counts, durations, %)
    | \b(?:PR|pr)\s*\#\s*\d+      # PR reference
    | (?:doctrine|journal|tests?|scripts?|plans)/[A-Za-z0-9._-]+
    """,
    re.VERBOSE,
)


def _success_criteria_is_measurable(section: str) -> bool:
    """Heuristic for SPEC § 4.3's "concrete signal" requirement.

    Accepts the section if it contains at least one of: a markdown link, an
    inline code reference, a numeric value, a ``PR #<n>`` reference, or a
    path into a durable directory (``doctrine/``, ``journal/``, ``tests/``,
    ``scripts/``, ``plans/``). This is deliberately lenient — the goal is to
    reject prose-only "it works well" criteria, not to police wording.
    """
    return bool(_MEASURABLE_SIGNAL_RE.search(section))


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


def _strip_frontmatter_and_fences(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, line)`` pairs for lines that are *not* inside
    YAML frontmatter at the top of the file or fenced code blocks.

    Line numbers are 1-indexed (matching how editors / issue messages display
    them). Frontmatter is recognized only as ``---`` on the first non-empty
    line through the next ``---``. Fences are CommonMark ```` ``` ```` /
    ``~~~`` (matched on the leading marker, like the existing fence-aware
    helpers in this module).
    """
    lines = text.splitlines()
    result: list[tuple[int, str]] = []

    # Detect YAML frontmatter: first non-empty line must be `---`.
    in_frontmatter = False
    frontmatter_end: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "":
            continue
        if line.strip() == "---":
            in_frontmatter = True
            # Find closing `---`.
            for j in range(idx + 1, len(lines)):
                if lines[j].strip() == "---":
                    frontmatter_end = j
                    break
        break

    fence: str | None = None
    for i, line in enumerate(lines):
        if in_frontmatter and frontmatter_end is not None and i <= frontmatter_end:
            continue
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                continue
            result.append((i + 1, line))
        else:
            if stripped.startswith(fence):
                fence = None
            # Lines inside a fence are skipped entirely.
    return result


def check_claude_agents(project_root: Path) -> list[Issue]:
    """Warn on unscoped LLM/API constraints in ``CLAUDE.md`` / ``AGENTS.md``.

    Heuristic (deliberately conservative — false positives are worse than
    false negatives): a line is flagged only if it contains both a
    constraint keyword (no, never, always, forbidden, only, must, don't,
    do not) AND an LLM/API/provider keyword (llm, api, provider, cloud,
    inference, drafting) on the same line, AND none of the line itself or
    the next two lines contains ``(applies to:``. Lines inside YAML
    frontmatter (top-of-file ``---`` block) and fenced code blocks
    (```` ``` ```` / ``~~~``) are skipped.

    Motivation: sentinel's planner reads CLAUDE.md statements like "No
    cloud LLMs." and applies them globally — including to dev-toolchain
    config like ``.sentinel/config.toml``. The intent was app-runtime
    only. Adding ``(applies to: runtime|toolchain|both)`` lets downstream
    tools disambiguate. See autumngarage cycle-4 finding F2 (2026-04-19).
    """
    issues: list[Issue] = []
    for filename in CLAUDE_AGENTS_FILES:
        path = project_root / filename
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text()
        live_lines = _strip_frontmatter_and_fences(text)
        # Build a quick map for the lookahead check; SCOPE_LOOKAHEAD_LINES
        # counts the *next* lines after the matched line, so we look at
        # entries whose 1-indexed line number is in [matched, matched + N].
        line_lookup = dict(live_lines)
        for lineno, content in live_lines:
            if not CONSTRAINT_KEYWORD_RE.search(content):
                continue
            if not LLM_KEYWORD_RE.search(content):
                continue
            window = [
                line_lookup.get(lineno + offset, "")
                for offset in range(SCOPE_LOOKAHEAD_LINES + 1)
            ]
            if any(SCOPE_QUALIFIER_RE.search(w) for w in window):
                continue
            issues.append(
                Issue(
                    Severity.WARNING,
                    filename,
                    f"{filename}:{lineno}: constraint statement lacks "
                    "\"(applies to: runtime|toolchain|both)\" scope qualifier "
                    "— sentinel and other tools may apply this globally.",
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
    issues.extend(check_claude_agents(project_root))
    return sorted(issues, key=lambda i: (i.severity.value, i.path, i.message))
