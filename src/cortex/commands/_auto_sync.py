"""Auto-sync on version-bump (Layer 2 of cortex#190).

This module is the single startup hook that fires from
``cortex.cli.cli`` before any subcommand body runs. It compares the
installed CLI's ``__version__`` against ``.cortex/.last-cli-version``
(the marker file) and, when the difference is at least minor,
invokes the same code path as ``cortex sync`` with the doctor pass
disabled.

Design notes:

- **One code path with `cortex sync`.** Auto-sync calls
  :func:`cortex.commands.sync.run_sync`. There is no parallel
  implementation that could drift apart from the operator-driven
  command.
- **Marker writes are atomic.** `_write_marker` writes to
  ``.last-cli-version.tmp`` then ``os.replace``s into place, so a
  crash mid-write leaves either the prior or the new content but
  never a half-file.
- **Skip list is explicit.** ``init``, ``sync``, and
  ``migrate-state`` cannot trigger auto-sync, regardless of marker
  state — those commands either pre-date the marker (init) or
  already ARE the sync code path (sync) or are explicitly lossy and
  must require operator consent (migrate-state). Init's own marker
  write at scaffold time is the seed; we never assume init succeeds
  to suppress the auto-sync, the skip list does.
- **Patch bumps do not trigger.** A patch release should be safe to
  install without rebuilding derived state. Only minor or major
  bumps trigger; the marker still advances on patch so the next
  minor compares against the new patch baseline.
- **Opt-out is two-fold.** A global ``--no-auto-sync`` flag and
  ``[sync].auto = false`` in ``.cortex/config.toml`` both disable
  the hook. Either path leaves the marker untouched so the operator
  can flip the switch back on later and the next bump is detected.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import tomllib
from pathlib import Path

import click

from cortex import __version__

MARKER_FILENAME = ".last-cli-version"
MARKER_TMP_FILENAME = ".last-cli-version.tmp"
RELEASE_COMMIT_SUBJECT_RE = re.compile(r"^chore(\(.*\))?: release v\d+\.\d+\.\d+")
DIRTY_TREE_SKIP_NOTICE = (
    "==> auto-sync: skipped (working tree is dirty); "
    "run `cortex sync` after committing"
)

# Commands that MUST never trigger auto-sync. Each entry is the click
# command name as registered on the top-level group. The list is
# explicit by design (do not derive it from "is the project missing
# .cortex/?"): init's failure mode might leave a half-scaffolded
# directory that LOOKS sync-ready but isn't, and we'd rather refuse
# than guess.
SKIP_COMMANDS: frozenset[str] = frozenset({
    "init",
    "sync",
    "migrate-state",
    "version",
})


def _parse_minor(version: str) -> tuple[int, int] | None:
    """Return (major, minor) for a semver-shaped version string, else None.

    Patch components and pre-release suffixes (e.g. ``-dev``,
    ``-rc1``) are intentionally dropped — Layer 2 only cares about
    minor-or-greater changes.
    """

    parts = version.strip().split("-", 1)[0].split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _read_marker(cortex_dir: Path) -> str | None:
    """Return the marker contents (stripped), or None if missing/unreadable."""

    marker_path = cortex_dir / MARKER_FILENAME
    if not marker_path.is_file():
        return None
    try:
        return marker_path.read_text().strip() or None
    except OSError:
        return None


def _write_marker(cortex_dir: Path, version: str) -> None:
    """Write the marker atomically.

    Writes ``.last-cli-version.tmp`` first, then ``os.replace``s it
    onto the target. The replace is atomic on POSIX and on Windows
    (within a single filesystem), so a crash before or during the
    rename leaves the existing marker intact. A crash AFTER the
    rename is fine — the new value is the truth.
    """

    cortex_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = cortex_dir / MARKER_TMP_FILENAME
    target_path = cortex_dir / MARKER_FILENAME
    tmp_path.write_text(version)
    os.replace(tmp_path, target_path)


def _config_disables_auto_sync(cortex_dir: Path) -> bool:
    """Return True when `.cortex/config.toml` has `[sync].auto = false`."""

    config_path = cortex_dir / "config.toml"
    if not config_path.is_file():
        return False
    try:
        data = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return False
    sync_section = data.get("sync")
    if not isinstance(sync_section, dict):
        return False
    return sync_section.get("auto") is False


def _git_stdout(project_root: Path, args: list[str]) -> str | None:
    """Return stripped git stdout, or None when git exits non-zero."""

    result = subprocess.run(
        ["git", *args],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _working_tree_is_dirty(project_root: Path) -> bool | None:
    """Return True when git reports any staged/unstaged/untracked change.

    None means git is unavailable, so the caller cannot safely auto-sync.
    A non-zero git status is treated as dirty by design.
    """

    try:
        status = _git_stdout(project_root, ["status", "--porcelain"])
    except FileNotFoundError:
        return None
    return status is None or bool(status)


def _release_commit_on_non_default_branch(project_root: Path) -> bool:
    """Return True for a release-bump commit before it has landed on default."""

    subject = _git_stdout(project_root, ["log", "-1", "--format=%s"])
    if subject is None or not RELEASE_COMMIT_SUBJECT_RE.match(subject):
        return False

    current_branch = _git_stdout(project_root, ["branch", "--show-current"]) or ""
    default_branch = (
        _git_stdout(project_root, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"])
        or ""
    )
    if default_branch.startswith("origin/"):
        default_branch = default_branch.removeprefix("origin/")

    return not current_branch or not default_branch or current_branch != default_branch


def _auto_sync_preflight_allows(project_root: Path) -> bool:
    """Gate expensive auto-sync regeneration on a safe Git state."""

    dirty = _working_tree_is_dirty(project_root)
    if dirty is None:
        click.echo(
            "==> auto-sync: skipped (git unavailable); run `cortex sync` manually",
            err=True,
        )
        return False
    if dirty:
        click.echo(DIRTY_TREE_SKIP_NOTICE, err=True)
        return False

    if _release_commit_on_non_default_branch(project_root):
        click.echo(
            "==> auto-sync: skipped (release commit on feature branch)",
            err=True,
        )
        return False

    return True


def maybe_auto_sync(
    project_root: Path,
    invoked_subcommand: str | None,
    *,
    disabled: bool,
) -> None:
    """Run auto-sync when the marker indicates a minor-or-greater bump.

    Called from the top-level click group callback BEFORE any
    subcommand body runs. Silent on the no-op paths (no marker → seed
    it; same version → nothing to do; patch bump → bump marker only;
    skipped command → return without touching anything; opt-out →
    return without touching the marker).
    """

    if disabled:
        return
    if invoked_subcommand in SKIP_COMMANDS:
        return

    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        # No project-level Cortex — nothing to sync. (Init's own job.)
        return

    if _config_disables_auto_sync(cortex_dir):
        return

    current = __version__
    current_minor = _parse_minor(current)
    if current_minor is None:
        # Defensive: if our own __version__ is unparseable we'd rather
        # do nothing than corrupt the marker.
        return

    marker_value = _read_marker(cortex_dir)
    if marker_value is None:
        # First-ever invocation against this store — seed the marker
        # without syncing. The seed is what Layer 2 compares against
        # next time, so a brand-new store doesn't auto-sync on its
        # very first command (that's `cortex init`'s territory).
        # Best-effort write; never block the user's command on a
        # marker-write failure.
        with contextlib.suppress(OSError):
            _write_marker(cortex_dir, current)
        return

    if marker_value == current:
        return

    marker_minor = _parse_minor(marker_value)
    if marker_minor is None:
        # Marker is corrupt / unparseable. Overwrite with the current
        # version so we get back to a known state, but don't sync —
        # we have no idea what the prior version actually was.
        with contextlib.suppress(OSError):
            _write_marker(cortex_dir, current)
        return

    if marker_minor == current_minor:
        # Patch-level difference only. Bump the marker so the next
        # comparison runs against the latest patch baseline, but
        # don't run sync — patch releases are by convention safe.
        with contextlib.suppress(OSError):
            _write_marker(cortex_dir, current)
        return

    if not _auto_sync_preflight_allows(project_root):
        return

    # Minor (or major) bump. Run sync, then advance the marker.
    click.echo(
        f"==> auto-sync: cortex {marker_value} → {current}",
        err=True,
    )
    try:
        from cortex.commands.sync import run_sync

        run_sync(project_root, run_doctor=False, output_prefix="==> auto-sync:")
    except BaseException as exc:
        # Auto-sync MUST NOT block the user's command. We catch
        # BaseException (not just Exception) so that SystemExit from
        # underlying writers — e.g. `require_compatible` calls
        # `sys.exit(2)` when SPEC_VERSION is missing or unsupported —
        # turns into a visible warning instead of taking down the
        # outer command. KeyboardInterrupt remains the operator's
        # call to make: re-raise so Ctrl+C still propagates.
        if isinstance(exc, KeyboardInterrupt):
            raise
        click.echo(
            f"warning: auto-sync failed: {exc}; continuing with original command.",
            err=True,
        )
        return

    try:
        _write_marker(cortex_dir, current)
    except OSError as exc:
        click.echo(
            f"warning: auto-sync ran but could not update marker: {exc}",
            err=True,
        )


def project_root_from_path_override(path_override: Path | None) -> Path:
    """Resolve the project root the same way the click group's --path option does."""

    target = path_override if path_override is not None else Path.cwd()
    return Path(target).resolve()


# Sentinel printed by tests / for visibility when a malformed environment
# disables auto-sync. Kept stable so downstream tooling can grep for it.
SENTINEL_AUTO_SYNC_DISABLED = "auto-sync disabled"


def auto_sync_via_env_disabled() -> bool:
    """Return True when ``CORTEX_NO_AUTO_SYNC=1`` is set in the environment.

    The env var is the same opt-out as ``--no-auto-sync`` for callers
    that can't easily inject CLI flags (e.g. shell aliases that wrap
    ``cortex`` with extra args). Empty / unset / non-1 values are
    treated as "auto-sync allowed".
    """

    value = os.environ.get("CORTEX_NO_AUTO_SYNC", "").strip()
    return value == "1"


__all__ = [
    "MARKER_FILENAME",
    "SENTINEL_AUTO_SYNC_DISABLED",
    "SKIP_COMMANDS",
    "auto_sync_via_env_disabled",
    "maybe_auto_sync",
    "project_root_from_path_override",
]
