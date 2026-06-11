"""Human-ground-truth feedback on Compass Review comments (cortex#394/#393).

This is the flywheel keystone: the append-only corpus of **human** judgments
on the advisory comments Compass Review posts on a PR. Each event is keyed to
the exact replay identity that produced the comment it judges — the
``(model_id, prompt_version, graph_snapshot_hash)`` stamp plus the PR head SHA
— so a later evaluator improvement can be measured against precisely what the
human reacted to, never a blended regime.

THE DISCIPLINE (product/technical vision principle 9, load-bearing):

- **Feedback is human ground truth only.** Every event here records a real
  human action: a reaction (👍/👎) or a reply on a Cortex review comment. The
  model's own predictions are never written here — training on our own output
  is the failure mode this corpus exists to avoid.
- **Absence is never approval.** A comment with no human reaction is
  ``missing`` feedback, not a positive label. Nothing in this module
  synthesizes a positive event from silence; the absence of a row IS the
  signal that no human has judged that comment yet.
- **Replies are stored, classified later.** A reply is captured verbatim
  (content-bounded) with ``sentiment = unclassified``. The converse-role
  sentiment classifier (cortex#549) fills ``sentiment`` in a follow-up via the
  ``review_feedback_classify_pending_sql`` seam — this module never calls a
  model.

Relationship to ``ledger_events`` / ``FEEDBACK_RECORDED``. The hosted ledger
carries a ``FEEDBACK_RECORDED`` event type for graph-affecting feedback inside
a tenant's decision ledger (confirm/reject/supersede actions that mutate the
projection). This table is a *different* concern: it is the
operator-internal, cross-tenant **evaluator training corpus** of raw human
reactions on advisory review comments, keyed to the review's replay stamps
rather than to a decision node. Keeping it separate means a reaction on a
review comment never has to manufacture a graph mutation, and the corpus stays
queryable per ``(model_id, prompt_version, snapshot_hash)`` without walking the
decision ledger. It is INTERNAL ground-truth, never a customer surface.

The matching DDL lives in ``cortex.hosted.schema.create_schema_sql`` (schema
``v9``); this module owns the row dataclass, the feedback-kind / sentiment
vocabularies, and the idempotent insert + classify-pending SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

# A reply excerpt is content-bounded: a human reply is feedback, not a blob to
# warehouse. The bound keeps a pasted log or a giant comment from bloating the
# corpus while preserving the human's actual words for the classifier. Derived
# from the domain (a review reply is a sentence or two), not a storage limit.
MAX_REPLY_EXCERPT_CHARS = 4000


class ReviewFeedbackError(ValueError):
    """Raised when a feedback event would corrupt the ground-truth corpus."""


class FeedbackKind(StrEnum):
    """How the human expressed feedback on a Cortex review comment.

    Reactions are poll-captured (GitHub emits no reaction webhook); replies are
    webhook-captured. There is deliberately no "approval" kind that a system
    could synthesize — every value names a real human action.
    """

    REACTION_UP = "reaction_up"
    REACTION_DOWN = "reaction_down"
    REPLY = "reply"


class FeedbackSentiment(StrEnum):
    """The human's judgment, where known.

    A reaction maps directly (up -> positive, down -> negative). A reply lands
    as ``unclassified`` and is filled later by the cortex#549 converse-role
    classifier — never by a model call from this module. ``neutral`` exists for
    reactions that express engagement without a clear up/down (reserved; the
    reaction mapper currently emits only positive/negative/unclassified).
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"
    UNCLASSIFIED = "unclassified"


