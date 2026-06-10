"""Deterministic replay runner for the Stage 0 evaluator harness (cortex#336).

Stage 0 wins or fails on measured advisory quality: evaluator changes must
be comparable across runs, and CI must never make a live model call. This
runner replays one frozen ``EvalFixture`` (cortex#332) against recorded
model responses (cortex#347) and grades the emitted findings against the
fixture's ``expected_findings`` — same fixture + same recordings in, same
bytes out (``ReplayResult.to_canonical_json()``).

**Fixture-local retrieval emulation.** The candidate pack fed to the
evaluator is built FROM THE FIXTURE's own decisions, not from hosted
Postgres: the hosted retrieval substrate is non-executing SQL strings until
cortex#472 lands the first executable path, so Stage 0 replay emulates the
shipped ``decisions_for_diff`` contract deterministically — candidates from
fixture decisions with their span material, scored by a structural-match
heuristic over the changed surface that ``diff_surface.
extract_changed_surface`` extracts from the fixture's patch. Real retrieval
replay (live hybrid RRF over Postgres) arrives when SQL executes
(cortex#472); the emulation's ``retrieval_config_version`` names itself so
results from the two regimes are never silently comparable.

**Omitted-decision diagnostics (cortex#331).** Every stage that can drop a
decision is counted under its own name and never summed away:
``status_filtered`` (non-reviewable status, mirroring the SQL status
filter), ``suppressed_below_floor`` (no structural match — retrieval would
not have returned it), ``over_limit`` (ranked past the pack bound), and
``over_budget`` (context assembly dropped it for the token budget). The
diagnostics section additionally lists which EXPECTED findings became
impossible because their decision was omitted, naming the stage — the
silent-failure detector made loud.

**Over-budget surfacing (cortex#369).** When context assembly dropped
candidates for budget, the report carries ``needs_manual_review=True``
(derived, never stored, so it cannot disagree with the arithmetic) plus the
full budget arithmetic — the master plan's manual-review signal for
over-budget PRs.

Evaluator seam: the dedicated evaluator module (``evaluator.py``,
soft-evaluator-core branch) has not merged as of this module's authoring,
so the runner speaks directly to the ``EvaluateModel`` protocol from
``model_interfaces.py`` and grades the ``FindingDraft`` outcomes it
returns. When ``evaluator.py`` lands, ``run_fixture`` should accept its
evaluator as just another ``EvaluateModel`` implementation — the seam is
the protocol, not this module.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.context_assembly import (
    OVER_BUDGET_OMISSION_KEY,
    TokenEstimator,
    assemble_evaluation_context,
    default_token_estimator,
)
from cortex.hosted.decisions_for_diff import (
    DEFAULT_DECISIONS_FOR_DIFF_LIMIT,
    MAX_DECISIONS_FOR_DIFF_LIMIT,
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)
from cortex.hosted.diff_surface import DiffSurfaceValidationError, extract_changed_surface
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    FindingClass,
    FixtureDecision,
    FixtureValidationError,
)
from cortex.hosted.model_interfaces import (
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
    ensure_result_binds_request,
)
from cortex.hosted.recorded_responses import RecordedResponseError
from cortex.hosted.routing import RecordedResponseMissingError

REPLAY_REPORT_SCHEMA_VERSION = 1

# Names the fixture-local emulation regime. Distinct from the live
# `DECISIONS_FOR_DIFF_RETRIEVAL_CONFIG_VERSION` on purpose: results produced
# under different retrieval regimes are a versioned data boundary, never
# silently comparable (real retrieval replay arrives with cortex#472).
FIXTURE_LOCAL_RETRIEVAL_CONFIG_VERSION = "fixture-local-structural-v1"

# Mirrors the `node.status = ANY(statuses)` filter in the shipped
# decisions_for_diff SQL: only these statuses reach the evaluator.
_REVIEWABLE_STATUSES = frozenset({DecisionStatus.CANDIDATE, DecisionStatus.CONFIRMED})

_UNKNOWN_DECISION_NODE_REASON = "unknown_decision_node"
_DECISION_NOT_IN_CONTEXT_REASON = "decision_not_in_evaluator_context"
_NO_MATCHING_EXPECTED_REASON = "no_matching_expected_finding"


class ReplayError(ValueError):
    """Raised when a replay run cannot proceed or report deterministically.

    The marquee failure is a missing recorded response: replay refuses to
    fall back to a live model call, naming the fixture id so the fix
    (re-record locally, commit the updated fixture) is obvious.
    """


class OmissionStage(StrEnum):
    """The named stages at which a fixture decision can be omitted.

    Each stage is counted under its own key — per cortex#331 the counts are
    never summed away, because "how many" without "where" cannot explain
    why an expected finding became impossible.
    """

    STATUS_FILTERED = "status_filtered"
    SUPPRESSED_BELOW_FLOOR = "suppressed_below_floor"
    OVER_LIMIT = "over_limit"
    OVER_BUDGET = "over_budget"


_PACK_STAGE_KEYS = (
    OmissionStage.STATUS_FILTERED.value,
    OmissionStage.SUPPRESSED_BELOW_FLOOR.value,
    OmissionStage.OVER_LIMIT.value,
)


@dataclass(frozen=True)
class FixtureRetrievalEmulation:
    """A fixture-local candidate pack plus the bookkeeping replay needs.

    ``decision_node_id_by_decision_id`` maps every fixture decision (not
    just packed ones) to its derived UUID, so scripted/recorded evaluator
    results can be authored against stable ids.
    ``omission_stage_by_decision_id`` names the stage that kept each
    omitted decision out of the pack.
    """

    pack: DecisionsForDiffCandidatePack
    decision_node_id_by_decision_id: Mapping[str, str]
    decision_id_by_node_id: Mapping[str, str]
    omission_stage_by_decision_id: Mapping[str, OmissionStage]

    def __post_init__(self) -> None:
        node_by_decision = dict(self.decision_node_id_by_decision_id)
        decision_by_node = dict(self.decision_id_by_node_id)
        stages = dict(self.omission_stage_by_decision_id)
        packed_decision_ids: set[str] = set()
        for candidate in self.pack.candidates:
            decision_id = decision_by_node.get(candidate.decision_node_id)
            if decision_id is None:
                raise ReplayError(
                    "every pack candidate must map back to a fixture decision id"
                )
            packed_decision_ids.add(decision_id)
        overlap = packed_decision_ids & set(stages)
        if overlap:
            raise ReplayError(
                f"decisions cannot be both packed and omitted: {sorted(overlap)}"
            )
        for stage in stages.values():
            if not isinstance(stage, OmissionStage):
                raise ReplayError(f"unknown omission stage: {stage!r}")
        object.__setattr__(
            self, "decision_node_id_by_decision_id", MappingProxyType(node_by_decision)
        )
        object.__setattr__(self, "decision_id_by_node_id", MappingProxyType(decision_by_node))
        object.__setattr__(self, "omission_stage_by_decision_id", MappingProxyType(stages))


@dataclass(frozen=True)
class ExpectedFindingOutcome:
    """One expected finding graded against the replayed evaluator output."""

    finding_id: str
    finding_class: FindingClass
    decision_id: str
    cited_span_hashes: tuple[str, ...]
    matched: bool
    omitted_at_stage: OmissionStage | None

    def __post_init__(self) -> None:
        _require_non_empty("finding_id", self.finding_id)
        _require_non_empty("decision_id", self.decision_id)
        if not self.cited_span_hashes:
            raise ReplayError("expected finding outcomes require cited span hashes")
        if tuple(sorted(self.cited_span_hashes)) != self.cited_span_hashes:
            raise ReplayError("cited_span_hashes must be sorted for deterministic output")
        if self.matched and self.omitted_at_stage is not None:
            raise ReplayError(
                "a finding cannot both match and have its decision omitted; "
                "the grader produced contradictory attribution"
            )

    def as_payload(self) -> dict[str, Any]:
        return {
            "cited_span_hashes": list(self.cited_span_hashes),
            "decision_id": self.decision_id,
            "finding_class": self.finding_class.value,
            "finding_id": self.finding_id,
            "matched": self.matched,
            "omitted_at_stage": None
            if self.omitted_at_stage is None
            else self.omitted_at_stage.value,
        }


@dataclass(frozen=True)
class UnexpectedEmission:
    """An emitted finding that matched no expected finding, with the reason."""

    finding_class: FindingClass
    decision_node_id: str
    decision_id: str | None
    cited_span_hashes: tuple[str, ...]
    summary: str
    reason: str

    def __post_init__(self) -> None:
        _require_non_empty("decision_node_id", self.decision_node_id)
        _require_non_empty("summary", self.summary)
        _require_non_empty("reason", self.reason)
        if tuple(sorted(self.cited_span_hashes)) != self.cited_span_hashes:
            raise ReplayError("cited_span_hashes must be sorted for deterministic output")

    def as_payload(self) -> dict[str, Any]:
        return {
            "cited_span_hashes": list(self.cited_span_hashes),
            "decision_id": self.decision_id,
            "decision_node_id": self.decision_node_id,
            "finding_class": self.finding_class.value,
            "reason": self.reason,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ImpossibleExpectedFinding:
    """An expected finding the evaluator could never emit: its decision was
    omitted before the model saw anything, at the named stage."""

    finding_id: str
    decision_id: str
    omitted_at_stage: OmissionStage

    def __post_init__(self) -> None:
        _require_non_empty("finding_id", self.finding_id)
        _require_non_empty("decision_id", self.decision_id)

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "finding_id": self.finding_id,
            "omitted_at_stage": self.omitted_at_stage.value,
        }


@dataclass(frozen=True)
class OmissionDiagnostics:
    """Per-stage omission visibility for one replayed fixture (cortex#331).

    Each stage keeps its own named count — pack omissions per stage, the
    context's budget omission, and the merged ``total_omitted`` — so a
    reader can attribute every dropped decision to exactly one stage.
    """

    pack_omitted_counts: Mapping[str, int]
    context_omitted_for_budget: int
    total_omitted: Mapping[str, int]
    impossible_expected_findings: tuple[ImpossibleExpectedFinding, ...]
    evaluator_reported_omitted_decisions: int
    evaluator_degraded_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        pack_counts = dict(self.pack_omitted_counts)
        total = dict(self.total_omitted)
        for key in _PACK_STAGE_KEYS:
            if key not in pack_counts:
                raise ReplayError(
                    f"pack_omitted_counts must carry {key!r} so per-stage "
                    "accounting stays visible even when zero"
                )
        _validate_counts("pack_omitted_counts", pack_counts)
        _validate_counts("total_omitted", total)
        if self.context_omitted_for_budget < 0:
            raise ReplayError("context_omitted_for_budget must be >= 0")
        if total.get(OVER_BUDGET_OMISSION_KEY, 0) < self.context_omitted_for_budget:
            raise ReplayError(
                f"total_omitted[{OVER_BUDGET_OMISSION_KEY!r}] must include every "
                "candidate counted in context_omitted_for_budget"
            )
        if self.evaluator_reported_omitted_decisions < 0:
            raise ReplayError("evaluator_reported_omitted_decisions must be >= 0")
        for reason in self.evaluator_degraded_reasons:
            _require_non_empty("evaluator_degraded_reasons", reason)
        object.__setattr__(self, "pack_omitted_counts", MappingProxyType(pack_counts))
        object.__setattr__(self, "total_omitted", MappingProxyType(total))

    def as_payload(self) -> dict[str, Any]:
        return {
            "context_omitted_for_budget": self.context_omitted_for_budget,
            "evaluator_degraded_reasons": list(self.evaluator_degraded_reasons),
            "evaluator_reported_omitted_decisions": self.evaluator_reported_omitted_decisions,
            "impossible_expected_findings": [
                finding.as_payload() for finding in self.impossible_expected_findings
            ],
            "pack_omitted_counts": dict(self.pack_omitted_counts),
            "total_omitted": dict(self.total_omitted),
        }


@dataclass(frozen=True)
class BudgetArithmetic:
    """The over-budget arithmetic the manual-review signal cites (cortex#369)."""

    token_budget: int
    estimated_tokens_used: int
    estimator_version: str
    included_candidate_count: int
    omitted_for_budget: int

    def __post_init__(self) -> None:
        if self.token_budget < 1:
            raise ReplayError("token_budget must be >= 1")
        if self.estimated_tokens_used < 0:
            raise ReplayError("estimated_tokens_used must be >= 0")
        if self.estimated_tokens_used > self.token_budget:
            raise ReplayError("estimated_tokens_used must not exceed token_budget")
        _require_non_empty("estimator_version", self.estimator_version)
        if self.included_candidate_count < 0:
            raise ReplayError("included_candidate_count must be >= 0")
        if self.omitted_for_budget < 0:
            raise ReplayError("omitted_for_budget must be >= 0")

    @property
    def remaining_tokens(self) -> int:
        return self.token_budget - self.estimated_tokens_used

    def as_payload(self) -> dict[str, Any]:
        return {
            "estimated_tokens_used": self.estimated_tokens_used,
            "estimator_version": self.estimator_version,
            "included_candidate_count": self.included_candidate_count,
            "omitted_for_budget": self.omitted_for_budget,
            "remaining_tokens": self.remaining_tokens,
            "token_budget": self.token_budget,
        }


@dataclass(frozen=True)
class ReplayResult:
    """The frozen, byte-deterministic outcome of replaying one fixture."""

    fixture_id: str
    fixture_hash: str
    retrieval_config_version: str
    query_hash: str
    graph_snapshot_hash: str
    context_hash: str
    input_hash: str
    model_id: str
    prompt_version: str
    expected_finding_outcomes: tuple[ExpectedFindingOutcome, ...]
    unexpected_emissions: tuple[UnexpectedEmission, ...]
    diagnostics: OmissionDiagnostics
    budget: BudgetArithmetic
    report_schema_version: int = REPLAY_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_non_empty("fixture_id", self.fixture_id)
        _require_hash("fixture_hash", self.fixture_hash)
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        _require_hash("query_hash", self.query_hash)
        _require_hash("graph_snapshot_hash", self.graph_snapshot_hash)
        _require_hash("context_hash", self.context_hash)
        _require_hash("input_hash", self.input_hash)
        _require_non_empty("model_id", self.model_id)
        _require_non_empty("prompt_version", self.prompt_version)
        _ensure_supported_version(self.report_schema_version)

    @property
    def matched_count(self) -> int:
        return sum(1 for outcome in self.expected_finding_outcomes if outcome.matched)

    @property
    def missed_count(self) -> int:
        return sum(1 for outcome in self.expected_finding_outcomes if not outcome.matched)

    @property
    def unexpected_count(self) -> int:
        return len(self.unexpected_emissions)

    @property
    def needs_manual_review(self) -> bool:
        """Manual-review signal for over-budget PRs (cortex#369).

        Derived, never stored: the flag cannot disagree with the budget
        arithmetic it summarizes.
        """

        return self.budget.omitted_for_budget > 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "budget": self.budget.as_payload(),
            "context_hash": self.context_hash,
            "diagnostics": self.diagnostics.as_payload(),
            "expected_finding_outcomes": [
                outcome.as_payload() for outcome in self.expected_finding_outcomes
            ],
            "fixture_hash": self.fixture_hash,
            "fixture_id": self.fixture_id,
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "input_hash": self.input_hash,
            "matched_count": self.matched_count,
            "missed_count": self.missed_count,
            "model_id": self.model_id,
            "needs_manual_review": self.needs_manual_review,
            "prompt_version": self.prompt_version,
            "query_hash": self.query_hash,
            "replay_report_schema_version": self.report_schema_version,
            "retrieval_config_version": self.retrieval_config_version,
            "unexpected_count": self.unexpected_count,
            "unexpected_emissions": [
                emission.as_payload() for emission in self.unexpected_emissions
            ],
        }

    def to_canonical_json(self) -> str:
        """Serialize deterministically; identical replays are identical bytes."""

        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n"
        )


