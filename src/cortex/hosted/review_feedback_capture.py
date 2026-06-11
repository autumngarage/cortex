"""Capture human feedback on Compass Review comments (cortex#393/#394).

Two capture paths feed the one append-only ground-truth corpus
(:mod:`cortex.hosted.review_feedback`):

1. **Reply capture (webhook-driven).** A ``github.issue_comment`` job arrives
   when a human comments on a PR. :func:`handle_issue_comment_feedback` decides
   whether that comment is feedback on a Cortex review — the PR carries a Cortex
   review comment (one of OUR App's comments, found by the hidden marker) — and
   if so writes a ``feedback_kind=reply`` event bound to that review's replay
   identity. A comment authored by our own App is ignored (the recursion guard:
   we never treat our own output as feedback on ourselves). A comment on a PR
   with no Cortex review is a visible no-op result, not an error.

2. **Reaction capture (poll-driven).** Reactions emit no webhook, so
   :func:`poll_comment_reactions` reads the reactions on a recently-posted
   Cortex comment and writes ``reaction_up`` / ``reaction_down`` events,
   idempotent per ``(comment_id, actor_login, content)``. The App's own
   reactions are skipped.

THE DISCIPLINE (load-bearing, product/technical vision principle 9):

- Every event is a real human action. The ABSENCE of a reaction or reply is
  *missing* feedback, never *approval* — nothing here writes an event for a
  comment no human has touched, and silence is never a positive label.
- A reply is stored verbatim (content-bounded) as ``sentiment=unclassified``;
  the cortex#549 converse-role classifier fills sentiment later. No model is
  called here.

The recursion guard derives the App's own login from the matched Cortex review
comment itself: the comment that carries our hidden marker was authored by our
App, so its ``user.login`` IS our App login. Any feedback comment / reaction by
that same login is ignored. No extra configuration is needed for the guard to
hold.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from cortex.hosted.db import HostedConnection
from cortex.hosted.github_app_auth import CommentReaction
from cortex.hosted.github_comment import (
    ReviewReplayMarker,
    extract_marker,
    extract_replay_marker,
)
from cortex.hosted.jobs import ClaimedJob, HostedJobError
from cortex.hosted.review_feedback import (
    MAX_REPLY_EXCERPT_CHARS,
    FeedbackKind,
    ReviewFeedbackError,
    ReviewFeedbackEvent,
    reaction_idempotency_key,
    reaction_kind_for_content,
    reply_idempotency_key,
    review_feedback_insert_sql,
    sentiment_for_reaction_kind,
)

logger = logging.getLogger("cortex.hosted.review_feedback_capture")


class FeedbackGithubClient(Protocol):
    """The narrow GitHub surface feedback capture reads.

    ``cortex.hosted.github_app_auth.GithubInstallationClient`` satisfies this
    structurally; typing against the protocol keeps the dependency to exactly
    the two reads feedback capture needs and lets tests inject a fake.
    """

    def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[Mapping[str, Any], ...]: ...

    def list_comment_reactions(
        self, owner: str, repo: str, comment_id: int
    ) -> tuple[CommentReaction, ...]: ...


@dataclass(frozen=True)
class CortexReviewComment:
    """The Cortex review comment a piece of feedback refers to.

    Resolved by scanning a PR's comments for OUR hidden marker. Carries the
    replay identity (model/prompt/snapshot) the feedback binds to, the comment
    id reactions hang off, and the App login that authored it — the login the
    recursion guard compares against so we never treat our own output as
    feedback.
    """

    comment_id: int
    pr_number: int
    head_sha: str
    app_login: str
    replay: ReviewReplayMarker


def find_cortex_review_comment(
    client: FeedbackGithubClient,
    *,
    owner: str,
    repo: str,
    pr_number: int,
) -> CortexReviewComment | None:
    """Find the most recent Cortex review comment on a PR, or ``None``.

    Scans the PR's comments for OUR hidden marker (``extract_marker``) plus the
    replay marker (``extract_replay_marker``). Returns the latest such comment
    so feedback binds to the current review state. ``None`` means this PR has no
    Cortex review comment — a visible "nothing to attach feedback to" signal,
    not an error.

    A comment carrying the pr/head marker but no replay marker is skipped with a
    warning: we cannot bind feedback to a regime we cannot read, and guessing
    would corrupt the corpus.
    """

    comments = client.list_issue_comments(owner, repo, pr_number)
    best: CortexReviewComment | None = None
    best_id = -1
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str):
            continue
        marker = extract_marker(body)
        if marker is None:
            continue
        comment_id = _comment_id(comment)
        if comment_id is None:
            continue
        app_login = _comment_author_login(comment)
        if app_login is None:
            # Our own comment must name its author; a marker-bearing comment
            # without an author login cannot anchor the recursion guard, so it
            # is skipped visibly rather than trusted.
            _log(
                "feedback.review_comment_missing_author",
                pr_number=pr_number,
                comment_id=comment_id,
            )
            continue
        replay = extract_replay_marker(body)
        if replay is None:
            _log(
                "feedback.review_comment_missing_replay_marker",
                pr_number=pr_number,
                comment_id=comment_id,
            )
            continue
        if comment_id > best_id:
            best_id = comment_id
            best = CortexReviewComment(
                comment_id=comment_id,
                pr_number=marker.pr_number,
                head_sha=marker.head_sha,
                app_login=app_login,
                replay=replay,
            )
    return best


def handle_issue_comment_feedback(
    job: ClaimedJob,
    *,
    conn: HostedConnection,
    client_factory: Callable[[str], FeedbackGithubClient],
    tenant_id: str,
) -> Mapping[str, Any]:
    """Capture a PR comment as reply feedback on a Cortex review, if it is one.

    Wired into ``build_review_registry(issue_comment_handler=...)`` and chosen
    in ``build_worker_registry``. The flow, fail-closed at every gate:

    1. Parse the ``github.issue_comment`` payload (fail-closed on a malformed
       delivery — a real failure the worker retries/dead-letters).
    2. Find OUR Cortex review comment on that PR by the hidden marker. None ->
       visible no-op (``feedback_recorded=False, reason=no_cortex_review``).
    3. Recursion guard: a comment authored by our own App login is ignored
       (``reason=self_authored``) — we never treat our own output as feedback.
    4. Write one ``feedback_kind=reply`` event bound to the review's replay
       identity, idempotent on ``(cortex_comment_id, feedback_comment_id)``.

    A no-op (no review, our own comment) returns a result naming why — it is
    visible, never silent, and never an exception (the job succeeded; there was
    simply nothing to record).
    """

    parsed = parse_issue_comment_payload(job.payload)
    if parsed.action == "deleted":
        # A deleted comment is not new feedback; recording one would attribute a
        # human action that was retracted. Visible no-op.
        return {
            "handled": True,
            "feedback_recorded": False,
            "reason": "comment_deleted",
            "comment_id": parsed.comment_id,
        }

    client = client_factory(parsed.installation_id)
    review = find_cortex_review_comment(
        client, owner=parsed.owner, repo=parsed.repo, pr_number=parsed.pr_number
    )
    if review is None:
        return {
            "handled": True,
            "feedback_recorded": False,
            "reason": "no_cortex_review",
            "pr_number": parsed.pr_number,
        }
    if parsed.comment_id == review.comment_id or _same_login(
        parsed.actor_login, review.app_login
    ):
        # Recursion guard: the comment is our own review comment, or authored by
        # our own App. We never record feedback on ourselves.
        return {
            "handled": True,
            "feedback_recorded": False,
            "reason": "self_authored",
            "actor_login": parsed.actor_login,
        }

    excerpt = _bound_excerpt(parsed.body)
    if excerpt is None:
        # A blank reply carries no human signal; recording an empty excerpt
        # would be a row with nothing to classify. Visible no-op.
        return {
            "handled": True,
            "feedback_recorded": False,
            "reason": "empty_reply",
            "comment_id": parsed.comment_id,
        }

    event = ReviewFeedbackEvent(
        tenant_id=tenant_id,
        repo_full_name=f"{parsed.owner}/{parsed.repo}",
        pr_number=parsed.pr_number,
        head_sha=review.head_sha,
        cortex_comment_id=review.comment_id,
        model_id=review.replay.model_id,
        prompt_version=review.replay.prompt_version,
        snapshot_hash=review.replay.snapshot_hash,
        feedback_kind=FeedbackKind.REPLY,
        actor_login=parsed.actor_login,
        occurred_at=parsed.occurred_at,
        idempotency_key=reply_idempotency_key(
            cortex_comment_id=review.comment_id,
            feedback_comment_id=parsed.comment_id,
        ),
        raw_excerpt=excerpt,
    )
    recorded = _insert_event(conn, event)
    _log(
        "feedback.reply_captured",
        pr_number=parsed.pr_number,
        cortex_comment_id=review.comment_id,
        feedback_comment_id=parsed.comment_id,
        actor_login=parsed.actor_login,
        recorded=recorded,
    )
    return {
        "handled": True,
        "feedback_recorded": recorded,
        "reason": "reply_recorded" if recorded else "already_recorded",
        "feedback_kind": FeedbackKind.REPLY.value,
        "cortex_comment_id": review.comment_id,
        "feedback_comment_id": parsed.comment_id,
    }


def poll_comment_reactions(
    client: FeedbackGithubClient,
    *,
    conn: HostedConnection,
    tenant_id: str,
    review: CortexReviewComment,
    repo_full_name: str,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Sweep reactions on one Cortex review comment into the feedback corpus.

    Reactions have no webhook, so a scheduled sweep calls this for a
    recently-posted Cortex comment. Each mapped reaction (``+1``/``heart``/
    ``hooray``/``rocket`` -> up; ``-1``/``confused`` -> down) becomes one
    feedback event, idempotent on ``(comment_id, actor_login, content)`` so a
    re-poll never double-counts. Unmapped reactions (``eyes``, ``laugh``) and
    the App's own reactions are skipped — we never invent a label or react to
    ourselves. The absence of any reaction writes nothing: missing is not
    approval.

    Returns a visible accounting: how many reactions were seen, recorded,
    duplicates, skipped-self, and skipped-unmapped.
    """

    reactions = client.list_comment_reactions(
        *_split_repo(repo_full_name), review.comment_id
    )
    seen = 0
    recorded = 0
    duplicates = 0
    skipped_self = 0
    skipped_unmapped = 0
    when = occurred_at or datetime.now(UTC)
    for reaction in reactions:
        seen += 1
        if _same_login(reaction.user_login, review.app_login):
            skipped_self += 1
            continue
        kind = reaction_kind_for_content(reaction.content)
        if kind is None:
            skipped_unmapped += 1
            continue
        event = ReviewFeedbackEvent(
            tenant_id=tenant_id,
            repo_full_name=repo_full_name,
            pr_number=review.pr_number,
            head_sha=review.head_sha,
            cortex_comment_id=review.comment_id,
            model_id=review.replay.model_id,
            prompt_version=review.replay.prompt_version,
            snapshot_hash=review.replay.snapshot_hash,
            feedback_kind=kind,
            sentiment=sentiment_for_reaction_kind(kind),
            actor_login=reaction.user_login,
            occurred_at=when,
            idempotency_key=reaction_idempotency_key(
                cortex_comment_id=review.comment_id,
                actor_login=reaction.user_login,
                content=reaction.content,
            ),
        )
        if _insert_event(conn, event):
            recorded += 1
        else:
            duplicates += 1
    _log(
        "feedback.reactions_polled",
        cortex_comment_id=review.comment_id,
        seen=seen,
        recorded=recorded,
        duplicates=duplicates,
        skipped_self=skipped_self,
        skipped_unmapped=skipped_unmapped,
    )
    return {
        "seen": seen,
        "recorded": recorded,
        "duplicates": duplicates,
        "skipped_self": skipped_self,
        "skipped_unmapped": skipped_unmapped,
    }


