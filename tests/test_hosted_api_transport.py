"""Tests for the server-side API-key HTTP transport adapter (cortex#517).

The adapter is exercised exclusively through dependency-injected seams
(opener / sleep / environ) — no network, no real keys, no vendor SDK. The
byte-equivalence suite proves recordings produced through this adapter and
through the claude-CLI adapter are one format, not two.
"""

from __future__ import annotations

import email.message
import hashlib
import io
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from cortex.hosted.api_transport import (
    API_HTTP_ADAPTER_ID,
    API_KEY_REMEDIATION,
    DEFAULT_API_ENDPOINT,
    DEFAULT_API_KEY_ENV,
    DEFAULT_API_TIMEOUT_SECONDS,
    DEFAULT_API_VERSION,
    DEFAULT_MAX_OUTPUT_TOKENS,
    ApiHttpAdapter,
    ApiHttpOutputError,
    ApiKeyMissingError,
)
from cortex.hosted.cost import CallOutcome, CostBasis, TokenUsage
from cortex.hosted.degradation import (
    DegradationMode,
    classify_failure,
    remediation_for,
)
from cortex.hosted.eval_fixtures import FindingClass
from cortex.hosted.model_interfaces import DeriveModel, DeriveResult, EvaluateModel
from cortex.hosted.recorded_responses import (
    derive_result_as_payload,
    evaluate_result_as_payload,
)
from cortex.hosted.routing import (
    ClaudeCliAdapter,
    RecordedResponseAdapter,
    RouteConfig,
    RoutingError,
    TaskKind,
)
from tests.test_hosted_routing import (
    CLAUDE_MODEL,
    PACK_SPAN_HASH,
    _derive_request,
    _derive_result,
    _document,
    _evaluate_request,
    _fake_claude,
    _ledger,
    _route,
    _router,
)
from tests.test_vendor_boundary import (
    HOSTED_DIR,
    MODEL_NAME_LITERAL_ALLOWED,
    SRC_CORTEX,
    VENDOR_IMPORT_ALLOWED,
    model_name_literal_violations,
    vendor_import_violations,
)

SECRET_KEY = "sk-test-key-must-never-appear-in-errors"
USAGE_BLOCK = {"input_tokens": 1200, "output_tokens": 300}
DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "degradation-modes.md"


