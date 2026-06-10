"""Tests for the soft evaluator core (cortex#370, #371, #372, #377)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from cortex.hosted.advisory_ladder import (
    DEFAULT_ADVISORY_LADDER,
    AdvisoryLadder,
    EmissionBehavior,
)
from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.confidence import ConfidenceTier
from cortex.hosted.context_assembly import (
    ESTIMATOR_VERSION,
    default_token_estimator,
    serialize_candidate_payload,
)
from cortex.hosted.cost import (
    BudgetExceededError,
    ModelPriceTable,
    RunBudget,
    RunLedger,
)
from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)
from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.eval_fixtures import DecisionStatus, FindingClass
from cortex.hosted.evaluator import (
    GRAPH_SUPERSEDES_REASON_CODE,
    REASON_CITED_SPAN_NOT_IN_PACK,
    REASON_CONTRADICTED_DECISION_NOT_CONFIRMED,
    REASON_DECISION_REF_NOT_IN_PACK,
    REASON_FINDING_CLASS_NOT_REGISTERED,
    REASON_MISSING_PATH_DECISION_NOT_CONFIRMED,
    REASON_MISSING_PATH_NOT_NAMED,
    REASON_OMISSION_ANCHOR_NOT_CONFIRMED,
    REASON_OMISSION_STAGE_NOT_NAMED,
    REASON_OMITTED_DECISION_NOT_NAMED,
    REASON_REVERSED_DECISION_NOT_SUPERSEDED,
    REASON_SUPERSEDING_DECISION_MISSING,
    REASON_UNKNOWN_CONFIDENCE_LABEL,
    STAGE0_FINDING_CLASS_REGISTRY,
    ClassEvidenceRule,
    EvaluationOutcome,
    EvaluationState,
    EvaluatorValidationError,
    FindingClassSpec,
    ShadowFinding,
    UncitedFindingError,
    evaluate_diff,
    evaluate_prompt_guidance,
)
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType
from cortex.hosted.model_interfaces import (
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
    ModelInterfaceValidationError,
)

TENANT = "0b6f9f3e-3a2f-4f2e-9c8d-1a2b3c4d5e6f"
SOURCE = "1c7f8e2d-4b3a-4c5d-8e9f-2b3c4d5e6f70"
MODEL_ID = "stub/eval-model"
PROMPT_VERSION = "evaluate-stage0/v1+abcdefabcdef"
QUERY_HASH = hashlib.sha256(b"query").hexdigest()
GRAPH_HASH = hashlib.sha256(b"graph").hexdigest()
DIFF = "-    delay = backoff_with_jitter(attempt)\n+    delay = 5.0\n"
OCCURRED_AT = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
ACTOR = ActorRef(actor_type="service", actor_id="evaluator-test")


def _span_hash(index: int) -> str:
    return hashlib.sha256(f"span-{index}".encode()).hexdigest()


def _candidate(
    index: int,
    *,
    status: str = "confirmed",
    reason_codes: tuple[str, ...] = ("scope:path:src/app.py",),
) -> DecisionsForDiffCandidate:
    return DecisionsForDiffCandidate(
        decision_node_id=str(UUID(int=index * 2 + 1)),
        decision_version_id=str(UUID(int=index * 2 + 2)),
        status=status,
        decision_text=f"decision body {index}: retries use exponential backoff",
        score=float(10 - index),
        reason_codes=reason_codes,
        cited_spans=(
            CitedSourceSpan(
                span_hash=_span_hash(index),
                excerpt=f"excerpt {index}",
                permalink=f"https://github.com/acme/app/blob/main/docs/adr/{index:04d}.md",
                source_document_id=str(UUID(int=9000 + index)),
                source_id=str(UUID(int=7000 + index)),
            ),
        ),
    )


def _pack(
    candidates: Sequence[DecisionsForDiffCandidate],
    *,
    omitted_counts: dict[str, int] | None = None,
    pool_extra: int = 0,
) -> DecisionsForDiffCandidatePack:
    return DecisionsForDiffCandidatePack(
        query_hash=QUERY_HASH,
        retrieval_config_version="decisions-for-diff-v2+test",
        graph_snapshot_hash=GRAPH_HASH,
        candidates=tuple(candidates),
        omitted_counts=omitted_counts if omitted_counts is not None else {"over_limit": 0},
        graph_node_count=12,
        candidate_pool_size=len(candidates) + pool_extra,
    )


def _finding(
    candidate: DecisionsForDiffCandidate,
    *,
    finding_class: FindingClass = FindingClass.CONTRADICTS_PRIOR_DECISION,
    label: str = "advisory",
    span_hashes: tuple[str, ...] | None = None,
    decision_node_id: str | None = None,
    summary: str = "The diff conflicts with a recorded decision.",
) -> FindingDraft:
    return FindingDraft(
        finding_class=finding_class,
        decision_node_id=(
            candidate.decision_node_id if decision_node_id is None else decision_node_id
        ),
        cited_span_hashes=(
            tuple(span.span_hash for span in candidate.cited_spans)
            if span_hashes is None
            else span_hashes
        ),
        summary=summary,
        confidence_label=label,
    )


# A cites-missing-path summary that satisfies the named-path evidence gate.
MISSING_PATH_SUMMARY = (
    "The diff imports src/payments/missing_helper.py, which does not exist "
    "in the changed surface the pack carries."
)


@dataclass
class ScriptedModel:
    """A scripted fake satisfying the EvaluateModel protocol."""

    build_findings: Callable[[EvaluateRequest], tuple[FindingDraft, ...]]
    omitted_decision_count: int = 0
    degraded_reasons: tuple[str, ...] = ()
    model_id: str = MODEL_ID
    requests: list[EvaluateRequest] = field(default_factory=list)

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        self.requests.append(request)
        return EvaluateResult(
            findings=self.build_findings(request),
            model_id=self.model_id,
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
            omitted_decision_count=self.omitted_decision_count,
            degraded_reasons=self.degraded_reasons,
        )


def _scripted(*findings: FindingDraft, **kwargs: object) -> ScriptedModel:
    return ScriptedModel(build_findings=lambda _request: tuple(findings), **kwargs)  # type: ignore[arg-type]


def _ledger(budget: RunBudget | None = None) -> RunLedger:
    return RunLedger(
        run_id="run-eval-1",
        price_table=ModelPriceTable(version="2026-06-10", prices=()),
        budget=budget,
    )


def _evaluate(
    pack: DecisionsForDiffCandidatePack,
    model: EvaluateModel,
    **overrides: Any,
) -> EvaluationOutcome:
    params: dict[str, Any] = {
        "token_budget": 100_000,
        "ladder": DEFAULT_ADVISORY_LADDER,
        "run_ledger": _ledger(),
        "prompt_version": PROMPT_VERSION,
        "tenant_id": TENANT,
        "source_id": SOURCE,
        "actor": ACTOR,
        "occurred_at": OCCURRED_AT,
    }
    params.update(overrides)
    return evaluate_diff(pack, DIFF, model, **params)


# --- full evaluate_diff flow (#370) -----------------------------------------


def test_emits_contradiction_against_confirmed_decision() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(pack, _scripted(_finding(candidate)))
    assert outcome.state is EvaluationState.FINDINGS_EMITTED
    assert len(outcome.emitted) == 1
    emitted = outcome.emitted[0]
    assert emitted.decision_node_id == candidate.decision_node_id
    assert emitted.decision_version_id == candidate.decision_version_id
    assert emitted.tier is ConfidenceTier.ADVISORY
    assert emitted.behavior is EmissionBehavior.ADVISORY_COMMENT
    assert outcome.rejected == ()
    assert outcome.suppressed == ()


def test_replay_key_material_is_complete() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    model = _scripted(_finding(candidate))
    outcome = _evaluate(pack, model)
    request = model.requests[0]
    replay = outcome.replay
    assert replay.graph_snapshot_hash == pack.graph_snapshot_hash
    assert replay.retrieval_config_version == pack.retrieval_config_version
    assert replay.query_hash == pack.query_hash
    assert replay.candidate_set_hash == request.candidate_pack.candidate_set_hash
    assert replay.input_hash == request.input_hash
    assert replay.model_id == MODEL_ID
    assert replay.prompt_version == PROMPT_VERSION
    assert replay.run_id == "run-eval-1"
    assert replay.estimator_version == ESTIMATOR_VERSION
    assert replay.token_budget == 100_000
    assert len(replay.context_hash) == 64


def test_ledger_draft_validates_envelope_with_full_replay_material() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(pack, _scripted(_finding(candidate)))
    assert len(outcome.ledger_event_drafts) == 1
    draft = outcome.ledger_event_drafts[0]
    # The envelope itself enforces spans + snapshot + model stamping; an
    # instance existing means those invariants held at construction.
    assert isinstance(draft, LedgerEvent)
    assert draft.event_type is LedgerEventType.FINDING_EMITTED
    assert draft.source_span_hashes == outcome.emitted[0].finding.cited_span_hashes
    assert draft.graph_snapshot_hash == pack.graph_snapshot_hash
    assert draft.model_id == MODEL_ID
    assert draft.prompt_version == PROMPT_VERSION
    assert draft.payload["replay"] == outcome.replay.as_payload()
    assert draft.payload["blocking_enabled"] is False
    assert draft.payload["decision_version_id"] == candidate.decision_version_id
    assert draft.as_insert_parameters()["event_hash"] == draft.event_hash


def test_ledger_draft_idempotency_is_stable_across_replay() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    first = _evaluate(pack, _scripted(_finding(candidate)))
    second = _evaluate(
        pack,
        _scripted(_finding(candidate)),
        occurred_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
    )
    assert (
        first.ledger_event_drafts[0].idempotency_key
        == second.ledger_event_drafts[0].idempotency_key
    )


def test_draft_external_id_uses_the_model_result_ordinal() -> None:
    """Ordinals index the model result, so sibling rejections cannot shift them."""

    good = _candidate(0)
    pack = _pack([good])
    uncited = _finding(good, decision_node_id=str(UUID(int=999)))
    outcome = _evaluate(pack, _scripted(uncited, _finding(good)))
    assert len(outcome.ledger_event_drafts) == 1
    draft = outcome.ledger_event_drafts[0]
    assert draft.source_event_external_id == (
        f"evaluate:{outcome.replay.input_hash}:finding:1"
    )


def test_metadata_travels_into_the_request() -> None:
    candidate = _candidate(0)
    model = _scripted(_finding(candidate))
    _evaluate(_pack([candidate]), model, metadata={"pr_number": 12})
    assert model.requests[0].metadata == {"pr_number": 12}


def test_unbound_result_is_refused() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])

    class UnboundModel:
        def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
            return EvaluateResult(
                findings=(),
                model_id=MODEL_ID,
                prompt_version=request.prompt_version,
                input_hash="b" * 64,
            )

    with pytest.raises(ModelInterfaceValidationError, match="input_hash"):
        _evaluate(pack, UnboundModel())


def test_budget_breach_is_refused_before_the_model_call() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    model = _scripted(_finding(candidate))
    with pytest.raises(BudgetExceededError):
        _evaluate(pack, model, run_ledger=_ledger(RunBudget(max_calls=0)))
    assert model.requests == []


def test_budget_exceeded_classifies_as_fail_closed_refusal() -> None:
    assert (
        classify_failure(BudgetExceededError("budget probe"))
        is DegradationMode.FAIL_CLOSED_REFUSAL
    )


# --- citation fail-closed (#377) --------------------------------------------


def test_rejects_decision_ref_not_in_pack() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    foreign = _finding(candidate, decision_node_id=str(UUID(int=999)))
    outcome = _evaluate(pack, _scripted(foreign))
    assert outcome.emitted == ()
    assert len(outcome.rejected) == 1
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_DECISION_REF_NOT_IN_PACK
    assert rejection.degradation.mode is DegradationMode.FAIL_CLOSED_REFUSAL
    assert outcome.ledger_event_drafts == ()


def test_rejects_cited_span_not_in_pack() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    fabricated = _finding(candidate, span_hashes=(hashlib.sha256(b"forged").hexdigest(),))
    outcome = _evaluate(pack, _scripted(fabricated))
    assert outcome.emitted == ()
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_CITED_SPAN_NOT_IN_PACK
    assert rejection.degradation.mode is DegradationMode.FAIL_CLOSED_REFUSAL
    assert outcome.ledger_event_drafts == ()


def test_uncited_finding_never_reaches_the_ledger_path() -> None:
    """#377: provenance-less findings stop before FINDING_EMITTED composition."""

    good = _candidate(0)
    pack = _pack([good])
    forged_hash = hashlib.sha256(b"forged").hexdigest()
    uncited = _finding(good, span_hashes=(forged_hash,))
    outcome = _evaluate(pack, _scripted(uncited, _finding(good)))
    assert len(outcome.ledger_event_drafts) == 1
    for draft in outcome.ledger_event_drafts:
        assert forged_hash not in draft.source_span_hashes


