"""Soft evaluator core for the hosted decision reviewer (cortex#370).

``evaluate_diff`` is the one orchestration path from a retrieval candidate
pack to advisory findings: assemble the token-budgeted evaluation context
(cortex#330), build the bounded ``EvaluateRequest``, call the cortex#344
``EvaluateModel`` boundary (routing's adapters satisfy it), validate every
proposed finding, and return a frozen :class:`EvaluationOutcome` carrying
the emitted findings, every visible drop, the replay key material, and one
``finding.emitted`` :class:`~cortex.hosted.ledger_events.LedgerEvent` draft
per emitted finding.

Four sibling issue families live here:

- **cortex#371 / #372 — finding-class evidence.** The
  :data:`STAGE0_FINDING_CLASS_REGISTRY` maps each Stage 0 finding class to
  the pack evidence it requires. ``contradicts-prior-decision`` requires the
  contradicted decision present in the pack with status ``confirmed``;
  ``reverses-superseded-pattern`` requires the cited decision's status to be
  ``superseded`` *and* the superseding decision present in the pack
  (recognized by the ``graph:supersedes`` retrieval reason code). Findings
  whose class evidence fails are rejected, reason-coded, and counted — never
  emitted. The registry is extensible: pass a wider mapping to
  ``evaluate_diff`` once a new class's evidence contract is reviewed.

  Note the fail-closed consequence: ``decisions_for_diff`` retrieval
  currently ships only ``candidate``/``confirmed`` candidates, so the
  ``superseded`` status requirement cannot be satisfied until retrieval
  ships superseded candidates — every ``reverses-superseded-pattern``
  finding is rejected visibly until then, by design.

- **cortex#373 / #374 — the shadow lanes.** ``cites-missing-path`` and
  ``omitted-load-bearing-constraint`` are registered with ``shadow=True``:
  their findings pass the same citation gate, class-evidence checks, and
  confidence-label vocabulary as the live classes, but validated findings
  are *captured* in :attr:`EvaluationOutcome.shadow_findings` — never
  emitted, never assessed by the advisory ladder, never drafted into ledger
  events, and never rendered as advisory — until their measured precision
  clears the graduation bar. Shadow capture is visible by construction:
  the outcome counts shadow findings per class, and a shadow-only run is an
  explicit ``no_findings`` result. ``cites-missing-path`` additionally
  requires the missing path to be named in the summary;
  ``omitted-load-bearing-constraint`` requires the summary to name a
  decision id that context assembly actually omitted for budget plus the
  omission stage (only budget omissions carry knowable ids — retrieval-stage
  omissions arrive as counts, so claims about them are unverifiable and are
  rejected).

- **cortex#375 — the advisory ladder.** Emission behavior and the
  suppression floor come from
  :class:`~cortex.hosted.advisory_ladder.AdvisoryLadder`; below-floor
  findings are counted in ``suppressed_below_floor``, and blocking is
  unrepresentable while ``BLOCKING_ENABLED`` is ``False``.

- **cortex#377 — runtime fail-closed citation enforcement.** A finding
  whose decision reference does not resolve to a pack candidate, or whose
  cited span hashes are not present in the pack's span material, is
  rejected with :class:`UncitedFindingError` and a visible
  ``DegradationReport`` — mirroring ``ask_ledger.build_cited_context_pack``
  flipping to ``AnswerState.NO_ANSWER`` instead of answering uncited. Span
  material is reused from the pack (``CitedSourceSpan`` rows produced by
  ``provenance.SourceSpan`` hashing); no second span-validation
  implementation exists here. A rejected finding never reaches the
  ``FINDING_EMITTED`` ledger insert path — the write-time invariant in
  ``ledger_events.py`` is the backstop, not the catcher.

The prompt-side contract for the Stage 0 classes is documented by
:func:`evaluate_prompt_guidance`: the registered evaluate prompt template
(``model_registry``) must ask for exactly the registered classes, and the
stamped ``prompt_version`` identifies that contract for replay.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from cortex.hosted.advisory_ladder import (
    BLOCKING_ENABLED,
    CONFIDENCE_LABEL_VOCABULARY,
    TIER_EMISSION_BEHAVIOR,
    AdvisoryLadder,
    AdvisoryLadderError,
    EmissionBehavior,
)
from cortex.hosted.confidence import ConfidenceTier
from cortex.hosted.context_assembly import (
    OVER_BUDGET_OMISSION_KEY,
    TokenEstimator,
    assemble_evaluation_context,
    default_token_estimator,
)
from cortex.hosted.cost import RunLedger
from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)
from cortex.hosted.eval_fixtures import DecisionStatus, FindingClass
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)
from cortex.hosted.model_interfaces import (
    EvaluateModel,
    EvaluateRequest,
    FindingDraft,
    ensure_result_binds_request,
)

if TYPE_CHECKING:
    from cortex.hosted.degradation import DegradationReport

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

EVALUATOR_SOURCE = "cortex.hosted.evaluator"

# The retrieval reason code marking a candidate that entered the pack through
# a `supersedes` graph edge (decisions_for_diff emits 'graph:<edge_type>').
GRAPH_SUPERSEDES_REASON_CODE = "graph:supersedes"

# Rejection reason codes — the evaluator slice of the cortex#329 taxonomy's
# reason-code vocabulary. Stable strings: they appear in DegradationReports,
# rejection counts, and replay reports.
REASON_DECISION_REF_NOT_IN_PACK = "decision_ref_not_in_pack"
REASON_CITED_SPAN_NOT_IN_PACK = "cited_span_not_in_pack"
REASON_FINDING_CLASS_NOT_REGISTERED = "finding_class_not_registered"
REASON_CONTRADICTED_DECISION_NOT_CONFIRMED = "contradicted_decision_not_confirmed"
REASON_REVERSED_DECISION_NOT_SUPERSEDED = "reversed_decision_not_superseded"
REASON_SUPERSEDING_DECISION_MISSING = "superseding_decision_missing"
REASON_UNKNOWN_CONFIDENCE_LABEL = "unknown_confidence_label"
# cortex#373 (cites-missing-path, shadow) rejection codes.
REASON_MISSING_PATH_NOT_NAMED = "missing_path_not_named"
REASON_MISSING_PATH_DECISION_NOT_CONFIRMED = "missing_path_decision_not_confirmed"
# cortex#374 (omitted-load-bearing-constraint, shadow) rejection codes.
REASON_OMITTED_DECISION_NOT_NAMED = "omitted_decision_not_named"
REASON_OMISSION_STAGE_NOT_NAMED = "omission_stage_not_named"
REASON_OMISSION_ANCHOR_NOT_CONFIRMED = "omission_anchor_decision_not_confirmed"

# A finding "names" a path when its summary carries a path-shaped token:
# a slash-joined relative path (src/app.py, docs/adr/0001.md) or a dotted
# filename whose extension is at least two characters (retry.py, setup.cfg —
# the two-character floor keeps prose abbreviations like "e.g." from
# satisfying the gate; single-character extensions need a directory segment
# to qualify). The check is deliberately lexical: the evaluator has no
# filesystem access by design, so "the missing path is named" is the
# strongest deterministic evidence gate available for cortex#373.
_PATH_TOKEN_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+|\b[\w-]+\.[A-Za-z]\w{1,11}\b")


class EvaluatorValidationError(ValueError):
    """Raised when evaluator material violates the soft-evaluator contract."""


class UncitedFindingError(EvaluatorValidationError):
    """Raised for findings whose provenance is absent from the pack.

    The cortex#377 fail-closed class: no resolvable decision reference, or a
    cited span hash the pack never offered. These findings are refused
    emission outright; the citation boundary holds.
    """


class EvaluationState(StrEnum):
    """Whether the evaluation emitted findings or fail-closed to none."""

    FINDINGS_EMITTED = "findings_emitted"
    NO_FINDINGS = "no_findings"


# Validation helpers sit above the Stage 0 registry because the registry's
# FindingClassSpec entries are constructed (and validated) at module load.


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise EvaluatorValidationError(f"{name} must not be empty")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise EvaluatorValidationError(f"{name} must be a UUID") from exc


def _validate_hash(name: str, value: str) -> None:
    if not _SHA256_RE.match(value):
        raise EvaluatorValidationError(f"{name} must be a sha256 hex string")


def _validate_omission_counts(name: str, value: Mapping[str, int]) -> None:
    if not isinstance(value, Mapping):
        raise EvaluatorValidationError(f"{name} must be a mapping")
    for key, count in value.items():
        if not isinstance(key, str) or not key.strip():
            raise EvaluatorValidationError(f"{name} keys must be non-empty strings")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise EvaluatorValidationError(f"{name}[{key!r}] must be a non-negative int")


@dataclass(frozen=True)
class CompanionEvidenceRequirement:
    """Pack-level evidence a finding class requires beyond the cited decision."""

    description: str
    reason_code_marker: str
    rejection_code: str

    def __post_init__(self) -> None:
        _require_non_empty("description", self.description)
        _require_non_empty("reason_code_marker", self.reason_code_marker)
        _require_non_empty("rejection_code", self.rejection_code)


class ClassEvidenceRule(StrEnum):
    """Class-specific evidence checks beyond companion + cited-status.

    Each rule names one deterministic check ``FindingClassSpec.evidence_failure``
    dispatches after the companion and status gates. Rules are an enum (not
    free callables) so a registry entry's evidence contract stays declarative
    and reviewable.
    """

    # cortex#373: the missing path must be named in the finding's summary.
    NAMED_MISSING_PATH = "named-missing-path"
    # cortex#374: the summary must name a decision id that context assembly
    # actually omitted for budget, plus the omission stage key.
    NAMED_BUDGET_OMISSION = "named-budget-omission"


@dataclass(frozen=True)
class FindingClassSpec:
    """Evidence requirements and prompt guidance for one finding class.

    ``evidence_failure`` checks the companion requirement first, then the
    cited decision's status, then the class-specific ``class_rule`` (when
    set), so every rejection path is reachable and independently testable.
    The check order is deterministic and the first failure names the
    rejection.

    ``shadow=True`` marks a cortex#373/#374 shadow lane: validated findings
    of the class are captured in ``EvaluationOutcome.shadow_findings``
    instead of emitted — the advisory ladder and every render layer skip
    shadow classes by construction.
    """

    finding_class: FindingClass
    required_cited_status: str
    status_rejection_code: str
    prompt_guidance: str
    companion: CompanionEvidenceRequirement | None = None
    shadow: bool = False
    class_rule: ClassEvidenceRule | None = None

    def __post_init__(self) -> None:
        valid_statuses = {status.value for status in DecisionStatus}
        if self.required_cited_status not in valid_statuses:
            raise EvaluatorValidationError(
                f"required_cited_status must be one of {sorted(valid_statuses)}; "
                f"got {self.required_cited_status!r}"
            )
        _require_non_empty("status_rejection_code", self.status_rejection_code)
        _require_non_empty("prompt_guidance", self.prompt_guidance)
        if self.class_rule is not None and not isinstance(self.class_rule, ClassEvidenceRule):
            raise EvaluatorValidationError(
                f"class_rule must be a ClassEvidenceRule; got {self.class_rule!r}"
            )

    def evidence_failure(
        self,
        *,
        finding: FindingDraft,
        cited: DecisionsForDiffCandidate,
        pack_candidates: tuple[DecisionsForDiffCandidate, ...],
        omitted_for_budget_ids: tuple[str, ...],
    ) -> tuple[str, str] | None:
        """Return ``(rejection_code, detail)`` for failed evidence, else None."""

        if self.companion is not None:
            marker = self.companion.reason_code_marker
            companion_present = any(
                candidate.decision_node_id != cited.decision_node_id
                and marker in candidate.reason_codes
                for candidate in pack_candidates
            )
            if not companion_present:
                return (
                    self.companion.rejection_code,
                    f"{self.finding_class.value} requires "
                    f"{self.companion.description}; no other pack candidate "
                    f"carries reason code {marker!r}",
                )
        if cited.status != self.required_cited_status:
            return (
                self.status_rejection_code,
                f"{self.finding_class.value} requires the cited decision's "
                f"status to be {self.required_cited_status!r}; pack candidate "
                f"{cited.decision_node_id} has status {cited.status!r}",
            )
        if self.class_rule is ClassEvidenceRule.NAMED_MISSING_PATH:
            return _named_missing_path_failure(finding)
        if self.class_rule is ClassEvidenceRule.NAMED_BUDGET_OMISSION:
            return _named_budget_omission_failure(finding, omitted_for_budget_ids)
        return None


def _named_missing_path_failure(finding: FindingDraft) -> tuple[str, str] | None:
    """cortex#373: the missing path must be named in the summary."""

    if _PATH_TOKEN_RE.search(finding.summary) is None:
        return (
            REASON_MISSING_PATH_NOT_NAMED,
            f"{finding.finding_class.value} requires the summary to name the "
            "missing path (a slash-joined path or a filename with an "
            "extension); the summary names none",
        )
    return None


