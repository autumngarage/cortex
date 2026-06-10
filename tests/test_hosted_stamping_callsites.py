"""Ledger call-site stamping tests (cortex#322 + #326).

Two sides of one replay contract:

- **Deterministic side (#326):** derive-pipeline ``CANDIDATE_PROPOSED``
  events come from extractors that make no model calls, so they must carry
  no ``(model_id, prompt_version)`` stamp — asserted explicitly, at both the
  extractor (``ensure_unstamped_deterministic_event``) and the derive
  pipeline boundary (``_require_candidate_event``).
- **Model-backed side (#322 + #326):** every ``finding.emitted`` draft
  carries the full stamp from the ``EvaluateResult`` (model_id +
  prompt_version + input_hash), the cortex#328 ``bank_key`` linkage, and a
  first-class ``decision_version_id`` resolved from the pack per cited
  decision. Drift between the pack and the draft is refused visibly.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from cortex.commands.derive import DeriveSourceError, run_derive
from cortex.hosted.advisory_ladder import DEFAULT_ADVISORY_LADDER, EmissionBehavior
from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.banking import BankKey
from cortex.hosted.confidence import ConfidenceTier
from cortex.hosted.cost import ModelPriceTable, RunLedger
from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)
from cortex.hosted.eval_fixtures import FindingClass
from cortex.hosted.evaluator import (
    EmittedFinding,
    EvaluationOutcome,
    EvaluationReplayKey,
    EvaluatorValidationError,
    _finding_emitted_event,
    evaluate_diff,
)
from cortex.hosted.extractors import (
    ExtractorError,
    candidate_events,
    ensure_unstamped_deterministic_event,
    extract_repo_native,
)
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType
from cortex.hosted.model_interfaces import (
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
)
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.schema import create_schema_sql

TENANT = "0b6f9f3e-3a2f-4f2e-9c8d-1a2b3c4d5e6f"
SOURCE = "1c7f8e2d-4b3a-4c5d-8e9f-2b3c4d5e6f70"
MODEL_ID = "stub/eval-model"
PROMPT_VERSION = "evaluate-stage0/v1+abcdefabcdef"
OTHER_PROMPT_VERSION = "evaluate-stage0/v2+abcdefabcdef"
QUERY_HASH = hashlib.sha256(b"query").hexdigest()
GRAPH_HASH = hashlib.sha256(b"graph").hexdigest()
DIFF = "-    delay = backoff_with_jitter(attempt)\n+    delay = 5.0\n"
OCCURRED_AT = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
ACTOR = ActorRef(actor_type="service", actor_id="stamping-test")


# --- shared fixtures ----------------------------------------------------------


def _span_hash(index: int) -> str:
    return hashlib.sha256(f"span-{index}".encode()).hexdigest()


def _candidate(index: int, *, status: str = "confirmed") -> DecisionsForDiffCandidate:
    return DecisionsForDiffCandidate(
        decision_node_id=str(UUID(int=index * 2 + 1)),
        decision_version_id=str(UUID(int=index * 2 + 2)),
        status=status,
        decision_text=f"decision body {index}: retries use exponential backoff",
        score=float(10 - index),
        reason_codes=("scope:path:src/app.py",),
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
) -> DecisionsForDiffCandidatePack:
    return DecisionsForDiffCandidatePack(
        query_hash=QUERY_HASH,
        retrieval_config_version="decisions-for-diff-v2+test",
        graph_snapshot_hash=GRAPH_HASH,
        candidates=tuple(candidates),
        omitted_counts={"over_limit": 0},
        graph_node_count=12,
        candidate_pool_size=len(candidates),
    )


def _finding(candidate: DecisionsForDiffCandidate) -> FindingDraft:
    return FindingDraft(
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_node_id=candidate.decision_node_id,
        cited_span_hashes=tuple(span.span_hash for span in candidate.cited_spans),
        summary="The diff conflicts with a recorded decision.",
        confidence_label="advisory",
    )


@dataclass
class ScriptedModel:
    """A scripted fake satisfying the EvaluateModel protocol."""

    build_findings: Callable[[EvaluateRequest], tuple[FindingDraft, ...]]
    model_id: str = MODEL_ID
    requests: list[EvaluateRequest] = field(default_factory=list)

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        self.requests.append(request)
        return EvaluateResult(
            findings=self.build_findings(request),
            model_id=self.model_id,
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
        )


def _scripted(*findings: FindingDraft) -> ScriptedModel:
    return ScriptedModel(build_findings=lambda _request: tuple(findings))


def _evaluate(
    pack: DecisionsForDiffCandidatePack,
    model: EvaluateModel,
    **overrides: Any,
) -> EvaluationOutcome:
    params: dict[str, Any] = {
        "token_budget": 100_000,
        "ladder": DEFAULT_ADVISORY_LADDER,
        "run_ledger": RunLedger(
            run_id="run-stamping-1",
            price_table=ModelPriceTable(version="2026-06-10", prices=()),
        ),
        "prompt_version": PROMPT_VERSION,
        "tenant_id": TENANT,
        "source_id": SOURCE,
        "actor": ACTOR,
        "occurred_at": OCCURRED_AT,
    }
    params.update(overrides)
    return evaluate_diff(pack, DIFF, model, **params)


def _document(
    *, document_type: str, external_id: str, content: str
) -> SourceDocument:
    return SourceDocument(
        tenant_id=TENANT,
        source_id=SOURCE,
        document_type=document_type,
        external_id=external_id,
        permalink=f"test:{external_id}",
        author_ref="stamping-test <test@example.com>",
        source_timestamp=OCCURRED_AT,
        content=content,
    )


def _stamped_candidate_event(
    document: SourceDocument, *, model_id: str | None, prompt_version: str | None
) -> LedgerEvent:
    return LedgerEvent(
        tenant_id=document.tenant_id,
        source_id=document.source_id,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=ActorRef(actor_type="derive", actor_id="stamped-test-extractor"),
        occurred_at=document.source_timestamp,
        idempotency_key=f"stamped:{document.external_id}",
        source_event_external_id=document.external_id,
        payload={"decision_text": "stamped"},
        model_id=model_id,
        prompt_version=prompt_version,
    )


def _replay_key(**overrides: Any) -> EvaluationReplayKey:
    params: dict[str, Any] = {
        "graph_snapshot_hash": GRAPH_HASH,
        "retrieval_config_version": "decisions-for-diff-v2+test",
        "query_hash": QUERY_HASH,
        "candidate_set_hash": hashlib.sha256(b"set").hexdigest(),
        "context_hash": hashlib.sha256(b"ctx").hexdigest(),
        "input_hash": hashlib.sha256(b"input").hexdigest(),
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "run_id": "run-stamping-1",
        "estimator_version": "est-v1",
        "token_budget": 1000,
    }
    params.update(overrides)
    return EvaluationReplayKey(**params)


def _tampered_outcome(
    outcome: EvaluationOutcome, draft: LedgerEvent
) -> EvaluationOutcome:
    return replace(outcome, ledger_event_drafts=(draft,))


def _with_payload(draft: LedgerEvent, payload: dict[str, Any]) -> LedgerEvent:
    return replace(draft, payload=payload, metadata=dict(draft.metadata))


# --- deterministic extractors stay unstamped (#326) ---------------------------


def test_agent_instruction_candidate_events_carry_no_model_stamp() -> None:
    document = _document(
        document_type="repo-file",
        external_id="CLAUDE.md",
        content="# Rules\n\n- Never commit secrets to the repository.\n",
    )
    events = candidate_events(document, extract_repo_native(document))
    assert events, "the constraint bullet must extract as a candidate"
    for event in events:
        assert event.model_id is None
        assert event.prompt_version is None


def test_commit_message_candidate_events_carry_no_model_stamp() -> None:
    document = _document(
        document_type="commit_message",
        external_id="a" * 40,
        content=(
            "fix: resolve retry backoff regression\n\n"
            "We decided to pin the retry policy to exponential backoff.\n"
        ),
    )
    events = candidate_events(document, extract_repo_native(document))
    assert events, "the decision-shaped commit must extract candidates"
    for event in events:
        assert event.model_id is None
        assert event.prompt_version is None


def test_unstamped_guard_returns_event_unchanged() -> None:
    document = _document(
        document_type="repo-file", external_id="CLAUDE.md", content="x"
    )
    event = _stamped_candidate_event(document, model_id=None, prompt_version=None)
    assert ensure_unstamped_deterministic_event(event) is event


def test_unstamped_guard_refuses_stamped_event() -> None:
    document = _document(
        document_type="repo-file", external_id="CLAUDE.md", content="x"
    )
    stamped = _stamped_candidate_event(
        document, model_id="stub/derive-model", prompt_version=PROMPT_VERSION
    )
    with pytest.raises(ExtractorError, match=r"model stamp.*cortex#326") as excinfo:
        ensure_unstamped_deterministic_event(stamped)
    assert "stub/derive-model" in str(excinfo.value)


def test_derive_pipeline_refuses_stamped_extractor_output(tmp_path: Path) -> None:
    """The pipeline boundary backstops pluggable extractors (cortex#326)."""

    source = tmp_path / "CLAUDE.md"
    source.write_text("- Never commit secrets.\n", encoding="utf-8")
    db_path = tmp_path / "derive-events.sqlite"

    def stamped_extractor(document: SourceDocument) -> tuple[LedgerEvent, ...]:
        return (
            _stamped_candidate_event(
                document, model_id="stub/derive-model", prompt_version=PROMPT_VERSION
            ),
        )

    with pytest.raises(DeriveSourceError, match=r"model stamp.*cortex#326") as excinfo:
        run_derive(
            project_root=tmp_path,
            source_files=[source],
            tenant_id=TENANT,
            source_id=SOURCE,
            extractor=stamped_extractor,
            db_path=db_path,
        )
    # The failure names the offending source, and validate-all-then-persist
    # means the refused run leaves no store behind.
    assert "CLAUDE.md" in str(excinfo.value)
    assert not db_path.exists()


def test_candidate_proposed_event_validates_without_model_stamp() -> None:
    """The model-stamp requirement is per-event-type, never global."""

    document = _document(
        document_type="repo-file", external_id="CLAUDE.md", content="x"
    )
    event = _stamped_candidate_event(document, model_id=None, prompt_version=None)
    assert event.event_type is LedgerEventType.CANDIDATE_PROPOSED
    assert event.as_immutable_payload()["model_id"] is None
    assert event.as_immutable_payload()["prompt_version"] is None


# --- model-backed drafts carry the full stamp (#326) ---------------------------


def test_draft_carries_result_model_stamp_and_input_hash() -> None:
    candidate = _candidate(0)
    model = _scripted(_finding(candidate))
    outcome = _evaluate(_pack([candidate]), model)
    draft = outcome.ledger_event_drafts[0]
    request = model.requests[0]
    assert draft.model_id == MODEL_ID
    assert draft.prompt_version == PROMPT_VERSION
    assert draft.payload["replay"]["model_id"] == MODEL_ID
    assert draft.payload["replay"]["prompt_version"] == PROMPT_VERSION
    assert draft.payload["replay"]["input_hash"] == request.input_hash


def test_draft_payload_carries_bank_key() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    draft = outcome.ledger_event_drafts[0]
    expected = BankKey(
        task="evaluate",
        input_hash=outcome.replay.input_hash,
        model_id=MODEL_ID,
        prompt_version=PROMPT_VERSION,
    ).bank_key
    assert outcome.replay.bank_key == expected
    assert draft.payload["bank_key"] == expected


def test_bank_key_is_deterministic_across_runs() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    first = _evaluate(pack, _scripted(_finding(candidate)))
    second = _evaluate(
        pack,
        _scripted(_finding(candidate)),
        occurred_at=datetime(2026, 6, 11, 9, 30, tzinfo=UTC),
    )
    assert (
        first.ledger_event_drafts[0].payload["bank_key"]
        == second.ledger_event_drafts[0].payload["bank_key"]
    )


def test_bank_key_attributes_prompt_version_drift() -> None:
    candidate = _candidate(0)
    pack = _pack([candidate])
    first = _evaluate(pack, _scripted(_finding(candidate)))
    drifted = _evaluate(
        pack, _scripted(_finding(candidate)), prompt_version=OTHER_PROMPT_VERSION
    )
    assert (
        first.ledger_event_drafts[0].payload["bank_key"]
        != drifted.ledger_event_drafts[0].payload["bank_key"]
    )


# --- decision-version stamping per citation (#322) -----------------------------


def test_each_draft_carries_its_cited_candidates_decision_version() -> None:
    first = _candidate(0)
    second = _candidate(1)
    pack = _pack([first, second])
    outcome = _evaluate(pack, _scripted(_finding(first), _finding(second)))
    assert len(outcome.ledger_event_drafts) == 2
    by_node = {
        draft.payload["finding"]["decision_node_id"]: draft.payload[
            "decision_version_id"
        ]
        for draft in outcome.ledger_event_drafts
    }
    assert by_node == {
        first.decision_node_id: first.decision_version_id,
        second.decision_node_id: second.decision_version_id,
    }
    assert first.decision_version_id != second.decision_version_id


def test_outcome_refuses_decision_version_drift_between_pack_and_draft() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    payload = dict(outcome.ledger_event_drafts[0].payload)
    payload["decision_version_id"] = str(UUID(int=777))
    tampered = _with_payload(outcome.ledger_event_drafts[0], payload)
    with pytest.raises(EvaluatorValidationError, match=r"decision_version_id.*cortex#322"):
        _tampered_outcome(outcome, tampered)


def test_outcome_refuses_draft_missing_bank_key() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    payload = dict(outcome.ledger_event_drafts[0].payload)
    del payload["bank_key"]
    tampered = _with_payload(outcome.ledger_event_drafts[0], payload)
    with pytest.raises(EvaluatorValidationError, match=r"bank key.*cortex#326"):
        _tampered_outcome(outcome, tampered)


def test_outcome_refuses_draft_with_wrong_bank_key() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    payload = dict(outcome.ledger_event_drafts[0].payload)
    payload["bank_key"] = BankKey(
        task="evaluate",
        input_hash=outcome.replay.input_hash,
        model_id="stub/other-model",
        prompt_version=PROMPT_VERSION,
    ).bank_key
    tampered = _with_payload(outcome.ledger_event_drafts[0], payload)
    with pytest.raises(EvaluatorValidationError, match=r"bank key.*cortex#326"):
        _tampered_outcome(outcome, tampered)


def test_outcome_refuses_model_stamp_drift_on_draft() -> None:
    candidate = _candidate(0)
    outcome = _evaluate(_pack([candidate]), _scripted(_finding(candidate)))
    draft = outcome.ledger_event_drafts[0]
    tampered = replace(
        draft,
        model_id="stub/other-model",
        payload=dict(draft.payload),
        metadata=dict(draft.metadata),
    )
    with pytest.raises(
        EvaluatorValidationError, match=r"\(model_id, prompt_version\) stamp"
    ):
        _tampered_outcome(outcome, tampered)


def test_builder_refuses_decision_version_drift_against_pack() -> None:
    candidate = _candidate(0)
    emitted = EmittedFinding(
        finding=_finding(candidate),
        decision_version_id=str(UUID(int=4242)),
        tier=ConfidenceTier.ADVISORY,
        behavior=EmissionBehavior.ADVISORY_COMMENT,
    )
    with pytest.raises(
        EvaluatorValidationError, match=r"decision-version drift.*cortex#322"
    ):
        _finding_emitted_event(
            emitted=emitted,
            cited=candidate,
            ordinal=0,
            replay=_replay_key(),
            tenant_id=TENANT,
            source_id=SOURCE,
            actor=ACTOR,
            occurred_at=OCCURRED_AT,
        )


def test_builder_refuses_citation_node_drift() -> None:
    cited = _candidate(0)
    other = _candidate(1)
    emitted = EmittedFinding(
        finding=_finding(other),
        decision_version_id=other.decision_version_id,
        tier=ConfidenceTier.ADVISORY,
        behavior=EmissionBehavior.ADVISORY_COMMENT,
    )
    with pytest.raises(EvaluatorValidationError, match=r"citation drift.*cortex#322"):
        _finding_emitted_event(
            emitted=emitted,
            cited=cited,
            ordinal=0,
            replay=_replay_key(),
            tenant_id=TENANT,
            source_id=SOURCE,
            actor=ACTOR,
            occurred_at=OCCURRED_AT,
        )


# --- schema note for the deferred DB hardening (#322) --------------------------


def test_schema_carries_322_hardening_todo_note() -> None:
    sql = create_schema_sql()
    assert "TODO(cortex#322)" in sql
    # The note sits with the table whose hardening it defers.
    assert sql.index("TODO(cortex#322)") < sql.index(
        "CREATE TABLE IF NOT EXISTS cortex_hosted.ledger_events"
    )
