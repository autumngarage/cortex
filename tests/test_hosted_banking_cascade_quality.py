"""Tests for banking policy (#328), cascade inference (#346), quality series (#342)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from cortex.hosted.banking import (
    BankDecision,
    BankingValidationError,
    BankKey,
    decide,
    drifted_components,
)
from cortex.hosted.cascade import (
    ESCALATE_DEGRADED_PRIMARY,
    ESCALATE_EMPTY_ON_NONEMPTY_PACK,
    CascadeModel,
    CascadePolicy,
    CascadeValidationError,
)
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
    FixtureLabel,
    FixtureSourceSpan,
    LabelClass,
)
from cortex.hosted.model_interfaces import (
    DeriveRequest,
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
)
from cortex.hosted.model_registry import RegisteredPrompt
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.quality_series import (
    OVERRIDE_CONTEXT_CLASSES,
    PRECISION_SERIES_CLASSES,
    RECALL_CLASSES,
    TONE_SERIES_CLASSES,
    QualitySeriesValidationError,
    quality_series_point,
)
from tests.test_hosted_model_interfaces import _candidate_pack  # shared pack fixture

PROMPT = RegisteredPrompt(
    prompt_id="evaluate-contradiction",
    version_number=1,
    template_text="Judge DIFF against DECISIONS.",
    description="test",
)
DERIVE_PROMPT = RegisteredPrompt(
    prompt_id="derive-repo-native",
    version_number=1,
    template_text="Extract from DOCUMENT.",
    description="test",
)


# --- #328 banking ------------------------------------------------------------


def _key(**overrides: str) -> BankKey:
    base = {
        "task": "evaluate",
        "input_hash": hashlib.sha256(b"inputs").hexdigest(),
        "model_id": "anthropic/claude-fable-5",
        "prompt_version": PROMPT.prompt_version,
    }
    base.update(overrides)
    return BankKey(**base)


def test_exact_match_reuses() -> None:
    decision = decide(_key(), _key())
    assert decision.action == "reuse"
    assert decision.drifted_components == ()


def test_any_drift_re_derives_with_attribution() -> None:
    banked = _key()
    for component, new_value in (
        ("input_hash", hashlib.sha256(b"other").hexdigest()),
        ("model_id", "openai/gpt-5.4"),
        ("prompt_version", RegisteredPrompt(
            prompt_id="evaluate-contradiction", version_number=2,
            template_text="Revised.", description="test").prompt_version),
    ):
        requested = _key(**{component: new_value})
        decision = decide(requested, banked)
        assert decision.action == "re-derive"
        assert decision.drifted_components == (component,)


def test_cold_bank_re_derives_without_attribution() -> None:
    decision = decide(_key(), None)
    assert decision.action == "re-derive"
    assert decision.banked is None


def test_unattributed_re_derive_is_unrepresentable() -> None:
    with pytest.raises(BankingValidationError, match="unattributable"):
        BankDecision(
            action="re-derive", requested=_key(), banked=_key(), drifted_components=()
        )


def test_multi_component_drift_names_all() -> None:
    requested = _key(model_id="openai/gpt-5.4", task="derive",
                     prompt_version=DERIVE_PROMPT.prompt_version)
    drift = drifted_components(requested, _key())
    assert set(drift) == {"task", "model_id", "prompt_version"}


# --- #346 cascade ------------------------------------------------------------


class _ScriptedEvaluate:
    def __init__(self, result: EvaluateResult) -> None:
        self._result = result

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        return self._result


def _evaluate_request() -> EvaluateRequest:
    return EvaluateRequest(
        candidate_pack=_candidate_pack(),
        diff_patch="-a\n+b\n",
        prompt_version=PROMPT.prompt_version,
    )


def _evaluate_result(request: EvaluateRequest, *, model: str, degraded: tuple[str, ...] = ()) -> EvaluateResult:
    return EvaluateResult(
        findings=(),
        model_id=model,
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
        degraded_reasons=degraded,
    )


def test_clean_primary_answers_without_escalation() -> None:
    request = _evaluate_request()
    primary = _ScriptedEvaluate(_evaluate_result(request, model="z/cheap"))
    escalator = _ScriptedEvaluate(_evaluate_result(request, model="z/strong"))
    cascade = CascadeModel(primary=primary, escalator=escalator)
    result = cascade.evaluate(request)
    assert result.model_id == "z/cheap"
    assert cascade.traces[-1].answered_by == "primary"
    assert isinstance(cascade, EvaluateModel)


def test_degraded_primary_escalates_with_reason() -> None:
    request = _evaluate_request()
    primary = _ScriptedEvaluate(
        _evaluate_result(request, model="z/cheap", degraded=("parse retried",))
    )
    escalator = _ScriptedEvaluate(_evaluate_result(request, model="z/strong"))
    cascade = CascadeModel(primary=primary, escalator=escalator)
    result = cascade.evaluate(request)
    assert result.model_id == "z/strong"
    trace = cascade.traces[-1]
    assert trace.answered_by == "escalator"
    assert trace.escalation_reasons == (ESCALATE_DEGRADED_PRIMARY,)


def test_empty_findings_escalation_is_opt_in() -> None:
    request = _evaluate_request()  # pack has one candidate
    primary = _ScriptedEvaluate(_evaluate_result(request, model="z/cheap"))
    escalator = _ScriptedEvaluate(_evaluate_result(request, model="z/strong"))

    default_cascade = CascadeModel(primary=primary, escalator=escalator)
    assert default_cascade.evaluate(request).model_id == "z/cheap"

    opted = CascadeModel(
        primary=primary,
        escalator=escalator,
        policy=CascadePolicy(escalate_on_empty_findings=True),
    )
    result = opted.evaluate(request)
    assert result.model_id == "z/strong"
    assert opted.traces[-1].escalation_reasons == (ESCALATE_EMPTY_ON_NONEMPTY_PACK,)


def test_task_mismatched_leg_is_refused() -> None:
    request = DeriveRequest(
        source_document=SourceDocument(
            tenant_id="0b6f9f3e-3a2f-4f2e-9c8d-1a2b3c4d5e6f",
            source_id="1c7f8e2d-4b3a-4c5d-8e9f-2b3c4d5e6f70",
            document_type="adr", external_id="x", permalink="https://x",
            author_ref="h", source_timestamp=datetime(2026, 6, 9, tzinfo=UTC),
            content="content",
        ),
        prompt_version=DERIVE_PROMPT.prompt_version,
    )
    evaluate_only = _ScriptedEvaluate(_evaluate_result(_evaluate_request(), model="z/cheap"))
    cascade = CascadeModel(primary=evaluate_only, escalator=evaluate_only)
    with pytest.raises(CascadeValidationError, match="does not implement derive"):
        cascade.derive(request)


# --- #342 quality series ------------------------------------------------------


DOC = "We always use exponential backoff with jitter for webhook retries."
DOC_HASH = hashlib.sha256(DOC.encode()).hexdigest()


def _fixture(fixture_id: str, labels: tuple[FixtureLabel, ...]) -> EvalFixture:
    span = FixtureSourceSpan(
        source_document_hash=DOC_HASH, start_offset=0, end_offset=len(DOC),
        excerpt=DOC, permalink="https://example/adr",
    )
    decision = FixtureDecision(
        decision_id="retry-policy", decision_text=DOC,
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-05-14T09:30:00+00:00", spans=(span,),
    )
    finding = ExpectedFinding(
        finding_id="finding-1",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="retry-policy",
        cited_span_hashes=(span.span_hash,),
        summary="Fixed retry contradicts backoff decision.",
    )
    return EvalFixture(
        fixture_id=fixture_id,
        diff=FixtureDiff(
            repo_owner="acme", repo_name="payments",
            base_sha="a1b2c3d", head_sha="b2c3d4e", patch="-a\n+b\n",
        ),
        decisions=(decision,),
        expected_findings=(finding,),
        labels=labels,
    )


def _label(label: LabelClass, grader: str = "henry") -> FixtureLabel:
    return FixtureLabel(
        finding_id="finding-1", label=label, grader=grader, graded_at="2026-06-09"
    )


def test_partitions_are_disjoint_by_construction() -> None:
    assert not TONE_SERIES_CLASSES - PRECISION_SERIES_CLASSES  # tone ⊂ precision-graded
    assert not RECALL_CLASSES & PRECISION_SERIES_CLASSES
    assert not OVERRIDE_CONTEXT_CLASSES & PRECISION_SERIES_CLASSES
    assert not OVERRIDE_CONTEXT_CLASSES & TONE_SERIES_CLASSES
    assert not OVERRIDE_CONTEXT_CLASSES & RECALL_CLASSES
    assert LabelClass.INCORRECT_PRECISION not in TONE_SERIES_CLASSES


def test_tone_label_moves_tone_series_and_leaves_fp_rate_bit_identical() -> None:
    base = (
        _fixture("f1", (_label(LabelClass.CORRECT_USEFUL),)),
        _fixture("f2", (_label(LabelClass.INCORRECT_PRECISION),)),
    )
    before = quality_series_point(base)

    with_tone = (*base, _fixture("f3", (_label(LabelClass.CORRECT_NOT_USEFUL),)))
    after = quality_series_point(with_tone)

    assert after.tone_flagged_count == before.tone_flagged_count + 1
    # FP numerator untouched by the tone-class addition:
    assert after.incorrect_precision_count == before.incorrect_precision_count
    # Note the denominator legitimately grows (one more graded finding); the
    # *numerator* isolation is the structural guarantee:
    assert before.false_positive_rate == 0.5
    assert after.false_positive_rate == pytest.approx(1 / 3)


def test_missed_expected_is_recall_material_not_a_rate_input() -> None:
    point = quality_series_point(
        (_fixture("f1", (_label(LabelClass.MISSED_EXPECTED),)),)
    )
    assert point.missed_expected_count == 1
    assert point.false_positive_rate is None
    assert "no graded emitted findings" in (point.false_positive_rate_unavailable_reason or "")


def test_override_context_is_visible_but_not_a_rate_input() -> None:
    point = quality_series_point(
        (
            _fixture("f1", (_label(LabelClass.CORRECT_USEFUL),)),
            _fixture("f2", (_label(LabelClass.INCORRECT_PRECISION),)),
            _fixture("f3", (_label(LabelClass.OVERRIDE_CHANGED_DECISION),)),
            _fixture("f4", (_label(LabelClass.OVERRIDE_EMERGENCY_EXCEPTION),)),
        )
    )

    assert point.override_context_count == 2
    assert point.graded_emitted_count == 2
    assert point.false_positive_rate == pytest.approx(0.5)
    assert point.tone_rate == pytest.approx(0.0)


def test_zero_denominator_is_reasoned_not_zero() -> None:
    point = quality_series_point(())
    assert point.false_positive_rate is None
    assert point.tone_rate is None
    assert point.false_positive_rate_unavailable_reason
    # The invariant: a rate and its unavailability reason are mutually
    # exclusive and one is mandatory — both-set and neither-set are
    # unrepresentable, so a missing rate can never be smuggled as 0.0
    # without dropping its reason (which raises).
    with pytest.raises(QualitySeriesValidationError, match=r"never a silent 0\.0"):
        type(point)(
            graded_emitted_count=0, incorrect_precision_count=0,
            tone_flagged_count=0, missed_expected_count=0, override_context_count=0,
            false_positive_rate=0.0, false_positive_rate_unavailable_reason="both set",
            tone_rate=None, tone_rate_unavailable_reason="x",
        )
    with pytest.raises(QualitySeriesValidationError, match=r"never a silent 0\.0"):
        type(point)(
            graded_emitted_count=0, incorrect_precision_count=0,
            tone_flagged_count=0, missed_expected_count=0, override_context_count=0,
            false_positive_rate=None, false_positive_rate_unavailable_reason=None,
            tone_rate=None, tone_rate_unavailable_reason="x",
        )


def test_duplicate_fixture_ids_rejected() -> None:
    fixture = _fixture("dup", (_label(LabelClass.CORRECT_USEFUL),))
    with pytest.raises(QualitySeriesValidationError, match="duplicate"):
        quality_series_point((fixture, fixture))