# --- injected transport fakes ---------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    """Scripted urlopen stand-in: each call consumes one outcome in order."""

    def __init__(self, outcomes: list[Mapping[str, Any] | Exception]) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, *, timeout: float) -> _FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self._outcomes:
            raise AssertionError("opener called more times than the test scripted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResponse(outcome)


class RecordedSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _http_error(code: int, body: str = '{"type": "error"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://models.internal.test/v1/messages",
        code,
        "scripted failure",
        email.message.Message(),
        io.BytesIO(body.encode("utf-8")),
    )


def _adapter(
    opener: FakeOpener,
    *,
    environ: Mapping[str, str] | None = None,
    sleep: RecordedSleep | None = None,
) -> ApiHttpAdapter:
    return ApiHttpAdapter(
        opener=opener,
        sleep=sleep if sleep is not None else RecordedSleep(),
        environ={DEFAULT_API_KEY_ENV: SECRET_KEY} if environ is None else environ,
    )


def _api_route(task_kind: TaskKind, **params: Any) -> RouteConfig:
    return RouteConfig(
        task_kind=task_kind,
        model_id=CLAUDE_MODEL,
        adapter_id=API_HTTP_ADAPTER_ID,
        params=params,
    )


# --- model-output fixtures ------------------------------------------------------


def _derive_model_payload() -> dict[str, Any]:
    document = _document()
    return {
        "candidates": [
            {
                "decision_text": "Webhook retries use exponential backoff with jitter.",
                "spans": [{"start_offset": 0, "end_offset": len(document.content)}],
            }
        ],
        "dropped": [{"reason_code": "smalltalk", "excerpt": "lgtm!"}],
        "degraded_reasons": [],
    }


def _evaluate_model_payload() -> dict[str, Any]:
    return {
        "findings": [
            {
                "finding_class": FindingClass.CONTRADICTS_PRIOR_DECISION.value,
                "decision_node_id": "3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
                "cited_span_hashes": [PACK_SPAN_HASH],
                "summary": "Fixed 5s retry contradicts the confirmed backoff decision.",
                "confidence_label": "advisory",
                "suggested_repair": None,
            }
        ],
        "omitted_decision_count": 0,
        "degraded_reasons": [],
    }


def _envelope(
    model_payload: Mapping[str, Any] | None = None,
    *,
    text: str | None = None,
    usage: Mapping[str, int] | None = USAGE_BLOCK,
    stop_reason: str = "end_turn",
    content: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if content is None:
        body_text = text if text is not None else json.dumps(model_payload)
        content = [{"type": "text", "text": body_text}]
    envelope: dict[str, Any] = {
        "type": "message",
        "role": "assistant",
        "content": content,
        "stop_reason": stop_reason,
    }
    if usage is not None:
        envelope["usage"] = dict(usage)
    return envelope


# --- protocol conformance behind the router -------------------------------------


def test_api_routed_router_satisfies_the_boundary_protocols() -> None:
    opener = FakeOpener([_envelope(_derive_model_payload())])
    router = _router(
        [_api_route(TaskKind.DERIVE)], {API_HTTP_ADAPTER_ID: _adapter(opener)}
    )
    assert isinstance(router, DeriveModel)
    assert isinstance(router, EvaluateModel)
    result = router.derive(_derive_request())
    assert result.model_id == CLAUDE_MODEL
    assert result.input_hash == _derive_request().input_hash


def test_route_config_selects_cli_api_or_recorded_with_unchanged_business_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cortex#517 acceptance criterion: cli|api|recorded is a config edit."""

    def business_logic(model: DeriveModel) -> DeriveResult:
        # No adapter types, no model names, no transport branching.
        return model.derive(_derive_request())

    model_payload = _derive_model_payload()
    cli_envelope = {
        "is_error": False,
        "result": json.dumps(model_payload),
        "usage": dict(USAGE_BLOCK),
    }
    _fake_claude(monkeypatch, stdout=json.dumps(cli_envelope))
    cli_router = _router(
        [_route(TaskKind.DERIVE, CLAUDE_MODEL, "claude-cli")],
        {"claude-cli": ClaudeCliAdapter()},
    )
    cli_result = business_logic(cli_router)

    api_router = _router(
        [_api_route(TaskKind.DERIVE)],
        {API_HTTP_ADAPTER_ID: _adapter(FakeOpener([_envelope(model_payload)]))},
    )
    api_result = business_logic(api_router)

    recorded_router = _router(
        [_route(TaskKind.DERIVE, CLAUDE_MODEL, "recorded")],
        {"recorded": RecordedResponseAdapter(derive_results=[cli_result])},
    )
    recorded_result = business_logic(recorded_router)

    assert cli_result == api_result == recorded_result


# --- the REST shape is an adapter-internal detail driven by params --------------


def test_api_adapter_posts_the_messages_rest_shape_from_route_params() -> None:
    opener = FakeOpener([_envelope(_derive_model_payload())])
    adapter = ApiHttpAdapter(
        opener=opener,
        sleep=RecordedSleep(),
        environ={"CORTEX_MODEL_KEY": "k-route-scoped"},
    )
    route = _api_route(
        TaskKind.DERIVE,
        api_key_env="CORTEX_MODEL_KEY",
        endpoint="https://models.internal.test/v1/messages",
        api_version="2031-01-01",
        api_model="provider/model-prime",
        max_output_tokens=512,
        timeout_seconds=7.5,
    )
    router = _router([route], {API_HTTP_ADAPTER_ID: adapter})
    router.derive(_derive_request())

    request = opener.requests[0]
    assert request.full_url == "https://models.internal.test/v1/messages"
    assert request.get_method() == "POST"
    assert request.get_header("X-api-key") == "k-route-scoped"
    assert request.get_header("Anthropic-version") == "2031-01-01"
    assert request.get_header("Content-type") == "application/json"
    assert opener.timeouts == [7.5]
    assert isinstance(request.data, bytes)
    body = json.loads(request.data.decode("utf-8"))
    assert body["model"] == "provider/model-prime"
    assert body["max_tokens"] == 512
    assert body["messages"][0]["role"] == "user"
    assert _document().content in body["messages"][0]["content"]


def test_api_adapter_defaults_apply_when_params_are_omitted() -> None:
    opener = FakeOpener([_envelope(_derive_model_payload())])
    router = _router(
        [_api_route(TaskKind.DERIVE)], {API_HTTP_ADAPTER_ID: _adapter(opener)}
    )
    router.derive(_derive_request())

    request = opener.requests[0]
    assert request.full_url == DEFAULT_API_ENDPOINT
    assert request.get_header("X-api-key") == SECRET_KEY
    assert request.get_header("Anthropic-version") == DEFAULT_API_VERSION
    assert opener.timeouts == [DEFAULT_API_TIMEOUT_SECONDS]
    assert isinstance(request.data, bytes)
    body = json.loads(request.data.decode("utf-8"))
    # The provider-side model name defaults to the route's registry model id.
    assert body["model"] == CLAUDE_MODEL
    assert body["max_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


# --- key absence is a registered, named degradation -----------------------------


def test_missing_api_key_is_a_registered_degradation_naming_the_var() -> None:
    opener = FakeOpener([])  # must never be called without a key
    adapter = ApiHttpAdapter(opener=opener, sleep=RecordedSleep(), environ={})
    with pytest.raises(ApiKeyMissingError, match=DEFAULT_API_KEY_ENV) as excinfo:
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))
    assert opener.requests == []
    assert API_KEY_REMEDIATION in str(excinfo.value)
    assert classify_failure(excinfo.value) is DegradationMode.DEGRADED_CAPABILITY
    assert DEFAULT_API_KEY_ENV in remediation_for("model_api_key_missing")


def test_blank_api_key_is_treated_as_missing() -> None:
    opener = FakeOpener([])
    adapter = ApiHttpAdapter(
        opener=opener, sleep=RecordedSleep(), environ={DEFAULT_API_KEY_ENV: "   "}
    )
    with pytest.raises(ApiKeyMissingError, match="unset or blank"):
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))
    assert opener.requests == []


