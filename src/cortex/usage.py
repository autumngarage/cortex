"""Local usage counters for grep/retrieve/manifest command telemetry.

The counter file lives at ``.cortex/.index/usage.json`` (gitignored with the
retrieve index) and is intentionally local-only: no remote reporting, no PII.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, cast

USAGE_SCHEMA_VERSION: Final[int] = 1
_USAGE_COUNT_KEYS: Final[tuple[str, ...]] = (
    "grep",
    "retrieve_bm25",
    "retrieve_semantic",
    "retrieve_hybrid",
    "manifest",
)

UsageCounter = Literal[
    "grep",
    "retrieve_bm25",
    "retrieve_semantic",
    "retrieve_hybrid",
    "manifest",
]


def usage_path(project_root: Path) -> Path:
    return project_root / ".cortex" / ".index" / "usage.json"


def read_usage(project_root: Path) -> dict[str, object]:
    """Return validated usage payload; reset-and-warn when corrupt."""

    return _load_usage(usage_path(project_root))


def increment_usage(project_root: Path, counter: UsageCounter) -> None:
    """Increment ``counter`` best-effort (never crashes the caller)."""

    if counter not in _USAGE_COUNT_KEYS:
        raise ValueError(f"unknown usage counter: {counter}")
    path = usage_path(project_root)
    payload = _load_usage(path)
    counts = cast(dict[str, int], payload["counts"])
    counts[counter] += 1
    _write_usage(path, payload, action=f"increment {counter}")


def reset_usage(project_root: Path) -> None:
    """Reset usage counters to zero with a fresh ``since`` timestamp."""

    _write_usage(usage_path(project_root), _default_usage_payload(), action="reset usage counters")


def _default_usage_payload() -> dict[str, object]:
    counts = dict.fromkeys(_USAGE_COUNT_KEYS, 0)
    return {
        "schema_version": USAGE_SCHEMA_VERSION,
        "since": _utc_now_z(),
        "counts": counts,
    }


def _utc_now_z() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_usage(path: Path) -> dict[str, object]:
    if not path.exists():
        return _default_usage_payload()

    try:
        raw = json.loads(path.read_text())
        return _normalize_payload(raw)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        _warn(f"warning: {path} is corrupt ({exc}); resetting to default usage counters.")
        payload = _default_usage_payload()
        _write_usage(path, payload, action="reset corrupted usage counters")
        return payload


def _normalize_payload(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("top-level JSON must be an object")

    schema_version = raw.get("schema_version")
    if schema_version != USAGE_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {schema_version!r}; expected {USAGE_SCHEMA_VERSION}"
        )

    since = raw.get("since")
    if not isinstance(since, str) or not since.strip():
        raise ValueError("missing or invalid `since` timestamp")

    counts_raw = raw.get("counts")
    if not isinstance(counts_raw, dict):
        raise ValueError("missing or invalid `counts` object")

    counts: dict[str, int] = {}
    for key in _USAGE_COUNT_KEYS:
        value = counts_raw.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"counts.{key} must be a non-negative integer")
        counts[key] = value

    return {
        "schema_version": USAGE_SCHEMA_VERSION,
        "since": since,
        "counts": counts,
    }


def _write_usage(path: Path, payload: dict[str, object], *, action: str) -> None:
    try:
        _atomic_write_json(path, payload)
    except OSError as exc:
        _warn(f"warning: could not {action} at {path}: {exc}")


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _warn(message: str) -> None:
    print(message, file=sys.stderr)