@dataclass(frozen=True)
class CorpusReplayReport:
    """A frozen batch report over a corpus of replayed fixtures."""

    results: tuple[ReplayResult, ...]
    report_schema_version: int = REPLAY_REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _ensure_supported_version(self.report_schema_version)
        fixture_ids = [result.fixture_id for result in self.results]
        if len(set(fixture_ids)) != len(fixture_ids):
            raise ReplayError(
                "corpus reports require unique fixture ids; duplicates would "
                "make aggregate counts unattributable"
            )
        object.__setattr__(
            self,
            "results",
            tuple(sorted(self.results, key=lambda result: result.fixture_id)),
        )

    @property
    def fixtures_run(self) -> int:
        return len(self.results)

    @property
    def matched_total(self) -> int:
        return sum(result.matched_count for result in self.results)

    @property
    def missed_total(self) -> int:
        return sum(result.missed_count for result in self.results)

    @property
    def unexpected_total(self) -> int:
        return sum(result.unexpected_count for result in self.results)

    @property
    def needs_manual_review_count(self) -> int:
        return sum(1 for result in self.results if result.needs_manual_review)

    def as_payload(self) -> dict[str, Any]:
        return {
            "fixtures_run": self.fixtures_run,
            "matched_total": self.matched_total,
            "missed_total": self.missed_total,
            "needs_manual_review_count": self.needs_manual_review_count,
            "replay_report_schema_version": self.report_schema_version,
            "results": [result.as_payload() for result in self.results],
            "unexpected_total": self.unexpected_total,
        }

    def to_canonical_json(self) -> str:
        """Serialize deterministically; identical corpus runs are identical bytes."""

        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n"
        )


