"""Narrow per-task model interfaces for hosted Cortex (cortex#344).

The Obsidian foundation invariant this preserves: business logic talks to
models through exactly two task-shaped calls — ``derive(...)`` and
``evaluate(...)`` — with **no vendor SDK types and no model-name branching
across the boundary**. Routing (which provider/model serves a request) is
cortex#345's job behind these interfaces; cascade composition is #346;
recorded-response fixtures for CI are #347; the no-vendor-types lint is
#348. This module owns only the boundary shapes.

Every result is model-backed by definition, so every result carries the
atomic ``(model_id, prompt_version)`` pair (same contract the ledger and
the registry enforce) plus a deterministic ``input_hash`` binding the
result to its request — the ``hash(inputs) + model-id + prompt-version``
cache key the roadmap names (consumed by #326's call-site stamping and
#328's banking policy).

Degradation is visible: dropped derive candidates carry reasons, evaluator
omissions carry counts, and nothing here can represent an uncited finding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from cortex.hosted.decisions_for_diff import DecisionsForDiffCandidatePack
from cortex.hosted.eval_fixtures import FindingClass, FixtureScope
from cortex.hosted.model_registry import RegistryValidationError, parse_prompt_version
from cortex.hosted.provenance import SourceDocument, SourceSpan

_SHA256_HEX_LENGTH = 64


class ModelInterfaceValidationError(ValueError):
    """Raised when boundary material cannot support replayable model calls."""


@dataclass(frozen=True)
class DeriveRequest:
    """One derive task: extract decision candidates from one source snapshot."""

    source_document: SourceDocument
    prompt_version: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_prompt_version(self.prompt_version)
        _validate_json_object("metadata", self.metadata)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def input_hash(self) -> str:
        """Deterministic hash over everything the model call depends on."""

        return _hash_mapping(
            {
                "document_hash": self.source_document.document_hash,
                "metadata": dict(self.metadata),
                "prompt_version": self.prompt_version,
                "task": "derive",
            }
        )


@dataclass(frozen=True)
class DeriveCandidate:
    """One proposed decision extracted from the source document."""

    decision_text: str
    spans: tuple[SourceSpan, ...]
    proposed_scopes: tuple[FixtureScope, ...] = ()

    def __post_init__(self) -> None:
        if not self.decision_text.strip():
            raise ModelInterfaceValidationError("decision_text must not be empty")
        if not self.spans:
            raise ModelInterfaceValidationError(
                "derive candidates require at least one source span; "
                "uncited candidates are unrepresentable"
            )

    @property
    def span_hashes(self) -> tuple[str, ...]:
        return tuple(span.span_hash for span in self.spans)


@dataclass(frozen=True)
class DroppedChatter:
    """A visible record of source material derive declined to propose."""

    reason_code: str
    excerpt_hash: str

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ModelInterfaceValidationError("reason_code must not be empty")
        if len(self.excerpt_hash) != _SHA256_HEX_LENGTH:
            raise ModelInterfaceValidationError("excerpt_hash must be a sha256 hex string")


@dataclass(frozen=True)
class DeriveResult:
    """The derive boundary's only output shape."""

    candidates: tuple[DeriveCandidate, ...]
    model_id: str
    prompt_version: str
    input_hash: str
    dropped: tuple[DroppedChatter, ...] = ()
    degraded_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_stamp(self.model_id, self.prompt_version)
        if len(self.input_hash) != _SHA256_HEX_LENGTH:
            raise ModelInterfaceValidationError("input_hash must be a sha256 hex string")
        for reason in self.degraded_reasons:
            if not reason.strip():
                raise ModelInterfaceValidationError("degraded_reasons must be non-empty strings")