def test_custom_api_key_env_param_is_honored_in_the_refusal() -> None:
    opener = FakeOpener([])
    adapter = ApiHttpAdapter(
        opener=opener, sleep=RecordedSleep(), environ={DEFAULT_API_KEY_ENV: SECRET_KEY}
    )
    route = _api_route(TaskKind.DERIVE, api_key_env="CORTEX_MODEL_KEY")
    with pytest.raises(ApiKeyMissingError, match="CORTEX_MODEL_KEY"):
        adapter.run_derive(_derive_request(), route)


# --- bounded retries with capped exponential backoff -----------------------------


def test_retries_on_429_and_5xx_then_succeeds_with_visible_backoff(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleep = RecordedSleep()
    opener = FakeOpener(
        [_http_error(429), _http_error(503), _envelope(_derive_model_payload())]
    )
    ledger = _ledger()
    route = _api_route(TaskKind.DERIVE, max_retries=2, backoff_base_seconds=0.5)
    router = _router(
        [route], {API_HTTP_ADAPTER_ID: _adapter(opener, sleep=sleep)}, ledger=ledger
    )
    with caplog.at_level(logging.WARNING, logger="cortex.hosted.api_transport"):
        result = router.derive(_derive_request())

    assert result.model_id == CLAUDE_MODEL
    assert len(opener.requests) == 3
    assert sleep.delays == [0.5, 1.0]
    retry_lines = [message for message in caplog.messages if "retrying in" in message]
    assert len(retry_lines) == 2
    record = ledger.entries[0].record
    assert record.outcome is CallOutcome.OK
    assert record.cost_basis is CostBasis.REPORTED_TOKENS


def test_backoff_is_capped() -> None:
    sleep = RecordedSleep()
    opener = FakeOpener(
        [_http_error(500), _http_error(500), _envelope(_derive_model_payload())]
    )
    route = _api_route(
        TaskKind.DERIVE,
        max_retries=2,
        backoff_base_seconds=10.0,
        backoff_cap_seconds=12.0,
    )
    router = _router([route], {API_HTTP_ADAPTER_ID: _adapter(opener, sleep=sleep)})
    router.derive(_derive_request())
    # Uncapped this would be [10.0, 20.0]; the cap bounds the second wait.
    assert sleep.delays == [10.0, 12.0]


def test_exhausted_retries_raise_a_visible_error_naming_attempts_and_status() -> None:
    sleep = RecordedSleep()
    opener = FakeOpener([_http_error(429), _http_error(500, '{"detail": "down"}')])
    adapter = _adapter(opener, sleep=sleep)
    route = _api_route(TaskKind.DERIVE, max_retries=1)
    with pytest.raises(ApiHttpOutputError, match=r"after 2 attempt\(s\).*HTTP 500"):
        adapter.run_derive(_derive_request(), route)
    assert len(opener.requests) == 2
    assert len(sleep.delays) == 1


def test_router_falls_back_visibly_when_api_retries_exhaust() -> None:
    """The exhausted-retries error is RouteFallbackRecord-compatible."""

    request = _derive_request()
    opener = FakeOpener([_http_error(500)])
    recorded = RecordedResponseAdapter(
        derive_results=[_derive_result(request, model_id=CLAUDE_MODEL)]
    )
    ledger = _ledger()
    router = _router(
        [
            _api_route(TaskKind.DERIVE, max_retries=0),
            _route(TaskKind.DERIVE, CLAUDE_MODEL, "recorded"),
        ],
        {API_HTTP_ADAPTER_ID: _adapter(opener), "recorded": recorded},
        ledger=ledger,
    )
    result = router.derive(request)

    assert result == _derive_result(request, model_id=CLAUDE_MODEL)
    assert len(router.fallback_records) == 1
    fallback = router.fallback_records[0]
    assert fallback.failed_adapter_id == API_HTTP_ADAPTER_ID
    assert "HTTP 500" in fallback.failure
    assert fallback.fallback_adapter_id == "recorded"
    outcomes = [entry.record.outcome for entry in ledger.entries]
    assert outcomes == [CallOutcome.FAILED, CallOutcome.OK]
    assert ledger.entries[0].record.cost_basis is CostBasis.NO_RESPONSE
    assert ledger.entries[1].record.cost_basis is CostBasis.RECORDED_PLAYBACK


def test_non_retryable_client_error_fails_immediately() -> None:
    sleep = RecordedSleep()
    opener = FakeOpener([_http_error(401, '{"error": "bad key"}')])
    adapter = _adapter(opener, sleep=sleep)
    route = _api_route(TaskKind.DERIVE, max_retries=5)
    with pytest.raises(ApiHttpOutputError, match=r"HTTP 401 \(not retryable\)"):
        adapter.run_derive(_derive_request(), route)
    assert len(opener.requests) == 1
    assert sleep.delays == []


def test_network_failure_is_a_visible_named_error_not_a_bare_traceback() -> None:
    opener = FakeOpener([urllib.error.URLError("dns resolution failed")])
    adapter = _adapter(opener)
    with pytest.raises(ApiHttpOutputError, match="failed before a response"):
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))
    assert len(opener.requests) == 1  # network errors are not retried


