"""Source-PR journal staging helpers for T1.9 (`pr-merged`).

Pre-merge staging writes the journal entry on the source branch so the squash
carries both the change and its memory record. Post-merge automation becomes a
verifier when ``[journal.t1_9].mode = "stage"`` in ``.cortex/config.toml``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cortex.audit import _PR_NUMBER_IN_TITLE_RE, _journal_header_fields
from cortex.journal_markers import UNRESOLVED_MARKER_PATTERNS

STAGED_FOR_PR_FIELD = "Staged-for-pr"


@dataclass(frozen=True)
class StagingVerification:
    ok: bool
    path: Path | None
    messages: tuple[str, ...]


def find_pr_merged_entry(project_root: Path, pr_number: int) -> Path | None:
    """Return a journal entry whose title records ``PR #<n>``."""

    journal_dir = project_root / ".cortex" / "journal"
    if not journal_dir.is_dir():
        return None
    for path in sorted(journal_dir.glob("*.md")):
        try:
            text = path.read_text()
        except OSError:
            continue
        type_, _trigger, _tag, _merge, entry_pr = _journal_header_fields(path)
        if type_ != "pr-merged":
            continue
        if entry_pr == pr_number:
            return path
        for line in text.splitlines()[:8]:
            if line.startswith("# "):
                match = _PR_NUMBER_IN_TITLE_RE.search(line)
                if match and int(match.group(1)) == pr_number:
                    return path
                break
    return None


def entry_has_unresolved_markers(body: str) -> list[str]:
    """Return human-readable labels for template pollution still present."""

    found: list[str] = []
    for label, pattern in UNRESOLVED_MARKER_PATTERNS:
        if pattern.search(body):
            found.append(label)
    return found


def verify_pr_merged_staged(project_root: Path, pr_number: int) -> StagingVerification:
    """Check that a clean ``pr-merged`` entry exists for this PR on the branch."""

    path = find_pr_merged_entry(project_root, pr_number)
    if path is None:
        return StagingVerification(
            ok=False,
            path=None,
            messages=(
                f"no staged pr-merged journal entry for PR #{pr_number}; "
                f"run `cortex journal stage pr-merged --pr {pr_number}`",
            ),
        )
    try:
        body = path.read_text()
    except OSError as exc:
        return StagingVerification(
            ok=False,
            path=path,
            messages=(f"could not read {path}: {exc}",),
        )
    markers = entry_has_unresolved_markers(body)
    if markers:
        return StagingVerification(
            ok=False,
            path=path,
            messages=(
                f"{path} still contains unresolved template markers: "
                + "; ".join(markers),
            ),
        )
    return StagingVerification(ok=True, path=path, messages=())


def annotate_staged_for_pr(body: str, pr_number: int) -> str:
    """Insert ``**Staged-for-pr:**`` after the title block when absent."""

    if re.search(rf"^\*\*{STAGED_FOR_PR_FIELD}:\*\*\s*{pr_number}\s*$", body, re.MULTILINE):
        return body
    if re.search(rf"^\*\*{STAGED_FOR_PR_FIELD}:\*\*", body, re.MULTILINE):
        return body
    lines = body.splitlines()
    insert_at = 1
    for idx, line in enumerate(lines[1:8], start=1):
        if line.startswith("**") and line.endswith("**") and ":" in line:
            insert_at = idx + 1
        elif line.strip() == "":
            break
    lines.insert(insert_at, f"**{STAGED_FOR_PR_FIELD}:** {pr_number}")
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")
