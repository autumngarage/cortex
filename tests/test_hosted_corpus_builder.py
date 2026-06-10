"""Tests for the eval-corpus builder tooling (cortex#339)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from cortex.hosted.corpus_builder import (
    CORPUS_SOURCES,
    REAL_HISTORY_SOURCE,
    SYNTHETIC_SOURCE,
    CorpusBuilderError,
    build_document_span,
    build_fixture_from_commit_range,
    build_fixture_from_pr,
    build_synthetic_fixture,
    document_content_hash,
    fetch_commit_range_diff,
    fetch_merged_pr_diff,
    load_corpus,
    run_command,
    write_fixture,
)
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
)

DOC_CONTENT = (
    "# Retry doctrine\n\nOutbound retries use exponential backoff with jitter; "
    "fixed-interval retries are forbidden.\n"
)
DOC_PERMALINK = "https://github.com/autumngarage/cortex/blob/abc1234/docs/retry.md"

PR_VIEW_PAYLOAD = {
    "baseRefOid": "a" * 40,
    "headRefOid": "b" * 40,
    "files": [{"path": "src/b.py"}, {"path": "src/a.py"}, {"path": "src/b.py"}],
    "mergedAt": "2026-06-09T17:07:32Z",
    "state": "MERGED",
}
PR_PATCH = "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"


class RecordingRunner:
    """Fake CommandRunner that records argv and replays canned outputs."""

    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        self.calls.append(list(argv))
        if not self.outputs:
            raise AssertionError(f"unexpected extra command: {argv}")
        return self.outputs.pop(0)


def _pr_runner(payload: Mapping[str, object] | None = None, patch: str = PR_PATCH) -> RecordingRunner:
    return RecordingRunner([json.dumps(payload or PR_VIEW_PAYLOAD), patch])


def _decision() -> FixtureDecision:
    span = build_document_span(
        document_content=DOC_CONTENT,
        excerpt="fixed-interval retries are forbidden.",
        permalink=DOC_PERMALINK,
    )
    return FixtureDecision(
        decision_id="retry-backoff",
        decision_text="Retries use exponential backoff with jitter.",
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-05-14T09:30:00+00:00",
        spans=(span,),
    )


def _finding(decision: FixtureDecision) -> ExpectedFinding:
    return ExpectedFinding(
        finding_id="finding-fixed-retry",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id=decision.decision_id,
        cited_span_hashes=decision.span_hashes,
        summary="The diff replaces backoff with a fixed retry interval.",
    )


# --- run_command -------------------------------------------------------------


def test_run_command_rejects_empty_argv():
    with pytest.raises(CorpusBuilderError, match="argv must not be empty"):
        run_command([])


def test_run_command_fails_closed_on_nonzero_exit():
    with pytest.raises(CorpusBuilderError, match="command exited"):
        run_command(["git", "definitely-not-a-real-subcommand-339"])


def test_run_command_fails_closed_on_missing_binary():
    with pytest.raises(CorpusBuilderError, match="command not found"):
        run_command(["cortex-no-such-binary-339"])


# --- document spans ----------------------------------------------------------


def test_build_document_span_freezes_offsets_and_hash():
    excerpt = "Outbound retries use exponential backoff with jitter;"
    span = build_document_span(
        document_content=DOC_CONTENT, excerpt=excerpt, permalink=DOC_PERMALINK
    )
    assert span.start_offset == DOC_CONTENT.index(excerpt)
    assert span.end_offset == span.start_offset + len(excerpt)
    assert DOC_CONTENT[span.start_offset : span.end_offset] == excerpt
    assert span.source_document_hash == hashlib.sha256(DOC_CONTENT.encode("utf-8")).hexdigest()
    assert span.source_document_hash == document_content_hash(DOC_CONTENT)


def test_build_document_span_missing_excerpt_fails_closed():
    with pytest.raises(CorpusBuilderError, match="excerpt not found"):
        build_document_span(
            document_content=DOC_CONTENT,
            excerpt="this text is not in the document",
            permalink=DOC_PERMALINK,
        )


def test_build_document_span_ambiguous_excerpt_fails_closed():
    with pytest.raises(CorpusBuilderError, match="occurs 2 times"):
        build_document_span(
            document_content="retry retry", excerpt="retry", permalink=DOC_PERMALINK
        )


def test_build_document_span_rejects_empty_inputs():
    with pytest.raises(CorpusBuilderError, match="document content"):
        build_document_span(document_content="", excerpt="x", permalink=DOC_PERMALINK)
    with pytest.raises(CorpusBuilderError, match="excerpt must not be empty"):
        build_document_span(document_content=DOC_CONTENT, excerpt="", permalink=DOC_PERMALINK)


def test_document_content_hash_rejects_empty():
    with pytest.raises(CorpusBuilderError, match="must not be empty"):
        document_content_hash("")


# --- fetch_merged_pr_diff ----------------------------------------------------


def test_fetch_merged_pr_diff_freezes_gh_metadata():
    runner = _pr_runner()
    frozen = fetch_merged_pr_diff("autumngarage/cortex", 483, runner=runner)
    assert frozen.merged_at == "2026-06-09T17:07:32Z"
    assert frozen.diff.repo_owner == "autumngarage"
    assert frozen.diff.repo_name == "cortex"
    assert frozen.diff.base_sha == "a" * 40
    assert frozen.diff.head_sha == "b" * 40
    assert frozen.diff.patch == PR_PATCH
    # Sorted and de-duplicated.
    assert frozen.diff.changed_paths == ("src/a.py", "src/b.py")
    assert runner.calls[0][:4] == ["gh", "pr", "view", "483"]
    assert runner.calls[1][:4] == ["gh", "pr", "diff", "483"]


def test_fetch_merged_pr_diff_rejects_unmerged_pr():
    payload = dict(PR_VIEW_PAYLOAD, state="OPEN")
    with pytest.raises(CorpusBuilderError, match="not MERGED"):
        fetch_merged_pr_diff("autumngarage/cortex", 1, runner=_pr_runner(payload))


def test_fetch_merged_pr_diff_rejects_empty_file_list():
    payload = dict(PR_VIEW_PAYLOAD, files=[])
    with pytest.raises(CorpusBuilderError, match="no changed files"):
        fetch_merged_pr_diff("autumngarage/cortex", 1, runner=_pr_runner(payload))


def test_fetch_merged_pr_diff_rejects_empty_patch():
    with pytest.raises(CorpusBuilderError, match="empty patch"):
        fetch_merged_pr_diff("autumngarage/cortex", 1, runner=_pr_runner(patch="\n"))


def test_fetch_merged_pr_diff_rejects_invalid_json():
    runner = RecordingRunner(["not json", PR_PATCH])
    with pytest.raises(CorpusBuilderError, match="did not return valid JSON"):
        fetch_merged_pr_diff("autumngarage/cortex", 1, runner=runner)


def test_fetch_merged_pr_diff_rejects_bad_repo_and_pr_number():
    with pytest.raises(CorpusBuilderError, match="owner/name"):
        fetch_merged_pr_diff("not-a-repo", 1, runner=_pr_runner())
    with pytest.raises(CorpusBuilderError, match="pr_number must be positive"):
        fetch_merged_pr_diff("autumngarage/cortex", 0, runner=_pr_runner())


# --- fetch_commit_range_diff -------------------------------------------------


def test_fetch_commit_range_diff_scopes_paths_and_sorts():
    patch = "diff --git a/.cortex/journal/x.md b/.cortex/journal/x.md\n-deleted\n"
    runner = RecordingRunner([patch, ".cortex/journal/z.md\n.cortex/journal/x.md\n"])
    diff = fetch_commit_range_diff(
        "autumngarage/cortex",
        "a" * 40,
        "b" * 40,
        paths=(".cortex/journal/",),
        runner=runner,
    )
    assert diff.patch == patch
    assert diff.changed_paths == (".cortex/journal/x.md", ".cortex/journal/z.md")
    assert runner.calls[0] == ["git", "diff", "a" * 40, "b" * 40, "--", ".cortex/journal/"]
    assert runner.calls[1] == [
        "git",
        "diff",
        "--name-only",
        "a" * 40,
        "b" * 40,
        "--",
        ".cortex/journal/",
    ]


def test_fetch_commit_range_diff_rejects_empty_patch():
    runner = RecordingRunner(["\n"])
    with pytest.raises(CorpusBuilderError, match="empty patch"):
        fetch_commit_range_diff(
            "autumngarage/cortex", "a" * 40, "b" * 40, paths=("nope/",), runner=runner
        )


def test_fetch_commit_range_diff_rejects_blank_shas_and_paths():
    with pytest.raises(CorpusBuilderError, match="base_sha"):
        fetch_commit_range_diff("autumngarage/cortex", " ", "b" * 40, runner=RecordingRunner([]))
    with pytest.raises(CorpusBuilderError, match="paths entries"):
        fetch_commit_range_diff(
            "autumngarage/cortex", "a" * 40, "b" * 40, paths=(" ",), runner=RecordingRunner([])
        )


# --- fixture assembly --------------------------------------------------------


def test_build_fixture_from_pr_is_deterministic_and_ungraded():
    decision = _decision()
    finding = _finding(decision)

    def build() -> EvalFixture:
        return build_fixture_from_pr(
            "autumngarage/cortex",
            483,
            fixture_id="example-001",
            decisions=(decision,),
            expected_findings=(finding,),
            extra_metadata={"scenario": "example"},
            runner=_pr_runner(),
        )

    first, second = build(), build()
    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.labels == ()
    assert first.metadata["source"] == REAL_HISTORY_SOURCE
    assert first.metadata["repo"] == "autumngarage/cortex"
    assert first.metadata["pr_number"] == 483
    assert first.metadata["merged_at"] == "2026-06-09T17:07:32Z"
    assert first.metadata["scenario"] == "example"


def test_build_fixture_from_pr_rejects_reserved_metadata_override():
    with pytest.raises(CorpusBuilderError, match=r"builder-stamped keys.*source"):
        build_fixture_from_pr(
            "autumngarage/cortex",
            483,
            fixture_id="example-001",
            decisions=(_decision(),),
            extra_metadata={"source": "hand-authored"},
            runner=_pr_runner(),
        )


def test_build_fixture_from_commit_range_discloses_scoping():
    patch = "diff --git a/principles/x.md b/principles/x.md\n+synced\n"
    runner = RecordingRunner([patch, "principles/x.md\n"])
    fixture = build_fixture_from_commit_range(
        "autumngarage/cortex",
        "a" * 40,
        "b" * 40,
        fixture_id="range-001",
        decisions=(_decision(),),
        paths=("principles/",),
        pr_number=98,
        runner=runner,
    )
    assert fixture.labels == ()
    assert fixture.metadata["source"] == REAL_HISTORY_SOURCE
    assert fixture.metadata["pr_number"] == 98
    assert fixture.metadata["patch_paths"] == ["principles/"]


def test_build_fixture_from_commit_range_rejects_nonpositive_pr_number():
    runner = RecordingRunner(["patch\n", "a.md\n"])
    with pytest.raises(CorpusBuilderError, match="pr_number must be positive"):
        build_fixture_from_commit_range(
            "autumngarage/cortex",
            "a" * 40,
            "b" * 40,
            fixture_id="range-001",
            decisions=(_decision(),),
            pr_number=0,
            runner=runner,
        )


def test_build_synthetic_fixture_is_marked_synthetic():
    decision = _decision()
    diff = FixtureDiff(
        repo_owner="autumngarage",
        repo_name="cortex",
        base_sha="a" * 40,
        head_sha="deadbeef" * 5,
        patch="diff --git a/x.py b/x.py\n+from sentinel import x\n",
        changed_paths=("x.py",),
    )
    fixture = build_synthetic_fixture(
        fixture_id="synthetic-001",
        diff=diff,
        decisions=(decision,),
        expected_findings=(_finding(decision),),
    )
    assert fixture.metadata == {"source": SYNTHETIC_SOURCE}
    assert fixture.labels == ()
    assert SYNTHETIC_SOURCE in CORPUS_SOURCES


# --- write_fixture / load_corpus ---------------------------------------------


def _synthetic_fixture(fixture_id: str = "synthetic-001") -> EvalFixture:
    decision = _decision()
    diff = FixtureDiff(
        repo_owner="autumngarage",
        repo_name="cortex",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch="diff --git a/x.py b/x.py\n+y\n",
        changed_paths=("x.py",),
    )
    return build_synthetic_fixture(fixture_id=fixture_id, diff=diff, decisions=(decision,))


def test_write_fixture_and_load_corpus_round_trip(tmp_path: Path) -> None:
    fixture = _synthetic_fixture()
    path = write_fixture(fixture, tmp_path)
    assert path == tmp_path / "synthetic-001.json"
    loaded = load_corpus(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].to_canonical_json() == fixture.to_canonical_json()


def test_write_fixture_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(CorpusBuilderError, match="does not exist"):
        write_fixture(_synthetic_fixture(), tmp_path / "missing")


def test_load_corpus_rejects_non_canonical_bytes(tmp_path: Path) -> None:
    fixture = _synthetic_fixture()
    payload = json.loads(fixture.to_canonical_json())
    (tmp_path / "synthetic-001.json").write_text(
        json.dumps(payload, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(CorpusBuilderError, match="not canonical JSON"):
        load_corpus(tmp_path)


def test_load_corpus_rejects_filename_id_mismatch(tmp_path: Path) -> None:
    fixture = _synthetic_fixture()
    (tmp_path / "wrong-name.json").write_text(fixture.to_canonical_json(), encoding="utf-8")
    with pytest.raises(CorpusBuilderError, match="does not match fixture_id"):
        load_corpus(tmp_path)


def test_load_corpus_rejects_unknown_source_class(tmp_path: Path) -> None:
    decision = _decision()
    diff = FixtureDiff(
        repo_owner="autumngarage",
        repo_name="cortex",
        base_sha="a" * 40,
        head_sha="b" * 40,
        patch="diff --git a/x.py b/x.py\n+y\n",
        changed_paths=("x.py",),
    )
    fixture = EvalFixture(
        fixture_id="unsourced-001",
        diff=diff,
        decisions=(decision,),
        metadata={"source": "vibes"},
    )
    write_fixture(fixture, tmp_path)
    with pytest.raises(CorpusBuilderError, match=r"metadata\.source"):
        load_corpus(tmp_path)


def test_load_corpus_rejects_empty_or_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(CorpusBuilderError, match="has no fixtures"):
        load_corpus(tmp_path)
    with pytest.raises(CorpusBuilderError, match="does not exist"):
        load_corpus(tmp_path / "missing")