# ---------------------------------------------------------------------------
# Payload parsing (fail-closed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IssueCommentEvent:
    """The narrow slice of a ``github.issue_comment`` body feedback capture needs."""

    installation_id: str
    owner: str
    repo: str
    pr_number: int
    comment_id: int
    actor_login: str
    body: str
    occurred_at: datetime
    action: str


def parse_issue_comment_payload(payload: Mapping[str, Any]) -> IssueCommentEvent:
    """Parse a ``github.issue_comment`` webhook body, fail-closed.

    The worker's ``ClaimedJob.payload`` carries the webhook envelope; the issue
    comment body lives at the top level or under ``body`` (the shape the Stage 1
    ``ArrivalRecorder`` and stateless reviewer both read). A delivery missing
    the installation/repo/issue/comment fields cannot be attributed and is
    refused before any GitHub read.
    """

    if not isinstance(payload, Mapping):
        raise HostedJobError("github.issue_comment payload must be a JSON object")
    body = payload.get("body")
    event_body: Mapping[str, Any] = body if isinstance(body, Mapping) else payload

    installation = _require_mapping(event_body, "installation")
    repository = _require_mapping(event_body, "repository")
    owner = _require_mapping(repository, "owner")
    issue = _require_mapping(event_body, "issue")
    comment = _require_mapping(event_body, "comment")
    user = _require_mapping(comment, "user")

    return IssueCommentEvent(
        installation_id=_require_scalar_str(installation, "id"),
        owner=_require_str(owner, "login"),
        repo=_require_str(repository, "name"),
        pr_number=_require_int(issue, "number"),
        comment_id=_require_int(comment, "id"),
        actor_login=_require_str(user, "login"),
        body=_require_comment_body(comment),
        occurred_at=_parse_timestamp(comment, "created_at"),
        action=_optional_str(event_body, "action"),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _insert_event(conn: HostedConnection, event: ReviewFeedbackEvent) -> bool:
    """Append one feedback event; return whether a row was inserted.

    ``ON CONFLICT DO NOTHING`` means a redelivery/re-poll returns no row — that
    is ``False`` (already recorded), not a failure. A DB error propagates: a
    database that cannot persist a human judgment is a real failure, not a
    degradation, and the worker retries/dead-letters it visibly.
    """

    row = conn.execute(review_feedback_insert_sql(), event.as_insert_parameters()).fetchone()
    return row is not None


def _bound_excerpt(text: str) -> str | None:
    """Trim a reply body to the content bound, or ``None`` if it is blank.

    Whitespace-only replies carry no human signal and are dropped (the caller
    treats this as a visible no-op). A long reply is truncated to the corpus
    bound so a pasted log cannot bloat the ground-truth store.
    """

    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_REPLY_EXCERPT_CHARS:
        return stripped[:MAX_REPLY_EXCERPT_CHARS]
    return stripped


def _split_repo(repo_full_name: str) -> tuple[str, str]:
    owner, sep, repo = repo_full_name.partition("/")
    if not sep or not owner.strip() or not repo.strip():
        raise ReviewFeedbackError(
            f"repo_full_name must be 'owner/repo', got {repo_full_name!r}"
        )
    return owner, repo


def _same_login(left: str, right: str) -> bool:
    """Case-insensitive GitHub login comparison (logins are case-insensitive)."""

    return left.strip().lower() == right.strip().lower()


def _comment_id(comment: Mapping[str, Any]) -> int | None:
    value = comment.get("id")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _comment_author_login(comment: Mapping[str, Any]) -> str | None:
    user = comment.get("user")
    if not isinstance(user, Mapping):
        return None
    login = user.get("login")
    if not isinstance(login, str) or not login.strip():
        return None
    return login


def _require_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise HostedJobError(
            f"github.issue_comment payload field {key!r} must be a JSON object"
        )
    return value


def _require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HostedJobError(
            f"github.issue_comment payload field {key!r} must be a non-empty string"
        )
    return value


def _optional_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _require_scalar_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        raise HostedJobError(f"github.issue_comment payload field {key!r} is missing")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value
    raise HostedJobError(
        f"github.issue_comment payload field {key!r} must be a string or integer"
    )


def _require_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HostedJobError(
            f"github.issue_comment payload field {key!r} must be a positive integer"
        )
    return value


def _require_comment_body(comment: Mapping[str, Any]) -> str:
    value = comment.get("body")
    # A comment with an empty/missing body is not malformed (GitHub allows an
    # empty edit); it carries no reply text, so it is returned as "" and the
    # handler treats it as a visible no-op rather than a parse failure.
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HostedJobError("github.issue_comment payload comment.body must be a string")
    return value


def _parse_timestamp(comment: Mapping[str, Any], key: str) -> datetime:
    raw = comment.get(key)
    if not isinstance(raw, str) or not raw.strip():
        # No timestamp on the comment: fall back to now() (the capture time)
        # rather than refusing — the event still binds to the right comment and
        # replay regime; only the human-action timestamp is approximate, and
        # that is visible (recorded_at vs occurred_at coincide).
        return datetime.now(UTC)
    candidate = raw.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _log(event: str, **fields: Any) -> None:
    import json

    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))
