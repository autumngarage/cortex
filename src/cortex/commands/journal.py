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
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path

import click

from cortex.compat import require_compatible
from cortex.config import load_refresh_index_config
from cortex.index import refresh_index

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
# Valid type names are lowercase identifiers with optional dashes — same
# shape as the bundled template stems (decision, pr-merged, release, ...).
# Restricting here closes a path-traversal hole: ``journal_type`` flows into
# both the template-resolution path and (via the fallback slug) the journal
# filename, so any ``..`` / ``/`` / leading-dash input would resolve outside
# ``.cortex/templates/journal/`` or write outside ``.cortex/journal/``.
_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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
        r"\1 _(none recorded — fill on edit)_",
        body,
        flags=re.MULTILINE,
    )

    def _rewrite_cites(match: re.Match[str]) -> str:
        resolved = [
            part.strip()
            for part in match.group(1).split(",")
            if "{{" not in part and part.strip()
        ]
        if not resolved:
            return "**Cites:** _(none — fill on edit)_"
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
    pr_number: int | None,
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

    if slug_override:
        slug = _normalize_slug(slug_override)
    elif title:
        slug = _normalize_slug(title)
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

    commits = _gather_git_context(project_root)
    pr_text, gh_reason = _gather_gh_pr_context(project_root)
    body += _render_context_block(commits, pr_text, gh_reason)

    if no_edit:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Exclusive-create closes the TOCTOU race between the early
            # existence check above and this write — Journal is append-only
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


@click.group("journal")
def journal_group() -> None:
    """Create and manage Journal entries.

    Use ``cortex journal draft <type>`` to scaffold an entry from a template.
    Available types: decision, pr-merged, release, incident, plan-transition.
    """


journal_group.add_command(draft_command)