def ensure_replay_report_payload_version(payload: Mapping[str, Any]) -> None:
    """Version-gate a serialized replay report; unknown versions fail visibly."""

    if not isinstance(payload, Mapping):
        raise ReplayError("replay report payload must be a JSON object")
    raw_version = payload.get("replay_report_schema_version")
    if not isinstance(raw_version, int) or isinstance(raw_version, bool):
        raise ReplayError(
            "replay_report_schema_version must be an integer; refusing to guess"
        )
    _ensure_supported_version(raw_version)


# ---------------------------------------------------------------------------
# Fixture-local retrieval emulation
# ---------------------------------------------------------------------------


def build_fixture_candidate_pack(
    fixture: EvalFixture,
    *,
    limit: int = DEFAULT_DECISIONS_FOR_DIFF_LIMIT,
    score_floor: float = 0.0,
) -> FixtureRetrievalEmulation:
    """Build the evaluator candidate pack from the fixture's own decisions.

    FIXTURE-LOCAL RETRIEVAL EMULATION: the hosted substrate is
    non-executing (SQL strings only) until cortex#472 lands the first
    executable Postgres path, so Stage 0 replay emulates the shipped
    ``decisions_for_diff`` contract deterministically:

    1. Decisions whose status is not reviewable (mirroring the SQL
       ``statuses`` filter) are omitted at stage ``status_filtered``.
    2. Each remaining decision is scored by structural match: the sum of
       structural scope weights for every decision scope whose normalized
       ``(scope_type, value)`` appears in the changed surface extracted from
       the fixture's patch via ``diff_surface.extract_changed_surface``.
    3. Decisions scoring at or below ``score_floor`` are omitted at stage
       ``suppressed_below_floor`` — structurally unmatched decisions are
       exactly what live retrieval would not have returned.
    4. Survivors rank by (score desc, decision_id asc — the deterministic
       tiebreak) and the pack keeps the top ``limit``; the rest are omitted
       at stage ``over_limit``.

    Every omission is counted under its stage name in the pack's
    ``omitted_counts`` and attributed per decision in
    ``omission_stage_by_decision_id`` (cortex#331).
    """

    if score_floor < 0:
        raise ReplayError("score_floor must be >= 0")
    if not 1 <= limit <= MAX_DECISIONS_FOR_DIFF_LIMIT:
        # Mirrors the DecisionsForDiffQuery bound: the emulation must not be
        # able to feed the evaluator a wider pack than live retrieval may.
        raise ReplayError(f"limit must be between 1 and {MAX_DECISIONS_FOR_DIFF_LIMIT}")
    try:
        surface = extract_changed_surface(fixture.diff.patch)
    except DiffSurfaceValidationError as exc:
        raise ReplayError(
            f"fixture {fixture.fixture_id!r}: diff patch cannot be parsed into a "
            f"changed surface: {exc}"
        ) from exc
    surface_index = {
        (scope.scope_type, scope.normalized_value): scope
        for scope in surface.query_scopes()
    }

    node_by_decision: dict[str, str] = {}
    omission_stage: dict[str, OmissionStage] = {}
    scored: list[tuple[float, FixtureDecision, tuple[str, ...]]] = []
    eligible_count = 0
    for decision in fixture.decisions:
        node_by_decision[decision.decision_id] = _fixture_uuid(
            "decision-node", decision.decision_id
        )
        if decision.status not in _REVIEWABLE_STATUSES:
            omission_stage[decision.decision_id] = OmissionStage.STATUS_FILTERED
            continue
        eligible_count += 1
        matched = [
            surface_index[key]
            for scope in decision.scopes
            if (key := (scope.scope_type, scope.normalized_value)) in surface_index
        ]
        score = float(sum(scope.structural_weight for scope in matched))
        if score <= score_floor:
            omission_stage[decision.decision_id] = OmissionStage.SUPPRESSED_BELOW_FLOOR
            continue
        reason_codes = tuple(sorted({scope.reason_code for scope in matched}))
        scored.append((score, decision, reason_codes))

    scored.sort(key=lambda item: (-item[0], item[1].decision_id))
    pool_size = len(scored)
    packed = scored[:limit]
    for _, decision, _ in scored[limit:]:
        omission_stage[decision.decision_id] = OmissionStage.OVER_LIMIT

    candidates = tuple(
        _candidate_from_decision(decision, score=score, reason_codes=reason_codes)
        for score, decision, reason_codes in packed
    )
    pack = DecisionsForDiffCandidatePack(
        query_hash=_fixture_query_hash(fixture, limit=limit, score_floor=score_floor),
        retrieval_config_version=FIXTURE_LOCAL_RETRIEVAL_CONFIG_VERSION,
        graph_snapshot_hash=_fixture_graph_snapshot_hash(fixture),
        candidates=candidates,
        omitted_counts={
            OmissionStage.STATUS_FILTERED.value: len(fixture.decisions) - eligible_count,
            OmissionStage.SUPPRESSED_BELOW_FLOOR.value: eligible_count - pool_size,
            OmissionStage.OVER_LIMIT.value: pool_size - len(packed),
        },
        graph_node_count=eligible_count,
        candidate_pool_size=pool_size,
    )
    return FixtureRetrievalEmulation(
        pack=pack,
        decision_node_id_by_decision_id=node_by_decision,
        decision_id_by_node_id={node: dec for dec, node in node_by_decision.items()},
        omission_stage_by_decision_id=omission_stage,
    )


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def run_fixture(
    fixture: EvalFixture,
    player: EvaluateModel,
    *,
    prompt_version: str,
    token_budget: int,
    limit: int = DEFAULT_DECISIONS_FOR_DIFF_LIMIT,
    score_floor: float = 0.0,
    estimator: TokenEstimator = default_token_estimator,
) -> ReplayResult:
    """Replay one fixture against a recorded (or scripted) evaluate model.

    ``player`` is any ``EvaluateModel`` — in CI it is a
    ``RecordedResponsePlayer`` (cortex#347), which can never fall back to a
    live call. Two runs over the same fixture, same recordings, and same
    parameters produce byte-identical ``to_canonical_json()`` output.
    """

    emulation = build_fixture_candidate_pack(fixture, limit=limit, score_floor=score_floor)
    context = assemble_evaluation_context(
        emulation.pack, token_budget=token_budget, estimator=estimator
    )

    omission_stage = dict(emulation.omission_stage_by_decision_id)
    included_node_ids = {candidate.decision_node_id for candidate in context.candidates}
    for candidate in emulation.pack.candidates:
        if candidate.decision_node_id not in included_node_ids:
            decision_id = emulation.decision_id_by_node_id[candidate.decision_node_id]
            omission_stage[decision_id] = OmissionStage.OVER_BUDGET

    # The evaluator must see exactly the budgeted material: rebuild the pack
    # from the context's included candidates so the request's input_hash (and
    # therefore the recording key) binds what the model actually saw.
    budgeted_pack = DecisionsForDiffCandidatePack(
        query_hash=emulation.pack.query_hash,
        retrieval_config_version=emulation.pack.retrieval_config_version,
        graph_snapshot_hash=emulation.pack.graph_snapshot_hash,
        candidates=context.candidates,
        omitted_counts=dict(context.total_omitted),
        graph_node_count=emulation.pack.graph_node_count,
        candidate_pool_size=emulation.pack.candidate_pool_size,
    )
    request = EvaluateRequest(
        candidate_pack=budgeted_pack,
        diff_patch=fixture.diff.patch,
        prompt_version=prompt_version,
    )
    try:
        result = player.evaluate(request)
    except (RecordedResponseError, RecordedResponseMissingError) as exc:
        raise ReplayError(
            f"fixture {fixture.fixture_id!r}: no usable recorded evaluate response "
            f"for input_hash {request.input_hash}; replay never falls back to a "
            f"live model call ({exc})"
        ) from exc
    # The recorded player binds results itself; arbitrary EvaluateModel
    # implementations may not, so the runner re-checks before grading.
    ensure_result_binds_request(request, result)

    visible_decision_ids = {
        emulation.decision_id_by_node_id[node_id] for node_id in included_node_ids
    }
    outcomes, unexpected = _grade_findings(
        fixture,
        result,
        decision_id_by_node_id=emulation.decision_id_by_node_id,
        visible_decision_ids=visible_decision_ids,
        omission_stage=omission_stage,
    )
    impossible = tuple(
        ImpossibleExpectedFinding(
            finding_id=outcome.finding_id,
            decision_id=outcome.decision_id,
            omitted_at_stage=outcome.omitted_at_stage,
        )
        for outcome in outcomes
        if outcome.omitted_at_stage is not None
    )
    diagnostics = OmissionDiagnostics(
        pack_omitted_counts=dict(emulation.pack.omitted_counts),
        context_omitted_for_budget=context.omitted_for_budget,
        total_omitted=dict(context.total_omitted),
        impossible_expected_findings=impossible,
        evaluator_reported_omitted_decisions=result.omitted_decision_count,
        evaluator_degraded_reasons=result.degraded_reasons,
    )
    budget = BudgetArithmetic(
        token_budget=context.token_budget,
        estimated_tokens_used=context.estimated_tokens_used,
        estimator_version=context.estimator_version,
        included_candidate_count=len(context.candidates),
        omitted_for_budget=context.omitted_for_budget,
    )
    return ReplayResult(
        fixture_id=fixture.fixture_id,
        fixture_hash=fixture.fixture_hash,
        retrieval_config_version=emulation.pack.retrieval_config_version,
        query_hash=emulation.pack.query_hash,
        graph_snapshot_hash=emulation.pack.graph_snapshot_hash,
        context_hash=context.context_hash,
        input_hash=request.input_hash,
        model_id=result.model_id,
        prompt_version=result.prompt_version,
        expected_finding_outcomes=outcomes,
        unexpected_emissions=unexpected,
        diagnostics=diagnostics,
        budget=budget,
    )


