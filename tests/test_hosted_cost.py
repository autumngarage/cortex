"""Tests for cost/budget accounting at the model boundary (cortex#335)."""

from __future__ import annotations

import hashlib
import json

import pytest

from cortex.hosted.cost import (
    BudgetExceededError,
    CallOutcome,
    CostBasis,
    CostRecord,
    CostValidationError,
    ModelPrice,
    ModelPriceTable,
    RunBudget,
    RunLedger,
    TokenUsage,
)
from cortex.hosted.model_registry import RegisteredPrompt

PROMPT = RegisteredPrompt(
    prompt_id="derive-repo-native",
    version_number=1,
    template_text="Extract decision candidates from DOCUMENT.",
    description="Tier-1 derive prompt.",
)
INPUT_HASH = hashlib.sha256(b"request").hexdigest()
MODEL = "anthropic/claude-fable-5"
_DEFAULT_USAGE = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)


def _price_table() -> ModelPriceTable:
    return ModelPriceTable(
        version="2026-06-09",
        prices=(
            ModelPrice(
                model_id=MODEL,
                usd_per_million_input_tokens=3.0,
                usd_per_million_output_tokens=15.0,
            ),
        ),
    )


def _record(
    *,
    cost_basis: CostBasis = CostBasis.REPORTED_TOKENS,
    usage: TokenUsage | None = _DEFAULT_USAGE,
    outcome: CallOutcome = CallOutcome.OK,
    failure_reason: str | None = None,
    model_id: str = MODEL,
) -> CostRecord:
    return CostRecord(
        task_kind="derive",
        model_id=model_id,
        prompt_version=PROMPT.prompt_version,
        input_hash=INPUT_HASH,
        cost_basis=cost_basis,
        usage=usage,
        wall_ms=42,
        outcome=outcome,
        failure_reason=failure_reason,
    )


def _ledger(budget: RunBudget | None = None) -> RunLedger:
    return RunLedger(run_id="run-2026-06-09-a", price_table=_price_table(), budget=budget)


def test_token_usage_rejects_negative_counts() -> None:
    with pytest.raises(CostValidationError, match="input_tokens"):
        TokenUsage(input_tokens=-1, output_tokens=0)
    with pytest.raises(CostValidationError, match="output_tokens"):
        TokenUsage(input_tokens=0, output_tokens=-1)


def test_price_table_rejects_duplicates_and_names_unpriced_models() -> None:
    price = ModelPrice(
        model_id=MODEL,
        usd_per_million_input_tokens=3.0,
        usd_per_million_output_tokens=15.0,
    )
    with pytest.raises(CostValidationError, match="duplicate price"):
        ModelPriceTable(version="v1", prices=(price, price))
    with pytest.raises(CostValidationError, match="other/model"):
        _price_table().price_for("other/model")


def test_usd_is_derived_from_raw_token_counts_times_prices() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert _price_table().usd_for(MODEL, usage) == pytest.approx(18.0)
    small = TokenUsage(input_tokens=1200, output_tokens=300)
    assert _price_table().usd_for(MODEL, small) == pytest.approx(0.0081)


def test_unreported_tokens_are_explicit_never_zero() -> None:
    # Claiming reported tokens without usage is unrepresentable.
    with pytest.raises(CostValidationError, match="unreported-tokens"):
        _record(cost_basis=CostBasis.REPORTED_TOKENS, usage=None)
    # And an unreported record cannot smuggle token counts in.
    with pytest.raises(CostValidationError, match="must not carry usage"):
        _record(cost_basis=CostBasis.UNREPORTED_TOKENS)
    record = _record(cost_basis=CostBasis.UNREPORTED_TOKENS, usage=None)
    assert record.usage is None
    entry = _ledger().append(record)
    assert entry.usd is None  # explicitly unknown, not 0.0


def test_failed_records_require_a_reason_and_ok_records_forbid_one() -> None:
    with pytest.raises(CostValidationError, match="failure_reason"):
        _record(outcome=CallOutcome.FAILED)
    with pytest.raises(CostValidationError, match="must not carry a failure_reason"):
        _record(failure_reason="but it worked")
    record = _record(outcome=CallOutcome.FAILED, failure_reason="claude CLI exited 1")
    assert record.failure_reason == "claude CLI exited 1"


