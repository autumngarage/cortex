"""Inline Tier 1 trigger evaluation against a diff.

Where ``cortex.audit`` looks backward over a window of git history to flag
*missing* Journal entries (post-hoc), this module evaluates Tier 1 triggers
*forward*: given a specific diff (a ref range or the staged diff), report
which triggers fire so an agent can author the right Journal entry inline,
before context is lost.

This is the primitive surface for `cortex check-triggers` (issue #195 step 1).
Stop-hook recipes, CI ``--strict`` gating, and refactoring existing post-merge
hooks to call this primitive are explicit follow-ups.

Coverage in this slice — the deterministic, diff-derivable triggers:

- **T1.1** — diff touches ``.cortex/doctrine/``, ``.cortex/plans/``,
  ``principles/``, or ``SPEC.md``. Reuses ``cortex.audit`` path constants.
- **T1.4** — a file deletion exceeds the configured line threshold
  (default 100; ``T1.4.line-threshold:`` in ``.cortex/protocol.md`` overrides).
- **T1.5** — diff touches a dependency manifest from
  ``cortex.audit.DEP_MANIFESTS``.
- **T1.8** — a commit subject in the ref range matches the regex set in
  ``cortex.audit.T1_8_RE``.

Out of scope here (see command help epilog for the user-facing list): T1.2
(test failure — runtime), T1.3 (plan status change — cross-cuts plan
machinery), T1.6 (Sentinel cycle — runtime), T1.7 (Touchstone pre-merge —
runtime), T1.9 (post-merge — already wired via ``scripts/cortex-pr-merged-hook.sh``),
T1.10 (release — already wired via the release-substitution path #192).

Engineering notes:

- All trigger detection constants (path prefixes, manifest list, T1.8 regex)
  are imported from ``cortex.audit`` so a future Protocol bump rolls through
  in one place. **One code path** principle.
- Threshold overrides parse from ``.cortex/protocol.md`` using a permissive
  ``key: value`` scanner; unparseable values fall back to the default with a
  stderr warning (no silent failure).
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from cortex.audit import (
    DEP_MANIFESTS,
    T1_1_EXACT_PATHS,
    T1_1_PATH_PREFIXES,
    T1_8_RE,
    Trigger,
)

DEFAULT_DELETION_LINE_THRESHOLD = 100

# Template a project's writer should reach for when each trigger fires.
# Tracks Protocol § 2 verbatim — change here only when the spec table changes.
TEMPLATE_FOR_TRIGGER: dict[Trigger, str] = {
    Trigger.T1_1: "journal/decision.md",
    Trigger.T1_5: "journal/decision.md",
    Trigger.T1_8: "journal/decision.md",
    # T1.4 isn't in the audit-side ``Trigger`` enum because audit doesn't
    # exercise it (deferred there); we use a literal string in the NDJSON
    # output so the template column stays correct.
}

T1_4_TEMPLATE = "journal/decision.md"


@dataclass(frozen=True)
class DiffCommit:
    """Lightweight commit record for T1.8 evaluation over a ref range."""

    sha: str
    subject: str


@dataclass(frozen=True)
class DeletionRow:
    """One file-deletion stat row from ``git diff --numstat``.

    ``deleted_lines`` is the count from numstat (``-`` for binary files,
    which we filter out before reaching here).
    """

    path: str
    deleted_lines: int


@dataclass(frozen=True)
class TriggerHit:
    """One fired trigger, ready to render to NDJSON.

    ``trigger`` is the literal string written to the output (e.g. ``"T1.4"``);
    we don't constrain to the ``Trigger`` enum because the user-visible vocab
    is wider than the audit-side enum.
    """

    trigger: str
    reason: str
    template: str
    ref: str
    files: tuple[str, ...] = ()
    commit: str | None = None
    subject: str | None = None
    lines_deleted: int | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "trigger": self.trigger,
            "reason": self.reason,
            "template": self.template,
            "ref": self.ref,
        }
        if self.files:
            out["files"] = list(self.files)
        if self.commit is not None:
            out["commit"] = self.commit
        if self.subject is not None:
            out["subject"] = self.subject
        if self.lines_deleted is not None:
            out["lines_deleted"] = self.lines_deleted
        return out


@dataclass
class EvalConfig:
    """Resolved trigger thresholds and patterns for one project.

    Defaults match Protocol § 2; overrides are parsed from
    ``.cortex/protocol.md`` (see ``load_protocol_overrides``). Each field
    being a separate attribute makes it cheap to extend per future Protocol
    bumps without rewiring the call sites.
    """

    deletion_line_threshold: int = DEFAULT_DELETION_LINE_THRESHOLD
    t1_8_re: re.Pattern[str] = T1_8_RE
    t1_1_path_prefixes: tuple[str, ...] = T1_1_PATH_PREFIXES
    t1_1_exact_paths: tuple[str, ...] = T1_1_EXACT_PATHS
    dep_manifests: tuple[str, ...] = DEP_MANIFESTS
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Override parsing
# ---------------------------------------------------------------------------


_OVERRIDE_KEY_RE = re.compile(
    r"^\s*(T1\.\d+\.[a-z0-9_-]+)\s*:\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def load_protocol_overrides(project_root: Path) -> EvalConfig:
    """Read project-specific Protocol overrides from ``.cortex/protocol.md``.

    Recognized keys today (others surface as warnings — never silent):

    - ``T1.4.line-threshold: <int>``

    Other Protocol § 6 overrides (``T1.7.patterns``, ``T1.8.patterns``,
    ``T1.10.tag-pattern``) are accepted-but-not-yet-wired in this slice. We
    parse them and emit a stderr warning so users know their override didn't
    take effect rather than silently being honored. This matches the issue
    proposal's stance that the parser stay narrow.

    Unparseable values fall back to defaults with a stderr warning.
    """
    config = EvalConfig()
    path = project_root / ".cortex" / "protocol.md"
    if not path.exists():
        return config
    try:
        text = path.read_text()
    except OSError as exc:
        config.warnings.append(f"could not read {path}: {exc}; using defaults")
        return config

    text_no_code = _strip_fenced_code(text)
    seen: set[str] = set()
    for match in _OVERRIDE_KEY_RE.finditer(text_no_code):
        key = match.group(1).strip()
        raw_value = match.group(2).strip()
        # First match wins per key — the protocol document is read top-to-
        # bottom and a project's override is conventionally near the top.
        if key in seen:
            continue
        seen.add(key)
        _apply_override(config, key, raw_value)
    return config


def _strip_fenced_code(text: str) -> str:
    """Remove ```fenced``` blocks so example tables don't leak into overrides.

    The Protocol shipped with Cortex *documents* override syntax inside
    inline-code spans like ``T1.4.line-threshold: 200`` and inside the
    "Trigger thresholds are project-configurable" paragraph. A naive scan
    would match those examples and silently flip thresholds. Stripping
    fenced code blocks is enough — inline backticks survive but the regex
    requires a line start (`^\\s*`) so prose mentions like ``the
    `T1.4.line-threshold: 200` override`` don't match.
    """
    out_lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _apply_override(config: EvalConfig, key: str, value: str) -> None:
    if key.lower() == "t1.4.line-threshold":
        try:
            parsed = int(value)
        except ValueError:
            config.warnings.append(
                f"invalid T1.4.line-threshold value {value!r}; using default {DEFAULT_DELETION_LINE_THRESHOLD}"
            )
            return
        if parsed < 0:
            config.warnings.append(
                f"T1.4.line-threshold must be non-negative; got {parsed}; using default {DEFAULT_DELETION_LINE_THRESHOLD}"
            )
            return
        config.deletion_line_threshold = parsed
        return
    # Recognized-but-not-yet-wired overrides — never honor silently.
    config.warnings.append(
        f"override {key} parsed but not honored by `cortex check-triggers` in this release; using defaults"
    )


# ---------------------------------------------------------------------------
# Diff loading
# ---------------------------------------------------------------------------


def _git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def resolve_ref(project_root: Path, ref: str) -> str | None:
    """Return the canonical SHA for ``ref``, or None if it doesn't exist."""
    result = _git(project_root, "rev-parse", "--verify", "--quiet", ref)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.strip()


