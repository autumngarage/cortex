"""Tests for the deterministic replay runner (cortex#336, #331, #369)."""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from cortex.hosted.context_assembly import (
    default_token_estimator,
    serialize_candidate_payload,
)
from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
    FixtureScope,
    FixtureSourceSpan,
)
from cortex.hosted.model_interfaces import (
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
)
from cortex.hosted.model_registry import RegisteredPrompt
from cortex.hosted.recorded_responses import (
    RecordedResponsePlayer,
    RecordedResponseStore,
    RecordingEvaluateModel,
    ResponseRecorder,
)
from cortex.hosted.replay_runner import (
    FIXTURE_LOCAL_RETRIEVAL_CONFIG_VERSION,
    REPLAY_REPORT_SCHEMA_VERSION,
    CorpusReplayReport,
    OmissionStage,
    ReplayError,
    ReplayResult,
    build_fixture_candidate_pack,
    ensure_replay_report_payload_version,
    run_corpus_directory,
    run_fixture,
)
from cortex.hosted.scopes import ScopeType

EVAL_PROMPT = RegisteredPrompt(
    prompt_id="evaluate-replay",
    version_number=1,
    template_text="Judge DIFF against DECISIONS.",
    description="Replay-runner test prompt.",
)
PROMPT_VERSION = EVAL_PROMPT.prompt_version
RECORDED_AT = "2026-06-10T12:00:00+00:00"
BIG_BUDGET = 100_000

PATCH = """\
diff --git a/src/payments/retry.py b/src/payments/retry.py
index 1111111..2222222 100644
--- a/src/payments/retry.py
+++ b/src/payments/retry.py
@@ -1,5 +1,7 @@
+import tenacity
+
-def retry_with_backoff(attempt: int) -> float:
+def retry_with_backoff(attempt: int, jitter: bool = False) -> float:
+    # fixed delay per cortex#999
-    return 2.0 ** attempt
+    return 0.5
"""


def _span(doc: str, excerpt: str) -> FixtureSourceSpan:
    return FixtureSourceSpan(
        source_document_hash=hashlib.sha256(doc.encode("utf-8")).hexdigest(),
        start_offset=0,
        end_offset=len(excerpt),
        excerpt=excerpt,
        permalink=f"https://github.com/acme/payments/blob/main/{doc}",
    )


def _decision(
    decision_id: str,
    *,
    status: DecisionStatus = DecisionStatus.CONFIRMED,
    scopes: tuple[FixtureScope, ...] = (),
    superseded_by: str | None = None,
) -> FixtureDecision:
    return FixtureDecision(
        decision_id=decision_id,
        decision_text=f"Decision text for {decision_id}.",
        status=status,
        source_timestamp="2026-06-01T09:00:00+00:00",
        spans=(_span(f"docs/adr/{decision_id}.md", f"excerpt for {decision_id}"),),
        scopes=scopes,
        superseded_by=superseded_by,
    )


D_BACKOFF = _decision(
    "use-exponential-backoff",
    scopes=(
        FixtureScope(scope_type=ScopeType.PATH, value="src/payments/retry.py"),
        FixtureScope(scope_type=ScopeType.SYMBOL, value="retry_with_backoff"),
    ),
)
D_TENACITY = _decision(
    "pin-tenacity",
    status=DecisionStatus.CANDIDATE,
    scopes=(FixtureScope(scope_type=ScopeType.PACKAGE, value="tenacity"),),
)
D_UNRELATED = _decision(
    "unrelated-docs-rule",
    scopes=(FixtureScope(scope_type=ScopeType.PATH, value="docs/runbook.md"),),
)
D_SUPERSEDED = _decision(
    "old-retry-rule",
    status=DecisionStatus.SUPERSEDED,
    scopes=(FixtureScope(scope_type=ScopeType.PATH, value="src/payments/retry.py"),),
    superseded_by="use-exponential-backoff",
)

