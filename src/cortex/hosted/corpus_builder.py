"""Freeze real repository history into eval-corpus fixtures (cortex#339).

This module is the tooling half of the Stage 0 evaluation corpus. It turns a
merged PR (via the ``gh`` CLI) or an arbitrary commit range (via ``git``) into
a frozen :class:`~cortex.hosted.eval_fixtures.FixtureDiff`, attaches
caller-supplied decision context whose provenance spans are computed with
SourceDocument-style offset math over real document content, and assembles the
result into a structurally valid :class:`~cortex.hosted.eval_fixtures.EvalFixture`.

The committed corpus lives at ``tests/fixtures/hosted_eval/corpus/`` and the
assembly inputs that built it live in
``tests/fixtures/hosted_eval/corpus_assembly.py``.

**Corpus fixtures are written ungraded.** Every fixture this module assembles
has an empty ``labels`` list; hand-grading is a human activity that happens
through the ``labeling.py`` workflow (cortex#333) — model output is never
treated as ground truth. The graded corpus then feeds cortex#450's local
derive/evaluator replay over Cortex PR history and the cortex#378 hand-grade
quality bar.

**Determinism contract.** The same PR (or commit range) plus the same decision
inputs produce a byte-identical fixture: diffs for merged PRs and reachable
commit ranges are immutable, metadata is restricted to immutable facts
(``mergedAt``, SHAs, scoped paths), and serialization goes through
``EvalFixture.to_canonical_json``. Builders therefore never stamp wall-clock
time or environment-dependent values into fixtures.

Note: ``gh pr view --json files`` reports at most the first 100 changed files
of a PR; the corpus only freezes small PRs, and ``fetch_merged_pr_diff`` fails
closed when the file list comes back empty.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex.hosted.eval_fixtures import (
    EvalFixture,
    ExpectedFinding,
    FixtureDecision,
    FixtureDiff,
    FixtureSourceSpan,
)

REAL_HISTORY_SOURCE = "real-history"
SYNTHETIC_SOURCE = "synthetic"
SIMLAB_SOURCE = "simlab"
CORPUS_SOURCES = frozenset({REAL_HISTORY_SOURCE, SYNTHETIC_SOURCE, SIMLAB_SOURCE})

_COMMAND_TIMEOUT_SECONDS = 120.0

CommandRunner = Callable[[Sequence[str]], str]


class CorpusBuilderError(ValueError):
    """Raised when history cannot be frozen into a replayable corpus fixture."""


def run_command(argv: Sequence[str]) -> str:
    """Run a CLI command and return stdout; fail closed on any error."""

    if not argv:
        raise CorpusBuilderError("command argv must not be empty")
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CorpusBuilderError(f"command not found: {argv[0]!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CorpusBuilderError(
            f"command timed out after {_COMMAND_TIMEOUT_SECONDS}s: {' '.join(argv)}"
        ) from exc
    if completed.returncode != 0:
        raise CorpusBuilderError(
            f"command exited {completed.returncode}: {' '.join(argv)}: "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def document_content_hash(content: str) -> str:
    """Hash a cited document's full content, mirroring the fixture span scheme."""

    if not content:
        raise CorpusBuilderError("document content must not be empty")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_document_span(
    *, document_content: str, excerpt: str, permalink: str
) -> FixtureSourceSpan:
    """Locate ``excerpt`` in real document content and freeze it as a span.

    This is the SourceDocument-style offset math from hosted ``provenance.py``:
    offsets index into the immutable document content, the excerpt must match
    the offsets exactly, and the span hash is recomputable from the document
    hash plus offsets. The excerpt must occur exactly once so the citation is
    unambiguous; extend the excerpt if it is repeated.
    """

    if not document_content:
        raise CorpusBuilderError("document content must not be empty")
    if not excerpt:
        raise CorpusBuilderError("excerpt must not be empty")
    occurrences = document_content.count(excerpt)
    if occurrences == 0:
        raise CorpusBuilderError(
            f"excerpt not found in document content: {excerpt[:80]!r}"
        )
    if occurrences > 1:
        raise CorpusBuilderError(
            f"excerpt occurs {occurrences} times in document content; extend it "
            f"until the citation is unambiguous: {excerpt[:80]!r}"
        )
    start_offset = document_content.index(excerpt)
    return FixtureSourceSpan(
        source_document_hash=document_content_hash(document_content),
        start_offset=start_offset,
        end_offset=start_offset + len(excerpt),
        excerpt=excerpt,
        permalink=permalink,
    )


