"""Tests for feedback capture wiring (cortex#393/#394).

The reply handler and reaction poller are exercised against an in-memory fake
DB (mirroring the append-only ``review_feedback_events`` ON CONFLICT idiom) and
a fake GitHub client (serving canned PR comments + reactions). The
load-bearing disciplines are asserted directly: a Cortex review comment must
exist for feedback to be recorded, our own comments/reactions are ignored
(recursion guard), and the ABSENCE of a human action writes nothing — silence
is never a positive label.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from cortex.hosted.github_app_auth import CommentReaction
from cortex.hosted.github_comment import make_marker, make_replay_marker
from cortex.hosted.jobs import ClaimedJob, HostedJobError
from cortex.hosted.review_feedback_capture import (
    CortexReviewComment,
    find_cortex_review_comment,
    handle_issue_comment_feedback,
    parse_issue_comment_payload,
    poll_comment_reactions,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
APP_LOGIN = "cortex-review[bot]"
HUMAN_LOGIN = "octocat"
MODEL_ID = "anthropic/claude-opus-4"
PROMPT_VERSION = "evaluate-stage0/v1"
SNAPSHOT = "b" * 64
OWNER = "acme"
REPO = "app"
PR_NUMBER = 7
HEAD_SHA = "abc1234"
CORTEX_COMMENT_ID = 5001


class FakeFeedbackResult:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class FakeFeedbackDb:
    """In-memory emulation of the review_feedback_events ON CONFLICT insert."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.commits = 0
        self.rollbacks = 0

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> FakeFeedbackResult:
        q = query.strip()
        p = dict(params or {})
        if q.startswith("INSERT INTO cortex_hosted.review_feedback_events"):
            key = str(p["idempotency_key"])
            if key in self.rows:
                return FakeFeedbackResult(None)  # ON CONFLICT DO NOTHING
            self.rows[key] = p
            return FakeFeedbackResult((f"row-{len(self.rows)}",))
        raise AssertionError(f"FakeFeedbackDb saw unexpected SQL: {q[:80]}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


def _cortex_review_body() -> str:
    return "\n\n".join(
        [
            make_marker(PR_NUMBER, HEAD_SHA),
            make_replay_marker(
                model_id=MODEL_ID, prompt_version=PROMPT_VERSION, snapshot_hash=SNAPSHOT
            ),
            "### Cortex reviewed this PR\n\nAdvisory.",
        ]
    )


class FakeGithubClient:
    """A canned PR served from memory: comments and per-comment reactions."""

    def __init__(
        self,
        *,
        comments: tuple[Mapping[str, Any], ...] = (),
        reactions: tuple[CommentReaction, ...] = (),
    ) -> None:
        self._comments = comments
        self._reactions = reactions
        self.reaction_calls: list[int] = []

    def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[Mapping[str, Any], ...]:
        return self._comments

    def list_comment_reactions(
        self, owner: str, repo: str, comment_id: int
    ) -> tuple[CommentReaction, ...]:
        self.reaction_calls.append(comment_id)
        return self._reactions


def _cortex_review_comment(comment_id: int = CORTEX_COMMENT_ID) -> Mapping[str, Any]:
    return {
        "id": comment_id,
        "body": _cortex_review_body(),
        "user": {"login": APP_LOGIN},
    }


def _human_comment(
    *,
    comment_id: int = 9001,
    login: str = HUMAN_LOGIN,
    body: str = "I disagree, that was superseded.",
) -> Mapping[str, Any]:
    return {"id": comment_id, "body": body, "user": {"login": login}}


def _issue_comment_job(
    *,
    comment_id: int = 9001,
    login: str = HUMAN_LOGIN,
    body: str = "I disagree, that was superseded.",
    action: str = "created",
) -> ClaimedJob:
    return ClaimedJob(
        job_id="job-1",
        job_type="github.issue_comment",
        idempotency_key="github-delivery:guid-1",
        payload={
            "event": "issue_comment",
            "delivery": "guid-1",
            "body": {
                "action": action,
                "installation": {"id": 55},
                "repository": {"name": REPO, "owner": {"login": OWNER}},
                "issue": {"number": PR_NUMBER},
                "comment": {
                    "id": comment_id,
                    "body": body,
                    "user": {"login": login},
                    "created_at": "2026-06-11T12:00:00Z",
                },
            },
        },
        attempts=1,
        max_attempts=3,
    )


def _client_factory(client: FakeGithubClient) -> Any:
    return lambda _installation_id: client


# ---------------------------------------------------------------------------
# 1. find_cortex_review_comment
# ---------------------------------------------------------------------------


def test_find_cortex_review_comment_resolves_replay_identity() -> None:
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    review = find_cortex_review_comment(client, owner=OWNER, repo=REPO, pr_number=PR_NUMBER)
    assert review is not None
    assert review.comment_id == CORTEX_COMMENT_ID
    assert review.app_login == APP_LOGIN
    assert review.replay.model_id == MODEL_ID
    assert review.replay.snapshot_hash == SNAPSHOT


def test_find_cortex_review_comment_returns_none_when_no_marker() -> None:
    client = FakeGithubClient(comments=(_human_comment(),))
    assert find_cortex_review_comment(client, owner=OWNER, repo=REPO, pr_number=PR_NUMBER) is None


def test_find_cortex_review_comment_prefers_latest_comment() -> None:
    older = dict(_cortex_review_comment(comment_id=4000))
    newer = dict(_cortex_review_comment(comment_id=6000))
    client = FakeGithubClient(comments=(older, newer))
    review = find_cortex_review_comment(client, owner=OWNER, repo=REPO, pr_number=PR_NUMBER)
    assert review is not None
    assert review.comment_id == 6000


# ---------------------------------------------------------------------------
# 2. Reply handler: records a reply when a Cortex review exists
# ---------------------------------------------------------------------------


def test_reply_handler_records_a_reply_when_a_cortex_comment_exists() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    result = handle_issue_comment_feedback(
        _issue_comment_job(),
        conn=db,
        client_factory=_client_factory(client),
        tenant_id=TENANT_ID,
    )
    assert result["feedback_recorded"] is True
    assert result["reason"] == "reply_recorded"
    assert result["feedback_kind"] == "reply"
    assert result["cortex_comment_id"] == CORTEX_COMMENT_ID
    # The stored event binds to the review's replay identity.
    (stored,) = db.rows.values()
    assert stored["model_id"] == MODEL_ID
    assert stored["prompt_version"] == PROMPT_VERSION
    assert stored["snapshot_hash"] == SNAPSHOT
    assert stored["feedback_kind"] == "reply"
    assert stored["sentiment"] == "unclassified"
    assert stored["raw_excerpt"] == "I disagree, that was superseded."


def test_reply_handler_is_idempotent_across_redeliveries() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    first = handle_issue_comment_feedback(
        _issue_comment_job(),
        conn=db,
        client_factory=_client_factory(client),
        tenant_id=TENANT_ID,
    )
    second = handle_issue_comment_feedback(
        _issue_comment_job(),
        conn=db,
        client_factory=_client_factory(client),
        tenant_id=TENANT_ID,
    )
    assert first["feedback_recorded"] is True
    assert second["feedback_recorded"] is False
    assert second["reason"] == "already_recorded"
    assert len(db.rows) == 1  # one human reply -> exactly one row


def test_reply_handler_visible_noop_when_no_cortex_review() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_human_comment(),))  # no Cortex comment
    result = handle_issue_comment_feedback(
        _issue_comment_job(),
        conn=db,
        client_factory=_client_factory(client),
        tenant_id=TENANT_ID,
    )
    assert result["feedback_recorded"] is False
    assert result["reason"] == "no_cortex_review"
    assert db.rows == {}  # nothing recorded — and no exception


