"""Cascade inference for extraction and evaluation (cortex#346).

Composition, not configuration: a cascade is itself a ``DeriveModel`` /
``EvaluateModel`` wrapping a cheap *primary* and a stronger *escalator*
behind the same narrow boundary (cortex#344), so business logic above the
boundary cannot tell a cascade from a single model — no model-name
branching leaks upward.

Escalation is policy-driven and **visible**: every cascade call returns
the chosen result unchanged plus records a ``CascadeTrace`` naming which
leg answered and exactly why escalation fired (or didn't). Cost
accounting needs no help here — each leg is a routed model whose calls
the router already records (#335); the trace ties the two records to one
logical call via the request's ``input_hash``.

Escalation triggers (Stage 0 policy, versioned):

- ``degraded-primary`` — the primary result carries ``degraded_reasons``.
- ``empty-on-nonempty-pack`` (evaluate only, opt-in) — zero findings
  against a non-empty candidate pack; the cheap model saying "nothing to
  see" about a pack retrieval considered relevant is exactly when the
  strong model earns its cost.

A primary *transport failure* is not handled here: adapters raise, and
refusing to silently absorb a broken adapter is the routing layer's
documented stance. The cascade composes results, not exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cortex.hosted.model_interfaces import (
    DeriveModel,
    DeriveRequest,
    DeriveResult,
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
    ensure_result_binds_request,
)

CASCADE_POLICY_VERSION = 1

ESCALATE_DEGRADED_PRIMARY = "degraded-primary"
ESCALATE_EMPTY_ON_NONEMPTY_PACK = "empty-on-nonempty-pack"
_KNOWN_REASONS = frozenset({ESCALATE_DEGRADED_PRIMARY, ESCALATE_EMPTY_ON_NONEMPTY_PACK})


class CascadeValidationError(ValueError):
    """Raised when a cascade cannot be composed or traced coherently."""


@dataclass(frozen=True)
class CascadePolicy:
    """Which conditions send a request to the escalator."""

    escalate_on_degraded: bool = True
    escalate_on_empty_findings: bool = False

    def evaluate_reasons(
        self, request: EvaluateRequest, primary: EvaluateResult
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.escalate_on_degraded and primary.degraded_reasons:
            reasons.append(ESCALATE_DEGRADED_PRIMARY)
        if (
            self.escalate_on_empty_findings
            and not primary.findings
            and request.candidate_pack.candidates
        ):
            reasons.append(ESCALATE_EMPTY_ON_NONEMPTY_PACK)
        return tuple(reasons)

    def derive_reasons(self, primary: DeriveResult) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.escalate_on_degraded and primary.degraded_reasons:
            reasons.append(ESCALATE_DEGRADED_PRIMARY)
        return tuple(reasons)


@dataclass(frozen=True)
class CascadeTrace:
    """The visible record of one cascade decision."""

    task: str
    input_hash: str
    answered_by: str  # "primary" | "escalator"
    escalation_reasons: tuple[str, ...]
    primary_model_id: str
    escalator_model_id: str | None

    def __post_init__(self) -> None:
        if self.task not in ("derive", "evaluate"):
            raise CascadeValidationError(f"unknown task {self.task!r}")
        if self.answered_by not in ("primary", "escalator"):
            raise CascadeValidationError(f"unknown answered_by {self.answered_by!r}")
        unknown = set(self.escalation_reasons) - _KNOWN_REASONS
        if unknown:
            raise CascadeValidationError(f"unknown escalation reasons: {sorted(unknown)}")
        if self.answered_by == "escalator" and not self.escalation_reasons:
            raise CascadeValidationError("escalation must carry at least one reason")
        if self.answered_by == "primary" and self.escalation_reasons:
            raise CascadeValidationError(
                "a primary answer cannot carry escalation reasons"
            )

    def as_payload(self) -> dict[str, object]:
        return {
            "answered_by": self.answered_by,
            "escalation_reasons": list(self.escalation_reasons),
            "escalator_model_id": self.escalator_model_id,
            "input_hash": self.input_hash,
            "policy_version": CASCADE_POLICY_VERSION,
            "primary_model_id": self.primary_model_id,
            "task": self.task,
        }


@dataclass
class CascadeModel:
    """A DeriveModel + EvaluateModel composed from primary and escalator legs."""

    primary: DeriveModel | EvaluateModel
    escalator: DeriveModel | EvaluateModel
    policy: CascadePolicy = field(default_factory=CascadePolicy)

    def __post_init__(self) -> None:
        self._traces: list[CascadeTrace] = []

    @property
    def traces(self) -> tuple[CascadeTrace, ...]:
        return tuple(self._traces)

    def derive(self, request: DeriveRequest) -> DeriveResult:
        primary_model = self._require("primary", "derive")
        result = primary_model.derive(request)  # type: ignore[union-attr]
        ensure_result_binds_request(request, result)
        reasons = self.policy.derive_reasons(result)
        if not reasons:
            self._trace("derive", request.input_hash, "primary", (), result.model_id, None)
            return result
        escalator_model = self._require("escalator", "derive")
        escalated = escalator_model.derive(request)  # type: ignore[union-attr]
        ensure_result_binds_request(request, escalated)
        self._trace(
            "derive", request.input_hash, "escalator", reasons,
            result.model_id, escalated.model_id,
        )
        return escalated

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        primary_model = self._require("primary", "evaluate")
        result = primary_model.evaluate(request)  # type: ignore[union-attr]
        ensure_result_binds_request(request, result)
        reasons = self.policy.evaluate_reasons(request, result)
        if not reasons:
            self._trace("evaluate", request.input_hash, "primary", (), result.model_id, None)
            return result
        escalator_model = self._require("escalator", "evaluate")
        escalated = escalator_model.evaluate(request)  # type: ignore[union-attr]
        ensure_result_binds_request(request, escalated)
        self._trace(
            "evaluate", request.input_hash, "escalator", reasons,
            result.model_id, escalated.model_id,
        )
        return escalated

    def _require(self, leg: str, method: str) -> DeriveModel | EvaluateModel:
        model = getattr(self, leg)
        if not hasattr(model, method):
            raise CascadeValidationError(
                f"{leg} model does not implement {method}(); compose task-matching legs"
            )
        return model  # type: ignore[no-any-return]

    def _trace(
        self,
        task: str,
        input_hash: str,
        answered_by: str,
        reasons: tuple[str, ...],
        primary_model_id: str,
        escalator_model_id: str | None,
    ) -> None:
        self._traces.append(
            CascadeTrace(
                task=task,
                input_hash=input_hash,
                answered_by=answered_by,
                escalation_reasons=reasons,
                primary_model_id=primary_model_id,
                escalator_model_id=escalator_model_id,
            )
        )
