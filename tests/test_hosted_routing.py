"""Tests for provider-agnostic task routing behind the boundary (cortex#345)."""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import cortex.hosted.routing as routing_module
from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.cost import (
    BudgetExceededError,
    CallOutcome,
    CostBasis,
    ModelPrice,
    ModelPriceTable,
    RunBudget,
    RunLedger,
    TokenUsage,
)
from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)
from cortex.hosted.eval_fixtures import FindingClass
from cortex.hosted.model_interfaces import (
    DeriveCandidate,
    DeriveModel,
    DeriveRequest,
    DeriveResult,
    DroppedChatter,
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
    ModelInterfaceValidationError,
)
from cortex.hosted.model_registry import (
    ModelPromptRegistry,
    RegisteredModel,
    RegisteredPrompt,
    RegistryValidationError,
)
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.routing import (
    AdapterOutcome,
    ClaudeCliAdapter,
    ClaudeCliUnavailableError,
    ModelRouter,
    RecordedResponseAdapter,
    RouteConfig,
    RouteTable,
    RoutingError,
    TaskKind,
    derive_result_as_payload,
    derive_result_from_payload,
    evaluate_result_as_payload,
    evaluate_result_from_payload,
)

TENANT = "0b6f9f3e-3a2f-4f2e-9c8d-1a2b3c4d5e6f"
SOURCE = "1c7f8e2d-4b3a-4c5d-8e9f-2b3c4d5e6f70"
CLAUDE_MODEL = "anthropic/claude-fable-5"
STUB_A_MODEL = "stub-a/model-one"
STUB_B_MODEL = "stub-b/model-two"
PACK_SPAN_HASH = hashlib.sha256(b"span").hexdigest()

PROMPT = RegisteredPrompt(
    prompt_id="derive-repo-native",
    version_number=1,
    template_text="Extract decision candidates from DOCUMENT.",
    description="Tier-1 derive prompt.",
)
EVAL_PROMPT = RegisteredPrompt(
    prompt_id="evaluate-contradiction",
    version_number=1,
    template_text="Judge whether DIFF contradicts DECISIONS.",
    description="Soft-evaluator prompt.",
)


def _registry() -> ModelPromptRegistry:
    return ModelPromptRegistry(
        models=(
            RegisteredModel(model_id=CLAUDE_MODEL, description="claude CLI route"),
            RegisteredModel(model_id=STUB_A_MODEL, description="stub provider a"),
            RegisteredModel(model_id=STUB_B_MODEL, description="stub provider b"),
        ),
        prompts=(PROMPT, EVAL_PROMPT),
    )


def _price_table() -> ModelPriceTable:
    return ModelPriceTable(
        version="2026-06-09",
        prices=tuple(
            ModelPrice(
                model_id=model_id,
                usd_per_million_input_tokens=3.0,
                usd_per_million_output_tokens=15.0,
            )
            for model_id in (CLAUDE_MODEL, STUB_A_MODEL, STUB_B_MODEL)
        ),
    )


def _ledger(budget: RunBudget | None = None) -> RunLedger:
    return RunLedger(run_id="run-1", price_table=_price_table(), budget=budget)


def _document() -> SourceDocument:
    return SourceDocument(
        tenant_id=TENANT,
        source_id=SOURCE,
        document_type="adr",
        external_id="docs/adr/0007-retry-policy.md",
        permalink="https://github.com/acme/payments/blob/main/docs/adr/0007-retry-policy.md",
        author_ref="henry",
        source_timestamp=datetime(2026, 5, 14, 9, 30, tzinfo=UTC),
        content="We use exponential backoff with jitter for webhook retries.",
    )


def _derive_request(**metadata: object) -> DeriveRequest:
    return DeriveRequest(
        source_document=_document(),
        prompt_version=PROMPT.prompt_version,
        metadata=dict(metadata),
    )


