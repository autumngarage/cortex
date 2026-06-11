"""Tests for the human-ground-truth feedback corpus (cortex#394/#393).

The store dataclass, its kind/sentiment invariants, the idempotency keys, the
reaction mapping, and the append-only schema/migration are exercised offline; a
DATABASE_URL-gated test proves the migration applies idempotently against a
real Postgres and that the corpus binds to the replay identity.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from cortex.hosted.review_feedback import (
    MAX_REPLY_EXCERPT_CHARS,
    FeedbackKind,
    FeedbackSentiment,
    ReviewFeedbackError,
    ReviewFeedbackEvent,
    reaction_idempotency_key,
    reaction_kind_for_content,
    reply_idempotency_key,
    review_feedback_classify_pending_sql,
    review_feedback_insert_sql,
    sentiment_for_reaction_kind,
)
from cortex.hosted.schema import HOSTED_SCHEMA_VERSION

TENANT_ID = "11111111-1111-4111-8111-111111111111"
NODE_ID = "22222222-2222-4222-8222-222222222222"
SNAPSHOT = "a" * 64
WHEN = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


def _reply_event(**overrides: object) -> ReviewFeedbackEvent:
    kwargs: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "repo_full_name": "acme/app",
        "pr_number": 7,
        "head_sha": "abc1234",
        "cortex_comment_id": 5001,
        "model_id": "anthropic/claude-opus-4",
        "prompt_version": "evaluate-stage0/v1",
        "snapshot_hash": SNAPSHOT,
        "feedback_kind": FeedbackKind.REPLY,
        "actor_login": "octocat",
        "occurred_at": WHEN,
        "idempotency_key": "reply:5001:9001",
        "raw_excerpt": "This is wrong, the decision was superseded.",
    }
    kwargs.update(overrides)
    return ReviewFeedbackEvent(**kwargs)  # type: ignore[arg-type]


def _reaction_event(**overrides: object) -> ReviewFeedbackEvent:
    kwargs: dict[str, object] = {
        "tenant_id": TENANT_ID,
        "repo_full_name": "acme/app",
        "pr_number": 7,
        "head_sha": "abc1234",
        "cortex_comment_id": 5001,
        "model_id": "anthropic/claude-opus-4",
        "prompt_version": "evaluate-stage0/v1",
        "snapshot_hash": SNAPSHOT,
        "feedback_kind": FeedbackKind.REACTION_UP,
        "sentiment": FeedbackSentiment.POSITIVE,
        "actor_login": "octocat",
        "occurred_at": WHEN,
        "idempotency_key": "reaction:5001:octocat:+1",
    }
    kwargs.update(overrides)
    return ReviewFeedbackEvent(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Event shape + replay identity
# ---------------------------------------------------------------------------


def test_reply_event_carries_the_replay_identity_on_every_event() -> None:
    params = _reply_event().as_insert_parameters()
    assert params["model_id"] == "anthropic/claude-opus-4"
    assert params["prompt_version"] == "evaluate-stage0/v1"
    assert params["snapshot_hash"] == SNAPSHOT
    assert params["feedback_kind"] == "reply"
    assert params["sentiment"] == "unclassified"
    assert params["raw_excerpt"] == "This is wrong, the decision was superseded."


def test_reaction_event_carries_resolved_sentiment_and_no_excerpt() -> None:
    params = _reaction_event().as_insert_parameters()
    assert params["feedback_kind"] == "reaction_up"
    assert params["sentiment"] == "positive"
    assert params["raw_excerpt"] is None
    assert params["snapshot_hash"] == SNAPSHOT


def test_decision_node_and_finding_class_are_nullable() -> None:
    event = _reaction_event(decision_node_id=NODE_ID, finding_class="contradicts_prior_decision")
    params = event.as_insert_parameters()
    assert params["decision_node_id"] == NODE_ID
    assert params["finding_class"] == "contradicts_prior_decision"
    # And default to None.
    assert _reaction_event().as_insert_parameters()["decision_node_id"] is None
    assert _reaction_event().as_insert_parameters()["finding_class"] is None


def test_event_rejects_non_uuid_tenant() -> None:
    with pytest.raises(ReviewFeedbackError, match="tenant_id"):
        _reply_event(tenant_id="not-a-uuid")


def test_event_rejects_non_sha256_snapshot() -> None:
    with pytest.raises(ReviewFeedbackError, match="snapshot_hash"):
        _reaction_event(snapshot_hash="deadbeef")


def test_event_rejects_naive_timestamp() -> None:
    with pytest.raises(ReviewFeedbackError, match="timezone-aware"):
        _reply_event(occurred_at=datetime(2026, 6, 11, 12, 0, 0))


def test_event_rejects_non_positive_comment_id() -> None:
    with pytest.raises(ReviewFeedbackError, match="cortex_comment_id"):
        _reply_event(cortex_comment_id=0)


# ---------------------------------------------------------------------------
# 2. Kind <-> shape invariants (the discipline, enforced at construction)
# ---------------------------------------------------------------------------


def test_reply_must_carry_a_non_empty_excerpt() -> None:
    with pytest.raises(ReviewFeedbackError, match="non-empty raw_excerpt"):
        _reply_event(raw_excerpt="   ")


def test_reply_excerpt_is_content_bounded() -> None:
    with pytest.raises(ReviewFeedbackError, match="exceeds"):
        _reply_event(raw_excerpt="x" * (MAX_REPLY_EXCERPT_CHARS + 1))


def test_reply_must_stay_unclassified_this_module_never_labels_a_reply() -> None:
    with pytest.raises(ReviewFeedbackError, match="unclassified"):
        _reply_event(sentiment=FeedbackSentiment.POSITIVE)


def test_reaction_must_not_carry_an_excerpt() -> None:
    with pytest.raises(ReviewFeedbackError, match="must not carry a raw_excerpt"):
        _reaction_event(raw_excerpt="surprise text")


def test_reaction_must_carry_a_resolved_sentiment() -> None:
    with pytest.raises(ReviewFeedbackError, match="resolved sentiment"):
        _reaction_event(sentiment=FeedbackSentiment.UNCLASSIFIED)


# ---------------------------------------------------------------------------
# 3. Reaction mapping (+1 -> up, -1/confused -> down, others ignored)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("+1", FeedbackKind.REACTION_UP),
        ("heart", FeedbackKind.REACTION_UP),
        ("hooray", FeedbackKind.REACTION_UP),
        ("rocket", FeedbackKind.REACTION_UP),
        ("-1", FeedbackKind.REACTION_DOWN),
        ("confused", FeedbackKind.REACTION_DOWN),
    ],
)
def test_reaction_content_maps_to_a_kind(content: str, expected: FeedbackKind) -> None:
    assert reaction_kind_for_content(content) is expected


@pytest.mark.parametrize("content", ["eyes", "laugh", "", "   ", "shrug"])
def test_unmapped_reactions_are_ignored_never_invented(content: str) -> None:
    # eyes/laugh express engagement, not a judgment — we never invent a label.
    assert reaction_kind_for_content(content) is None


def test_reaction_kind_sentiment_directions() -> None:
    assert sentiment_for_reaction_kind(FeedbackKind.REACTION_UP) is FeedbackSentiment.POSITIVE
    assert sentiment_for_reaction_kind(FeedbackKind.REACTION_DOWN) is FeedbackSentiment.NEGATIVE


def test_reply_kind_has_no_implied_reaction_sentiment() -> None:
    with pytest.raises(ReviewFeedbackError, match="no implied reaction sentiment"):
        sentiment_for_reaction_kind(FeedbackKind.REPLY)


# ---------------------------------------------------------------------------
# 4. Idempotency keys
# ---------------------------------------------------------------------------


def test_reply_idempotency_key_is_stable_per_comment_pair() -> None:
    key = reply_idempotency_key(cortex_comment_id=5001, feedback_comment_id=9001)
    assert key == "reply:5001:9001"
    # Same reply text on a different review never collides.
    other = reply_idempotency_key(cortex_comment_id=6001, feedback_comment_id=9001)
    assert other != key


def test_reaction_idempotency_key_collapses_re_polls_not_distinct_reactions() -> None:
    first = reaction_idempotency_key(cortex_comment_id=5001, actor_login="octocat", content="+1")
    again = reaction_idempotency_key(cortex_comment_id=5001, actor_login="octocat", content="+1")
    assert first == again  # a re-poll of the same reaction does not double-count
    changed = reaction_idempotency_key(
        cortex_comment_id=5001, actor_login="octocat", content="heart"
    )
    assert changed != first  # 👍 -> ❤️ is a new human signal


def test_reaction_idempotency_key_rejects_blank_actor() -> None:
    with pytest.raises(ReviewFeedbackError, match="actor_login"):
        reaction_idempotency_key(cortex_comment_id=5001, actor_login="  ", content="+1")


# ---------------------------------------------------------------------------
# 5. Insert + classify-pending SQL
# ---------------------------------------------------------------------------


def test_insert_sql_is_idempotent_on_conflict() -> None:
    sql = review_feedback_insert_sql()
    assert "INSERT INTO cortex_hosted.review_feedback_events" in sql
    assert "ON CONFLICT (idempotency_key) DO NOTHING" in sql
    assert "RETURNING review_feedback_event_id" in sql


def test_classify_pending_sql_selects_only_unclassified_replies() -> None:
    sql = review_feedback_classify_pending_sql()
    assert "feedback_kind = 'reply'" in sql
    assert "sentiment = 'unclassified'" in sql
    assert "ORDER BY occurred_at ASC" in sql
    # The seam carries the replay identity so the cortex#549 classifier binds to
    # the regime it judges.
    assert "model_id" in sql and "prompt_version" in sql and "snapshot_hash" in sql


def test_insert_sql_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(ReviewFeedbackError, match="invalid SQL identifier"):
        review_feedback_insert_sql("cortex; DROP TABLE x")


# ---------------------------------------------------------------------------
# 6. Schema DDL: the append-only ground-truth corpus
# ---------------------------------------------------------------------------


def test_schema_defines_the_append_only_feedback_table() -> None:
    from cortex.hosted.schema import create_schema_sql

    sql = create_schema_sql()
    assert "CREATE TABLE IF NOT EXISTS cortex_hosted.review_feedback_events" in sql
    assert "CONSTRAINT review_feedback_events_idempotency_key_unique UNIQUE (idempotency_key)" in sql
    assert "prevent_review_feedback_mutation" in sql
    assert "BEFORE UPDATE ON cortex_hosted.review_feedback_events" in sql
    assert "BEFORE DELETE ON cortex_hosted.review_feedback_events" in sql
    # The schema comment states the human-ground-truth boundary plainly.
    assert "HUMAN-GROUND-TRUTH" in sql
    assert "never \"approval\"" in sql
    assert HOSTED_SCHEMA_VERSION == 9


def test_schema_enforces_kind_shape_invariant_in_the_db() -> None:
    from cortex.hosted.schema import create_schema_sql

    sql = create_schema_sql()
    # A reply must carry text and stay unclassified; a reaction must not carry
    # text and must carry a resolved sentiment — the DB mirrors the dataclass.
    assert "feedback_kind = 'reply' AND raw_excerpt IS NOT NULL AND sentiment = 'unclassified'" in sql
    assert "feedback_kind <> 'reply' AND raw_excerpt IS NULL AND sentiment <> 'unclassified'" in sql


# ---------------------------------------------------------------------------
# 7. DATABASE_URL-gated migration + binding proof
# ---------------------------------------------------------------------------


DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set DATABASE_URL to a Postgres with pgcrypto/pg_trgm/vector to run feedback integration",
)
def test_migration_applies_idempotently_and_binds_to_replay_identity() -> None:
    from cortex.hosted.db import connect
    from cortex.hosted.migrations import apply_schema

    conn = connect(DATABASE_URL)
    try:
        # Idempotent: applying twice lands at the same version, no error.
        first = apply_schema(conn)
        second = apply_schema(conn)
        assert first.version == HOSTED_SCHEMA_VERSION
        assert second.already_current is True

        # Insert + idempotent re-insert proves the corpus binds to the replay
        # identity and collapses redeliveries. The whole probe runs in one
        # transaction rolled back at the end — the append-only corpus cannot be
        # cleaned up with DELETE (the trigger blocks it), so the test commits
        # nothing and leaves the table untouched.
        key = "reaction:int-test:octocat:+1"
        event = _reaction_event(idempotency_key=key)
        inserted = conn.execute(
            review_feedback_insert_sql(), event.as_insert_parameters()
        ).fetchone()
        assert inserted is not None
        redelivery = conn.execute(
            review_feedback_insert_sql(), event.as_insert_parameters()
        ).fetchone()
        assert redelivery is None  # ON CONFLICT DO NOTHING held within the txn

        row = conn.execute(
            "SELECT model_id, prompt_version, snapshot_hash, feedback_kind, sentiment "
            "FROM cortex_hosted.review_feedback_events WHERE idempotency_key = %(k)s",
            {"k": key},
        ).fetchone()
        assert row is not None
        assert row[0] == "anthropic/claude-opus-4"
        assert row[2] == SNAPSHOT
        assert row[3] == "reaction_up"
        assert row[4] == "positive"
    finally:
        # Roll back the probe so the integration DB keeps no test rows; the
        # append-only trigger forbids DELETE, so rollback is the only clean exit.
        conn.rollback()
        conn.close()
