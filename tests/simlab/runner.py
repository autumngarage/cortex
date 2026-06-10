"""simlab scenario runner: derive → local store → fixture-pack replay (#521).

One scenario run is the whole Stage 0 single-player loop against a synthetic
repo, composed only from shipped modules:

1. **Materialize** the scenario's archetype (``generator.materialize_archetype``).
2. **Derive** through the production pipeline into the repo's local
   replay-export store (``generator.derive_materialized`` →
   ``.cortex/.index/derive-events.sqlite``).
3. **Re-read the store** through the same decode path the confirm verbs use
   (``cortex.commands.confirm.load_candidate_rows``) and build the scenario's
   :class:`~cortex.hosted.eval_fixtures.EvalFixture`: spec selectors mark
   which decisions a human confirmed or superseded, and post-derive edits
   trigger the span-drift check — a decision whose source document no longer
   hashes to what its spans cite is *excluded with a named skip*, mirroring
   the ``cortex push`` content-drift contract. A drifted citation never
   reaches the evaluator.
4. **Replay** through ``cortex.hosted.replay_runner.run_fixture`` — the one
   fixture-pack review path — against a recorded (or scripted) evaluate
   model. CI uses the committed recordings via ``RecordedResponsePlayer``;
   zero live model calls.
5. **Verify** every number the scenario spec pins against the frozen
   :class:`~cortex.hosted.replay_runner.ReplayResult` shapes.

The scripted evaluate model is the scenario's ground truth: it emits exactly
the spec's expected findings for decisions the budgeted pack actually shows
it (an omitted decision can no more be cited by the script than by a real
model), so true negatives are literal empty results and the no-spam bar is a
real assertion, not a vibe.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cortex.commands.confirm import CandidateRow, load_candidate_rows
from cortex.commands.derive import DERIVE_AUTHOR_REF, DERIVE_DOCUMENT_TYPE
from cortex.hosted.derive_store import DeriveEventStore, derive_store_path
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FixtureDecision,
    FixtureDiff,
    FixtureScope,
    FixtureSourceSpan,
)
from cortex.hosted.evaluator import evaluate_prompt_guidance
from cortex.hosted.model_interfaces import (
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
)
from cortex.hosted.model_registry import RegisteredPrompt
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.replay_runner import ReplayResult, run_fixture
from cortex.hosted.scopes import ScopeType
from tests.simlab.generator import (
    MaterializedRepo,
    SimlabDeriveOutcome,
    derive_materialized,
    materialize_archetype,
)
from tests.simlab.specs import (
    ArchetypeSpec,
    ExpectedFindingSpec,
    ScenarioSpec,
    SimlabSpecError,
)

# The simlab evaluate contract. The template embeds the canonical Stage 0
# class guidance, so this self-certifying prompt_version drifts — and every
# committed recording misses visibly — the moment the registered finding
# vocabulary changes (the same posture as the review verb's prompt).
SIMLAB_EVALUATE_PROMPT = RegisteredPrompt(
    prompt_id="simlab-evaluate",
    version_number=1,
    template_text=(
        "Emit exactly the scenario's expected findings for decisions present "
        "in the bounded candidate pack (simlab scripted ground truth, "
        "issues #520/#521).\n" + evaluate_prompt_guidance()
    ),
    description="simlab scenario replay evaluate contract (issue #521).",
)
SIMLAB_PROMPT_VERSION = SIMLAB_EVALUATE_PROMPT.prompt_version
SIMLAB_MODEL_ID = "simlab/scripted-evaluate"

# Derive source types whose documents are repo files the drift check can
# rebuild content-keyed from the working tree — mirrors
# cortex.hosted.push.FILE_BACKED_SOURCE_TYPES (gathered text sources are
# immutable history and cannot drift).
FILE_BACKED_SOURCE_TYPES = frozenset({"agent_instructions", "adr", "codeowners"})

SPAN_DRIFT_SKIP_REASON = "span_drift"


class SimlabRunError(SimlabSpecError):
    """Raised when a scenario pipeline cannot run or its spec cannot resolve."""


class SimlabExpectationError(AssertionError):
    """Raised when a replayed scenario disagrees with its pinned expectations."""


# ---------------------------------------------------------------------------
# Selector resolution (unique-substring, fail-closed)
# ---------------------------------------------------------------------------


def select_decision(
    decisions: Mapping[str, FixtureDecision], selector: str, *, scenario_id: str
) -> FixtureDecision:
    """Resolve a unique-substring selector over derived decision text."""

    matches = [
        decision
        for decision in decisions.values()
        if selector in decision.decision_text
    ]
    if not matches:
        raise SimlabRunError(
            f"scenario {scenario_id!r}: selector {selector!r} matches no derived decision"
        )
    if len(matches) > 1:
        listing = ", ".join(decision.decision_id for decision in matches)
        raise SimlabRunError(
            f"scenario {scenario_id!r}: selector {selector!r} is ambiguous; "
            f"it matches: {listing}"
        )
    return matches[0]


# ---------------------------------------------------------------------------
# Store rows → fixture decisions (with the span-drift check)
# ---------------------------------------------------------------------------


def _decision_id_for(candidate: CandidateRow) -> str:
    return f"d-{candidate.event_hash[:12]}"


def _source_relpath(candidate: CandidateRow) -> str:
    """The repo-relative path of a file-backed candidate's source document.

    Derive stamps ``source_event_external_id`` as
    ``<external-id>@<timestamp>#<span-hash>``; for walked repo files the
    external id is the path relative to the project root.
    """

    external = candidate.external_id
    if not external:
        raise SimlabRunError(
            f"candidate {candidate.event_hash[:12]} has no external id; "
            "cannot locate its source document"
        )
    return external.rsplit("#", 1)[0].rsplit("@", 1)[0]


def _document_drifted(candidate: CandidateRow, *, project_root: Path) -> bool:
    """True when the working tree no longer matches the candidate's snapshot.

    Rebuilds the content-keyed ``document_hash`` from the current file bytes
    (same identity recipe derive used) and compares it with the hash the
    candidate event recorded. A missing file is drift by definition.
    """

    recorded = candidate.payload.get("spans")
    if not isinstance(recorded, list) or not recorded:
        raise SimlabRunError(
            f"candidate {candidate.event_hash[:12]} carries no span payloads"
        )
    first_span = recorded[0]
    if not isinstance(first_span, Mapping):
        raise SimlabRunError(
            f"candidate {candidate.event_hash[:12]} carries a non-object span payload"
        )
    recorded_hash = first_span.get("source_document_hash")
    if not isinstance(recorded_hash, str):
        raise SimlabRunError(
            f"candidate {candidate.event_hash[:12]} span payload lacks "
            "source_document_hash"
        )
    rel = _source_relpath(candidate)
    path = project_root / rel
    if not path.is_file():
        return True
    rebuilt = SourceDocument(
        tenant_id=candidate.tenant_id,
        source_id=candidate.source_id,
        document_type=DERIVE_DOCUMENT_TYPE,
        external_id=rel,
        permalink=rel,
        author_ref=DERIVE_AUTHOR_REF,
        # The document hash is content-keyed; the timestamp does not feed it.
        source_timestamp=_DOCUMENT_REBUILD_TIMESTAMP,
        content=path.read_text(encoding="utf-8"),
    )
    return rebuilt.document_hash != recorded_hash


# Placeholder timestamp for content-keyed document rebuilds; document_hash
# never reads it, but SourceDocument requires a timezone-aware value.
_DOCUMENT_REBUILD_TIMESTAMP = datetime(2026, 1, 1, tzinfo=UTC)


def _fixture_decision_from_candidate(
    candidate: CandidateRow, *, occurred_at: str
) -> FixtureDecision:
    spans_raw = candidate.payload.get("spans")
    if not isinstance(spans_raw, list) or not spans_raw:
        raise SimlabRunError(
            f"candidate {candidate.event_hash[:12]} carries no span payloads"
        )
    spans = tuple(
        FixtureSourceSpan.from_payload(item)
        for item in spans_raw
        if isinstance(item, Mapping)
    )
    if len(spans) != len(spans_raw):
        raise SimlabRunError(
            f"candidate {candidate.event_hash[:12]} carries a non-object span payload"
        )
    scopes_raw = candidate.payload.get("proposed_scopes", [])
    if not isinstance(scopes_raw, list):
        raise SimlabRunError(
            f"candidate {candidate.event_hash[:12]} carries a non-list proposed_scopes"
        )
    scopes: list[FixtureScope] = []
    for item in scopes_raw:
        if not isinstance(item, Mapping):
            raise SimlabRunError(
                f"candidate {candidate.event_hash[:12]} carries a non-object scope payload"
            )
        scopes.append(
            FixtureScope(
                scope_type=ScopeType(str(item["scope_type"])), value=str(item["value"])
            )
        )
    return FixtureDecision(
        decision_id=_decision_id_for(candidate),
        decision_text=candidate.decision_text,
        status=DecisionStatus.CANDIDATE,
        source_timestamp=occurred_at,
        spans=spans,
        scopes=tuple(scopes),
    )


@dataclass(frozen=True)
class SpanDriftSkip:
    """One decision excluded because its source document drifted post-derive."""

    decision_id: str
    decision_text: str
    source_path: str
    reason: str = SPAN_DRIFT_SKIP_REASON


@dataclass(frozen=True)
class ScenarioFixture:
    """The scenario's EvalFixture plus the named exclusions that shaped it."""

    fixture: EvalFixture
    drift_skips: tuple[SpanDriftSkip, ...]


