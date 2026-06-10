"""simlab scenario regression suite (cortex#521).

One command — ``pytest tests/simlab/`` — replays every committed scenario
end to end (materialize → derive → local store → fixture-pack review path)
against the committed recorded responses. Zero live model calls: the player
is ``RecordedResponsePlayer`` and a recording miss is a hard failure. The
committed recording is drift-guarded byte-for-byte against this suite's own
generator, so the recordings and the pipeline can never quietly disagree.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    FindingClass,
    FixtureDecision,
    FixtureSourceSpan,
)
from cortex.hosted.model_interfaces import EvaluateModel
from cortex.hosted.recorded_responses import (
    RecordedResponseError,
    RecordedResponsePlayer,
    RecordedResponseStore,
)
from cortex.hosted.replay_runner import OmissionStage, ReplayError, ReplayResult
from tests.simlab.recordings import (
    RECORDINGS_PATH,
    generate_recordings_json,
    load_recorded_player,
)
from tests.simlab.runner import (
    SIMLAB_PROMPT_VERSION,
    ScenarioRunResult,
    SimlabRunError,
    render_transcript,
    run_scenario,
    select_decision,
    verify_scenario,
)
from tests.simlab.specs import (
    SHIPPED_ARCHETYPE_IDS,
    ArchetypeSpec,
    ScenarioSpec,
    load_archetype_specs,
    load_scenario_specs,
)

MINIMUM_SCENARIO_COUNT = 10

_SCENARIOS = load_scenario_specs()
_SCENARIO_IDS = [spec.scenario_id for spec in _SCENARIOS]


@pytest.fixture(scope="module")
def archetypes() -> dict[str, ArchetypeSpec]:
    return load_archetype_specs()


@pytest.fixture(scope="module")
def scenarios_by_id() -> dict[str, ScenarioSpec]:
    return {spec.scenario_id: spec for spec in _SCENARIOS}


@pytest.fixture(scope="module")
def recorded_player() -> RecordedResponsePlayer:
    return load_recorded_player()


@pytest.fixture(scope="module")
def replayed_results(
    archetypes: dict[str, ArchetypeSpec],
    recorded_player: RecordedResponsePlayer,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, ScenarioRunResult]:
    """Replay every scenario once against the committed recordings."""

    base = tmp_path_factory.mktemp("simlab-replay")

    def player_factory(scenario: ScenarioSpec, fixture: EvalFixture) -> EvaluateModel:
        _ = scenario, fixture
        return recorded_player

    return {
        scenario.scenario_id: run_scenario(
            scenario,
            archetypes[scenario.archetype_id],
            player_factory=player_factory,
            work_dir=base,
        )
        for scenario in _SCENARIOS
    }


# ---------------------------------------------------------------------------
# Drift guard: the committed recording is regenerable byte-for-byte
# ---------------------------------------------------------------------------


def test_committed_recordings_match_generator(tmp_path: Path) -> None:
    assert RECORDINGS_PATH.is_file(), (
        f"missing committed recording {RECORDINGS_PATH}; regenerate it with "
        "`uv run python -m tests.simlab.recordings`"
    )
    committed = RECORDINGS_PATH.read_text(encoding="utf-8")
    assert committed == generate_recordings_json(tmp_path), (
        "committed simlab recordings drifted from the generator; re-record "
        "with `uv run python -m tests.simlab.recordings` and commit the result"
    )


# ---------------------------------------------------------------------------
# The >=10 scenarios, replayed end to end with zero live calls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_scenario_replays_offline_and_matches_its_spec(
    scenario_id: str, replayed_results: dict[str, ScenarioRunResult]
) -> None:
    result = replayed_results[scenario_id]
    verify_scenario(result)
    # Replay identity: the report stamps the simlab contract, never a live model.
    assert result.replay.prompt_version == SIMLAB_PROMPT_VERSION
    assert result.replay.model_id == "simlab/scripted-evaluate"


def test_corpus_size_and_archetype_coverage(
    scenarios_by_id: dict[str, ScenarioSpec],
) -> None:
    assert len(scenarios_by_id) >= MINIMUM_SCENARIO_COUNT
    for archetype_id in SHIPPED_ARCHETYPE_IDS:
        per_archetype = [
            spec
            for spec in scenarios_by_id.values()
            if spec.archetype_id == archetype_id
        ]
        assert len(per_archetype) >= 3, archetype_id
        # Every archetype carries at least one true positive and one true
        # negative (the no-spam bar is per archetype, not global).
        assert any(spec.expected.matched > 0 for spec in per_archetype), archetype_id
        assert any(
            spec.expected.matched == 0
            and spec.expected.unexpected == 0
            and not spec.expected_findings
            for spec in per_archetype
        ), archetype_id


def test_corpus_covers_the_named_edge_cases(
    scenarios_by_id: dict[str, ScenarioSpec],
) -> None:
    """#521 names the edges the invariants exist for; each must be present."""

    specs = list(scenarios_by_id.values())
    assert any(spec.expected.over_budget > 0 for spec in specs), "over-budget"
    assert any(
        spec.expected.pack_omitted.get(OmissionStage.STATUS_FILTERED.value, 0) > 0
        for spec in specs
    ), "status-filtered (superseded chain)"
    assert any(spec.expected.span_drift_skips for spec in specs), "span drift"
    assert any(
        spec.expected.degraded_reasons_contain for spec in specs
    ), "unconfirmed-twin disclosure"
    assert any(
        spec.finding_class is FindingClass.REVERSES_SUPERSEDED_PATTERN
        for spec_item in specs
        for spec in spec_item.expected_findings
    ), "reverses-superseded true positive"
    assert any(
        spec.finding_class is FindingClass.CITES_MISSING_PATH
        for spec_item in specs
        for spec in spec_item.expected_findings
    ), "cites-missing-path (stale anchor)"


