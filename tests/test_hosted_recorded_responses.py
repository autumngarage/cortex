"""Tests for recorded-response fixtures (cortex#347): CI never calls live models."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)
from cortex.hosted.eval_fixtures import FindingClass, FixtureScope
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
from cortex.hosted.model_registry import RegisteredPrompt
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.recorded_responses import (
    RECORDED_RESPONSE_SCHEMA_VERSION,
    RecordedResponse,
    RecordedResponseError,
    RecordedResponsePlayer,
    RecordedResponseStore,
    RecordedTaskKind,
    RecordingDeriveModel,
    RecordingEvaluateModel,
    ResponseRecorder,
    derive_result_as_payload,
    derive_result_from_payload,
    evaluate_result_as_payload,
    evaluate_result_from_payload,
)
from cortex.hosted.scopes import ScopeType

EXAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "recorded_responses" / "example.json"
)

TENANT = "0b6f9f3e-3a2f-4f2e-9c8d-1a2b3c4d5e6f"
SOURCE = "1c7f8e2d-4b3a-4c5d-8e9f-2b3c4d5e6f70"
MODEL_ID = "anthropic/claude-fable-5"
RECORDED_AT = "2026-06-09T12:00:00+00:00"

DERIVE_PROMPT = RegisteredPrompt(
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


def _derive_request() -> DeriveRequest:
    return DeriveRequest(
        source_document=_document(),
        prompt_version=DERIVE_PROMPT.prompt_version,
    )


def _derive_result(request: DeriveRequest) -> DeriveResult:
    doc = _document()
    candidate = DeriveCandidate(
        decision_text="Webhook retries use exponential backoff with jitter.",
        spans=(doc.span(start_offset=0, end_offset=len(doc.content)),),
        proposed_scopes=(
            FixtureScope(scope_type=ScopeType.PATH, value="src/payments/webhook_client.py"),
        ),
    )
    return DeriveResult(
        candidates=(candidate,),
        model_id=MODEL_ID,
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
        dropped=(
            DroppedChatter(
                reason_code="smalltalk",
                excerpt_hash=hashlib.sha256(b"lgtm!").hexdigest(),
            ),
        ),
        degraded_reasons=("comment-thread-truncated-at-200-items",),
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
                span_hash=hashlib.sha256(b"span").hexdigest(),
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


def _evaluate_result(request: EvaluateRequest) -> EvaluateResult:
    finding = FindingDraft(
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_node_id="3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
        cited_span_hashes=(hashlib.sha256(b"span").hexdigest(),),
        summary="Fixed 5s retry contradicts the confirmed backoff decision.",
        confidence_label="advisory",
        suggested_repair="Restore backoff_with_jitter(attempt) (see cited ADR span).",
    )
    return EvaluateResult(
        findings=(finding,),
        model_id=MODEL_ID,
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
        omitted_decision_count=2,
    )


def _example_store() -> RecordedResponseStore:
    """The exact store committed at ``EXAMPLE_PATH`` (generated by this module)."""

    derive_request = _derive_request()
    evaluate_request = _evaluate_request()
    derive_result = _derive_result(derive_request)
    evaluate_result = _evaluate_result(evaluate_request)
    return RecordedResponseStore(
        (
            RecordedResponse(
                task=RecordedTaskKind.DERIVE,
                input_hash=derive_result.input_hash,
                model_id=derive_result.model_id,
                prompt_version=derive_result.prompt_version,
                recorded_at=RECORDED_AT,
                result_payload=derive_result_as_payload(derive_result),
            ),
            RecordedResponse(
                task=RecordedTaskKind.EVALUATE,
                input_hash=evaluate_result.input_hash,
                model_id=evaluate_result.model_id,
                prompt_version=evaluate_result.prompt_version,
                recorded_at=RECORDED_AT,
                result_payload=evaluate_result_as_payload(evaluate_result),
            ),
        )
    )


# ---------------------------------------------------------------------------
# Format round-trips
# ---------------------------------------------------------------------------


def test_derive_result_payload_round_trips_exactly() -> None:
    result = _derive_result(_derive_request())
    assert derive_result_from_payload(derive_result_as_payload(result)) == result


def test_evaluate_result_payload_round_trips_exactly() -> None:
    result = _evaluate_result(_evaluate_request())
    assert evaluate_result_from_payload(evaluate_result_as_payload(result)) == result
    bare = EvaluateResult(
        findings=(),
        model_id=MODEL_ID,
        prompt_version=EVAL_PROMPT.prompt_version,
        input_hash=_evaluate_request().input_hash,
    )
    assert evaluate_result_from_payload(evaluate_result_as_payload(bare)) == bare


def test_store_canonical_json_round_trips_byte_identically() -> None:
    raw = _example_store().to_canonical_json()
    reloaded = RecordedResponseStore.from_json(raw)
    assert reloaded.to_canonical_json() == raw


def test_tampered_span_hash_fails_visibly() -> None:
    payload = derive_result_as_payload(_derive_result(_derive_request()))
    payload["candidates"][0]["spans"][0]["span_hash"] = hashlib.sha256(b"oops").hexdigest()
    with pytest.raises(RecordedResponseError, match="span_hash does not match"):
        derive_result_from_payload(payload)


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------


def test_unknown_schema_version_fails_visibly() -> None:
    payload = json.loads(_example_store().to_canonical_json())
    payload["recorded_response_schema_version"] = RECORDED_RESPONSE_SCHEMA_VERSION + 1
    with pytest.raises(RecordedResponseError, match="unknown recorded_response_schema_version"):
        RecordedResponseStore.from_payload(payload)


def test_missing_schema_version_fails_visibly() -> None:
    payload = json.loads(_example_store().to_canonical_json())
    del payload["recorded_response_schema_version"]
    with pytest.raises(RecordedResponseError, match="recorded_response_schema_version"):
        RecordedResponseStore.from_payload(payload)


def test_unknown_task_kind_fails_visibly() -> None:
    payload = json.loads(_example_store().to_canonical_json())
    payload["responses"][0]["task"] = "summarize"
    with pytest.raises(RecordedResponseError, match="unknown task kind 'summarize'"):
        RecordedResponseStore.from_payload(payload)


# ---------------------------------------------------------------------------
# Entry consistency (a recording that drifts from its key is unrepresentable)
# ---------------------------------------------------------------------------


def test_recording_must_match_its_result_payload() -> None:
    request = _derive_request()
    result = _derive_result(request)
    payload = derive_result_as_payload(result)
    with pytest.raises(RecordedResponseError, match="does not match the result"):
        RecordedResponse(
            task=RecordedTaskKind.DERIVE,
            input_hash=hashlib.sha256(b"some-other-request").hexdigest(),
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            recorded_at=RECORDED_AT,
            result_payload=payload,
        )
    with pytest.raises(RecordedResponseError, match="model_id"):
        RecordedResponse(
            task=RecordedTaskKind.DERIVE,
            input_hash=result.input_hash,
            model_id="anthropic/some-other-route",
            prompt_version=result.prompt_version,
            recorded_at=RECORDED_AT,
            result_payload=payload,
        )


def test_recorded_at_must_be_timezone_aware_iso8601() -> None:
    result = _derive_result(_derive_request())
    for bad in ("2026-06-09T12:00:00", "yesterday", "  "):
        with pytest.raises(RecordedResponseError, match="recorded_at"):
            RecordedResponse(
                task=RecordedTaskKind.DERIVE,
                input_hash=result.input_hash,
                model_id=result.model_id,
                prompt_version=result.prompt_version,
                recorded_at=bad,
                result_payload=derive_result_as_payload(result),
            )


def test_store_add_is_idempotent_but_never_overwrites() -> None:
    request = _derive_request()
    result = _derive_result(request)
    recording = RecordedResponse(
        task=RecordedTaskKind.DERIVE,
        input_hash=result.input_hash,
        model_id=result.model_id,
        prompt_version=result.prompt_version,
        recorded_at=RECORDED_AT,
        result_payload=derive_result_as_payload(result),
    )
    store = RecordedResponseStore((recording,))
    assert store.add(recording) == recording
    assert len(store.responses) == 1

    rerouted = DeriveResult(
        candidates=result.candidates,
        model_id="anthropic/some-other-route",
        prompt_version=result.prompt_version,
        input_hash=result.input_hash,
        dropped=result.dropped,
        degraded_reasons=result.degraded_reasons,
    )
    drifted = RecordedResponse(
        task=RecordedTaskKind.DERIVE,
        input_hash=rerouted.input_hash,
        model_id=rerouted.model_id,
        prompt_version=rerouted.prompt_version,
        recorded_at=RECORDED_AT,
        result_payload=derive_result_as_payload(rerouted),
    )
    with pytest.raises(RecordedResponseError, match="append-only"):
        store.add(drifted)


# ---------------------------------------------------------------------------
# Recorder tee
# ---------------------------------------------------------------------------


class _FakeDeriveModel:
    def derive(self, request: DeriveRequest) -> DeriveResult:
        return _derive_result(request)


class _FakeEvaluateModel:
    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        return _evaluate_result(request)


def test_recorder_tees_results_to_the_fixture_file(tmp_path: Path) -> None:
    fixture_path = tmp_path / "recordings" / "session.json"
    recorder = ResponseRecorder(fixture_path=fixture_path, recorded_at=RECORDED_AT)
    derive_model = RecordingDeriveModel(_FakeDeriveModel(), recorder)
    evaluate_model = RecordingEvaluateModel(_FakeEvaluateModel(), recorder)
    assert isinstance(derive_model, DeriveModel)
    assert isinstance(evaluate_model, EvaluateModel)

    derive_request = _derive_request()
    evaluate_request = _evaluate_request()
    derive_result = derive_model.derive(derive_request)
    evaluate_result = evaluate_model.evaluate(evaluate_request)

    player = RecordedResponsePlayer.load(fixture_path)
    assert isinstance(player, DeriveModel)
    assert isinstance(player, EvaluateModel)
    assert player.derive(derive_request) == derive_result
    assert player.evaluate(evaluate_request) == evaluate_result


def test_recorder_refuses_a_result_that_does_not_bind_its_request(tmp_path: Path) -> None:
    recorder = ResponseRecorder(
        fixture_path=tmp_path / "session.json", recorded_at=RECORDED_AT
    )
    bound_request = _derive_request()
    other_request = DeriveRequest(
        source_document=_document(),
        prompt_version=DERIVE_PROMPT.prompt_version,
        metadata={"lane": "structured"},
    )
    with pytest.raises(ModelInterfaceValidationError, match="does not match request"):
        recorder.record_derive(other_request, _derive_result(bound_request))


# ---------------------------------------------------------------------------
# Replay misses fail visibly — never a silent live call
# ---------------------------------------------------------------------------


def test_replay_miss_names_the_hash_and_the_fixture_file(tmp_path: Path) -> None:
    fixture_path = tmp_path / "session.json"
    recorder = ResponseRecorder(fixture_path=fixture_path, recorded_at=RECORDED_AT)
    derive_request = _derive_request()
    recorder.record_derive(derive_request, _derive_result(derive_request))

    player = RecordedResponsePlayer.load(fixture_path)
    stale_request = DeriveRequest(
        source_document=_document(),
        prompt_version=DERIVE_PROMPT.prompt_version,
        metadata={"prompt-or-input": "changed"},
    )
    with pytest.raises(RecordedResponseError) as derive_miss:
        player.derive(stale_request)
    assert stale_request.input_hash in str(derive_miss.value)
    assert str(fixture_path) in str(derive_miss.value)
    assert "never a silent live call" in str(derive_miss.value)

    evaluate_request = _evaluate_request()
    with pytest.raises(RecordedResponseError) as evaluate_miss:
        player.evaluate(evaluate_request)
    assert evaluate_request.input_hash in str(evaluate_miss.value)
    assert str(fixture_path) in str(evaluate_miss.value)


def test_missing_fixture_file_fails_visibly(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "absent.json"
    with pytest.raises(RecordedResponseError, match="does not exist"):
        RecordedResponsePlayer.load(missing)


# ---------------------------------------------------------------------------
# The committed example fixture (generated by this module, byte-frozen)
# ---------------------------------------------------------------------------


def test_committed_example_fixture_matches_module_construction() -> None:
    raw = EXAMPLE_PATH.read_text(encoding="utf-8")
    assert _example_store().to_canonical_json() == raw


def test_committed_example_fixture_round_trips_byte_identically() -> None:
    raw = EXAMPLE_PATH.read_text(encoding="utf-8")
    reloaded = RecordedResponseStore.from_json(raw)
    assert reloaded.to_canonical_json() == raw


def test_committed_example_fixture_replays_through_the_player() -> None:
    player = RecordedResponsePlayer.load(EXAMPLE_PATH)
    derive_request = _derive_request()
    evaluate_request = _evaluate_request()
    assert player.derive(derive_request) == _derive_result(derive_request)
    assert player.evaluate(evaluate_request) == _evaluate_result(evaluate_request)
