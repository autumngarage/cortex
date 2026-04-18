"""Autumn Garage sibling detection for `cortex doctor`.

Surfaces presence + version of the two sibling tools (Touchstone,
Sentinel) per Doctrine 0002 — file-contract + CLI-shell-out only, no
imports from the sibling packages, no hard dependency on either being
installed. Absence is normal; the block never errors or warn-exits.

Detection is two-axis:

1. **CLI presence.** `shutil.which("<tool>")`. When present, shell out
   to `<tool> version` with a 3-second timeout and parse the first
   `\\d+\\.\\d+\\.\\d+` semver token from the output.
2. **Project-local config.** Walk up from cwd to the git root (or
   filesystem root) looking for the sibling's project-local marker
   (`.touchstone-config` file for Touchstone, `.sentinel/config.toml`
   for Sentinel).

The two axes are independent: a sibling can be installed globally
without being configured in *this* project, and vice-versa. The output
block reports both states for each sibling so the user sees exactly
which half is missing when something is off.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Shell-out timeout for `<tool> version`. Keeps `cortex doctor` fast when
# a sibling CLI is installed but hangs or is slow (e.g. cold start).
_VERSION_TIMEOUT_SECONDS = 3.0

# Semver pattern — matches 1.2.3 anywhere in the version-command output.
# Non-capturing so the first match wins even if multiple appear.
_SEMVER_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class SiblingStatus:
    """Presence + version report for a single sibling tool."""

    name: str
    cli_path: str | None
    version: str | None
    version_error: str | None
    project_marker: str  # human-readable description of what we looked for
    project_marker_present: bool


def _find_git_root(start: Path) -> Path:
    """Walk up from `start` looking for a `.git/` directory. Return the
    git root, or `start` itself if none is found (so the search stays
    bounded and predictable in non-git directories)."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _project_marker_present(start: Path, relative: str) -> bool:
    """True iff `relative` resolves under any directory from `start` up
    to the git root. Matches the "walk up to git root" contract in the
    PR scope."""
    root = _find_git_root(start)
    current = start.resolve()
    while True:
        if (current / relative).exists():
            return True
        if current == root or current.parent == current:
            return False
        current = current.parent


def _try_version_args(cli_path: str, args: list[str]) -> tuple[str | None, str | None]:
    """Single invocation attempt. Returns ``(version, error)``."""
    try:
        completed = subprocess.run(
            [cli_path, *args],
            capture_output=True,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {_VERSION_TIMEOUT_SECONDS:.0f}s"
    except OSError as exc:
        # Permission errors, ENOENT races after `which` succeeded, etc.
        return None, f"exec failed: {exc}"

    combined = (completed.stdout or "") + "\n" + (completed.stderr or "")
    match = _SEMVER_RE.search(combined)
    if match is not None:
        return match.group(1), None
    if completed.returncode != 0:
        return None, f"exit {completed.returncode}"
    return None, "no version in output"


def _read_version(cli_path: str) -> tuple[str | None, str | None]:
    """Shell out to `<cli_path>` to parse a semver. Tries `version`
    first (Touchstone's shape), falls back to `--version` (Sentinel +
    most Click-based CLIs) so we don't require the sibling to honor
    one particular sub-command convention.

    Returns ``(version, error)``; at most one is non-None. The error
    from the last attempt is surfaced so the reason is visible.
    """
    version, error = _try_version_args(cli_path, ["version"])
    if version is not None:
        return version, None
    # Fall back to --version for CLIs that don't expose a `version` subcommand.
    version, fallback_error = _try_version_args(cli_path, ["--version"])
    if version is not None:
        return version, None
    return None, fallback_error or error


def detect_sibling(
    name: str,
    *,
    project_marker: str,
    cwd: Path,
) -> SiblingStatus:
    """Detect one sibling by name. `project_marker` is the relative
    path of the project-local config (e.g. `.touchstone-config`)."""
    cli_path = shutil.which(name)
    version: str | None = None
    version_error: str | None = None
    if cli_path is not None:
        version, version_error = _read_version(cli_path)
    return SiblingStatus(
        name=name,
        cli_path=cli_path,
        version=version,
        version_error=version_error,
        project_marker=project_marker,
        project_marker_present=_project_marker_present(cwd, project_marker),
    )


def detect_siblings(cwd: Path) -> list[SiblingStatus]:
    """Detect all known Autumn Garage siblings relative to `cwd`."""
    return [
        detect_sibling("touchstone", project_marker=".touchstone-config", cwd=cwd),
        detect_sibling("sentinel", project_marker=".sentinel/config.toml", cwd=cwd),
    ]


def format_sibling_block(statuses: list[SiblingStatus]) -> str:
    """Render the "Autumn Garage siblings:" block.

    Glyphs follow the existing doctor output style:
    - ``✓`` — CLI installed + version parsed
    - ``!`` — CLI installed but version could not be parsed (non-fatal)
    - ``—`` — CLI not installed (informational, absence is normal)
    """
    lines = ["Autumn Garage siblings:"]
    for s in statuses:
        if s.cli_path is None:
            glyph = "—"
            state = "not installed"
        elif s.version is not None:
            glyph = "✓"
            state = f"{s.version} (installed)"
        else:
            glyph = "!"
            detail = s.version_error or "version unknown"
            state = f"installed, version unknown ({detail})"
        if s.project_marker_present:
            marker_note = f"{s.project_marker} present"
        else:
            marker_note = f"{s.project_marker} absent"
        lines.append(f"  {glyph} {s.name} {state} — {marker_note}")
    return "\n".join(lines)