def test_every_candidate_uncited_yields_explicit_no_findings() -> None:
    """#377: the all-dropped run returns a fail-closed no-findings result."""

    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(
        pack,
        _scripted(
            _finding(candidate, decision_node_id=str(UUID(int=998))),
            _finding(candidate, span_hashes=(hashlib.sha256(b"forged").hexdigest(),)),
        ),
    )
    assert outcome.state is EvaluationState.NO_FINDINGS
    assert outcome.emitted == ()
    assert outcome.ledger_event_drafts == ()
    assert outcome.rejection_counts == {
        REASON_CITED_SPAN_NOT_IN_PACK: 1,
        REASON_DECISION_REF_NOT_IN_PACK: 1,
    }


def test_rejection_degradation_report_shape() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(
        pack, _scripted(_finding(candidate, decision_node_id=str(UUID(int=999))))
    )
    report = outcome.rejected[0].degradation
    assert report.source == "cortex.hosted.evaluator"
    assert report.reason_code == REASON_DECISION_REF_NOT_IN_PACK
    assert report.safety_boundary_held is True
    assert report.as_payload()["mode"] == "fail_closed_refusal"


def test_uncited_finding_error_classifies_as_fail_closed_refusal() -> None:
    assert (
        classify_failure(UncitedFindingError("boundary probe"))
        is DegradationMode.FAIL_CLOSED_REFUSAL
    )
    assert (
        classify_failure(EvaluatorValidationError("boundary probe"))
        is DegradationMode.INVALID_INPUT_REJECTED
    )


