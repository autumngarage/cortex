"""Tests for the operator-INTERNAL cost telemetry (cortex#547).

Five surfaces, all offline (a fake DB stands in for Postgres) plus one
``DATABASE_URL``-gated round-trip:

1. the api-http judge model is priced in ``REVIEW_PRICE_TABLE`` (the
   regression fix: ``price_for`` no longer raises "unpriced call");
2. ``ModelRouter.cost_summary`` is populated by a routed evaluate;
3. ``run_stateless_review`` carries a ``ReviewCost`` of the right shape
   (recorded-playback path = 0 usd; a scripted reported-tokens path = real usd);
4. the worker writes one idempotent internal cost row;
5. the ``cost-report`` aggregation math (mean/median/p95) on seeded rows.

The internal/customer boundary is asserted explicitly: the cost figures are
provider dollars, and the report header says so.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from cortex.commands.cost_report import (
    INTERNAL_REPORT_HEADER,
    CostReportError,
    aggregate_cost_rows,
    render_cost_report,
    review_cost_query_sql,
)
from cortex.commands.review import (
    DEFAULT_REVIEW_API_MODEL,
    REVIEW_EVALUATE_PROMPT,
    REVIEW_PRICE_TABLE,
    EvaluateRoute,
    build_review_router,
)
from cortex.hosted.cost import (
    CostBasis,
    CostValidationError,
    RunLedger,
    TokenUsage,
)
from cortex.hosted.model_interfaces import (
    EvaluateRequest,
    EvaluateResult,
)
from cortex.hosted.review_cost import (
    ReviewCostError,
    ReviewCostRecord,
    review_cost_insert_sql,
)
from cortex.hosted.routing import (
    AdapterOutcome,
    RouteConfig,
    RouterCostSummary,
)
from cortex.hosted.stateless_review import (
    ReviewCost,
    ReviewHandlerConfig,
    run_stateless_review,
)

# Reuse the stateless-review fixtures (one canned repo, recorded model, payload).
from tests.test_hosted_stateless_review import (
    _HEAD_SHA,
    _OWNER,
    _PR_NUMBER,
    _RECORDINGS_PATH,
    _REPO,
    PLAYBACK_MODEL_ID,
    _full_repo_client,
    _payload,
)

API_MODEL_ID = f"anthropic/{DEFAULT_REVIEW_API_MODEL}"


def _recorded_router() -> Any:
    """A real ``ModelRouter`` over the committed recordings.

    This mirrors what ``default_model_resolver`` builds in production
    (``resolve_evaluate_route`` -> ``build_review_router``): a router whose
    private ledger records ``CostBasis.RECORDED_PLAYBACK`` (usd 0) so
    ``cost_summary`` is populated. The hand-rolled recorded shim in
    ``test_hosted_stateless_review`` deliberately exposes no ledger; the cost
    path needs the real router, which is the production wiring anyway.
    """

    from cortex.hosted.routing import RecordedResponseAdapter

    adapter = RecordedResponseAdapter.from_json(_RECORDINGS_PATH.read_text(encoding="utf-8"))
    route = EvaluateRoute(
        adapter=adapter,
        adapter_id="recorded-responses",
        model_id=PLAYBACK_MODEL_ID,
    )
    ledger = RunLedger(run_id="recorded-cost-test", price_table=REVIEW_PRICE_TABLE)
    return build_review_router(route, run_ledger=ledger)


# ---------------------------------------------------------------------------
# Part 1: the missing-price regression fix
# ---------------------------------------------------------------------------


def test_api_judge_model_is_priced_in_the_review_table() -> None:
    # The regression: the server-path judge model was unpriced, so price_for
    # raised on the live worker. It must resolve now.
    price = REVIEW_PRICE_TABLE.price_for(API_MODEL_ID)
    assert price.usd_per_million_input_tokens == 3.0
    assert price.usd_per_million_output_tokens == 15.0


def test_api_judge_model_usd_is_real_provider_dollars() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    usd = REVIEW_PRICE_TABLE.usd_for(API_MODEL_ID, usage)
    # 3.0 + 15.0 per million each = 18.0 for a million of each.
    assert usd == pytest.approx(18.0)


def test_cli_route_stays_unmetered_zero() -> None:
    # The CLI transport is subscription-authenticated; its internal cost is $0,
    # and that $0 is a deliberate cost fact, not a missing price.
    cli_price = REVIEW_PRICE_TABLE.price_for("anthropic/claude-cli")
    assert cli_price.usd_per_million_input_tokens == 0.0
    assert cli_price.usd_per_million_output_tokens == 0.0


def test_unpriced_model_still_raises_visibly() -> None:
    with pytest.raises(CostValidationError, match="no price registered"):
        REVIEW_PRICE_TABLE.price_for("anthropic/some-unpriced-model")


# ---------------------------------------------------------------------------
# Part 2: ModelRouter.cost_summary
# ---------------------------------------------------------------------------


def _candidate_pack() -> Any:
    from cortex.hosted.ask_ledger import CitedSourceSpan
    from cortex.hosted.decisions_for_diff import (
        DecisionsForDiffCandidate,
        DecisionsForDiffCandidatePack,
    )

    span_hash = hashlib.sha256(b"span").hexdigest()
    candidate = DecisionsForDiffCandidate(
        decision_node_id="3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
        decision_version_id="4e0f9a8b-6c5d-4e7f-a091-4d5e6f7a8b92",
        status="confirmed",
        decision_text="Webhook retries use exponential backoff with jitter.",
        score=1.0,
        reason_codes=("scope:path:src/payments/webhook_client.py",),
        cited_spans=(
            CitedSourceSpan(
                span_hash=span_hash,
                excerpt="exponential backoff with jitter",
                permalink="https://github.com/acme/payments/blob/main/docs/adr/0007.md",
                source_document_id="5f1a0b9c-7d6e-4f80-b1a2-5e6f7a8b9c03",
                source_id=str(uuid4()),
            ),
        ),
    )
    return DecisionsForDiffCandidatePack(
        query_hash=hashlib.sha256(b"query").hexdigest(),
        retrieval_config_version="decisions-for-diff-v1+test",
        graph_snapshot_hash=hashlib.sha256(b"graph").hexdigest(),
        candidates=(candidate,),
        omitted_counts={},
        graph_node_count=10,
        candidate_pool_size=3,
    )


def _evaluate_request() -> EvaluateRequest:
    return EvaluateRequest(
        candidate_pack=_candidate_pack(),
        diff_patch="-    delay = backoff_with_jitter(attempt)\n+    delay = 5.0\n",
        prompt_version=REVIEW_EVALUATE_PROMPT.prompt_version,
    )


class _ReportedTokensAdapter:
    """A stub adapter stamping the api judge model with reported tokens."""

    def __init__(self, *, input_tokens: int, output_tokens: int) -> None:
        self._usage = TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)

    def run_derive(self, request: Any, route: RouteConfig) -> AdapterOutcome:  # pragma: no cover
        raise AssertionError("derive not used in these tests")

    def run_evaluate(self, request: EvaluateRequest, route: RouteConfig) -> AdapterOutcome:
        # Emit no findings: this stub exists to exercise the cost path with
        # reported tokens, not the citation gate. A finding would have to bind
        # to whatever pack the caller built; an empty findings tuple is always
        # valid and keeps the test focused on the cost shape.
        result = EvaluateResult(
            findings=(),
            model_id=route.model_id,
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
        )
        return AdapterOutcome(
            result=result,
            cost_basis=CostBasis.REPORTED_TOKENS,
            usage=self._usage,
        )


def _api_router(*, input_tokens: int, output_tokens: int) -> Any:
    route = EvaluateRoute(
        adapter=_ReportedTokensAdapter(input_tokens=input_tokens, output_tokens=output_tokens),
        adapter_id="api-http",
        model_id=API_MODEL_ID,
        params={"api_model": DEFAULT_REVIEW_API_MODEL},
    )
    ledger = RunLedger(run_id="cost-summary-test", price_table=REVIEW_PRICE_TABLE)
    return build_review_router(route, run_ledger=ledger)


def test_cost_summary_is_empty_before_any_call() -> None:
    router = _api_router(input_tokens=1000, output_tokens=200)
    summary = router.cost_summary
    assert isinstance(summary, RouterCostSummary)
    assert summary.call_count == 0
    assert summary.known_usd_total == 0.0
    assert summary.model_ids == ()


def test_cost_summary_is_populated_by_a_routed_evaluate() -> None:
    router = _api_router(input_tokens=1_000_000, output_tokens=1_000_000)
    router.evaluate(_evaluate_request())
    summary = router.cost_summary
    assert summary.call_count == 1
    assert summary.model_ids == (API_MODEL_ID,)
    assert summary.reported_input_tokens == 1_000_000
    assert summary.reported_output_tokens == 1_000_000
    # 3.0 + 15.0 per million each.
    assert summary.known_usd_total == pytest.approx(18.0)
    assert summary.unknown_cost_call_count == 0


def test_cost_summary_does_not_expose_the_mutable_ledger() -> None:
    import dataclasses

    router = _api_router(input_tokens=10, output_tokens=10)
    summary = router.cost_summary
    # A frozen value object, not the ledger.
    assert not hasattr(summary, "append")
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.call_count = 99


def test_router_cost_summary_rejects_negative_fields() -> None:
    from cortex.hosted.routing import RoutingError

    with pytest.raises(RoutingError):
        RouterCostSummary(
            model_ids=(),
            call_count=-1,
            reported_input_tokens=0,
            reported_output_tokens=0,
            known_usd_total=0.0,
            unknown_cost_call_count=0,
        )


# ---------------------------------------------------------------------------
# Part 3: run_stateless_review carries ReviewCost
# ---------------------------------------------------------------------------


def test_recorded_playback_review_carries_zero_usd_cost() -> None:
    # The recorded adapter plays back with CostBasis.RECORDED_PLAYBACK; live usd
    # is exactly 0 by definition, but the ReviewCost shape is still present.
    result = run_stateless_review(
        _payload(),
        client=_full_repo_client(),
        model=_recorded_router(),
        config=ReviewHandlerConfig(dry_run=True),
    )
    assert result.cost is not None
    assert isinstance(result.cost, ReviewCost)
    assert result.cost.usd == 0.0
    assert result.cost.call_count == 1
    assert result.cost.model == PLAYBACK_MODEL_ID
    # Recorded playback carries no reported tokens.
    assert result.cost.input_tokens == 0
    assert result.cost.output_tokens == 0


def test_review_cost_is_in_the_result_mapping_under_cost_key() -> None:
    result = run_stateless_review(
        _payload(),
        client=_full_repo_client(),
        model=_recorded_router(),
        config=ReviewHandlerConfig(dry_run=True),
    )
    mapping = result.as_result_mapping()
    assert "cost" in mapping
    assert mapping["cost"]["usd"] == 0.0
    assert mapping["cost"]["model"] == PLAYBACK_MODEL_ID


def test_review_cost_is_not_in_the_rendered_comment() -> None:
    # The internal cost is operator-only; it must never leak into the customer
    # PR comment body.
    result = run_stateless_review(
        _payload(),
        client=_full_repo_client(),
        model=_recorded_router(),
        config=ReviewHandlerConfig(dry_run=True),
    )
    assert "usd" not in result.comment_body.lower()
    assert "cost" not in result.comment_body.lower()


def test_no_decisions_review_carries_no_cost() -> None:
    from tests.test_hosted_stateless_review import _empty_repo_client

    result = run_stateless_review(
        _payload(),
        client=_empty_repo_client(),
        model=_recorded_router(),
        config=ReviewHandlerConfig(dry_run=True),
    )
    assert result.no_decisions is True
    assert result.cost is None
    assert result.as_result_mapping()["cost"] is None


def test_review_with_reported_tokens_records_real_usd() -> None:
    # The scripted reported-tokens path: a router over the api judge model with
    # real token counts yields a non-zero internal usd cost.
    router = _api_router(input_tokens=1_000_000, output_tokens=1_000_000)
    result = run_stateless_review(
        _payload(),
        client=_full_repo_client(),
        model=router,
        config=ReviewHandlerConfig(dry_run=True),
    )
    assert result.cost is not None
    assert result.cost.model == API_MODEL_ID
    assert result.cost.input_tokens == 1_000_000
    assert result.cost.output_tokens == 1_000_000
    assert result.cost.usd == pytest.approx(18.0)
    assert result.cost.call_count == 1


def test_review_cost_dataclass_validates_fields() -> None:
    from cortex.hosted.stateless_review import StatelessReviewError

    with pytest.raises(StatelessReviewError):
        ReviewCost(model="", input_tokens=0, output_tokens=0, usd=0.0, call_count=1)
    with pytest.raises(StatelessReviewError):
        ReviewCost(model="m", input_tokens=-1, output_tokens=0, usd=0.0, call_count=1)
    with pytest.raises(StatelessReviewError):
        ReviewCost(model="m", input_tokens=0, output_tokens=0, usd=-1.0, call_count=1)


# ---------------------------------------------------------------------------
# Part 4: the worker writes one idempotent internal cost row
# ---------------------------------------------------------------------------


def _record(
    *,
    tenant_id: str | None = None,
    pr_number: int = 7,
    occurred_at: datetime | None = None,
) -> ReviewCostRecord:
    return ReviewCostRecord(
        tenant_id=tenant_id if tenant_id is not None else str(uuid4()),
        repo_full_name="acme/widgets",
        pr_number=pr_number,
        head_sha="abc123",
        model_id="anthropic/claude-cli",
        input_tokens=0,
        output_tokens=0,
        usd=0.0,
        occurred_at=occurred_at if occurred_at is not None else datetime.now(UTC),
    )


def test_review_cost_record_rejects_bad_identity() -> None:
    with pytest.raises(ReviewCostError, match="tenant_id"):
        _record(tenant_id="not-a-uuid")
    with pytest.raises(ReviewCostError, match="pr_number"):
        _record(pr_number=0)
    with pytest.raises(ReviewCostError, match="timezone-aware"):
        _record(occurred_at=datetime(2026, 6, 11))


def test_review_cost_record_idempotency_key_is_stable_per_identity() -> None:
    tenant = str(uuid4())
    a = ReviewCostRecord(
        tenant_id=tenant,
        repo_full_name="acme/widgets",
        pr_number=7,
        head_sha="abc123",
        model_id="anthropic/claude-cli",
        input_tokens=1,
        output_tokens=2,
        usd=0.0,
        occurred_at=datetime.now(UTC),
    )
    b = ReviewCostRecord(
        tenant_id=tenant,
        repo_full_name="acme/widgets",
        pr_number=7,
        head_sha="abc123",
        model_id="anthropic/claude-cli",
        input_tokens=999,  # different cost material...
        output_tokens=888,
        usd=42.0,
        occurred_at=datetime.now(UTC) + timedelta(hours=1),
        # ...but same identity (tenant, repo, pr, head_sha, model) -> same key.
    )
    assert a.idempotency_key == b.idempotency_key
    c = ReviewCostRecord(
        tenant_id=tenant,
        repo_full_name="acme/widgets",
        pr_number=7,
        head_sha="DIFFERENT",  # new head sha -> new key
        model_id="anthropic/claude-cli",
        input_tokens=1,
        output_tokens=2,
        usd=0.0,
        occurred_at=datetime.now(UTC),
    )
    assert c.idempotency_key != a.idempotency_key


class _CostCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _CostWorkerDb:
    """Fake DB emulating the jobs queue plus the review_cost_records insert."""

    def __init__(self, payload: Mapping[str, Any], result: Mapping[str, Any]) -> None:
        self._payload = dict(payload)
        self._result = dict(result)
        self.cost_rows: dict[str, dict[str, Any]] = {}
        self.claimed = False
        self.commits = 0
        self.rollbacks = 0
        self.completed = False

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> _CostCursor:
        q = query.strip()
        p = dict(params or {})
        if "FOR UPDATE SKIP LOCKED" in q:
            if self.claimed:
                return _CostCursor([])
            self.claimed = True
            return _CostCursor(
                [
                    (
                        "job-1",
                        "github.pull_request",
                        "idem-1",
                        json.dumps(self._payload),
                        1,
                        3,
                    )
                ]
            )
        if q.startswith("INSERT INTO cortex_hosted.review_cost_records"):
            key = str(p["idempotency_key"])
            if key in self.cost_rows:
                return _CostCursor([])  # ON CONFLICT DO NOTHING
            self.cost_rows[key] = dict(p)
            return _CostCursor([(str(uuid4()),)])
        if "SET status = 'succeeded'" in q:
            self.completed = True
            return _CostCursor([("job-1",)])
        raise AssertionError(f"unexpected SQL: {q[:80]}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass

    # The worker's handler is invoked separately; for this test we stub the
    # registry so the handler returns the canned stateless result.
    def stub_result(self) -> Mapping[str, Any]:
        return self._result


def _stateless_result_mapping(*, model: str, usd: float, input_tokens: int) -> dict[str, Any]:
    return {
        "handled": True,
        "review_mode": "stateless",
        "dry_run": True,
        "posted": False,
        "finding_count": 1,
        "decision_count": 1,
        "no_decisions": False,
        "comment_id": None,
        "comment_url": None,
        "comment_body": "advisory body",
        "cost": {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": 2,
            "usd": usd,
            "call_count": 1,
        },
    }


def _cost_worker(db: _CostWorkerDb) -> Any:
    from cortex.hosted.worker import HandlerRegistry, Worker

    registry = HandlerRegistry()
    registry.register("github.pull_request", lambda job: db.stub_result())
    return Worker(conn=db, registry=registry, worker_id="w-test")


def _review_payload() -> dict[str, Any]:
    # The worker re-parses PR identity from the payload; reuse the stateless
    # fixture's payload shape.
    return _payload()


def test_worker_writes_one_internal_cost_row(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    db = _CostWorkerDb(
        _review_payload(),
        _stateless_result_mapping(model=API_MODEL_ID, usd=18.0, input_tokens=1_000_000),
    )
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert _cost_worker(db).run_once() is True
    assert len(db.cost_rows) == 1
    (row,) = db.cost_rows.values()
    assert row["model_id"] == API_MODEL_ID
    assert row["usd"] == 18.0
    assert row["repo_full_name"] == f"{_OWNER}/{_REPO}"
    assert row["pr_number"] == _PR_NUMBER
    assert row["head_sha"] == _HEAD_SHA
    # The structured review.cost log line was emitted.
    events = [json.loads(rec.message).get("event") for rec in caplog.records]
    assert "review.cost" in events


def test_worker_cost_row_is_idempotent_on_redelivery() -> None:
    payload = _review_payload()
    mapping = _stateless_result_mapping(model=API_MODEL_ID, usd=18.0, input_tokens=10)
    db = _CostWorkerDb(payload, mapping)
    _cost_worker(db).run_once()
    # Simulate a redelivery: a second run with the same identity must not add a
    # second row (ON CONFLICT DO NOTHING on the idempotency key).
    db.claimed = False
    db.completed = False
    _cost_worker(db).run_once()
    assert len(db.cost_rows) == 1


def test_worker_skips_cost_for_non_review_result(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    db = _CostWorkerDb(_review_payload(), {"handled": True, "job_type": "github.pull_request"})
    with caplog.at_level(logging.INFO, logger="cortex.hosted.worker"):
        assert _cost_worker(db).run_once() is True
    assert db.cost_rows == {}


# ---------------------------------------------------------------------------
# Part 5: cost-report aggregation math
# ---------------------------------------------------------------------------


def test_aggregate_empty_rows_is_a_visible_zero_report() -> None:
    report = aggregate_cost_rows([])
    assert report.total_reviews == 0
    assert report.total_usd == 0.0
    assert report.by_model == ()
    assert INTERNAL_REPORT_HEADER in render_cost_report(report)


def test_aggregate_mean_median_p95_on_seeded_rows() -> None:
    # Ten reviews with known costs: 1..10 dollars, all one model.
    rows = [{"model_id": "anthropic/claude-cli", "usd": float(n)} for n in range(1, 11)]
    report = aggregate_cost_rows(rows)
    assert report.total_reviews == 10
    assert report.total_usd == pytest.approx(55.0)
    assert report.mean_usd == pytest.approx(5.5)
    # Even n: median is the average of the 5th and 6th (5 and 6) -> 5.5.
    assert report.median_usd == pytest.approx(5.5)
    # Nearest-rank p95 of 1..10: ceil(0.95*10)=10 -> the 10th value = 10.
    assert report.p95_usd == pytest.approx(10.0)


def test_aggregate_breaks_down_by_model() -> None:
    rows = [
        {"model_id": "anthropic/claude-cli", "usd": 0.0},
        {"model_id": "anthropic/claude-cli", "usd": 0.0},
        {"model_id": API_MODEL_ID, "usd": 2.0},
        {"model_id": API_MODEL_ID, "usd": 4.0},
    ]
    report = aggregate_cost_rows(rows)
    by_model = {b.model_id: b for b in report.by_model}
    assert by_model["anthropic/claude-cli"].review_count == 2
    assert by_model["anthropic/claude-cli"].total_usd == 0.0
    assert by_model[API_MODEL_ID].review_count == 2
    assert by_model[API_MODEL_ID].mean_usd == pytest.approx(3.0)
    assert report.total_usd == pytest.approx(6.0)


def test_aggregate_median_odd_count() -> None:
    rows = [{"model_id": "m", "usd": float(n)} for n in (1, 2, 3, 4, 5)]
    report = aggregate_cost_rows(rows)
    assert report.median_usd == pytest.approx(3.0)
    # p95 of 5 values: ceil(0.95*5)=5 -> the 5th = 5.
    assert report.p95_usd == pytest.approx(5.0)


def test_aggregate_rejects_malformed_rows() -> None:
    with pytest.raises(CostReportError, match="usd"):
        aggregate_cost_rows([{"model_id": "m", "usd": "free"}])
    with pytest.raises(CostReportError, match="usd must be non-negative"):
        aggregate_cost_rows([{"model_id": "m", "usd": -1.0}])
    with pytest.raises(CostReportError, match="model_id"):
        aggregate_cost_rows([{"model_id": "", "usd": 1.0}])


def test_render_header_states_the_internal_customer_boundary() -> None:
    report = aggregate_cost_rows([{"model_id": "m", "usd": 1.0}])
    rendered = render_cost_report(report)
    assert "provider dollars, not customer credits" in rendered
    assert "Do not expose" in rendered


def test_query_sql_adds_filters_only_when_requested() -> None:
    plain = review_cost_query_sql()
    assert "WHERE" not in plain
    since_only = review_cost_query_sql(since=True)
    assert "occurred_at >= %(since)s" in since_only
    both = review_cost_query_sql(since=True, repo=True)
    assert "occurred_at >= %(since)s" in both
    assert "repo_full_name = %(repo)s" in both
    assert "AND" in both


# ---------------------------------------------------------------------------
# DATABASE_URL-gated round-trip: the append-only cost ledger end to end
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set DATABASE_URL to a pgvector Postgres to run the cost-ledger round-trip",
)
def test_review_cost_ledger_round_trip_is_append_only_and_idempotent() -> None:
    from cortex.hosted.db import connect
    from cortex.hosted.migrations import apply_schema

    connection = connect(DATABASE_URL)
    try:
        apply_schema(connection)
        tenant = str(uuid4())
        record = ReviewCostRecord(
            tenant_id=tenant,
            repo_full_name="autumngarage/cortex",
            pr_number=4242,
            head_sha=hashlib.sha256(b"head").hexdigest()[:12],
            model_id=API_MODEL_ID,
            input_tokens=123,
            output_tokens=45,
            usd=1.234567,
            occurred_at=datetime.now(UTC),
        )
        first = connection.execute(
            review_cost_insert_sql(), record.as_insert_parameters()
        ).fetchone()
        assert first is not None
        # Redelivery: same identity -> ON CONFLICT DO NOTHING, no second row.
        second = connection.execute(
            review_cost_insert_sql(), record.as_insert_parameters()
        ).fetchone()
        assert second is None
        connection.commit()
        count = connection.execute(
            "SELECT count(*) FROM cortex_hosted.review_cost_records "
            "WHERE tenant_id = %(tenant_id)s",
            {"tenant_id": tenant},
        ).fetchone()
        assert count is not None and int(count[0]) == 1
        # Append-only: an UPDATE must be refused by the trigger.
        import psycopg

        with pytest.raises(psycopg.Error):
            connection.execute(
                "UPDATE cortex_hosted.review_cost_records SET usd = 0 "
                "WHERE tenant_id = %(tenant_id)s",
                {"tenant_id": tenant},
            )
        connection.rollback()
    finally:
        connection.close()
