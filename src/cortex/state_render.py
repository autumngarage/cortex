"""Deterministic renderer for `.cortex/state.md`.

`cortex refresh-state` treats State as a derived layer with explicit
provenance. The command edge gathers files from disk; this module keeps the
actual parsing and rendering logic small enough to unit-test against real
fixtures without mocks.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cortex import __version__
from cortex.frontmatter import FrontmatterValue, parse_frontmatter
from cortex.manifests import ManifestInfo, detect_project_manifest

DETERMINISTIC_GENERATED = "2000-01-01T00:00:00+00:00"
STALE_PLAN_DAYS = 14
HAND_OPEN = "<!-- cortex:hand -->"
HAND_CLOSE = "<!-- cortex:end-hand -->"

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+", re.MULTILINE)
_BOLD_FIELD_RE = re.compile(r"^\*\*(?P<key>[^:*]+):\*\*\s*(?P<value>.+?)\s*$", re.MULTILINE)
_PLAIN_FIELD_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9-]*):\s*(?P<value>.+?)\s*$", re.MULTILINE)
_SPEC_VERSION_RE = re.compile(r"\bSpec:\s*([0-9][^\s]+)")


@dataclass(frozen=True)
class SourceFile:
    path: str
    text: str


@dataclass(frozen=True)
class StateInputs:
    project_root: Path
    generated: str
    head_sha: str | None
    spec_version: str | None
    project_manifest: ManifestInfo | None
    package_version: str
    previous_state: str
    plans: list[SourceFile] = field(default_factory=list)
    journal: list[SourceFile] = field(default_factory=list)
    doctrine: list[SourceFile] = field(default_factory=list)
    templates: list[SourceFile] = field(default_factory=list)
    case_studies: list[SourceFile] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    incomplete: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanState:
    slug: str
    path: str
    title: str
    status: str
    goal_hash: str
    completed: int
    total: int
    updated_at: datetime | None

    @property
    def completion_percent(self) -> int:
        if self.total == 0:
            return 100
        return round(self.completed * 100 / self.total)

    @property
    def has_open_checkboxes(self) -> bool:
        return self.completed < self.total


@dataclass(frozen=True)
class JournalState:
    path: str
    title: str
    date: str
    type: str
    stale_by: str | None
    shipped: bool


def build_state_inputs(project_root: Path, *, deterministic: bool = False) -> StateInputs:
    """Read primary sources for State regeneration.

    Unreadable or malformed optional inputs are recorded in ``incomplete`` with
    a path and reason so the rendered layer never pretends the walk was clean.
    """
    project_root = project_root.resolve()
    cortex_dir = project_root / ".cortex"
    incomplete: list[str] = []

    def read_optional(path: Path) -> str:
        try:
            if path.exists():
                return path.read_text()
        except OSError as exc:
            incomplete.append(f"{_rel(project_root, path)} — unreadable: {exc}")
        return ""

    previous_state = read_optional(cortex_dir / "state.md")
    spec_version = _read_spec_version(project_root, incomplete)
    project_manifest = detect_project_manifest(project_root)
    if project_manifest and project_manifest.error:
        incomplete.append(f"{project_manifest.filename} — unreadable: {project_manifest.error}")
    head_sha = _read_head_sha(project_root, incomplete)
    omitted = []
    if not (cortex_dir / ".index.json").exists():
        omitted.append(".cortex/.index.json — absent; promotion queue index ships in a later lifecycle tier")

    return StateInputs(
        project_root=project_root,
        generated=(
            DETERMINISTIC_GENERATED
            if deterministic
            else datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        ),
        head_sha=head_sha,
        spec_version=spec_version,
        project_manifest=project_manifest,
        package_version=__version__,
        previous_state=previous_state,
        plans=_read_tree(project_root, cortex_dir / "plans", "*.md", incomplete),
        journal=_read_tree(project_root, cortex_dir / "journal", "*.md", incomplete),
        doctrine=_read_tree(project_root, cortex_dir / "doctrine", "*.md", incomplete),
        templates=_read_tree(project_root, cortex_dir / "templates", "*.md", incomplete),
        case_studies=_read_tree(project_root, project_root / "docs" / "case-studies", "*.md", incomplete),
        omitted=omitted,
        incomplete=incomplete,
    )


def render_state(inputs: StateInputs, *, now: datetime | None = None) -> str:
    """Render a SPEC-conformant `.cortex/state.md` body."""
    now = now or datetime.now(UTC)
    plans, plan_errors = _parse_plans(inputs.plans)
    journals, journal_errors = _parse_journals(inputs.journal)
    incomplete = [*inputs.incomplete, *plan_errors, *journal_errors]
    hand_regions = extract_hand_regions(inputs.previous_state)

    lines: list[str] = []
    lines.extend(_render_header(inputs, incomplete))
    lines.extend(["", "# Project State", ""])
    if hand_regions:
        lines.extend(hand_regions)
        lines.append("")
    lines.extend(_render_active_plans(plans))
    lines.append("")
    lines.extend(_render_shipped_recently(journals))
    lines.append("")
    lines.extend(_render_stale(plans, journals, now=now))
    return "\n".join(lines).rstrip() + "\n"


def extract_hand_regions(text: str) -> list[str]:
    """Return hand-authored marker regions, preserving bytes as text verbatim."""
    if not text:
        return []
    pattern = re.compile(
        rf"(?m)^{re.escape(HAND_OPEN)}\r?\n.*?^{re.escape(HAND_CLOSE)}\s*$",
        re.DOTALL,
    )
    return [match.group(0).rstrip("\n") for match in pattern.finditer(text)]


def _render_header(inputs: StateInputs, incomplete: list[str]) -> list[str]:
    sources = _source_lines(inputs)
    corpus = (
        f"{len(inputs.journal)} Journal entries, {len(inputs.plans)} Plans, "
        f"{len(inputs.doctrine)} Doctrine entries, {len(inputs.templates)} Templates, "
        f"{len(inputs.case_studies)} Case studies"
    )
    return [
        "---",
        f"Generated: {inputs.generated}",
        f"Generator: cortex refresh-state v{inputs.package_version}",
        "Sources:",
        *[f"  - {line}" for line in sources],
        f"Corpus: {corpus}",
        "Omitted:",
        *(_yaml_list(inputs.omitted) if inputs.omitted else ["  []"]),
        "Incomplete:",
        *(_yaml_list(incomplete) if incomplete else ["  []"]),
        "Conflicts-preserved: []",
        f"Spec: {inputs.spec_version or 'unknown'}",
        "---",
    ]


def _source_lines(inputs: StateInputs) -> list[str]:
    journal_dates = _journal_date_range(inputs.journal)
    return [
        f"HEAD sha: {inputs.head_sha or 'unavailable'}",
        f".cortex/plans/*.md ({len(inputs.plans)} files)",
        f".cortex/journal/*.md ({len(inputs.journal)} entries{journal_dates})",
        f".cortex/doctrine/*.md ({len(inputs.doctrine)} entries)",
        f".cortex/templates/**/*.md ({len(inputs.templates)} templates)",
        f"docs/case-studies/*.md ({len(inputs.case_studies)} case studies)",
        f"SPEC version: {inputs.spec_version or 'unknown'}",
        _manifest_source_line(inputs.project_manifest, inputs.package_version),
    ]


def _manifest_source_line(manifest: ManifestInfo | None, package_version: str) -> str:
    if manifest is None:
        return f"(no project manifest detected) + cortex package version: {package_version}"
    detail = f": {manifest.detail}" if manifest.detail else ""
    return f"{manifest.filename}{detail} + cortex package version: {package_version}"


def _render_active_plans(plans: list[PlanState]) -> list[str]:
    lines = ["## Active plans", ""]
    active = sorted((p for p in plans if p.status == "active"), key=lambda p: p.slug)
    if not active:
        lines.append("- none")
        return lines
    for plan in active:
        lines.append(
            f"- `{plan.slug}` — {plan.title}; Goal-hash `{plan.goal_hash}`; "
            f"{plan.completion_percent}% complete ({plan.completed}/{plan.total} checkboxes)"
        )
    return lines


def _render_shipped_recently(journals: list[JournalState]) -> list[str]:
    lines = ["## Shipped recently", ""]
    shipped = sorted((j for j in journals if j.shipped), key=lambda j: (j.date, j.path))
    if not shipped:
        lines.append("- none")
        return lines
    for entry in shipped:
        lines.append(f"- **{entry.date}** — {entry.title} (`{entry.path}`, Type: {entry.type})")
    return lines


def _render_stale(plans: list[PlanState], journals: list[JournalState], *, now: datetime) -> list[str]:
    lines = ["## Stale-now / handle-later", ""]
    stale_cutoff = now - timedelta(days=STALE_PLAN_DAYS)
    stale_plans = [
        p
        for p in plans
        if p.status == "active"
        and p.has_open_checkboxes
        and p.updated_at is not None
        and p.updated_at < stale_cutoff
    ]
    stale_journals = [j for j in journals if j.stale_by]
    if not stale_plans and not stale_journals:
        lines.append("- none")
        return lines
    for plan in sorted(stale_plans, key=lambda p: p.slug):
        updated = plan.updated_at.date().isoformat() if plan.updated_at else "unknown"
        lines.append(f"- `{plan.slug}` — active plan stale since {updated}; open checkboxes remain")
    for entry in sorted(stale_journals, key=lambda j: (j.stale_by or "", j.path)):
        lines.append(f"- `{entry.path}` — Stale-by: {entry.stale_by}")
    return lines


def _parse_plans(files: list[SourceFile]) -> tuple[list[PlanState], list[str]]:
    plans: list[PlanState] = []
    errors: list[str] = []
    for file in files:
        try:
            frontmatter, body = parse_frontmatter(file.text)
            status = _scalar(frontmatter.get("Status")) or "unknown"
            title = _title(body) or file.path.rsplit("/", 1)[-1].removesuffix(".md")
            completed, total = _checkbox_counts(body)
            plans.append(
                PlanState(
                    slug=file.path.rsplit("/", 1)[-1].removesuffix(".md"),
                    path=file.path,
                    title=title,
                    status=status.strip(),
                    goal_hash=_scalar(frontmatter.get("Goal-hash")) or "unknown",
                    completed=completed,
                    total=total,
                    updated_at=_last_updated_at(frontmatter.get("Updated-by")),
                )
            )
        except (ValueError, TypeError) as exc:
            errors.append(f"{file.path} — plan parse error: {exc}")
    return plans, errors


def _parse_journals(files: list[SourceFile]) -> tuple[list[JournalState], list[str]]:
    journals: list[JournalState] = []
    errors: list[str] = []
    for file in files:
        try:
            frontmatter, body = parse_frontmatter(file.text)
            type_ = _field(file.text, frontmatter, "Type") or "unknown"
            stale_by = _field(file.text, frontmatter, "Stale-by")
            title = _title(body or file.text) or file.path.rsplit("/", 1)[-1].removesuffix(".md")
            date = _field(file.text, frontmatter, "Date") or _date_from_filename(file.path) or "unknown-date"
            shipped = type_ in {"release", "pr-merged"} or (
                type_ == "plan-transition" and _looks_shipped(file.text)
            )
            journals.append(
                JournalState(
                    path=file.path,
                    title=title,
                    date=date,
                    type=type_,
                    stale_by=stale_by,
                    shipped=shipped,
                )
            )
        except (ValueError, TypeError) as exc:
            errors.append(f"{file.path} — journal parse error: {exc}")
    return journals, errors


def _read_tree(project_root: Path, dir_path: Path, pattern: str, incomplete: list[str]) -> list[SourceFile]:
    if not dir_path.exists():
        incomplete.append(f"{_rel(project_root, dir_path)} — missing source directory")
        return []
    files: list[SourceFile] = []
    for path in sorted(dir_path.rglob(pattern)):
        if not path.is_file():
            continue
        try:
            files.append(SourceFile(path=_rel(project_root, path), text=path.read_text()))
        except OSError as exc:
            incomplete.append(f"{_rel(project_root, path)} — unreadable: {exc}")
    return files


def _read_head_sha(project_root: Path, incomplete: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        incomplete.append("HEAD sha — git executable not found")
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _read_spec_version(project_root: Path, incomplete: list[str]) -> str | None:
    version_file = project_root / ".cortex" / "SPEC_VERSION"
    try:
        if version_file.exists():
            value = version_file.read_text().strip()
            if value:
                return value
    except OSError as exc:
        incomplete.append(f"{_rel(project_root, version_file)} — unreadable: {exc}")
    spec = project_root / "SPEC.md"
    try:
        if spec.exists():
            match = _SPEC_VERSION_RE.search(spec.read_text())
            if match:
                return match.group(1)
    except OSError as exc:
        incomplete.append(f"{_rel(project_root, spec)} — unreadable: {exc}")
    return None


def _yaml_list(items: list[str]) -> list[str]:
    return [f"  - {item}" for item in items]


def _field(text: str, frontmatter: dict[str, FrontmatterValue], key: str) -> str | None:
    value = _scalar(frontmatter.get(key))
    if value:
        return value.strip()
    for match in _BOLD_FIELD_RE.finditer(text):
        if match.group("key") == key:
            return match.group("value").strip()
    for match in _PLAIN_FIELD_RE.finditer(text):
        if match.group("key") == key:
            return match.group("value").strip()
    return None


def _scalar(value: FrontmatterValue | None) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _title(body: str) -> str | None:
    match = _H1_RE.search(body)
    return match.group(1).strip() if match else None


def _checkbox_counts(body: str) -> tuple[int, int]:
    marks = [match.group("mark") for match in _CHECKBOX_RE.finditer(body)]
    completed = sum(1 for mark in marks if mark.lower() == "x")
    return completed, len(marks)


def _last_updated_at(value: FrontmatterValue | None) -> datetime | None:
    items: list[str]
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [value]
    else:
        return None
    parsed: list[datetime] = []
    for item in items:
        match = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})", item)
        if not match:
            continue
        parsed.append(datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC))
    if not parsed:
        return None
    return max(parsed)


def _looks_shipped(text: str) -> bool:
    return bool(re.search(r"\b(Status:\s*shipped|status moved to shipped|→\s*shipped|active\s*->\s*shipped)\b", text, re.I))


def _date_from_filename(path: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", path)
    return match.group(1) if match else None


def _journal_date_range(files: list[SourceFile]) -> str:
    dates = sorted(d for file in files if (d := _date_from_filename(file.path)))
    if not dates:
        return ""
    return f", {dates[0]}..{dates[-1]}"


def _rel(project_root: Path, path: Path) -> str:
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.as_posix()
