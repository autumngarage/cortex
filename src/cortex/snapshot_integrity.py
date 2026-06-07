"""Checkout vs committed-state snapshot integrity.

Derived layers such as ``state.md`` record the ``HEAD sha`` they were built
against. When an agent resumes on a feature branch whose work has not yet
landed in the committed corpus, manifest/status can look empty even though
git history shows active WIP. This module surfaces that mismatch visibly
instead of letting agents infer the project is idle.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cortex.frontmatter import parse_frontmatter

STATE_HEAD_SHA_RE = re.compile(r"^\s*-\s*HEAD sha:\s*([0-9a-fA-F]+)\s*$", re.MULTILINE)
_ORIGIN_BASE_REFS = ("origin/main", "origin/master")


@dataclass(frozen=True)
class GitCheckoutSnapshot:
    head_sha: str | None
    branch: str | None
    upstream_ref: str | None
    upstream_sha: str | None
    commits_ahead: int | None
    error: str | None = None


@dataclass(frozen=True)
class StateSnapshotRecord:
    recorded_head_sha: str | None
    generated_at: str | None


@dataclass(frozen=True)
class SnapshotIntegrityReport:
    checkout: GitCheckoutSnapshot
    state: StateSnapshotRecord
    warnings: tuple[str, ...]

    @property
    def head_mismatch(self) -> bool:
        recorded = self.state.recorded_head_sha
        current = self.checkout.head_sha
        if not recorded or not current:
            return False
        a, b = recorded.lower(), current.lower()
        return not (a.startswith(b) or b.startswith(a))

    @property
    def branch_has_unique_commits(self) -> bool:
        ahead = self.checkout.commits_ahead
        return ahead is not None and ahead > 0


def read_recorded_head_sha(state_text: str) -> str | None:
    """Parse ``HEAD sha:`` from a ``state.md`` Sources block."""

    match = STATE_HEAD_SHA_RE.search(state_text)
    if match:
        return match.group(1).lower()
    for line in state_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("HEAD sha:"):
            value = stripped.split(":", 1)[1].strip()
            if re.fullmatch(r"[0-9a-fA-F]+", value):
                return value.lower()
    return None


def read_state_snapshot(state_path: Path) -> StateSnapshotRecord:
    if not state_path.exists():
        return StateSnapshotRecord(recorded_head_sha=None, generated_at=None)
    try:
        text = state_path.read_text()
    except OSError:
        return StateSnapshotRecord(recorded_head_sha=None, generated_at=None)
    frontmatter, _body = parse_frontmatter(text)
    generated = frontmatter.get("Generated")
    generated_at = generated if isinstance(generated, str) else None
    return StateSnapshotRecord(
        recorded_head_sha=read_recorded_head_sha(text),
        generated_at=generated_at,
    )


def _git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def read_git_checkout(project_root: Path) -> GitCheckoutSnapshot:
    if shutil.which("git") is None:
        return GitCheckoutSnapshot(
            head_sha=None,
            branch=None,
            upstream_ref=None,
            upstream_sha=None,
            commits_ahead=None,
            error="git executable not found",
        )
    if not (project_root / ".git").exists():
        return GitCheckoutSnapshot(
            head_sha=None,
            branch=None,
            upstream_ref=None,
            upstream_sha=None,
            commits_ahead=None,
            error="not a git repository",
        )

    head = _git(project_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return GitCheckoutSnapshot(
            head_sha=None,
            branch=None,
            upstream_ref=None,
            upstream_sha=None,
            commits_ahead=None,
            error="could not resolve HEAD",
        )
    head_sha = head.stdout.strip().lower()

    branch_result = _git(project_root, "branch", "--show-current")
    branch = branch_result.stdout.strip() or None

    upstream_ref: str | None = None
    upstream_sha: str | None = None
    for ref in (*_ORIGIN_BASE_REFS, "main", "master"):
        probe = _git(project_root, "rev-parse", "--verify", "--quiet", ref)
        if probe.returncode == 0:
            upstream_ref = ref
            upstream_sha = probe.stdout.strip().lower()
            if branch and (
                branch == ref or branch == ref.removeprefix("origin/")
            ):
                # Comparing a branch to itself is not meaningful for WIP detection.
                upstream_ref = None
                upstream_sha = None
            break

    commits_ahead: int | None = None
    if upstream_ref is not None:
        count = _git(project_root, "rev-list", "--count", f"{upstream_ref}..HEAD")
        if count.returncode == 0 and count.stdout.strip().isdigit():
            commits_ahead = int(count.stdout.strip())

    return GitCheckoutSnapshot(
        head_sha=head_sha,
        branch=branch,
        upstream_ref=upstream_ref,
        upstream_sha=upstream_sha,
        commits_ahead=commits_ahead,
    )


def assess_snapshot_integrity(project_root: Path) -> SnapshotIntegrityReport:
    """Compare current git checkout against ``.cortex/state.md`` provenance."""

    state_path = project_root / ".cortex" / "state.md"
    checkout = read_git_checkout(project_root)
    state = read_state_snapshot(state_path)
    warnings: list[str] = []

    if checkout.error:
        warnings.append(f"snapshot check skipped: {checkout.error}")
        return SnapshotIntegrityReport(checkout=checkout, state=state, warnings=tuple(warnings))

    recorded = state.recorded_head_sha
    current = checkout.head_sha
    if recorded and current:
        a, b = recorded.lower(), current.lower()
        if not (a.startswith(b) or b.startswith(a)):
            branch_note = f" on branch `{checkout.branch}`" if checkout.branch else ""
            warnings.append(
                f"state.md was generated against HEAD {recorded[:12]}, but the "
                f"checkout is at {current[:12]}{branch_note}; run `cortex refresh-state` "
                f"on this branch or inspect git history for in-progress work"
            )

    ahead = checkout.commits_ahead
    if ahead is not None and ahead > 0 and checkout.branch not in (None, "main", "master"):
        warnings.append(
            f"branch `{checkout.branch}` is {ahead} commit{'s' if ahead != 1 else ''} "
            f"ahead of {checkout.upstream_ref or 'upstream'}; committed Cortex memory "
            f"may not reflect current WIP — use `git log {checkout.upstream_ref or 'origin/main'}..HEAD` "
            f"to rediscover active work"
        )

    return SnapshotIntegrityReport(checkout=checkout, state=state, warnings=tuple(warnings))