def _named_budget_omission_failure(
    finding: FindingDraft, omitted_for_budget_ids: tuple[str, ...]
) -> tuple[str, str] | None:
    """cortex#374: name an actually-omitted decision id plus its stage.

    Only budget omissions carry knowable decision ids — the evaluator holds
    both the full pack and the budgeted context, so the omitted set is exact.
    Retrieval-stage omissions arrive as counts without ids, so a claim about
    them cannot be verified and fails this gate (fail-closed, by design).
    """

    named = tuple(
        node_id for node_id in omitted_for_budget_ids if node_id in finding.summary
    )
    if not named:
        return (
            REASON_OMITTED_DECISION_NOT_NAMED,
            f"{finding.finding_class.value} requires the summary to name a "
            "decision id that context assembly actually omitted; "
            f"{len(omitted_for_budget_ids)} candidate(s) were omitted at stage "
            f"{OVER_BUDGET_OMISSION_KEY!r} and the summary names none of them",
        )
    if OVER_BUDGET_OMISSION_KEY not in finding.summary:
        return (
            REASON_OMISSION_STAGE_NOT_NAMED,
            f"{finding.finding_class.value} requires the summary to name the "
            f"omission stage {OVER_BUDGET_OMISSION_KEY!r} alongside omitted "
            f"decision {named[0]}",
        )
    return None