# --- finding-class evidence (#371 + #372) ------------------------------------


def test_rejects_unregistered_finding_class() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    narrow_registry = {
        FindingClass.CONTRADICTS_PRIOR_DECISION: STAGE0_FINDING_CLASS_REGISTRY[
            FindingClass.CONTRADICTS_PRIOR_DECISION
        ]
    }
    unregistered = _finding(
        candidate, finding_class=FindingClass.REVERSES_SUPERSEDED_PATTERN
    )
    outcome = _evaluate(pack, _scripted(unregistered), registry=narrow_registry)
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_FINDING_CLASS_NOT_REGISTERED
    assert rejection.degradation.mode is DegradationMode.INVALID_INPUT_REJECTED


def test_rejects_contradiction_against_non_confirmed_decision() -> None:
    candidate = _candidate(0, status="candidate")
    pack = _pack([candidate])
    outcome = _evaluate(pack, _scripted(_finding(candidate)))
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_CONTRADICTED_DECISION_NOT_CONFIRMED
    assert "'confirmed'" in rejection.detail
    assert outcome.emitted == ()


def test_rejects_reverses_without_superseding_companion() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    reverses = _finding(candidate, finding_class=FindingClass.REVERSES_SUPERSEDED_PATTERN)
    outcome = _evaluate(pack, _scripted(reverses))
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_SUPERSEDING_DECISION_MISSING
    assert GRAPH_SUPERSEDES_REASON_CODE in rejection.detail