def _derive_result(request: DeriveRequest, *, model_id: str) -> DeriveResult:
    doc = request.source_document
    span = doc.span(start_offset=0, end_offset=len(doc.content))
    return DeriveResult(
        candidates=(
            DeriveCandidate(
                decision_text="Webhook retries use exponential backoff with jitter.",
                spans=(span,),
            ),
        ),
        model_id=model_id,
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
        dropped=(
            DroppedChatter(
                reason_code="smalltalk",
                excerpt_hash=hashlib.sha256(b"lgtm!").hexdigest(),
            ),
        ),
    )


def _candidate_pack() -> DecisionsForDiffCandidatePack:
    candidate = DecisionsForDiffCandidate(
        decision_node_id="3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
        decision_version_id="4e0f9a8b-6c5d-4e7f-a091-4d5e6f7a8b92",
        status="confirmed",
        decision_text="Webhook retries use exponential backoff with jitter.",
        score=1.0,
        reason_codes=("scope:path:src/payments/webhook_client.py",),
        cited_spans=(
            CitedSourceSpan(
                span_hash=PACK_SPAN_HASH,
                excerpt="exponential backoff with jitter",
                permalink="https://github.com/acme/payments/blob/main/docs/adr/0007.md",
                source_document_id="5f1a0b9c-7d6e-4f80-b1a2-5e6f7a8b9c03",
                source_id=SOURCE,
            ),
        ),
    )
    return DecisionsForDiffCandidatePack(
        query_hash=hashlib.sha256(b"query").hexdigest(),
        retrieval_config_version="decisions-for-diff-v1+test",
        graph_snapshot_hash=hashlib.sha256(b"graph").hexdigest(),
        candidates=(candidate,),
        omitted_counts={"full_text": 2},
        graph_node_count=10,
        candidate_pool_size=3,
    )


def _evaluate_request() -> EvaluateRequest:
    return EvaluateRequest(
        candidate_pack=_candidate_pack(),
        diff_patch="-    delay = backoff_with_jitter(attempt)\n+    delay = 5.0\n",
        prompt_version=EVAL_PROMPT.prompt_version,
    )


def _evaluate_result(request: EvaluateRequest, *, model_id: str) -> EvaluateResult:
    return EvaluateResult(
        findings=(
            FindingDraft(
                finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
                decision_node_id="3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
                cited_span_hashes=(PACK_SPAN_HASH,),
                summary="Fixed 5s retry contradicts the confirmed backoff decision.",
                confidence_label="advisory",
            ),
        ),
        model_id=model_id,
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
    )


class StubAdapter:
    """A second/third provider behind the boundary, for routing tests."""

    def __init__(
        self,
        model_id: str,
        *,
        fail_with: Exception | None = None,
        usage: TokenUsage | None = None,
        drift_input_hash: bool = False,
    ) -> None:
        self.model_id = model_id
        self.fail_with = fail_with
        self.usage = usage
        self.drift_input_hash = drift_input_hash
        self.derive_calls = 0
        self.evaluate_calls = 0

    def _outcome(self, result: DeriveResult | EvaluateResult) -> AdapterOutcome:
        basis = (
            CostBasis.REPORTED_TOKENS if self.usage is not None else CostBasis.UNREPORTED_TOKENS
        )
        return AdapterOutcome(result=result, cost_basis=basis, usage=self.usage)

    def run_derive(self, request: DeriveRequest, route: RouteConfig) -> AdapterOutcome:
        self.derive_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        source = _derive_request(drifted=True) if self.drift_input_hash else request
        return self._outcome(_derive_result(source, model_id=route.model_id))

    def run_evaluate(self, request: EvaluateRequest, route: RouteConfig) -> AdapterOutcome:
        self.evaluate_calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        return self._outcome(_evaluate_result(request, model_id=route.model_id))


def _route(task_kind: TaskKind, model_id: str, adapter_id: str) -> RouteConfig:
    return RouteConfig(task_kind=task_kind, model_id=model_id, adapter_id=adapter_id)


def _router(
    routes: Iterable[RouteConfig],
    adapters: Mapping[str, Any],
    *,
    ledger: RunLedger | None = None,
) -> ModelRouter:
    return ModelRouter(
        route_table=RouteTable(routes=tuple(routes)),
        adapters=adapters,
        registry=_registry(),
        ledger=ledger if ledger is not None else _ledger(),
    )