@dataclass(frozen=True)
class MergedPrDiff:
    """A merged PR's diff frozen alongside its immutable merge timestamp."""

    diff: FixtureDiff
    merged_at: str


def fetch_merged_pr_diff(
    repo: str, pr_number: int, *, runner: CommandRunner = run_command
) -> MergedPrDiff:
    """Freeze a merged PR's diff metadata via the ``gh`` CLI."""

    repo_owner, repo_name = _split_repo(repo)
    if pr_number <= 0:
        raise CorpusBuilderError(f"pr_number must be positive; got {pr_number}")
    view_output = runner(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "baseRefOid,headRefOid,files,mergedAt,state",
        ]
    )
    payload = _json_object(view_output, context=f"gh pr view {pr_number}")
    state = _get_str(payload, "state")
    if state != "MERGED":
        raise CorpusBuilderError(
            f"PR {repo}#{pr_number} is {state!r}, not MERGED; the corpus only "
            "freezes merged history"
        )
    merged_at = _get_str(payload, "mergedAt")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise CorpusBuilderError(
            f"PR {repo}#{pr_number} reported no changed files; refusing to freeze "
            "an empty diff"
        )
    changed_paths: list[str] = []
    for item in files:
        if not isinstance(item, Mapping):
            raise CorpusBuilderError("gh pr view files entries must be JSON objects")
        changed_paths.append(_get_str(item, "path"))
    patch = runner(["gh", "pr", "diff", str(pr_number), "--repo", repo])
    if not patch.strip():
        raise CorpusBuilderError(f"gh pr diff {pr_number} returned an empty patch")
    diff = FixtureDiff(
        repo_owner=repo_owner,
        repo_name=repo_name,
        base_sha=_get_str(payload, "baseRefOid"),
        head_sha=_get_str(payload, "headRefOid"),
        patch=patch,
        changed_paths=tuple(sorted(set(changed_paths))),
    )
    return MergedPrDiff(diff=diff, merged_at=merged_at)


def fetch_commit_range_diff(
    repo: str,
    base_sha: str,
    head_sha: str,
    *,
    paths: Sequence[str] = (),
    runner: CommandRunner = run_command,
) -> FixtureDiff:
    """Freeze a real commit-range diff via ``git``, optionally scoped to paths.

    This covers history that was never a PR's final diff — intermediate branch
    commits and stale-base review artifacts — while keeping every byte real.
    When ``paths`` is given, the patch is scoped to those paths and the scoping
    must be disclosed in fixture metadata by the calling builder.
    """

    repo_owner, repo_name = _split_repo(repo)
    for name, value in (("base_sha", base_sha), ("head_sha", head_sha)):
        if not value.strip():
            raise CorpusBuilderError(f"{name} must not be empty")
    scope = list(paths)
    if any(not path.strip() for path in scope):
        raise CorpusBuilderError("paths entries must not be empty")
    patch = runner(["git", "diff", base_sha, head_sha, "--", *scope])
    if not patch.strip():
        raise CorpusBuilderError(
            f"git diff {base_sha}..{head_sha} produced an empty patch for "
            f"paths {scope or ['<all>']}; refusing to freeze an empty diff"
        )
    name_only = runner(["git", "diff", "--name-only", base_sha, head_sha, "--", *scope])
    changed_paths = tuple(sorted({line for line in name_only.splitlines() if line.strip()}))
    if not changed_paths:
        raise CorpusBuilderError(
            f"git diff --name-only {base_sha}..{head_sha} reported no changed paths"
        )
    return FixtureDiff(
        repo_owner=repo_owner,
        repo_name=repo_name,
        base_sha=base_sha,
        head_sha=head_sha,
        patch=patch,
        changed_paths=changed_paths,
    )