EF_BACKOFF = ExpectedFinding(
    finding_id="f-contradicts-backoff",
    finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
    decision_id="use-exponential-backoff",
    cited_span_hashes=D_BACKOFF.span_hashes,
    summary="The diff replaces exponential backoff with a fixed delay.",
)
EF_UNRELATED = ExpectedFinding(
    finding_id="f-omitted-constraint",
    finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
    decision_id="unrelated-docs-rule",
    cited_span_hashes=D_UNRELATED.span_hashes,
    summary="The diff ignores the runbook constraint.",
)


def _fixture(
    *,
    fixture_id: str = "replay-fixture",
    decisions: tuple[FixtureDecision, ...] = (
        D_BACKOFF,
        D_TENACITY,
        D_UNRELATED,
        D_SUPERSEDED,
    ),
    expected_findings: tuple[ExpectedFinding, ...] = (EF_BACKOFF,),
    patch: str = PATCH,
) -> EvalFixture:
    return EvalFixture(
        fixture_id=fixture_id,
        diff=FixtureDiff(
            repo_owner="acme",
            repo_name="payments",
            base_sha="abc1234",
            head_sha="def5678",
            patch=patch,
        ),
        decisions=decisions,
        expected_findings=expected_findings,
    )


class _ScriptedEvaluateModel:
    """Deterministic EvaluateModel: zero live calls, result binds the request."""

    def __init__(
        self,
        findings: tuple[FindingDraft, ...] = (),
        *,
        omitted_decision_count: int = 0,
        degraded_reasons: tuple[str, ...] = (),
    ) -> None:
        self._findings = findings
        self._omitted = omitted_decision_count
        self._degraded = degraded_reasons

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        return EvaluateResult(
            findings=self._findings,
            model_id="anthropic/claude-fable-5",
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
            omitted_decision_count=self._omitted,
            degraded_reasons=self._degraded,
        )


def _finding_for(
    fixture: EvalFixture,
    decision_id: str,
    *,
    finding_class: FindingClass = FindingClass.CONTRADICTS_PRIOR_DECISION,
    cited_span_hashes: tuple[str, ...] | None = None,
) -> FindingDraft:
    emulation = build_fixture_candidate_pack(fixture)
    decision = next(d for d in fixture.decisions if d.decision_id == decision_id)
    return FindingDraft(
        finding_class=finding_class,
        decision_node_id=emulation.decision_node_id_by_decision_id[decision_id],
        cited_span_hashes=(
            cited_span_hashes if cited_span_hashes is not None else decision.span_hashes
        ),
        summary=f"Replayed finding about {decision_id}.",
        confidence_label="high",
    )