def test_api_key_never_leaks_into_failure_messages() -> None:
    for outcomes in (
        [_http_error(500)],
        [_http_error(401)],
        [urllib.error.URLError("boom")],
    ):
        adapter = _adapter(FakeOpener(list(outcomes)))
        route = _api_route(TaskKind.DERIVE, max_retries=0)
        with pytest.raises(RoutingError) as excinfo:
            adapter.run_derive(_derive_request(), route)
        assert SECRET_KEY not in str(excinfo.value)


# --- strict-JSON output contract (shared with the CLI adapter) -------------------


def test_contract_violation_is_refused_and_spend_still_accounted() -> None:
    opener = FakeOpener([_envelope(text="Sure! Here are the decisions I found: ...")])
    ledger = _ledger()
    router = _router(
        [_api_route(TaskKind.DERIVE)], {API_HTTP_ADAPTER_ID: _adapter(opener)}, ledger=ledger
    )
    with pytest.raises(RoutingError, match="violated the contract"):
        router.derive(_derive_request())

    assert ledger.call_count == 1
    failed = ledger.entries[0].record
    assert failed.outcome is CallOutcome.FAILED
    assert failed.cost_basis is CostBasis.REPORTED_TOKENS
    assert failed.usage == TokenUsage(input_tokens=1200, output_tokens=300)
    assert "violated the contract" in (failed.failure_reason or "")


def test_evaluate_refuses_span_hashes_outside_the_pack() -> None:
    payload = _evaluate_model_payload()
    payload["findings"][0]["cited_span_hashes"] = [
        hashlib.sha256(b"fabricated").hexdigest()
    ]
    opener = FakeOpener([_envelope(payload)])
    router = _router(
        [_api_route(TaskKind.EVALUATE)], {API_HTTP_ADAPTER_ID: _adapter(opener)}
    )
    with pytest.raises(RoutingError, match="not present in the candidate pack"):
        router.evaluate(_evaluate_request())