def test_rejects_reverses_against_non_superseded_decision() -> None:
    cited = _candidate(0)
    superseding = _candidate(1, reason_codes=(GRAPH_SUPERSEDES_REASON_CODE,))
    pack = _pack([cited, superseding])
    reverses = _finding(cited, finding_class=FindingClass.REVERSES_SUPERSEDED_PATTERN)
    outcome = _evaluate(pack, _scripted(reverses))
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_REVERSED_DECISION_NOT_SUPERSEDED
    assert "'superseded'" in rejection.detail


def test_stage0_registry_shape() -> None:
    assert set(STAGE0_FINDING_CLASS_REGISTRY) == {
        FindingClass.CONTRADICTS_PRIOR_DECISION,
        FindingClass.REVERSES_SUPERSEDED_PATTERN,
        FindingClass.CITES_MISSING_PATH,
        FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
    }
    contradicts = STAGE0_FINDING_CLASS_REGISTRY[FindingClass.CONTRADICTS_PRIOR_DECISION]
    assert contradicts.required_cited_status == DecisionStatus.CONFIRMED.value
    assert contradicts.companion is None
    assert contradicts.shadow is False
    assert contradicts.class_rule is None
    reverses = STAGE0_FINDING_CLASS_REGISTRY[FindingClass.REVERSES_SUPERSEDED_PATTERN]
    assert reverses.required_cited_status == DecisionStatus.SUPERSEDED.value
    assert reverses.companion is not None
    assert reverses.companion.reason_code_marker == GRAPH_SUPERSEDES_REASON_CODE
    assert reverses.shadow is False
    assert reverses.class_rule is None
    missing_path = STAGE0_FINDING_CLASS_REGISTRY[FindingClass.CITES_MISSING_PATH]
    assert missing_path.shadow is True
    assert missing_path.class_rule is ClassEvidenceRule.NAMED_MISSING_PATH
    assert missing_path.required_cited_status == DecisionStatus.CONFIRMED.value
    omitted = STAGE0_FINDING_CLASS_REGISTRY[FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT]
    assert omitted.shadow is True
    assert omitted.class_rule is ClassEvidenceRule.NAMED_BUDGET_OMISSION
    assert omitted.required_cited_status == DecisionStatus.CONFIRMED.value