def run_corpus_directory(
    corpus_dir: Path | str,
    player: EvaluateModel,
    *,
    prompt_version: str,
    token_budget: int,
    limit: int = DEFAULT_DECISIONS_FOR_DIFF_LIMIT,
    score_floor: float = 0.0,
    estimator: TokenEstimator = default_token_estimator,
) -> CorpusReplayReport:
    """Replay every ``*.json`` fixture in a corpus directory.

    Fixture files load in sorted filename order; the report sorts results by
    fixture id, so two runs over the same corpus are byte-identical. An
    empty or missing corpus fails visibly — a zero-fixture replay reporting
    success would be the silent-failure shape this harness exists to kill.
    """

    directory = Path(corpus_dir)
    if not directory.is_dir():
        raise ReplayError(f"corpus directory does not exist: {directory}")
    fixture_paths = sorted(directory.glob("*.json"))
    if not fixture_paths:
        raise ReplayError(
            f"corpus directory contains no *.json fixtures: {directory}; "
            "an empty replay cannot stand in for a passing one"
        )
    results: list[ReplayResult] = []
    seen_fixture_ids: set[str] = set()
    for path in fixture_paths:
        try:
            fixture = EvalFixture.from_json(path.read_text(encoding="utf-8"))
        except FixtureValidationError as exc:
            raise ReplayError(f"fixture file {path} is invalid: {exc}") from exc
        if fixture.fixture_id in seen_fixture_ids:
            raise ReplayError(
                f"fixture file {path} duplicates fixture_id {fixture.fixture_id!r}"
            )
        seen_fixture_ids.add(fixture.fixture_id)
        results.append(
            run_fixture(
                fixture,
                player,
                prompt_version=prompt_version,
                token_budget=token_budget,
                limit=limit,
                score_floor=score_floor,
                estimator=estimator,
            )
        )
    return CorpusReplayReport(results=tuple(results))


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _grade_findings(
    fixture: EvalFixture,
    result: EvaluateResult,
    *,
    decision_id_by_node_id: Mapping[str, str],
    visible_decision_ids: set[str],
    omission_stage: Mapping[str, OmissionStage],
) -> tuple[tuple[ExpectedFindingOutcome, ...], tuple[UnexpectedEmission, ...]]:
    """Match emissions to expected findings on (class, decision, span set).

    Matching is one-to-one and order-deterministic: expected findings are
    walked in fixture order, emissions in result order, and each emission
    satisfies at most one expectation. Emissions naming decisions the
    evaluator never saw cannot match — an evaluator credited for a finding
    about material it was not shown would hide the omission the
    diagnostics exist to surface.
    """

    consumed = [False] * len(result.findings)
    emission_keys: list[tuple[FindingClass, str, tuple[str, ...]] | None] = []
    for finding in result.findings:
        decision_id = decision_id_by_node_id.get(finding.decision_node_id)
        if decision_id is None or decision_id not in visible_decision_ids:
            emission_keys.append(None)
            continue
        emission_keys.append(
            (
                finding.finding_class,
                decision_id,
                tuple(sorted(set(finding.cited_span_hashes))),
            )
        )

    outcomes: list[ExpectedFindingOutcome] = []
    for expected in fixture.expected_findings:
        stage = omission_stage.get(expected.decision_id)
        expected_key = (
            expected.finding_class,
            expected.decision_id,
            tuple(sorted(set(expected.cited_span_hashes))),
        )
        matched_index: int | None = None
        if stage is None:
            for index, key in enumerate(emission_keys):
                if not consumed[index] and key == expected_key:
                    matched_index = index
                    break
        if matched_index is not None:
            consumed[matched_index] = True
        outcomes.append(
            ExpectedFindingOutcome(
                finding_id=expected.finding_id,
                finding_class=expected.finding_class,
                decision_id=expected.decision_id,
                cited_span_hashes=tuple(sorted(set(expected.cited_span_hashes))),
                matched=matched_index is not None,
                omitted_at_stage=stage,
            )
        )

    unexpected: list[UnexpectedEmission] = []
    for index, finding in enumerate(result.findings):
        if consumed[index]:
            continue
        decision_id = decision_id_by_node_id.get(finding.decision_node_id)
        if decision_id is None:
            reason = _UNKNOWN_DECISION_NODE_REASON
        elif decision_id not in visible_decision_ids:
            reason = _DECISION_NOT_IN_CONTEXT_REASON
        else:
            reason = _NO_MATCHING_EXPECTED_REASON
        unexpected.append(
            UnexpectedEmission(
                finding_class=finding.finding_class,
                decision_node_id=finding.decision_node_id,
                decision_id=decision_id,
                cited_span_hashes=tuple(sorted(set(finding.cited_span_hashes))),
                summary=finding.summary,
                reason=reason,
            )
        )
    return tuple(outcomes), tuple(unexpected)