def test_split_text_blocks_are_concatenated_before_parsing() -> None:
    text = json.dumps(_derive_model_payload())
    envelope = _envelope(
        content=[
            {"type": "text", "text": text[:25]},
            {"type": "tool_use", "id": "ignored"},
            {"type": "text", "text": text[25:]},
        ]
    )
    router = _router(
        [_api_route(TaskKind.DERIVE)],
        {API_HTTP_ADAPTER_ID: _adapter(FakeOpener([envelope]))},
    )
    result = router.derive(_derive_request())
    assert result.candidates[0].decision_text == (
        "Webhook retries use exponential backoff with jitter."
    )


def test_response_without_text_blocks_is_refused() -> None:
    envelope = _envelope(content=[{"type": "tool_use", "id": "x"}])
    adapter = _adapter(FakeOpener([envelope]))
    with pytest.raises(ApiHttpOutputError, match="no text content blocks"):
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))


def test_provider_error_envelope_is_refused() -> None:
    envelope = {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Overloaded"},
    }
    adapter = _adapter(FakeOpener([envelope]))
    with pytest.raises(ApiHttpOutputError, match=r"provider error.*overloaded_error"):
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))


def test_truncated_max_tokens_response_is_refused_with_usage_carried() -> None:
    envelope = _envelope(text='{"candidates": [', stop_reason="max_tokens")
    adapter = _adapter(FakeOpener([envelope]))
    with pytest.raises(ApiHttpOutputError, match="max_output_tokens") as excinfo:
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))
    assert excinfo.value.usage == TokenUsage(input_tokens=1200, output_tokens=300)


def test_non_json_response_body_is_refused() -> None:
    class _ProseResponse:
        def read(self) -> bytes:
            return b"<html>gateway timeout</html>"

        def close(self) -> None:
            return None

    class _ProseOpener:
        def __call__(
            self, request: urllib.request.Request, *, timeout: float
        ) -> _ProseResponse:
            return _ProseResponse()

    adapter = ApiHttpAdapter(
        opener=_ProseOpener(),
        sleep=RecordedSleep(),
        environ={DEFAULT_API_KEY_ENV: SECRET_KEY},
    )
    with pytest.raises(ApiHttpOutputError, match="not valid JSON"):
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))


# --- usage accounting -------------------------------------------------------------


def test_usage_block_reports_tokens_and_computes_usd() -> None:
    opener = FakeOpener([_envelope(_derive_model_payload())])
    ledger = _ledger()
    router = _router(
        [_api_route(TaskKind.DERIVE)], {API_HTTP_ADAPTER_ID: _adapter(opener)}, ledger=ledger
    )
    router.derive(_derive_request())
    entry = ledger.entries[0]
    assert entry.record.cost_basis is CostBasis.REPORTED_TOKENS
    assert entry.record.usage == TokenUsage(input_tokens=1200, output_tokens=300)
    assert entry.usd == pytest.approx((1200 * 3.0 + 300 * 15.0) / 1_000_000)


def test_missing_usage_degrades_to_unreported_not_zero() -> None:
    opener = FakeOpener([_envelope(_derive_model_payload(), usage=None)])
    ledger = _ledger()
    router = _router(
        [_api_route(TaskKind.DERIVE)], {API_HTTP_ADAPTER_ID: _adapter(opener)}, ledger=ledger
    )
    router.derive(_derive_request())
    record = ledger.entries[0].record
    assert record.cost_basis is CostBasis.UNREPORTED_TOKENS
    assert record.usage is None
    assert ledger.entries[0].usd is None


def test_malformed_usage_block_is_refused_visibly() -> None:
    envelope = _envelope(
        _derive_model_payload(), usage={"input_tokens": -1, "output_tokens": 2}
    )
    adapter = _adapter(FakeOpener([envelope]))
    with pytest.raises(ApiHttpOutputError, match="usage block violated the contract"):
        adapter.run_derive(_derive_request(), _api_route(TaskKind.DERIVE))


# --- params are validated fail-closed ---------------------------------------------


def test_endpoint_must_be_https() -> None:
    adapter = _adapter(FakeOpener([]))
    route = _api_route(TaskKind.DERIVE, endpoint="http://models.internal.test/v1")
    with pytest.raises(RoutingError, match="must be an https:// URL"):
        adapter.run_derive(_derive_request(), route)


