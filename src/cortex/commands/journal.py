"""`cortex journal draft <type>` — scaffold a Journal entry from a template.

Templates resolve in this order:
1. ``<project>/.cortex/templates/journal/<type>.md`` (project override)
2. Bundled ``src/cortex/_data/templates/journal/<type>.md`` (shipped with the CLI)

Pre-fills today's date in the ``**Date:**`` field, optionally rewrites the
H1 from ``--title``, and appends an HTML-comment block with recent
``git log`` and ``gh pr view`` context to inform the user as they fill in
the body. Default flow opens ``$EDITOR`` on a temp file and moves the result
to ``.cortex/journal/<filename>`` on editor close; ``--no-edit`` writes
directly and prints the path.

For ``--type pr-merged`` (T1.9), the command also resolves the merged PR
number from ``--pr N`` (when given) or by parsing HEAD's most recent merge
commit subject for ``(#NNN)`` (the squash-merge convention) and substitutes
the template's ``{{ nnn }}``, ``{{ short title }}``, ``{{ full sha }}``,
``{{ <type>/<slug> }}`` and ``{{ <date>-<slug> }}`` placeholders from
``gh pr view`` plus ``git log`` context. Placeholders the resolved context
cannot fill stay as-is so the user knows what to fill on ``--edit``.

For ``--type release`` (T1.10), the command resolves the git tag from
``--tag VTAG`` (when given) or by reading the most recent semver tag
matching ``^v\\d+\\.\\d+\\.\\d+$`` from ``git tag --list``. It then pulls
``gh release view <tag>`` for the release name / URL, and (in ``--no-edit``
mode) seeds the ``## What shipped`` bullets from ``git log <prev_tag>..<tag>``
PR-shaped subjects. Mirrors pr-merged: missing context warns on stderr and
leaves the placeholder intact rather than failing or silently wedging.

``gh`` is optional. If it's not installed, not authenticated, or there is
no open PR for the current branch, the PR-context block degrades to a note;
the command never blocks on missing optional tooling.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import click

from cortex.compat import require_compatible
from cortex.config import load_journal_t19_config, load_refresh_index_config
from cortex.index import refresh_index
from cortex.journal_facts import FactsFileError, load_and_validate_facts_file, render_facts_draft
from cortex.journal_markers import UNRESOLVED_MARKER_PATTERNS
from cortex.journal_staging import (
    annotate_staged_for_pr,
    entry_has_unresolved_markers,
    find_pr_merged_entry,
    verify_pr_merged_staged,
)
from cortex.manifest import estimate_tokens, estimate_words

_DATE_PLACEHOLDER = "{{ YYYY-MM-DD }}"
_H1_TEMPLATE_RE = re.compile(r"^# \{\{[^}]+\}\}.*$", re.MULTILINE)
_SLUG_MAX_CHARS = 50
# Squash-merge convention: subject ends with `(#NNN)` from `gh pr merge --squash`.
_PR_NUMBER_IN_SUBJECT_RE = re.compile(r"\(#(\d{1,6})\)\s*$")
# Window for inferring "the most recent merge" — keeps stale-on-old-clone safer
# than scanning history forever, but wide enough that a hook firing minutes
# after a squash always finds the source commit.
_PR_INFER_LOOKBACK_DAYS = 14
# Window for finding a recent journal slug to populate `{{ <date>-<slug> }}`.
_JOURNAL_SLUG_LOOKBACK_DAYS = 7
DEFAULT_JOURNAL_DRAFT_WARNING_TOKENS = 1200
# Default release-tag detection (Protocol § 2 / T1.10): semver tags only.
# Projects using calendar versioning override per-project; we keep the CLI
# default in sync with the Protocol's default.
_RELEASE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
# Valid type names are lowercase identifiers with optional dashes — same
# shape as the bundled template stems (decision, pr-merged, release, ...).
# Restricting here closes a path-traversal hole: ``journal_type`` flows into
# both the template-resolution path and (via the fallback slug) the journal
# filename, so any ``..`` / ``/`` / leading-dash input would resolve outside
# ``.cortex/templates/journal/`` or write outside ``.cortex/journal/``.
_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# Journal types whose `--no-edit` path runs a body scrubber that promises a
# placeholder-free entry (pr-merged via the post-merge hook, release via the
# release event). Only these types are guarded by
# `_assert_no_unresolved_markers`: other types (decision, incident,
# plan-transition) have no `--no-edit` scrubber and would legitimately fail the
# guard until a scrubber is written for them — see issue #275 follow-up note.
_NO_EDIT_SCRUBBED_TYPES = frozenset({"pr-merged", "release"})
# Unresolved-marker classes the `--no-edit` guard rejects. Each entry is
# (human-readable label, compiled regex). The invariant these enforce: a
# pr-merged/release entry written in `--no-edit` mode contains zero unresolved
# template markers — no mustache prompts, no HTML edit-instruction comments, no
# "fill on edit" sentinels, no template checklist placeholder lines.
def _normalize_slug(text: str) -> str:
    """Lowercase, strip non-[a-z0-9 -], collapse spaces to dashes, cap at 50 chars."""
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    s = re.sub(r"-+", "-", s)
    return s[:_SLUG_MAX_CHARS] or "untitled"


def _resolve_template(cortex_dir: Path, journal_type: str) -> str:
    """Return template text for ``journal_type``; raises FileNotFoundError if absent.

    Project override under ``.cortex/templates/journal/`` wins over the
    bundled fallback so customizations don't get masked.
    """
    project_template = cortex_dir / "templates" / "journal" / f"{journal_type}.md"
    if project_template.exists():
        return project_template.read_text()
    bundle_resource = files("cortex._data").joinpath("templates", "journal", f"{journal_type}.md")
    if bundle_resource.is_file():
        return bundle_resource.read_text()
    raise FileNotFoundError(journal_type)


def _list_known_types() -> list[str]:
    """Return bundled journal-template type names (filename stems)."""
    bundle_dir = files("cortex._data").joinpath("templates", "journal")
    if not bundle_dir.is_dir():
        return []
    return sorted(
        p.name.removesuffix(".md")
        for p in bundle_dir.iterdir()
        if p.name.endswith(".md")
    )


def _gather_git_context(project_root: Path) -> list[str]:
    """Return up to 5 recent commit ``%h %s`` lines, or empty on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "log", "-5", "--pretty=format:%h %s"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _gather_gh_pr_context(project_root: Path) -> tuple[str | None, str | None]:
    """Return ``(pr_text, degradation_reason)``.

    On success ``pr_text`` is non-None and ``degradation_reason`` is None.
    On any non-success path ``pr_text`` is None and ``degradation_reason``
    explains why so the user sees something specific in the context block.
    """
    if shutil.which("gh") is None:
        return None, "gh not installed"
    auth = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    if auth.returncode != 0:
        return None, "gh not authenticated (run `gh auth login`)"
    pr = subprocess.run(
        ["gh", "pr", "view", "--json", "number,title,url"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if pr.returncode != 0 or not pr.stdout.strip():
        return None, "no open PR for this branch"
    return pr.stdout.strip(), None


def _infer_recent_pr_number(project_root: Path) -> int | None:
    """Return the most recent merged PR number from HEAD's commit subject.

    The Touchstone post-merge hook fires after ``gh pr merge --squash``,
    which produces a commit subject like ``feat: foo (#NNN)``. We scan the
    last ~14 days of subjects and return the first ``(#NNN)`` we see — that
    is, the merge that triggered the hook. Returns ``None`` when no commit
    in the window matches; callers degrade by leaving placeholders intact.
    """
    since_iso = (datetime.now() - timedelta(days=_PR_INFER_LOOKBACK_DAYS)).isoformat()
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "log",
                f"--since={since_iso}",
                "--pretty=format:%s",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = _PR_NUMBER_IN_SUBJECT_RE.search(line)
        if match:
            try:
                return int(match.group(1))
            except ValueError:  # pragma: no cover — regex enforces digits
                continue
    return None


def _gh_pr_view_json(project_root: Path, pr_number: int) -> dict[str, str] | None:
    """Return a dict of PR fields, or None on any failure.

    Keys returned: ``number`` (str), ``title``, ``headRefName``,
    ``body``, ``mergeCommit.oid`` flattened to ``mergeCommitSha``. Failures (gh
    missing, not authenticated, PR not found, parse error) all return
    ``None`` so callers degrade by leaving placeholders intact rather than
    crashing.
    """
    if shutil.which("gh") is None:
        return None
    auth = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    if auth.returncode != 0:
        return None
    pr = subprocess.run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "number,title,body,headRefName,mergeCommit",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if pr.returncode != 0 or not pr.stdout.strip():
        return None
    try:
        data = json.loads(pr.stdout)
    except json.JSONDecodeError:
        return None
    out: dict[str, str] = {}
    if isinstance(data.get("number"), int):
        out["number"] = str(data["number"])
    if isinstance(data.get("title"), str):
        out["title"] = data["title"]
    if isinstance(data.get("body"), str):
        out["body"] = data["body"]
    if isinstance(data.get("headRefName"), str):
        out["headRefName"] = data["headRefName"]
    merge_commit = data.get("mergeCommit")
    if isinstance(merge_commit, dict) and isinstance(merge_commit.get("oid"), str):
        out["mergeCommitSha"] = merge_commit["oid"]
    return out


def _head_sha(project_root: Path) -> str | None:
    """Return ``git rev-parse HEAD`` or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _recent_journal_slug(project_root: Path) -> str | None:
    """Return ``<date>-<slug>`` of a journal entry written in the last 7 days.

    Used to populate ``{{ <date>-<slug> }}`` in the pr-merged template. The
    pr-merged note typically cites the most recent in-branch journal
    activity; an exact match isn't required (the human edits this on
    ``--edit``), so we just return the most recent entry's stem. Returns
    None when no recent entry exists; the placeholder stays intact.
    """
    journal_dir = project_root / ".cortex" / "journal"
    if not journal_dir.is_dir():
        return None
    cutoff = date.today() - timedelta(days=_JOURNAL_SLUG_LOOKBACK_DAYS)
    candidates: list[tuple[date, str]] = []
    for entry in journal_dir.glob("*.md"):
        match = re.match(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$", entry.name)
        if not match:
            continue
        try:
            entry_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if entry_date < cutoff:
            continue
        candidates.append((entry_date, entry.name[:-3]))  # strip `.md`
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _recent_plan_slug(project_root: Path) -> str | None:
    """Return the stem of the most-recently-modified active plan in `.cortex/plans/`.

    Used to populate ``{{ <slug> }}`` in the release template's ``**Cites:**``
    line. Most release entries cite the plan that drove the work; selecting
    the most-recently-modified plan is a useful default — humans correct on
    ``--edit``. Returns ``None`` when no plan files exist; the placeholder
    stays intact.
    """
    plans_dir = project_root / ".cortex" / "plans"
    if not plans_dir.is_dir():
        return None
    candidates = sorted(
        plans_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None
    return candidates[0].stem


def _latest_release_tag(project_root: Path) -> str | None:
    """Return the most recent semver-shaped tag, or None on failure.

    Uses ``git tag --list --sort=-version:refname`` and filters for
    ``^v\\d+\\.\\d+\\.\\d+$``. Returns ``None`` when git is unavailable or no
    matching tag exists; callers degrade by leaving placeholders intact.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "tag",
                "--list",
                "--sort=-version:refname",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        tag = line.strip()
        if _RELEASE_TAG_RE.match(tag):
            return tag
    return None


def _previous_release_tag(project_root: Path, tag: str) -> str | None:
    """Return the semver tag immediately preceding ``tag``, or None.

    Best-effort: scans ``git tag --list --sort=-version:refname`` for
    semver-shaped tags and returns the first one that is not ``tag``. If
    the only tag is ``tag`` (first release), returns None — callers fall
    back to "all commits reachable from ``tag``" or warn.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "tag",
                "--list",
                "--sort=-version:refname",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    seen_target = False
    for line in result.stdout.splitlines():
        candidate = line.strip()
        if not _RELEASE_TAG_RE.match(candidate):
            continue
        if not seen_target:
            if candidate == tag:
                seen_target = True
            continue
        return candidate
    return None


def _gh_release_view_json(project_root: Path, tag: str) -> dict[str, str] | None:
    """Return a dict of GitHub Release fields, or None on any failure.

    Keys returned (when present): ``tagName``, ``name``, ``body``,
    ``publishedAt``, ``url``, ``isPrerelease``. Failures (gh missing, not
    authenticated, release not found, parse error) all return ``None`` so
    callers degrade by leaving placeholders intact.
    """
    if shutil.which("gh") is None:
        return None
    auth = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    if auth.returncode != 0:
        return None
    rel = subprocess.run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--json",
            "tagName,name,body,publishedAt,targetCommitish,isPrerelease,url",
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if rel.returncode != 0 or not rel.stdout.strip():
        return None
    try:
        data = json.loads(rel.stdout)
    except json.JSONDecodeError:
        return None
    out: dict[str, str] = {}
    for key in ("tagName", "name", "body", "publishedAt", "url", "targetCommitish"):
        value = data.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    pre = data.get("isPrerelease")
    if isinstance(pre, bool):
        out["isPrerelease"] = "true" if pre else "false"
    return out


def _pr_subjects_since_tag(
    project_root: Path, prev_tag: str | None, tag: str
) -> list[tuple[int, str]]:
    """Return a list of ``(pr_number, subject_without_(#N))`` for PRs landed
    between ``prev_tag`` (exclusive) and ``tag`` (inclusive).

    Squash-merge convention: subjects look like ``feat: foo (#123)``. We
    parse the trailing ``(#NNN)`` and use it as the PR number, returning the
    subject sans the suffix as the human-readable bullet. Empty list on any
    git failure or when no PR-shaped subjects are found; callers degrade by
    leaving placeholders intact.
    """
    revrange = f"{prev_tag}..{tag}" if prev_tag else tag
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "log",
                revrange,
                "--pretty=format:%s",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for line in result.stdout.splitlines():
        match = _PR_NUMBER_IN_SUBJECT_RE.search(line)
        if not match:
            continue
        try:
            number = int(match.group(1))
        except ValueError:  # pragma: no cover — regex enforces digits
            continue
        if number in seen:
            continue
        seen.add(number)
        # Strip the trailing ` (#NNN)` and any whitespace so the subject is
        # ready to drop into a bullet.
        subject = _PR_NUMBER_IN_SUBJECT_RE.sub("", line).rstrip()
        out.append((number, subject))
    return out


def _present_downstream_doc_files(project_root: Path) -> list[str]:
    """Return the subset of conventional downstream-doc paths that exist.

    The Downstream-docs section of the release template is the seed for
    the v0.5.0 ``--audit-instructions`` check, which walks listed paths and
    flags stale references. Listing only files that actually exist keeps
    the seed honest; the human can add tap-repo / external paths on edit.
    """
    candidates = ("CLAUDE.md", "AGENTS.md", "README.md", "docs/PITCH.md")
    return [name for name in candidates if (project_root / name).exists()]


def _substitute_release_placeholders(
    body: str,
    *,
    tag: str | None,
    release_name: str | None,
    release_published_date: str | None,
    release_url: str | None,
    plan_slug: str | None,
    journal_slug: str | None,
) -> tuple[str, list[str]]:
    """Replace release template placeholders from resolved context.

    Mirrors :func:`_substitute_pr_merged_placeholders`: each placeholder is
    replaced only when its source value is non-None; missing sources leave
    the original ``{{ ... }}`` token in place so the user knows what to
    fill on ``--edit``. Returns ``(rewritten_body, unfilled_labels)`` so the
    caller can emit a stderr ``warning:`` (engineering principle: no silent
    failures).
    """
    unfilled: list[str] = []

    if tag is not None:
        # `{{ git tag, e.g. v0.3.0 }}` appears twice (header + Artifact block).
        body = body.replace("{{ git tag, e.g. v0.3.0 }}", tag)
        version = tag.removeprefix("v") if tag.startswith("v") else tag
        body = body.replace("{{ vX.Y.Z }}", version)
        title_value = (
            f"Release {tag} — {release_name}" if release_name else f"Release {tag}"
        )
        body = body.replace("{{ Release vX.Y.Z — short title }}", title_value)
    else:
        unfilled.append("git tag")

    if release_url is not None:
        body = body.replace(
            "{{ link to GitHub Release page or release-notes section }}",
            release_url,
        )
    else:
        unfilled.append("release URL")

    if plan_slug is not None:
        body = body.replace("{{ <slug> }}", plan_slug)
    else:
        unfilled.append("plan slug")

    if journal_slug is not None:
        body = body.replace("{{ <date>-<slug> }}", journal_slug)
    else:
        unfilled.append("recent journal slug")

    # The Date placeholder has already been substituted with `today` upstream;
    # if a release publishedAt date is available and differs, we leave today
    # as-is since `today` is what the writer actually saw. The template's
    # `Date:` field is "when this entry was authored," not "when the artifact
    # shipped" — `Tag:` answers the latter.
    _ = release_published_date  # reserved for future use; not currently consumed

    return body, unfilled


def _substitute_release_body_no_edit(
    body: str,
    *,
    tag: str | None,
    release_name: str | None,
    pr_subjects: list[tuple[int, str]],
    downstream_docs: list[str],
) -> str:
    """Remove prompt-only release body placeholders for no-edit drafts.

    Edit-mode keeps template prompts visible for humans. In no-edit mode the
    invariant is stricter: an auto-committed Journal entry must not contain
    unresolved ``{{ ... }}`` prompts, and must not claim a deferred checkbox
    exists when no SPEC § 4.2 target has been resolved.
    """
    # The lede callout: rewrite or strip.
    if tag:
        lede = f"> Release {tag} shipped."
        if release_name:
            lede = f"> {release_name} ({tag})."
        body = re.sub(
            r"^> \{\{ One sentence:.*\}\}$",
            lede,
            body,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        body = re.sub(
            r"^> \{\{ One sentence:.*\}\}\n?",
            "",
            body,
            count=1,
            flags=re.MULTILINE,
        )

    # Artifact block: Kind / Location seeded best-effort. We only commit
    # values we can derive; otherwise we use a "fill on edit" sentinel so
    # the no-edit invariant (no `{{ }}`) holds.
    body = re.sub(
        r"\{\{ Homebrew tap \| PyPI release \| Docker image \| GitHub Release \| git tag \| other \}\}",
        "GitHub Release",
        body,
        count=1,
    )
    body = re.sub(
        r"\{\{ e\.g\. `autumngarage/cortex` tap formula.*?\}\}",
        "_Not recorded (tap formula / PyPI / Docker / etc.)._",
        body,
        count=1,
        flags=re.DOTALL,
    )

    # `## What shipped` — replace the bullet placeholder with PR subjects.
    if pr_subjects:
        bullets = [f"- {subject} (#{number})" for number, subject in pr_subjects]
    else:
        summary = release_name or (f"Release {tag}" if tag else "Release")
        bullets = [f"- {summary}"]
    body = re.sub(
        r"\{\{ Bulleted list of user-visible changes in this release\..*?\}\}",
        "\n".join(bullets),
        body,
        count=1,
        flags=re.DOTALL,
    )

    # `## Downstream docs this changes` — replace the example bullets with
    # the present-files set. We replace from the first `- {{ CLAUDE.md ...`
    # bullet through the trailing `- {{ ... }}` placeholder.
    if downstream_docs:
        downstream_block = "\n".join(f"- `{name}`" for name in downstream_docs)
    else:
        downstream_block = "_(none in this repo)_"
    body = re.sub(
        r"- \{\{ CLAUDE\.md.*?\}\}\n- \{\{ \.\.\. \}\}",
        downstream_block,
        body,
        count=1,
        flags=re.DOTALL,
    )

    # `## Follow-ups` placeholder checkbox: SPEC § 4.2 says deferred items
    # must resolve to another layer in the same commit. An auto-drafted
    # release with no resolved target leaves the placeholder as a stale
    # ``[ ]``; replace it with `_None._` (matches pr-merged behavior).
    body = re.sub(
        r"^- \[ \] \{\{ item .*?\}\}$",
        "_None._",
        body,
        count=1,
        flags=re.MULTILINE,
    )

    # `**Cites:**` line: strip any unresolved `{{ ... }}` segments so the
    # `--no-edit` invariant (no surviving `{{` tokens) holds. Mirrors
    # pr-merged's _rewrite_cites behavior.
    def _rewrite_cites(match: re.Match[str]) -> str:
        resolved = [
            part.strip()
            for part in match.group(1).split(",")
            if "{{" not in part and part.strip()
        ]
        if not resolved:
            return "**Cites:** _None recorded._"
        return f"**Cites:** {', '.join(resolved)}"

    return re.sub(
        r"^\*\*Cites:\*\* (.+)$", _rewrite_cites, body, count=1, flags=re.MULTILINE
    )


def _substitute_pr_merged_placeholders(
    body: str,
    *,
    pr_number: int | None,
    pr_title: str | None,
    head_sha: str | None,
    branch: str | None,
    journal_slug: str | None,
) -> tuple[str, list[str]]:
    """Replace pr-merged template placeholders from resolved context.

    Returns ``(rewritten_body, unsubstituted_placeholder_labels)``. Each
    placeholder is replaced only when its source value is non-None; missing
    sources leave the original ``{{ ... }}`` token in place so the user
    knows what to fill on ``--edit``. The returned label list is what the
    caller emits as a ``warning:`` so missing context is never silent
    (engineering principle: no silent failures).
    """
    substitutions: list[tuple[str, str | None, str]] = [
        # (placeholder, value, label)
        ("{{ nnn }}", str(pr_number) if pr_number is not None else None, "PR number"),
        ("{{ short title }}", pr_title, "PR title"),
        ("{{ full sha }}", head_sha, "HEAD sha"),
        ("{{ <type>/<slug> }}", branch, "branch name"),
        ("{{ <date>-<slug> }}", journal_slug, "recent journal slug"),
    ]
    unfilled: list[str] = []
    for placeholder, value, label in substitutions:
        if value is None:
            unfilled.append(label)
            continue
        body = body.replace(placeholder, value)
    return body, unfilled


def _pr_body_bullets(pr_body: str | None) -> list[str]:
    """Extract top-level Markdown bullet lines from a PR body."""
    if not pr_body:
        return []
    bullets: list[str] = []
    for line in pr_body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            bullets.append(f"- {stripped[2:].strip()}")
    return [bullet for bullet in bullets if bullet != "-"]


def _substitute_pr_merged_body_no_edit(
    body: str,
    *,
    pr_number: int | None,
    pr_title: str | None,
    pr_body: str | None,
) -> str:
    """Remove prompt-only pr-merged body placeholders for no-edit drafts.

    Edit-mode keeps template prompts visible for humans. In no-edit mode the
    invariant is stricter: an auto-committed Journal entry must not contain
    unresolved ``{{ ... }}`` prompts, and must not claim a deferred checkbox
    exists when no SPEC § 4.2 target has been resolved.
    """
    if pr_title:
        body = re.sub(
            r"^> \{\{ One sentence:.*\}\}$",
            f"> {pr_title}.",
            body,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        body = re.sub(
            r"^> \{\{ One sentence:.*\}\}\n?",
            "",
            body,
            count=1,
            flags=re.MULTILINE,
        )

    bullets = _pr_body_bullets(pr_body)
    if not bullets:
        summary = pr_title or "Merged PR"
        suffix = f" (#{pr_number})" if pr_number is not None else ""
        bullets = [f"- {summary}{suffix}"]
    body = re.sub(
        r"\{\{ Bulleted list of the user-visible or protocol-visible changes in this PR\..*?\}\}",
        "\n".join(bullets),
        body,
        count=1,
        flags=re.DOTALL,
    )

    body = re.sub(
        r"^(- \*\*(?:Plans|Doctrine|Journal linkage):\*\*) \{\{.*\}\}$",
        r"\1 _None recorded._",
        body,
        flags=re.MULTILINE,
    )
    body = re.sub(
        r"_?\(none recorded — fill on edit\)_?",
        "_None recorded._",
        body,
        flags=re.IGNORECASE,
    )

    def _rewrite_cites(match: re.Match[str]) -> str:
        resolved = [
            part.strip()
            for part in match.group(1).split(",")
            if "{{" not in part and part.strip()
        ]
        if not resolved:
            return "**Cites:** _None recorded._"
        return f"**Cites:** {', '.join(resolved)}"

    body = re.sub(r"^\*\*Cites:\*\* (.+)$", _rewrite_cites, body, count=1, flags=re.MULTILINE)

    body = re.sub(
        r"^- \[ \] \{\{ item .*?\}\}$",
        "_None._",
        body,
        count=1,
        flags=re.MULTILINE,
    )

    return re.sub(
        r"\n## What we'd do differently\n\n\{\{ Optional .*?Omit if nothing\. \}\}\n?",
        "\n",
        body,
        count=1,
        flags=re.DOTALL,
    )


def _assert_no_unresolved_markers(
    body: str, *, journal_type: str, target: Path
) -> None:
    """Fail loudly if a ``--no-edit`` draft still carries template markers.

    Defense-in-depth backstop for the scrubbing in
    :func:`_substitute_pr_merged_body_no_edit` /
    :func:`_substitute_release_body_no_edit`. The scrubbers are the root-cause
    fix (produce clean output); this guard guarantees the invariant holds even
    when a project ships a *custom* template the scrubbers don't fully cover —
    the post-merge hook runs against whatever template the project ships, not
    just the bundled one (issue #275).

    On any leftover marker the command exits non-zero **before** the entry is
    written or its path printed, naming the file and the offending markers
    (engineering principle: no silent failures). The append-only Journal
    invariant is preserved because the write never happens.
    """
    found: list[str] = []
    for label, pattern in UNRESOLVED_MARKER_PATTERNS:
        matches = pattern.findall(body)
        if matches:
            sample = matches[0]
            sample = sample if isinstance(sample, str) else sample[0]
            sample = " ".join(sample.split())
            if len(sample) > 80:
                sample = sample[:77] + "..."
            found.append(f"{label} (e.g. {sample!r})")
    if not found:
        return
    click.echo(
        f"error: {target} would contain unresolved template markers after "
        f"`{journal_type}` --no-edit generation; refusing to write a polluted "
        f"Journal entry. Offending markers: " + "; ".join(found) + ". "
        "This usually means a project-custom template has prompt content the "
        "no-edit scrubber does not recognize; resolve those values in the "
        "template or run without --no-edit to fill them by hand.",
        err=True,
    )
    sys.exit(2)


def _render_context_block(
    commits: list[str], pr_text: str | None, gh_reason: str | None
) -> str:
    """Render the auto-context HTML comment appended to the draft body."""
    lines = [
        "",
        "<!--",
        "Context auto-pulled at draft time. Remove this block before saving.",
        "",
    ]
    if commits:
        lines.append("Recent commits:")
        lines.extend(f"- {c}" for c in commits)
    else:
        lines.append("Recent commits: (git log unavailable)")
    lines.append("")
    if pr_text:
        lines.append("Active-branch PR:")
        lines.append(pr_text)
    else:
        lines.append(f"Active-branch PR: ({gh_reason or 'unavailable'})")
    lines.append("-->")
    lines.append("")
    return "\n".join(lines)


def _journal_budget_warning(body: str, *, limit_tokens: int) -> str | None:
    used_tokens = estimate_tokens(body)
    if used_tokens <= limit_tokens:
        return None
    used_words = estimate_words(body)
    return (
        "warning: journal draft is "
        f"~{used_tokens} tokens / ~{used_words} words; target is "
        f"<={limit_tokens} tokens for reviewable agent handoffs. "
        "Tighten the entry, split it into cited follow-ups, or pass "
        "`--allow-large` to acknowledge the oversized draft."
    )


@dataclass(frozen=True)
class _DraftWriteOutcome:
    path: Path
    body: str


def _write_journal_entry(*, target: Path, body: str, project_root: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    body = body.rstrip() + "\n"
    try:
        # Exclusive-create closes the TOCTOU race between the early
        # existence check and this write — Journal is append-only
        # (SPEC § 3.5 / Protocol § 4.1), so silently overwriting an
        # entry that appeared in the meantime is a spec violation.
        with target.open("x") as f:
            f.write(body)
    except FileExistsError:
        click.echo(
            f"error: {target} appeared between the existence check and "
            f"the write (race or duplicate run); not overwriting "
            f"(Journal is append-only).",
            err=True,
        )
        sys.exit(2)
    _refresh_index_after_write(project_root)


def _derive_output_slug(
    *,
    journal_type: str,
    title: str | None,
    slug_override: str | None,
    resolved_tag: str | None,
) -> str:
    if slug_override:
        return _normalize_slug(slug_override)
    if title:
        return _normalize_slug(title)
    if journal_type == "release" and resolved_tag is not None:
        # `<date>-release-<tag-without-v>.md` is the canonical release-entry
        # filename. Keeps tag-resolution unambiguous for the `--audit-instructions`
        # check (T1.10's `Tag:` scalar is the source of truth, but a tag-shaped
        # filename helps humans grep).
        return f"release-{resolved_tag.removeprefix('v') if resolved_tag.startswith('v') else resolved_tag}"
    # Use the type + HHMM so multiple drafts of the same type on the same day
    # don't collide before the user gives them real names.
    return f"{journal_type}-{datetime.now().strftime('%H%M')}"


def _facts_file_draft(
    *,
    project_root: Path,
    cortex_dir: Path,
    journal_type: str,
    template: str,
    facts_file: Path,
    allow_large: bool,
    slug_override: str | None,
    staged_for_pr: int | None = None,
) -> _DraftWriteOutcome:
    packet = load_and_validate_facts_file(facts_file, expected_type=journal_type)
    today = date.today().isoformat()
    body = render_facts_draft(template=template, packet=packet, today=today)
    if staged_for_pr is not None:
        body = annotate_staged_for_pr(body, staged_for_pr)

    # Keep filename behavior aligned with the existing draft flow:
    # without CLI `--title`, default slugging is type+HHMM (or release-tag
    # when type=release and a tag is present). Facts-file `title` affects the
    # H1/body, not filename selection.
    tag_value = packet.get("tag") if journal_type == "release" else None
    release_tag = tag_value if isinstance(tag_value, str) else None
    slug = _derive_output_slug(
        journal_type=journal_type,
        title=None,
        slug_override=slug_override,
        resolved_tag=release_tag,
    )
    filename = f"{today}-{slug}.md"
    target = cortex_dir / "journal" / filename

    if target.exists():
        click.echo(
            f"error: {target} already exists; pass --slug to differentiate "
            f"or remove the existing entry.",
            err=True,
        )
        sys.exit(2)

    if not allow_large:
        budget_warning = _journal_budget_warning(
            body,
            limit_tokens=DEFAULT_JOURNAL_DRAFT_WARNING_TOKENS,
        )
        if budget_warning is not None:
            click.echo(budget_warning, err=True)

    _write_journal_entry(target=target, body=body, project_root=project_root)
    return _DraftWriteOutcome(path=target, body=body)


@click.command("draft")
@click.argument("journal_type")
@click.option(
    "--title",
    "title",
    default=None,
    help="Override the H1 title; also seeds the slug if --slug is omitted.",
)
@click.option(
    "--slug",
    "slug_override",
    default=None,
    help="Override the filename slug; defaults to a normalization of --title.",
)
@click.option(
    "--no-edit",
    "no_edit",
    is_flag=True,
    default=False,
    help="Write directly to .cortex/journal/ without opening $EDITOR.",
)
@click.option(
    "--allow-large",
    "allow_large",
    is_flag=True,
    default=False,
    help=(
        "Suppress the journal draft size warning. Default target is "
        f"{DEFAULT_JOURNAL_DRAFT_WARNING_TOKENS} estimated tokens."
    ),
)
@click.option(
    "--pr",
    "pr_number",
    type=int,
    default=None,
    help=(
        "PR number for `--type pr-merged`. When omitted, inferred from the most recent "
        "merge commit subject (e.g. `feat: foo (#123)`). Requires `gh` to be installed "
        "and authenticated; degrades gracefully when `gh` is unavailable. "
        "Ignored for other types."
    ),
)
@click.option(
    "--tag",
    "release_tag",
    default=None,
    help=(
        "Git tag for `--type release`. When omitted, defaults to the most recent semver "
        "tag (`^v\\d+\\.\\d+\\.\\d+$`). Pulls release notes via `gh release view <tag>` "
        "when `gh` is available; degrades gracefully otherwise. Ignored for other types."
    ),
)
@click.option(
    "--plan-slug",
    "plan_slug",
    default=None,
    help=(
        "Plan stem for `--type release`'s `**Cites:** plans/<slug>` field. "
        "Defaults to the most-recently-modified file under `.cortex/plans/`. "
        "Ignored for other types."
    ),
)
@click.option(
    "--facts-file",
    "facts_file",
    type=click.Path(file_okay=True, dir_okay=False, exists=True, path_type=Path),
    default=None,
    help=(
        "Path to a compact JSON facts packet for deterministic draft rendering. "
        "Supported for `pr-merged`, `decision`, and `release`."
    ),
)
@click.option(
    "--staged-for-pr",
    "staged_for_pr",
    type=int,
    default=None,
    hidden=True,
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def draft_command(
    *,
    journal_type: str,
    title: str | None,
    slug_override: str | None,
    no_edit: bool,
    allow_large: bool,
    pr_number: int | None,
    release_tag: str | None,
    plan_slug: str | None,
    facts_file: Path | None,
    staged_for_pr: int | None,
    target_path: Path,
) -> None:
    """Create a new Journal entry of TYPE from a template and open it in $EDITOR.

    TYPE is the template name: decision, pr-merged, release, incident,
    plan-transition, or any custom type added under .cortex/templates/journal/.
    The entry is pre-filled with today's date and recent git/PR context.
    """
    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)
    if not _TYPE_RE.match(journal_type):
        click.echo(
            f"error: invalid journal type {journal_type!r}; types are "
            f"lowercase identifiers with optional dashes (e.g. `decision`, "
            f"`pr-merged`, `release`).",
            err=True,
        )
        sys.exit(2)
    # `journal draft` is a writer; SPEC § 7 says writers refuse on unknown
    # major versions rather than warning. require_compatible exits 2 on
    # missing or unsupported SPEC_VERSION.
    require_compatible(cortex_dir)

    try:
        template = _resolve_template(cortex_dir, journal_type)
    except FileNotFoundError:
        known = _list_known_types()
        listing = ", ".join(known) if known else "(no bundled templates found)"
        click.echo(
            f"error: no template for journal type {journal_type!r}.\n"
            f"  Available types: {listing}\n"
            f"  Add a project override at .cortex/templates/journal/{journal_type}.md "
            f"to define a new type.",
            err=True,
        )
        sys.exit(2)

    if facts_file is not None:
        # Facts packets own PR identity; ignore a forwarded --pr from parent
        # commands such as `cortex journal stage` (Click propagates matching
        # option names through ctx.invoke).
        pr_number = None
        disallowed_flags: list[str] = []
        if title is not None:
            disallowed_flags.append("--title")
        if release_tag is not None:
            disallowed_flags.append("--tag")
        if plan_slug is not None:
            disallowed_flags.append("--plan-slug")
        if disallowed_flags:
            click.echo(
                "error: --facts-file cannot be combined with "
                f"{', '.join(disallowed_flags)}; put these values in the facts packet.",
                err=True,
            )
            sys.exit(2)
        try:
            outcome = _facts_file_draft(
                project_root=project_root,
                cortex_dir=cortex_dir,
                journal_type=journal_type,
                template=template,
                facts_file=facts_file,
                allow_large=allow_large,
                slug_override=slug_override,
                staged_for_pr=staged_for_pr,
            )
        except FactsFileError as exc:
            click.echo(
                json.dumps(
                    exc.as_structured_error(facts_file=facts_file, journal_type=journal_type),
                    indent=2,
                ),
                err=True,
            )
            sys.exit(2)
        click.echo(str(outcome.path))
        return

    today = date.today().isoformat()
    body = template.replace(_DATE_PLACEHOLDER, today)
    if title is not None:
        body = _H1_TEMPLATE_RE.sub(f"# {title}", body, count=1)

    # Bug A (cortex#101): pr-merged drafts auto-fired by the Touchstone
    # post-merge hook landed as raw template noise — placeholders weren't
    # substituted. Resolve `--pr N` (or infer from HEAD's `(#NNN)` subject),
    # look up `gh pr view`, and substitute what we know.
    if journal_type == "pr-merged":
        resolved_pr = pr_number
        if resolved_pr is None:
            resolved_pr = _infer_recent_pr_number(project_root)
        pr_data: dict[str, str] | None = None
        if resolved_pr is not None:
            pr_data = _gh_pr_view_json(project_root, resolved_pr)
        pr_title = pr_data.get("title") if pr_data else None
        pr_body = pr_data.get("body") if pr_data else None
        branch = pr_data.get("headRefName") if pr_data else None
        body, unfilled = _substitute_pr_merged_placeholders(
            body,
            pr_number=resolved_pr,
            pr_title=pr_title,
            head_sha=_head_sha(project_root),
            branch=branch,
            journal_slug=_recent_journal_slug(project_root),
        )
        if resolved_pr is None:
            # No PR could be resolved at all — surface why so the user knows
            # the entry will need hand-editing (engineering principle: no
            # silent failures).
            click.echo(
                "warning: could not resolve a PR number for pr-merged draft "
                "(no `--pr N` and HEAD's recent commits had no `(#NNN)` "
                "merge subject); placeholders left intact.",
                err=True,
            )
        elif unfilled:
            click.echo(
                "warning: pr-merged draft left placeholders intact for: "
                f"{', '.join(unfilled)}.",
                err=True,
            )
        if no_edit and pr_data is not None:
            body = _substitute_pr_merged_body_no_edit(
                body,
                pr_number=resolved_pr,
                pr_title=pr_title,
                pr_body=pr_body,
            )
            remaining_placeholders = re.findall(r"\{\{[^}]+\}\}", body)
            if remaining_placeholders:
                click.echo(
                    "warning: pr-merged --no-edit draft left body "
                    "placeholders intact for: "
                    f"{', '.join(remaining_placeholders)}.",
                    err=True,
                )

    if staged_for_pr is not None:
        if journal_type != "pr-merged":
            click.echo(
                "error: staged Journal annotation is only supported for pr-merged",
                err=True,
            )
            sys.exit(2)
        body = annotate_staged_for_pr(body, staged_for_pr)

    # T1.10 (release): mirror the pr-merged path. Resolve the tag (explicit
    # `--tag` or latest matching `^v\d+\.\d+\.\d+$`), pull `gh release view`
    # metadata best-effort, seed `What shipped` from `git log <prev>..<tag>`
    # PR-shaped subjects.
    resolved_tag: str | None = None
    if journal_type == "release":
        resolved_tag = release_tag or _latest_release_tag(project_root)
        release_data: dict[str, str] | None = None
        if resolved_tag is not None:
            release_data = _gh_release_view_json(project_root, resolved_tag)
        release_name = release_data.get("name") if release_data else None
        release_url = release_data.get("url") if release_data else None
        release_published_date: str | None = None
        if release_data and "publishedAt" in release_data:
            # publishedAt is RFC3339; we only need the date prefix.
            published = release_data["publishedAt"]
            if len(published) >= 10:
                release_published_date = published[:10]
        resolved_plan_slug = plan_slug or _recent_plan_slug(project_root)
        body, unfilled = _substitute_release_placeholders(
            body,
            tag=resolved_tag,
            release_name=release_name,
            release_published_date=release_published_date,
            release_url=release_url,
            plan_slug=resolved_plan_slug,
            journal_slug=_recent_journal_slug(project_root),
        )
        if resolved_tag is None:
            # No tag could be resolved at all — surface why so the user knows
            # the entry will need hand-editing (no silent failures).
            click.echo(
                "warning: could not resolve a tag for release draft "
                "(no `--tag VTAG` and `git tag --list` produced no "
                f"`{_RELEASE_TAG_RE.pattern}`-shaped tags); placeholders "
                "left intact.",
                err=True,
            )
        elif release_data is None:
            click.echo(
                f"warning: release draft for {resolved_tag} found the tag "
                "but could not fetch GitHub Release metadata "
                "(`gh` missing/unauthenticated, or no release exists for "
                "the tag); placeholders that depend on `gh release view` "
                "left intact.",
                err=True,
            )
        if unfilled:
            click.echo(
                "warning: release draft left placeholders intact for: "
                f"{', '.join(unfilled)}.",
                err=True,
            )
        if no_edit:
            prev_tag = (
                _previous_release_tag(project_root, resolved_tag)
                if resolved_tag is not None
                else None
            )
            pr_subjects = (
                _pr_subjects_since_tag(project_root, prev_tag, resolved_tag)
                if resolved_tag is not None
                else []
            )
            body = _substitute_release_body_no_edit(
                body,
                tag=resolved_tag,
                release_name=release_name,
                pr_subjects=pr_subjects,
                downstream_docs=_present_downstream_doc_files(project_root),
            )
            remaining_placeholders = re.findall(r"\{\{[^}]+\}\}", body)
            if remaining_placeholders:
                click.echo(
                    "warning: release --no-edit draft left body "
                    "placeholders intact for: "
                    f"{', '.join(remaining_placeholders)}.",
                    err=True,
                )

    if slug_override:
        slug = _normalize_slug(slug_override)
    elif title:
        slug = _normalize_slug(title)
    elif journal_type == "release" and resolved_tag is not None:
        # `<date>-release-<tag-without-v>.md` is the canonical release-entry
        # filename. Keeps tag-resolution unambiguous for the `--audit-instructions`
        # check (T1.10's `Tag:` scalar is the source of truth, but a tag-shaped
        # filename helps humans grep).
        slug = f"release-{resolved_tag.removeprefix('v') if resolved_tag.startswith('v') else resolved_tag}"
    else:
        # Use the type + HHMM so multiple drafts of the same type on the
        # same day don't collide before the user gives them real names.
        slug = f"{journal_type}-{datetime.now().strftime('%H%M')}"

    filename = f"{today}-{slug}.md"
    target = cortex_dir / "journal" / filename
    if target.exists():
        click.echo(
            f"error: {target} already exists; pass --slug to differentiate "
            f"or remove the existing entry.",
            err=True,
        )
        sys.exit(2)

    # The auto-context block is an HTML comment whose own instructions say
    # "Remove this block before saving." In interactive (--edit) mode the
    # human removes it. In --no-edit mode there is no human in the loop — the
    # block would be committed verbatim, polluting the entry (and future
    # `cortex manifest` context) with an unresolved edit instruction. Only
    # append it when a human will see and prune it.
    if not no_edit:
        commits = _gather_git_context(project_root)
        pr_text, gh_reason = _gather_gh_pr_context(project_root)
        body += _render_context_block(commits, pr_text, gh_reason)
    if not allow_large:
        budget_warning = _journal_budget_warning(
            body,
            limit_tokens=DEFAULT_JOURNAL_DRAFT_WARNING_TOKENS,
        )
        if budget_warning is not None:
            click.echo(budget_warning, err=True)

    if no_edit:
        if journal_type in _NO_EDIT_SCRUBBED_TYPES:
            _assert_no_unresolved_markers(
                body, journal_type=journal_type, target=target
            )
        _write_journal_entry(target=target, body=body, project_root=project_root)
        click.echo(str(target))
        return

    editor_env = os.environ.get("EDITOR")
    if editor_env:
        editor_argv = shlex.split(editor_env)
    else:
        fallback = shutil.which("vi") or shutil.which("nano")
        if fallback is None:
            click.echo(
                "error: $EDITOR is unset and neither `vi` nor `nano` is on "
                "PATH. Set $EDITOR or pass --no-edit.",
                err=True,
            )
            sys.exit(2)
        editor_argv = [fallback]

    fd, tmp_path = tempfile.mkstemp(suffix=".md", prefix=f"cortex-{journal_type}-")
    tmp = Path(tmp_path)
    preserve_tmp = True
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        try:
            result = subprocess.run([*editor_argv, str(tmp)], check=False)
        except FileNotFoundError as exc:
            click.echo(
                f"error: editor command {editor_argv!r} not executable "
                f"({exc}); draft preserved at {tmp}.",
                err=True,
            )
            sys.exit(2)
        if result.returncode != 0:
            click.echo(
                f"error: editor exited with code {result.returncode}; "
                f"draft preserved at {tmp}.",
                err=True,
            )
            sys.exit(2)
        edited = tmp.read_text()
        if not allow_large:
            budget_warning = _journal_budget_warning(
                edited,
                limit_tokens=DEFAULT_JOURNAL_DRAFT_WARNING_TOKENS,
            )
            if budget_warning is not None:
                click.echo(budget_warning, err=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("x") as f:
                f.write(edited)
        except FileExistsError:
            # Same race-window concern as the --no-edit path. The user spent
            # time editing, so leave the temp file in place with a pointer.
            click.echo(
                f"error: {target} appeared while editing (race or duplicate "
                f"run); edited draft preserved at {tmp}.",
                err=True,
            )
            sys.exit(2)
        _refresh_index_after_write(project_root)
        click.echo(str(target))
        preserve_tmp = False
    finally:
        # Only delete the temp file on the clean success path. Every error
        # exit above leaves preserve_tmp=True so the user can recover their
        # work — the prior version's blanket unlink contradicted the
        # "draft preserved at <tmp>" promise on every error path.
        if not preserve_tmp and tmp.exists():
            tmp.unlink()


def _refresh_index_after_write(project_root: Path) -> None:
    """Best-effort inline index refresh; silent on success."""

    config = load_refresh_index_config(project_root)
    try:
        result = refresh_index(project_root, config)
    except Exception as exc:
        click.echo(f"warning: could not refresh .cortex/.index.json: {exc}", err=True)
        return
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)
    _refresh_retrieve_index_if_present(project_root)


def _refresh_retrieve_index_if_present(project_root: Path) -> None:
    try:
        from cortex.retrieve.index import rebuild_index, retrieve_index_exists

        if not retrieve_index_exists(project_root):
            return
        rebuild_index(project_root)
    except Exception as exc:
        click.echo(f"warning: could not refresh .cortex/.index/chunks.sqlite: {exc}", err=True)


@click.command("stage")
@click.option(
    "--type",
    "journal_type",
    required=True,
    help="Journal template type. Currently only `pr-merged` is supported.",
)
@click.option(
    "--pr",
    "pr_number",
    type=int,
    required=True,
    help="PR number for `--type pr-merged`.",
)
@click.option(
    "--facts-file",
    "facts_file",
    type=click.Path(file_okay=True, dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="Optional JSON facts packet for deterministic draft rendering.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
@click.pass_context
def stage_command(
    ctx: click.Context,
    *,
    journal_type: str,
    pr_number: int,
    facts_file: Path | None,
    target_path: Path,
) -> None:
    """Stage a Journal entry on the source branch before merge.

    For ``pr-merged``, writes a ``--no-edit`` draft and annotates it with
    ``**Staged-for-pr:**`` so post-merge automation can verify instead of
    rewrite.
    """
    if journal_type != "pr-merged":
        click.echo("error: stage currently supports only --type pr-merged", err=True)
        raise SystemExit(2)

    project_root = Path(target_path).resolve()
    if facts_file is not None:
        try:
            packet = load_and_validate_facts_file(facts_file, expected_type="pr-merged")
        except FactsFileError as exc:
            click.echo(
                json.dumps(
                    exc.as_structured_error(facts_file=facts_file, journal_type="pr-merged"),
                    sort_keys=True,
                ),
                err=True,
            )
            raise SystemExit(2) from exc
        facts_pr = packet.get("pr_number")
        if facts_pr != pr_number:
            click.echo(
                "error: --pr "
                f"{pr_number} does not match facts file pr_number {facts_pr!r}; "
                "refusing to write a partial Journal entry.",
                err=True,
            )
            raise SystemExit(2)

    existing = find_pr_merged_entry(project_root, pr_number)
    if existing is not None:
        try:
            body = existing.read_text()
        except OSError as exc:
            click.echo(f"error: could not read {existing}: {exc}", err=True)
            raise SystemExit(1) from exc
        markers = entry_has_unresolved_markers(body)
        if markers:
            click.echo(
                f"error: {existing} still contains unresolved template markers: "
                + "; ".join(markers),
                err=True,
            )
            raise SystemExit(1)
        click.echo(str(existing.resolve()))
        return

    ctx.invoke(
        draft_command,
        journal_type="pr-merged",
        title=None,
        slug_override=None,
        no_edit=True,
        allow_large=False,
        pr_number=None if facts_file is not None else pr_number,
        release_tag=None,
        plan_slug=None,
        facts_file=facts_file,
        staged_for_pr=pr_number,
        target_path=target_path,
    )


@click.command("verify")
@click.option(
    "--type",
    "journal_type",
    required=True,
    help="Journal template type. Currently only `pr-merged` is supported.",
)
@click.option(
    "--pr",
    "pr_number",
    type=int,
    required=True,
    help="PR number for `--type pr-merged`.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def verify_command(
    *,
    journal_type: str,
    pr_number: int,
    target_path: Path,
) -> None:
    """Verify a staged Journal entry exists and is free of template pollution."""
    if journal_type != "pr-merged":
        click.echo("error: verify currently supports only --type pr-merged", err=True)
        raise SystemExit(2)

    result = verify_pr_merged_staged(Path(target_path).resolve(), pr_number)
    if not result.ok:
        for message in result.messages:
            click.echo(f"error: {message}", err=True)
        raise SystemExit(1)
    assert result.path is not None
    click.echo(str(result.path.resolve()))


@click.command("post-merge")
@click.option(
    "--type",
    "journal_type",
    default="pr-merged",
    show_default=True,
    help="Journal template type handled by post-merge automation.",
)
@click.option(
    "--pr",
    "pr_number",
    type=int,
    default=None,
    help="PR number. Required when `[journal.t1_9].mode = \"stage\"`.",
)
@click.option(
    "--no-edit",
    "no_edit",
    is_flag=True,
    default=False,
    help="Write directly in post-merge-writer mode without opening $EDITOR.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
@click.pass_context
def post_merge_command(
    ctx: click.Context,
    *,
    journal_type: str,
    pr_number: int | None,
    no_edit: bool,
    target_path: Path,
) -> None:
    """Post-merge T1.9 handler used by ``cortex-pr-merged-hook.sh``.

    In ``stage`` mode, verifies a staged entry exists. In ``post-merge-writer``
    mode, delegates to ``cortex journal draft``.
    """
    project_root = Path(target_path).resolve()
    if journal_type != "pr-merged":
        click.echo("error: post-merge currently supports only --type pr-merged", err=True)
        raise SystemExit(2)

    config = load_journal_t19_config(project_root)
    for warning in config.warnings:
        click.echo(f"warning: {warning}", err=True)

    if config.mode == "stage":
        if pr_number is None:
            click.echo(
                "error: --pr is required when journal.t1_9 mode is stage",
                err=True,
            )
            raise SystemExit(2)
        result = verify_pr_merged_staged(project_root, pr_number)
        if not result.ok:
            for message in result.messages:
                click.echo(f"error: {message}", err=True)
            raise SystemExit(1)
        assert result.path is not None
        click.echo(str(result.path.resolve()))
        return

    ctx.invoke(
        draft_command,
        journal_type="pr-merged",
        title=None,
        slug_override=None,
        no_edit=no_edit,
        allow_large=False,
        pr_number=pr_number,
        release_tag=None,
        plan_slug=None,
        facts_file=None,
        target_path=project_root,
    )


@click.group("facts")
def facts_group() -> None:
    """Validate journal facts packets without writing files."""


@click.command("validate")
@click.argument("journal_type")
@click.option(
    "--facts-file",
    "facts_file",
    type=click.Path(file_okay=True, dir_okay=False, exists=True, path_type=Path),
    required=True,
    help="Path to the JSON facts packet to validate.",
)
def facts_validate_command(*, journal_type: str, facts_file: Path) -> None:
    """Validate a facts packet against the journal draft handoff schema."""
    try:
        load_and_validate_facts_file(facts_file, expected_type=journal_type)
    except FactsFileError as exc:
        click.echo(
            json.dumps(
                exc.as_structured_error(facts_file=facts_file, journal_type=journal_type),
                sort_keys=True,
            )
        )
        raise SystemExit(2) from exc
    click.echo("ok")


facts_group.add_command(facts_validate_command)


@click.group("journal")
def journal_group() -> None:
    """Create and manage Journal entries.

    Use ``cortex journal draft <type>`` to scaffold an entry from a template.
    Available types: decision, pr-merged, release, incident, plan-transition.
    """


journal_group.add_command(draft_command)
journal_group.add_command(stage_command)
journal_group.add_command(verify_command)
journal_group.add_command(post_merge_command)
journal_group.add_command(facts_group)
