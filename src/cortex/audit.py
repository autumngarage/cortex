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
- **T1.10** — a tag matching the release pattern (default
  ``^v\\d+\\.\\d+\\.\\d+``) was created in the audit window. Walks
  ``git tag --list`` rather than ``git log``; the tag's commit date
  defines the 72h match window. Expects ``Type: release``.

Deferred off the current git-derived audit slice:

- T1.2 (test failure), T1.6 (Sentinel cycle), T1.7 (Touchstone pre-merge)
  — these need runtime session state or hooks, so they are v1.x work.
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

# Default release-tag pattern per Protocol § 2 ("T1.10 ... default `^v\d+\.\d+\.\d+`").
# Projects using calendar versioning or non-`v`-prefix tags override via .cortex/protocol.md.
DEFAULT_TAG_PATTERN = re.compile(r"^v\d+\.\d+\.\d+")


class Trigger(StrEnum):
    T1_1 = "T1.1"
    T1_5 = "T1.5"
    T1_8 = "T1.8"
    T1_9 = "T1.9"
    T1_10 = "T1.10"


EXPECTED_TYPE: dict[Trigger, str] = {
    Trigger.T1_1: "decision",
    Trigger.T1_5: "decision",
    Trigger.T1_8: "decision",
    Trigger.T1_9: "pr-merged",
    Trigger.T1_10: "release",
}


@dataclass(frozen=True)
class Commit:
    sha: str
    date: datetime
    subject: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Tag:
    name: str
    sha: str
    date: datetime


@dataclass(frozen=True)
class JournalEntry:
    path: Path
    date: datetime
    type_: str | None
    trigger: str | None
    tag: str | None = None


@dataclass(frozen=True)
class TriggerFire:
    """One Tier-1 trigger fire.

    Either ``commit`` or ``tag`` is set depending on the source: T1.1/1.5/1.8/1.9
    fire from commits; T1.10 fires from tags (a tag is its own ref object with
    its own date, distinct from any one commit on the branch).
    """

    trigger: Trigger
    matched: bool
    matched_entry: Path | None
    commit: Commit | None = None
    tag: Tag | None = None

    @property
    def short_sha(self) -> str:
        if self.commit is not None:
            return self.commit.sha[:8]
        if self.tag is not None:
            return self.tag.sha[:8]
        return "????????"

    @property
    def source_date(self) -> datetime:
        if self.commit is not None:
            return self.commit.date
        if self.tag is not None:
            return self.tag.date
        raise RuntimeError("TriggerFire has neither commit nor tag")

    @property
    def label(self) -> str:
        """Human-readable identifier for the fire's source — used in audit output."""
        if self.tag is not None:
            return f"tag {self.tag.name}"
        if self.commit is not None:
            return f"commit {self.commit.subject}"
        return "<unknown source>"


@dataclass
class AuditReport:
    since: datetime
    commits_examined: int
    tags_examined: int = 0
    fires: list[TriggerFire] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def unmatched(self) -> list[TriggerFire]:
        return [f for f in self.fires if not f.matched]


