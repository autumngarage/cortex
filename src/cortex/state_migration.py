"""Helpers for migrating legacy hand-authored `.cortex/state.md` files."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from cortex.frontmatter import parse_frontmatter
from cortex.state_render import HAND_CLOSE, HAND_OPEN, build_state_inputs, render_state

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


def is_legacy_hand_authored_state(text: str) -> bool:
    """Return True for pre-v0.4 hand-authored State without hand markers."""

    if HAND_OPEN in text or HAND_CLOSE in text:
        return False
    fields, _body = parse_frontmatter(text)
    generator = str(fields.get("Generator", "")).lower()
    spec = str(fields.get("Spec", ""))
    return "hand-authored" in generator or _version_before(spec, (0, 4, 0))


def migrate_legacy_state_text(text: str) -> str:
    """Wrap the existing body in a preserved hand-authored region."""

    prefix, body = _split_frontmatter(text)
    if HAND_OPEN in body or HAND_CLOSE in body:
        return text
    ending = "" if body.endswith("\n") else "\n"
    return f"{prefix}\n{HAND_OPEN}\n{body}{ending}{HAND_CLOSE}\n"


def render_migrated_state(
    project_root: Path,
    *,
    deterministic: bool = False,
    assume_index_present: bool = False,
) -> str:
    """Render the post-migration, refreshable State content."""

    state_path = project_root / ".cortex" / "state.md"
    before = state_path.read_text()
    migrated = migrate_legacy_state_text(before)
    inputs = build_state_inputs(
        project_root,
        deterministic=deterministic,
        assume_index_present=assume_index_present,
    )
    now = datetime(2000, 1, 1, tzinfo=UTC) if deterministic else None
    return render_state(replace(inputs, previous_state=migrated), now=now)


def _split_frontmatter(text: str) -> tuple[str, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return "", text
    return match.group(0).rstrip("\n"), text[match.end() :]


def _version_before(value: str, boundary: tuple[int, int, int]) -> bool:
    match = re.match(r"^\s*(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        return False
    version = tuple(int(part) for part in match.groups())
    return version < boundary
