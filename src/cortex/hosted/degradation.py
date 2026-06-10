"""No-silent-failure degradation taxonomy for the hosted reviewer (cortex#329).

The substrate already fails closed in many places; this module unifies that
vocabulary so every reviewer failure surfaces as one of a small set of
*behaviors* instead of a per-module exception name. The modes are derived
from what the shipped code already does:

- ``ask_ledger`` flips to ``AnswerState.NO_ANSWER`` / ``no_cited_support``
  instead of answering without citations (refusal, boundary held).
- ``visibility`` and ``storage`` refuse operations that would cross a
  declared authorization or storage boundary (refusal, boundary held).
- ``ledger_events``, ``provenance``, ``scopes``, ``ask_ledger``,
  ``decisions_for_diff``, ``diff_surface``, ``eval_fixtures``,
  ``embeddings``, and ``model_interfaces`` reject invalid material before
  any model call (rejection, nothing partial was produced).
- ``model_registry`` raises when stamped identity material does not match
  the registered content ("prompt drift detected"), and at review time a
  registry failure means a verdict's (model, prompt) identity cannot be
  trusted as stated (drift).
- ``decisions_for_diff`` and ``ask_ledger`` answer with bounded candidate
  sets and visible ``omitted_counts`` (bounded omission, counts visible).

Consumers: cortex#377 applies this taxonomy inside the soft evaluator
(Wave 5), and the Stage 2 GitHub reviewer surfaces these modes in advisory
comments. The prose contract lives in ``docs/degradation-modes.md``.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from enum import StrEnum

from cortex.hosted.ask_ledger import AnswerState, AskLedgerValidationError
from cortex.hosted.banking import BankingValidationError
from cortex.hosted.candidate_metrics import CandidateMetricsValidationError
from cortex.hosted.cascade import CascadeValidationError
from cortex.hosted.citation_check import CitationCheckError
from cortex.hosted.confidence import ConfidenceValidationError
from cortex.hosted.context_assembly import ContextAssemblyValidationError
from cortex.hosted.cost import BudgetExceededError, CostValidationError
from cortex.hosted.decisions_for_diff import DecisionsForDiffValidationError
from cortex.hosted.derive_store import DeriveStoreError
from cortex.hosted.diff_surface import DiffSurfaceValidationError
from cortex.hosted.embeddings import HostedEmbeddingValidationError
from cortex.hosted.eval_fixtures import FixtureValidationError
from cortex.hosted.event_ordering import EventOrderingError
from cortex.hosted.extractors import ExtractorError
from cortex.hosted.graph_snapshot import GraphSnapshotValidationError
from cortex.hosted.graph_writes import GraphWriteValidationError
from cortex.hosted.labeling import LabelingError
from cortex.hosted.lane_assignment import LaneAssignmentError
from cortex.hosted.lanes import LanePolicyValidationError
from cortex.hosted.ledger_events import LedgerEventValidationError
from cortex.hosted.model_registry import RegistryValidationError
from cortex.hosted.provenance import ProvenanceValidationError
from cortex.hosted.quality_series import QualitySeriesValidationError
from cortex.hosted.recorded_responses import RecordedResponseError
from cortex.hosted.route_comparison import RouteComparisonValidationError
from cortex.hosted.routing import (
    ClaudeCliOutputError,
    ClaudeCliUnavailableError,
    RecordedResponseMissingError,
    RoutingError,
)
from cortex.hosted.scopes import ScopeValidationError
from cortex.hosted.storage import StoreBoundaryError
from cortex.hosted.visibility import VisibilityBoundaryValidationError


class DegradationTaxonomyError(ValueError):
    """Raised when a failure cannot be classified or a report is malformed."""


class DegradationMode(StrEnum):
    """Visible reviewer degradation behaviors.

    Modes classify what the system *did* when something went wrong, not
    which module raised. Every mode is documented in
    ``docs/degradation-modes.md`` with its trigger, a shipped-code example,
    what the user sees, and what is never allowed.
    """

    FAIL_CLOSED_REFUSAL = "fail_closed_refusal"
    BOUNDED_OMISSION = "bounded_omission"
    INVALID_INPUT_REJECTED = "invalid_input_rejected"
    DRIFT_DETECTED = "drift_detected"
    DEGRADED_CAPABILITY = "degraded_capability"


# Exact-type dispatch table. Classification is per concrete type, never
# inherited: a future subclass refines behavior, and inheriting the parent's
# mode would silently mislabel that refinement. Unknown types therefore
# raise in classify_failure instead of falling back to anything.
_FAILURE_MODE_BY_TYPE: dict[type[BaseException], DegradationMode] = {
    BudgetExceededError: DegradationMode.FAIL_CLOSED_REFUSAL,
    CandidateMetricsValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    CitationCheckError: DegradationMode.INVALID_INPUT_REJECTED,
    ClaudeCliOutputError: DegradationMode.FAIL_CLOSED_REFUSAL,
    ClaudeCliUnavailableError: DegradationMode.DEGRADED_CAPABILITY,
    ContextAssemblyValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    CostValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    EventOrderingError: DegradationMode.INVALID_INPUT_REJECTED,
    GraphSnapshotValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    RecordedResponseError: DegradationMode.DRIFT_DETECTED,
    RecordedResponseMissingError: DegradationMode.FAIL_CLOSED_REFUSAL,
    RoutingError: DegradationMode.INVALID_INPUT_REJECTED,
    AskLedgerValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    BankingValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    CascadeValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    QualitySeriesValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    RouteComparisonValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    ConfidenceValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    DecisionsForDiffValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # DeriveStoreError's marquee failure is the same-idempotency-key /
    # different-event-hash collision — recorded state disagreeing with a
    # re-derivation is drift, not bad input.
    DeriveStoreError: DegradationMode.DRIFT_DETECTED,
    DiffSurfaceValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # An unrecognized or malformed derive source is rejected before any
    # extraction or write; recognized-but-noisy material is not an error at
    # all (it becomes DroppedChatter with a reason code).
    ExtractorError: DegradationMode.INVALID_INPUT_REJECTED,
    FixtureValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    GraphWriteValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    HostedEmbeddingValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    LabelingError: DegradationMode.INVALID_INPUT_REJECTED,
    # LaneAssignmentError fires before any model call or write — dropped
    # material attempting graph entry, laundered backfill flags, forged lane
    # claims — so the operation never starts, same family as the lane policy.
    LaneAssignmentError: DegradationMode.INVALID_INPUT_REJECTED,
    LanePolicyValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    LedgerEventValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    ProvenanceValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    RegistryValidationError: DegradationMode.DRIFT_DETECTED,
    ScopeValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    StoreBoundaryError: DegradationMode.FAIL_CLOSED_REFUSAL,
    VisibilityBoundaryValidationError: DegradationMode.FAIL_CLOSED_REFUSAL,
}

# Substrate error types that ship in a separate, not-yet-merged module
# (cortex#344 lands cortex.hosted.model_interfaces). Registering them only
# when importable cannot misclassify anything: an exception class that does
# not exist cannot be raised. The skip is visible, never silent — it is
# reported by unregistered_optional_failure_sources(), asserted in tests,
# and documented in docs/degradation-modes.md.
OPTIONAL_FAILURE_SOURCES: tuple[tuple[str, str, DegradationMode], ...] = (
    (
        "cortex.hosted.model_interfaces",
        "ModelInterfaceValidationError",
        DegradationMode.INVALID_INPUT_REJECTED,
    ),
)

_UNREGISTERED_OPTIONAL_SOURCES: list[tuple[str, str]] = []


def _register_optional_failure_types() -> None:
    for module_name, class_name, mode in OPTIONAL_FAILURE_SOURCES:
        if importlib.util.find_spec(module_name) is None:
            _UNREGISTERED_OPTIONAL_SOURCES.append((module_name, class_name))
            continue
        module = importlib.import_module(module_name)
        candidate = getattr(module, class_name, None)
        if candidate is None:
            raise DegradationTaxonomyError(
                f"{module_name} is importable but does not define {class_name}; "
                "the degradation taxonomy is out of sync with the substrate"
            )
        if not (isinstance(candidate, type) and issubclass(candidate, BaseException)):
            raise DegradationTaxonomyError(
                f"{module_name}.{class_name} is not an exception type; "
                "the degradation taxonomy is out of sync with the substrate"
            )
        _FAILURE_MODE_BY_TYPE[candidate] = mode


_register_optional_failure_types()


def unregistered_optional_failure_sources() -> tuple[tuple[str, str], ...]:
    """Optional substrate error types that were not importable at load time.

    Non-empty output is a declared reduced capability, not a silent gap:
    a class that does not exist cannot be raised, so nothing raisable is
    left unclassified.
    """

    return tuple(_UNREGISTERED_OPTIONAL_SOURCES)


def classified_failure_types() -> tuple[type[BaseException], ...]:
    """Every exception type the taxonomy classifies, sorted by name."""

    return tuple(sorted(_FAILURE_MODE_BY_TYPE, key=lambda exc_type: exc_type.__qualname__))


def classify_failure(failure: BaseException | AnswerState) -> DegradationMode:
    """Classify a reviewer failure into its visible degradation behavior.

    Accepts a raised substrate exception instance or an ``AnswerState``.
    Unknown exception types raise ``DegradationTaxonomyError`` — an
    unclassified failure must never be treated as benign, so the taxonomy
    forces an explicit mapping before the failure can be handled.
    """

    if isinstance(failure, AnswerState):
        if failure is AnswerState.NO_ANSWER:
            return DegradationMode.FAIL_CLOSED_REFUSAL
        raise DegradationTaxonomyError(
            f"answer state {failure.value!r} is not a failure; "
            "refusing to classify success as degradation"
        )
    mode = _FAILURE_MODE_BY_TYPE.get(type(failure))
    if mode is None:
        raise DegradationTaxonomyError(
            f"unclassified failure type {type(failure).__qualname__}; every reviewer "
            "failure must map to an explicit DegradationMode before it can be "
            "handled (an unknown failure is never classified as benign)"
        ) from failure
    return mode


@dataclass(frozen=True)
class DegradationReport:
    """One visible degradation event, shaped by the no-silent-failure rule.

    The engineering-principles fallback rule: continuing after a failure is
    allowed only when the system reports what failed (``source``), why
    (``reason_code``), and what safety boundary still holds
    (``safety_boundary_held``). A report with ``safety_boundary_held=False``
    is structurally invalid: if no boundary held, the failure is an incident
    that must propagate, not a degradation that may continue.
    """

    mode: DegradationMode
    reason_code: str
    source: str
    safety_boundary_held: bool

    def __post_init__(self) -> None:
        try:
            mode = DegradationMode(self.mode)
        except ValueError as exc:
            raise DegradationTaxonomyError(
                f"unknown degradation mode: {self.mode!r}"
            ) from exc
        object.__setattr__(self, "mode", mode)
        _require_non_empty("reason_code", self.reason_code)
        _require_non_empty("source", self.source)
        object.__setattr__(self, "reason_code", self.reason_code.strip())
        object.__setattr__(self, "source", self.source.strip())
        if self.safety_boundary_held is not True:
            raise DegradationTaxonomyError(
                "a degradation report must attest the safety boundary that still "
                "holds; if no boundary held, raise the failure instead of reporting "
                "a degradation"
            )

    def as_payload(self) -> dict[str, object]:
        """JSON-ready payload for logs, traces, and advisory comments."""

        return {
            "mode": self.mode.value,
            "reason_code": self.reason_code,
            "safety_boundary_held": self.safety_boundary_held,
            "source": self.source,
        }


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise DegradationTaxonomyError(f"{name} must be a non-empty string")