def _resolve_default_branch(project_root: Path) -> str:
    """Best-effort default-branch ref detection.

    Prefers the remote ref (``origin/main``, ``origin/master``) so a stale
    or missing local branch doesn't cause the audit to silently fall back
    to an older snapshot. Falls back to local ``main`` / ``master`` when
    the remote ref isn't available (e.g. fresh `git init` repos used by
    tests). Returns a ref that `git log` can resolve.
    """
    probes: tuple[tuple[list[str], str], ...] = (
        (["rev-parse", "--abbrev-ref", "origin/HEAD"], "origin/HEAD-detected"),
        (["rev-parse", "--verify", "--quiet", "refs/remotes/origin/main"], "origin/main"),
        (["rev-parse", "--verify", "--quiet", "refs/remotes/origin/master"], "origin/master"),
        (["rev-parse", "--verify", "--quiet", "refs/heads/main"], "main"),
        (["rev-parse", "--verify", "--quiet", "refs/heads/master"], "master"),
    )
    for cmd, label in probes:
        result = subprocess.run(
            ["git", "-C", str(project_root), *cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            continue
        if label == "origin/HEAD-detected":
            # The abbrev-ref form returns e.g. ``origin/main``. Use it as-is
            # so we target the remote tip, not a local copy that may lag.
            return result.stdout.strip()
        return label
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
    # ``--first-parent`` restricts the walk to mainline commits only. Without
    # it, a merge commit on main would fan out into every feature-branch
    # commit reachable from that merge, producing one T1.9 fire per WIP
    # commit instead of per PR merge.
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "log",
            ref,
            "--first-parent",
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


def load_tags(
    project_root: Path,
    since_days: int,
    *,
    pattern: re.Pattern[str] = DEFAULT_TAG_PATTERN,
) -> list[Tag]:
    """Return tags matching ``pattern`` whose creation date falls within the window.

    Uses ``git for-each-ref refs/tags`` so both lightweight tags (which have
    only the underlying commit's date) and annotated tags (which carry their
    own ``creatordate``) work. Tags outside the audit window are dropped.
    """
    cutoff = datetime.now(UTC) - timedelta(days=since_days)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "for-each-ref",
            "--format=%(refname:short)%09%(objectname)%09%(creatordate:iso-strict)",
            "refs/tags",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    tags: list[Tag] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, sha, iso_date = parts
        if not pattern.match(name):
            continue
        try:
            tag_date = datetime.fromisoformat(iso_date)
        except ValueError:
            continue
        if tag_date < cutoff:
            continue
        tags.append(Tag(name=name, sha=sha, date=tag_date))
    return tags


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
        type_, trigger, tag = _journal_header_fields(path)
        entries.append(
            JournalEntry(
                path=path,
                date=entry_date,
                type_=type_,
                trigger=trigger,
                tag=tag,
            )
        )
    return entries


def _journal_header_fields(path: Path) -> tuple[str | None, str | None, str | None]:
    """Return ``(Type, Trigger, Tag)`` for a Journal entry.

    All three fields accept YAML frontmatter or bold-inline (SPEC § 6).
    Missing fields return None. ``Trigger`` only appears on Protocol-
    triggered entries; ``Tag`` only appears on ``Type: release`` entries
    naming the specific git tag the release record describes.
    """
    try:
        text = path.read_text()
    except OSError:
        return None, None, None
    frontmatter, _body = parse_frontmatter(text)
    header = "\n".join(text.splitlines()[:40])

    def _from_either(field: str) -> str | None:
        fm_value = frontmatter.get(field)
        if isinstance(fm_value, str) and fm_value.strip():
            return fm_value.strip()
        m = re.search(rf"\*\*{re.escape(field)}:\*\*\s*([^\n]+)", header)
        if m:
            return m.group(1).strip()
        return None

    type_ = _from_either("Type")
    raw_trigger = _from_either("Trigger")
    trigger: str | None = None
    if raw_trigger:
        # Allow values like "T1.3 (Plan status changed)" or a bare "T1.3".
        t_match = re.match(r"(T\d+\.\d+)", raw_trigger)
        trigger = t_match.group(1) if t_match else raw_trigger
    tag = _from_either("Tag")
    return type_, trigger, tag


def _best_matching_entry(
    source_date: datetime,
    trigger: Trigger,
    expected_type: str,
    candidates: Iterable[JournalEntry],
    *,
    tag_name: str | None = None,
) -> JournalEntry | None:
    """Return the nearest in-window candidate for this fire, or None.

    A candidate matches only if its ``Type:`` matches the expected value and,
    when it declares a ``Trigger:`` field, that trigger equals the fire's
    trigger. Human-authored Protocol entries without a ``Trigger:`` still
    count as valid matches (Type-only) so teams aren't forced to retrofit
    the field.

    For T1.10 (release) fires, ``tag_name`` is required and the entry's
    structured ``**Tag:**`` field must equal it. This prevents one
    ``Type: release`` entry from accidentally satisfying every nearby
    release tag — each release entry has to declare which tag it records.
    Entries without a ``Tag:`` field are not considered for T1.10 matches
    when a tag_name is in scope (the writer is expected to set the field;
    cortex doctor would otherwise pass a stale or generic release entry
    against any tag).
    """
    window = timedelta(hours=JOURNAL_MATCH_WINDOW_HOURS)
    best: JournalEntry | None = None
    best_delta = window
    for entry in candidates:
        if entry.type_ != expected_type:
            continue
        if entry.trigger is not None and entry.trigger != trigger.value:
            continue
        if trigger is Trigger.T1_10 and tag_name is not None and entry.tag != tag_name:
            continue
        delta = abs(entry.date - source_date)
        if delta <= best_delta:
            best = entry
            best_delta = delta
    return best


def audit(
    project_root: Path,
    since_days: int = DEFAULT_WINDOW_DAYS,
    *,
    branch: str | None = None,
    tag_pattern: re.Pattern[str] = DEFAULT_TAG_PATTERN,
) -> AuditReport:
    warnings: list[str] = []
    try:
        commits = load_commits(project_root, since_days, branch=branch)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        warnings.append(f"commit audit unavailable: {_format_subprocess_error(exc)}")
        commits = []
    try:
        tags = load_tags(project_root, since_days, pattern=tag_pattern)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError) as exc:
        warnings.append(f"tag audit unavailable: {_format_subprocess_error(exc)}")
        tags = []
    journal = load_journal_entries(project_root)
    report = AuditReport(
        since=datetime.now(UTC) - timedelta(days=since_days),
        commits_examined=len(commits),
        tags_examined=len(tags),
        warnings=warnings,
    )
    # A Journal entry is one event per file (SPEC § 3.5) — each entry can
    # satisfy at most one trigger fire. Process fires oldest-first so the
    # earliest trigger wins the entry; later fires needing the same type
    # must find their own unconsumed entry or remain unmatched.
    consumed: set[Path] = set()
    # Build the unified, oldest-first fire stream across commit-sourced and
    # tag-sourced triggers. Each entry is (source_date, trigger, commit_or_None, tag_or_None).
    ordered_fires: list[tuple[datetime, Trigger, Commit | None, Tag | None]] = []
    for commit_item in commits:
        for trigger in classify(commit_item):
            ordered_fires.append((commit_item.date, trigger, commit_item, None))
    for tag_item in tags:
        ordered_fires.append((tag_item.date, Trigger.T1_10, None, tag_item))
    ordered_fires.sort(key=lambda f: f[0])
    for source_date, trigger, fire_commit, fire_tag in ordered_fires:
        available = [e for e in journal if e.path not in consumed]
        entry = _best_matching_entry(
            source_date,
            trigger,
            EXPECTED_TYPE[trigger],
            available,
            tag_name=fire_tag.name if fire_tag is not None else None,
        )
        if entry is not None:
            consumed.add(entry.path)
        report.fires.append(
            TriggerFire(
                trigger=trigger,
                matched=entry is not None,
                matched_entry=entry.path if entry else None,
                commit=fire_commit,
                tag=fire_tag,
            )
        )
    return report


def _format_subprocess_error(exc: BaseException) -> str:
    """Return a compact user-facing reason for a failed git audit query."""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = (exc.stderr or "").strip()
        if stderr:
            return stderr.splitlines()[-1]
        return f"git exited {exc.returncode}"
    if isinstance(exc, FileNotFoundError):
        return "git not installed or working directory missing"
    return str(exc)


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
        _frontmatter, body = parse_frontmatter(text)
        # Only sample body bullets; YAML frontmatter list items (e.g.
        # `Sources`, `Omitted`) are metadata, not digest claims.
        claims = [
            line
            for line in body.splitlines()
            if line.lstrip().startswith(("- ", "* "))
        ]
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
