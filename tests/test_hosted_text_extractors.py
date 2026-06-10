"""Tests for the deterministic text-source extractors (cortex#354-#356).

Covers: commit-message statement extraction (T1.8 subjects, decision-verb
body lines, trailer scope hints) with exact spans; PR-description section
gating (Why/Decision/Approach rank, checklists/boilerplate drop); PR-review-
comment rule mining with the always-backfilled cold-start stamp; document_type
dispatch; gathering helpers over committed git/gh fixture captures (no
network); ledger-event identity; and an end-to-end derive run over gathered
documents, including the `--commits` CLI flag against a real temporary git
repo.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.derive import (
    default_source_id,
    default_tenant_id,
    run_derive,
)
from cortex.hosted.derive_store import DeriveEventStore, derive_store_path
from cortex.hosted.extractors import (
    COMMIT_MESSAGE_DOCUMENT_TYPE,
    DROP_COMMIT_BODY_LINE_WITHOUT_DECISION_VERB,
    DROP_COMMIT_EMPTY_MESSAGE,
    DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN,
    DROP_COMMIT_TRAILER_WITHOUT_CANDIDATE,
    DROP_COMMIT_TRAILER_WITHOUT_ISSUE_REF,
    DROP_PR_CHECKLIST_ITEM,
    DROP_PR_EMPTY_BODY,
    DROP_PR_HEADING_ONLY,
    DROP_PR_OUTSIDE_DECISION_SECTION,
    DROP_PR_TEMPLATE_STUB,
    DROP_REVIEW_APPROVAL_ONLY,
    DROP_REVIEW_EMOJI_ONLY,
    DROP_REVIEW_NIT_ONLY,
    DROP_REVIEW_NO_RULE_PROPOSAL,
    EXTRACTOR_IDS,
    GIT_LOG_PRETTY_FORMAT,
    PR_DESCRIPTION_DOCUMENT_TYPE,
    PR_REVIEW_COMMENT_DOCUMENT_TYPE,
    PR_VIEW_JSON_FIELDS,
    ExtractorError,
    RepoNativeExtractor,
    candidate_events,
    classify_source,
    commit_message_documents,
    extract_commit_message_decisions,
    extract_pr_description_decisions,
    extract_pr_review_comment_rules,
    extract_repo_native,
    gather_commit_message_documents,
    gather_pr_documents,
    pr_description_documents,
    pr_review_comment_documents,
)
from cortex.hosted.lanes import DEFAULT_LANE_POLICY, DeriveSourceType, Lane
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.scopes import ScopeType

FIXTURES = Path(__file__).parent / "fixtures" / "text_extractors"
TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
T0 = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)


def _document(
    document_type: str,
    external_id: str,
    content: str,
    *,
    source_timestamp: datetime = T0,
) -> SourceDocument:
    return SourceDocument(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        document_type=document_type,
        external_id=external_id,
        permalink=f"fixture:{external_id}",
        author_ref="fixture-author",
        source_timestamp=source_timestamp,
        content=content,
    )


def _commit(content: str, sha: str = "a" * 40) -> SourceDocument:
    return _document(COMMIT_MESSAGE_DOCUMENT_TYPE, sha, content)


def _pr_description(content: str) -> SourceDocument:
    return _document(PR_DESCRIPTION_DOCUMENT_TYPE, "pr-612", content)


def _review_comment(content: str) -> SourceDocument:
    return _document(PR_REVIEW_COMMENT_DOCUMENT_TYPE, "pr-612/review-comment-92", content)


# ---------------------------------------------------------------------------
# #354 — commit messages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "fix: resolve scheduler regression under load",
        "fix(scheduler)!: regression in retry backoff",
        "refactor: split the store (removes the cache layer)",
        "refactor(core): introduces a write-ahead log",
        "feat: new ingest path (breaking)",
        "feat!: replaces the polling loop",
    ],
)
def test_t18_subjects_become_candidates_with_exact_spans(subject: str) -> None:
    content = f"{subject}\n\nNothing binding in this body.\n"
    outcome = extract_commit_message_decisions(_commit(content))
    assert outcome.source_type is DeriveSourceType.COMMIT_MESSAGE
    texts = [item.candidate.decision_text for item in outcome.extracted]
    assert texts == [subject]
    span = outcome.extracted[0].candidate.spans[0]
    assert span.start_offset == 0
    assert span.end_offset == len(subject)
    assert span.excerpt == subject
    assert outcome.extracted[0].metadata["statement_kind"] == "subject"


def test_non_decision_subject_drops_with_reason() -> None:
    outcome = extract_commit_message_decisions(_commit("chore: bump dependencies\n"))
    assert outcome.extracted == ()
    assert [chatter.reason_code for chatter in outcome.dropped] == [
        DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN
    ]


def test_body_decision_verb_lines_become_candidates_with_real_offsets() -> None:
    line_a = "We decided to drive ingestion from webhooks."
    line_b = "The poller is no longer the entry point."
    content = f"chore: tidy\n\n{line_a}\nPlain context prose.\n{line_b}\n"
    outcome = extract_commit_message_decisions(_commit(content))
    texts = [item.candidate.decision_text for item in outcome.extracted]
    assert texts == [line_a, line_b]
    for item, line in zip(outcome.extracted, (line_a, line_b), strict=True):
        span = item.candidate.spans[0]
        assert span.start_offset == content.index(line)
        assert span.excerpt == line
        assert item.metadata["statement_kind"] == "body_line"
    reasons = [chatter.reason_code for chatter in outcome.dropped]
    assert reasons == [
        DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN,
        DROP_COMMIT_BODY_LINE_WITHOUT_DECISION_VERB,
    ]


def test_trailers_contribute_issue_ref_scopes_to_every_candidate() -> None:
    content = (
        "fix: scheduler regression\n"
        "\n"
        "The scheduler must use monotonic clocks.\n"
        "\n"
        "Closes-issue: #354\n"
        "BREAKING CHANGE: removes wall-clock scheduling (#99)\n"
    )
    outcome = extract_commit_message_decisions(_commit(content))
    assert len(outcome.extracted) == 2  # subject + body line
    for item in outcome.extracted:
        scopes = {
            (scope.scope_type, scope.normalized_value)
            for scope in item.candidate.proposed_scopes
        }
        assert (ScopeType.ISSUE_REF, "#354") in scopes
        assert (ScopeType.ISSUE_REF, "#99") in scopes
        assert item.metadata["issue_refs"] == ["#354", "#99"]
    # Consumed trailers are scope hints, not drops.
    assert outcome.dropped == ()


def test_trailer_without_candidate_drops_visibly() -> None:
    content = "chore: tidy\n\nCloses-issue: #354\n"
    outcome = extract_commit_message_decisions(_commit(content))
    assert outcome.extracted == ()
    reasons = [chatter.reason_code for chatter in outcome.dropped]
    assert reasons == [
        DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN,
        DROP_COMMIT_TRAILER_WITHOUT_CANDIDATE,
    ]


def test_trailer_without_issue_ref_drops_visibly() -> None:
    content = "fix: a regression\n\nCloses: the design gap\n"
    outcome = extract_commit_message_decisions(_commit(content))
    assert len(outcome.extracted) == 1
    assert [chatter.reason_code for chatter in outcome.dropped] == [
        DROP_COMMIT_TRAILER_WITHOUT_ISSUE_REF
    ]


def test_commit_lane_is_provisional_never_auto_promotable() -> None:
    outcome = extract_commit_message_decisions(
        _commit("feat: replaces the queue\n\nWe decided to keep one writer.\n")
    )
    expected = DEFAULT_LANE_POLICY.assign(DeriveSourceType.COMMIT_MESSAGE, backfilled=False)
    for item in outcome.extracted:
        assert item.lane == expected
        assert item.lane.lane is Lane.PROVISIONAL
        assert item.lane.auto_promotable is False
        assert item.lane.backfilled is False


def test_commit_extraction_is_deterministic_across_runs() -> None:
    content = "fix: cache regression\n\nReads must go through the index.\n"
    document = _commit(content)
    first = extract_commit_message_decisions(document)
    second = extract_commit_message_decisions(document)
    assert [item.candidate for item in first.extracted] == [
        item.candidate for item in second.extracted
    ]
    events_a = candidate_events(document, first)
    events_b = candidate_events(document, second)
    assert [event.idempotency_key for event in events_a] == [
        event.idempotency_key for event in events_b
    ]
    assert [event.event_hash for event in events_a] == [
        event.event_hash for event in events_b
    ]


# ---------------------------------------------------------------------------
# #355 — PR descriptions
# ---------------------------------------------------------------------------

PR_BODY = (
    "## Why\n"
    "\n"
    "We route all ledger writes through the queue so replay stays ordered.\n"
    "\n"
    "## Approach\n"
    "\n"
    "- Switch the writer to the queue client.\n"
    "\n"
    "## Test plan\n"
    "\n"
    "Ran the replay suite locally.\n"
    "\n"
    "## Checklist\n"
    "\n"
    "- [ ] Tests pass\n"
    "- [x] Docs updated\n"
    "\n"
    "<!-- template: describe your change -->\n"
    "\n"
    "\U0001f916 Generated with [Claude Code](https://claude.com/claude-code)\n"
)


def test_pr_statements_under_decision_sections_rank_as_candidates() -> None:
    outcome = extract_pr_description_decisions(_pr_description(PR_BODY))
    assert outcome.source_type is DeriveSourceType.PR_DESCRIPTION
    texts = [item.candidate.decision_text for item in outcome.extracted]
    assert texts == [
        "We route all ledger writes through the queue so replay stays ordered.",
        "Switch the writer to the queue client.",
    ]
    assert [item.metadata["section"] for item in outcome.extracted] == ["Why", "Approach"]
    span = outcome.extracted[0].candidate.spans[0]
    assert span.start_offset == PR_BODY.index(texts[0])
    assert span.excerpt == texts[0]


def test_pr_statements_outside_decision_sections_drop() -> None:
    outcome = extract_pr_description_decisions(_pr_description(PR_BODY))
    reasons = [chatter.reason_code for chatter in outcome.dropped]
    assert DROP_PR_OUTSIDE_DECISION_SECTION in reasons  # the test-plan prose
    assert reasons.count(DROP_PR_HEADING_ONLY) == 4


def test_pr_checklist_items_drop_with_reason() -> None:
    outcome = extract_pr_description_decisions(_pr_description(PR_BODY))
    reasons = [chatter.reason_code for chatter in outcome.dropped]
    assert reasons.count(DROP_PR_CHECKLIST_ITEM) == 2


def test_pr_template_stubs_and_boilerplate_drop_with_reason() -> None:
    outcome = extract_pr_description_decisions(_pr_description(PR_BODY))
    reasons = [chatter.reason_code for chatter in outcome.dropped]
    # The HTML comment stub and the attribution footer.
    assert reasons.count(DROP_PR_TEMPLATE_STUB) == 2


def test_pr_checklist_inside_decision_section_still_drops() -> None:
    body = "## Decision\n\n- [ ] decide later\n- Use one queue per tenant.\n"
    outcome = extract_pr_description_decisions(_pr_description(body))
    assert [item.candidate.decision_text for item in outcome.extracted] == [
        "Use one queue per tenant."
    ]
    reasons = [chatter.reason_code for chatter in outcome.dropped]
    assert DROP_PR_CHECKLIST_ITEM in reasons


def test_pr_lane_is_provisional_never_auto_promotable() -> None:
    outcome = extract_pr_description_decisions(_pr_description(PR_BODY))
    expected = DEFAULT_LANE_POLICY.assign(DeriveSourceType.PR_DESCRIPTION, backfilled=False)
    for item in outcome.extracted:
        assert item.lane == expected
        assert item.lane.lane is Lane.PROVISIONAL
        assert item.lane.auto_promotable is False


# ---------------------------------------------------------------------------
# #356 — PR review comments
# ---------------------------------------------------------------------------


def test_review_rule_sentences_become_candidates_with_sentence_spans() -> None:
    content = (
        "Nice catch. We should never call the database from the render path. "
        "Going forward, always namespace the keys."
    )
    outcome = extract_pr_review_comment_rules(_review_comment(content))
    assert outcome.source_type is DeriveSourceType.PR_REVIEW_COMMENT
    texts = [item.candidate.decision_text for item in outcome.extracted]
    assert texts == [
        "We should never call the database from the render path.",
        "Going forward, always namespace the keys.",
    ]
    for item, text in zip(outcome.extracted, texts, strict=True):
        span = item.candidate.spans[0]
        assert span.start_offset == content.index(text)
        assert span.excerpt == text
    assert outcome.dropped == ()


def test_review_convention_comment_becomes_candidate() -> None:
    content = "Convention: error codes are namespaced per module."
    outcome = extract_pr_review_comment_rules(_review_comment(content))
    assert [item.candidate.decision_text for item in outcome.extracted] == [content]


def test_review_candidates_are_always_backfilled_advisory_only() -> None:
    """Cold-start caveat: bad backfill is worse than an empty graph — every
    review-mined candidate enters backfilled=True, advisory-only, never
    auto-promotable."""

    outcome = extract_pr_review_comment_rules(
        _review_comment("We should always pin the schema version.")
    )
    expected = DEFAULT_LANE_POLICY.assign(
        DeriveSourceType.PR_REVIEW_COMMENT, backfilled=True
    )
    for item in outcome.extracted:
        assert item.lane == expected
        assert item.lane.backfilled is True
        assert item.lane.advisory_only is True
        assert item.lane.auto_promotable is False


@pytest.mark.parametrize("content", ["lgtm", "LGTM!", "ship it", "+1", "looks good to me."])
def test_review_approvals_drop_with_reason(content: str) -> None:
    outcome = extract_pr_review_comment_rules(_review_comment(content))
    assert outcome.extracted == ()
    assert [chatter.reason_code for chatter in outcome.dropped] == [
        DROP_REVIEW_APPROVAL_ONLY
    ]


def test_review_single_emoji_drops_with_reason() -> None:
    outcome = extract_pr_review_comment_rules(_review_comment("\U0001f44d"))
    assert [chatter.reason_code for chatter in outcome.dropped] == [DROP_REVIEW_EMOJI_ONLY]


@pytest.mark.parametrize(
    "content", ["typo: recieve -> receive", "nit: trailing whitespace", "Nitpick, naming."]
)
def test_review_nits_drop_with_reason(content: str) -> None:
    outcome = extract_pr_review_comment_rules(_review_comment(content))
    assert [chatter.reason_code for chatter in outcome.dropped] == [DROP_REVIEW_NIT_ONLY]


def test_review_other_prose_drops_as_no_rule_proposal() -> None:
    outcome = extract_pr_review_comment_rules(
        _review_comment("Could you explain why this loop runs twice?")
    )
    assert [chatter.reason_code for chatter in outcome.dropped] == [
        DROP_REVIEW_NO_RULE_PROPOSAL
    ]


# ---------------------------------------------------------------------------
# Dispatch and ledger-event identity
# ---------------------------------------------------------------------------


def test_classify_source_routes_text_document_types() -> None:
    assert classify_source(_commit("fix: a regression\n")) is DeriveSourceType.COMMIT_MESSAGE
    assert (
        classify_source(_pr_description("## Why\n\nBecause.\n"))
        is DeriveSourceType.PR_DESCRIPTION
    )
    assert (
        classify_source(_review_comment("lgtm"))
        is DeriveSourceType.PR_REVIEW_COMMENT
    )
    # Repo files still route by filename; unknown files still fail closed.
    with pytest.raises(ExtractorError, match=r"README\.md"):
        classify_source(_document("repo-file", "README.md", "hello"))


def test_commit_candidate_events_conform_to_the_derive_envelope() -> None:
    sha = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    document = _commit("fix: cache regression\n\nReads must go through the index.\n", sha)
    outcome = extract_repo_native(document)
    events = candidate_events(document, outcome)
    assert len(events) == 2
    for event in events:
        assert event.actor.actor_id == EXTRACTOR_IDS[DeriveSourceType.COMMIT_MESSAGE]
        assert event.payload["source_type"] == DeriveSourceType.COMMIT_MESSAGE.value
        assert event.payload["lane_assignment"]["lane"] == "provisional"
        external_ref = event.source_event_external_id
        assert external_ref is not None
        assert external_ref.startswith(f"{sha}@")


def test_repo_native_extractor_accumulates_text_source_drops() -> None:
    extractor = RepoNativeExtractor()
    assert extractor(_review_comment("lgtm")) == ()
    assert extractor(_commit("chore: tidy\n")) == ()
    assert [record.chatter.reason_code for record in extractor.dropped] == [
        DROP_REVIEW_APPROVAL_ONLY,
        DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN,
    ]


# ---------------------------------------------------------------------------
# Gathering: git log
# ---------------------------------------------------------------------------


def test_commit_documents_from_git_log_fixture_are_deterministic() -> None:
    raw = (FIXTURES / "git_log.txt").read_text(encoding="utf-8")
    gathered = commit_message_documents(raw, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    assert gathered.dropped == ()
    # Ascending (author timestamp, sha) order regardless of git's newest-first.
    assert [doc.external_id for doc in gathered.documents] == [
        "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "b2c3d4e5f6a7081920a1b2c3d4e5f60718293a4b",
        "c3d4e5f6a7b8091a2b3c4d5e6f7081920a1b2c3d",
    ]
    first = gathered.documents[0]
    assert first.document_type == COMMIT_MESSAGE_DOCUMENT_TYPE
    assert first.author_ref == "Ada Lovelace <ada@example.com>"
    assert first.source_timestamp == datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    assert first.content.startswith("feat: replace the polling loop (replaces poller)")
    # Re-parsing yields identical documents (content-keyed hashes included).
    again = commit_message_documents(raw, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    assert [doc.document_hash for doc in again.documents] == [
        doc.document_hash for doc in gathered.documents
    ]


def test_git_log_fixture_extracts_candidates_end_to_end() -> None:
    raw = (FIXTURES / "git_log.txt").read_text(encoding="utf-8")
    gathered = commit_message_documents(raw, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    extractor = RepoNativeExtractor()
    events = [event for document in gathered.documents for event in extractor(document)]
    texts = [event.payload["decision_text"] for event in events]
    assert "feat: replace the polling loop (replaces poller)" in texts
    assert "The scheduler must use monotonic clocks." in texts
    assert "We decided to drive ingestion from webhooks instead of polling." in texts


def test_commit_documents_with_empty_message_drop_visibly() -> None:
    sha = "d" * 40
    raw = f"{sha}\x1fDev <dev@example.com>\x1f2026-06-04T10:00:00+00:00\x1f\n\x1e"
    gathered = commit_message_documents(raw, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    assert gathered.documents == ()
    assert [record.external_id for record in gathered.dropped] == [sha]
    assert [record.chatter.reason_code for record in gathered.dropped] == [
        DROP_COMMIT_EMPTY_MESSAGE
    ]


def test_malformed_git_log_record_fails_closed() -> None:
    with pytest.raises(ExtractorError, match="expected 4"):
        commit_message_documents(
            "only-two-fields\x1foops\x1e", tenant_id=TENANT_ID, source_id=SOURCE_ID
        )
    with pytest.raises(ExtractorError, match="not a commit sha"):
        commit_message_documents(
            "NOT-A-SHA\x1fDev <d@e.c>\x1f2026-06-04T10:00:00+00:00\x1ffix: regression x\x1e",
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
        )
    with pytest.raises(ExtractorError, match="timezone-aware"):
        commit_message_documents(
            ("e" * 40) + "\x1fDev <d@e.c>\x1f2026-06-04T10:00:00\x1ffix: regression x\x1e",
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
        )


def test_gather_commit_documents_invokes_git_with_the_pinned_format(
    tmp_path: Path,
) -> None:
    seen: list[tuple[tuple[str, ...], Path]] = []
    raw = (FIXTURES / "git_log.txt").read_text(encoding="utf-8")

    def runner(argv: Sequence[str], cwd: Path) -> str:
        seen.append((tuple(argv), cwd))
        return raw

    gathered = gather_commit_message_documents(
        tmp_path, tenant_id=TENANT_ID, source_id=SOURCE_ID, limit=3, runner=runner
    )
    assert len(gathered.documents) == 3
    assert seen == [
        (
            ("git", "log", "-n", "3", f"--pretty=format:{GIT_LOG_PRETTY_FORMAT}"),
            tmp_path,
        )
    ]
    with pytest.raises(ExtractorError, match=">= 1"):
        gather_commit_message_documents(
            tmp_path, tenant_id=TENANT_ID, source_id=SOURCE_ID, limit=0, runner=runner
        )


# ---------------------------------------------------------------------------
# Gathering: gh pr view + review comments
# ---------------------------------------------------------------------------


def test_pr_description_document_from_fixture_json() -> None:
    payload = json.loads((FIXTURES / "pr_view.json").read_text(encoding="utf-8"))
    gathered = pr_description_documents(payload, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    assert gathered.dropped == ()
    (document,) = gathered.documents
    assert document.document_type == PR_DESCRIPTION_DOCUMENT_TYPE
    assert document.external_id == "pr-612"
    assert document.permalink == "https://github.com/autumngarage/cortex/pull/612"
    assert document.author_ref == "octocat"
    assert document.source_timestamp == datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
    assert document.metadata["pr_number"] == 612
    outcome = extract_repo_native(document)
    assert [item.candidate.decision_text for item in outcome.extracted] == [
        "We route all ledger writes through the queue so replay stays ordered.",
        "Switch the writer to the queue client in `src/cortex/hosted/graph_writes.py`.",
    ]


def test_pr_description_empty_body_drops_visibly() -> None:
    payload = {
        "number": 7,
        "title": "t",
        "body": "  \n",
        "author": {"login": "octocat"},
        "createdAt": "2026-06-02T12:00:00Z",
        "url": "https://example.com/pull/7",
    }
    gathered = pr_description_documents(payload, tenant_id=TENANT_ID, source_id=SOURCE_ID)
    assert gathered.documents == ()
    assert [record.external_id for record in gathered.dropped] == ["pr-7"]
    assert [record.chatter.reason_code for record in gathered.dropped] == [
        DROP_PR_EMPTY_BODY
    ]


def test_pr_description_missing_field_fails_closed() -> None:
    with pytest.raises(ExtractorError, match="missing field 'author'"):
        pr_description_documents(
            {"number": 7, "title": "t", "body": "x", "createdAt": "x", "url": "u"},
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
        )


def test_pr_review_comment_documents_from_fixture_json_sorted_and_typed() -> None:
    payload = json.loads((FIXTURES / "pr_review_comments.json").read_text(encoding="utf-8"))
    gathered = pr_review_comment_documents(
        payload, pr_number=612, tenant_id=TENANT_ID, source_id=SOURCE_ID
    )
    assert gathered.dropped == ()
    # (created_at, id) ascending regardless of the capture's order.
    assert [doc.external_id for doc in gathered.documents] == [
        "pr-612/review-comment-91",
        "pr-612/review-comment-92",
        "pr-612/review-comment-93",
        "pr-612/review-comment-94",
        "pr-612/review-comment-95",
    ]
    for document in gathered.documents:
        assert document.document_type == PR_REVIEW_COMMENT_DOCUMENT_TYPE
        assert document.metadata["pr_number"] == 612

    extractor = RepoNativeExtractor()
    events = [event for document in gathered.documents for event in extractor(document)]
    texts = [event.payload["decision_text"] for event in events]
    assert texts == [
        "We should never call the database from the render path.",
        "Convention: error codes are namespaced per module going forward.",
    ]
    for event in events:
        assert event.payload["lane_assignment"]["backfilled"] is True
    assert sorted(record.chatter.reason_code for record in extractor.dropped) == sorted(
        [DROP_REVIEW_APPROVAL_ONLY, DROP_REVIEW_EMOJI_ONLY, DROP_REVIEW_NIT_ONLY]
    )


def test_pr_review_comment_payload_must_be_an_array_of_objects() -> None:
    with pytest.raises(ExtractorError, match="JSON array"):
        pr_review_comment_documents(
            {"id": 1}, pr_number=612, tenant_id=TENANT_ID, source_id=SOURCE_ID
        )
    with pytest.raises(ExtractorError, match=r"\[0\].*missing field 'user'"):
        pr_review_comment_documents(
            [{"id": 1, "body": "x", "created_at": "t", "html_url": "u"}],
            pr_number=612,
            tenant_id=TENANT_ID,
            source_id=SOURCE_ID,
        )


def test_gather_pr_documents_replays_fixture_captures_without_network(
    tmp_path: Path,
) -> None:
    listing_raw = (FIXTURES / "pr_list.json").read_text(encoding="utf-8")
    view_raw = (FIXTURES / "pr_view.json").read_text(encoding="utf-8")
    comments_raw = (FIXTURES / "pr_review_comments.json").read_text(encoding="utf-8")
    seen: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str], cwd: Path) -> str:
        assert cwd == tmp_path
        command = tuple(argv)
        seen.append(command)
        if command[:3] == ("gh", "pr", "list"):
            return listing_raw
        if command[:3] == ("gh", "pr", "view"):
            return view_raw
        if command[:2] == ("gh", "api"):
            return comments_raw
        raise AssertionError(f"unexpected command: {command}")

    gathered = gather_pr_documents(
        tmp_path, tenant_id=TENANT_ID, source_id=SOURCE_ID, limit=1, runner=runner
    )
    assert [doc.document_type for doc in gathered.documents] == [
        PR_DESCRIPTION_DOCUMENT_TYPE,
        *([PR_REVIEW_COMMENT_DOCUMENT_TYPE] * 5),
    ]
    assert seen == [
        ("gh", "pr", "list", "--state", "merged", "--limit", "1", "--json", "number"),
        ("gh", "pr", "view", "612", "--json", PR_VIEW_JSON_FIELDS),
        ("gh", "api", "repos/{owner}/{repo}/pulls/612/comments"),
    ]


def test_gather_pr_documents_fails_closed_on_invalid_json(tmp_path: Path) -> None:
    def runner(argv: Sequence[str], cwd: Path) -> str:
        _ = argv, cwd
        return "not json"

    with pytest.raises(ExtractorError, match="not valid JSON"):
        gather_pr_documents(
            tmp_path, tenant_id=TENANT_ID, source_id=SOURCE_ID, limit=1, runner=runner
        )


# ---------------------------------------------------------------------------
# End-to-end: run_derive over gathered documents + the --commits CLI flag
# ---------------------------------------------------------------------------


@pytest.fixture
def cortex_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cortex").mkdir(parents=True)
    return root


def test_run_derive_persists_gathered_document_events(cortex_repo: Path) -> None:
    raw = (FIXTURES / "git_log.txt").read_text(encoding="utf-8")
    tenant = default_tenant_id(cortex_repo)
    source = default_source_id(cortex_repo)
    gathered = commit_message_documents(raw, tenant_id=tenant, source_id=source)
    result = run_derive(
        project_root=cortex_repo,
        source_files=(),
        tenant_id=tenant,
        source_id=source,
        extractor=RepoNativeExtractor(),
        documents=gathered.documents,
    )
    assert result.documents == gathered.documents
    assert result.inserted == len(result.events) > 0
    # Re-running over unchanged gathered documents is an ignored duplicate set.
    rerun = run_derive(
        project_root=cortex_repo,
        source_files=(),
        tenant_id=tenant,
        source_id=source,
        extractor=RepoNativeExtractor(),
        documents=gathered.documents,
    )
    assert rerun.inserted == 0
    assert rerun.ignored == len(result.events)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.com",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_cli_derive_commits_flag_ingests_local_git_history(
    cortex_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    _git(cortex_repo, "init", "-q")
    _git(cortex_repo, "commit", "-q", "--allow-empty", "-m", "chore: scaffold")
    _git(
        cortex_repo,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "fix: scheduler regression\n\nThe scheduler must use monotonic clocks.\n\n"
        "Closes-issue: #354",
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["derive", "--path", str(cortex_repo), "--commits", "5"])
    assert result.exit_code == 0, result.output
    assert "2 gathered document(s)" in result.output
    assert "2 inserted" in result.output
    assert DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN in result.output
    with DeriveEventStore(derive_store_path(cortex_repo)) as store:
        assert len(store.event_hashes()) == 2


def test_cli_derive_rejects_negative_commits(cortex_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["derive", "--path", str(cortex_repo), "--commits", "-1"])
    assert result.exit_code != 0
