"""simlab generator: archetype spec → repo with deterministic git history (#520).

Materialization is a pure function of the spec: ``git init`` plus one
``git commit`` per :class:`~tests.simlab.specs.SpecCommit`, with author *and*
committer identity/date pinned from spec literals and host git config
isolated away, so the resulting commit shas are byte-identical on any
machine. After the last commit, every working-tree file's mtime is set to
the ``authored_at`` of the last commit that touched it — derive's
``occurred_at`` for walked repo files is the file mtime, so this is the step
that makes "same spec twice → identical derive ``event_hash`` set" hold.

Derive runs through the shipped pipeline, never a parallel one:
``cortex.commands.derive.run_derive`` with the production
``RepoNativeExtractor``, the same default source walk the CLI uses, the same
``gather_commit_message_documents`` over the materialized git history, and
the PR gatherer document builders fed from the spec's committed ``gh``-shaped
fixture JSON (the documented seam those builders expose instead of shelling
out to ``gh``).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cortex.commands.derive import DeriveRunResult, resolve_source_files, run_derive
from cortex.hosted.extractors import (
    DroppedSourceChatter,
    RepoNativeExtractor,
    gather_commit_message_documents,
    pr_description_documents,
    pr_review_comment_documents,
)
from cortex.hosted.provenance import SourceDocument
from tests.simlab.specs import ArchetypeSpec, SimlabSpecError

# Minimal `.cortex/` marker file: the materialized repos are Cortex-enabled
# projects (the CLI verbs require `.cortex/` to exist), but this file is not
# part of derive's default source walk, so it never feeds candidates.
CORTEX_MARKER_PATH = ".cortex/state.md"
CORTEX_MARKER_CONTENT = "# Project State\n\nSynthetic simlab project.\n"


class SimlabGitError(SimlabSpecError):
    """Raised when a git step of materialization fails (named, never silent)."""


@dataclass(frozen=True)
class MaterializedRepo:
    """One materialized archetype: where it lives and the sha it landed on."""

    spec: ArchetypeSpec
    root: Path
    head_sha: str


def _git_env(*, author_name: str, author_email: str, authored_at: str) -> dict[str, str]:
    """A hermetic git environment: spec identity only, host config ignored."""

    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_AUTHOR_DATE": authored_at,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
        "GIT_COMMITTER_DATE": authored_at,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def _run_git(args: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise SimlabGitError(f"cannot run git {' '.join(args)}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise SimlabGitError(
            f"git {' '.join(args)} failed (exit {completed.returncode}): {detail}"
        )
    return completed.stdout


def materialize_archetype(spec: ArchetypeSpec, dest: Path) -> MaterializedRepo:
    """Materialize one archetype spec into ``dest`` (created, must be empty).

    Returns the repo with its deterministic HEAD sha. Materializing the same
    spec into two different directories yields the same HEAD sha and the
    same per-file mtimes — the determinism the #520 acceptance bar pins.
    """

    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise SimlabSpecError(
            f"materialization target {dest} is not empty; refusing to mix "
            "spec-driven content with pre-existing files"
        )

    first = spec.commits[0]
    init_env = _git_env(
        author_name=first.author_name,
        author_email=first.author_email,
        authored_at=first.authored_at,
    )
    _run_git(["init", "--quiet", "--initial-branch", "main"], cwd=dest, env=init_env)

    last_touch: dict[str, str] = {}
    for index, commit in enumerate(spec.commits):
        files = dict(commit.files)
        if index == 0 and CORTEX_MARKER_PATH not in files:
            files[CORTEX_MARKER_PATH] = CORTEX_MARKER_CONTENT
        for rel, content in files.items():
            path = dest / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            last_touch[rel] = commit.authored_at
        for rel in commit.deleted:
            path = dest / rel
            if not path.is_file():
                raise SimlabSpecError(
                    f"commit {commit.message.splitlines()[0]!r} deletes {rel!r}, "
                    "which does not exist at that point in history"
                )
            path.unlink()
            last_touch.pop(rel, None)
        env = _git_env(
            author_name=commit.author_name,
            author_email=commit.author_email,
            authored_at=commit.authored_at,
        )
        _run_git(["add", "--all"], cwd=dest, env=env)
        _run_git(["commit", "--quiet", "-m", commit.message], cwd=dest, env=env)

    for rel, authored_at in last_touch.items():
        timestamp = datetime.fromisoformat(authored_at).timestamp()
        os.utime(dest / rel, (timestamp, timestamp))

    head_sha = _run_git(["rev-parse", "HEAD"], cwd=dest, env=init_env).strip()
    return MaterializedRepo(spec=spec, root=dest, head_sha=head_sha)


# ---------------------------------------------------------------------------
# Derive over a materialized repo — the shipped pipeline, no parallel path
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimlabDeriveOutcome:
    """One derive run over a materialized repo, with the drop accounting."""

    result: DeriveRunResult
    dropped: tuple[DroppedSourceChatter, ...]

    @property
    def event_hashes(self) -> frozenset[str]:
        return frozenset(event.event_hash for event in self.result.events)

    @property
    def candidate_count(self) -> int:
        return len(self.result.events)

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)


def gather_spec_pr_documents(
    spec: ArchetypeSpec, *, tenant_id: str, source_id: str
) -> tuple[tuple[SourceDocument, ...], tuple[DroppedSourceChatter, ...]]:
    """Build PR documents from the spec's committed ``gh``-shaped fixtures.

    Same builders the live ``gather_pr_documents`` path uses
    (``pr_description_documents`` / ``pr_review_comment_documents``), fed
    from spec JSON instead of ``gh`` — the documented payload seam, so the
    fail-closed shape validation is identical to production.
    """

    documents: list[SourceDocument] = []
    dropped: list[DroppedSourceChatter] = []
    for fixture in sorted(spec.pr_fixtures, key=lambda item: item.pr_number):
        description = pr_description_documents(
            fixture.view, tenant_id=tenant_id, source_id=source_id
        )
        documents.extend(description.documents)
        dropped.extend(description.dropped)
        comments = pr_review_comment_documents(
            list(fixture.comments),
            pr_number=fixture.pr_number,
            tenant_id=tenant_id,
            source_id=source_id,
        )
        documents.extend(comments.documents)
        dropped.extend(comments.dropped)
    return tuple(documents), tuple(dropped)


def derive_materialized(
    repo: MaterializedRepo,
    *,
    tenant_id: str | None = None,
    source_id: str | None = None,
) -> SimlabDeriveOutcome:
    """Run the production derive pipeline over a materialized repo.

    Identity defaults to the spec's fixed tenant/source pair (path-derived
    identity would break cross-machine determinism — tmp dirs differ).
    Writes the local replay-export store at the repo's default
    ``.cortex/.index/derive-events.sqlite`` path, exactly like the CLI.
    """

    spec = repo.spec
    tenant = tenant_id if tenant_id is not None else spec.tenant_id
    source = source_id if source_id is not None else spec.source_id

    source_files = resolve_source_files(repo.root, ())
    gathered = gather_commit_message_documents(
        repo.root, tenant_id=tenant, source_id=source, limit=spec.commit_gather_limit
    )
    documents = list(gathered.documents)
    gathered_dropped = list(gathered.dropped)
    pr_documents, pr_dropped = gather_spec_pr_documents(
        spec, tenant_id=tenant, source_id=source
    )
    documents.extend(pr_documents)
    gathered_dropped.extend(pr_dropped)

    extractor = RepoNativeExtractor()
    result = run_derive(
        project_root=repo.root,
        source_files=source_files,
        tenant_id=tenant,
        source_id=source,
        extractor=extractor,
        documents=tuple(documents),
    )
    return SimlabDeriveOutcome(
        result=result, dropped=(*gathered_dropped, *extractor.dropped)
    )