# ---------------------------------------------------------------------------
# Deterministic derivation helpers
# ---------------------------------------------------------------------------


def _candidate_from_decision(
    decision: FixtureDecision, *, score: float, reason_codes: tuple[str, ...]
) -> DecisionsForDiffCandidate:
    return DecisionsForDiffCandidate(
        decision_node_id=_fixture_uuid("decision-node", decision.decision_id),
        decision_version_id=_fixture_uuid("decision-version", decision.decision_id),
        status=decision.status.value,
        decision_text=decision.decision_text,
        score=score,
        reason_codes=reason_codes,
        cited_spans=tuple(
            CitedSourceSpan(
                span_hash=span.span_hash,
                excerpt=span.excerpt,
                permalink=span.permalink,
                source_document_id=_fixture_uuid(
                    "source-document", span.source_document_hash
                ),
                source_id=_fixture_uuid("source", span.source_document_hash),
            )
            for span in decision.spans
        ),
    )


def _fixture_uuid(namespace: str, value: str) -> str:
    """Derive a stable UUID for fixture material.

    The hosted candidate shapes require UUID identifiers (they mirror
    Postgres rows); fixtures carry kebab-case ids and document hashes.
    Hash-derived UUIDs keep the emulation deterministic so recordings keyed
    by input_hash stay valid across runs. Real ids arrive with cortex#472.
    """

    digest = hashlib.sha256(f"cortex-fixture-{namespace}:{value}".encode()).hexdigest()
    return str(uuid.UUID(hex=digest[:32]))