def build_fixture_from_pr(
    repo: str,
    pr_number: int,
    *,
    fixture_id: str,
    decisions: Sequence[FixtureDecision],
    expected_findings: Sequence[ExpectedFinding] = (),
    extra_metadata: Mapping[str, Any] | None = None,
    runner: CommandRunner = run_command,
) -> EvalFixture:
    """Assemble an ungraded fixture from a merged PR's real diff."""

    frozen = fetch_merged_pr_diff(repo, pr_number, runner=runner)
    metadata = _merge_metadata(
        {
            "source": REAL_HISTORY_SOURCE,
            "repo": repo,
            "pr_number": pr_number,
            "merged_at": frozen.merged_at,
        },
        extra_metadata,
    )
    return _assemble_fixture(
        fixture_id=fixture_id,
        diff=frozen.diff,
        decisions=decisions,
        expected_findings=expected_findings,
        metadata=metadata,
    )


def build_fixture_from_commit_range(
    repo: str,
    base_sha: str,
    head_sha: str,
    *,
    fixture_id: str,
    decisions: Sequence[FixtureDecision],
    expected_findings: Sequence[ExpectedFinding] = (),
    paths: Sequence[str] = (),
    pr_number: int | None = None,
    extra_metadata: Mapping[str, Any] | None = None,
    runner: CommandRunner = run_command,
) -> EvalFixture:
    """Assemble an ungraded fixture from a real commit-range diff.

    ``pr_number`` records which PR's history the range belongs to (for
    intermediate-commit and stale-base artifacts); ``paths`` scoping is
    disclosed in metadata as ``patch_paths`` so a reader knows the patch is a
    real but path-scoped slice of the range.
    """

    diff = fetch_commit_range_diff(repo, base_sha, head_sha, paths=paths, runner=runner)
    stamped: dict[str, Any] = {"source": REAL_HISTORY_SOURCE, "repo": repo}
    if pr_number is not None:
        if pr_number <= 0:
            raise CorpusBuilderError(f"pr_number must be positive; got {pr_number}")
        stamped["pr_number"] = pr_number
    if paths:
        stamped["patch_paths"] = sorted(paths)
    metadata = _merge_metadata(stamped, extra_metadata)
    return _assemble_fixture(
        fixture_id=fixture_id,
        diff=diff,
        decisions=decisions,
        expected_findings=expected_findings,
        metadata=metadata,
    )


def build_synthetic_fixture(
    *,
    fixture_id: str,
    diff: FixtureDiff,
    decisions: Sequence[FixtureDecision],
    expected_findings: Sequence[ExpectedFinding] = (),
    extra_metadata: Mapping[str, Any] | None = None,
) -> EvalFixture:
    """Assemble the clearly-marked synthetic fixture (``metadata.source``).

    The diff is hand-written rather than frozen from history; the decision
    context must still cite real repository documents with real offsets.
    """

    metadata = _merge_metadata({"source": SYNTHETIC_SOURCE}, extra_metadata)
    return _assemble_fixture(
        fixture_id=fixture_id,
        diff=diff,
        decisions=decisions,
        expected_findings=expected_findings,
        metadata=metadata,
    )


def write_fixture(fixture: EvalFixture, directory: Path) -> Path:
    """Write a fixture to ``<directory>/<fixture_id>.json`` in canonical bytes."""

    if not directory.is_dir():
        raise CorpusBuilderError(f"corpus directory does not exist: {directory}")
    path = directory / f"{fixture.fixture_id}.json"
    path.write_text(fixture.to_canonical_json(), encoding="utf-8")
    return path