# GitHub reaction content -> feedback kind. ``+1``/``heart``/``hooray``/
# ``rocket`` are read as endorsement; ``-1``/``confused`` as disagreement.
# ``eyes``/``laugh`` are intentionally absent: they signal engagement, not a
# clear up/down judgment, so they are NOT mapped to a feedback event (mapping
# them would invent a label the human did not give). The map is the one place
# this policy lives so the poller and the tests cannot drift.
REACTION_CONTENT_TO_KIND: dict[str, FeedbackKind] = {
    "+1": FeedbackKind.REACTION_UP,
    "heart": FeedbackKind.REACTION_UP,
    "hooray": FeedbackKind.REACTION_UP,
    "rocket": FeedbackKind.REACTION_UP,
    "-1": FeedbackKind.REACTION_DOWN,
    "confused": FeedbackKind.REACTION_DOWN,
}

# The sentiment each reaction kind implies. A reply carries no implied
# sentiment (it is classified later), so it is absent here and defaults to
# ``UNCLASSIFIED`` at construction.
_REACTION_KIND_TO_SENTIMENT: dict[FeedbackKind, FeedbackSentiment] = {
    FeedbackKind.REACTION_UP: FeedbackSentiment.POSITIVE,
    FeedbackKind.REACTION_DOWN: FeedbackSentiment.NEGATIVE,
}


def reaction_kind_for_content(content: str) -> FeedbackKind | None:
    """Map a GitHub reaction content string to a feedback kind, or ``None``.

    ``None`` means "this reaction is not a judgment we record" (e.g. ``eyes``,
    ``laugh``) — the caller skips it rather than inventing a label. A blank or
    unknown content is also ``None``: we never guess a human's intent.
    """

    if not isinstance(content, str):
        return None
    return REACTION_CONTENT_TO_KIND.get(content.strip())


def sentiment_for_reaction_kind(kind: FeedbackKind) -> FeedbackSentiment:
    """Return the sentiment a reaction kind implies (fail-closed on a reply)."""

    sentiment = _REACTION_KIND_TO_SENTIMENT.get(kind)
    if sentiment is None:
        raise ReviewFeedbackError(
            f"{kind.value} has no implied reaction sentiment; only "
            "reaction_up/reaction_down carry a direct sentiment (a reply is "
            "classified later, never mapped here)"
        )
    return sentiment