def build_scenario_fixture(
    scenario: ScenarioSpec, repo: MaterializedRepo
) -> ScenarioFixture:
    """Build the scenario EvalFixture from the repo's local derive store.

    Reads the store through ``load_candidate_rows`` (the confirm verbs'
    decode path), applies the span-drift exclusion against the *current*
    working tree, then resolves the spec's confirm/supersede selectors and
    expected findings. Every exclusion is a named ``SpanDriftSkip``; every
    selector must resolve uniquely or the build fails.
    """

    db_path = derive_store_path(repo.root)
    if not db_path.exists():
        raise SimlabRunError(f"no derive store at {db_path}; run derive first")
    with DeriveEventStore(db_path) as store:
        rows = store.export_events()
    candidates, _statuses = load_candidate_rows(rows)
    occurred_by_hash = {
        str(row["event_hash"]): str(row["occurred_at"]) for row in rows
    }

    decisions: dict[str, FixtureDecision] = {}
    drift_skips: list[SpanDriftSkip] = []
    for candidate in candidates:
        decision = _fixture_decision_from_candidate(
            candidate, occurred_at=occurred_by_hash[candidate.event_hash]
        )
        if candidate.source_type in FILE_BACKED_SOURCE_TYPES and _document_drifted(
            candidate, project_root=repo.root
        ):
            drift_skips.append(
                SpanDriftSkip(
                    decision_id=decision.decision_id,
                    decision_text=decision.decision_text,
                    source_path=_source_relpath(candidate),
                )
            )
            continue
        decisions[decision.decision_id] = decision

    if not decisions:
        raise SimlabRunError(
            f"scenario {scenario.scenario_id!r}: no decisions survived the "
            "drift check; an empty fixture cannot exercise the review path"
        )

    # Selector-driven status transitions: the spec records what a human
    # confirmed or superseded (simlab's stand-in for the triage ritual).
    status_overrides: dict[str, tuple[DecisionStatus, str | None]] = {}
    for selector in scenario.confirm:
        decision = select_decision(decisions, selector, scenario_id=scenario.scenario_id)
        if decision.decision_id in status_overrides:
            raise SimlabRunError(
                f"scenario {scenario.scenario_id!r}: decision "
                f"{decision.decision_id} resolved by more than one status selector"
            )
        status_overrides[decision.decision_id] = (DecisionStatus.CONFIRMED, None)
    for supersede in scenario.supersede:
        old = select_decision(decisions, supersede.decision, scenario_id=scenario.scenario_id)
        new = select_decision(decisions, supersede.by, scenario_id=scenario.scenario_id)
        if old.decision_id in status_overrides:
            raise SimlabRunError(
                f"scenario {scenario.scenario_id!r}: decision "
                f"{old.decision_id} resolved by more than one status selector"
            )
        status_overrides[old.decision_id] = (DecisionStatus.SUPERSEDED, new.decision_id)

    final_decisions: list[FixtureDecision] = []
    for decision_id in sorted(decisions):
        decision = decisions[decision_id]
        override = status_overrides.get(decision_id)
        if override is not None:
            status, superseded_by = override
            decision = FixtureDecision(
                decision_id=decision.decision_id,
                decision_text=decision.decision_text,
                status=status,
                source_timestamp=decision.source_timestamp,
                spans=decision.spans,
                scopes=decision.scopes,
                superseded_by=superseded_by,
            )
        final_decisions.append(decision)

    decisions_by_id = {decision.decision_id: decision for decision in final_decisions}
    expected_findings = tuple(
        _expected_finding(scenario, spec_finding, decisions_by_id)
        for spec_finding in scenario.expected_findings
    )

    fixture = EvalFixture(
        fixture_id=scenario.scenario_id,
        diff=FixtureDiff(
            repo_owner="simlab",
            repo_name=scenario.archetype_id,
            base_sha=scenario.diff_base_sha,
            head_sha=scenario.diff_head_sha,
            patch=scenario.patch,
        ),
        decisions=tuple(final_decisions),
        expected_findings=expected_findings,
        metadata={
            "archetype_id": scenario.archetype_id,
            "scenario_id": scenario.scenario_id,
            "simlab": True,
        },
    )
    return ScenarioFixture(fixture=fixture, drift_skips=tuple(drift_skips))


