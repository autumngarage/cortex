"""Tier-1 Protocol audit — `cortex doctor --audit`.

Protocol § 2 lists nine Tier-1 (machine-observable) write triggers. For each
commit in a window of git history this module classifies which triggers
fired, then checks whether a Journal entry of the expected ``Type:`` was
authored within the same 72-hour window. Unmatched triggers surface as
warnings (never errors in this first slice — post-hoc retrofitting a
Journal entry should be easy, not blocking).

First-slice coverage:

- **T1.1** — commit diff touches ``.cortex/doctrine/``, ``.cortex/plans/``,
  ``principles/``, or ``SPEC.md``. Expects ``Type: decision``.
- **T1.5** — commit diff touches ``pyproject.toml``, ``package.json``,
  ``Cargo.toml``, ``go.mod``, or ``Gemfile``. Expects ``Type: decision``.
- **T1.8** — commit subject matches ``fix: ... regression``,
  ``refactor: ... (removes|introduces)``, or
  ``feat: ... (breaking|replaces)``. Expects ``Type: decision``.
- **T1.9** — commit landed on the default branch (every commit in the
  audited range, since this repo uses squash-merge to main). Expects
  ``Type: pr-merged``.

Deferred to a follow-up slice (tracked in PLAN.md Phase B):

- T1.2 (test failure), T1.6 (Sentinel cycle), T1.7 (Touchstone pre-merge)
  — these need runtime session state, not git state.
- T1.3 (Plan ``Status:`` change) and T1.4 (file deletion >100 lines) —
  need per-commit diff parsing.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from cortex.frontmatter import parse_frontmatter

DEFAULT_WINDOW_DAYS = 7
JOURNAL_MATCH_WINDOW_HOURS = 72

DEP_MANIFESTS = ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Gemfile")

T1_8_RE = re.compile(
    r"^(fix:\s.*regression"
    r"|refactor:\s.*(?:removes|introduces)"
    r"|feat:\s.*(?:breaking|replaces))",
    re.IGNORECASE,
)

T1_1_PATH_PREFIXES = (".cortex/doctrine/", ".cortex/plans/", "principles/")
T1_1_EXACT_PATHS = ("SPEC.md",)


class Trigger(StrEnum):
    T1_1 = "T1.1"
    T1_5 = "T1.5"
    T1_8 = "T1.8"
    T1_9 = "T1.9"


EXPECTED_TYPE: dict[Trigger, str] = {
    Trigger.T1_1: "decision",
    Trigger.T1_5: "decision",
    Trigger.T1_8: "decision",
    Trigger.T1_9: "pr-merged",
}


@dataclass(frozen=True)
class Commit:
    sha: str
    date: datetime
    subject: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class JournalEntry:
    path: Path
    date: datetime
    type_: str | None


@dataclass(frozen=True)
class TriggerFire:
    commit: Commit
    trigger: Trigger
    matched: bool
    matched_entry: Path | None


@dataclass
class AuditReport:
    since: datetime
    commits_examined: int
    fires: list[TriggerFire] = field(default_factory=list)

    @property
    def unmatched(self) -> list[TriggerFire]:
        return [f for f in self.fires if not f.matched]


def _resolve_default_branch(project_root: Path) -> str:
    """Best-effort default-branch detection.

    Tries ``refs/remotes/origin/HEAD`` first (set by ``git clone``), then
    falls back to common names (``main``, ``master``). Returns the first
    branch that resolves.
    """
    for candidate_cmd in (
        ["rev-parse", "--abbrev-ref", "origin/HEAD"],
        ["rev-parse", "--verify", "--quiet", "refs/heads/main"],
        ["rev-parse", "--verify", "--quiet", "refs/heads/master"],
    ):
        result = subprocess.run(
            ["git", "-C", str(project_root), *candidate_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            ref = result.stdout.strip()
            return ref.removeprefix("origin/")
    return "main"


def load_commits(
    project_root: Path,
    since_days: int,
    branch: str | None = None,
) -> list[Commit]:
    """Parse ``git log`` output for commits on the default branch.

    Restricting to the default branch matters for T1.9 classification:
    feature-branch work-in-progress commits haven't been merged yet, so they
    do not fire the ``pr-merged`` trigger. Callers can override ``branch``
    to audit a different line of history.
    """
    ref = branch or _resolve_default_branch(project_root)
    since_iso = (datetime.now(UTC) - timedelta(days=since_days)).isoformat()
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            ref,
            f"--since={since_iso}",
            "--name-only",
            "--pretty=format:--commit--%n%H%n%cI%n%s",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    commits: list[Commit] = []
    for block in result.stdout.split("--commit--\n"):
        if not block.strip():
            continue
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        sha, iso_date, subject = lines[0], lines[1], lines[2]
        files = tuple(line for line in lines[3:] if line.strip())
        commits.append(
            Commit(
                sha=sha,
                date=datetime.fromisoformat(iso_date),
                subject=subject,
                files=files,
            )
        )
    return commits


def classify(commit: Commit) -> list[Trigger]:
    fired: list[Trigger] = []
    if any(
        path.startswith(T1_1_PATH_PREFIXES) or path in T1_1_EXACT_PATHS
        for path in commit.files
    ):
        fired.append(Trigger.T1_1)
    if any(Path(path).name in DEP_MANIFESTS for path in commit.files):
        fired.append(Trigger.T1_5)
    if T1_8_RE.match(commit.subject):
        fired.append(Trigger.T1_8)
    # T1.9: every commit in the audited range has landed on the default
    # branch (git log runs against the current HEAD). The audit is run
    # against main in practice, so every commit is a merge by convention.
    fired.append(Trigger.T1_9)
    return fired


def load_journal_entries(project_root: Path) -> list[JournalEntry]:
    journal_dir = project_root / ".cortex" / "journal"
    if not journal_dir.exists():
        return []
    entries: list[JournalEntry] = []
    for path in sorted(journal_dir.glob("*.md")):
        match = re.match(r"^(\d{4}-\d{2}-\d{2})-", path.name)
        if not match:
            continue
        entry_date = datetime.fromisoformat(match.group(1)).replace(tzinfo=UTC)
        entries.append(
            JournalEntry(
                path=path,
                date=entry_date,
                type_=_journal_type(path),
            )
        )
    return entries


def _journal_type(path: Path) -> str | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    frontmatter, _body = parse_frontmatter(text)
    value = frontmatter.get("Type")
    if isinstance(value, str) and value.strip():
        return value.strip()
    header = "\n".join(text.splitlines()[:40])
    m = re.search(r"\*\*Type:\*\*\s*([^\n]+)", header)
    if m:
        return m.group(1).strip()
    return None


def _best_matching_entry(
    commit: Commit, expected_type: str, candidates: Iterable[JournalEntry]
) -> JournalEntry | None:
    """Return the nearest in-window candidate of the expected type, or None."""
    window = timedelta(hours=JOURNAL_MATCH_WINDOW_HOURS)
    best: JournalEntry | None = None
    best_delta = window
    for entry in candidates:
        if entry.type_ != expected_type:
            continue
        delta = abs(entry.date - commit.date)
        if delta <= best_delta:
            best = entry
            best_delta = delta
    return best


def audit(
    project_root: Path,
    since_days: int = DEFAULT_WINDOW_DAYS,
    *,
    branch: str | None = None,
) -> AuditReport:
    commits = load_commits(project_root, since_days, branch=branch)
    journal = load_journal_entries(project_root)
    report = AuditReport(
        since=datetime.now(UTC) - timedelta(days=since_days),
        commits_examined=len(commits),
    )
    # A Journal entry is one event per file (SPEC § 3.5) — each entry can
    # satisfy at most one trigger fire. Process fires oldest-first so the
    # earliest trigger wins the entry; later fires needing the same type
    # must find their own unconsumed entry or remain unmatched.
    consumed: set[Path] = set()
    ordered_fires: list[tuple[Commit, Trigger]] = []
    for commit in sorted(commits, key=lambda c: c.date):
        for trigger in classify(commit):
            ordered_fires.append((commit, trigger))
    for commit, trigger in ordered_fires:
        available = [e for e in journal if e.path not in consumed]
        entry = _best_matching_entry(commit, EXPECTED_TYPE[trigger], available)
        if entry is not None:
            consumed.add(entry.path)
        report.fires.append(
            TriggerFire(
                commit=commit,
                trigger=trigger,
                matched=entry is not None,
                matched_entry=entry.path if entry else None,
            )
        )
    return report


_DIGEST_CITATION_RE = re.compile(r"journal/[A-Za-z0-9._-]+")


def audit_digests(project_root: Path, sample_per_digest: int = 5) -> list[str]:
    """Return a list of warning messages for digests whose claims lack citations.

    Simple first-slice heuristic per SPEC § 5.4: for each digest entry,
    sample up to ``sample_per_digest`` lines that look like claims (bullets
    or sentences outside fenced code blocks) and require each one either
    contains a ``journal/...`` path or appears in a bullet that cites one.
    Digests without any citations, or where more than half the sample
    claims lack citations, produce a warning.
    """
    warnings: list[str] = []
    for entry in load_journal_entries(project_root):
        if entry.type_ != "digest":
            continue
        text = entry.path.read_text()
        claims = [line for line in text.splitlines() if line.strip().startswith(("- ", "* "))]
        if not claims:
            warnings.append(
                f"{entry.path.relative_to(project_root)}: digest has no bulleted claims to sample."
            )
            continue
        sample = claims[:sample_per_digest]
        uncited = [line for line in sample if not _DIGEST_CITATION_RE.search(line)]
        if len(uncited) > len(sample) // 2:
            warnings.append(
                f"{entry.path.relative_to(project_root)}: {len(uncited)}/{len(sample)} sampled "
                "claims lack a `journal/...` citation (SPEC § 5.4)."
            )
    return warnings