def load_changed_files(project_root: Path, ref_range: str, *, staged: bool) -> list[str]:
    """Return the set of paths changed in the diff (any change type)."""
    cmd = ["diff", "--cached", "--name-only"] if staged else ["diff", "--name-only", ref_range]
    result = _git(project_root, *cmd)
    if result.returncode != 0:
        raise _GitError(result.stderr.strip() or "git diff failed", result.returncode)
    return [line for line in result.stdout.splitlines() if line.strip()]


def load_deletions(project_root: Path, ref_range: str, *, staged: bool) -> list[DeletionRow]:
    """Return per-file deleted-line counts.

    ``git diff --numstat`` emits ``<added>\\t<deleted>\\t<path>``; binary files
    use ``-`` for both columns and are skipped because line-threshold logic
    doesn't apply.
    """
    cmd = ["diff", "--cached", "--numstat"] if staged else ["diff", "--numstat", ref_range]
    result = _git(project_root, *cmd)
    if result.returncode != 0:
        raise _GitError(result.stderr.strip() or "git diff --numstat failed", result.returncode)
    rows: list[DeletionRow] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        try:
            deleted = int(parts[1])
        except ValueError:
            continue
        rows.append(DeletionRow(path=parts[2], deleted_lines=deleted))
    return rows


def load_commits_in_range(project_root: Path, ref_range: str) -> list[DiffCommit]:
    """Return commits in ``base..head`` order (oldest first).

    Used for T1.8 evaluation; not consulted in ``--staged`` mode (a staged
    diff has no commits to scan yet).
    """
    result = _git(
        project_root,
        "log",
        ref_range,
        "--reverse",
        "--pretty=format:%H%x09%s",
    )
    if result.returncode != 0:
        raise _GitError(result.stderr.strip() or "git log failed", result.returncode)
    commits: list[DiffCommit] = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        commits.append(DiffCommit(sha=sha, subject=subject))
    return commits


