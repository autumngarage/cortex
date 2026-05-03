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


def run_plain_checks(project_root: Path) -> list[Issue]:
    """Checks that run on plain `cortex doctor`."""

    checks = (
        check_append_only_journal,
        check_immutable_doctrine,
        check_cli_less_fallback,
        check_generated_layers,
        check_canonical_ownership,
        check_semantic_retrieval_runtime,
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
    for entry in sorted(doctrine_dir.glob("*.md")):
        for sha in _modified_commits(project_root, entry):
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
    if (cortex_dir / ".index.json").exists():
        return []
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
    return [
        Issue(
            Severity.WARNING,
            ".cortex/.index.json",
            "corpus exceeds CLI-less fallback threshold "
            f"({doctrine_count} Doctrine, {journal_count} Journal); run `cortex refresh-index`",
        )
    ]


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
    return issues


def check_retention_visibility(project_root: Path) -> list[Issue]:
    issues: list[Issue] = []
    cutoff = date.today() - timedelta(days=DEFAULT_RETENTION_DAYS)
    plans_dir = project_root / ".cortex" / "plans"
    if plans_dir.exists():
        for plan in sorted(plans_dir.glob("*.md")):
            fields, _body = parse_frontmatter(plan.read_text())
            status = _field_str(fields, "Status")
            if status not in {"shipped", "cancelled"}:
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
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            "--follow",
            "--diff-filter=M",
            "--format=%H",
            "--",
            rel,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
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
    raise AssertionError(f"unknown config schema kind: {expected}")


def _schema_label(expected: str) -> str:
    return {
        "optional-string": "a string or null",
        "string-list": "a list of strings",
        "optional-string-list": "a list of strings or null",
    }[expected]


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
    plans_dir = cortex_dir / "plans"
    if not plans_dir.exists():
        return []
    active: list[Path] = []
    for plan in sorted(plans_dir.glob("*.md")):
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