def test_empty_pack_scenario_is_honestly_empty(
    replayed_results: dict[str, ScenarioRunResult],
) -> None:
    """The empty-pack edge: zero candidates reach the evaluator, zero findings
    come back, and every omission is attributed to a stage."""

    result = replayed_results["clean-shop-unrelated-docs"]
    assert result.replay.budget.included_candidate_count == 0
    assert result.replay.matched_count == 0
    assert result.replay.unexpected_count == 0
    omitted = dict(result.replay.diagnostics.pack_omitted_counts)
    assert sum(omitted.values()) == len(result.fixture.decisions)


def test_over_budget_scenario_flags_manual_review(
    replayed_results: dict[str, ScenarioRunResult],
) -> None:
    result = replayed_results["clean-shop-over-budget"]
    assert result.replay.needs_manual_review
    assert result.replay.budget.omitted_for_budget == 2
    impossible = result.replay.diagnostics.impossible_expected_findings
    assert [(item.finding_id, item.omitted_at_stage.value) for item in impossible] == [
        ("f-omitted", "over_budget")
    ]


def test_span_drift_scenario_excludes_the_drifted_decision(
    replayed_results: dict[str, ScenarioRunResult],
) -> None:
    result = replayed_results["legacy-migration-span-drift"]
    assert len(result.drift_skips) == 1
    skip = result.drift_skips[0]
    assert skip.source_path == "docs/adr/0004-postgres-ledger-store.md"
    assert skip.reason == "span_drift"
    drifted_texts = [
        decision.decision_text
        for decision in result.fixture.decisions
        if "Postgres as the ledger store" in decision.decision_text
    ]
    assert drifted_texts == [], "the drifted decision must not reach the fixture"


# ---------------------------------------------------------------------------
# Replay discipline: a recording miss is a hard failure, never a live call
# ---------------------------------------------------------------------------


def test_replay_miss_fails_loudly_instead_of_passing(
    archetypes: dict[str, ArchetypeSpec], tmp_path: Path
) -> None:
    scenario = next(
        spec for spec in _SCENARIOS if spec.scenario_id == "clean-shop-retry-fixed-delay"
    )
    empty_player = RecordedResponsePlayer(
        RecordedResponseStore(), fixture_path=tmp_path / "empty.json"
    )

    def player_factory(sc: ScenarioSpec, fx: EvalFixture) -> EvaluateModel:
        _ = sc, fx
        return empty_player

    with pytest.raises((ReplayError, RecordedResponseError), match="input_hash"):
        run_scenario(
            scenario,
            archetypes[scenario.archetype_id],
            player_factory=player_factory,
            work_dir=tmp_path / "repos",
        )


def test_selector_resolution_fails_closed_on_ambiguity() -> None:
    def decision(decision_id: str, text: str) -> FixtureDecision:
        return FixtureDecision(
            decision_id=decision_id,
            decision_text=text,
            status=DecisionStatus.CANDIDATE,
            source_timestamp="2026-06-01T00:00:00+00:00",
            spans=(
                FixtureSourceSpan(
                    source_document_hash=hashlib.sha256(text.encode()).hexdigest(),
                    start_offset=0,
                    end_offset=len(text),
                    excerpt=text,
                    permalink="doc.md",
                ),
            ),
        )

    decisions = {
        "d-one": decision("d-one", "retries use backoff"),
        "d-two": decision("d-two", "retries use backoff with jitter"),
    }
    with pytest.raises(SimlabRunError, match="ambiguous"):
        select_decision(decisions, "retries use backoff", scenario_id="unit")
    with pytest.raises(SimlabRunError, match="matches no derived decision"):
        select_decision(decisions, "no such text", scenario_id="unit")


# ---------------------------------------------------------------------------
# Demo rails: every verified scenario renders a demo-ready transcript
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_transcripts_render_demo_ready(
    scenario_id: str, replayed_results: dict[str, ScenarioRunResult]
) -> None:
    result = replayed_results[scenario_id]
    transcript = render_transcript(result)
    assert f"scenario: {scenario_id}" in transcript
    assert "replay-key: model=simlab/scripted-evaluate" in transcript
    assert "omissions:" in transcript
    for outcome in result.replay.expected_finding_outcomes:
        assert outcome.finding_id in transcript
    if result.drift_skips:
        assert "a drifted citation never renders" in transcript
    if result.replay.needs_manual_review:
        assert "needs manual review" in transcript


def test_marquee_transcript_carries_the_citation(
    replayed_results: dict[str, ScenarioRunResult],
) -> None:
    transcript = render_transcript(replayed_results["clean-shop-retry-fixed-delay"])
    assert "contradicts-prior-decision [matched]" in transcript
    assert "citation: CLAUDE.md (span " in transcript
    assert "**Retry policy.**" in transcript


def test_replay_results_are_the_shipped_report_shape(
    replayed_results: dict[str, ScenarioRunResult],
) -> None:
    """Guardrail: the runner replays through the shipped fixture-pack path —
    every scenario's report is the frozen ``ReplayResult`` shape, not a
    simlab-local reimplementation."""

    for result in replayed_results.values():
        assert isinstance(result.replay, ReplayResult)
        # v2: glob-granularity structural matching (cortex#484).
        assert result.replay.retrieval_config_version == "fixture-local-structural-v2"