def _expected_finding(
    scenario: ScenarioSpec,
    spec_finding: ExpectedFindingSpec,
    decisions_by_id: Mapping[str, FixtureDecision],
) -> ExpectedFinding:
    decision = select_decision(
        decisions_by_id, spec_finding.decision, scenario_id=scenario.scenario_id
    )
    return ExpectedFinding(
        finding_id=spec_finding.finding_id,
        finding_class=spec_finding.finding_class,
        decision_id=decision.decision_id,
        cited_span_hashes=tuple(sorted(set(decision.span_hashes))),
        summary=spec_finding.summary,
        suggested_repair=spec_finding.suggested_repair,
    )


# ---------------------------------------------------------------------------
# The scripted evaluate model — the scenario's ground truth
# ---------------------------------------------------------------------------


class ScriptedEvaluateModel:
    """Emits exactly the scenario's expected findings, pack-visibility-bound.

    For each expected finding, the script locates the decision in the
    *budgeted* candidate pack by selector; a decision the pipeline omitted
    (status filter, score floor, limit, budget) is unciteable here exactly
    as it is for a real model. Citations are the candidate's own span
    hashes — the script cannot fabricate a span the pack does not carry.
    """

    def __init__(self, scenario: ScenarioSpec, *, model_id: str = SIMLAB_MODEL_ID) -> None:
        self._scenario = scenario
        self._model_id = model_id

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        findings: list[FindingDraft] = []
        for expected in self._scenario.expected_findings:
            matches = [
                candidate
                for candidate in request.candidate_pack.candidates
                if expected.decision in candidate.decision_text
            ]
            if len(matches) > 1:
                raise SimlabRunError(
                    f"scenario {self._scenario.scenario_id!r}: selector "
                    f"{expected.decision!r} is ambiguous within the candidate pack"
                )
            if not matches:
                # The decision was omitted before the model saw anything; the
                # replay diagnostics attribute the stage.
                continue
            candidate = matches[0]
            findings.append(
                FindingDraft(
                    finding_class=expected.finding_class,
                    decision_node_id=candidate.decision_node_id,
                    cited_span_hashes=tuple(
                        sorted({span.span_hash for span in candidate.cited_spans})
                    ),
                    summary=expected.summary,
                    confidence_label="advisory",
                    suggested_repair=expected.suggested_repair,
                )
            )
        return EvaluateResult(
            findings=tuple(findings),
            model_id=self._model_id,
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
            omitted_decision_count=self._scenario.scripted_omitted_decision_count,
            degraded_reasons=self._scenario.scripted_degraded_reasons,
        )


