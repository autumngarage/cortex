"""Provider-agnostic task routing behind the model boundary (cortex#345).

Scope boundary (from the issue body): this routing layer lives in the hosted
product surface only. The repo rule that **CLI synthesis shells out directly
to the ``claude`` CLI (no SDK, no provider abstraction)** is untouched — this
module serves the hosted reviewer/ledger product, not ``cortex`` CLI
commands. No vendor SDK is imported anywhere here; the only live transport is
the ``claude`` CLI via subprocess, plus recorded-response playback.

The contract:

- Business logic talks to :class:`ModelRouter` only through the cortex#344
  ``DeriveModel`` / ``EvaluateModel`` protocols. Adapter types, provider
  names, and model-name branching never cross the boundary.
- Task kind → (adapter, model, params) resolution comes from a
  :class:`RouteTable` that is configuration (canonical JSON), not code.
  Changing a route is a config edit; adding a provider is a new adapter
  registration — callers of ``derive(...)``/``evaluate(...)`` never change.
- Every result is stamped registry-validated via
  ``ModelPromptRegistry.stamp`` and bound to its request via
  ``ensure_result_binds_request`` before the router returns it; drifted
  results are refused loudly.
- Fallback between routes is visible: every fallback emits a log line and a
  :class:`RouteFallbackRecord` naming what failed and what route ran
  instead. No silent fallback.
- Every routed call appends exactly one :class:`~cortex.hosted.cost.CostRecord`
  to the run ledger — failures record too, marked (cortex#335). A
  predictable budget breach raises ``BudgetExceededError`` *before* the
  adapter is invoked, in which case no call happened and no record is
  written.

Recorded-response payloads (``derive_result_as_payload`` /
``derive_result_from_payload`` and the evaluate twins) are the ONE recording
format. cortex#347's record/replay harness imports these from here (this
module never imports from #347's), so recording and playback cannot drift
into two formats.

Cascade escalation (cheap-first, escalate on low confidence) is cortex#346;
the lint/CI enforcement of the no-vendor-SDK boundary is cortex#348.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from cortex.hosted.cost import (
    CallOutcome,
    CostBasis,
    CostRecord,
    RunLedger,
    TokenUsage,
)
from cortex.hosted.eval_fixtures import FindingClass, FixtureScope, FixtureValidationError
from cortex.hosted.evaluator import evaluate_prompt_guidance
from cortex.hosted.model_interfaces import (
    DeriveCandidate,
    DeriveRequest,
    DeriveResult,
    DroppedChatter,
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
    ModelInterfaceValidationError,
    ensure_result_binds_request,
)
from cortex.hosted.model_registry import ModelPromptRegistry, RegistryValidationError
from cortex.hosted.provenance import ProvenanceValidationError, SourceSpan, content_hash
from cortex.hosted.recorded_responses import (
    derive_result_as_payload,
    derive_result_from_payload,
    evaluate_result_as_payload,
    evaluate_result_from_payload,
)

_LOGGER = logging.getLogger(__name__)

ROUTE_TABLE_SCHEMA_VERSION = 1
RECORDED_RESPONSES_SCHEMA_VERSION = 1
DEFAULT_CLAUDE_BINARY = "claude"
DEFAULT_CLAUDE_TIMEOUT_SECONDS = 600.0
_OUTPUT_EXCERPT_CHARS = 240


class RoutingError(ValueError):
    """Raised when a route cannot produce a trustworthy, bound result."""


class ClaudeCliUnavailableError(RoutingError):
    """Raised at call time when the claude CLI binary is not on PATH."""


class ClaudeCliOutputError(RoutingError):
    """Raised when claude CLI output violates the JSON contract.

    Carries the transport-reported ``usage`` when the envelope parsed far
    enough to report tokens, so the failed call's cost record can still
    account for the spend.
    """

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class RecordedResponseMissingError(RoutingError):
    """Raised when playback has no recording for a request."""


class TaskKind(StrEnum):
    """Stage 0 task vocabulary, matching the cortex#344 boundary calls."""

    DERIVE = "derive"
    EVALUATE = "evaluate"