def _fixture_query_hash(fixture: EvalFixture, *, limit: int, score_floor: float) -> str:
    return _hash_mapping(
        {
            "diff": fixture.diff.as_payload(),
            "fixture_id": fixture.fixture_id,
            "limit": limit,
            "retrieval_config_version": FIXTURE_LOCAL_RETRIEVAL_CONFIG_VERSION,
            "score_floor": score_floor,
        }
    )


def _fixture_graph_snapshot_hash(fixture: EvalFixture) -> str:
    """The fixture's decision set *is* the graph in fixture-local replay."""

    return _hash_mapping(
        {"decisions": [decision.as_payload() for decision in fixture.decisions]}
    )


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _ensure_supported_version(version: int) -> None:
    if version != REPLAY_REPORT_SCHEMA_VERSION:
        raise ReplayError(
            f"unknown replay_report_schema_version {version!r}; this runner "
            f"supports only {REPLAY_REPORT_SCHEMA_VERSION} — no silent fallback "
            "for unrecognized report versions"
        )


def _validate_counts(name: str, counts: Mapping[str, int]) -> None:
    for key, value in counts.items():
        if not isinstance(key, str) or not key.strip():
            raise ReplayError(f"{name} keys must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReplayError(f"{name}[{key!r}] must be a non-negative int")


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReplayError(f"{name} must be a non-empty string")


def _require_hash(name: str, value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        ch not in "0123456789abcdef" for ch in value
    ):
        raise ReplayError(f"{name} must be a sha256 hex string")


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