def test_shadow_membership_is_a_registry_attribute_not_a_special_case() -> None:
    """The shadow lanes are ordinary registry entries with shadow=True."""

    shadow_classes = {
        finding_class
        for finding_class, spec in STAGE0_FINDING_CLASS_REGISTRY.items()
        if spec.shadow
    }
    assert shadow_classes == {
        FindingClass.CITES_MISSING_PATH,
        FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
    }


def test_spec_requires_a_known_decision_status() -> None:
    with pytest.raises(EvaluatorValidationError, match="required_cited_status"):
        FindingClassSpec(
            finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
            required_cited_status="bogus",
            status_rejection_code="x",
            prompt_guidance="y",
        )


def test_registry_key_spec_mismatch_is_rejected() -> None:
    candidate = _candidate(0)
    mismatched = {
        FindingClass.CONTRADICTS_PRIOR_DECISION: STAGE0_FINDING_CLASS_REGISTRY[
            FindingClass.REVERSES_SUPERSEDED_PATTERN
        ]
    }
    with pytest.raises(EvaluatorValidationError, match="keys must match"):
        _evaluate(_pack([candidate]), _scripted(), registry=mismatched)


def test_empty_registry_is_rejected() -> None:
    candidate = _candidate(0)
    with pytest.raises(EvaluatorValidationError, match="at least one class"):
        _evaluate(_pack([candidate]), _scripted(), registry={})


def test_prompt_guidance_asks_for_exactly_the_stage0_classes() -> None:
    text = evaluate_prompt_guidance()
    assert FindingClass.CONTRADICTS_PRIOR_DECISION.value in text
    # The #373/#374 shadow classes are asked for (so precision is
    # measurable) but the evaluator captures them instead of emitting.
    assert FindingClass.CITES_MISSING_PATH.value in text
    assert FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT.value in text
    assert "suggest, advisory, confirmed_cited" in text
    assert evaluate_prompt_guidance() == text
    # The two pre-shadow definitions are unchanged: same guidance lines as
    # before the #373/#374 vocabulary extension.
    assert (
        "- contradicts-prior-decision: emit only when the diff conflicts "
        "with a decision in the pack whose status is 'confirmed'; cite span "
        "hashes from that decision's citations"
    ) in text
    assert (
        "- reverses-superseded-pattern: emit only when the diff reintroduces "
        "a pattern from a pack decision whose status is 'superseded' and the "
        "superseding decision is also present in the pack; cite span hashes "
        "from the superseded decision's citations"
    ) in text


# --- shadow lanes (#373 + #374) ------------------------------------------------


def test_cites_missing_path_is_captured_in_shadow_not_emitted() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    proposed = _finding(
        candidate,
        finding_class=FindingClass.CITES_MISSING_PATH,
        summary=MISSING_PATH_SUMMARY,
    )
    outcome = _evaluate(pack, _scripted(proposed))
    assert outcome.emitted == ()
    assert outcome.rejected == ()
    assert outcome.suppressed == ()
    assert outcome.ledger_event_drafts == ()
    assert outcome.shadow_finding_count == 1
    captured = outcome.shadow_findings[0]
    assert isinstance(captured, ShadowFinding)
    assert captured.finding.finding_class is FindingClass.CITES_MISSING_PATH
    assert captured.decision_node_id == candidate.decision_node_id
    assert captured.decision_version_id == candidate.decision_version_id
    assert captured.tier is ConfidenceTier.ADVISORY


def test_shadow_only_run_is_an_explicit_no_findings_state() -> None:
    """Shadow captures never flip the outcome state to findings_emitted."""

    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary=MISSING_PATH_SUMMARY,
            )
        ),
    )
    assert outcome.state is EvaluationState.NO_FINDINGS
    assert outcome.shadow_finding_count == 1


def test_shadow_capture_skips_the_emission_floor_by_construction() -> None:
    """The ladder never assesses shadow findings: a raised floor cannot
    suppress them, because they are captured before any emission decision."""

    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary=MISSING_PATH_SUMMARY,
                label="suggest",
            )
        ),
        ladder=AdvisoryLadder(emission_floor=ConfidenceTier.ADVISORY),
    )
    assert outcome.suppressed == ()
    assert outcome.shadow_finding_count == 1
    assert outcome.shadow_findings[0].tier is ConfidenceTier.SUGGEST


def test_shadow_findings_never_reach_ledger_drafts_alongside_emissions() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    shadow = _finding(
        candidate,
        finding_class=FindingClass.CITES_MISSING_PATH,
        summary=MISSING_PATH_SUMMARY,
    )
    outcome = _evaluate(pack, _scripted(shadow, _finding(candidate)))
    assert len(outcome.emitted) == 1
    assert outcome.shadow_finding_count == 1
    assert len(outcome.ledger_event_drafts) == 1
    draft = outcome.ledger_event_drafts[0]
    assert (
        draft.payload["finding"]["finding_class"]
        == FindingClass.CONTRADICTS_PRIOR_DECISION.value
    )