@dataclass(frozen=True)
class ReviewFeedbackEvent:
    """One human judgment on a Cortex review comment, bound to its replay key.

    The replay stamps (``model_id``, ``prompt_version``, ``snapshot_hash``) are
    the exact identity of the model run that produced the reviewed comment, so
    a label can never be attributed to the wrong regime. ``decision_node_id``
    and ``finding_class`` are nullable: a reaction targets the *comment* (the
    whole review) and may not single out one finding, while a reply may target
    the review as a whole. ``raw_excerpt`` carries a reply's verbatim text
    (content-bounded) and is ``None`` for reactions, which have no text.

    Frozen and validated at construction: a malformed event is refused before
    it can enter the append-only corpus.
    """

    tenant_id: str
    repo_full_name: str
    pr_number: int
    head_sha: str
    cortex_comment_id: int
    model_id: str
    prompt_version: str
    snapshot_hash: str
    feedback_kind: FeedbackKind
    actor_login: str
    occurred_at: datetime
    idempotency_key: str
    decision_node_id: str | None = None
    finding_class: str | None = None
    sentiment: FeedbackSentiment = FeedbackSentiment.UNCLASSIFIED
    raw_excerpt: str | None = None

    def __post_init__(self) -> None:
        try:
            UUID(self.tenant_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ReviewFeedbackError("tenant_id must be a UUID") from exc
        if self.decision_node_id is not None:
            try:
                UUID(self.decision_node_id)
            except (ValueError, AttributeError, TypeError) as exc:
                raise ReviewFeedbackError("decision_node_id must be a UUID or None") from exc
        for name, value in (
            ("repo_full_name", self.repo_full_name),
            ("head_sha", self.head_sha),
            ("model_id", self.model_id),
            ("prompt_version", self.prompt_version),
            ("actor_login", self.actor_login),
            ("idempotency_key", self.idempotency_key),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ReviewFeedbackError(f"{name} must be a non-empty string")
        if not isinstance(self.snapshot_hash, str) or not _SHA256_RE.match(self.snapshot_hash):
            raise ReviewFeedbackError("snapshot_hash must be a sha256 hex string")
        for int_name, int_value in (
            ("pr_number", self.pr_number),
            ("cortex_comment_id", self.cortex_comment_id),
        ):
            if isinstance(int_value, bool) or not isinstance(int_value, int) or int_value <= 0:
                raise ReviewFeedbackError(f"{int_name} must be a positive integer")
        if not isinstance(self.feedback_kind, FeedbackKind):
            raise ReviewFeedbackError("feedback_kind must be a FeedbackKind")
        if not isinstance(self.sentiment, FeedbackSentiment):
            raise ReviewFeedbackError("sentiment must be a FeedbackSentiment")
        if self.finding_class is not None and (
            not isinstance(self.finding_class, str) or not self.finding_class.strip()
        ):
            raise ReviewFeedbackError("finding_class must be a non-empty string or None")
        if not isinstance(self.occurred_at, datetime):
            raise ReviewFeedbackError("occurred_at must be a datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ReviewFeedbackError("occurred_at must be timezone-aware")
        self._validate_kind_invariants()

    def _validate_kind_invariants(self) -> None:
        """Enforce the kind <-> excerpt/sentiment shape rules.

        A reply must carry a non-blank, content-bounded excerpt and stays
        ``unclassified`` here (the classifier fills it later); a reaction has
        no text and must carry a resolved positive/negative sentiment. Keeping
        these as construction invariants means a malformed pairing (e.g. a
        reaction with a stored excerpt, or a reply pre-labeled positive by this
        module) cannot enter the corpus.
        """

        if self.feedback_kind is FeedbackKind.REPLY:
            if not isinstance(self.raw_excerpt, str) or not self.raw_excerpt.strip():
                raise ReviewFeedbackError("a reply feedback event must carry a non-empty raw_excerpt")
            if len(self.raw_excerpt) > MAX_REPLY_EXCERPT_CHARS:
                raise ReviewFeedbackError(
                    f"raw_excerpt exceeds {MAX_REPLY_EXCERPT_CHARS} chars; bound it before storing"
                )
            if self.sentiment is not FeedbackSentiment.UNCLASSIFIED:
                raise ReviewFeedbackError(
                    "a reply must be stored as sentiment=unclassified; the cortex#549 "
                    "classifier fills sentiment later — this module never labels a reply"
                )
        else:  # a reaction
            if self.raw_excerpt is not None:
                raise ReviewFeedbackError("a reaction feedback event must not carry a raw_excerpt")
            if self.sentiment is FeedbackSentiment.UNCLASSIFIED:
                raise ReviewFeedbackError(
                    "a reaction must carry a resolved sentiment (positive/negative); "
                    "unclassified is for replies awaiting cortex#549 classification"
                )

    def as_insert_parameters(self) -> dict[str, Any]:
        """Return DB-API named parameters for :func:`review_feedback_insert_sql`."""

        return {
            "tenant_id": self.tenant_id,
            "repo_full_name": self.repo_full_name,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "cortex_comment_id": self.cortex_comment_id,
            "decision_node_id": self.decision_node_id,
            "finding_class": self.finding_class,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "snapshot_hash": self.snapshot_hash,
            "feedback_kind": self.feedback_kind.value,
            "sentiment": self.sentiment.value,
            "raw_excerpt": self.raw_excerpt,
            "actor_login": self.actor_login,
            "occurred_at": self.occurred_at,
            "idempotency_key": self.idempotency_key,
        }


def reply_idempotency_key(*, cortex_comment_id: int, feedback_comment_id: int) -> str:
    """Stable idempotency key for a reply event.

    A reply IS a GitHub comment with its own immutable id, so the key is the
    pair ``(cortex_comment_id, feedback_comment_id)``: a webhook redelivery of
    the same reply collapses to one row, while two distinct replies on the same
    review are two rows. Keyed to the Cortex comment too so the same reply text
    on two different reviews never collides.
    """

    if isinstance(cortex_comment_id, bool) or not isinstance(cortex_comment_id, int):
        raise ReviewFeedbackError("cortex_comment_id must be an integer")
    if isinstance(feedback_comment_id, bool) or not isinstance(feedback_comment_id, int):
        raise ReviewFeedbackError("feedback_comment_id must be an integer")
    return f"reply:{cortex_comment_id}:{feedback_comment_id}"


def reaction_idempotency_key(
    *, cortex_comment_id: int, actor_login: str, content: str
) -> str:
    """Stable idempotency key for a reaction event.

    A reaction has no id of its own, so it is keyed by
    ``(cortex_comment_id, actor_login, content)``: the poller re-reads
    reactions on every sweep, so re-seeing the same person's same reaction must
    NOT write a second row. A person changing 👍 to ❤️ is a different content
    and therefore a new row — both are real human signals.
    """

    if isinstance(cortex_comment_id, bool) or not isinstance(cortex_comment_id, int):
        raise ReviewFeedbackError("cortex_comment_id must be an integer")
    if not actor_login.strip():
        raise ReviewFeedbackError("actor_login must be a non-empty string")
    if not content.strip():
        raise ReviewFeedbackError("content must be a non-empty string")
    return f"reaction:{cortex_comment_id}:{actor_login.strip()}:{content.strip()}"


def review_feedback_insert_sql(schema: str = "cortex_hosted") -> str:
    """Return the idempotent append statement for review feedback events.

    ``ON CONFLICT (idempotency_key) DO NOTHING`` so a webhook redelivery (reply)
    or a re-poll (reaction) never double-counts a single human action. Returns
    ``review_feedback_event_id`` when a row was inserted, nothing on conflict —
    so the caller can report ``recorded`` vs ``already_recorded`` honestly.
    """

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.review_feedback_events (
    tenant_id,
    repo_full_name,
    pr_number,
    head_sha,
    cortex_comment_id,
    decision_node_id,
    finding_class,
    model_id,
    prompt_version,
    snapshot_hash,
    feedback_kind,
    sentiment,
    raw_excerpt,
    actor_login,
    occurred_at,
    idempotency_key
) VALUES (
    %(tenant_id)s,
    %(repo_full_name)s,
    %(pr_number)s,
    %(head_sha)s,
    %(cortex_comment_id)s,
    %(decision_node_id)s,
    %(finding_class)s,
    %(model_id)s,
    %(prompt_version)s,
    %(snapshot_hash)s,
    %(feedback_kind)s,
    %(sentiment)s,
    %(raw_excerpt)s,
    %(actor_login)s,
    %(occurred_at)s,
    %(idempotency_key)s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING review_feedback_event_id;
""".strip()


def review_feedback_classify_pending_sql(schema: str = "cortex_hosted") -> str:
    """Return rows awaiting sentiment classification (the cortex#549 seam).

    A reply lands ``sentiment = unclassified``; the converse-role classifier
    (cortex#549) reads these and writes the sentiment in a follow-up. This is
    the read seam only — the classifier owns the write path. Ordered oldest
    first so the backlog drains in arrival order.

    TODO(cortex#549): the converse-role sentiment classifier consumes this
    query and fills ``sentiment`` for replies. Do NOT call a model from this
    module — that crosses the human-ground-truth boundary this corpus exists to
    protect.
    """

    _validate_sql_identifier(schema)
    return f"""
SELECT
    review_feedback_event_id,
    raw_excerpt,
    model_id,
    prompt_version,
    snapshot_hash
FROM {schema}.review_feedback_events
WHERE feedback_kind = 'reply'
  AND sentiment = 'unclassified'
ORDER BY occurred_at ASC
LIMIT %(limit)s;
""".strip()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ReviewFeedbackError(f"invalid SQL identifier: {name!r}")