@dataclass(frozen=True)
class RouteConfig:
    """One configured route: a task kind served by an adapter and model."""

    task_kind: TaskKind
    model_id: str
    adapter_id: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise RoutingError("route model_id must not be empty")
        if not self.adapter_id.strip():
            raise RoutingError("route adapter_id must not be empty")
        _validate_json_object("params", self.params)
        object.__setattr__(self, "params", dict(self.params))

    def as_payload(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "model_id": self.model_id,
            "params": dict(self.params),
            "task_kind": self.task_kind.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RouteConfig:
        raw_task = _get_str(payload, "task_kind")
        try:
            task_kind = TaskKind(raw_task)
        except ValueError as exc:
            raise RoutingError(f"unknown task_kind {raw_task!r} in route config") from exc
        params = payload.get("params", {})
        if not isinstance(params, Mapping):
            raise RoutingError("route params must be a JSON object")
        return cls(
            task_kind=task_kind,
            model_id=_get_str(payload, "model_id"),
            adapter_id=_get_str(payload, "adapter_id"),
            params=params,
        )


@dataclass(frozen=True)
class RouteTable:
    """Ordered routes per task kind: primary first, fallbacks after.

    The table is configuration, not code — it round-trips through canonical
    JSON so a deployment changes routes by editing config only.
    """

    routes: tuple[RouteConfig, ...]

    def __post_init__(self) -> None:
        seen: set[tuple[str, str, str]] = set()
        for route in self.routes:
            key = (route.task_kind.value, route.adapter_id, route.model_id)
            if key in seen:
                raise RoutingError(
                    f"duplicate route for task {route.task_kind.value!r} via "
                    f"adapter {route.adapter_id!r} and model {route.model_id!r}"
                )
            seen.add(key)

    def routes_for(self, task_kind: TaskKind) -> tuple[RouteConfig, ...]:
        return tuple(route for route in self.routes if route.task_kind is task_kind)

    def as_payload(self) -> dict[str, Any]:
        return {
            "route_table_schema_version": ROUTE_TABLE_SCHEMA_VERSION,
            "routes": [route.as_payload() for route in self.routes],
        }

    def to_canonical_json(self) -> str:
        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RouteTable:
        if not isinstance(payload, Mapping):
            raise RoutingError("route table payload must be a JSON object")
        raw_version = payload.get("route_table_schema_version")
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise RoutingError("route_table_schema_version must be an integer")
        if raw_version != ROUTE_TABLE_SCHEMA_VERSION:
            raise RoutingError(
                f"unknown route_table_schema_version {raw_version!r}; this loader "
                f"supports only {ROUTE_TABLE_SCHEMA_VERSION} — no silent fallback"
            )
        return cls(
            routes=tuple(
                RouteConfig.from_payload(item)
                for item in _get_object_list(payload, "routes")
            )
        )

    @classmethod
    def from_json(cls, text: str) -> RouteTable:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RoutingError(f"route table is not valid JSON: {exc}") from exc
        return cls.from_payload(payload)


@dataclass(frozen=True)
class AdapterOutcome:
    """What an adapter hands back to the router: result + cost visibility."""

    result: DeriveResult | EvaluateResult
    cost_basis: CostBasis
    usage: TokenUsage | None = None

    def __post_init__(self) -> None:
        if self.cost_basis is CostBasis.REPORTED_TOKENS and self.usage is None:
            raise RoutingError(
                "adapters claiming 'reported-tokens' must supply usage"
            )
        if self.cost_basis is not CostBasis.REPORTED_TOKENS and self.usage is not None:
            raise RoutingError(
                f"adapters must not supply usage with cost_basis {self.cost_basis.value!r}"
            )


class ProviderAdapter(Protocol):
    """The only shape the router needs from a provider integration."""

    def run_derive(self, request: DeriveRequest, route: RouteConfig) -> AdapterOutcome: ...

    def run_evaluate(self, request: EvaluateRequest, route: RouteConfig) -> AdapterOutcome: ...


@dataclass(frozen=True)
class RouteFallbackRecord:
    """Visible record of one fallback: what failed, what ran instead."""

    task_kind: TaskKind
    failed_adapter_id: str
    failed_model_id: str
    failure: str
    fallback_adapter_id: str
    fallback_model_id: str

    def __post_init__(self) -> None:
        if not self.failure.strip():
            raise RoutingError("fallback records must name the failure")


class ModelRouter:
    """Routes derive/evaluate requests to configured adapters.

    Satisfies the ``DeriveModel`` and ``EvaluateModel`` protocols, so
    business logic depends only on the cortex#344 boundary — never on
    adapter types or model names.
    """

    def __init__(
        self,
        *,
        route_table: RouteTable,
        adapters: Mapping[str, ProviderAdapter],
        registry: ModelPromptRegistry,
        ledger: RunLedger,
    ) -> None:
        for route in route_table.routes:
            if route.adapter_id not in adapters:
                raise RoutingError(
                    f"route for task {route.task_kind.value!r} names adapter "
                    f"{route.adapter_id!r}, which is not registered with this router"
                )
            # Fail at construction, not mid-run, when config names an
            # unregistered model (cortex#327 registry is the authority).
            registry.resolve_model(route.model_id)
        self._route_table = route_table
        self._adapters = dict(adapters)
        self._registry = registry
        self._ledger = ledger
        self._fallback_records: list[RouteFallbackRecord] = []

    @property
    def fallback_records(self) -> tuple[RouteFallbackRecord, ...]:
        return tuple(self._fallback_records)

    def derive(self, request: DeriveRequest) -> DeriveResult:
        result = self._run(TaskKind.DERIVE, request)
        if not isinstance(result, DeriveResult):
            raise RoutingError("adapter returned a non-derive result for a derive task")
        return result

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        result = self._run(TaskKind.EVALUATE, request)
        if not isinstance(result, EvaluateResult):
            raise RoutingError("adapter returned a non-evaluate result for an evaluate task")
        return result

    def _run(
        self, task_kind: TaskKind, request: DeriveRequest | EvaluateRequest
    ) -> DeriveResult | EvaluateResult:
        routes = self._route_table.routes_for(task_kind)
        if not routes:
            raise RoutingError(f"no route configured for task {task_kind.value!r}")
        failures: list[str] = []
        for index, route in enumerate(routes):
            # Predictable budget breaches refuse the call up front; no call,
            # no cost record (cortex#335).
            self._ledger.ensure_budget_allows_call(task_kind=task_kind.value)
            adapter = self._adapters[route.adapter_id]
            started = time.monotonic()
            try:
                outcome = (
                    adapter.run_derive(request, route)
                    if isinstance(request, DeriveRequest)
                    else adapter.run_evaluate(request, route)
                )
            except RoutingError as exc:
                self._record_call(
                    task_kind,
                    route,
                    request,
                    started,
                    usage=getattr(exc, "usage", None),
                    failure=str(exc),
                )
                failures.append(
                    f"{route.adapter_id}/{route.model_id}: {exc}"
                )
                next_route = routes[index + 1] if index + 1 < len(routes) else None
                if next_route is None:
                    raise RoutingError(
                        f"all {len(routes)} route(s) failed for task "
                        f"{task_kind.value!r}: " + "; ".join(failures)
                    ) from exc
                self._note_fallback(task_kind, route, next_route, exc)
                continue
            try:
                self._validate_outcome(task_kind, route, request, outcome)
            except (RegistryValidationError, ModelInterfaceValidationError, RoutingError) as exc:
                # A response that fails stamping/binding is an integrity
                # violation, not a transport failure — record it and raise;
                # falling back could mask a broken adapter contract.
                self._record_call(
                    task_kind,
                    route,
                    request,
                    started,
                    usage=outcome.usage,
                    failure=str(exc),
                    cost_basis=outcome.cost_basis,
                )
                raise
            self._record_call(
                task_kind,
                route,
                request,
                started,
                usage=outcome.usage,
                cost_basis=outcome.cost_basis,
            )
            return outcome.result
        raise RoutingError(f"no route produced a result for task {task_kind.value!r}")

    def _validate_outcome(
        self,
        task_kind: TaskKind,
        route: RouteConfig,
        request: DeriveRequest | EvaluateRequest,
        outcome: AdapterOutcome,
    ) -> None:
        result = outcome.result
        expected_type: type = DeriveResult if task_kind is TaskKind.DERIVE else EvaluateResult
        if not isinstance(result, expected_type):
            raise RoutingError(
                f"adapter {route.adapter_id!r} returned "
                f"{type(result).__name__} for task {task_kind.value!r}"
            )
        if result.model_id != route.model_id:
            raise RoutingError(
                f"adapter {route.adapter_id!r} stamped model {result.model_id!r} "
                f"but the route is {route.model_id!r}; refusing drifted stamping"
            )
        # Registry-validated (model_id, prompt_version) stamping (cortex#326/#327).
        self._registry.stamp(model_id=result.model_id, prompt_version=result.prompt_version)
        # Bind the result to its request before anything downstream sees it.
        ensure_result_binds_request(request, result)

    def _record_call(
        self,
        task_kind: TaskKind,
        route: RouteConfig,
        request: DeriveRequest | EvaluateRequest,
        started: float,
        *,
        usage: TokenUsage | None,
        failure: str | None = None,
        cost_basis: CostBasis | None = None,
    ) -> None:
        if cost_basis is None:
            cost_basis = (
                CostBasis.REPORTED_TOKENS if usage is not None else CostBasis.NO_RESPONSE
            )
        wall_ms = int((time.monotonic() - started) * 1000)
        self._ledger.append(
            CostRecord(
                task_kind=task_kind.value,
                model_id=route.model_id,
                prompt_version=request.prompt_version,
                input_hash=request.input_hash,
                cost_basis=cost_basis,
                usage=usage if cost_basis is CostBasis.REPORTED_TOKENS else None,
                wall_ms=wall_ms,
                outcome=CallOutcome.FAILED if failure is not None else CallOutcome.OK,
                failure_reason=failure,
            )
        )

    def _note_fallback(
        self,
        task_kind: TaskKind,
        failed: RouteConfig,
        fallback: RouteConfig,
        error: RoutingError,
    ) -> None:
        record = RouteFallbackRecord(
            task_kind=task_kind,
            failed_adapter_id=failed.adapter_id,
            failed_model_id=failed.model_id,
            failure=str(error),
            fallback_adapter_id=fallback.adapter_id,
            fallback_model_id=fallback.model_id,
        )
        self._fallback_records.append(record)
        _LOGGER.warning(
            "route fallback for task %s: %s/%s failed (%s); falling back to %s/%s",
            task_kind.value,
            failed.adapter_id,
            failed.model_id,
            error,
            fallback.adapter_id,
            fallback.model_id,
        )


@dataclass(frozen=True)
class ClaudeCliAdapter:
    """Shells out to the ``claude`` CLI with a strict JSON-output contract.

    Transport contract: ``claude -p --output-format json`` with the prompt on
    stdin. The CLI prints one JSON envelope on stdout; this adapter requires
    ``result`` (the model's text, itself required to be a single JSON object
    per the task contracts below) and reads ``usage.input_tokens`` /
    ``usage.output_tokens`` when present. A missing usage block degrades
    visibly to ``CostBasis.UNREPORTED_TOKENS`` — never to zero tokens. The
    envelope's ``total_cost_usd`` is deliberately ignored: the versioned
    price table in ``cortex.hosted.cost`` is the only cost basis (#335).

    The binary is **not** required at import or construction time; absence
    is detected at call time and raised as
    :class:`ClaudeCliUnavailableError` naming the missing binary.
    """

    binary: str = DEFAULT_CLAUDE_BINARY
    timeout_seconds: float = DEFAULT_CLAUDE_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not self.binary.strip():
            raise RoutingError("claude CLI binary name must not be empty")
        if self.timeout_seconds <= 0:
            raise RoutingError("timeout_seconds must be positive")

    def run_derive(self, request: DeriveRequest, route: RouteConfig) -> AdapterOutcome:
        model_text, usage = self._invoke(_derive_prompt(request))
        try:
            payload = _json_object(model_text, context="claude CLI derive output")
            result = _derive_result_from_model_payload(
                payload, request=request, model_id=route.model_id
            )
        except (RoutingError, ModelInterfaceValidationError, ProvenanceValidationError) as exc:
            raise ClaudeCliOutputError(
                f"claude CLI derive output violated the contract: {exc}", usage=usage
            ) from exc
        return AdapterOutcome(
            result=result,
            cost_basis=(
                CostBasis.REPORTED_TOKENS if usage is not None else CostBasis.UNREPORTED_TOKENS
            ),
            usage=usage,
        )

    def run_evaluate(self, request: EvaluateRequest, route: RouteConfig) -> AdapterOutcome:
        model_text, usage = self._invoke(_evaluate_prompt(request))
        try:
            payload = _json_object(model_text, context="claude CLI evaluate output")
            result = _evaluate_result_from_model_payload(
                payload, request=request, model_id=route.model_id
            )
        except (RoutingError, ModelInterfaceValidationError) as exc:
            raise ClaudeCliOutputError(
                f"claude CLI evaluate output violated the contract: {exc}", usage=usage
            ) from exc
        return AdapterOutcome(
            result=result,
            cost_basis=(
                CostBasis.REPORTED_TOKENS if usage is not None else CostBasis.UNREPORTED_TOKENS
            ),
            usage=usage,
        )

    def _invoke(self, prompt: str) -> tuple[str, TokenUsage | None]:
        resolved = shutil.which(self.binary)
        if resolved is None:
            raise ClaudeCliUnavailableError(
                f"claude CLI binary {self.binary!r} not found on PATH; install "
                "the claude CLI or route this task to a different adapter"
            )
        try:
            completed = subprocess.run(
                [resolved, "-p", "--output-format", "json"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliOutputError(
                f"claude CLI timed out after {self.timeout_seconds}s"
            ) from exc
        if completed.returncode != 0:
            raise ClaudeCliOutputError(
                f"claude CLI exited {completed.returncode}; stderr: "
                f"{_excerpt(completed.stderr)}"
            )
        envelope = _json_object(completed.stdout, context="claude CLI stdout envelope")
        usage = _usage_from_envelope(envelope)
        if envelope.get("is_error") is True:
            raise ClaudeCliOutputError(
                f"claude CLI reported an error result: {_excerpt(str(envelope.get('result')))}",
                usage=usage,
            )
        result_text = envelope.get("result")
        if not isinstance(result_text, str):
            raise ClaudeCliOutputError(
                "claude CLI envelope is missing a string 'result' field; got "
                f"{type(result_text).__name__}",
                usage=usage,
            )
        return result_text, usage


class RecordedResponseAdapter:
    """Plays back recorded results keyed by ``(task, input_hash)``.

    This is the playback half of the seam cortex#347 formalizes (fixture
    locations, record/replay toggles, CI pinning). The recording format is
    the payload shape produced by ``derive_result_as_payload`` /
    ``evaluate_result_as_payload`` in this module — one format, owned by
    by #347's harness. A missing recording is a hard, named failure, never
    a silent live call.
    """

    def __init__(
        self,
        *,
        derive_results: Iterable[DeriveResult] = (),
        evaluate_results: Iterable[EvaluateResult] = (),
    ) -> None:
        self._derive: dict[str, DeriveResult] = {}
        self._evaluate: dict[str, EvaluateResult] = {}
        for derive_result in derive_results:
            if derive_result.input_hash in self._derive:
                raise RoutingError(
                    f"duplicate recorded derive response for input_hash "
                    f"{derive_result.input_hash!r}"
                )
            self._derive[derive_result.input_hash] = derive_result
        for evaluate_result in evaluate_results:
            if evaluate_result.input_hash in self._evaluate:
                raise RoutingError(
                    f"duplicate recorded evaluate response for input_hash "
                    f"{evaluate_result.input_hash!r}"
                )
            self._evaluate[evaluate_result.input_hash] = evaluate_result

    def run_derive(self, request: DeriveRequest, route: RouteConfig) -> AdapterOutcome:
        result = self._derive.get(request.input_hash)
        if result is None:
            raise RecordedResponseMissingError(
                f"no recorded derive response for input_hash {request.input_hash!r}; "
                "re-record fixtures at the boundary (cortex#347) — playback never "
                "falls back to a live call"
            )
        self._check_route_match(result.model_id, route)
        return AdapterOutcome(result=result, cost_basis=CostBasis.RECORDED_PLAYBACK)

    def run_evaluate(self, request: EvaluateRequest, route: RouteConfig) -> AdapterOutcome:
        result = self._evaluate.get(request.input_hash)
        if result is None:
            raise RecordedResponseMissingError(
                f"no recorded evaluate response for input_hash {request.input_hash!r}; "
                "re-record fixtures at the boundary (cortex#347) — playback never "
                "falls back to a live call"
            )
        self._check_route_match(result.model_id, route)
        return AdapterOutcome(result=result, cost_basis=CostBasis.RECORDED_PLAYBACK)

    @staticmethod
    def _check_route_match(recorded_model_id: str, route: RouteConfig) -> None:
        if recorded_model_id != route.model_id:
            raise RecordedResponseMissingError(
                f"recording was made with model {recorded_model_id!r} but the "
                f"route asks for {route.model_id!r}; stale recordings are a "
                "visible failure, not a substitute"
            )

    def as_payload(self) -> dict[str, Any]:
        return {
            "derive": [
                derive_result_as_payload(self._derive[key]) for key in sorted(self._derive)
            ],
            "evaluate": [
                evaluate_result_as_payload(self._evaluate[key])
                for key in sorted(self._evaluate)
            ],
            "recorded_responses_schema_version": RECORDED_RESPONSES_SCHEMA_VERSION,
        }

    def to_canonical_json(self) -> str:
        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RecordedResponseAdapter:
        if not isinstance(payload, Mapping):
            raise RoutingError("recorded responses payload must be a JSON object")
        raw_version = payload.get("recorded_responses_schema_version")
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise RoutingError("recorded_responses_schema_version must be an integer")
        if raw_version != RECORDED_RESPONSES_SCHEMA_VERSION:
            raise RoutingError(
                f"unknown recorded_responses_schema_version {raw_version!r}; this "
                f"loader supports only {RECORDED_RESPONSES_SCHEMA_VERSION} — no "
                "silent fallback"
            )
        return cls(
            derive_results=[
                derive_result_from_payload(item)
                for item in _get_object_list(payload, "derive")
            ],
            evaluate_results=[
                evaluate_result_from_payload(item)
                for item in _get_object_list(payload, "evaluate")
            ],
        )

    @classmethod
    def from_json(cls, text: str) -> RecordedResponseAdapter:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RoutingError(f"recorded responses file is not valid JSON: {exc}") from exc
        return cls.from_payload(payload)


# --- The one recording format (#347 imports these; never the reverse) -------


def _candidate_as_payload(candidate: DeriveCandidate) -> dict[str, Any]:
    return {
        "decision_text": candidate.decision_text,
        "proposed_scopes": [scope.as_payload() for scope in candidate.proposed_scopes],
        "spans": [_span_as_payload(span) for span in candidate.spans],
    }


def _candidate_from_payload(payload: Mapping[str, Any]) -> DeriveCandidate:
    try:
        scopes = tuple(
            FixtureScope.from_payload(item)
            for item in _get_object_list(payload, "proposed_scopes")
        )
    except FixtureValidationError as exc:
        raise RoutingError(f"recorded candidate scope is invalid: {exc}") from exc
    return DeriveCandidate(
        decision_text=_get_str(payload, "decision_text"),
        spans=tuple(
            _span_from_payload(item) for item in _get_object_list(payload, "spans")
        ),
        proposed_scopes=scopes,
    )


def _span_as_payload(span: SourceSpan) -> dict[str, Any]:
    return {
        "end_offset": span.end_offset,
        "excerpt": span.excerpt,
        "permalink": span.permalink,
        "source_document_hash": span.source_document_hash,
        "span_hash": span.span_hash,
        "start_offset": span.start_offset,
        "tenant_id": span.tenant_id,
    }


def _span_from_payload(payload: Mapping[str, Any]) -> SourceSpan:
    try:
        span = SourceSpan(
            tenant_id=_get_str(payload, "tenant_id"),
            source_document_hash=_get_str(payload, "source_document_hash"),
            start_offset=_get_int(payload, "start_offset"),
            end_offset=_get_int(payload, "end_offset"),
            excerpt=_get_str(payload, "excerpt"),
            permalink=_get_str(payload, "permalink"),
        )
    except ProvenanceValidationError as exc:
        raise RoutingError(f"recorded span is invalid: {exc}") from exc
    recorded_hash = payload.get("span_hash")
    if recorded_hash is not None and recorded_hash != span.span_hash:
        raise RoutingError(
            "recorded span_hash does not match the span content — the recording "
            "drifted; re-record instead of trusting it"
        )
    return span


def _finding_as_payload(finding: FindingDraft) -> dict[str, Any]:
    return {
        "cited_span_hashes": list(finding.cited_span_hashes),
        "confidence_label": finding.confidence_label,
        "decision_node_id": finding.decision_node_id,
        "finding_class": finding.finding_class.value,
        "suggested_repair": finding.suggested_repair,
        "summary": finding.summary,
    }


def _finding_from_payload(payload: Mapping[str, Any]) -> FindingDraft:
    raw_class = _get_str(payload, "finding_class")
    try:
        finding_class = FindingClass(raw_class)
    except ValueError as exc:
        raise RoutingError(f"unknown finding_class {raw_class!r}") from exc
    suggested = payload.get("suggested_repair")
    if suggested is not None and not isinstance(suggested, str):
        raise RoutingError("suggested_repair must be a string or null")
    return FindingDraft(
        finding_class=finding_class,
        decision_node_id=_get_str(payload, "decision_node_id"),
        cited_span_hashes=_get_str_tuple(payload, "cited_span_hashes"),
        summary=_get_str(payload, "summary"),
        confidence_label=_get_str(payload, "confidence_label"),
        suggested_repair=suggested,
    )


# --- claude CLI model-output contract ----------------------------------------


def _derive_prompt(request: DeriveRequest) -> str:
    """Render the derive task as a strict-JSON instruction prompt.

    Registry-template rendering integrates when the derive pipeline
    (cortex#350) lands; the stamped ``prompt_version`` already identifies
    the prompt contract for replay either way.
    """

    document = request.source_document
    task = {
        "document": {
            "content": document.content,
            "document_type": document.document_type,
            "external_id": document.external_id,
        },
        "metadata": dict(request.metadata),
        "task": "derive",
    }
    return (
        "Extract decision candidates from the source document below.\n"
        "Respond with ONLY one JSON object, no prose, shaped exactly as:\n"
        '{"candidates": [{"decision_text": str, '
        '"spans": [{"start_offset": int, "end_offset": int}]}], '
        '"dropped": [{"reason_code": str, "excerpt": str}], '
        '"degraded_reasons": [str]}\n'
        "Offsets index into document.content. Every candidate must cite at "
        "least one span.\n\n"
        + json.dumps(task, sort_keys=True, ensure_ascii=False, indent=2)
    )


def _evaluate_prompt(request: EvaluateRequest) -> str:
    """Render the evaluate task as a strict-JSON instruction prompt."""

    pack = request.candidate_pack
    task = {
        "decisions": [
            {
                "decision_node_id": candidate.decision_node_id,
                "decision_text": candidate.decision_text,
                "span_hashes": [span.span_hash for span in candidate.cited_spans],
                "status": candidate.status,
            }
            for candidate in pack.candidates
        ],
        "diff_patch": request.diff_patch,
        "metadata": dict(request.metadata),
        "task": "evaluate",
    }
    return (
        "Judge whether the diff conflicts with the decisions below.\n"
        "Respond with ONLY one JSON object, no prose, shaped exactly as:\n"
        '{"findings": [{"finding_class": str, "decision_node_id": str, '
        '"cited_span_hashes": [str], "summary": str, "confidence_label": str, '
        '"suggested_repair": str | null}], '
        '"omitted_decision_count": int, "degraded_reasons": [str]}\n'
        + evaluate_prompt_guidance()
        + "\n\n"
        + json.dumps(task, sort_keys=True, ensure_ascii=False, indent=2)
    )


def _derive_result_from_model_payload(
    payload: Mapping[str, Any], *, request: DeriveRequest, model_id: str
) -> DeriveResult:
    """Build a bound DeriveResult from model output.

    Spans are rebuilt through ``SourceDocument.span`` so a model cannot
    fabricate excerpts — offsets that do not exist in the request's document
    fail loudly (and the failure is recorded by the caller).
    """

    document = request.source_document
    candidates = []
    for item in _get_object_list(payload, "candidates"):
        spans = tuple(
            document.span(
                start_offset=_get_int(span_item, "start_offset"),
                end_offset=_get_int(span_item, "end_offset"),
            )
            for span_item in _get_object_list(item, "spans")
        )
        candidates.append(
            DeriveCandidate(decision_text=_get_str(item, "decision_text"), spans=spans)
        )
    dropped = tuple(
        DroppedChatter(
            reason_code=_get_str(item, "reason_code"),
            excerpt_hash=content_hash(_get_str(item, "excerpt")),
        )
        for item in _get_object_list(payload, "dropped")
    )
    return DeriveResult(
        candidates=tuple(candidates),
        model_id=model_id,
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
        dropped=dropped,
        degraded_reasons=_get_str_tuple(payload, "degraded_reasons"),
    )


def _evaluate_result_from_model_payload(
    payload: Mapping[str, Any], *, request: EvaluateRequest, model_id: str
) -> EvaluateResult:
    """Build a bound EvaluateResult from model output.

    Cited span hashes must come from the request's candidate pack; a finding
    citing a hash the pack never offered is refused loudly.
    """

    pack_span_hashes = {
        span.span_hash
        for candidate in request.candidate_pack.candidates
        for span in candidate.cited_spans
    }
    findings = []
    for item in _get_object_list(payload, "findings"):
        finding = _finding_from_payload(item)
        unknown = [
            value for value in finding.cited_span_hashes if value not in pack_span_hashes
        ]
        if unknown:
            raise RoutingError(
                f"finding cites span hash(es) not present in the candidate pack: "
                f"{unknown!r}; uncited-by-pack findings are refused"
            )
        findings.append(finding)
    return EvaluateResult(
        findings=tuple(findings),
        model_id=model_id,
        prompt_version=request.prompt_version,
        input_hash=request.input_hash,
        omitted_decision_count=_get_int(payload, "omitted_decision_count"),
        degraded_reasons=_get_str_tuple(payload, "degraded_reasons"),
    )


def _usage_from_envelope(envelope: Mapping[str, Any]) -> TokenUsage | None:
    raw = envelope.get("usage")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ClaudeCliOutputError("claude CLI envelope 'usage' must be a JSON object")
    input_tokens = raw.get("input_tokens")
    output_tokens = raw.get("output_tokens")
    for name, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ClaudeCliOutputError(
                f"claude CLI envelope usage.{name} must be a non-negative integer; "
                f"got {value!r}"
            )
    assert isinstance(input_tokens, int) and isinstance(output_tokens, int)
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens)


# --- strict parsing helpers ---------------------------------------------------


def _excerpt(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= _OUTPUT_EXCERPT_CHARS:
        return cleaned or "<empty>"
    return cleaned[:_OUTPUT_EXCERPT_CHARS] + "…"


def _json_object(text: str, *, context: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaudeCliOutputError(
            f"{context} is not valid JSON ({exc}); first bytes: {_excerpt(text)}"
        ) from exc
    if not isinstance(parsed, Mapping):
        raise ClaudeCliOutputError(
            f"{context} must be a JSON object; got {type(parsed).__name__}"
        )
    return parsed


def _validate_json_object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping):
        raise RoutingError(f"{name} must be a JSON object")
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RoutingError(f"{name} must be JSON-serializable") from exc


def _get_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RoutingError(f"{key} must be a string; got {type(value).__name__}")
    return value


def _get_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RoutingError(f"{key} must be an integer; got {type(value).__name__}")
    return value


def _get_str_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise RoutingError(f"{key} must be a list of strings")
    for item in value:
        if not isinstance(item, str):
            raise RoutingError(f"{key} entries must be strings")
    return tuple(value)


def _get_object_list(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise RoutingError(f"{key} must be a list")
    for item in value:
        if not isinstance(item, Mapping):
            raise RoutingError(f"{key} entries must be JSON objects")
    return value