# ---------------------------------------------------------------------------
# Scenario pipeline + verification
# ---------------------------------------------------------------------------

PlayerFactory = Callable[[ScenarioSpec, EvalFixture], EvaluateModel]


@dataclass(frozen=True)
class ScenarioRunResult:
    """Everything one scenario run produced, for verification and transcripts."""

    scenario: ScenarioSpec
    repo: MaterializedRepo
    derive: SimlabDeriveOutcome
    fixture: EvalFixture
    drift_skips: tuple[SpanDriftSkip, ...]
    replay: ReplayResult


def run_scenario(
    scenario: ScenarioSpec,
    archetype: ArchetypeSpec,
    *,
    player_factory: PlayerFactory,
    work_dir: Path,
) -> ScenarioRunResult:
    """Run one scenario end to end; see the module docstring for the stages."""

    if scenario.archetype_id != archetype.archetype_id:
        raise SimlabRunError(
            f"scenario {scenario.scenario_id!r} names archetype "
            f"{scenario.archetype_id!r} but was given {archetype.archetype_id!r}"
        )
    repo = materialize_archetype(archetype, work_dir / scenario.scenario_id)
    derive_outcome = derive_materialized(repo)

    for rel, content in sorted(scenario.post_derive_edits.items()):
        path = repo.root / rel
        if not path.is_file():
            raise SimlabRunError(
                f"scenario {scenario.scenario_id!r}: post_derive_edits names "
                f"{rel!r}, which does not exist in the materialized tree"
            )
        if path.read_text(encoding="utf-8") == content:
            raise SimlabRunError(
                f"scenario {scenario.scenario_id!r}: post_derive_edits for "
                f"{rel!r} leaves the file unchanged; a no-op edit cannot drift"
            )
        path.write_text(content, encoding="utf-8")

    scenario_fixture = build_scenario_fixture(scenario, repo)
    player = player_factory(scenario, scenario_fixture.fixture)
    replay = run_fixture(
        scenario_fixture.fixture,
        player,
        prompt_version=SIMLAB_PROMPT_VERSION,
        token_budget=scenario.token_budget,
    )
    return ScenarioRunResult(
        scenario=scenario,
        repo=repo,
        derive=derive_outcome,
        fixture=scenario_fixture.fixture,
        drift_skips=scenario_fixture.drift_skips,
        replay=replay,
    )


