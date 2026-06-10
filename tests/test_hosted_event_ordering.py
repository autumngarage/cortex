from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cortex.hosted.event_ordering import EventOrderingError, ordering_key, resolve_current
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
SPAN_HASH = "a" * 64

T0 = datetime(2026, 6, 9, 14, 0, tzinfo=UTC)


def _mapping_event(
    *,
    occurred_at: datetime,
    ingested_at: datetime | None,
    event_hash: str,
    label: str,
) -> dict[str, Any]:
    return {
        "occurred_at": occurred_at,
        "ingested_at": ingested_at,
        "event_hash": event_hash,
        "label": label,
    }


def _ledger_event(occurred_at: datetime, idempotency_key: str = "key-1") -> LedgerEvent:
    return LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.DECISION_SUPERSEDED,
        actor=ActorRef(actor_type="slack-user", actor_id="henry"),
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        payload={"decision_node_id": "node-1"},
        source_span_hashes=(SPAN_HASH,),
    )


def test_out_of_order_webhook_redelivery_resolves_by_source_timestamp() -> None:
    # The motivating case: the supersede was authored at 15:00 and its
    # webhook arrived promptly at 15:01; the decision it supersedes was
    # authored at 14:00 but *redelivered* at 15:30. By arrival order the old
    # decision would look current; by source order the supersede wins.
    confirm = _mapping_event(
        occurred_at=T0,
        ingested_at=T0 + timedelta(minutes=90),
        event_hash="c" * 64,
        label="decision.confirmed",
    )
    supersede = _mapping_event(
        occurred_at=T0 + timedelta(hours=1),
        ingested_at=T0 + timedelta(minutes=61),
        event_hash="d" * 64,
        label="decision.superseded",
    )

    assert resolve_current([confirm, supersede]) is supersede
    assert resolve_current([supersede, confirm]) is supersede


def test_equal_occurred_at_falls_to_ingested_at() -> None:
    # The later hash is given to the *earlier* arrival so the hash level
    # cannot be what decides this case.
    early_arrival = _mapping_event(
        occurred_at=T0, ingested_at=T0 + timedelta(minutes=1), event_hash="f" * 64, label="early"
    )
    late_arrival = _mapping_event(
        occurred_at=T0, ingested_at=T0 + timedelta(minutes=2), event_hash="0" * 64, label="late"
    )

    assert resolve_current([early_arrival, late_arrival]) is late_arrival
    assert resolve_current([late_arrival, early_arrival]) is late_arrival


def test_equal_occurred_and_ingested_falls_to_event_hash() -> None:
    low = _mapping_event(occurred_at=T0, ingested_at=T0, event_hash="0" * 64, label="low")
    high = _mapping_event(occurred_at=T0, ingested_at=T0, event_hash="f" * 64, label="high")

    assert resolve_current([low, high]) is high
    assert resolve_current([high, low]) is high


def test_total_order_is_identical_across_shuffles() -> None:
    events: list[dict[str, Any]] = []
    counter = 0
    for occurred_offset in (0, 1):
        for ingested_offset in (0, 1, None):
            for hash_char in ("1", "2"):
                events.append(
                    _mapping_event(
                        occurred_at=T0 + timedelta(minutes=occurred_offset),
                        ingested_at=(
                            None
                            if ingested_offset is None
                            else T0 + timedelta(minutes=ingested_offset)
                        ),
                        event_hash=hash_char * 64,
                        label=f"event-{counter}",
                    )
                )
                counter += 1

    rng = random.Random(313)
    shuffle_one = events[:]
    rng.shuffle(shuffle_one)
    shuffle_two = events[:]
    rng.shuffle(shuffle_two)

    once = sorted(shuffle_one, key=ordering_key)

    assert sorted(once, key=ordering_key) == once
    assert sorted(shuffle_two, key=ordering_key) == once
    assert resolve_current(shuffle_one) == once[-1]


def test_ordering_key_accepts_ledger_event_and_mapping_inputs() -> None:
    event = _ledger_event(T0)
    equivalent_mapping = {
        "occurred_at": event.occurred_at,
        "ingested_at": None,
        "event_hash": event.event_hash,
    }

    assert ordering_key(event) == ordering_key(equivalent_mapping)


def test_unpersisted_event_orders_after_persisted_arrival_at_same_instant() -> None:
    # A LedgerEvent has no ingested_at until the database assigns one; its
    # arrival has not happened yet, so it orders after every recorded
    # arrival at the same source instant.
    unpersisted = _ledger_event(T0)
    persisted = _mapping_event(
        occurred_at=T0, ingested_at=T0 + timedelta(minutes=5), event_hash="f" * 64, label="stored"
    )

    assert resolve_current([persisted, unpersisted]) is unpersisted

    # But a later source timestamp still beats the unpersisted event:
    # arrival state never outranks occurred_at.
    later_source = _mapping_event(
        occurred_at=T0 + timedelta(minutes=1),
        ingested_at=T0 + timedelta(minutes=2),
        event_hash="0" * 64,
        label="later",
    )

    assert resolve_current([unpersisted, later_source]) is later_source


def test_missing_occurred_at_fails_closed() -> None:
    with pytest.raises(EventOrderingError, match="occurred_at"):
        ordering_key({"ingested_at": T0, "event_hash": "a" * 64})
    with pytest.raises(EventOrderingError, match="occurred_at"):
        ordering_key(object())


def test_naive_timestamps_fail_closed() -> None:
    with pytest.raises(EventOrderingError, match="occurred_at must be a timezone-aware"):
        ordering_key(
            {"occurred_at": datetime(2026, 6, 9, 14, 0), "event_hash": "a" * 64}
        )
    with pytest.raises(EventOrderingError, match="ingested_at must be a timezone-aware"):
        ordering_key(
            {
                "occurred_at": T0,
                "ingested_at": datetime(2026, 6, 9, 14, 0),
                "event_hash": "a" * 64,
            }
        )


def test_missing_or_malformed_event_hash_fails_closed() -> None:
    with pytest.raises(EventOrderingError, match="event_hash"):
        ordering_key({"occurred_at": T0, "ingested_at": T0})
    with pytest.raises(EventOrderingError, match="sha256"):
        ordering_key({"occurred_at": T0, "ingested_at": T0, "event_hash": "not-a-hash"})


def test_resolve_current_refuses_empty_group() -> None:
    with pytest.raises(EventOrderingError, match="at least one event"):
        resolve_current([])
