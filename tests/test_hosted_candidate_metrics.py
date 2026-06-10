"""Tests for candidate-set quality metrics (cortex#341)."""

from __future__ import annotations

import pytest

from cortex.hosted.candidate_metrics import (
    DEFAULT_REPORT_KS,
    CandidateMetricsValidationError,
    CandidateSetMetrics,
    LabeledCandidatePack,
    aggregate_candidate_set_metrics,
    compute_candidate_set_metrics,
)
from cortex.hosted.decisions_for_diff import (
    MAX_DECISIONS_FOR_DIFF_LIMIT,
    DecisionsForDiffCandidatePack,
    DecisionsForDiffQuery,
    build_decisions_for_diff_candidate_pack,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_DOCUMENT_ID = "44444444-4444-4444-8444-444444444444"
NODE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NODE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NODE_C = "99999999-9999-4999-8999-999999999999"
NODE_MISSING = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
VERSION_A = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
VERSION_B = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
VERSION_C = "88888888-8888-4888-8888-888888888888"
GRAPH_HASH = "a" * 64
SPAN_HASH = "b" * 64


def _row(*, decision_node_id: str, decision_version_id: str, score: float) -> dict[str, object]:
    return {
        "candidate_pool_size": 5,
        "cited_spans": [
            {
                "span_hash": SPAN_HASH,
                "excerpt": "Confirmed hosted retrieval decisions must stay cited.",
                "permalink": "https://github.com/autumngarage/cortex/pull/481/files#diff",
                "source_document_id": SOURCE_DOCUMENT_ID,
                "source_id": SOURCE_ID,
            }
        ],
        "decision_node_id": decision_node_id,
        "decision_text": "Keep hosted retrieval cited and bounded.",
        "decision_version_id": decision_version_id,
        "fused_score": score,
        "graph_node_count": 10,
        "reason_codes": ["scope:path:src/cortex/hosted/schema.py"],
        "status": "confirmed",
    }


def _pack(diff_text: str = "touch hosted retrieval") -> DecisionsForDiffCandidatePack:
    query = DecisionsForDiffQuery(
        tenant_id=TENANT_ID,
        diff_text=diff_text,
        visible_source_ids=(SOURCE_ID,),
    )
    rows = [
        _row(decision_node_id=NODE_A, decision_version_id=VERSION_A, score=4.2),
        _row(decision_node_id=NODE_B, decision_version_id=VERSION_B, score=3.1),
        _row(decision_node_id=NODE_C, decision_version_id=VERSION_C, score=1.7),
    ]
    return build_decisions_for_diff_candidate_pack(
        query=query, graph_snapshot_hash=GRAPH_HASH, rows=rows
    )


def test_recall_arithmetic_and_companion_metrics() -> None:
    pack = _pack()
    metrics = compute_candidate_set_metrics(
        pack=pack, relevant_decision_ids=(NODE_A, NODE_C, NODE_MISSING)
    )
    # recall_at_k = |relevant ∩ candidates| / |relevant| = 2 / 3
    assert metrics.recall_at_k == pytest.approx(2 / 3)
    assert metrics.precision_at_k == pytest.approx(2 / MAX_DECISIONS_FOR_DIFF_LIMIT)
    assert metrics.reciprocal_rank == 1.0  # NODE_A is rank 1
    assert metrics.candidates_in_budget == 3
    assert metrics.relevant_present is True
    assert metrics.omitted_relevant_count == 1
    assert metrics.omitted_relevant_ids == (NODE_MISSING,)
    assert metrics.unavailable_reason is None
    # The metrics row binds to the pack it measured.
    assert metrics.query_hash == pack.query_hash
    assert metrics.candidate_set_hash == pack.candidate_set_hash
    # Truncation stays visible: the pack's omitted_counts travel with the row.
    assert dict(metrics.pack_omitted_counts) == dict(pack.omitted_counts)
    assert metrics.pack_omitted_counts["over_limit"] == 2


def test_zero_denominator_recall_is_none_with_reason_never_silent_zero() -> None:
    metrics = compute_candidate_set_metrics(pack=_pack(), relevant_decision_ids=())
    assert metrics.recall_at_k is None
    assert metrics.precision_at_k is None
    assert metrics.reciprocal_rank is None
    assert metrics.unavailable_reason is not None
    assert "|relevant| = 0" in metrics.unavailable_reason
    assert metrics.relevant_present is False
    assert metrics.omitted_relevant_count == 0


def test_small_k_slices_ordering_while_presence_stays_pack_level() -> None:
    metrics = compute_candidate_set_metrics(
        pack=_pack(), relevant_decision_ids=(NODE_B,), k=1
    )
    # NODE_B is rank 2: outside the top-1 slice but inside the bounded pack.
    assert metrics.recall_at_k == 0.0
    assert metrics.precision_at_k == 0.0
    assert metrics.reciprocal_rank == 0.0
    assert metrics.relevant_present is True
    assert metrics.omitted_relevant_count == 0


def test_relevant_absent_from_pack_is_the_silent_failure_detector() -> None:
    metrics = compute_candidate_set_metrics(
        pack=_pack(), relevant_decision_ids=(NODE_MISSING,)
    )
    assert metrics.relevant_present is False
    assert metrics.omitted_relevant_count == 1
    assert metrics.recall_at_k == 0.0
    assert metrics.reciprocal_rank == 0.0


def test_presence_rate_aggregation_excludes_unlabeled_fixtures_visibly() -> None:
    items = (
        LabeledCandidatePack(
            fixture_id="fixture-present",
            pack=_pack("alpha diff"),
            relevant_decision_ids=(NODE_A,),
        ),
        LabeledCandidatePack(
            fixture_id="fixture-absent",
            pack=_pack("beta diff"),
            relevant_decision_ids=(NODE_MISSING,),
        ),
        LabeledCandidatePack(
            fixture_id="fixture-unlabeled",
            pack=_pack("gamma diff"),
            relevant_decision_ids=(),
        ),
    )
    report = aggregate_candidate_set_metrics(items)
    assert report.fixtures_total == 3
    assert report.fixtures_with_relevant == 2
    assert report.fixtures_without_relevant == 1
    assert report.fixtures_with_relevant_present == 1
    # presence_rate = |fixtures where relevant_present| / |fixtures with a
    # non-empty relevant set| = 1 / 2; the unlabeled fixture never deflates it.
    assert report.presence_rate == pytest.approx(0.5)
    assert report.presence_rate_unavailable_reason is None
    # MRR over defined rows only: (1.0 + 0.0) / 2.
    assert report.mean_reciprocal_rank == pytest.approx(0.5)
    assert [row.fixture_id for row in report.rows] == [
        "fixture-absent",
        "fixture-present",
        "fixture-unlabeled",
    ]


def test_batch_aggregation_is_deterministic_under_input_order() -> None:
    def _items() -> tuple[LabeledCandidatePack, ...]:
        return (
            LabeledCandidatePack(
                fixture_id="fixture-present",
                pack=_pack("alpha diff"),
                relevant_decision_ids=(NODE_A,),
            ),
            LabeledCandidatePack(
                fixture_id="fixture-absent",
                pack=_pack("beta diff"),
                relevant_decision_ids=(NODE_MISSING,),
            ),
            LabeledCandidatePack(
                fixture_id="fixture-unlabeled",
                pack=_pack("gamma diff"),
                relevant_decision_ids=(),
            ),
        )

    forward = aggregate_candidate_set_metrics(_items())
    reversed_input = aggregate_candidate_set_metrics(tuple(reversed(_items())))
    assert forward == reversed_input
    assert [row.fixture_id for row in forward.rows] == [
        row.fixture_id for row in reversed_input.rows
    ]


def test_duplicate_fixture_ids_are_rejected() -> None:
    item = LabeledCandidatePack(
        fixture_id="fixture-dup", pack=_pack(), relevant_decision_ids=(NODE_A,)
    )
    with pytest.raises(CandidateMetricsValidationError, match="duplicate fixture_id"):
        aggregate_candidate_set_metrics((item, item))


def test_empty_batch_reports_reasons_not_silent_rates() -> None:
    report = aggregate_candidate_set_metrics(())
    assert report.fixtures_total == 0
    assert report.presence_rate is None
    assert report.presence_rate_unavailable_reason is not None
    assert report.mean_reciprocal_rank is None
    assert report.mean_reciprocal_rank_unavailable_reason is not None


def test_invalid_k_and_invalid_relevant_ids_are_rejected() -> None:
    with pytest.raises(CandidateMetricsValidationError, match="k must be >= 1"):
        compute_candidate_set_metrics(pack=_pack(), relevant_decision_ids=(NODE_A,), k=0)
    with pytest.raises(CandidateMetricsValidationError, match="non-empty strings"):
        compute_candidate_set_metrics(pack=_pack(), relevant_decision_ids=("",))


def test_inconsistent_metrics_rows_are_unrepresentable() -> None:
    pack = _pack()
    metrics = compute_candidate_set_metrics(pack=pack, relevant_decision_ids=(NODE_A,))
    # A silent 0.0 next to an unavailable_reason cannot be constructed.
    with pytest.raises(CandidateMetricsValidationError, match="unavailable_reason"):
        CandidateSetMetrics(
            query_hash=metrics.query_hash,
            candidate_set_hash=metrics.candidate_set_hash,
            k=metrics.k,
            candidates_in_budget=metrics.candidates_in_budget,
            relevant_decision_ids=metrics.relevant_decision_ids,
            present_relevant_ids=metrics.present_relevant_ids,
            omitted_relevant_ids=metrics.omitted_relevant_ids,
            pack_omitted_counts=dict(metrics.pack_omitted_counts),
            recall_at_k=0.0,
            precision_at_k=0.0,
            reciprocal_rank=0.0,
            unavailable_reason="should not coexist with values",
        )
    # Wrong arithmetic cannot be constructed either.
    with pytest.raises(CandidateMetricsValidationError, match="recall_at_k must equal"):
        CandidateSetMetrics(
            query_hash=metrics.query_hash,
            candidate_set_hash=metrics.candidate_set_hash,
            k=metrics.k,
            candidates_in_budget=metrics.candidates_in_budget,
            relevant_decision_ids=metrics.relevant_decision_ids,
            present_relevant_ids=metrics.present_relevant_ids,
            omitted_relevant_ids=metrics.omitted_relevant_ids,
            pack_omitted_counts=dict(metrics.pack_omitted_counts),
            recall_at_k=0.25,
            precision_at_k=metrics.precision_at_k,
            reciprocal_rank=metrics.reciprocal_rank,
            unavailable_reason=None,
        )


def test_default_report_ks_cover_the_cap_and_a_smaller_probe() -> None:
    assert MAX_DECISIONS_FOR_DIFF_LIMIT in DEFAULT_REPORT_KS
    assert any(k < MAX_DECISIONS_FOR_DIFF_LIMIT for k in DEFAULT_REPORT_KS)
    report = aggregate_candidate_set_metrics(
        (
            LabeledCandidatePack(
                fixture_id="fixture-small-k",
                pack=_pack(),
                relevant_decision_ids=(NODE_C,),
            ),
        ),
        k=min(DEFAULT_REPORT_KS),
    )
    assert report.k == min(DEFAULT_REPORT_KS)
    assert report.rows[0].metrics.k == min(DEFAULT_REPORT_KS)