def test_shadow_counts_are_visible_per_class() -> None:
    first = _candidate(0)
    second = _candidate(1)
    pack = _pack([first, second])
    first_cost = default_token_estimator.estimate_tokens(
        serialize_candidate_payload(first)
    )
    omission_summary = (
        f"Context assembly omitted decision {second.decision_node_id} at stage "
        "over_budget; its scope matched the diff."
    )
    outcome = _evaluate(
        pack,
        _scripted(
            _finding(
                first,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary=MISSING_PATH_SUMMARY,
            ),
            _finding(
                first,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary="The diff relies on docs/adr/0099-missing.md, which is absent.",
            ),
            _finding(
                first,
                finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
                summary=omission_summary,
            ),
        ),
        token_budget=first_cost,
    )
    assert outcome.shadow_finding_count == 3
    assert outcome.shadow_class_counts == {
        FindingClass.CITES_MISSING_PATH.value: 2,
        FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT.value: 1,
    }
    payload = outcome.as_payload()
    assert payload["shadow_class_counts"] == outcome.shadow_class_counts
    assert payload["shadow_finding_count"] == 3


def test_shadow_finding_payload_shape() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary=MISSING_PATH_SUMMARY,
            )
        ),
    )
    payload = outcome.shadow_findings[0].as_payload()
    assert set(payload) == {"decision_version_id", "finding", "shadow", "tier"}
    assert payload["shadow"] is True
    assert payload["tier"] == "advisory"
    assert payload["finding"]["finding_class"] == FindingClass.CITES_MISSING_PATH.value


def test_shadow_finding_requires_uuid_decision_version() -> None:
    candidate = _candidate(0)
    with pytest.raises(EvaluatorValidationError, match="decision_version_id"):
        ShadowFinding(
            finding=_finding(candidate, finding_class=FindingClass.CITES_MISSING_PATH),
            decision_version_id="not-a-uuid",
            tier=ConfidenceTier.ADVISORY,
        )


def test_shadow_class_citation_gate_still_applies() -> None:
    """#377 runs before shadow capture: forged provenance never shadows."""

    candidate = _candidate(0)
    pack = _pack([candidate])
    forged = _finding(
        candidate,
        finding_class=FindingClass.CITES_MISSING_PATH,
        summary=MISSING_PATH_SUMMARY,
        span_hashes=(hashlib.sha256(b"forged").hexdigest(),),
    )
    outcome = _evaluate(pack, _scripted(forged))
    assert outcome.shadow_findings == ()
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_CITED_SPAN_NOT_IN_PACK
    assert rejection.degradation.mode is DegradationMode.FAIL_CLOSED_REFUSAL


def test_shadow_capture_validates_the_confidence_label() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary=MISSING_PATH_SUMMARY,
                label="vibes",
            )
        ),
    )
    assert outcome.shadow_findings == ()
    assert outcome.rejected[0].reason_code == REASON_UNKNOWN_CONFIDENCE_LABEL


def test_cites_missing_path_requires_a_named_path() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary="The diff relies on something that is gone.",
            )
        ),
    )
    assert outcome.shadow_findings == ()
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_MISSING_PATH_NOT_NAMED
    assert "name the missing path" in rejection.detail


def test_bare_filename_with_extension_satisfies_the_named_path_gate() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary="The helper module missing_helper.py is absent, e.g. on import.",
            )
        ),
    )
    assert outcome.shadow_finding_count == 1


def test_prose_abbreviations_do_not_satisfy_the_named_path_gate() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary="The diff relies on a module that is gone, e.g. an import.",
            )
        ),
    )
    assert outcome.shadow_findings == ()
    assert outcome.rejected[0].reason_code == REASON_MISSING_PATH_NOT_NAMED


def test_cites_missing_path_requires_confirmed_cited_decision() -> None:
    """The status gate fires even when the path is named (deterministic order)."""

    candidate = _candidate(0, status="candidate")
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.CITES_MISSING_PATH,
                summary=MISSING_PATH_SUMMARY,
            )
        ),
    )
    assert outcome.shadow_findings == ()
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_MISSING_PATH_DECISION_NOT_CONFIRMED
    assert "'confirmed'" in rejection.detail