def test_unknown_route_params_are_refused_not_silently_ignored() -> None:
    adapter = _adapter(FakeOpener([]))
    route = _api_route(TaskKind.DERIVE, timout_seconds=5)  # deliberate typo
    with pytest.raises(RoutingError, match=r"unknown key\(s\).*timout_seconds"):
        adapter.run_derive(_derive_request(), route)


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("max_retries", -1),
        ("timeout_seconds", 0),
        ("max_output_tokens", 0),
        ("api_version", 7),
        ("backoff_base_seconds", -0.1),
        ("api_key_env", "   "),
    ],
)
def test_invalid_param_values_are_refused(param: str, value: object) -> None:
    adapter = _adapter(FakeOpener([]))
    route = _api_route(TaskKind.DERIVE, **{param: value})
    with pytest.raises(RoutingError, match=param):
        adapter.run_derive(_derive_request(), route)


# --- one recording format across adapters ------------------------------------------


def test_derive_recordings_from_api_and_cli_adapters_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_payload = _derive_model_payload()
    cli_envelope = {
        "is_error": False,
        "result": json.dumps(model_payload),
        "usage": dict(USAGE_BLOCK),
    }
    _fake_claude(monkeypatch, stdout=json.dumps(cli_envelope))
    cli_router = _router(
        [_route(TaskKind.DERIVE, CLAUDE_MODEL, "claude-cli")],
        {"claude-cli": ClaudeCliAdapter()},
    )
    cli_result = cli_router.derive(_derive_request())

    api_router = _router(
        [_api_route(TaskKind.DERIVE)],
        {API_HTTP_ADAPTER_ID: _adapter(FakeOpener([_envelope(model_payload)]))},
    )
    api_result = api_router.derive(_derive_request())

    assert api_result == cli_result
    assert derive_result_as_payload(api_result) == derive_result_as_payload(cli_result)
    api_bytes = (
        RecordedResponseAdapter(derive_results=[api_result])
        .to_canonical_json()
        .encode("utf-8")
    )
    cli_bytes = (
        RecordedResponseAdapter(derive_results=[cli_result])
        .to_canonical_json()
        .encode("utf-8")
    )
    assert api_bytes == cli_bytes


def test_evaluate_recordings_from_api_and_cli_adapters_are_byte_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_payload = _evaluate_model_payload()
    cli_envelope = {
        "is_error": False,
        "result": json.dumps(model_payload),
        "usage": dict(USAGE_BLOCK),
    }
    _fake_claude(monkeypatch, stdout=json.dumps(cli_envelope))
    cli_router = _router(
        [_route(TaskKind.EVALUATE, CLAUDE_MODEL, "claude-cli")],
        {"claude-cli": ClaudeCliAdapter()},
    )
    cli_result = cli_router.evaluate(_evaluate_request())

    api_router = _router(
        [_api_route(TaskKind.EVALUATE)],
        {API_HTTP_ADAPTER_ID: _adapter(FakeOpener([_envelope(model_payload)]))},
    )
    api_result = api_router.evaluate(_evaluate_request())

    assert api_result == cli_result
    assert evaluate_result_as_payload(api_result) == evaluate_result_as_payload(cli_result)
    api_bytes = (
        RecordedResponseAdapter(evaluate_results=[api_result])
        .to_canonical_json()
        .encode("utf-8")
    )
    cli_bytes = (
        RecordedResponseAdapter(evaluate_results=[cli_result])
        .to_canonical_json()
        .encode("utf-8")
    )
    assert api_bytes == cli_bytes


# --- boundary and taxonomy guardrails stay green ------------------------------------


def test_vendor_boundary_scanners_stay_green_and_cover_api_transport() -> None:
    assert (HOSTED_DIR / "api_transport.py").exists()
    # The new module is subject to both scanners, not allowlisted around them.
    assert "api_transport.py" not in VENDOR_IMPORT_ALLOWED
    assert "api_transport.py" not in MODEL_NAME_LITERAL_ALLOWED
    assert vendor_import_violations(SRC_CORTEX) == []
    assert model_name_literal_violations(HOSTED_DIR) == []


def test_new_error_types_are_registered_in_the_degradation_taxonomy() -> None:
    assert classify_failure(ApiHttpOutputError("probe")) is (
        DegradationMode.FAIL_CLOSED_REFUSAL
    )
    assert classify_failure(ApiKeyMissingError("probe")) is (
        DegradationMode.DEGRADED_CAPABILITY
    )
    doc = DOC_PATH.read_text(encoding="utf-8")
    assert "api_transport.ApiKeyMissingError" in doc
    assert "api_transport.ApiHttpOutputError" in doc
