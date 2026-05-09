"""Auto-sync on version-bump (Layer 2 of cortex#190).

This module is the single startup hook that fires from
``cortex.cli.cli`` before any subcommand body runs. It compares the
installed CLI's ``__version__`` against a marker in git metadata
(``.git/cortex/.last-cli-version``) and, when the difference is at
least minor, invokes the same code path as ``cortex update`` with the
doctor pass disabled.

Design notes:

- **One code path with `cortex update`.** Auto-sync calls
  :func:`cortex.commands.sync.run_sync`. There is no parallel
  implementation that could drift apart from the operator-driven
  command.
- **Marker writes are atomic.** `_write_marker` writes to
  ``.last-cli-version.tmp`` then ``os.replace``s into place, so a
  crash mid-write leaves either the prior or the new content but
  never a half-file.
- **Marker lives outside the working tree.** Operational state belongs
  in git metadata (``<gitdir>/cortex/.last-cli-version``), not
  ``.cortex/``. This avoids dirty-worktree false positives.
- **Legacy marker migration is best-effort.** If an old
  ``.cortex/.last-cli-version`` exists, we migrate it to the gitdir
  marker on first upgraded run and delete the legacy file. Failures are
  logged and ignored so user commands still run.
- **Skip list is explicit.** ``init``, ``update``, ``sync``, and
  ``migrate-state`` cannot trigger auto-sync, regardless of marker
  state — those commands either pre-date the marker (init) or
  already ARE the update code path (update/sync) or are explicitly lossy and
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
import fnmatch
import os
import re
import subprocess
import tomllib
from pathlib import Path

import click

from cortex import __version__

MARKER_FILENAME = ".last-cli-version"
MARKER_TMP_FILENAME = ".last-cli-version.tmp"
MARKER_SUBDIR = "cortex"
RELEASE_COMMIT_SUBJECT_RE = re.compile(r"^chore(\(.*\))?: release v\d+\.\d+\.\d+")
PLANNED_WRITE_PATTERNS: tuple[str, ...] = (
    ".cortex/state.md",
    ".cortex/.index.json",
    ".cortex/.index/**",
)

# Commands that MUST never trigger auto-sync. Each entry is the click
# command name as registered on the top-level group. The list is
# explicit by design (do not derive it from "is the project missing
# .cortex/?"): init's failure mode might leave a half-scaffolded
# directory that LOOKS sync-ready but isn't, and we'd rather refuse
# than guess.
SKIP_COMMANDS: frozenset[str] = frozenset({
    "init",
    "update",
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


def _git_dir(project_root: Path) -> Path | None:
    """Return the resolved gitdir for `project_root`, or None when unavailable.

    Fast path: when ``project_root/.git`` is a real directory, return it
    directly — no subprocess. This covers the overwhelming majority of
    invocations (every normal checkout) and keeps auto-sync's startup
    cost off the hot path. Only fall back to ``git rev-parse --git-dir``
    for the worktree / submodule case where ``.git`` is a file pointing
    at the real gitdir, or where the project is nested inside a parent
    repo. Skip entirely when ``.git`` is absent — we won't be a git repo.
    """

    direct = project_root / ".git"
    if direct.is_dir():
        return direct.resolve()
    if not direct.is_file():
        # Either `.git` is absent (not a git checkout) or it's some
        # exotic file-system entity. Either way, we can't safely write
        # operational state — skip cleanly.
        return None

    # `.git` is a file (linked worktree or submodule). Read the gitdir
    # pointer directly to avoid a subprocess. The file format is a
    # single line `gitdir: <path>` per `git-worktree(1)`.
    try:
        gitfile_text = direct.read_text()
    except OSError:
        return None

    gitdir_line = next(
        (line for line in gitfile_text.splitlines() if line.startswith("gitdir:")),
        None,
    )
    if gitdir_line is None:
        return None

    git_dir = Path(gitdir_line[len("gitdir:") :].strip())
    git_dir = (project_root / git_dir).resolve() if not git_dir.is_absolute() else git_dir.resolve()

    if not git_dir.exists() or not git_dir.is_dir():
        return None
    return git_dir


def _legacy_marker_path(project_root: Path) -> Path:
    return project_root / ".cortex" / MARKER_FILENAME


def _marker_path(project_root: Path) -> Path | None:
    """Return marker path under git metadata, or None outside git checkouts."""

    git_dir = _git_dir(project_root)
    if git_dir is None:
        return None
    return git_dir / MARKER_SUBDIR / MARKER_FILENAME


def _migrate_legacy_marker(project_root: Path, marker_path: Path | None) -> str | None:
    """Best-effort migration from `.cortex/.last-cli-version` to gitdir marker.

    Returns the migrated version when migration succeeded; otherwise None.
    Any migration failure is logged and ignored.
    """

    legacy_path = _legacy_marker_path(project_root)
    if not legacy_path.is_file():
        return None

    if marker_path is None:
        click.echo(
            "warning: auto-sync marker migration skipped (git metadata unavailable)",
            err=True,
        )
        return None

    if marker_path.is_file():
        # New marker already present; best-effort cleanup of legacy residue.
        with contextlib.suppress(OSError):
            legacy_path.unlink()
        return None

    try:
        value = legacy_path.read_text().strip()
    except OSError as exc:
        click.echo(
            f"warning: auto-sync marker migration failed: could not read legacy marker: {exc}",
            err=True,
        )
        return None

    if not value:
        with contextlib.suppress(OSError):
            legacy_path.unlink()
        return None

    try:
        _write_marker(project_root, value)
        legacy_path.unlink()
    except OSError as exc:
        click.echo(
            f"warning: auto-sync marker migration failed: {exc}",
            err=True,
        )
        return None

    return value


def _read_marker(project_root: Path) -> str | None:
    """Return marker contents (stripped), or None if missing/unreadable.

    If the legacy worktree marker exists, attempt one-time best-effort
    migration to the gitdir marker first.
    """

    marker_path = _marker_path(project_root)

    migrated = _migrate_legacy_marker(project_root, marker_path)
    if migrated is not None:
        return migrated

    if marker_path is None or not marker_path.is_file():
        return None

    try:
        return marker_path.read_text().strip() or None
    except OSError:
        return None


def _write_marker(project_root: Path, version: str) -> None:
    """Write the marker atomically under git metadata.

    No-op when `project_root` is not in a git checkout.

    Writes ``.last-cli-version.tmp`` first, then ``os.replace``s it
    onto the target. The replace is atomic on POSIX and on Windows
    (within a single filesystem), so a crash before or during the
    rename leaves the existing marker intact. A crash AFTER the
    rename is fine — the new value is the truth.
    """

    target_path = _marker_path(project_root)
    if target_path is None:
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(MARKER_TMP_FILENAME)
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


def _dirty_path_in_planned_write_set(project_root: Path) -> str | None:
    """Return the first dirty path auto-sync may write, or "" when git fails.

    None means there is no overlap. An empty string means git is unavailable or
    status failed, so the caller cannot safely auto-sync.
    """

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    for path in _paths_from_porcelain(result.stdout):
        if _path_overlaps_planned_writes(path):
            return path
    return None


def _paths_from_porcelain(status: str) -> list[str]:
    """Extract paths from porcelain v1 output."""

    paths: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path_field = line[3:].strip()
        if not path_field:
            continue
        if " -> " in path_field:
            old_path, new_path = path_field.split(" -> ", 1)
            paths.extend([old_path.strip(), new_path.strip()])
            continue
        paths.append(path_field.strip())
    return paths


def _path_overlaps_planned_writes(path: str) -> bool:
    normalized = path.strip().strip('"')
    return any(
        fnmatch.fnmatchcase(normalized, pattern)
        for pattern in PLANNED_WRITE_PATTERNS
    )


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

    conflicting_path = _dirty_path_in_planned_write_set(project_root)
    if conflicting_path == "":
        click.echo(
            "==> auto-sync: skipped (git unavailable); run `cortex update` manually",
            err=True,
        )
        return False
    if conflicting_path is not None:
        click.echo(
            f"==> auto-sync: skipped — dirty file in planned write set: {conflicting_path}",
            err=True,
        )
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

    marker_value = _read_marker(project_root)
    if marker_value is None:
        # First-ever invocation against this store — seed the marker
        # without syncing. The seed is what Layer 2 compares against
        # next time, so a brand-new store doesn't auto-sync on its
        # very first command (that's `cortex init`'s territory).
        # Best-effort write; never block the user's command on a
        # marker-write failure.
        with contextlib.suppress(OSError):
            _write_marker(project_root, current)
        return

    if marker_value == current:
        return

    marker_minor = _parse_minor(marker_value)
    if marker_minor is None:
        # Marker is corrupt / unparseable. Overwrite with the current
        # version so we get back to a known state, but don't sync —
        # we have no idea what the prior version actually was.
        with contextlib.suppress(OSError):
            _write_marker(project_root, current)
        return

    if marker_minor == current_minor:
        # Patch-level difference only. Bump the marker so the next
        # comparison runs against the latest patch baseline, but
        # don't run sync — patch releases are by convention safe.
        with contextlib.suppress(OSError):
            _write_marker(project_root, current)
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
        _write_marker(project_root, current)
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