def verify_scenario(result: ScenarioRunResult) -> None:
    """Assert every pinned expectation; report all mismatches together."""

    scenario = result.scenario
    expected = scenario.expected
    replay = result.replay
    problems: list[str] = []

    def check(name: str, actual: object, wanted: object) -> None:
        if actual != wanted:
            problems.append(f"{name}: expected {wanted!r}, got {actual!r}")

    check("matched", replay.matched_count, expected.matched)
    check("missed", replay.missed_count, expected.missed)
    check("unexpected", replay.unexpected_count, expected.unexpected)
    check("needs_manual_review", replay.needs_manual_review, expected.needs_manual_review)
    check(
        "pack_omitted",
        dict(replay.diagnostics.pack_omitted_counts),
        dict(expected.pack_omitted),
    )
    check("over_budget", replay.budget.omitted_for_budget, expected.over_budget)
    check(
        "evaluator_omitted_decisions",
        replay.diagnostics.evaluator_reported_omitted_decisions,
        expected.evaluator_omitted_decisions,
    )

    degraded = "\n".join(replay.diagnostics.evaluator_degraded_reasons)
    for needle in expected.degraded_reasons_contain:
        if needle not in degraded:
            problems.append(
                f"degraded_reasons_contain: {needle!r} not found in "
                f"{replay.diagnostics.evaluator_degraded_reasons!r}"
            )

    expected_impossible = {
        (finding.finding_id, finding.impossible_at.value)
        for finding in scenario.expected_findings
        if finding.impossible_at is not None
    }
    actual_impossible = {
        (item.finding_id, item.omitted_at_stage.value)
        for item in replay.diagnostics.impossible_expected_findings
    }
    check("impossible_expected_findings", actual_impossible, expected_impossible)

    for outcome in replay.expected_finding_outcomes:
        impossible_ids = {finding_id for finding_id, _stage in expected_impossible}
        should_match = outcome.finding_id not in impossible_ids
        if outcome.matched != should_match:
            problems.append(
                f"finding {outcome.finding_id!r}: expected matched={should_match}, "
                f"got matched={outcome.matched} "
                f"(omitted_at_stage={outcome.omitted_at_stage})"
            )

    expected_drift = set(expected.span_drift_skips)
    matched_drift: set[str] = set()
    for skip in result.drift_skips:
        selectors = [
            selector for selector in expected_drift if selector in skip.decision_text
        ]
        if not selectors:
            problems.append(
                f"span drift skip for {skip.decision_id} ({skip.source_path}) "
                "was not declared in expected.span_drift_skips"
            )
            continue
        matched_drift.update(selectors)
    for selector in sorted(expected_drift - matched_drift):
        problems.append(
            f"expected span drift skip matching {selector!r} did not happen"
        )

    if problems:
        details = "\n  - ".join(problems)
        raise SimlabExpectationError(
            f"scenario {scenario.scenario_id!r} disagrees with its spec:\n  - {details}"
        )