def test_omitted_constraint_captured_when_id_and_stage_named() -> None:
    first = _candidate(0)
    second = _candidate(1)
    pack = _pack([first, second])
    first_cost = default_token_estimator.estimate_tokens(
        serialize_candidate_payload(first)
    )
    proposed = _finding(
        first,
        finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
        summary=(
            f"Context assembly omitted decision {second.decision_node_id} at "
            "stage over_budget although its scope matched the diff."
        ),
    )
    outcome = _evaluate(pack, _scripted(proposed), token_budget=first_cost)
    assert outcome.omitted_for_budget == 1
    assert outcome.rejected == ()
    assert outcome.shadow_finding_count == 1
    assert (
        outcome.shadow_findings[0].finding.finding_class
        is FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT
    )


def test_omitted_constraint_requires_an_actually_omitted_decision_id() -> None:
    """Nothing was omitted for budget, so no omission claim can verify."""

    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
                summary=(
                    f"Context assembly omitted decision {UUID(int=777)} at "
                    "stage over_budget."
                ),
            )
        ),
    )
    assert outcome.shadow_findings == ()
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_OMITTED_DECISION_NOT_NAMED
    assert "actually omitted" in rejection.detail


def test_omitted_constraint_requires_the_stage_to_be_named() -> None:
    first = _candidate(0)
    second = _candidate(1)
    pack = _pack([first, second])
    first_cost = default_token_estimator.estimate_tokens(
        serialize_candidate_payload(first)
    )
    proposed = _finding(
        first,
        finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
        summary=(
            f"Context assembly omitted decision {second.decision_node_id} "
            "although its scope matched the diff."
        ),
    )
    outcome = _evaluate(pack, _scripted(proposed), token_budget=first_cost)
    assert outcome.shadow_findings == ()
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_OMISSION_STAGE_NOT_NAMED
    assert "over_budget" in rejection.detail


def test_omitted_constraint_retrieval_stage_claims_are_unverifiable() -> None:
    """Retrieval omissions arrive as counts without ids: claims fail closed."""

    candidate = _candidate(0)
    pack = _pack([candidate], omitted_counts={"over_limit": 3}, pool_extra=3)
    outcome = _evaluate(
        pack,
        _scripted(
            _finding(
                candidate,
                finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
                summary=(
                    f"Retrieval omitted decision {UUID(int=778)} at stage "
                    "over_limit; its scope matched the diff."
                ),
            )
        ),
    )
    assert outcome.shadow_findings == ()
    assert outcome.rejected[0].reason_code == REASON_OMITTED_DECISION_NOT_NAMED


def test_omitted_constraint_anchor_must_be_confirmed() -> None:
    anchor = _candidate(0, status="candidate")
    outcome = _evaluate(
        _pack([anchor]),
        _scripted(
            _finding(
                anchor,
                finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
                summary="Context assembly omitted a load-bearing decision.",
            )
        ),
    )
    assert outcome.shadow_findings == ()
    assert outcome.rejected[0].reason_code == REASON_OMISSION_ANCHOR_NOT_CONFIRMED


# --- ladder emission and suppression (#375) ----------------------------------


def test_unknown_confidence_label_is_rejected_with_reason() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(pack, _scripted(_finding(candidate, label="vibes")))
    rejection = outcome.rejected[0]
    assert rejection.reason_code == REASON_UNKNOWN_CONFIDENCE_LABEL
    assert rejection.degradation.mode is DegradationMode.INVALID_INPUT_REJECTED


def test_suppressed_below_floor_is_counted_not_emitted() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(
        pack,
        _scripted(_finding(candidate, label="suggest")),
        ladder=AdvisoryLadder(emission_floor=ConfidenceTier.ADVISORY),
    )
    assert outcome.emitted == ()
    assert outcome.rejected == ()
    assert outcome.suppressed_below_floor == 1
    assert outcome.suppressed[0].tier is ConfidenceTier.SUGGEST
    assert "below the emission floor" in outcome.suppressed[0].reason
    assert outcome.ledger_event_drafts == ()
    assert outcome.state is EvaluationState.NO_FINDINGS


def test_suggest_tier_emits_as_suggestion_on_the_default_ladder() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(pack, _scripted(_finding(candidate, label="suggest")))
    assert outcome.emitted[0].behavior is EmissionBehavior.SUGGESTION


def test_confirmed_cited_emits_blocking_eligible_but_never_blocking() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(pack, _scripted(_finding(candidate, label="confirmed_cited")))
    emitted = outcome.emitted[0]
    assert emitted.behavior is EmissionBehavior.BLOCKING_ELIGIBLE_COMMENT
    draft = outcome.ledger_event_drafts[0]
    assert draft.payload["blocking_enabled"] is False
    assert draft.payload["behavior"] == "blocking_eligible_comment"