# The Stage 0 registry (cortex#371 + #372 live, #373 + #374 shadow). Keys
# are the only classes the Stage 0 evaluate prompt asks for. The two
# ``shadow=True`` entries are captured in EvaluationOutcome.shadow_findings —
# never emitted, never laddered, never rendered — until their measured
# precision clears the graduation bar; graduation is the explicit, reviewed
# act of flipping ``shadow`` to False. Extensible: pass a wider mapping to
# evaluate_diff once a new class's evidence contract is reviewed.
STAGE0_FINDING_CLASS_REGISTRY: Mapping[FindingClass, FindingClassSpec] = MappingProxyType(
    {
        FindingClass.CONTRADICTS_PRIOR_DECISION: FindingClassSpec(
            finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
            required_cited_status=DecisionStatus.CONFIRMED.value,
            status_rejection_code=REASON_CONTRADICTED_DECISION_NOT_CONFIRMED,
            prompt_guidance=(
                "emit only when the diff conflicts with a decision in the pack "
                "whose status is 'confirmed'; cite span hashes from that "
                "decision's citations"
            ),
        ),
        FindingClass.REVERSES_SUPERSEDED_PATTERN: FindingClassSpec(
            finding_class=FindingClass.REVERSES_SUPERSEDED_PATTERN,
            required_cited_status=DecisionStatus.SUPERSEDED.value,
            status_rejection_code=REASON_REVERSED_DECISION_NOT_SUPERSEDED,
            companion=CompanionEvidenceRequirement(
                description=(
                    "the superseding decision present in the pack (it enters "
                    "via the 'supersedes' graph edge)"
                ),
                reason_code_marker=GRAPH_SUPERSEDES_REASON_CODE,
                rejection_code=REASON_SUPERSEDING_DECISION_MISSING,
            ),
            prompt_guidance=(
                "emit only when the diff reintroduces a pattern from a pack "
                "decision whose status is 'superseded' and the superseding "
                "decision is also present in the pack; cite span hashes from "
                "the superseded decision's citations"
            ),
        ),
        FindingClass.CITES_MISSING_PATH: FindingClassSpec(
            finding_class=FindingClass.CITES_MISSING_PATH,
            required_cited_status=DecisionStatus.CONFIRMED.value,
            status_rejection_code=REASON_MISSING_PATH_DECISION_NOT_CONFIRMED,
            shadow=True,
            class_rule=ClassEvidenceRule.NAMED_MISSING_PATH,
            prompt_guidance=(
                "emit only when the diff relies on a path that does not exist "
                "in the changed surface or repo state the candidate pack "
                "carries; name the missing path in the summary and cite span "
                "hashes from the confirmed decision establishing the path's "
                "expected existence"
            ),
        ),
        FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT: FindingClassSpec(
            finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
            required_cited_status=DecisionStatus.CONFIRMED.value,
            status_rejection_code=REASON_OMISSION_ANCHOR_NOT_CONFIRMED,
            shadow=True,
            class_rule=ClassEvidenceRule.NAMED_BUDGET_OMISSION,
            prompt_guidance=(
                "emit only when the omission accounting shows context assembly "
                "omitted a decision whose scope matched the diff; name the "
                "omitted decision id and the omission stage (for example "
                "'over_budget') in the summary and cite span hashes from a "
                "confirmed decision in the pack"
            ),
        ),
    }
)


