"""v0.6.0 invariant checks for `cortex doctor`.

These checks sit beside the structural validators in `cortex.validation`.
They all take a project root and return validation Issues so the command can
preserve one rendering and exit-code policy.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from cortex.audit import (
    DEFAULT_WINDOW_DAYS,
    JOURNAL_MATCH_WINDOW_HOURS,
    load_journal_entries,
)
from cortex.frontmatter import FrontmatterValue, parse_frontmatter
from cortex.index import read_index
from cortex.plans import iter_plan_files
from cortex.state_migration import is_legacy_hand_authored_state
from cortex.validation import SEVEN_FIELDS, Issue, Severity

CANONICAL_OWNERSHIP_RE = re.compile(
    r"^(ROADMAP|STATUS|PLAN|PLANS|NEXT|TODO)\.md$",
    re.IGNORECASE,
)
DOCTRINE_0007_SECTION = ("doctrine", "0007")
DOCTRINE_0007_URL = (
    "https://github.com/autumngarage/cortex/blob/main/"
    ".cortex/doctrine/0007-canonical-ownership-of-state-and-plans.md"
)

DEFAULT_FALLBACK_DOCTRINE_THRESHOLD = 20
DEFAULT_FALLBACK_JOURNAL_THRESHOLD = 100
DEFAULT_DELETION_LINE_THRESHOLD = 100
DEFAULT_GENERATED_FRESHNESS_DAYS = 7
DEFAULT_RETENTION_DAYS = 30
DEFAULT_JOURNAL_WARM_MAX = 200
DEFAULT_STALE_CHECKBOX_WINDOW_DAYS = 14
DEFAULT_STATE_JOURNAL_STALENESS_DAYS = 7
SOURCE_FRESHNESS_SLOP = timedelta(seconds=1)
STALE_CHECKBOX_BYPASS_MARKER = "<!-- cortex:no-stale-check -->"
AGENT_GUIDANCE_FILES = ("CLAUDE.md", "AGENTS.md")
PROTOCOL_IMPORT_MARKER = "@.cortex/protocol.md"
STATE_IMPORT_MARKER = "@.cortex/state.md"
MANIFEST_COMMAND_RE = re.compile(r"\bcortex\s+manifest\b")
STATE_SOURCE_DIRS = (
    ".cortex/plans",
    ".cortex/journal",
    ".cortex/doctrine",
    ".cortex/templates",
    "docs/case-studies",
)
STATE_SOURCE_FILES = (
    ".cortex/protocol.md",
    ".cortex/SPEC_VERSION",
    "SPEC.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "Package.swift",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)


def run_plain_checks(project_root: Path) -> list[Issue]:
    """Checks that run on plain `cortex doctor`."""

    checks = (
        check_append_only_journal,
        check_immutable_doctrine,
        check_cli_less_fallback,
        check_generated_layers,
        check_canonical_ownership,
        check_semantic_retrieval_runtime,
        check_stale_plan_checkboxes,
        check_stale_pickup_pointers,
        check_stale_state_current_work,
        check_legacy_state_migration_needed,
        check_state_journal_staleness,
    )
    issues: list[Issue] = []
    for check in checks:
        issues.extend(check(project_root))
    return sorted(issues, key=lambda i: (i.severity.value, i.path, i.message))


def check_semantic_retrieval_runtime(project_root: Path) -> list[Issue]:
    """Surface whether semantic retrieval (S2) is available on this machine.

    Gated on the user having opted into retrieve (i.e. ``.cortex/.index/``
    exists — built by ``cortex refresh-index --retrieve`` or auto-rebuilt
    after ``cortex retrieve``). Fresh scaffolds with no retrieve usage stay
    silent; only projects actually using retrieve see the runtime warning.

    Warning, not error: BM25 mode keeps working without these deps, and
    aarch64 Linux installs are documented to degrade gracefully.
    """

    if not (project_root / ".cortex" / ".index").exists():
        return []
    issues: list[Issue] = []
    missing: list[str] = []
    try:
        import sqlite_vec  # type: ignore[import-not-found] # noqa: F401
    except ImportError:
        missing.append("sqlite-vec")
    try:
        import fastembed  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        missing.append("fastembed")
    if missing:
        joined = " + ".join(missing)
        issues.append(
            Issue(
                Severity.WARNING,
                "",
                f"semantic retrieval unavailable: {joined} not importable. "
                "Run `pip install 'cortex[semantic]'` to enable "
                "`cortex retrieve --mode hybrid|semantic`. BM25 mode is unaffected. "
                "Note: aarch64 Linux lacks onnxruntime PyPI wheels.",
            )
        )
    return issues


def run_audit_checks(project_root: Path, *, since_days: int = DEFAULT_WINDOW_DAYS) -> list[Issue]:
    """Checks gated behind `cortex doctor --audit`."""

    issues: list[Issue] = []
    issues.extend(check_promotion_queue(project_root))
    issues.extend(check_t1_4_deletions(project_root, since_days=since_days))
    issues.extend(check_config_toml_schema(project_root))
    issues.extend(check_retention_visibility(project_root))
    return sorted(issues, key=lambda i: (i.severity.value, i.path, i.message))


def check_append_only_journal(project_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    journal_dir = project_root / ".cortex" / "journal"
    if not journal_dir.exists():
        return issues
    for entry in sorted(journal_dir.glob("*.md")):
        for sha in _modified_commits(project_root, entry):
            if _diff_only_allowed_frontmatter(project_root, sha, entry, {"Updated-by"}):
                continue
            issues.append(
                Issue(
                    Severity.WARNING,
                    _rel(entry, project_root),
                    f"journal {entry.name} modified at commit {sha[:12]} — append-only invariant violated",
                )
            )
    return issues


def check_immutable_doctrine(project_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    doctrine_dir = project_root / ".cortex" / "doctrine"
    if not doctrine_dir.exists():
        return issues
    grandfather_commits = _doctrine_append_only_grandfather_commits(project_root)
    for entry in sorted(doctrine_dir.glob("*.md")):
        for sha in _modified_commits(project_root, entry):
            if any(sha.startswith(g) for g in grandfather_commits):
                continue
            if _diff_only_allowed_frontmatter(project_root, sha, entry, {"Status"}):
                continue
            issues.append(
                Issue(
                    Severity.WARNING,
                    _rel(entry, project_root),
                    f"doctrine {entry.name} modified at commit {sha[:12]} — immutable Doctrine invariant violated",
                )
            )
    return issues


def check_promotion_queue(project_root: Path) -> list[Issue]:
    index_path = project_root / ".cortex" / ".index.json"
    if not index_path.exists():
        return []
    rel = _rel(index_path, project_root)
    try:
        data = read_index(index_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [Issue(Severity.WARNING, rel, f"promotion queue unreadable: {exc}")]
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return [
            Issue(
                Severity.WARNING,
                rel,
                "promotion queue malformed: top-level `candidates` must be a list",
            )
        ]
    issues: list[Issue] = []
    ids = [c.get("id") for c in candidates if isinstance(c, dict)]
    for candidate_id, count in Counter(ids).items():
        if candidate_id and count > 1:
            issues.append(
                Issue(
                    Severity.WARNING,
                    rel,
                    f"promotion queue duplicate candidate id `{candidate_id}`",
                )
            )
    today = date.today()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            issues.append(
                Issue(Severity.WARNING, rel, "promotion queue candidate is not an object")
            )
            continue
        source = candidate.get("source")
        if isinstance(source, str):
            if not source.startswith(".cortex/journal/"):
                issues.append(
                    Issue(
                        Severity.WARNING,
                        rel,
                        f"promotion queue source `{source}` is not under .cortex/journal/",
                    )
                )
            elif not (project_root / source).is_file():
                issues.append(
                    Issue(
                        Severity.WARNING, rel, f"promotion queue source `{source}` does not exist"
                    )
                )
        promoted_to = candidate.get("promoted_to")
        if isinstance(promoted_to, str) and promoted_to:
            target = promoted_to
            if not target.endswith(".md"):
                target = f"{target}.md"
            if target.startswith("doctrine/"):
                target = f".cortex/{target}"
            if not target.startswith(".cortex/doctrine/"):
                issues.append(
                    Issue(
                        Severity.WARNING,
                        rel,
                        f"promotion queue promoted_to `{promoted_to}` is not under .cortex/doctrine/",
                    )
                )
            elif not (project_root / target).is_file():
                issues.append(
                    Issue(
                        Severity.WARNING,
                        rel,
                        f"promotion queue promoted_to `{promoted_to}` does not exist",
                    )
                )
        last_touched = _parse_date(candidate.get("last_touched"))
        age_days = candidate.get("age_days")
        if last_touched is not None and isinstance(age_days, int):
            expected = (today - last_touched).days
            if abs(expected - age_days) > 1:
                issues.append(
                    Issue(
                        Severity.WARNING,
                        rel,
                        f"promotion queue candidate `{candidate.get('id', '<unknown>')}` age_days={age_days} "
                        f"does not match last_touched={last_touched.isoformat()}",
                    )
                )
    return issues


def check_cli_less_fallback(project_root: Path) -> list[Issue]:
    cortex_dir = project_root / ".cortex"
    doctrine_count = (
        len(list((cortex_dir / "doctrine").glob("*.md")))
        if (cortex_dir / "doctrine").exists()
        else 0
    )
    journal_count = (
        len(list((cortex_dir / "journal").glob("*.md"))) if (cortex_dir / "journal").exists() else 0
    )
    if (
        doctrine_count <= DEFAULT_FALLBACK_DOCTRINE_THRESHOLD
        and journal_count <= DEFAULT_FALLBACK_JOURNAL_THRESHOLD
    ):
        return []

    issues: list[Issue] = []
    for filename in AGENT_GUIDANCE_FILES:
        path = project_root / filename
        if not path.is_file():
            continue
        text = path.read_text()
        has_fallback_imports = (
            PROTOCOL_IMPORT_MARKER in text
            and STATE_IMPORT_MARKER in text
        )
        if has_fallback_imports and not MANIFEST_COMMAND_RE.search(text):
            issues.append(
                Issue(
                    Severity.WARNING,
                    filename,
                    "agent guidance uses fallback-only Cortex imports while "
                    "corpus exceeds fallback threshold "
                    f"({doctrine_count} Doctrine, {journal_count} Journal); "
                    "document `cortex manifest --budget <N>` as the preferred "
                    "session-start path and keep direct @imports as CLI-unavailable fallback.",
                )
            )
    return issues


def check_t1_4_deletions(
    project_root: Path,
    *,
    since_days: int = DEFAULT_WINDOW_DAYS,
    line_threshold: int | None = None,
) -> list[Issue]:
    threshold = line_threshold or _protocol_deletion_threshold(project_root)
    try:
        rows = _deleted_files(project_root, since_days=since_days)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        return [
            Issue(
                Severity.WARNING,
                "",
                f"T1.4 deletion audit unavailable: {_format_subprocess_error(exc)}",
            )
        ]
    journal = load_journal_entries(project_root)
    issues: list[Issue] = []
    window = timedelta(hours=JOURNAL_MATCH_WINDOW_HOURS)
    for sha, commit_date, deleted_lines, path in rows:
        if deleted_lines <= threshold:
            continue
        matched = any(
            entry.type_ == "decision" and abs(entry.date - commit_date) <= window
            for entry in journal
        )
        if not matched:
            issues.append(
                Issue(
                    Severity.WARNING,
                    path,
                    f"T1.4 deletion audit: {deleted_lines}-line file deleted at commit {sha[:12]} "
                    "without a Type: decision Journal entry within 72h",
                )
            )
    return issues


def check_generated_layers(project_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    for path in _generated_layer_paths(project_root):
        rel = _rel(path, project_root)
        try:
            frontmatter, _body = parse_frontmatter(path.read_text())
        except OSError as exc:
            issues.append(Issue(Severity.WARNING, rel, f"generated layer unreadable: {exc}"))
            continue
        if not frontmatter:
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    "generated layer has no YAML frontmatter; SPEC § 4.3 requires provenance fields",
                )
            )
            continue
        for field in SEVEN_FIELDS:
            if field not in frontmatter:
                issues.append(
                    Issue(
                        Severity.ERROR,
                        rel,
                        f"generated layer missing `{field}` provenance field (SPEC § 4.5)",
                    )
                )
        generated = _parse_datetime(frontmatter.get("Generated"))
        if generated is not None:
            age = datetime.now(UTC) - generated.astimezone(UTC)
            if age > timedelta(days=DEFAULT_GENERATED_FRESHNESS_DAYS):
                issues.append(
                    Issue(
                        Severity.WARNING,
                        rel,
                        "layer is stale; rerun `cortex refresh-state`",
                    )
                )
            if path.name == "state.md":
                newer = _newer_state_source(project_root, generated)
                if newer is not None:
                    source_rel, changed_at = newer
                    issues.append(
                        Issue(
                            Severity.WARNING,
                            rel,
                            "state.md generated before source changed "
                            f"({source_rel} at {changed_at.isoformat()}); "
                            "rerun `cortex refresh-state`",
                        )
                    )
    return issues


def check_config_toml_schema(project_root: Path) -> list[Issue]:
    path = project_root / ".cortex" / "config.toml"
    if not path.exists():
        return []
    rel = _rel(path, project_root)
    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        return [Issue(Severity.ERROR, rel, f"could not read config.toml: {exc}")]
    except tomllib.TOMLDecodeError as exc:
        return [Issue(Severity.ERROR, rel, f"could not parse config.toml: {exc}")]
    if not isinstance(data, dict):
        return [Issue(Severity.ERROR, rel, "config.toml top-level value must be a table")]
    issues: list[Issue] = []
    audit = data.get("audit-instructions")
    if isinstance(audit, dict):
        # gh_release intentionally absent — it was schema-validated but not
        # parsed by config.AuditInstructionsConfig; removed in cortex#93. The
        # audit uses github_repos (list of repos) instead. Keys here must
        # match what config.load_audit_instructions_config actually reads.
        audit_schema: dict[str, str] = {
            "homebrew_tap": "optional-string",
            "siblings": "optional-string-list",
            "pypi_package": "optional-string",
            "urls": "optional-string-list",
            "scan_files": "optional-string-list",
            "github_repos": "optional-string-list",
            "github_releases": "optional-string-list",
            "paas_repos": "optional-string-list",
        }
        issues.extend(_validate_table(rel, "audit-instructions", audit, audit_schema))
    refresh_index = data.get("refresh-index")
    if isinstance(refresh_index, dict):
        # Schema mirrors config.RefreshIndexConfig fields. Added in cortex#94
        # (was consumed by config.py but missing here, so unknown keys
        # silently passed and typos in candidate_patterns went undetected).
        issues.extend(
            _validate_table(
                rel,
                "refresh-index",
                refresh_index,
                {"candidate_patterns": "optional-string-list"},
            )
        )
    doctrine = data.get(DOCTRINE_0007_SECTION[0])
    doctrine_0007 = doctrine.get(DOCTRINE_0007_SECTION[1]) if isinstance(doctrine, dict) else None
    if isinstance(doctrine_0007, dict):
        issues.extend(
            _validate_table(
                rel,
                "doctrine.0007",
                doctrine_0007,
                {"allowed_root_files": "string-list"},
            )
        )
    doctrine_append_only = doctrine.get("append-only") if isinstance(doctrine, dict) else None
    if isinstance(doctrine_append_only, dict):
        issues.extend(
            _validate_table(
                rel,
                "doctrine.append-only",
                doctrine_append_only,
                {"grandfather-commits": "optional-string-list"},
            )
        )
    doctor = data.get("doctor")
    stale_checkbox = (
        doctor.get("stale-checkbox") if isinstance(doctor, dict) else None
    )
    if isinstance(stale_checkbox, dict):
        # Schema mirrors the `check_stale_plan_checkboxes` config reader
        # added for cortex#100. Catches typos like `window_day` or wrong
        # types before they silently fall back to the default window.
        issues.extend(
            _validate_table(
                rel,
                "doctor.stale-checkbox",
                stale_checkbox,
                {"window_days": "positive-int"},
            )
        )
    state_staleness = (
        doctor.get("state-staleness") if isinstance(doctor, dict) else None
    )
    if isinstance(state_staleness, dict):
        issues.extend(
            _validate_table(
                rel,
                "doctor.state-staleness",
                state_staleness,
                {"window_days": "positive-int"},
            )
        )
    return issues


def check_retention_visibility(project_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    cutoff = date.today() - timedelta(days=DEFAULT_RETENTION_DAYS)
    plans_dir = project_root / ".cortex" / "plans"
    if plans_dir.exists():
        for plan in iter_plan_files(project_root):
            fields, _body = parse_frontmatter(plan.read_text())
            status = _field_str(fields, "Status")
            if status not in {"shipped", "cancelled", "superseded"}:
                continue
            plan_date = _parse_date(_field_str(fields, "Date") or _field_str(fields, "Written"))
            if plan_date is None:
                plan_date = _first_commit_date(project_root, plan)
            if plan_date is not None and plan_date < cutoff:
                issues.append(
                    Issue(
                        Severity.WARNING,
                        _rel(plan, project_root),
                        "plan eligible for archive to `.cortex/plans/archive/`; "
                        "v0.6.0 surfaces this; destructive cleanup is parked v1.x",
                    )
                )
    journal_dir = project_root / ".cortex" / "journal"
    if journal_dir.exists():
        warm = [p for p in journal_dir.glob("*.md") if (_journal_date(p) or date.today()) < cutoff]
        if len(warm) > DEFAULT_JOURNAL_WARM_MAX:
            issues.append(
                Issue(
                    Severity.WARNING,
                    ".cortex/journal",
                    "warm journal threshold exceeded; consolidation to digest is overdue per SPEC § 5.1",
                )
            )
    return issues


def check_stale_plan_checkboxes(
    project_root: Path,
    *,
    window_days: int | None = None,
) -> list[Issue]:
    """Warn when active-plan `- [ ]` items overlap with recent shipped journal entries.

    Failure mode (cortex#100): after a heavy shipping marathon, plan checkboxes
    can stay unchecked even though the corresponding work shipped. ``cortex
    next`` then ranks already-shipped work as P0 because the plan is stale.

    For each ``- [ ]`` checkbox in an active plan, scan ``Type: release`` and
    ``Type: pr-merged`` journal entries within ``window_days`` (default
    14, configurable via ``[doctor.stale-checkbox] window_days``). The check
    extracts the journal entry's ``## What shipped`` (or ``## What landed``)
    section and computes overlap signals — shared ``PR #N`` references,
    shared ``briefs/<name>.md`` paths, shared doctrine numbers, and
    distinctive multi-token phrases. Warn when at least one strong signal
    fires, or when ≥2 phrase matches do.

    Bypass: a checkbox annotated ``<!-- cortex:no-stale-check -->`` is exempt
    (for aspirational items like "sustained-work period" that legitimately
    overlap with release-mention prose without being shipped).

    Warning, not error: false positives are possible (e.g., a release entry
    mentioning a future plan item). Authors review and either flip the
    checkbox or add the bypass annotation.
    """

    journal_dir = project_root / ".cortex" / "journal"
    if not (project_root / ".cortex" / "plans").exists() or not journal_dir.exists():
        return []
    effective_window = (
        window_days
        if window_days is not None
        else _stale_checkbox_window_days(project_root)
    )
    journal_signals = _collect_recent_shipped_signals(journal_dir, effective_window)
    if not journal_signals:
        return []
    issues: list[Issue] = []
    for plan in iter_plan_files(project_root):
        try:
            text = plan.read_text()
        except OSError:
            continue
        fields, body = parse_frontmatter(text)
        if _field_str(fields, "Status") != "active":
            continue
        for line in body.splitlines():
            checkbox = _parse_unchecked_checkbox(line)
            if checkbox is None:
                continue
            best = _best_journal_match(checkbox, journal_signals)
            if best is None:
                continue
            entry_rel, _ = best
            issues.append(
                Issue(
                    Severity.WARNING,
                    _rel(plan, project_root),
                    f"plan item likely shipped per {entry_rel}; consider flipping to [x]: "
                    f"{_truncate(checkbox, 100)}",
                )
            )
    return issues


def check_stale_pickup_pointers(
    project_root: Path,
    *,
    window_days: int | None = None,
) -> list[Issue]:
    """Warn when an active plan's `## Pickup pointer` cites shipped work.

    The pickup pointer is higher-authority than ordinary checklist prose for
    a fresh agent: it is explicitly "read this first." That makes stale pickup
    text worse than a stale checkbox. Reuse the shipped-journal overlap
    signals from ``check_stale_plan_checkboxes`` so a recent release/pr-merged
    entry that clearly overlaps the pointer produces a visible warning.
    """

    journal_dir = project_root / ".cortex" / "journal"
    if not (project_root / ".cortex" / "plans").exists() or not journal_dir.exists():
        return []
    effective_window = (
        window_days
        if window_days is not None
        else _stale_checkbox_window_days(project_root)
    )
    journal_signals = _collect_recent_shipped_signals(journal_dir, effective_window)
    if not journal_signals:
        return []
    issues: list[Issue] = []
    for plan in iter_plan_files(project_root):
        try:
            text = plan.read_text()
        except OSError:
            continue
        fields, body = parse_frontmatter(text)
        if _field_str(fields, "Status") != "active":
            continue
        pickup = _extract_h2_section(body, "Pickup pointer")
        if not pickup or STALE_CHECKBOX_BYPASS_MARKER in pickup:
            continue
        best = _best_journal_match(pickup, journal_signals)
        if best is None:
            continue
        entry_rel, _ = best
        issues.append(
            Issue(
                Severity.WARNING,
                _rel(plan, project_root),
                f"pickup pointer likely stale per {entry_rel}; refresh or remove shipped instructions: "
                f"{_truncate(pickup, 120)}",
            )
        )
    return issues


def check_stale_state_current_work(
    project_root: Path,
    *,
    window_days: int | None = None,
) -> list[Issue]:
    """Warn when `.cortex/state.md` current-work prose cites shipped work.

    ``refresh-state`` intentionally preserves hand-authored regions, which
    means a regenerated State file can still carry stale high-authority prose.
    This check keeps the preserved region honest by matching its ``## Current
    work`` section against recent shipped journal evidence.
    """

    state_path = project_root / ".cortex" / "state.md"
    journal_dir = project_root / ".cortex" / "journal"
    if not state_path.exists() or not journal_dir.exists():
        return []
    effective_window = (
        window_days
        if window_days is not None
        else _stale_checkbox_window_days(project_root)
    )
    journal_signals = _collect_recent_shipped_signals(journal_dir, effective_window)
    if not journal_signals:
        return []
    try:
        _fields, body = parse_frontmatter(state_path.read_text())
    except OSError:
        return []
    current_work = _extract_h2_section(body, "Current work")
    if not current_work or STALE_CHECKBOX_BYPASS_MARKER in current_work:
        return []
    for line in current_work.splitlines():
        bullet = _parse_bullet(line)
        if bullet is None:
            continue
        best = _best_journal_match(bullet, journal_signals)
        if best is None:
            continue
        entry_rel, _ = best
        return [
            Issue(
                Severity.WARNING,
                _rel(state_path, project_root),
                f"state current work likely stale per {entry_rel}; refresh the hand-authored "
                f"`## Current work` prose or remove shipped instructions: {_truncate(bullet, 120)}",
            )
        ]
    return []


def check_legacy_state_migration_needed(project_root: Path) -> list[Issue]:
    state_path = project_root / ".cortex" / "state.md"
    if not state_path.exists():
        return []
    try:
        text = state_path.read_text()
    except OSError as exc:
        return [Issue(Severity.WARNING, _rel(state_path, project_root), f"state.md unreadable: {exc}")]
    if not is_legacy_hand_authored_state(text):
        return []
    return [
        Issue(
            Severity.WARNING,
            _rel(state_path, project_root),
            "legacy hand-authored state.md has no cortex:hand markers; run `cortex migrate-state`",
        )
    ]


def check_state_journal_staleness(
    project_root: Path,
    *,
    window_days: int | None = None,
) -> list[Issue]:
    state_path = project_root / ".cortex" / "state.md"
    journal_dir = project_root / ".cortex" / "journal"
    if not state_path.exists() or not journal_dir.exists():
        return []
    try:
        fields, _body = parse_frontmatter(state_path.read_text())
    except OSError as exc:
        return [Issue(Severity.WARNING, _rel(state_path, project_root), f"state.md unreadable: {exc}")]
    generated = _parse_datetime(fields.get("Generated"))
    if generated is None:
        return []
    latest = _latest_journal_date(journal_dir)
    if latest is None:
        return []
    effective_window = (
        window_days if window_days is not None else _state_staleness_window_days(project_root)
    )
    age_days = (latest - generated.astimezone(UTC)).days
    if age_days <= effective_window:
        return []
    return [
        Issue(
            Severity.WARNING,
            _rel(state_path, project_root),
            f"state.md is {age_days} days older than latest journal entry "
            f"({latest.date().isoformat()}); rerun `cortex refresh-state`",
        )
    ]


def check_canonical_ownership(project_root: Path) -> list[Issue]:
    cortex_dir = project_root / ".cortex"
    if not (cortex_dir / "state.md").exists() or not _active_plans(cortex_dir):
        return []
    allowed = _doctrine_0007_allowed_root_files(project_root)
    offenders = [
        child
        for child in sorted(project_root.iterdir())
        if child.is_file()
        and CANONICAL_OWNERSHIP_RE.match(child.name)
        and child.name not in allowed
    ]
    issues: list[Issue] = []
    for offender in offenders:
        issues.append(
            Issue(
                Severity.WARNING,
                offender.name,
                f"canonical-ownership: {offender.name} at repo root duplicates content canonical in "
                ".cortex/state.md and .cortex/plans/<active>.md. Per Cortex Doctrine 0007, the "
                'canonical answers to "where are we" and "what\'s next" live in .cortex/. '
                "README links here, never restates. Either link from README and slim/delete this file, "
                "OR if you have a documented reason to keep it, add to .cortex/config.toml: "
                f'[doctrine.0007] allowed_root_files = ["{offender.name}"]. '
                f"See: {DOCTRINE_0007_URL}",
            )
        )
    return issues


def _modified_commits(project_root: Path, path: Path) -> list[str]:
    rel = _rel(path, project_root)
    # Find the file's introduction commit to use as the range baseline.
    # The append-only invariant is "no changes between introduction and HEAD",
    # so modifications before the file existed can never be violations.
    # --follow is safe here: we are finding where the file *first appeared*,
    # not tracing modifications — the M+--follow false-positive (cortex#103)
    # does not apply to the A filter.
    intro_result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            "--diff-filter=A",
            "--follow",
            "--format=%H",
            "--",
            rel,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    intro_shas = [line.strip() for line in intro_result.stdout.splitlines() if line.strip()]
    # git log is newest-first; the oldest A commit is the true introduction.
    intro_sha = intro_shas[-1] if intro_shas else None

    # No `--follow` for M commits: per Protocol § 4.1 + § 4.2, journal entries
    # and doctrine entries are append-only / immutable in place — they never
    # rename. Tracing renames false-positives when an entry's content is
    # byte-identical to another file (e.g. an auto-drafted entry whose
    # placeholders weren't substituted is identical to the template; git's
    # rename heuristic then traces "modifications" back to commits that touched
    # the template). See cortex#103 for the canonical repro.
    cmd = [
        "git",
        "-C",
        str(project_root),
        "log",
        "--diff-filter=M",
        "--format=%H",
    ]
    if intro_sha:
        cmd.append(f"{intro_sha}..HEAD")
    cmd.extend(["--", rel])
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


_BOLD_FIELD_RE = re.compile(r"^\*\*(?P<key>[^:*]+):\*\*\s*(?P<value>.+?)\s*$", re.MULTILINE)


def _extract_metadata(text: str) -> tuple[dict[str, Any], str]:
    """Return (combined_metadata, body_without_bold_fields).

    Cortex doctrine + journal entries declare metadata two ways:
    YAML frontmatter (`---` block) and bold-inline (`**Field:** value`)
    per SPEC § 6. The `_diff_only_allowed_frontmatter` predicate must
    treat both as metadata so a `**Status:**` flip on a markdown-style
    doctrine entry isn't falsely flagged as a body mutation.
    """
    frontmatter, body = parse_frontmatter(text)
    metadata: dict[str, Any] = dict(frontmatter or {})
    for match in _BOLD_FIELD_RE.finditer(body):
        key = match.group("key").strip()
        if key not in metadata:
            metadata[key] = match.group("value").strip()
    body_without_bold = _BOLD_FIELD_RE.sub("", body)
    return metadata, body_without_bold


def _diff_only_allowed_frontmatter(
    project_root: Path,
    sha: str,
    path: Path,
    allowed_fields: set[str] | None,
) -> bool:
    rel = _rel(path, project_root)
    old_text = _git_show_text(project_root, f"{sha}^:{rel}")
    new_text = _git_show_text(project_root, f"{sha}:{rel}")
    if old_text is None or new_text is None:
        return False
    old_metadata, old_body = _extract_metadata(old_text)
    new_metadata, new_body = _extract_metadata(new_text)
    if old_body != new_body or not old_metadata or not new_metadata:
        return False
    changed_keys = {
        key
        for key in set(old_metadata) | set(new_metadata)
        if old_metadata.get(key) != new_metadata.get(key)
    }
    if not changed_keys:
        return False
    return allowed_fields is None or changed_keys <= allowed_fields


def _git_show_text(project_root: Path, spec: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(project_root), "show", spec],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def _deleted_files(
    project_root: Path,
    *,
    since_days: int,
) -> list[tuple[str, datetime, int, str]]:
    since_iso = (datetime.now(UTC) - timedelta(days=since_days)).isoformat()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            f"--since={since_iso}",
            "--diff-filter=D",
            "--numstat",
            "--format=--commit--%n%H%n%cI",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[str, datetime, int, str]] = []
    for block in result.stdout.split("--commit--\n"):
        if not block.strip():
            continue
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        sha = lines[0]
        commit_date = datetime.fromisoformat(lines[1])
        for line in lines[2:]:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            deleted = _safe_int(parts[1])
            if deleted is None:
                continue
            rows.append((sha, commit_date, deleted, parts[2]))
    return rows


def _generated_layer_paths(project_root: Path) -> list[Path]:
    cortex_dir = project_root / ".cortex"
    paths = [p for p in (cortex_dir / "state.md", cortex_dir / "map.md") if p.exists()]
    journal_dir = cortex_dir / "journal"
    if journal_dir.exists():
        for entry in sorted(journal_dir.glob("*.md")):
            try:
                fields, _body = parse_frontmatter(entry.read_text())
            except OSError:
                continue
            if _field_str(fields, "Type") == "digest":
                paths.append(entry)
    return paths


def _newer_state_source(
    project_root: Path,
    generated: datetime,
) -> tuple[str, datetime] | None:
    """Return a State source newer than ``Generated:``, if one exists.

    Prefer git commit dates for clean worktrees so a fresh clone does not look
    stale merely because file mtimes were rewritten by checkout. Dirty tracked
    or untracked source files use mtime (or now for deletes) because no commit
    timestamp exists yet.
    """

    threshold = generated.astimezone(UTC) + SOURCE_FRESHNESS_SLOP
    pathspecs = _state_source_pathspecs(project_root)
    if not pathspecs:
        return None

    if not _looks_like_git_worktree(project_root):
        mtime = _latest_state_source_mtime(project_root)
        if mtime is not None and mtime[1] > threshold:
            return mtime
        return None

    dirty = _dirty_state_source(project_root, pathspecs, threshold)
    if dirty is not None:
        return dirty

    committed = _latest_committed_state_source(project_root, pathspecs)
    if committed is not None:
        source_rel, changed_at, source_sha = committed
        state_sha = _latest_commit_sha(project_root, ".cortex/state.md")
        if state_sha is not None:
            if source_sha == state_sha:
                return None
            if _is_ancestor(project_root, state_sha, source_sha):
                return source_rel, changed_at
        if changed_at > threshold:
            return source_rel, changed_at

    # Never-committed git projects still deserve a freshness check.
    if committed is None:
        mtime = _latest_state_source_mtime(project_root)
        if mtime is not None and mtime[1] > threshold:
            return mtime
    return None


def _state_source_pathspecs(project_root: Path) -> list[str]:
    pathspecs: list[str] = []
    for rel in (*STATE_SOURCE_DIRS, *STATE_SOURCE_FILES):
        if (project_root / rel).exists():
            pathspecs.append(rel)
    return pathspecs


def _looks_like_git_worktree(project_root: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (project_root, *project_root.parents))


def _dirty_state_source(
    project_root: Path,
    pathspecs: list[str],
    threshold: datetime,
) -> tuple[str, datetime] | None:
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain", "--", *pathspecs],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        rel = _status_path(line)
        if rel is None:
            continue
        path = project_root / rel
        if path.exists():
            changed_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        else:
            changed_at = datetime.now(UTC)
        if changed_at > threshold:
            return rel, changed_at
    return None


def _status_path(line: str) -> str | None:
    if len(line) < 4:
        return None
    raw = line[3:].strip()
    if " -> " in raw:
        raw = raw.rsplit(" -> ", 1)[1]
    return raw.strip('"') or None


def _latest_committed_state_source(
    project_root: Path,
    pathspecs: list[str],
) -> tuple[str, datetime, str] | None:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            "-1",
            "--format=%H%x00%cI",
            "--name-only",
            "--",
            *pathspecs,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    first = lines[0].split("\x00", 1)
    if len(first) != 2 or not first[0]:
        return None
    source_sha, raw_changed_at = first
    try:
        changed_at = datetime.fromisoformat(raw_changed_at).astimezone(UTC)
    except ValueError:
        return None
    source_rel = next((line for line in lines[1:] if line), pathspecs[0])
    return source_rel, changed_at, source_sha


def _latest_commit_sha(project_root: Path, rel: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(project_root), "log", "-1", "--format=%H", "--", rel],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _is_ancestor(project_root: Path, maybe_ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", maybe_ancestor, descendant],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _latest_state_source_mtime(project_root: Path) -> tuple[str, datetime] | None:
    latest: tuple[str, datetime] | None = None
    for path in _state_source_files(project_root):
        try:
            changed_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        except OSError:
            continue
        rel = _rel(path, project_root)
        if latest is None or changed_at > latest[1]:
            latest = (rel, changed_at)
    return latest


def _state_source_files(project_root: Path) -> list[Path]:
    files: list[Path] = []
    for rel in STATE_SOURCE_DIRS:
        path = project_root / rel
        if path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
    for rel in STATE_SOURCE_FILES:
        path = project_root / rel
        if path.is_file():
            files.append(path)
    return files


def _validate_table(
    rel: str,
    section: str,
    data: dict[str, Any],
    schema: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    for key, value in data.items():
        expected = schema.get(key)
        if expected is None:
            issues.append(Issue(Severity.WARNING, rel, f"[{section}] unknown key `{key}`"))
            continue
        if not _matches_schema(value, expected):
            issues.append(
                Issue(
                    Severity.ERROR,
                    rel,
                    f"[{section}] `{key}` must be {_schema_label(expected)}",
                )
            )
    return issues


def _matches_schema(value: Any, expected: str) -> bool:
    if expected == "optional-string":
        return value is None or isinstance(value, str)
    if expected == "string-list":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if expected == "optional-string-list":
        return value is None or _matches_schema(value, "string-list")
    if expected == "positive-int":
        # bool is a subclass of int in Python; reject it explicitly so
        # `window_days = true` doesn't pass as an integer.
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    raise AssertionError(f"unknown config schema kind: {expected}")


def _schema_label(expected: str) -> str:
    return {
        "optional-string": "a string or null",
        "string-list": "a list of strings",
        "optional-string-list": "a list of strings or null",
        "positive-int": "a positive integer",
    }[expected]


# --- stale-plan-checkbox helpers (cortex#100) -------------------------------

# A "shipped" journal section header. We accept either "What shipped" (the
# release.md template — SPEC § 3.3 example) or "What landed" (a phrasing
# some pr-merged drafts use). Headers are matched case-insensitive after
# stripping leading hashes; sub-headers (### …) inside the section are
# preserved as part of its body so PR-grouped subsections stay matchable.
_SHIPPED_SECTION_HEADINGS = {"what shipped", "what landed"}

# Treat 1+ leading whitespace as part of the bullet — checkboxes are nested
# under section headings sometimes. The line must start with `- [ ]` (an
# unchecked box); a `- [x]` already-flipped item is exempt by definition.
_UNCHECKED_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[\s\]\s+(?P<text>.+)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+)$")

# Strong tokens — citations of specific artifacts. A single match in both
# the checkbox text and a journal entry's What-shipped section is enough to
# warrant a warning.
_PR_REF_RE = re.compile(r"#(\d{2,5})\b")
_BRIEF_PATH_RE = re.compile(r"(?:^|[\s`(])(briefs/[A-Za-z0-9._-]+\.md)\b")
_DOCTRINE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9])doctrine[/\s-]+(\d{4})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# A pared-down English stoplist plus markdown / cortex-domain particles.
# Used to filter trigrams down to "distinctive" content phrases (signal #4).
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "with",
        "by", "from", "into", "via", "is", "was", "are", "were", "be", "been",
        "being", "as", "or", "but", "if", "then", "than", "that", "this",
        "those", "these", "it", "its", "we", "our", "us", "you", "your",
        "they", "them", "their", "i", "me", "my", "he", "she", "his", "her",
        "do", "does", "did", "done", "will", "would", "should", "could", "can",
        "may", "might", "must", "shall", "have", "has", "had", "having", "not",
        "no", "yes", "so", "up", "down", "out", "over", "under", "again",
        "further", "very", "just", "now", "all", "any", "each", "few", "more",
        "most", "other", "some", "such", "only", "own", "same", "too", "also",
        "per", "etc", "ie", "eg",
        "cortex", "shipped", "ships", "lands", "landed", "merged", "release",
        "released", "v0", "v1", "pr", "prs", "tag", "tagged",
    }
)

# Token shape used for both phrase extraction and a few other places. We
# accept identifier-ish tokens with internal `-`, `_`, `.`, `/`, `:` so
# `cortex retrieve --mode bm25` survives mostly intact.
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{1,}")


def _stale_checkbox_window_days(project_root: Path) -> int:
    """Read `[doctor.stale-checkbox] window_days` from .cortex/config.toml.

    Falls back to ``DEFAULT_STALE_CHECKBOX_WINDOW_DAYS`` (14) when the file
    or section is absent / malformed. Schema validation happens in
    ``check_config_toml_schema`` — this reader degrades silently so a
    typo'd config doesn't crash the doctor run; the warning surfaces from
    the schema check instead.
    """
    path = project_root / ".cortex" / "config.toml"
    if not path.exists():
        return DEFAULT_STALE_CHECKBOX_WINDOW_DAYS
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_STALE_CHECKBOX_WINDOW_DAYS
    doctor = data.get("doctor")
    if not isinstance(doctor, dict):
        return DEFAULT_STALE_CHECKBOX_WINDOW_DAYS
    section = doctor.get("stale-checkbox")
    if not isinstance(section, dict):
        return DEFAULT_STALE_CHECKBOX_WINDOW_DAYS
    value = section.get("window_days")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_STALE_CHECKBOX_WINDOW_DAYS
    return value


def _state_staleness_window_days(project_root: Path) -> int:
    path = project_root / ".cortex" / "config.toml"
    if not path.exists():
        return DEFAULT_STATE_JOURNAL_STALENESS_DAYS
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return DEFAULT_STATE_JOURNAL_STALENESS_DAYS
    doctor = data.get("doctor")
    if not isinstance(doctor, dict):
        return DEFAULT_STATE_JOURNAL_STALENESS_DAYS
    section = doctor.get("state-staleness")
    if not isinstance(section, dict):
        return DEFAULT_STATE_JOURNAL_STALENESS_DAYS
    value = section.get("window_days")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_STATE_JOURNAL_STALENESS_DAYS
    return value


def _parse_unchecked_checkbox(line: str) -> str | None:
    """Return the prose text of an unchecked checkbox, or None.

    Returns None for: lines that aren't `- [ ]`; lines that carry the
    bypass marker; and lines whose stripped text is empty (defensive).

    The full prose (including any bold markers and parenthetical PR
    references) is preserved — `_TOKEN_RE` already ignores asterisks and
    parens, so leaving them in keeps PR-ref + brief-path + phrase signals
    intact for matching.
    """
    match = _UNCHECKED_CHECKBOX_RE.match(line)
    if not match:
        return None
    text = match.group("text").strip()
    if STALE_CHECKBOX_BYPASS_MARKER in text:
        return None
    return text or None


def _parse_bullet(line: str) -> str | None:
    match = _BULLET_RE.match(line)
    if not match:
        return None
    text = match.group("text").strip()
    if STALE_CHECKBOX_BYPASS_MARKER in text:
        return None
    return text or None


def _collect_recent_shipped_signals(
    journal_dir: Path, window_days: int
) -> list[tuple[str, _Signals]]:
    """Return [(rel_path, signals)] for in-window release/pr-merged entries.

    The journal entry's date is parsed from its ``Date:`` frontmatter (or
    bold-inline). Falls back to the date prefix in the filename if neither
    field parses. Entries outside the window are skipped silently.
    """
    cutoff = date.today() - timedelta(days=window_days)
    out: list[tuple[str, _Signals]] = []
    for entry in sorted(journal_dir.glob("*.md")):
        try:
            text = entry.read_text()
        except OSError:
            continue
        fields, body = parse_frontmatter(text)
        type_ = _field_str(fields, "Type") or _field_str_inline(body, "Type")
        if type_ not in {"release", "pr-merged"}:
            continue
        entry_date = _parse_date(_field_str(fields, "Date")) or _parse_date(
            _field_str_inline(body, "Date")
        )
        if entry_date is None:
            entry_date = _journal_date(entry)
        if entry_date is None or entry_date < cutoff:
            continue
        section = _extract_shipped_section(body)
        if not section:
            continue
        signals = _extract_signals(section)
        if signals.is_empty:
            continue
        rel = f".cortex/journal/{entry.name}"
        out.append((rel, signals))
    return out


def _latest_journal_date(journal_dir: Path) -> datetime | None:
    latest: datetime | None = None
    for entry in sorted(journal_dir.glob("*.md")):
        try:
            fields, body = parse_frontmatter(entry.read_text())
        except OSError:
            continue
        entry_date = _parse_date(_field_str(fields, "Date")) or _parse_date(
            _field_str_inline(body, "Date")
        )
        if entry_date is None:
            entry_date = _journal_date(entry)
        if entry_date is None:
            continue
        candidate = datetime.combine(entry_date, datetime.min.time(), tzinfo=UTC)
        if latest is None or candidate > latest:
            latest = candidate
    return latest


def _field_str_inline(body: str, field_name: str) -> str | None:
    """Pull a `**Field:** value` from the first ~40 lines (header band)."""
    header = "\n".join(body.splitlines()[:40])
    match = re.search(
        rf"\*\*{re.escape(field_name)}:\*\*\s*([^\n]+)", header
    )
    if not match:
        return None
    return match.group(1).strip() or None


def _extract_shipped_section(body: str) -> str:
    """Return the prose under `## What shipped` / `## What landed`, or ''.

    The section ends at the next `## ` heading or end-of-file. Sub-headings
    (`### …`) inside the section are preserved as content. Returns '' when
    the section is absent.
    """
    in_section = False
    captured: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if heading in _SHIPPED_SECTION_HEADINGS:
                in_section = True
                continue
            if in_section:
                # Hit the next `## ` heading — stop.
                break
            continue
        if in_section:
            captured.append(line)
    return "\n".join(captured).strip()


def _extract_h2_section(body: str, heading_name: str) -> str:
    """Return prose under a specific H2 heading, or ''. """
    wanted = heading_name.strip().lower()
    in_section = False
    captured: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            if heading == wanted or heading.startswith(f"{wanted} "):
                in_section = True
                continue
            if in_section:
                break
            continue
        if in_section:
            captured.append(line)
    return "\n".join(captured).strip()


@dataclass(frozen=True)
class _Signals:
    pr_refs: frozenset[str]
    brief_paths: frozenset[str]
    doctrine_refs: frozenset[str]
    phrases: frozenset[tuple[str, ...]]

    @property
    def is_empty(self) -> bool:
        return not (
            self.pr_refs or self.brief_paths or self.doctrine_refs or self.phrases
        )


def _extract_signals(text: str) -> _Signals:
    return _Signals(
        pr_refs=frozenset(_PR_REF_RE.findall(text)),
        brief_paths=frozenset(m.lower() for m in _BRIEF_PATH_RE.findall(text)),
        doctrine_refs=frozenset(_DOCTRINE_REF_RE.findall(text)),
        phrases=frozenset(_distinctive_trigrams(text)),
    )


def _distinctive_trigrams(text: str) -> list[tuple[str, ...]]:
    """Extract sliding 3-grams of non-stopword tokens from `text`.

    Pure-stopword runs are skipped. Tokens are lowercased; punctuation and
    markdown symbols outside ``_TOKEN_RE`` are stripped. The resulting set
    is the "distinctive phrase" signal — individually weak, but a 2+
    overlap between checkbox text and a journal entry is meaningful.
    """
    tokens = [t.lower() for t in _TOKEN_RE.findall(text)]
    content = [t for t in tokens if t not in _STOPWORDS and len(t) > 1]
    if len(content) < 3:
        return []
    return [tuple(content[i : i + 3]) for i in range(len(content) - 2)]


def _best_journal_match(
    checkbox_text: str,
    journal_signals: list[tuple[str, _Signals]],
) -> tuple[str, _Signals] | None:
    """Return the strongest matching (rel_path, signals) tuple, or None.

    Matching policy (cortex#100):
      - At least one shared `PR #N`, `briefs/<name>.md`, or `doctrine/NNNN`
        reference is a STRONG match — fire on the first.
      - Otherwise, ≥2 distinctive trigram overlaps (PHRASE matches) fire.
      - Single-trigram overlap is intentionally not enough — too many false
        positives from generic phrasing like "release release prep".

    When multiple journal entries match, the one with the most overlap
    signals wins; ties break to the most recent (last-listed) entry.
    """
    cb_signals = _extract_signals(checkbox_text)
    if cb_signals.is_empty:
        return None
    best_score = 0
    best: tuple[str, _Signals] | None = None
    for rel, signals in journal_signals:
        shared_pr = cb_signals.pr_refs & signals.pr_refs
        shared_brief = cb_signals.brief_paths & signals.brief_paths
        shared_doctrine = cb_signals.doctrine_refs & signals.doctrine_refs
        shared_phrases = cb_signals.phrases & signals.phrases
        strong_count = len(shared_pr) + len(shared_brief) + len(shared_doctrine)
        phrase_count = len(shared_phrases)
        if strong_count == 0 and phrase_count < 2:
            continue
        # Score: strong matches dominate; phrase matches break ties.
        score = strong_count * 100 + phrase_count
        if score >= best_score:
            best_score = score
            best = (rel, signals)
    return best


def _truncate(text: str, max_len: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


def _doctrine_append_only_grandfather_commits(project_root: Path) -> set[str]:
    """Read ``[doctrine.append-only] grandfather-commits`` from config.toml.

    Returns a set of SHA prefixes (any length) that the append-only check
    should skip.  Adding a SHA here must come with a same-commit Journal entry
    explaining why — the config is the acknowledgement, the journal entry is
    the audit trail.
    """
    path = project_root / ".cortex" / "config.toml"
    if not path.exists():
        return set()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    doctrine = data.get("doctrine")
    if not isinstance(doctrine, dict):
        return set()
    append_only = doctrine.get("append-only")
    if not isinstance(append_only, dict):
        return set()
    commits = append_only.get("grandfather-commits", [])
    if not isinstance(commits, list):
        return set()
    return {c for c in commits if isinstance(c, str)}


def _doctrine_0007_allowed_root_files(project_root: Path) -> set[str]:
    path = project_root / ".cortex" / "config.toml"
    if not path.exists():
        return set()
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    doctrine = data.get(DOCTRINE_0007_SECTION[0])
    if not isinstance(doctrine, dict):
        return set()
    section = doctrine.get(DOCTRINE_0007_SECTION[1])
    if not isinstance(section, dict):
        return set()
    allowed = section.get("allowed_root_files", [])
    if not isinstance(allowed, list):
        return set()
    return {item for item in allowed if isinstance(item, str)}


def _active_plans(cortex_dir: Path) -> list[Path]:
    if not (cortex_dir / "plans").exists():
        return []
    active: list[Path] = []
    for plan in iter_plan_files(cortex_dir.parent):
        fields, _body = parse_frontmatter(plan.read_text())
        if _field_str(fields, "Status") == "active":
            active.append(plan)
    return active


def _protocol_deletion_threshold(project_root: Path) -> int:
    path = project_root / ".cortex" / "protocol.md"
    if not path.exists():
        return DEFAULT_DELETION_LINE_THRESHOLD
    text = path.read_text()
    match = re.search(r"^T1\.4\.line-threshold:\s*(\d+)", text, re.MULTILINE)
    if not match:
        return DEFAULT_DELETION_LINE_THRESHOLD
    return int(match.group(1))


def _first_commit_date(project_root: Path, path: Path) -> date | None:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            "--follow",
            "--diff-filter=A",
            "--format=%cI",
            "--",
            _rel(path, project_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    try:
        return datetime.fromisoformat(lines[-1]).date()
    except ValueError:
        return None


def _journal_date(path: Path) -> date | None:
    match = re.match(r"^(\d{4}-\d{2}-\d{2})-", path.name)
    if not match:
        return None
    return date.fromisoformat(match.group(1))


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: FrontmatterValue | None) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _field_str(fields: dict[str, FrontmatterValue], key: str) -> str | None:
    value = fields.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _format_subprocess_error(exc: BaseException) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        return stderr.splitlines()[-1] if stderr else f"git exited {exc.returncode}"
    if isinstance(exc, FileNotFoundError):
        return "git not installed or working directory missing"
    return str(exc)


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