@dataclass(frozen=True)
class EvaluateRequest:
    """One evaluate task: judge a diff against its bounded candidate pack."""

    candidate_pack: DecisionsForDiffCandidatePack
    diff_patch: str
    prompt_version: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.diff_patch.strip():
            raise ModelInterfaceValidationError("diff_patch must not be empty")
        _validate_prompt_version(self.prompt_version)
        _validate_json_object("metadata", self.metadata)
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def input_hash(self) -> str:
        return _hash_mapping(
            {
                "candidate_set_hash": self.candidate_pack.candidate_set_hash,
                "diff_hash": hashlib.sha256(self.diff_patch.encode("utf-8")).hexdigest(),
                "metadata": dict(self.metadata),
                "prompt_version": self.prompt_version,
                "task": "evaluate",
            }
        )


@dataclass(frozen=True)
class FindingDraft:
    """One advisory finding the evaluator proposes for a diff."""

    finding_class: FindingClass
    decision_node_id: str
    cited_span_hashes: tuple[str, ...]
    summary: str
    confidence_label: str
    suggested_repair: str | None = None

    def __post_init__(self) -> None:
        if not self.decision_node_id.strip():
            raise ModelInterfaceValidationError("decision_node_id must not be empty")
        if not self.cited_span_hashes:
            raise ModelInterfaceValidationError(
                "findings require at least one cited span hash; "
                "uncited findings are unrepresentable"
            )
        for value in self.cited_span_hashes:
            if len(value) != _SHA256_HEX_LENGTH:
                raise ModelInterfaceValidationError(
                    "cited_span_hashes must be sha256 hex strings"
                )
        if not self.summary.strip():
            raise ModelInterfaceValidationError("summary must not be empty")
        if not self.confidence_label.strip():
            raise ModelInterfaceValidationError(
                "confidence_label must not be empty (#375 owns the ladder vocabulary)"
            )
        if self.suggested_repair is not None and not self.suggested_repair.strip():
            raise ModelInterfaceValidationError("suggested_repair must not be empty when present")


@dataclass(frozen=True)
class EvaluateResult:
    """The evaluate boundary's only output shape."""

    findings: tuple[FindingDraft, ...]
    model_id: str
    prompt_version: str
    input_hash: str
    omitted_decision_count: int = 0
    degraded_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_stamp(self.model_id, self.prompt_version)
        if len(self.input_hash) != _SHA256_HEX_LENGTH:
            raise ModelInterfaceValidationError("input_hash must be a sha256 hex string")
        if self.omitted_decision_count < 0:
            raise ModelInterfaceValidationError("omitted_decision_count must be >= 0")
        for reason in self.degraded_reasons:
            if not reason.strip():
                raise ModelInterfaceValidationError("degraded_reasons must be non-empty strings")


@runtime_checkable
class DeriveModel(Protocol):
    """Anything that can run the derive task behind the boundary."""

    def derive(self, request: DeriveRequest) -> DeriveResult: ...


@runtime_checkable
class EvaluateModel(Protocol):
    """Anything that can run the evaluate task behind the boundary."""

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult: ...


def ensure_result_binds_request(
    request: DeriveRequest | EvaluateRequest,
    result: DeriveResult | EvaluateResult,
) -> None:
    """Fail visibly when a result does not bind its request.

    Replay and banking are meaningless if a result can drift from the inputs
    it claims to answer; callers check this before persisting anything.
    """

    if result.input_hash != request.input_hash:
        raise ModelInterfaceValidationError(
            "result input_hash does not match request input_hash; refusing to "
            "treat the result as an answer to this request"
        )
    if result.prompt_version != request.prompt_version:
        raise ModelInterfaceValidationError(
            "result prompt_version does not match request prompt_version"
        )


def _validate_stamp(model_id: str, prompt_version: str) -> None:
    if not model_id.strip():
        raise ModelInterfaceValidationError(
            "model_id must not be empty; results are model-backed by definition"
        )
    _validate_prompt_version(prompt_version)


def _validate_prompt_version(value: str) -> None:
    try:
        parse_prompt_version(value)
    except RegistryValidationError as exc:
        raise ModelInterfaceValidationError(str(exc)) from exc


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise ModelInterfaceValidationError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ModelInterfaceValidationError(f"{name} must be JSON-serializable") from exc


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