# --- boundary conformance -----------------------------------------------------


def test_router_satisfies_the_boundary_protocols() -> None:
    router = _router(
        [_route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a")],
        {"stub-a": StubAdapter(STUB_A_MODEL)},
    )
    assert isinstance(router, DeriveModel)
    assert isinstance(router, EvaluateModel)


def test_no_vendor_sdk_imports_in_routing_or_cost() -> None:
    """Precursor of #348's CI lint: the routing layer stays vendor-free."""

    import cortex.hosted.cost as cost_module

    for module in (routing_module, cost_module):
        assert module.__file__ is not None
        source = Path(module.__file__).read_text(encoding="utf-8")
        for vendor in ("anthropic", "openai", "google.genai", "mistral", "cohere", "litellm"):
            assert f"import {vendor}" not in source
            assert f"from {vendor}" not in source


# --- route table is configuration --------------------------------------------


def test_route_table_round_trips_through_canonical_json() -> None:
    table = RouteTable(
        routes=(
            _route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a"),
            _route(TaskKind.DERIVE, STUB_B_MODEL, "stub-b"),
            _route(TaskKind.EVALUATE, CLAUDE_MODEL, "claude-cli"),
        )
    )
    loaded = RouteTable.from_json(table.to_canonical_json())
    assert loaded.as_payload() == table.as_payload()
    assert loaded.to_canonical_json() == table.to_canonical_json()


def test_route_table_refuses_unknown_schema_and_task_kinds() -> None:
    with pytest.raises(RoutingError, match="route_table_schema_version"):
        RouteTable.from_json(json.dumps({"route_table_schema_version": 99, "routes": []}))
    with pytest.raises(RoutingError, match="unknown task_kind"):
        RouteConfig.from_payload(
            {"task_kind": "transcribe", "model_id": STUB_A_MODEL, "adapter_id": "stub-a"}
        )
    with pytest.raises(RoutingError, match="duplicate route"):
        RouteTable(
            routes=(
                _route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a"),
                _route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a"),
            )
        )


def test_route_params_must_be_json_serializable() -> None:
    with pytest.raises(RoutingError, match="JSON-serializable"):
        RouteConfig(
            task_kind=TaskKind.DERIVE,
            model_id=STUB_A_MODEL,
            adapter_id="stub-a",
            params={"bad": object()},
        )


def test_changing_a_route_is_a_config_edit_with_no_caller_change() -> None:
    """Two providers; the caller sees only the DeriveModel protocol."""

    def business_logic(model: DeriveModel) -> DeriveResult:
        # No adapter types, no model names, no branching.
        return model.derive(_derive_request())

    stub_a = StubAdapter(STUB_A_MODEL)
    stub_b = StubAdapter(STUB_B_MODEL)
    adapters = {"stub-a": stub_a, "stub-b": stub_b}

    config_v1 = RouteTable(
        routes=(_route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a"),)
    ).to_canonical_json()
    config_v2 = RouteTable(
        routes=(_route(TaskKind.DERIVE, STUB_B_MODEL, "stub-b"),)
    ).to_canonical_json()

    router_v1 = ModelRouter(
        route_table=RouteTable.from_json(config_v1),
        adapters=adapters,
        registry=_registry(),
        ledger=_ledger(),
    )
    assert business_logic(router_v1).model_id == STUB_A_MODEL

    router_v2 = ModelRouter(
        route_table=RouteTable.from_json(config_v2),
        adapters=adapters,
        registry=_registry(),
        ledger=_ledger(),
    )
    assert business_logic(router_v2).model_id == STUB_B_MODEL
    assert stub_a.derive_calls == 1
    assert stub_b.derive_calls == 1


def test_router_construction_fails_closed_on_bad_config() -> None:
    with pytest.raises(RoutingError, match="not registered with this router"):
        _router([_route(TaskKind.DERIVE, STUB_A_MODEL, "missing-adapter")], {})
    with pytest.raises(RegistryValidationError, match="not registered"):
        _router(
            [_route(TaskKind.DERIVE, "ghost/model", "stub-a")],
            {"stub-a": StubAdapter("ghost/model")},
        )


def test_task_without_a_route_fails_loudly() -> None:
    router = _router(
        [_route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a")],
        {"stub-a": StubAdapter(STUB_A_MODEL)},
    )
    with pytest.raises(RoutingError, match="no route configured for task 'evaluate'"):
        router.evaluate(_evaluate_request())


# --- visible fallback ---------------------------------------------------------


def test_fallback_is_visible_and_each_call_records_cost(caplog: pytest.LogCaptureFixture) -> None:
    ledger = _ledger()
    failing = StubAdapter(STUB_A_MODEL, fail_with=RoutingError("primary exploded"))
    working = StubAdapter(STUB_B_MODEL)
    router = _router(
        [
            _route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a"),
            _route(TaskKind.DERIVE, STUB_B_MODEL, "stub-b"),
        ],
        {"stub-a": failing, "stub-b": working},
        ledger=ledger,
    )
    with caplog.at_level(logging.WARNING, logger="cortex.hosted.routing"):
        result = router.derive(_derive_request())

    assert result.model_id == STUB_B_MODEL
    assert len(router.fallback_records) == 1
    record = router.fallback_records[0]
    assert record.failed_adapter_id == "stub-a"
    assert record.failed_model_id == STUB_A_MODEL
    assert "primary exploded" in record.failure
    assert record.fallback_adapter_id == "stub-b"
    assert record.fallback_model_id == STUB_B_MODEL
    assert any("route fallback" in message for message in caplog.messages)

    # Exactly one cost record per routed call: one failed, one ok.
    assert ledger.call_count == 2
    outcomes = [entry.record.outcome for entry in ledger.entries]
    assert outcomes == [CallOutcome.FAILED, CallOutcome.OK]
    assert ledger.entries[0].record.model_id == STUB_A_MODEL
    assert "primary exploded" in (ledger.entries[0].record.failure_reason or "")


def test_exhausted_routes_raise_a_summary_naming_every_failure() -> None:
    ledger = _ledger()
    router = _router(
        [
            _route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a"),
            _route(TaskKind.DERIVE, STUB_B_MODEL, "stub-b"),
        ],
        {
            "stub-a": StubAdapter(STUB_A_MODEL, fail_with=RoutingError("a down")),
            "stub-b": StubAdapter(STUB_B_MODEL, fail_with=RoutingError("b down")),
        },
        ledger=ledger,
    )
    with pytest.raises(RoutingError, match=r"all 2 route\(s\) failed.*a down.*b down"):
        router.derive(_derive_request())
    assert ledger.call_count == 2
    assert all(entry.record.outcome is CallOutcome.FAILED for entry in ledger.entries)


# --- binding and stamping enforcement -----------------------------------------


def test_drifted_result_is_refused_and_recorded_as_failure() -> None:
    ledger = _ledger()
    router = _router(
        [_route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a")],
        {"stub-a": StubAdapter(STUB_A_MODEL, drift_input_hash=True)},
        ledger=ledger,
    )
    with pytest.raises(ModelInterfaceValidationError, match="does not match request"):
        router.derive(_derive_request())
    assert ledger.call_count == 1
    failed = ledger.entries[0].record
    assert failed.outcome is CallOutcome.FAILED
    assert "does not match request" in (failed.failure_reason or "")


def test_model_id_drift_against_the_route_is_refused() -> None:
    class WrongModelAdapter(StubAdapter):
        def run_derive(self, request: DeriveRequest, route: RouteConfig) -> AdapterOutcome:
            del route
            return self._outcome(_derive_result(request, model_id=STUB_B_MODEL))

    router = _router(
        [_route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a")],
        {"stub-a": WrongModelAdapter(STUB_A_MODEL)},
    )
    with pytest.raises(RoutingError, match="refusing drifted stamping"):
        router.derive(_derive_request())


# --- recorded-response adapter -------------------------------------------------


def test_recorded_adapter_round_trips_through_fixture_file(tmp_path: Path) -> None:
    derive_request = _derive_request()
    evaluate_request = _evaluate_request()
    recorded = RecordedResponseAdapter(
        derive_results=[_derive_result(derive_request, model_id=STUB_A_MODEL)],
        evaluate_results=[_evaluate_result(evaluate_request, model_id=STUB_A_MODEL)],
    )
    fixture = tmp_path / "recorded_responses.json"
    fixture.write_text(recorded.to_canonical_json(), encoding="utf-8")

    loaded = RecordedResponseAdapter.from_json(fixture.read_text(encoding="utf-8"))
    assert loaded.as_payload() == recorded.as_payload()

    ledger = _ledger()
    router = _router(
        [
            _route(TaskKind.DERIVE, STUB_A_MODEL, "recorded"),
            _route(TaskKind.EVALUATE, STUB_A_MODEL, "recorded"),
        ],
        {"recorded": loaded},
        ledger=ledger,
    )
    derived = router.derive(derive_request)
    assert derived == _derive_result(derive_request, model_id=STUB_A_MODEL)
    evaluated = router.evaluate(evaluate_request)
    assert evaluated == _evaluate_result(evaluate_request, model_id=STUB_A_MODEL)

    # Replayed calls report zero live cost (#335).
    assert ledger.call_count == 2
    assert all(
        entry.record.cost_basis is CostBasis.RECORDED_PLAYBACK for entry in ledger.entries
    )
    assert ledger.summary_payload()["known_usd_total"] == 0.0


def test_result_payloads_round_trip_standalone() -> None:
    derive_request = _derive_request()
    derive_result = _derive_result(derive_request, model_id=STUB_A_MODEL)
    assert derive_result_from_payload(derive_result_as_payload(derive_result)) == derive_result

    evaluate_request = _evaluate_request()
    evaluate_result = _evaluate_result(evaluate_request, model_id=STUB_A_MODEL)
    assert (
        evaluate_result_from_payload(evaluate_result_as_payload(evaluate_result))
        == evaluate_result
    )


def test_recorded_span_hash_drift_is_refused() -> None:
    derive_request = _derive_request()
    payload = derive_result_as_payload(_derive_result(derive_request, model_id=STUB_A_MODEL))
    payload["candidates"][0]["spans"][0]["span_hash"] = hashlib.sha256(b"drift").hexdigest()
    with pytest.raises(RoutingError, match="recording drifted"):
        derive_result_from_payload(payload)


def test_missing_recording_is_a_named_failure_never_a_live_call() -> None:
    request = _derive_request()
    router = _router(
        [_route(TaskKind.DERIVE, STUB_A_MODEL, "recorded")],
        {"recorded": RecordedResponseAdapter()},
    )
    with pytest.raises(RoutingError, match=request.input_hash):
        router.derive(request)


def test_stale_recording_for_another_model_is_refused() -> None:
    request = _derive_request()
    recorded = RecordedResponseAdapter(
        derive_results=[_derive_result(request, model_id=STUB_A_MODEL)]
    )
    router = _router(
        [_route(TaskKind.DERIVE, STUB_B_MODEL, "recorded")],
        {"recorded": recorded},
    )
    with pytest.raises(RoutingError, match="stale recordings"):
        router.derive(request)


# --- claude CLI adapter ---------------------------------------------------------


def _fake_claude(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str | None = None,
    returncode: int = 0,
    stderr: str = "",
    which: str | None = "/usr/bin/claude",
) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def fake_which(binary: str) -> str | None:
        calls["which"] = binary
        return which

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout=stdout or "", stderr=stderr
        )

    monkeypatch.setattr("cortex.hosted.routing.shutil.which", fake_which)
    monkeypatch.setattr("cortex.hosted.routing.subprocess.run", fake_run)
    return calls


def _claude_router(ledger: RunLedger | None = None) -> ModelRouter:
    return _router(
        [
            _route(TaskKind.DERIVE, CLAUDE_MODEL, "claude-cli"),
            _route(TaskKind.EVALUATE, CLAUDE_MODEL, "claude-cli"),
        ],
        {"claude-cli": ClaudeCliAdapter()},
        ledger=ledger,
    )


def test_claude_adapter_command_shape_and_json_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    document = _document()
    model_payload = {
        "candidates": [
            {
                "decision_text": "Webhook retries use exponential backoff with jitter.",
                "spans": [{"start_offset": 0, "end_offset": len(document.content)}],
            }
        ],
        "dropped": [{"reason_code": "smalltalk", "excerpt": "lgtm!"}],
        "degraded_reasons": [],
    }
    envelope = {
        "type": "result",
        "is_error": False,
        "result": json.dumps(model_payload),
        "usage": {"input_tokens": 1200, "output_tokens": 300},
        "total_cost_usd": 99.0,  # deliberately ignored: price table is the basis
    }
    calls = _fake_claude(monkeypatch, stdout=json.dumps(envelope))
    ledger = _ledger()
    router = _claude_router(ledger)

    result = router.derive(_derive_request())

    assert calls["cmd"] == ["/usr/bin/claude", "-p", "--output-format", "json"]
    assert calls["kwargs"]["text"] is True
    assert calls["kwargs"]["capture_output"] is True
    assert calls["kwargs"]["check"] is False
    assert document.content in calls["kwargs"]["input"]

    assert result.model_id == CLAUDE_MODEL
    assert result.input_hash == _derive_request().input_hash
    expected_span = document.span(start_offset=0, end_offset=len(document.content))
    assert result.candidates[0].spans[0].span_hash == expected_span.span_hash
    assert result.dropped[0].excerpt_hash == hashlib.sha256(b"lgtm!").hexdigest()

    entry = ledger.entries[0]
    assert entry.record.cost_basis is CostBasis.REPORTED_TOKENS
    assert entry.record.usage == TokenUsage(input_tokens=1200, output_tokens=300)
    assert entry.usd == pytest.approx((1200 * 3.0 + 300 * 15.0) / 1_000_000)


def test_claude_adapter_missing_binary_is_a_call_time_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("cortex.hosted.routing.shutil.which", lambda binary: None)

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess must not run when the binary is missing")

    monkeypatch.setattr("cortex.hosted.routing.subprocess.run", explode)
    adapter = ClaudeCliAdapter()  # constructing without the binary is fine
    with pytest.raises(ClaudeCliUnavailableError, match="'claude' not found on PATH"):
        adapter.run_derive(
            _derive_request(), _route(TaskKind.DERIVE, CLAUDE_MODEL, "claude-cli")
        )


def test_claude_adapter_parse_failure_is_visible_and_cost_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    envelope = {
        "is_error": False,
        "result": "Sure! Here are the decisions I found: ...",
        "usage": {"input_tokens": 1000, "output_tokens": 50},
    }
    _fake_claude(monkeypatch, stdout=json.dumps(envelope))
    ledger = _ledger()
    router = _claude_router(ledger)
    with pytest.raises(RoutingError, match="not valid JSON"):
        router.derive(_derive_request())

    # The failed call still accounts for the tokens the envelope reported.
    assert ledger.call_count == 1
    failed = ledger.entries[0].record
    assert failed.outcome is CallOutcome.FAILED
    assert failed.cost_basis is CostBasis.REPORTED_TOKENS
    assert failed.usage == TokenUsage(input_tokens=1000, output_tokens=50)
    assert "not valid JSON" in (failed.failure_reason or "")


def test_claude_adapter_nonzero_exit_and_error_envelope_are_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_claude(monkeypatch, stdout="", returncode=3, stderr="boom")
    with pytest.raises(RoutingError, match=r"exited 3.*boom"):
        _claude_router().derive(_derive_request())

    _fake_claude(
        monkeypatch,
        stdout=json.dumps({"is_error": True, "result": "credit exhausted"}),
    )
    with pytest.raises(RoutingError, match="credit exhausted"):
        _claude_router().derive(_derive_request())


def test_claude_adapter_missing_usage_degrades_to_unreported_not_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    model_payload = {
        "candidates": [
            {
                "decision_text": "Webhook retries use exponential backoff with jitter.",
                "spans": [{"start_offset": 0, "end_offset": len(document.content)}],
            }
        ],
    }
    envelope = {"is_error": False, "result": json.dumps(model_payload)}
    _fake_claude(monkeypatch, stdout=json.dumps(envelope))
    ledger = _ledger()
    _claude_router(ledger).derive(_derive_request())
    record = ledger.entries[0].record
    assert record.cost_basis is CostBasis.UNREPORTED_TOKENS
    assert record.usage is None
    assert ledger.entries[0].usd is None


def test_claude_evaluate_refuses_span_hashes_outside_the_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finding = {
        "finding_class": FindingClass.CONTRADICTS_PRIOR_DECISION.value,
        "decision_node_id": "3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
        "cited_span_hashes": [hashlib.sha256(b"fabricated").hexdigest()],
        "summary": "Fixed 5s retry contradicts the confirmed backoff decision.",
        "confidence_label": "advisory",
        "suggested_repair": None,
    }
    model_payload = {"findings": [finding], "omitted_decision_count": 0}
    envelope = {"is_error": False, "result": json.dumps(model_payload)}
    _fake_claude(monkeypatch, stdout=json.dumps(envelope))
    with pytest.raises(RoutingError, match="not present in the candidate pack"):
        _claude_router().evaluate(_evaluate_request())


def test_claude_evaluate_accepts_pack_cited_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    finding = {
        "finding_class": FindingClass.CONTRADICTS_PRIOR_DECISION.value,
        "decision_node_id": "3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
        "cited_span_hashes": [PACK_SPAN_HASH],
        "summary": "Fixed 5s retry contradicts the confirmed backoff decision.",
        "confidence_label": "advisory",
        "suggested_repair": None,
    }
    model_payload = {"findings": [finding], "omitted_decision_count": 1}
    envelope = {"is_error": False, "result": json.dumps(model_payload)}
    calls = _fake_claude(monkeypatch, stdout=json.dumps(envelope))
    request = _evaluate_request()
    result = _claude_router().evaluate(request)
    # The diff travels JSON-escaped inside the prompt's task block.
    assert json.dumps(request.diff_patch) in calls["kwargs"]["input"]
    assert result.findings[0].cited_span_hashes == (PACK_SPAN_HASH,)
    assert result.omitted_decision_count == 1
    assert result.input_hash == request.input_hash


# --- budget integration ---------------------------------------------------------


def test_budget_ceiling_refuses_before_the_adapter_is_invoked() -> None:
    ledger = _ledger(budget=RunBudget(max_calls=1))
    stub = StubAdapter(STUB_A_MODEL)
    router = _router(
        [_route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a")],
        {"stub-a": stub},
        ledger=ledger,
    )
    router.derive(_derive_request())
    with pytest.raises(BudgetExceededError, match="1 call"):
        router.derive(_derive_request())
    assert stub.derive_calls == 1  # the refused call never reached the adapter
    assert ledger.call_count == 1  # and wrote no cost record


def test_usd_overrun_is_marked_then_blocks_the_next_call() -> None:
    ledger = _ledger(budget=RunBudget(max_usd=10.0))
    expensive_usage = TokenUsage(input_tokens=5_000_000, output_tokens=1_000_000)  # 30 USD
    stub = StubAdapter(STUB_A_MODEL, usage=expensive_usage)
    router = _router(
        [_route(TaskKind.DERIVE, STUB_A_MODEL, "stub-a")],
        {"stub-a": stub},
        ledger=ledger,
    )
    router.derive(_derive_request())
    assert ledger.entries[0].over_budget is True  # discovered after, recorded visibly
    with pytest.raises(BudgetExceededError, match=r"\$10"):
        router.derive(_derive_request())
    assert stub.derive_calls == 1