def _run(
    fixture: EvalFixture,
    model: object,
    *,
    token_budget: int = BIG_BUDGET,
    limit: int = 30,
) -> ReplayResult:
    return run_fixture(
        fixture,
        model,  # type: ignore[arg-type]
        prompt_version=PROMPT_VERSION,
        token_budget=token_budget,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Fixture-local pack construction
# ---------------------------------------------------------------------------


def test_pack_orders_by_structural_score_then_decision_id() -> None:
    emulation = build_fixture_candidate_pack(_fixture())
    ordered = [
        emulation.decision_id_by_node_id[c.decision_node_id]
        for c in emulation.pack.candidates
    ]
    # path(100) + symbol(95) = 195 beats package(75).
    assert ordered == ["use-exponential-backoff", "pin-tenacity"]
    scores = [c.score for c in emulation.pack.candidates]
    assert scores == [195.0, 75.0]


def test_pack_tie_breaks_on_decision_id() -> None:
    tied_a = _decision(
        "zeta-rule",
        scopes=(FixtureScope(scope_type=ScopeType.PACKAGE, value="tenacity"),),
    )
    tied_b = _decision(
        "alpha-rule",
        scopes=(FixtureScope(scope_type=ScopeType.PACKAGE, value="tenacity"),),
    )
    fixture = _fixture(decisions=(tied_a, tied_b), expected_findings=())
    emulation = build_fixture_candidate_pack(fixture)
    ordered = [
        emulation.decision_id_by_node_id[c.decision_node_id]
        for c in emulation.pack.candidates
    ]
    assert ordered == ["alpha-rule", "zeta-rule"]


def test_pack_suppresses_structurally_unmatched_decisions() -> None:
    emulation = build_fixture_candidate_pack(_fixture())
    assert emulation.pack.omitted_counts[OmissionStage.SUPPRESSED_BELOW_FLOOR.value] == 1
    assert (
        emulation.omission_stage_by_decision_id["unrelated-docs-rule"]
        is OmissionStage.SUPPRESSED_BELOW_FLOOR
    )


def test_pack_filters_non_reviewable_statuses() -> None:
    emulation = build_fixture_candidate_pack(_fixture())
    assert emulation.pack.omitted_counts[OmissionStage.STATUS_FILTERED.value] == 1
    assert (
        emulation.omission_stage_by_decision_id["old-retry-rule"]
        is OmissionStage.STATUS_FILTERED
    )
    # graph_node_count mirrors the SQL base_versions count: status-eligible only.
    assert emulation.pack.graph_node_count == 3
    assert emulation.pack.candidate_pool_size == 2


def test_pack_counts_over_limit_omissions() -> None:
    emulation = build_fixture_candidate_pack(_fixture(), limit=1)
    assert len(emulation.pack.candidates) == 1
    assert emulation.pack.omitted_counts[OmissionStage.OVER_LIMIT.value] == 1
    assert (
        emulation.omission_stage_by_decision_id["pin-tenacity"] is OmissionStage.OVER_LIMIT
    )


def test_pack_carries_fixture_span_material_and_scope_reason_codes() -> None:
    emulation = build_fixture_candidate_pack(_fixture())
    candidate = emulation.pack.candidates[0]
    assert [span.span_hash for span in candidate.cited_spans] == list(D_BACKOFF.span_hashes)
    assert candidate.cited_spans[0].excerpt == D_BACKOFF.spans[0].excerpt
    assert candidate.cited_spans[0].permalink == D_BACKOFF.spans[0].permalink
    assert candidate.reason_codes == (
        "scope:path:src/payments/retry.py",
        "scope:symbol:retry_with_backoff",
    )
    assert emulation.pack.retrieval_config_version == FIXTURE_LOCAL_RETRIEVAL_CONFIG_VERSION


def test_pack_construction_is_deterministic() -> None:
    first = build_fixture_candidate_pack(_fixture())
    second = build_fixture_candidate_pack(_fixture())
    assert first.pack.candidate_set_hash == second.pack.candidate_set_hash
    assert dict(first.decision_node_id_by_decision_id) == dict(
        second.decision_node_id_by_decision_id
    )
    # Derived ids are real UUIDs, satisfying the hosted candidate shapes.
    for node_id in first.decision_node_id_by_decision_id.values():
        UUID(node_id)


def test_unparseable_patch_fails_naming_the_fixture() -> None:
    fixture = _fixture(patch="this is not a unified diff\n", expected_findings=())
    with pytest.raises(ReplayError, match="replay-fixture"):
        build_fixture_candidate_pack(fixture)


def test_pack_limit_must_respect_retrieval_bound() -> None:
    with pytest.raises(ReplayError, match="limit must be between 1 and 30"):
        build_fixture_candidate_pack(_fixture(), limit=31)


# ---------------------------------------------------------------------------
# Grading: matched / missed / unexpected
# ---------------------------------------------------------------------------


def test_matched_expected_finding() -> None:
    fixture = _fixture()
    model = _ScriptedEvaluateModel((_finding_for(fixture, "use-exponential-backoff"),))
    result = _run(fixture, model)
    assert result.matched_count == 1
    assert result.missed_count == 0
    assert result.unexpected_count == 0
    outcome = result.expected_finding_outcomes[0]
    assert outcome.matched is True
    assert outcome.omitted_at_stage is None
    assert result.diagnostics.impossible_expected_findings == ()


def test_missed_expected_finding_when_evaluator_emits_nothing() -> None:
    fixture = _fixture()
    result = _run(fixture, _ScriptedEvaluateModel())
    assert result.matched_count == 0
    assert result.missed_count == 1
    outcome = result.expected_finding_outcomes[0]
    assert outcome.matched is False
    # The decision was visible: a genuine evaluator miss, not an omission.
    assert outcome.omitted_at_stage is None
    assert result.diagnostics.impossible_expected_findings == ()


def test_unexpected_emission_is_graded_with_reason() -> None:
    fixture = _fixture()
    extra = _finding_for(
        fixture, "pin-tenacity", finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT
    )
    matched = _finding_for(fixture, "use-exponential-backoff")
    result = _run(fixture, _ScriptedEvaluateModel((matched, extra)))
    assert result.matched_count == 1
    assert result.unexpected_count == 1
    emission = result.unexpected_emissions[0]
    assert emission.decision_id == "pin-tenacity"
    assert emission.reason == "no_matching_expected_finding"


def test_match_requires_identical_cited_span_set() -> None:
    fixture = _fixture()
    wrong_spans = _finding_for(
        fixture, "use-exponential-backoff", cited_span_hashes=D_TENACITY.span_hashes
    )
    result = _run(fixture, _ScriptedEvaluateModel((wrong_spans,)))
    assert result.matched_count == 0
    assert result.missed_count == 1
    assert result.unexpected_count == 1


def test_match_requires_same_finding_class() -> None:
    fixture = _fixture()
    wrong_class = _finding_for(
        fixture,
        "use-exponential-backoff",
        finding_class=FindingClass.REVERSES_SUPERSEDED_PATTERN,
    )
    result = _run(fixture, _ScriptedEvaluateModel((wrong_class,)))
    assert result.matched_count == 0
    assert result.unexpected_count == 1


def test_matching_is_one_to_one() -> None:
    fixture = _fixture()
    finding = _finding_for(fixture, "use-exponential-backoff")
    result = _run(fixture, _ScriptedEvaluateModel((finding, finding)))
    assert result.matched_count == 1
    assert result.unexpected_count == 1


def test_emission_for_unknown_decision_node_is_unexpected() -> None:
    fixture = _fixture()
    stranger = FindingDraft(
        finding_class=FindingClass.CITES_MISSING_PATH,
        decision_node_id=str(UUID(int=42)),
        cited_span_hashes=D_BACKOFF.span_hashes,
        summary="A finding about a decision the fixture never defined.",
        confidence_label="low",
    )
    result = _run(fixture, _ScriptedEvaluateModel((stranger,)))
    assert result.unexpected_count == 1
    assert result.unexpected_emissions[0].reason == "unknown_decision_node"
    assert result.unexpected_emissions[0].decision_id is None


def test_emission_for_omitted_decision_cannot_match() -> None:
    fixture = _fixture(expected_findings=(EF_UNRELATED,))
    emission = _finding_for(
        fixture,
        "unrelated-docs-rule",
        finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
    )
    result = _run(fixture, _ScriptedEvaluateModel((emission,)))
    # The expected finding stays impossible; crediting the emission would
    # hide that the evaluator never saw the decision.
    outcome = result.expected_finding_outcomes[0]
    assert outcome.matched is False
    assert outcome.omitted_at_stage is OmissionStage.SUPPRESSED_BELOW_FLOOR
    assert result.unexpected_emissions[0].reason == "decision_not_in_evaluator_context"


# ---------------------------------------------------------------------------
# Per-stage omission attribution (cortex#331)
# ---------------------------------------------------------------------------


def test_missed_attribution_suppressed_below_floor() -> None:
    fixture = _fixture(expected_findings=(EF_UNRELATED,))
    result = _run(fixture, _ScriptedEvaluateModel())
    outcome = result.expected_finding_outcomes[0]
    assert outcome.omitted_at_stage is OmissionStage.SUPPRESSED_BELOW_FLOOR
    impossible = result.diagnostics.impossible_expected_findings
    assert len(impossible) == 1
    assert impossible[0].finding_id == "f-omitted-constraint"
    assert impossible[0].decision_id == "unrelated-docs-rule"
    assert impossible[0].omitted_at_stage is OmissionStage.SUPPRESSED_BELOW_FLOOR


def test_missed_attribution_status_filtered() -> None:
    ef_superseded = ExpectedFinding(
        finding_id="f-reverses-superseded",
        finding_class=FindingClass.REVERSES_SUPERSEDED_PATTERN,
        decision_id="old-retry-rule",
        cited_span_hashes=D_SUPERSEDED.span_hashes,
        summary="The diff reverts to the superseded retry pattern.",
    )
    fixture = _fixture(expected_findings=(ef_superseded,))
    result = _run(fixture, _ScriptedEvaluateModel())
    outcome = result.expected_finding_outcomes[0]
    assert outcome.omitted_at_stage is OmissionStage.STATUS_FILTERED
    assert result.diagnostics.impossible_expected_findings[0].omitted_at_stage is (
        OmissionStage.STATUS_FILTERED
    )


def test_missed_attribution_over_limit() -> None:
    ef_tenacity = ExpectedFinding(
        finding_id="f-pin-tenacity",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="pin-tenacity",
        cited_span_hashes=D_TENACITY.span_hashes,
        summary="The diff imports tenacity without honoring the pin decision.",
    )
    fixture = _fixture(expected_findings=(ef_tenacity,))
    result = _run(fixture, _ScriptedEvaluateModel(), limit=1)
    outcome = result.expected_finding_outcomes[0]
    assert outcome.omitted_at_stage is OmissionStage.OVER_LIMIT


def test_missed_attribution_over_budget() -> None:
    ef_tenacity = ExpectedFinding(
        finding_id="f-pin-tenacity",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="pin-tenacity",
        cited_span_hashes=D_TENACITY.span_hashes,
        summary="The diff imports tenacity without honoring the pin decision.",
    )
    fixture = _fixture(expected_findings=(ef_tenacity,))
    emulation = build_fixture_candidate_pack(fixture)
    first_cost = default_token_estimator.estimate_tokens(
        serialize_candidate_payload(emulation.pack.candidates[0])
    )
    result = _run(fixture, _ScriptedEvaluateModel(), token_budget=first_cost)
    outcome = result.expected_finding_outcomes[0]
    assert outcome.omitted_at_stage is OmissionStage.OVER_BUDGET
    assert result.diagnostics.context_omitted_for_budget == 1


def test_per_stage_omission_counts_are_named_never_summed() -> None:
    fixture = _fixture()
    emulation = build_fixture_candidate_pack(fixture)
    first_cost = default_token_estimator.estimate_tokens(
        serialize_candidate_payload(emulation.pack.candidates[0])
    )
    result = _run(fixture, _ScriptedEvaluateModel(), token_budget=first_cost)
    diagnostics = result.diagnostics
    assert diagnostics.pack_omitted_counts["status_filtered"] == 1
    assert diagnostics.pack_omitted_counts["suppressed_below_floor"] == 1
    assert diagnostics.pack_omitted_counts["over_limit"] == 0
    assert diagnostics.context_omitted_for_budget == 1
    assert diagnostics.total_omitted["over_budget"] == 1
    # Every pack stage survives the merge into total_omitted unchanged.
    for key in ("status_filtered", "suppressed_below_floor", "over_limit"):
        assert diagnostics.total_omitted[key] == diagnostics.pack_omitted_counts[key]


def test_evaluator_reported_omissions_surface_in_diagnostics() -> None:
    fixture = _fixture()
    model = _ScriptedEvaluateModel(
        omitted_decision_count=2, degraded_reasons=("context window pressure",)
    )
    result = _run(fixture, model)
    assert result.diagnostics.evaluator_reported_omitted_decisions == 2
    assert result.diagnostics.evaluator_degraded_reasons == ("context window pressure",)


# ---------------------------------------------------------------------------
# Over-budget manual-review signal (cortex#369)
# ---------------------------------------------------------------------------


def test_needs_manual_review_arithmetic_when_over_budget() -> None:
    fixture = _fixture()
    emulation = build_fixture_candidate_pack(fixture)
    first_cost = default_token_estimator.estimate_tokens(
        serialize_candidate_payload(emulation.pack.candidates[0])
    )
    result = _run(fixture, _ScriptedEvaluateModel(), token_budget=first_cost)
    assert result.needs_manual_review is True
    budget = result.budget
    assert budget.token_budget == first_cost
    assert budget.estimated_tokens_used == first_cost
    assert budget.remaining_tokens == 0
    assert budget.included_candidate_count == 1
    assert budget.omitted_for_budget == 1
    # Invariant: included + budget-omitted covers the whole pack.
    assert budget.included_candidate_count + budget.omitted_for_budget == len(
        emulation.pack.candidates
    )
    payload = result.as_payload()
    assert payload["needs_manual_review"] is True
    assert payload["budget"]["omitted_for_budget"] == 1


def test_needs_manual_review_false_when_budget_fits() -> None:
    result = _run(_fixture(), _ScriptedEvaluateModel())
    assert result.needs_manual_review is False
    assert result.budget.omitted_for_budget == 0


# ---------------------------------------------------------------------------
# Byte determinism and the recorded-response loop (cortex#336)
# ---------------------------------------------------------------------------


def test_scripted_reruns_are_byte_identical() -> None:
    fixture = _fixture()
    finding = _finding_for(fixture, "use-exponential-backoff")
    first = _run(fixture, _ScriptedEvaluateModel((finding,)))
    second = _run(fixture, _ScriptedEvaluateModel((finding,)))
    assert first.to_canonical_json() == second.to_canonical_json()


def test_recorded_player_reruns_are_byte_identical(tmp_path: Path) -> None:
    fixture = _fixture()
    finding = _finding_for(fixture, "use-exponential-backoff")
    recording_path = tmp_path / "recorded.json"
    recorder = ResponseRecorder(fixture_path=recording_path, recorded_at=RECORDED_AT)
    recording_model = RecordingEvaluateModel(_ScriptedEvaluateModel((finding,)), recorder)
    recorded_run = _run(fixture, recording_model)

    player = RecordedResponsePlayer.load(recording_path)
    first = _run(fixture, player)
    second = _run(fixture, player)
    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.to_canonical_json() == recorded_run.to_canonical_json()
    assert first.matched_count == 1


def test_missing_recording_fails_naming_the_fixture(tmp_path: Path) -> None:
    fixture = _fixture()
    player = RecordedResponsePlayer(
        RecordedResponseStore(), fixture_path=tmp_path / "empty.json"
    )
    with pytest.raises(ReplayError, match="replay-fixture") as excinfo:
        _run(fixture, player)
    assert "never falls back to a live model call" in str(excinfo.value)


def test_replay_error_classifies_as_fail_closed_refusal() -> None:
    assert classify_failure(ReplayError("probe")) is DegradationMode.FAIL_CLOSED_REFUSAL


# ---------------------------------------------------------------------------
# Corpus batch runner
# ---------------------------------------------------------------------------


def _write_corpus(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # Filenames deliberately invert fixture-id order to prove result sorting.
    (corpus / "a-second.json").write_text(
        _fixture(fixture_id="zulu-fixture").to_canonical_json(), encoding="utf-8"
    )
    (corpus / "b-first.json").write_text(
        _fixture(fixture_id="alpha-fixture").to_canonical_json(), encoding="utf-8"
    )
    return corpus


def test_corpus_runner_runs_every_fixture_sorted_by_id(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path)
    report = run_corpus_directory(
        corpus,
        _ScriptedEvaluateModel(),
        prompt_version=PROMPT_VERSION,
        token_budget=BIG_BUDGET,
    )
    assert report.fixtures_run == 2
    assert [result.fixture_id for result in report.results] == [
        "alpha-fixture",
        "zulu-fixture",
    ]
    assert report.matched_total == 0
    assert report.missed_total == 2
    assert report.unexpected_total == 0
    assert report.needs_manual_review_count == 0


def test_corpus_runner_reruns_are_byte_identical(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path)
    first = run_corpus_directory(
        corpus,
        _ScriptedEvaluateModel(),
        prompt_version=PROMPT_VERSION,
        token_budget=BIG_BUDGET,
    )
    second = run_corpus_directory(
        corpus,
        _ScriptedEvaluateModel(),
        prompt_version=PROMPT_VERSION,
        token_budget=BIG_BUDGET,
    )
    assert first.to_canonical_json() == second.to_canonical_json()


def test_corpus_runner_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(ReplayError, match="does not exist"):
        run_corpus_directory(
            tmp_path / "absent",
            _ScriptedEvaluateModel(),
            prompt_version=PROMPT_VERSION,
            token_budget=BIG_BUDGET,
        )


def test_corpus_runner_rejects_empty_directory(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReplayError, match="no \\*\\.json fixtures"):
        run_corpus_directory(
            empty,
            _ScriptedEvaluateModel(),
            prompt_version=PROMPT_VERSION,
            token_budget=BIG_BUDGET,
        )


def test_corpus_runner_names_the_invalid_fixture_file(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    bad = corpus / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ReplayError, match=r"broken\.json"):
        run_corpus_directory(
            corpus,
            _ScriptedEvaluateModel(),
            prompt_version=PROMPT_VERSION,
            token_budget=BIG_BUDGET,
        )


def test_corpus_runner_rejects_duplicate_fixture_ids(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("one.json", "two.json"):
        (corpus / name).write_text(
            _fixture(fixture_id="same-id").to_canonical_json(), encoding="utf-8"
        )
    with pytest.raises(ReplayError, match="duplicates fixture_id"):
        run_corpus_directory(
            corpus,
            _ScriptedEvaluateModel(),
            prompt_version=PROMPT_VERSION,
            token_budget=BIG_BUDGET,
        )


# ---------------------------------------------------------------------------
# Version-gated report format
# ---------------------------------------------------------------------------


def test_replay_result_rejects_unknown_schema_version() -> None:
    result = _run(_fixture(), _ScriptedEvaluateModel())
    with pytest.raises(ReplayError, match="unknown replay_report_schema_version"):
        dataclasses.replace(result, report_schema_version=2)


def test_corpus_report_rejects_unknown_schema_version() -> None:
    result = _run(_fixture(), _ScriptedEvaluateModel())
    with pytest.raises(ReplayError, match="unknown replay_report_schema_version"):
        CorpusReplayReport(results=(result,), report_schema_version=99)


def test_payload_version_gate() -> None:
    result = _run(_fixture(), _ScriptedEvaluateModel())
    payload = result.as_payload()
    assert payload["replay_report_schema_version"] == REPLAY_REPORT_SCHEMA_VERSION
    ensure_replay_report_payload_version(payload)
    with pytest.raises(ReplayError, match="unknown replay_report_schema_version"):
        ensure_replay_report_payload_version({"replay_report_schema_version": 2})
    with pytest.raises(ReplayError, match="must be an integer"):
        ensure_replay_report_payload_version({})
    with pytest.raises(ReplayError, match="must be an integer"):
        ensure_replay_report_payload_version({"replay_report_schema_version": True})