# ---------------------------------------------------------------------------
# 3. Recursion guard: our own App is never feedback on ourselves
# ---------------------------------------------------------------------------


def test_reply_handler_ignores_our_own_app_comment() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    # A comment authored by our own App login (e.g. a follow-up Cortex comment).
    job = _issue_comment_job(comment_id=9100, login=APP_LOGIN, body="Cortex posted again.")
    result = handle_issue_comment_feedback(
        job, conn=db, client_factory=_client_factory(client), tenant_id=TENANT_ID
    )
    assert result["feedback_recorded"] is False
    assert result["reason"] == "self_authored"
    assert db.rows == {}


def test_reply_handler_ignores_the_review_comment_itself() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    # The webhook for our own review-comment creation must not feed back on us.
    job = _issue_comment_job(
        comment_id=CORTEX_COMMENT_ID, login=APP_LOGIN, body=_cortex_review_body()
    )
    result = handle_issue_comment_feedback(
        job, conn=db, client_factory=_client_factory(client), tenant_id=TENANT_ID
    )
    assert result["feedback_recorded"] is False
    assert result["reason"] == "self_authored"


# ---------------------------------------------------------------------------
# 4. Absence is not approval; blank/deleted are visible no-ops
# ---------------------------------------------------------------------------


def test_absence_of_human_feedback_writes_no_event() -> None:
    # A PR with a Cortex review and NO human comment yields no feedback row.
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    review = find_cortex_review_comment(client, owner=OWNER, repo=REPO, pr_number=PR_NUMBER)
    assert review is not None
    # No reactions on the comment -> the poller writes nothing. Silence is not
    # a positive label.
    accounting = poll_comment_reactions(
        FakeGithubClient(comments=(_cortex_review_comment(),), reactions=()),
        conn=db,
        tenant_id=TENANT_ID,
        review=review,
        repo_full_name=f"{OWNER}/{REPO}",
    )
    assert accounting["seen"] == 0
    assert accounting["recorded"] == 0
    assert db.rows == {}


