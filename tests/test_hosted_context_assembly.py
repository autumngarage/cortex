"""Tests for token-budgeted evaluation context assembly (cortex#330)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

import pytest

from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.context_assembly import (
    ESTIMATOR_VERSION,
    OVER_BUDGET_OMISSION_KEY,
    ContextAssemblyValidationError,
    EvaluationContext,
    TokenEstimator,
    assemble_evaluation_context,
    default_token_estimator,
    serialize_candidate_payload,
)
from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)

QUERY_HASH = hashlib.sha256(b"query").hexdigest()
GRAPH_HASH = hashlib.sha256(b"graph").hexdigest()


def _candidate(index: int, *, text: str) -> DecisionsForDiffCandidate:
    return DecisionsForDiffCandidate(
        decision_node_id=str(UUID(int=index * 2 + 1)),
        decision_version_id=str(UUID(int=index * 2 + 2)),
        status="confirmed",
        decision_text=text,
        score=float(100 - index),
        reason_codes=(f"scope:path:src/module_{index}.py",),
        cited_spans=(
            CitedSourceSpan(
                span_hash=hashlib.sha256(f"span-{index}".encode()).hexdigest(),
                excerpt=f"excerpt {index}: {text[:40]}",
                permalink=f"https://github.com/acme/payments/blob/main/docs/adr/{index:04d}.md",
                source_document_id=str(UUID(int=9000 + index)),
                source_id=str(UUID(int=7000 + index)),
            ),
        ),
    )


def _pack(
    texts: Sequence[str],
    *,
    omitted_counts: dict[str, int] | None = None,
    pool_extra: int = 0,
) -> DecisionsForDiffCandidatePack:
    candidates = tuple(_candidate(i, text=text) for i, text in enumerate(texts))
    return DecisionsForDiffCandidatePack(
        query_hash=QUERY_HASH,
        retrieval_config_version="decisions-for-diff-v2+test",
        graph_snapshot_hash=GRAPH_HASH,
        candidates=candidates,
        omitted_counts=omitted_counts if omitted_counts is not None else {"over_limit": 0},
        graph_node_count=50,
        candidate_pool_size=len(candidates) + pool_extra,
    )


def _costs(pack: DecisionsForDiffCandidatePack) -> list[int]:
    return [
        default_token_estimator.estimate_tokens(serialize_candidate_payload(candidate))
        for candidate in pack.candidates
    ]


def test_exact_budget_fit_includes_every_candidate_whole() -> None:
    pack = _pack(["alpha decision", "beta decision text", "gamma decision body"])
    budget = sum(_costs(pack))
    context = assemble_evaluation_context(pack, token_budget=budget)
    assert len(context.candidates) == 3
    assert context.estimated_tokens_used == budget
    assert context.omitted_for_budget == 0
    assert context.total_omitted == {"over_limit": 0, OVER_BUDGET_OMISSION_KEY: 0}
    assert context.degraded_reason is None
    # Inclusion order is pack order, which is score order.
    assert [c.decision_node_id for c in context.candidates] == [
        c.decision_node_id for c in pack.candidates
    ]


def test_one_token_over_budget_omits_the_last_candidate_whole() -> None:
    pack = _pack(["alpha decision", "beta decision text", "gamma decision body"])
    costs = _costs(pack)
    context = assemble_evaluation_context(pack, token_budget=sum(costs) - 1)
    assert len(context.candidates) == 2
    assert context.estimated_tokens_used == sum(costs[:2])
    assert context.omitted_for_budget == 1
    assert context.total_omitted[OVER_BUDGET_OMISSION_KEY] == 1
    omitted_text = pack.candidates[2].decision_text
    assert all(omitted_text not in payload for payload in context.candidate_payloads)


def test_omission_arithmetic_merges_pack_counts() -> None:
    pack = _pack(
        ["short", "a much longer decision body " * 10, "another long body " * 10],
        omitted_counts={"over_limit": 4},
        pool_extra=4,
    )
    costs = _costs(pack)
    context = assemble_evaluation_context(pack, token_budget=costs[0])
    assert len(context.candidates) == 1
    assert context.omitted_for_budget == 2
    assert dict(context.total_omitted) == {"over_limit": 4, OVER_BUDGET_OMISSION_KEY: 2}
    assert len(context.candidates) + context.omitted_for_budget == len(pack.candidates)


def test_pack_over_budget_count_sums_instead_of_overwriting() -> None:
    pack = _pack(["short", "a much longer decision body " * 10], omitted_counts={"over_budget": 3})
    costs = _costs(pack)
    context = assemble_evaluation_context(pack, token_budget=costs[0])
    assert context.omitted_for_budget == 1
    assert context.total_omitted[OVER_BUDGET_OMISSION_KEY] == 4


def test_inclusion_is_a_score_order_prefix_never_reordered() -> None:
    # The first (highest-scored) candidate is too big; the later, smaller
    # ones would fit. Pulling them in would silently invert the ranking, so
    # the honest result is an empty context with a visible reason.
    pack = _pack(["a very long decision body " * 50, "tiny", "tiny too"])
    costs = _costs(pack)
    assert costs[1] < costs[0] and costs[2] < costs[0]
    context = assemble_evaluation_context(pack, token_budget=costs[1] + costs[2])
    assert context.candidates == ()
    assert context.omitted_for_budget == 3
    assert context.degraded_reason is not None
    assert "first candidate" in context.degraded_reason


def test_determinism_and_context_hash_stability() -> None:
    pack = _pack(["alpha decision", "beta decision text", "gamma decision body"])
    budget = sum(_costs(pack))
    first = assemble_evaluation_context(pack, token_budget=budget)
    second = assemble_evaluation_context(pack, token_budget=budget)
    assert first == second
    assert first.context_hash == second.context_hash
    assert first.as_payload() == second.as_payload()
    # A budget that changes the included set changes the hash.
    smaller = assemble_evaluation_context(pack, token_budget=budget - 1)
    assert smaller.context_hash != first.context_hash


def test_whole_candidate_invariant_no_partial_payloads() -> None:
    pack = _pack(["alpha decision", "beta decision text " * 5])
    costs = _costs(pack)
    # Budget fits the first candidate plus half of the second.
    context = assemble_evaluation_context(pack, token_budget=costs[0] + costs[1] // 2)
    assert len(context.candidates) == 1
    for candidate, payload in zip(context.candidates, context.candidate_payloads, strict=True):
        assert json.loads(payload) == candidate.as_context_payload()
    assert all(
        pack.candidates[1].decision_text not in payload
        for payload in context.candidate_payloads
    )
    assert context.omitted_for_budget == 1


def test_budget_below_first_candidate_degrades_without_exception() -> None:
    pack = _pack(["alpha decision", "beta decision text"])
    context = assemble_evaluation_context(pack, token_budget=1)
    assert context.candidates == ()
    assert context.estimated_tokens_used == 0
    assert context.omitted_for_budget == 2
    assert context.total_omitted[OVER_BUDGET_OMISSION_KEY] == 2
    assert context.degraded_reason is not None
    assert "token_budget 1" in context.degraded_reason
    assert context.as_payload()["degraded_reason"] == context.degraded_reason


def test_zero_candidate_pack_passes_through_with_empty_context() -> None:
    pack = _pack([])
    context = assemble_evaluation_context(pack, token_budget=100)
    assert context.candidates == ()
    assert context.estimated_tokens_used == 0
    assert context.omitted_for_budget == 0
    assert context.total_omitted[OVER_BUDGET_OMISSION_KEY] == 0
    assert context.degraded_reason is None


def test_estimator_version_surfaces_in_payload() -> None:
    pack = _pack(["alpha decision"])
    context = assemble_evaluation_context(pack, token_budget=10_000)
    assert context.estimator_version == ESTIMATOR_VERSION
    assert context.as_payload()["estimator_version"] == ESTIMATOR_VERSION

    custom = TokenEstimator(version="fixed-cost-v9", estimate=lambda text: 1)
    custom_context = assemble_evaluation_context(pack, token_budget=10_000, estimator=custom)
    assert custom_context.estimator_version == "fixed-cost-v9"
    assert custom_context.as_payload()["estimator_version"] == "fixed-cost-v9"
    # Same included material under a different estimation regime is a
    # different replay context: the version is part of the hash material.
    assert custom_context.candidate_payloads == context.candidate_payloads
    assert custom_context.context_hash != context.context_hash


def test_default_estimator_uses_ceiling_division() -> None:
    assert default_token_estimator.estimate_tokens("") == 0
    assert default_token_estimator.estimate_tokens("abcd") == 1
    assert default_token_estimator.estimate_tokens("abcde") == 2


def test_invalid_budget_fails_closed() -> None:
    pack = _pack(["alpha decision"])
    with pytest.raises(ContextAssemblyValidationError, match=">= 1"):
        assemble_evaluation_context(pack, token_budget=0)
    with pytest.raises(ContextAssemblyValidationError, match=">= 1"):
        assemble_evaluation_context(pack, token_budget=-5)
    with pytest.raises(ContextAssemblyValidationError, match="must be an int"):
        assemble_evaluation_context(pack, token_budget=True)


def test_estimator_results_are_validated() -> None:
    pack = _pack(["alpha decision"])
    negative = TokenEstimator(version="negative-v1", estimate=lambda text: -1)
    with pytest.raises(ContextAssemblyValidationError, match="non-negative int"):
        assemble_evaluation_context(pack, token_budget=100, estimator=negative)
    boolish = TokenEstimator(version="bool-v1", estimate=lambda text: True)
    with pytest.raises(ContextAssemblyValidationError, match="non-negative int"):
        assemble_evaluation_context(pack, token_budget=100, estimator=boolish)
    with pytest.raises(ContextAssemblyValidationError, match="version must not be empty"):
        TokenEstimator(version="  ", estimate=lambda text: 1)


def _context(
    *,
    estimated_tokens_used: int = 0,
    omitted_for_budget: int = 0,
    total_omitted: dict[str, int] | None = None,
    degraded_reason: str | None = None,
) -> EvaluationContext:
    return EvaluationContext(
        query_hash=QUERY_HASH,
        retrieval_config_version="decisions-for-diff-v2+test",
        graph_snapshot_hash=GRAPH_HASH,
        candidates=(),
        token_budget=100,
        estimated_tokens_used=estimated_tokens_used,
        estimator_version=ESTIMATOR_VERSION,
        omitted_for_budget=omitted_for_budget,
        total_omitted=(
            total_omitted if total_omitted is not None else {OVER_BUDGET_OMISSION_KEY: 0}
        ),
        degraded_reason=degraded_reason,
    )


def test_context_invariants_reject_inconsistent_construction() -> None:
    with pytest.raises(ContextAssemblyValidationError, match="not exceed token_budget"):
        _context(estimated_tokens_used=101)
    with pytest.raises(ContextAssemblyValidationError, match="degraded_reason"):
        _context(omitted_for_budget=2, total_omitted={OVER_BUDGET_OMISSION_KEY: 2})
    with pytest.raises(ContextAssemblyValidationError, match="accounting stays visible"):
        _context(total_omitted={"over_limit": 0})
    with pytest.raises(ContextAssemblyValidationError, match="must include every"):
        _context(
            omitted_for_budget=3,
            total_omitted={OVER_BUDGET_OMISSION_KEY: 1},
            degraded_reason="budget below first candidate",
        )
