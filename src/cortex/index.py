"""Build and persist `.cortex/.index.json`.

The index is a regeneratable cache. Its promotion candidates are derived from
Journal entries and Doctrine reverse links, then written atomically so readers
never observe a half-written JSON document.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from cortex import __version__
from cortex.config import RefreshIndexConfig
from cortex.frontmatter import FrontmatterValue, parse_frontmatter

MARKDOWN_FIELD_RE = re.compile(r"^\*\*(?P<key>[^:*]+):\*\*\s*(?P<value>.*)$")
JOURNAL_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


@dataclass(frozen=True)
class RefreshIndexResult:
    """Result of refreshing `.cortex/.index.json`."""

    path: Path
    data: dict[str, Any]
    warnings: tuple[str, ...] = ()


def read_index(path: Path) -> dict[str, Any]:
    """Read an index JSON file.

    Missing files return an empty dict. Malformed JSON and non-object JSON are
    surfaced to the caller; callers decide whether that is fatal or a warning.
    """

    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level JSON value is not an object")
    return data


def write_index(path: Path, data: dict[str, Any]) -> None:
    """Atomically write pretty-printed index JSON to ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def compute_index(cortex_root: Path, config: RefreshIndexConfig) -> dict[str, Any]:
    """Compute the promotion-candidate index from primary Cortex sources."""

    project_root = cortex_root.parent
    today = _today()
    promoted = _promoted_to_by_source(cortex_root)
    candidates: list[dict[str, Any]] = []

    journal_dir = cortex_root / "journal"
    if journal_dir.exists():
        for path in sorted(journal_dir.glob("*.md")):
            text = path.read_text()
            fields, body = _entry_fields(text)
            if not _is_candidate(fields, body, config.candidate_patterns):
                continue
            rel = path.relative_to(project_root).as_posix()
            candidate_id = path.stem
            last_touched = _last_touched(path, fields)
            candidates.append(
                {
                    "id": candidate_id,
                    "source": rel,
                    "type": _field_scalar(fields, "Type") or "unknown",
                    "last_touched": last_touched.isoformat(),
                    "age_days": (today - last_touched).days,
                    "tags": _field_list(fields, "Tags"),
                    "supersedes": _field_scalar(fields, "Supersedes"),
                    "promoted_to": _field_scalar(fields, "Promoted-to") or promoted.get(f"journal/{candidate_id}"),
                }
            )

    generated = _generated_timestamp(cortex_root, [Path(c["source"]) for c in candidates])
    return {
        "spec": _spec_version(cortex_root),
        "generated": generated,
        "candidates": candidates,
    }


def refresh_index(project_root: Path, config: RefreshIndexConfig) -> RefreshIndexResult:
    """Rebuild `.cortex/.index.json`, preserving generation time on no-op refreshes."""

    cortex_root = project_root / ".cortex"
    index_path = cortex_root / ".index.json"
    data = compute_index(cortex_root, config)
    existing: dict[str, Any] = {}
    warnings = tuple(config.warnings)
    try:
        existing = read_index(index_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        warnings = (*warnings, f"could not read existing .cortex/.index.json: {exc}; rewriting")

    if _same_index_inputs(existing, data):
        data["generated"] = existing.get("generated", data["generated"])

    write_index(index_path, data)
    return RefreshIndexResult(path=index_path, data=data, warnings=warnings)


def _same_index_inputs(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return {
        "spec": left.get("spec"),
        "candidates": left.get("candidates"),
    } == {
        "spec": right.get("spec"),
        "candidates": right.get("candidates"),
    }


def _entry_fields(text: str) -> tuple[dict[str, FrontmatterValue], str]:
    frontmatter, body = parse_frontmatter(text)
    if frontmatter:
        return frontmatter, body

    fields: dict[str, FrontmatterValue] = {}
    for line in text.splitlines():
        match = MARKDOWN_FIELD_RE.match(line.strip())
        if not match:
            continue
        key = match.group("key").strip()
        value = match.group("value").strip()
        if value.startswith("[") and value.endswith("]"):
            fields[key] = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
        else:
            fields[key] = value
    return fields, text


def _is_candidate(fields: dict[str, FrontmatterValue], body: str, patterns: tuple[str, ...]) -> bool:
    tags = {tag.lower() for tag in _field_list(fields, "Tags")}
    if "candidate-doctrine" in tags:
        return True
    if (_field_scalar(fields, "Type") or "").lower() != "decision":
        return False
    body_lower = body.lower()
    return any(pattern.lower() in body_lower for pattern in patterns)


def _field_scalar(fields: dict[str, FrontmatterValue], key: str) -> str | None:
    value = fields.get(key)
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _field_list(fields: dict[str, FrontmatterValue], key: str) -> list[str]:
    value = fields.get(key)
    if isinstance(value, list):
        return [item.strip() for item in value if item.strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _last_touched(path: Path, fields: dict[str, FrontmatterValue]) -> date:
    for key in ("Last-touched", "Date"):
        raw = _field_scalar(fields, key)
        if raw:
            try:
                return date.fromisoformat(raw[:10])
            except ValueError:
                pass
    match = JOURNAL_DATE_RE.match(path.name)
    if match:
        return date.fromisoformat(match.group(1))
    return _today()


def _promoted_to_by_source(cortex_root: Path) -> dict[str, str]:
    links: dict[str, str] = {}
    doctrine_dir = cortex_root / "doctrine"
    if not doctrine_dir.exists():
        return links
    for path in sorted(doctrine_dir.glob("*.md")):
        fields, _body = _entry_fields(path.read_text())
        source = _field_scalar(fields, "Promoted-from")
        if not source:
            continue
        source = source.strip()
        if source.endswith(".md"):
            source = source.removesuffix(".md")
        if source.startswith(".cortex/"):
            source = source.removeprefix(".cortex/")
        links[source] = f"doctrine/{path.stem}"
    return links


def _generated_timestamp(cortex_root: Path, candidate_sources: list[Path]) -> str:
    mtimes: list[float] = []
    config = cortex_root / "config.toml"
    if config.exists():
        mtimes.append(config.stat().st_mtime)
    for rel in candidate_sources:
        source = cortex_root.parent / rel
        if source.exists():
            mtimes.append(source.stat().st_mtime)
    if mtimes:
        return datetime.fromtimestamp(max(mtimes), tz=UTC).astimezone().isoformat(timespec="seconds")
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _today() -> date:
    if os.environ.get("CORTEX_DETERMINISTIC") == "1":
        return date(2000, 1, 1)
    return date.today()


def _spec_version(cortex_root: Path) -> str:
    spec_path = cortex_root / "SPEC_VERSION"
    if spec_path.exists():
        value = spec_path.read_text().strip()
        if value:
            return value
    return f"{__version__}-unknown"