class _GitError(RuntimeError):
    def __init__(self, message: str, returncode: int) -> None:
        super().__init__(message)
        self.returncode = returncode


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------


def _matches_t1_1(path: str, config: EvalConfig) -> bool:
    return path.startswith(config.t1_1_path_prefixes) or path in config.t1_1_exact_paths


def _matches_t1_5(path: str, config: EvalConfig) -> bool:
    return Path(path).name in config.dep_manifests


def evaluate(
    *,
    config: EvalConfig,
    changed_files: Sequence[str],
    deletions: Sequence[DeletionRow],
    commits: Sequence[DiffCommit],
    ref_label: str,
) -> list[TriggerHit]:
    """Run every in-scope trigger against the loaded diff context.

    Returns hits in canonical order (T1.1 → T1.4 → T1.5 → T1.8) so NDJSON
    output is stable across runs of the same diff — important for stop-hook
    callers that diff the output to suppress duplicate prompts.
    """
    hits: list[TriggerHit] = []

    # T1.1 — sensitive paths in the diff.
    t1_1_files = sorted({p for p in changed_files if _matches_t1_1(p, config)})
    if t1_1_files:
        hits.append(
            TriggerHit(
                trigger="T1.1",
                reason=(
                    "diff touches `.cortex/doctrine/`, `.cortex/plans/`, "
                    "`principles/`, or `SPEC.md`"
                ),
                template=TEMPLATE_FOR_TRIGGER[Trigger.T1_1],
                ref=ref_label,
                files=tuple(t1_1_files),
            )
        )

    # T1.4 — per-file deletion threshold. One hit per file so the consumer
    # can write one Journal entry per deletion event.
    threshold = config.deletion_line_threshold
    for row in deletions:
        if row.deleted_lines <= threshold:
            continue
        hits.append(
            TriggerHit(
                trigger="T1.4",
                reason=(
                    f"file deletion exceeds {threshold} lines "
                    f"(deleted {row.deleted_lines} from {row.path})"
                ),
                template=T1_4_TEMPLATE,
                ref=ref_label,
                files=(row.path,),
                lines_deleted=row.deleted_lines,
            )
        )

    # T1.5 — dependency manifest changed.
    t1_5_files = sorted({p for p in changed_files if _matches_t1_5(p, config)})
    if t1_5_files:
        hits.append(
            TriggerHit(
                trigger="T1.5",
                reason="dependency manifest changed",
                template=TEMPLATE_FOR_TRIGGER[Trigger.T1_5],
                ref=ref_label,
                files=tuple(t1_5_files),
            )
        )

    # T1.8 — commit subject regex over the ref range.
    for commit in commits:
        match = config.t1_8_re.match(commit.subject)
        if not match:
            continue
        hits.append(
            TriggerHit(
                trigger="T1.8",
                reason=f"commit subject matches `{config.t1_8_re.pattern}`",
                template=TEMPLATE_FOR_TRIGGER[Trigger.T1_8],
                ref=ref_label,
                commit=commit.sha[:8],
                subject=commit.subject,
            )
        )

    return hits


# ---------------------------------------------------------------------------
# Top-level entrypoint used by the click command
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    hits: list[TriggerHit]
    warnings: list[str]


def check_triggers(
    project_root: Path,
    *,
    since: str | None,
    staged: bool,
) -> EvalResult:
    """Evaluate Tier 1 triggers for the given diff scope.

    Caller must enforce the ``since``/``staged`` mutual-exclusion before
    calling — this function trusts its inputs and assumes exactly one mode.

    Raises:
        FileNotFoundError: when ``.cortex/`` is absent.
        _GitError: when a git invocation fails (bad ref, etc.).
    """
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.exists():
        raise FileNotFoundError(f"{cortex_dir} does not exist; run `cortex init` first.")

    config = load_protocol_overrides(project_root)
    warnings = list(config.warnings)

    if staged:
        ref_label = "staged"
        commits: list[DiffCommit] = []  # No commits to scan in staged mode.
        changed_files = load_changed_files(project_root, "", staged=True)
        deletions = load_deletions(project_root, "", staged=True)
    else:
        assert since is not None  # caller enforces
        # Resolve both ends of the range so a typo in ``--since`` errors out
        # cleanly instead of producing an empty diff that looks like "no
        # triggers fired" (silent failure).
        if resolve_ref(project_root, since) is None:
            raise _GitError(f"unknown ref: {since!r}", 128)
        ref_range = f"{since}..HEAD"
        ref_label = ref_range
        commits = load_commits_in_range(project_root, ref_range)
        changed_files = load_changed_files(project_root, ref_range, staged=False)
        deletions = load_deletions(project_root, ref_range, staged=False)

    hits = evaluate(
        config=config,
        changed_files=changed_files,
        deletions=deletions,
        commits=commits,
        ref_label=ref_label,
    )
    return EvalResult(hits=hits, warnings=warnings)


def emit_warnings(warnings: Iterable[str], stream: IO[str] | None = None) -> None:
    """Write warnings to stderr — never silent. Public for the click layer."""
    target = sys.stderr if stream is None else stream
    for warning in warnings:
        print(f"warning: {warning}", file=target)