# --- visibility arithmetic (#370 + #377) -------------------------------------


def test_visibility_arithmetic_across_emit_reject_suppress_shadow() -> None:
    first = _candidate(0)
    second = _candidate(1)
    pack = _pack([first, second])
    findings = (
        _finding(first),  # emitted (advisory)
        _finding(first, decision_node_id=str(UUID(int=999))),  # rejected: uncited
        # rejected: shadow class whose summary names no path
        _finding(second, finding_class=FindingClass.CITES_MISSING_PATH),
        _finding(second, label="suggest"),  # suppressed below raised floor
        # shadow-captured: named missing path against a confirmed decision
        _finding(
            second,
            finding_class=FindingClass.CITES_MISSING_PATH,
            summary=MISSING_PATH_SUMMARY,
        ),
    )
    outcome = _evaluate(
        pack,
        _scripted(*findings),
        ladder=AdvisoryLadder(emission_floor=ConfidenceTier.ADVISORY),
    )
    assert len(outcome.emitted) == 1
    assert len(outcome.rejected) == 2
    assert outcome.suppressed_below_floor == 1
    assert outcome.shadow_finding_count == 1
    assert outcome.candidate_finding_count == len(findings)
    assert outcome.rejection_counts == {
        REASON_DECISION_REF_NOT_IN_PACK: 1,
        REASON_MISSING_PATH_NOT_NAMED: 1,
    }
    assert len(outcome.ledger_event_drafts) == len(outcome.emitted)
    payload = outcome.as_payload()
    assert payload["candidate_finding_count"] == 5
    assert payload["suppressed_below_floor"] == 1
    assert payload["shadow_finding_count"] == 1
    assert payload["state"] == "findings_emitted"


def test_omitted_counts_carry_from_pack_through_context() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate], omitted_counts={"over_limit": 4}, pool_extra=4)
    outcome = _evaluate(pack, _scripted(_finding(candidate)))
    assert dict(outcome.total_omitted) == {"over_limit": 4, "over_budget": 0}
    assert outcome.omitted_for_budget == 0


def test_budget_bounds_what_the_model_sees() -> None:
    first = _candidate(0)
    second = _candidate(1)
    pack = _pack([first, second])
    first_cost = default_token_estimator.estimate_tokens(
        serialize_candidate_payload(first)
    )
    model = _scripted()
    outcome = _evaluate(pack, model, token_budget=first_cost)
    bounded = model.requests[0].candidate_pack
    assert [c.decision_node_id for c in bounded.candidates] == [first.decision_node_id]
    assert bounded.omitted_counts["over_budget"] == 1
    assert outcome.omitted_for_budget == 1
    assert outcome.total_omitted["over_budget"] == 1


def test_model_omitted_decision_count_is_carried() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(
        _pack([candidate]),
        _scripted(_finding(candidate), omitted_decision_count=3),
    )
    assert outcome.model_omitted_decision_count == 3


def test_degraded_reasons_merge_context_then_result() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    outcome = _evaluate(
        pack,
        _scripted(degraded_reasons=("model reported degradation",)),
        token_budget=1,
    )
    assert len(outcome.degraded_reasons) == 2
    assert "token_budget 1" in outcome.degraded_reasons[0]
    assert outcome.degraded_reasons[1] == "model reported degradation"
    assert outcome.state is EvaluationState.NO_FINDINGS


# --- outcome invariants ------------------------------------------------------


def test_outcome_requires_one_draft_per_emitted_finding() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    with pytest.raises(EvaluatorValidationError, match=r"one finding\.emitted ledger draft"):
        replace(outcome, ledger_event_drafts=())


def test_outcome_rejects_orphan_drafts() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    with pytest.raises(EvaluatorValidationError, match=r"one finding\.emitted ledger draft"):
        replace(outcome, emitted=())


def test_outcome_payload_shape() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    payload = outcome.as_payload()
    assert set(payload) == {
        "candidate_finding_count",
        "degraded_reasons",
        "emitted",
        "ledger_event_draft_count",
        "model_omitted_decision_count",
        "omitted_for_budget",
        "rejected",
        "rejection_counts",
        "replay",
        "shadow_class_counts",
        "shadow_finding_count",
        "shadow_findings",
        "state",
        "suppressed",
        "suppressed_below_floor",
        "total_omitted",
    }
    assert payload["replay"] == outcome.replay.as_payload()
    assert payload["emitted"][0]["blocking_enabled"] is False
    assert payload["shadow_findings"] == []
    assert payload["shadow_finding_count"] == 0
    assert payload["shadow_class_counts"] == {}