# ---------------------------------------------------------------------------
# Demo-ready transcript rendering (#521: demos run on rails)
# ---------------------------------------------------------------------------


def render_transcript(result: ScenarioRunResult) -> str:
    """Render one verified scenario run as a customer-walkthrough transcript."""

    scenario = result.scenario
    replay = result.replay
    span_by_hash = {
        span.span_hash: span
        for decision in result.fixture.decisions
        for span in decision.spans
    }
    decision_by_id = {
        decision.decision_id: decision for decision in result.fixture.decisions
    }
    lines = [
        f"scenario: {scenario.scenario_id} ({scenario.archetype_id})",
        f"title: {scenario.title}",
        f"notes: {scenario.demo_notes}",
        f"repo: materialized at {result.repo.head_sha[:12]} "
        f"({result.derive.candidate_count} derived candidate(s), "
        f"{result.derive.dropped_count} dropped chatter record(s))",
        f"findings: {replay.matched_count} expected finding(s) matched, "
        f"{replay.unexpected_count} unexpected, {replay.missed_count} missed",
    ]
    for outcome in replay.expected_finding_outcomes:
        decision = decision_by_id[outcome.decision_id]
        state = "matched" if outcome.matched else (
            f"impossible (omitted at {outcome.omitted_at_stage.value})"
            if outcome.omitted_at_stage is not None
            else "missed"
        )
        lines.append(
            f"  finding {outcome.finding_id}: {outcome.finding_class.value} [{state}]"
        )
        lines.append(f"    decision: {decision.decision_text.splitlines()[0]}")
        for span_hash in outcome.cited_span_hashes:
            span = span_by_hash[span_hash]
            lines.append(f"    citation: {span.permalink} (span {span_hash[:12]})")
    for skip in result.drift_skips:
        lines.append(
            f"  skipped ({skip.reason}): {skip.source_path} drifted after derive; "
            f"decision {skip.decision_id} excluded — a drifted citation never renders"
        )
    lines.append(
        "omissions: "
        + ", ".join(
            f"{stage}={count}"
            for stage, count in sorted(replay.diagnostics.total_omitted.items())
        )
    )
    if replay.needs_manual_review:
        lines.append(
            f"needs manual review: context budget {replay.budget.token_budget} "
            f"omitted {replay.budget.omitted_for_budget} candidate(s)"
        )
    lines.append(
        f"replay-key: model={replay.model_id} prompt={replay.prompt_version} "
        f"input={replay.input_hash} snapshot={replay.graph_snapshot_hash}"
    )
    return "\n".join(lines)