def evaluate_prompt_guidance(
    registry: Mapping[FindingClass, FindingClassSpec] = STAGE0_FINDING_CLASS_REGISTRY,
) -> str:
    """The prompt-contract text asking for exactly the registered classes.

    This is the canonical Stage 0 source for the class vocabulary the
    evaluate prompt template carries; the registered template
    (``model_registry``) incorporates it, and the stamped ``prompt_version``
    identifies the contract for replay. Deterministic: same registry, same
    text.
    """

    _validate_registry(registry)
    lines = ["Emit findings using ONLY these finding_class values:"]
    for finding_class in sorted(registry, key=lambda entry: entry.value):
        lines.append(f"- {finding_class.value}: {registry[finding_class].prompt_guidance}")
    lines.append(
        "Set confidence_label to one of: " + ", ".join(CONFIDENCE_LABEL_VOCABULARY) + "."
    )
    lines.append(
        "Never emit any other finding class; never cite span hashes the "
        "decisions above do not carry."
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class EvaluationReplayKey:
    """The replay key material binding an evaluation to its exact inputs.

    Everything a replay needs to reproduce or audit the run: the graph
    snapshot and retrieval config that produced the pack, the pack and
    context hashes, the bound request hash, and the (model_id,
    prompt_version) stamp.
    """

    graph_snapshot_hash: str
    retrieval_config_version: str
    query_hash: str
    candidate_set_hash: str
    context_hash: str
    input_hash: str
    model_id: str
    prompt_version: str
    run_id: str
    estimator_version: str
    token_budget: int

    def __post_init__(self) -> None:
        for name, value in (
            ("graph_snapshot_hash", self.graph_snapshot_hash),
            ("query_hash", self.query_hash),
            ("candidate_set_hash", self.candidate_set_hash),
            ("context_hash", self.context_hash),
            ("input_hash", self.input_hash),
        ):
            _validate_hash(name, value)
        for name, value in (
            ("retrieval_config_version", self.retrieval_config_version),
            ("model_id", self.model_id),
            ("prompt_version", self.prompt_version),
            ("run_id", self.run_id),
            ("estimator_version", self.estimator_version),
        ):
            _require_non_empty(name, value)
        if isinstance(self.token_budget, bool) or not isinstance(self.token_budget, int):
            raise EvaluatorValidationError("token_budget must be an int")
        if self.token_budget < 1:
            raise EvaluatorValidationError("token_budget must be >= 1")

    def as_payload(self) -> dict[str, Any]:
        return {
            "candidate_set_hash": self.candidate_set_hash,
            "context_hash": self.context_hash,
            "estimator_version": self.estimator_version,
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "input_hash": self.input_hash,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "query_hash": self.query_hash,
            "retrieval_config_version": self.retrieval_config_version,
            "run_id": self.run_id,
            "token_budget": self.token_budget,
        }


@dataclass(frozen=True)
class EmittedFinding:
    """One validated finding cleared for advisory emission."""

    finding: FindingDraft
    decision_version_id: str
    tier: ConfidenceTier
    behavior: EmissionBehavior

    def __post_init__(self) -> None:
        _require_uuid("decision_version_id", self.decision_version_id)
        if self.behavior is not TIER_EMISSION_BEHAVIOR[self.tier]:
            raise EvaluatorValidationError(
                f"tier {self.tier.value!r} emits as "
                f"{TIER_EMISSION_BEHAVIOR[self.tier].value!r}, "
                f"not {self.behavior.value!r}"
            )

    @property
    def decision_node_id(self) -> str:
        return self.finding.decision_node_id

    def as_payload(self) -> dict[str, Any]:
        return {
            "behavior": self.behavior.value,
            "blocking_enabled": BLOCKING_ENABLED,
            "decision_version_id": self.decision_version_id,
            "finding": _finding_payload(self.finding),
            "tier": self.tier.value,
        }


@dataclass(frozen=True)
class RejectedFinding:
    """One finding refused emission, with its visible degradation report."""

    finding: FindingDraft
    reason_code: str
    detail: str
    degradation: DegradationReport

    def __post_init__(self) -> None:
        _require_non_empty("reason_code", self.reason_code)
        _require_non_empty("detail", self.detail)
        if self.degradation.reason_code != self.reason_code:
            raise EvaluatorValidationError(
                "a rejection's degradation report must carry the rejection's "
                f"reason code; got {self.degradation.reason_code!r} for "
                f"{self.reason_code!r}"
            )

    def as_payload(self) -> dict[str, Any]:
        return {
            "degradation": self.degradation.as_payload(),
            "detail": self.detail,
            "finding": _finding_payload(self.finding),
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class SuppressedFinding:
    """One valid finding suppressed below the ladder's emission floor."""

    finding: FindingDraft
    tier: ConfidenceTier
    reason: str

    def __post_init__(self) -> None:
        _require_non_empty("reason", self.reason)

    def as_payload(self) -> dict[str, Any]:
        return {
            "finding": _finding_payload(self.finding),
            "reason": self.reason,
            "tier": self.tier.value,
        }


@dataclass(frozen=True)
class ShadowFinding:
    """One validated finding captured in shadow mode (cortex#373/#374).

    Shadow findings cleared the citation gate, their class-evidence checks,
    and the confidence-label vocabulary — but their class has not earned
    emission yet. They carry no emission behavior by construction, so no
    render layer can present one as advisory; the tier is recorded for the
    precision measurement that decides graduation.
    """

    finding: FindingDraft
    decision_version_id: str
    tier: ConfidenceTier

    def __post_init__(self) -> None:
        _require_uuid("decision_version_id", self.decision_version_id)

    @property
    def decision_node_id(self) -> str:
        return self.finding.decision_node_id

    def as_payload(self) -> dict[str, Any]:
        return {
            "decision_version_id": self.decision_version_id,
            "finding": _finding_payload(self.finding),
            "shadow": True,
            "tier": self.tier.value,
        }


@dataclass(frozen=True)
class EvaluationOutcome:
    """The soft evaluator's only output shape.

    Visibility arithmetic: every finding the model proposed is exactly one
    of emitted, rejected, suppressed, or shadow-captured; every ledger draft
    pairs with one emitted finding; omission counts carried from context
    assembly stay visible. ``state`` is derived — an outcome with no emitted
    findings is the explicit fail-closed no-findings result (cortex#377),
    with the drop accounting in ``rejection_counts`` and
    ``suppressed_below_floor``. Shadow findings (cortex#373/#374) never flip
    the state: a shadow-only run is still ``no_findings``, with the capture
    counted in ``shadow_finding_count`` / ``shadow_class_counts``.
    """

    replay: EvaluationReplayKey
    emitted: tuple[EmittedFinding, ...]
    rejected: tuple[RejectedFinding, ...]
    suppressed: tuple[SuppressedFinding, ...]
    ledger_event_drafts: tuple[LedgerEvent, ...]
    total_omitted: Mapping[str, int]
    omitted_for_budget: int
    model_omitted_decision_count: int
    degraded_reasons: tuple[str, ...] = ()
    shadow_findings: tuple[ShadowFinding, ...] = ()

    def __post_init__(self) -> None:
        if len(self.ledger_event_drafts) != len(self.emitted):
            raise EvaluatorValidationError(
                "exactly one finding.emitted ledger draft per emitted finding; "
                f"got {len(self.ledger_event_drafts)} draft(s) for "
                f"{len(self.emitted)} emitted finding(s)"
            )
        for emitted, draft in zip(self.emitted, self.ledger_event_drafts, strict=True):
            if draft.event_type is not LedgerEventType.FINDING_EMITTED:
                raise EvaluatorValidationError(
                    f"ledger drafts must be {LedgerEventType.FINDING_EMITTED.value!r} "
                    f"events; got {draft.event_type.value!r}"
                )
            if draft.source_span_hashes != emitted.finding.cited_span_hashes:
                raise EvaluatorValidationError(
                    "a ledger draft's span hashes must equal its emitted "
                    "finding's cited span hashes"
                )
            if draft.graph_snapshot_hash != self.replay.graph_snapshot_hash:
                raise EvaluatorValidationError(
                    "ledger drafts must carry the evaluation's graph snapshot hash"
                )
            if (
                draft.model_id != self.replay.model_id
                or draft.prompt_version != self.replay.prompt_version
            ):
                raise EvaluatorValidationError(
                    "ledger drafts must carry the evaluation's "
                    "(model_id, prompt_version) stamp"
                )
        _validate_omission_counts("total_omitted", self.total_omitted)
        if OVER_BUDGET_OMISSION_KEY not in self.total_omitted:
            raise EvaluatorValidationError(
                f"total_omitted must carry {OVER_BUDGET_OMISSION_KEY!r}; budget "
                "accounting stays visible even when zero"
            )
        if self.omitted_for_budget < 0:
            raise EvaluatorValidationError("omitted_for_budget must be >= 0")
        if self.total_omitted[OVER_BUDGET_OMISSION_KEY] < self.omitted_for_budget:
            raise EvaluatorValidationError(
                f"total_omitted[{OVER_BUDGET_OMISSION_KEY!r}] must include every "
                "candidate counted in omitted_for_budget"
            )
        if self.model_omitted_decision_count < 0:
            raise EvaluatorValidationError("model_omitted_decision_count must be >= 0")
        for reason in self.degraded_reasons:
            _require_non_empty("degraded_reasons entries", reason)
        object.__setattr__(self, "total_omitted", MappingProxyType(dict(self.total_omitted)))

    @property
    def state(self) -> EvaluationState:
        return (
            EvaluationState.FINDINGS_EMITTED
            if self.emitted
            else EvaluationState.NO_FINDINGS
        )

    @property
    def suppressed_below_floor(self) -> int:
        """Visible count of valid findings the emission floor suppressed."""

        return len(self.suppressed)

    @property
    def rejection_counts(self) -> dict[str, int]:
        """Per-reason-code rejection counts; derived so they cannot drift."""

        counts: dict[str, int] = {}
        for rejection in self.rejected:
            counts[rejection.reason_code] = counts.get(rejection.reason_code, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def shadow_finding_count(self) -> int:
        """Visible count of validated findings captured in shadow mode."""

        return len(self.shadow_findings)

    @property
    def shadow_class_counts(self) -> dict[str, int]:
        """Per-class shadow capture counts; derived so they cannot drift."""

        counts: dict[str, int] = {}
        for shadow in self.shadow_findings:
            key = shadow.finding.finding_class.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def candidate_finding_count(self) -> int:
        """Every proposed finding: emitted + rejected + suppressed + shadow."""

        return (
            len(self.emitted)
            + len(self.rejected)
            + len(self.suppressed)
            + len(self.shadow_findings)
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "candidate_finding_count": self.candidate_finding_count,
            "degraded_reasons": list(self.degraded_reasons),
            "emitted": [finding.as_payload() for finding in self.emitted],
            "ledger_event_draft_count": len(self.ledger_event_drafts),
            "model_omitted_decision_count": self.model_omitted_decision_count,
            "omitted_for_budget": self.omitted_for_budget,
            "rejected": [rejection.as_payload() for rejection in self.rejected],
            "rejection_counts": self.rejection_counts,
            "replay": self.replay.as_payload(),
            "shadow_class_counts": self.shadow_class_counts,
            "shadow_finding_count": self.shadow_finding_count,
            "shadow_findings": [entry.as_payload() for entry in self.shadow_findings],
            "state": self.state.value,
            "suppressed": [entry.as_payload() for entry in self.suppressed],
            "suppressed_below_floor": self.suppressed_below_floor,
            "total_omitted": dict(self.total_omitted),
        }


def evaluate_diff(
    pack: DecisionsForDiffCandidatePack,
    diff_patch: str,
    model: EvaluateModel,
    *,
    token_budget: int,
    ladder: AdvisoryLadder,
    run_ledger: RunLedger,
    prompt_version: str,
    tenant_id: str,
    source_id: str,
    actor: ActorRef,
    occurred_at: datetime,
    registry: Mapping[FindingClass, FindingClassSpec] = STAGE0_FINDING_CLASS_REGISTRY,
    estimator: TokenEstimator = default_token_estimator,
    metadata: Mapping[str, Any] | None = None,
) -> EvaluationOutcome:
    """Evaluate one diff against its candidate pack, fail-closed throughout.

    Orchestration order: assemble the budgeted context, bound the pack to
    what the budget admitted, refuse predictable budget breaches before the
    model call (``run_ledger`` raises ``BudgetExceededError``), call the
    model boundary, bind the result to the request, then validate every
    proposed finding through the cortex#377 citation gate, the #371-#374
    class-evidence registry, and — for non-shadow classes — the #375 ladder.
    Validated findings of ``shadow=True`` classes are captured in
    ``shadow_findings`` instead of emitted (cortex#373/#374). ``occurred_at``
    is an explicit caller-owned timestamp (timezone-aware, enforced by
    ``LedgerEvent``) so the evaluator has no hidden clock.
    """

    _validate_registry(registry)
    context = assemble_evaluation_context(
        pack, token_budget=token_budget, estimator=estimator
    )
    bounded_pack = DecisionsForDiffCandidatePack(
        query_hash=pack.query_hash,
        retrieval_config_version=pack.retrieval_config_version,
        graph_snapshot_hash=pack.graph_snapshot_hash,
        candidates=context.candidates,
        omitted_counts=dict(context.total_omitted),
        graph_node_count=pack.graph_node_count,
        candidate_pool_size=pack.candidate_pool_size,
    )
    request = EvaluateRequest(
        candidate_pack=bounded_pack,
        diff_patch=diff_patch,
        prompt_version=prompt_version,
        metadata={} if metadata is None else metadata,
    )
    # Predictable budget breach refuses the call before any spend
    # (BudgetExceededError -> fail_closed_refusal in the #329 taxonomy).
    run_ledger.ensure_budget_allows_call(task_kind="evaluate")
    result = model.evaluate(request)
    # Bind here regardless of the model implementation: a result that does
    # not answer this request is refused even from a non-routing fake.
    ensure_result_binds_request(request, result)

    replay = EvaluationReplayKey(
        graph_snapshot_hash=pack.graph_snapshot_hash,
        retrieval_config_version=pack.retrieval_config_version,
        query_hash=pack.query_hash,
        candidate_set_hash=bounded_pack.candidate_set_hash,
        context_hash=context.context_hash,
        input_hash=result.input_hash,
        model_id=result.model_id,
        prompt_version=result.prompt_version,
        run_id=run_ledger.run_id,
        estimator_version=context.estimator_version,
        token_budget=token_budget,
    )

    candidates_by_id = {
        candidate.decision_node_id: candidate for candidate in bounded_pack.candidates
    }
    pack_span_hashes = {
        span.span_hash
        for candidate in bounded_pack.candidates
        for span in candidate.cited_spans
    }
    # The cortex#374 evidence material: budget omissions are the one omission
    # stage with knowable decision ids, because the evaluator holds both the
    # full pack and the budgeted context it was bounded to.
    omitted_for_budget_ids = tuple(
        candidate.decision_node_id
        for candidate in pack.candidates
        if candidate.decision_node_id not in candidates_by_id
    )

    emitted: list[EmittedFinding] = []
    rejected: list[RejectedFinding] = []
    suppressed: list[SuppressedFinding] = []
    shadow: list[ShadowFinding] = []
    drafts: list[LedgerEvent] = []
    for ordinal, finding in enumerate(result.findings):
        cited = candidates_by_id.get(finding.decision_node_id)
        if cited is None:
            rejected.append(
                _rejection(
                    finding,
                    UncitedFindingError(
                        f"finding cites decision {finding.decision_node_id}, which "
                        "is not in the bounded candidate pack; refusing to emit "
                        "without resolvable provenance"
                    ),
                    REASON_DECISION_REF_NOT_IN_PACK,
                )
            )
            continue
        missing_spans = tuple(
            span_hash
            for span_hash in finding.cited_span_hashes
            if span_hash not in pack_span_hashes
        )
        if missing_spans:
            rejected.append(
                _rejection(
                    finding,
                    UncitedFindingError(
                        f"finding cites span hash(es) {list(missing_spans)} that "
                        "the bounded candidate pack never offered; refusing to "
                        "emit an unverifiable citation"
                    ),
                    REASON_CITED_SPAN_NOT_IN_PACK,
                )
            )
            continue
        spec = registry.get(finding.finding_class)
        if spec is None:
            rejected.append(
                _rejection(
                    finding,
                    EvaluatorValidationError(
                        f"finding class {finding.finding_class.value!r} is not in "
                        "the Stage 0 registry; only registered classes may emit"
                    ),
                    REASON_FINDING_CLASS_NOT_REGISTERED,
                )
            )
            continue
        evidence = spec.evidence_failure(
            finding=finding,
            cited=cited,
            pack_candidates=bounded_pack.candidates,
            omitted_for_budget_ids=omitted_for_budget_ids,
        )
        if evidence is not None:
            code, detail = evidence
            rejected.append(_rejection(finding, EvaluatorValidationError(detail), code))
            continue
        try:
            tier = ladder.tier_for_label(finding.confidence_label)
        except AdvisoryLadderError as exc:
            rejected.append(_rejection(finding, exc, REASON_UNKNOWN_CONFIDENCE_LABEL))
            continue
        if spec.shadow:
            # Shadow capture (cortex#373/#374): validated, counted, never
            # emitted. The ladder's emission assessment is skipped by
            # construction — a shadow finding has no behavior to render and
            # no floor to be suppressed under.
            shadow.append(
                ShadowFinding(
                    finding=finding,
                    decision_version_id=cited.decision_version_id,
                    tier=tier,
                )
            )
            continue
        assessment = ladder.assess(tier)
        if not assessment.emitted:
            assert assessment.suppression_reason is not None  # LadderAssessment invariant
            suppressed.append(
                SuppressedFinding(
                    finding=finding, tier=tier, reason=assessment.suppression_reason
                )
            )
            continue
        assert assessment.behavior is not None  # LadderAssessment invariant
        emitted_finding = EmittedFinding(
            finding=finding,
            decision_version_id=cited.decision_version_id,
            tier=tier,
            behavior=assessment.behavior,
        )
        emitted.append(emitted_finding)
        drafts.append(
            _finding_emitted_event(
                emitted=emitted_finding,
                ordinal=ordinal,
                replay=replay,
                tenant_id=tenant_id,
                source_id=source_id,
                actor=actor,
                occurred_at=occurred_at,
            )
        )

    degraded_reasons: tuple[str, ...] = ()
    if context.degraded_reason is not None:
        degraded_reasons += (context.degraded_reason,)
    degraded_reasons += result.degraded_reasons

    return EvaluationOutcome(
        replay=replay,
        emitted=tuple(emitted),
        rejected=tuple(rejected),
        suppressed=tuple(suppressed),
        ledger_event_drafts=tuple(drafts),
        total_omitted=context.total_omitted,
        omitted_for_budget=context.omitted_for_budget,
        model_omitted_decision_count=result.omitted_decision_count,
        degraded_reasons=degraded_reasons,
        shadow_findings=tuple(shadow),
    )


def _rejection(
    finding: FindingDraft, failure: ValueError, reason_code: str
) -> RejectedFinding:
    # Imported lazily because degradation.py is the taxonomy aggregator: it
    # imports this module's error types at load time, so a module-level
    # import here would be circular. By the time a rejection is built, both
    # modules are fully initialized.
    from cortex.hosted.degradation import DegradationReport, classify_failure

    return RejectedFinding(
        finding=finding,
        reason_code=reason_code,
        detail=str(failure),
        degradation=DegradationReport(
            mode=classify_failure(failure),
            reason_code=reason_code,
            source=EVALUATOR_SOURCE,
            safety_boundary_held=True,
        ),
    )


def _finding_emitted_event(
    *,
    emitted: EmittedFinding,
    ordinal: int,
    replay: EvaluationReplayKey,
    tenant_id: str,
    source_id: str,
    actor: ActorRef,
    occurred_at: datetime,
) -> LedgerEvent:
    """Compose one finding.emitted ledger event with full replay material.

    The ``LedgerEvent`` envelope enforces the write-time invariants (span
    hashes, graph snapshot hash, model stamp); composition here only fills
    it. ``ordinal`` is the finding's position in the model result, so the
    idempotency key is stable across replays regardless of how many sibling
    findings were rejected.
    """

    payload: dict[str, Any] = {
        "behavior": emitted.behavior.value,
        "blocking_enabled": BLOCKING_ENABLED,
        "confidence_tier": emitted.tier.value,
        "decision_version_id": emitted.decision_version_id,
        "finding": _finding_payload(emitted.finding),
        "replay": replay.as_payload(),
    }
    external_id = f"evaluate:{replay.input_hash}:finding:{ordinal}"
    return LedgerEvent(
        tenant_id=tenant_id,
        source_id=source_id,
        event_type=LedgerEventType.FINDING_EMITTED,
        actor=actor,
        occurred_at=occurred_at,
        idempotency_key=derive_idempotency_key(
            source_id=source_id,
            event_type=LedgerEventType.FINDING_EMITTED,
            source_event_external_id=external_id,
            payload=payload,
        ),
        payload=payload,
        source_span_hashes=emitted.finding.cited_span_hashes,
        graph_snapshot_hash=replay.graph_snapshot_hash,
        model_id=replay.model_id,
        prompt_version=replay.prompt_version,
        source_event_external_id=external_id,
    )


def _finding_payload(finding: FindingDraft) -> dict[str, Any]:
    return {
        "cited_span_hashes": list(finding.cited_span_hashes),
        "confidence_label": finding.confidence_label,
        "decision_node_id": finding.decision_node_id,
        "finding_class": finding.finding_class.value,
        "suggested_repair": finding.suggested_repair,
        "summary": finding.summary,
    }


def _validate_registry(registry: Mapping[FindingClass, FindingClassSpec]) -> None:
    if not registry:
        raise EvaluatorValidationError(
            "the finding-class registry must register at least one class"
        )
    for finding_class, spec in registry.items():
        if spec.finding_class is not finding_class:
            raise EvaluatorValidationError(
                f"registry key {finding_class.value!r} maps to a spec for "
                f"{spec.finding_class.value!r}; keys must match their specs"
            )