def load_corpus(directory: Path) -> tuple[EvalFixture, ...]:
    """Load and validate every fixture in a corpus directory.

    Fail-closed invariants: at least one fixture exists, each file round-trips
    byte-identically through canonical JSON, each filename matches its
    ``fixture_id``, fixture ids are unique, and ``metadata.source`` is a known
    corpus source class (``real-history`` for frozen PR / commit-range diffs,
    ``synthetic`` for the clearly-marked hand-written diff, ``simlab`` for
    fixtures promoted from the deterministic simlab scenario packs). Grading
    state is intentionally not checked here — the same loader serves the
    corpus before and after the cortex#333 hand-labeling pass.
    """

    if not directory.is_dir():
        raise CorpusBuilderError(f"corpus directory does not exist: {directory}")
    fixture_paths = sorted(directory.glob("*.json"))
    if not fixture_paths:
        raise CorpusBuilderError(f"corpus directory has no fixtures: {directory}")
    fixtures: list[EvalFixture] = []
    seen_ids: set[str] = set()
    for path in fixture_paths:
        text = path.read_text(encoding="utf-8")
        fixture = EvalFixture.from_json(text)
        if fixture.to_canonical_json() != text:
            raise CorpusBuilderError(
                f"fixture {path.name} is not canonical JSON; rewrite it with "
                "write_fixture so the corpus stays byte-reproducible"
            )
        if path.stem != fixture.fixture_id:
            raise CorpusBuilderError(
                f"fixture filename {path.name!r} does not match fixture_id "
                f"{fixture.fixture_id!r}"
            )
        if fixture.fixture_id in seen_ids:
            raise CorpusBuilderError(f"duplicate fixture_id: {fixture.fixture_id!r}")
        seen_ids.add(fixture.fixture_id)
        source = fixture.metadata.get("source")
        if source not in CORPUS_SOURCES:
            raise CorpusBuilderError(
                f"fixture {fixture.fixture_id!r} has metadata.source {source!r}; "
                f"corpus fixtures must declare one of {sorted(CORPUS_SOURCES)}"
            )
        fixtures.append(fixture)
    return tuple(fixtures)


def _assemble_fixture(
    *,
    fixture_id: str,
    diff: FixtureDiff,
    decisions: Sequence[FixtureDecision],
    expected_findings: Sequence[ExpectedFinding],
    metadata: Mapping[str, Any],
) -> EvalFixture:
    return EvalFixture(
        fixture_id=fixture_id,
        diff=diff,
        decisions=tuple(decisions),
        expected_findings=tuple(expected_findings),
        labels=(),
        metadata=metadata,
    )


def _merge_metadata(
    stamped: Mapping[str, Any], extra_metadata: Mapping[str, Any] | None
) -> dict[str, Any]:
    merged = dict(stamped)
    if extra_metadata is None:
        return merged
    if not isinstance(extra_metadata, Mapping):
        raise CorpusBuilderError("extra_metadata must be a JSON object")
    collisions = sorted(set(extra_metadata) & set(stamped))
    if collisions:
        raise CorpusBuilderError(
            f"extra_metadata may not override builder-stamped keys: {collisions}"
        )
    merged.update(extra_metadata)
    return merged


def _split_repo(repo: str) -> tuple[str, str]:
    owner, separator, name = repo.partition("/")
    if not separator or not owner.strip() or not name.strip() or "/" in name:
        raise CorpusBuilderError(
            f"repo must be in 'owner/name' form; got {repo!r}"
        )
    return owner, name


def _json_object(text: str, *, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorpusBuilderError(f"{context} did not return valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CorpusBuilderError(f"{context} must return a JSON object")
    return payload


def _get_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CorpusBuilderError(f"{key} must be a non-empty string")
    return value
