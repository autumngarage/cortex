"""Tests for the narrow derive/evaluate model interfaces (cortex#344)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from cortex.hosted.ask_ledger import CitedSourceSpan
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
    ensure_result_binds_request,
)
from cortex.hosted.model_registry import RegisteredPrompt
from cortex.hosted.provenance import SourceDocument

TENANT = "0b6f9f3e-3a2f-4f2e-9c8d-1a2b3c4d5e6f"
SOURCE = "1c7f8e2d-4b3a-4c5d-8e9f-2b3c4d5e6f70"

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
        prompt_version=PROMPT.prompt_version,
    )


def _candidate() -> DeriveCandidate:
    doc = _document()
    span = doc.span(start_offset=0, end_offset=len(doc.content))
    return DeriveCandidate(
        decision_text="Webhook retries use exponential backoff with jitter.",
        spans=(span,),
    )


def _derive_result(request: DeriveRequest) -> DeriveResult:
    return DeriveResult(
        candidates=(_candidate(),),
        model_id="anthropic/claude-fable-5",
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


def _finding() -> FindingDraft:
    return FindingDraft(
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_node_id="3d9e8f7a-5b4c-4d6e-9f80-3c4d5e6f7a81",
        cited_span_hashes=(hashlib.sha256(b"span").hexdigest(),),
        summary="Fixed 5s retry contradicts the confirmed backoff decision.",
        confidence_label="advisory",
    )


def test_derive_request_input_hash_is_deterministic_and_content_sensitive() -> None:
    assert _derive_request().input_hash == _derive_request().input_hash
    other = DeriveRequest(
        source_document=_document(),
        prompt_version=PROMPT.prompt_version,
        metadata={"lane": "structured"},
    )
    assert other.input_hash != _derive_request().input_hash


def test_derive_candidate_requires_citation() -> None:
    with pytest.raises(ModelInterfaceValidationError, match="at least one source span"):
        DeriveCandidate(decision_text="Uncited.", spans=())


def test_finding_requires_citation_and_confidence() -> None:
    with pytest.raises(ModelInterfaceValidationError, match="at least one cited span"):
        FindingDraft(
            finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
            decision_node_id="node",
            cited_span_hashes=(),
            summary="No citation.",
            confidence_label="advisory",
        )
    with pytest.raises(ModelInterfaceValidationError, match="confidence_label"):
        FindingDraft(
            finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
            decision_node_id="node",
            cited_span_hashes=(hashlib.sha256(b"s").hexdigest(),),
            summary="ok",
            confidence_label="  ",
        )


def test_results_require_the_atomic_stamp() -> None:
    request = _derive_request()
    with pytest.raises(ModelInterfaceValidationError, match="model_id"):
        DeriveResult(
            candidates=(),
            model_id="  ",
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
        )
    with pytest.raises(ModelInterfaceValidationError, match="not canonical"):
        DeriveResult(
            candidates=(),
            model_id="anthropic/claude-fable-5",
            prompt_version="v1-not-canonical",
            input_hash=request.input_hash,
        )


def test_result_binding_detects_drift() -> None:
    request = _derive_request()
    result = _derive_result(request)
    ensure_result_binds_request(request, result)

    drifted = DeriveRequest(
        source_document=_document(),
        prompt_version=PROMPT.prompt_version,
        metadata={"changed": True},
    )
    with pytest.raises(ModelInterfaceValidationError, match="does not match request"):
        ensure_result_binds_request(drifted, result)


def test_evaluate_round_trip_binds() -> None:
    request = _evaluate_request()
    result = EvaluateResult(
        findings=(_finding(),),
        model_id="anthropic/claude-fable-5",
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
        omitted_decision_count=2,
    )
    ensure_result_binds_request(request, result)
    assert result.omitted_decision_count == 2


def test_protocols_accept_structural_implementations() -> None:
    class FakeDerive:
        def derive(self, request: DeriveRequest) -> DeriveResult:
            return _derive_result(request)

    class FakeEvaluate:
        def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
            return EvaluateResult(
                findings=(),
                model_id="anthropic/claude-fable-5",
                prompt_version=request.prompt_version,
                input_hash=request.input_hash,
            )

    assert isinstance(FakeDerive(), DeriveModel)
    assert isinstance(FakeEvaluate(), EvaluateModel)
    request = _derive_request()
    ensure_result_binds_request(request, FakeDerive().derive(request))


def test_boundary_module_imports_no_vendor_sdk() -> None:
    """Precursor of #348's CI lint: the boundary stays vendor-free."""

    from pathlib import Path

    import cortex.hosted.model_interfaces as boundary

    source = Path(boundary.__file__).read_text(encoding="utf-8")
    for vendor in ("anthropic", "openai", "google.genai", "mistral", "cohere", "litellm"):
        assert f"import {vendor}" not in source
        assert f"from {vendor}" not in source


def test_dropped_chatter_is_visible_and_validated() -> None:
    with pytest.raises(ModelInterfaceValidationError, match="reason_code"):
        DroppedChatter(reason_code=" ", excerpt_hash=hashlib.sha256(b"x").hexdigest())
    result = _derive_result(_derive_request())
    assert result.dropped[0].reason_code == "smalltalk"