def test_cost_record_validates_identity_fields() -> None:
    with pytest.raises(CostValidationError, match="task_kind"):
        CostRecord(
            task_kind=" ",
            model_id=MODEL,
            prompt_version=PROMPT.prompt_version,
            input_hash=INPUT_HASH,
            cost_basis=CostBasis.NO_RESPONSE,
            wall_ms=1,
            outcome=CallOutcome.OK,
        )
    with pytest.raises(CostValidationError, match="not canonical"):
        CostRecord(
            task_kind="derive",
            model_id=MODEL,
            prompt_version="v1-not-canonical",
            input_hash=INPUT_HASH,
            cost_basis=CostBasis.NO_RESPONSE,
            wall_ms=1,
            outcome=CallOutcome.OK,
        )
    with pytest.raises(CostValidationError, match="input_hash"):
        CostRecord(
            task_kind="derive",
            model_id=MODEL,
            prompt_version=PROMPT.prompt_version,
            input_hash="abc",
            cost_basis=CostBasis.NO_RESPONSE,
            wall_ms=1,
            outcome=CallOutcome.OK,
        )


def test_ledger_accumulates_and_summarizes_per_run() -> None:
    ledger = _ledger()
    reported = ledger.append(_record())
    assert reported.usd == pytest.approx(18.0)
    unreported = ledger.append(_record(cost_basis=CostBasis.UNREPORTED_TOKENS, usage=None))
    assert unreported.usd is None
    replayed = ledger.append(_record(cost_basis=CostBasis.RECORDED_PLAYBACK, usage=None))
    assert replayed.usd == 0.0

    assert ledger.call_count == 3
    assert ledger.known_usd_total == pytest.approx(18.0)
    assert ledger.unknown_cost_call_count == 1

    summary = ledger.summary_payload()
    assert summary["run_id"] == "run-2026-06-09-a"
    assert summary["price_table_version"] == "2026-06-09"
    assert summary["call_count"] == 3
    assert summary["ok_call_count"] == 3
    assert summary["failed_call_count"] == 0
    assert summary["known_usd_total"] == pytest.approx(18.0)
    assert summary["unknown_cost_call_count"] == 1
    assert summary["recorded_playback_call_count"] == 1
    assert summary["reported_input_tokens"] == 1_000_000
    assert summary["reported_output_tokens"] == 1_000_000
    assert summary["models"] == [MODEL]
    assert len(summary["records"]) == 3
    # The payload is the #349 report input; it must serialize as-is.
    json.dumps(summary, sort_keys=True)


def test_replayed_runs_report_zero_live_cost() -> None:
    ledger = _ledger()
    for _ in range(3):
        ledger.append(_record(cost_basis=CostBasis.RECORDED_PLAYBACK, usage=None))
    summary = ledger.summary_payload()
    assert summary["known_usd_total"] == 0.0
    assert summary["recorded_playback_call_count"] == 3
    assert summary["unknown_cost_call_count"] == 0


def test_call_count_ceiling_refuses_before_the_call() -> None:
    ledger = _ledger(budget=RunBudget(max_calls=1))
    ledger.ensure_budget_allows_call(task_kind="derive")
    ledger.append(_record())
    with pytest.raises(BudgetExceededError, match="1 call"):
        ledger.ensure_budget_allows_call(task_kind="derive")


def test_usd_ceiling_refuses_once_known_spend_meets_it() -> None:
    ledger = _ledger(budget=RunBudget(max_usd=10.0))
    ledger.ensure_budget_allows_call(task_kind="derive")
    ledger.append(_record())  # 18.0 USD
    with pytest.raises(BudgetExceededError, match=r"\$10"):
        ledger.ensure_budget_allows_call(task_kind="evaluate")


def test_over_budget_discovered_after_the_call_is_a_recorded_marker() -> None:
    ledger = _ledger(budget=RunBudget(max_usd=10.0))
    ledger.ensure_budget_allows_call(task_kind="derive")  # 0 < 10: allowed
    entry = ledger.append(_record())  # costs 18.0 — discovered after
    assert entry.over_budget is True
    assert ledger.over_budget is True
    summary = ledger.summary_payload()
    assert summary["over_budget"] is True
    assert summary["records"][0]["over_budget"] is True


def test_unpriced_live_call_fails_visibly_instead_of_completing_silently() -> None:
    table = ModelPriceTable(version="v1", prices=())
    ledger = RunLedger(run_id="run-x", price_table=table)
    with pytest.raises(CostValidationError, match=MODEL):
        ledger.append(_record())


def test_run_and_budget_validation() -> None:
    with pytest.raises(CostValidationError, match="run_id"):
        RunLedger(run_id="  ", price_table=_price_table())
    with pytest.raises(CostValidationError, match="max_usd"):
        RunBudget(max_usd=-1.0)
    with pytest.raises(CostValidationError, match="max_calls"):
        RunBudget(max_calls=-1)