def test_blank_reply_is_a_visible_noop() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    job = _issue_comment_job(body="    ")
    result = handle_issue_comment_feedback(
        job, conn=db, client_factory=_client_factory(client), tenant_id=TENANT_ID
    )
    assert result["feedback_recorded"] is False
    assert result["reason"] == "empty_reply"
    assert db.rows == {}


def test_deleted_comment_is_a_visible_noop() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(comments=(_cortex_review_comment(),))
    job = _issue_comment_job(action="deleted")
    result = handle_issue_comment_feedback(
        job, conn=db, client_factory=_client_factory(client), tenant_id=TENANT_ID
    )
    assert result["feedback_recorded"] is False
    assert result["reason"] == "comment_deleted"
    assert db.rows == {}


def test_deleted_comment_does_not_require_tenant_resolution() -> None:
    def explode_client(_installation_id: str) -> Any:
        raise AssertionError("deleted comments should not fetch GitHub comments")

    def explode_identity(_event: Any) -> Any:
        raise AssertionError("deleted comments should not resolve tenant identity")

    result = handle_issue_comment_feedback(
        _issue_comment_job(action="deleted"),
        conn=FakeFeedbackDb(),
        client_factory=explode_client,
        identity_resolver=explode_identity,
    )
    assert result["feedback_recorded"] is False
    assert result["reason"] == "comment_deleted"


# ---------------------------------------------------------------------------
# 5. Reaction poller mapping + self-skip + idempotency
# ---------------------------------------------------------------------------


def _review_record() -> CortexReviewComment:
    from cortex.hosted.github_comment import ReviewReplayMarker

    return CortexReviewComment(
        comment_id=CORTEX_COMMENT_ID,
        pr_number=PR_NUMBER,
        head_sha=HEAD_SHA,
        app_login=APP_LOGIN,
        replay=ReviewReplayMarker(
            model_id=MODEL_ID, prompt_version=PROMPT_VERSION, snapshot_hash=SNAPSHOT
        ),
    )


def test_reaction_poller_maps_up_down_and_skips_unmapped_and_self() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(
        reactions=(
            CommentReaction(content="+1", user_login="alice"),
            CommentReaction(content="-1", user_login="bob"),
            CommentReaction(content="eyes", user_login="carol"),  # unmapped
            CommentReaction(content="+1", user_login=APP_LOGIN),  # our own -> skip
        )
    )
    accounting = poll_comment_reactions(
        client,
        conn=db,
        tenant_id=TENANT_ID,
        review=_review_record(),
        repo_full_name=f"{OWNER}/{REPO}",
    )
    assert accounting["seen"] == 4
    assert accounting["recorded"] == 2  # alice up, bob down
    assert accounting["skipped_unmapped"] == 1  # eyes
    assert accounting["skipped_self"] == 1  # App's own +1
    kinds = sorted(row["feedback_kind"] for row in db.rows.values())
    assert kinds == ["reaction_down", "reaction_up"]
    sentiments = {row["actor_login"]: row["sentiment"] for row in db.rows.values()}
    assert sentiments == {"alice": "positive", "bob": "negative"}


def test_reaction_poller_is_idempotent_per_actor_content() -> None:
    db = FakeFeedbackDb()
    client = FakeGithubClient(reactions=(CommentReaction(content="+1", user_login="alice"),))
    first = poll_comment_reactions(
        client,
        conn=db,
        tenant_id=TENANT_ID,
        review=_review_record(),
        repo_full_name=f"{OWNER}/{REPO}",
    )
    second = poll_comment_reactions(
        client,
        conn=db,
        tenant_id=TENANT_ID,
        review=_review_record(),
        repo_full_name=f"{OWNER}/{REPO}",
    )
    assert first["recorded"] == 1
    assert second["recorded"] == 0
    assert second["duplicates"] == 1
    assert len(db.rows) == 1  # re-poll of the same reaction never double-counts


# ---------------------------------------------------------------------------
# 6. Payload parsing fail-closed
# ---------------------------------------------------------------------------


def test_parse_issue_comment_payload_reads_the_narrow_slice() -> None:
    event = parse_issue_comment_payload(_issue_comment_job().payload)
    assert event.installation_id == "55"
    assert event.owner == OWNER
    assert event.repo == REPO
    assert event.pr_number == PR_NUMBER
    assert event.comment_id == 9001
    assert event.actor_login == HUMAN_LOGIN
    assert event.action == "created"


def test_parse_issue_comment_payload_fails_closed_on_missing_fields() -> None:
    bad = {"body": {"installation": {"id": 55}, "repository": {"name": REPO}}}
    with pytest.raises(HostedJobError):
        parse_issue_comment_payload(bad)
