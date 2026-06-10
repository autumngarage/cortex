"""Recorded-response fixtures for deterministic CI (cortex#347).

CI must never make a live model call: a live provider in the test loop is
flaky, slow, costly, and untestable offline. This module records results at
the cortex#344 boundary — the only place models are spoken to — and replays
them deterministically.

The replay key is the boundary's ``input_hash`` (``hash(inputs) +
prompt-version + task``), so a prompt or input change invalidates stale
recordings visibly: the new hash simply has no recording and replay fails
loudly. The model route is not part of the key (requests cannot name routes;
routing lives behind the boundary, cortex#345) but every recording stamps the
``(model_id, prompt_version)`` pair top-level and inside the result payload,
so a route change surfaces in the replayed result's stamp and as an
append-only collision at re-record time — never silently.

Three invariants, all fail-closed:

- **Unknown schema versions fail visibly.** Same version-gate discipline as
  ``eval_fixtures.py`` and ``model_registry.py``; no silent fallback.
- **Canonical JSON round-trips byte-identically.** Identical stores are
  identical bytes, so committed fixtures diff cleanly and re-records that
  change nothing change nothing.
- **A replay miss is a hard failure** naming the input_hash and the fixture
  file searched. There is no fallback path from the player to a live call.

Result serialization lives here, not in ``model_interfaces.py`` — that module
owns only the boundary shapes; this one owns how they persist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from cortex.hosted.eval_fixtures import FindingClass, FixtureScope
from cortex.hosted.model_interfaces import (
    DeriveCandidate,
    DeriveModel,
    DeriveRequest,
    DeriveResult,
    DroppedChatter,
    EvaluateModel,
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
    ensure_result_binds_request,
)
from cortex.hosted.provenance import SourceSpan

RECORDED_RESPONSE_SCHEMA_VERSION = 1

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

_T = TypeVar("_T")


class RecordedResponseError(ValueError):
    """Raised when recorded material cannot support deterministic replay."""


class RecordedTaskKind(StrEnum):
    """The two model tasks the cortex#344 boundary defines."""

    DERIVE = "derive"
    EVALUATE = "evaluate"


# ---------------------------------------------------------------------------
# Result serialization (exact round-trips for the #344 boundary shapes)
# ---------------------------------------------------------------------------


def derive_result_as_payload(result: DeriveResult) -> dict[str, Any]:
    """Serialize a ``DeriveResult`` losslessly to a JSON object."""

    return {
        "candidates": [_derive_candidate_as_payload(candidate) for candidate in result.candidates],
        "degraded_reasons": list(result.degraded_reasons),
        "dropped": [_dropped_chatter_as_payload(dropped) for dropped in result.dropped],
        "input_hash": result.input_hash,
        "model_id": result.model_id,
        "prompt_version": result.prompt_version,
    }


def derive_result_from_payload(payload: Mapping[str, Any]) -> DeriveResult:
    """Reconstruct a ``DeriveResult`` exactly; any defect fails visibly."""

    _require_mapping("derive result payload", payload)
    candidates = tuple(
        _derive_candidate_from_payload(item) for item in _get_object_list(payload, "candidates")
    )
    dropped = tuple(
        _dropped_chatter_from_payload(item) for item in _get_object_list(payload, "dropped")
    )
    return _build(
        "derive result",
        lambda: DeriveResult(
            candidates=candidates,
            model_id=_get_str(payload, "model_id"),
            prompt_version=_get_str(payload, "prompt_version"),
            input_hash=_get_str(payload, "input_hash"),
            dropped=dropped,
            degraded_reasons=_get_str_tuple(payload, "degraded_reasons"),
        ),
    )


def evaluate_result_as_payload(result: EvaluateResult) -> dict[str, Any]:
    """Serialize an ``EvaluateResult`` losslessly to a JSON object."""

    return {
        "degraded_reasons": list(result.degraded_reasons),
        "findings": [_finding_draft_as_payload(finding) for finding in result.findings],
        "input_hash": result.input_hash,
        "model_id": result.model_id,
        "omitted_decision_count": result.omitted_decision_count,
        "prompt_version": result.prompt_version,
    }


def evaluate_result_from_payload(payload: Mapping[str, Any]) -> EvaluateResult:
    """Reconstruct an ``EvaluateResult`` exactly; any defect fails visibly."""

    _require_mapping("evaluate result payload", payload)
    findings = tuple(
        _finding_draft_from_payload(item) for item in _get_object_list(payload, "findings")
    )
    return _build(
        "evaluate result",
        lambda: EvaluateResult(
            findings=findings,
            model_id=_get_str(payload, "model_id"),
            prompt_version=_get_str(payload, "prompt_version"),
            input_hash=_get_str(payload, "input_hash"),
            omitted_decision_count=_get_int(payload, "omitted_decision_count"),
            degraded_reasons=_get_str_tuple(payload, "degraded_reasons"),
        ),
    )


def _derive_candidate_as_payload(candidate: DeriveCandidate) -> dict[str, Any]:
    return {
        "decision_text": candidate.decision_text,
        "proposed_scopes": [scope.as_payload() for scope in candidate.proposed_scopes],
        "spans": [_source_span_as_payload(span) for span in candidate.spans],
    }


def _derive_candidate_from_payload(payload: Mapping[str, Any]) -> DeriveCandidate:
    spans = tuple(_source_span_from_payload(item) for item in _get_object_list(payload, "spans"))
    proposed_scopes = tuple(
        _fixture_scope_from_payload(item) for item in _get_object_list(payload, "proposed_scopes")
    )
    return _build(
        "derive candidate",
        lambda: DeriveCandidate(
            decision_text=_get_str(payload, "decision_text"),
            spans=spans,
            proposed_scopes=proposed_scopes,
        ),
    )


def _fixture_scope_from_payload(payload: Mapping[str, Any]) -> FixtureScope:
    return _build("proposed scope", lambda: FixtureScope.from_payload(payload))


def _source_span_as_payload(span: SourceSpan) -> dict[str, Any]:
    return {
        "end_offset": span.end_offset,
        "excerpt": span.excerpt,
        "permalink": span.permalink,
        "source_document_hash": span.source_document_hash,
        "span_hash": span.span_hash,
        "start_offset": span.start_offset,
        "tenant_id": span.tenant_id,
    }


def _source_span_from_payload(payload: Mapping[str, Any]) -> SourceSpan:
    span = _build(
        "source span",
        lambda: SourceSpan(
            tenant_id=_get_str(payload, "tenant_id"),
            source_document_hash=_get_str(payload, "source_document_hash"),
            start_offset=_get_int(payload, "start_offset"),
            end_offset=_get_int(payload, "end_offset"),
            excerpt=_get_str(payload, "excerpt"),
            permalink=_get_str(payload, "permalink"),
        ),
    )
    recorded = payload.get("span_hash")
    if recorded is not None and recorded != span.span_hash:
        raise RecordedResponseError(
            "span_hash does not match span material; recorded citations must be recomputable"
        )
    return span


def _dropped_chatter_as_payload(dropped: DroppedChatter) -> dict[str, Any]:
    return {"excerpt_hash": dropped.excerpt_hash, "reason_code": dropped.reason_code}


def _dropped_chatter_from_payload(payload: Mapping[str, Any]) -> DroppedChatter:
    return _build(
        "dropped chatter",
        lambda: DroppedChatter(
            reason_code=_get_str(payload, "reason_code"),
            excerpt_hash=_get_str(payload, "excerpt_hash"),
        ),
    )


def _finding_draft_as_payload(finding: FindingDraft) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cited_span_hashes": list(finding.cited_span_hashes),
        "confidence_label": finding.confidence_label,
        "decision_node_id": finding.decision_node_id,
        "finding_class": finding.finding_class.value,
        "summary": finding.summary,
    }
    if finding.suggested_repair is not None:
        payload["suggested_repair"] = finding.suggested_repair
    return payload


def _finding_draft_from_payload(payload: Mapping[str, Any]) -> FindingDraft:
    try:
        finding_class = FindingClass(_get_str(payload, "finding_class"))
    except ValueError as exc:
        raise RecordedResponseError(
            f"unknown finding_class: {payload.get('finding_class')!r}"
        ) from exc
    return _build(
        "finding draft",
        lambda: FindingDraft(
            finding_class=finding_class,
            decision_node_id=_get_str(payload, "decision_node_id"),
            cited_span_hashes=_get_str_tuple(payload, "cited_span_hashes"),
            summary=_get_str(payload, "summary"),
            confidence_label=_get_str(payload, "confidence_label"),
            suggested_repair=_get_optional_str(payload, "suggested_repair"),
        ),
    )


# ---------------------------------------------------------------------------
# The recorded-response file format
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordedResponse:
    """One recorded model result keyed by the cortex#344 replay ``input_hash``.

    ``recorded_at`` is caller-supplied (timezone-aware ISO-8601); this module
    never reads a clock, so re-record runs that change nothing are
    byte-identical.
    """

    task: RecordedTaskKind
    input_hash: str
    model_id: str
    prompt_version: str
    recorded_at: str
    result_payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.task, RecordedTaskKind):
            raise RecordedResponseError(
                f"task must be a RecordedTaskKind; got {self.task!r}"
            )
        if not isinstance(self.input_hash, str) or not _SHA256_RE.match(self.input_hash):
            raise RecordedResponseError("input_hash must be a sha256 hex string")
        _validate_recorded_at(self.recorded_at)
        object.__setattr__(
            self, "result_payload", _normalize_json_object("result_payload", self.result_payload)
        )
        result = self.result()
        if result.input_hash != self.input_hash:
            raise RecordedResponseError(
                f"recording input_hash {self.input_hash} does not match the result "
                f"payload's input_hash {result.input_hash}; a recording that drifts "
                "from its key cannot be replayed"
            )
        if result.model_id != self.model_id:
            raise RecordedResponseError(
                f"recording model_id {self.model_id!r} does not match the result "
                f"payload's model_id {result.model_id!r}"
            )
        if result.prompt_version != self.prompt_version:
            raise RecordedResponseError(
                f"recording prompt_version {self.prompt_version!r} does not match the "
                f"result payload's prompt_version {result.prompt_version!r}"
            )

    def result(self) -> DeriveResult | EvaluateResult:
        """Reconstruct the recorded boundary result exactly."""

        if self.task is RecordedTaskKind.DERIVE:
            return derive_result_from_payload(self.result_payload)
        return evaluate_result_from_payload(self.result_payload)

    def as_payload(self) -> dict[str, Any]:
        return {
            "input_hash": self.input_hash,
            "model_id": self.model_id,
            "prompt_version": self.prompt_version,
            "recorded_at": self.recorded_at,
            "result": dict(self.result_payload),
            "task": self.task.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RecordedResponse:
        _require_mapping("recorded response payload", payload)
        raw_task = _get_str(payload, "task")
        try:
            task = RecordedTaskKind(raw_task)
        except ValueError as exc:
            raise RecordedResponseError(
                f"unknown task kind {raw_task!r}; this loader supports only "
                f"{[kind.value for kind in RecordedTaskKind]}"
            ) from exc
        return cls(
            task=task,
            input_hash=_get_str(payload, "input_hash"),
            model_id=_get_str(payload, "model_id"),
            prompt_version=_get_str(payload, "prompt_version"),
            recorded_at=_get_str(payload, "recorded_at"),
            result_payload=_get_mapping(payload, "result"),
        )


class RecordedResponseStore:
    """Append-only collection of recordings, canonical-JSON serializable."""

    def __init__(self, responses: Iterable[RecordedResponse] = ()) -> None:
        self._responses: dict[tuple[RecordedTaskKind, str], RecordedResponse] = {}
        for response in responses:
            self.add(response)

    def add(self, response: RecordedResponse) -> RecordedResponse:
        """Add a recording; identical re-adds are idempotent, drift is an error."""

        key = (response.task, response.input_hash)
        existing = self._responses.get(key)
        if existing is not None:
            if existing == response:
                return existing
            raise RecordedResponseError(
                f"a different {response.task.value} response is already recorded for "
                f"input_hash {response.input_hash}; recordings are append-only — a "
                "prompt or input change must surface as a new input_hash, and a route "
                "change must be re-recorded deliberately, never overwritten silently"
            )
        self._responses[key] = response
        return response

    def find(self, task: RecordedTaskKind, input_hash: str) -> RecordedResponse | None:
        return self._responses.get((task, input_hash))

    @property
    def responses(self) -> tuple[RecordedResponse, ...]:
        return tuple(
            self._responses[key]
            for key in sorted(self._responses, key=lambda key: (key[0].value, key[1]))
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "recorded_response_schema_version": RECORDED_RESPONSE_SCHEMA_VERSION,
            "responses": [response.as_payload() for response in self.responses],
        }

    def to_canonical_json(self) -> str:
        """Serialize deterministically; identical stores are identical bytes."""

        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n"
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RecordedResponseStore:
        if not isinstance(payload, Mapping):
            raise RecordedResponseError("recorded-response payload must be a JSON object")
        raw_version = payload.get("recorded_response_schema_version")
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise RecordedResponseError(
                "recorded_response_schema_version must be an integer; refusing to guess"
            )
        if raw_version != RECORDED_RESPONSE_SCHEMA_VERSION:
            raise RecordedResponseError(
                f"unknown recorded_response_schema_version {raw_version!r}; this loader "
                f"supports only {RECORDED_RESPONSE_SCHEMA_VERSION} — no silent fallback "
                "for unrecognized recording versions"
            )
        return cls(
            RecordedResponse.from_payload(item)
            for item in _get_object_list(payload, "responses")
        )

    @classmethod
    def from_json(cls, text: str) -> RecordedResponseStore:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RecordedResponseError(
                f"recorded-response file is not valid JSON: {exc}"
            ) from exc
        return cls.from_payload(payload)


# ---------------------------------------------------------------------------
# Recorder (tee to fixture file) and player (replay-only, no live fallback)
# ---------------------------------------------------------------------------


class ResponseRecorder:
    """Tees boundary results into a recorded-response fixture file.

    Every successful ``record_*`` call rewrites the fixture file in canonical
    JSON, so the on-disk recording is always loadable and always complete up
    to the last recorded result.
    """

    def __init__(
        self,
        *,
        fixture_path: Path | str,
        recorded_at: str,
        store: RecordedResponseStore | None = None,
    ) -> None:
        _validate_recorded_at(recorded_at)
        self._fixture_path = Path(fixture_path)
        self._recorded_at = recorded_at
        self._store = store if store is not None else RecordedResponseStore()

    @property
    def fixture_path(self) -> Path:
        return self._fixture_path

    @property
    def store(self) -> RecordedResponseStore:
        return self._store

    def record_derive(self, request: DeriveRequest, result: DeriveResult) -> RecordedResponse:
        ensure_result_binds_request(request, result)
        return self._record(
            task=RecordedTaskKind.DERIVE,
            result_input_hash=result.input_hash,
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            result_payload=derive_result_as_payload(result),
        )

    def record_evaluate(
        self, request: EvaluateRequest, result: EvaluateResult
    ) -> RecordedResponse:
        ensure_result_binds_request(request, result)
        return self._record(
            task=RecordedTaskKind.EVALUATE,
            result_input_hash=result.input_hash,
            model_id=result.model_id,
            prompt_version=result.prompt_version,
            result_payload=evaluate_result_as_payload(result),
        )

    def _record(
        self,
        *,
        task: RecordedTaskKind,
        result_input_hash: str,
        model_id: str,
        prompt_version: str,
        result_payload: Mapping[str, Any],
    ) -> RecordedResponse:
        response = self._store.add(
            RecordedResponse(
                task=task,
                input_hash=result_input_hash,
                model_id=model_id,
                prompt_version=prompt_version,
                recorded_at=self._recorded_at,
                result_payload=result_payload,
            )
        )
        self._fixture_path.parent.mkdir(parents=True, exist_ok=True)
        self._fixture_path.write_text(self._store.to_canonical_json(), encoding="utf-8")
        return response


class RecordingDeriveModel:
    """Wraps any ``DeriveModel``; every result is teed to the recorder's file."""

    def __init__(self, inner: DeriveModel, recorder: ResponseRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def derive(self, request: DeriveRequest) -> DeriveResult:
        result = self._inner.derive(request)
        self._recorder.record_derive(request, result)
        return result


class RecordingEvaluateModel:
    """Wraps any ``EvaluateModel``; every result is teed to the recorder's file."""

    def __init__(self, inner: EvaluateModel, recorder: ResponseRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        result = self._inner.evaluate(request)
        self._recorder.record_evaluate(request, result)
        return result


class RecordedResponsePlayer:
    """Serves recorded results by ``input_hash``; a miss fails visibly.

    The player is the only model implementation CI may use. There is no
    fallback path to a live call: a miss raises, naming the input_hash and
    the fixture file searched, so the fix (re-record locally, commit the
    updated fixture) is obvious from the failure alone.
    """

    def __init__(
        self, store: RecordedResponseStore, *, fixture_path: Path | str
    ) -> None:
        self._store = store
        self._fixture_path = Path(fixture_path)

    @property
    def fixture_path(self) -> Path:
        return self._fixture_path

    @classmethod
    def load(cls, fixture_path: Path | str) -> RecordedResponsePlayer:
        path = Path(fixture_path)
        if not path.is_file():
            raise RecordedResponseError(
                f"recorded-response fixture file does not exist: {path}; replay mode "
                "cannot run without recordings and never falls back to live calls"
            )
        store = RecordedResponseStore.from_json(path.read_text(encoding="utf-8"))
        return cls(store, fixture_path=path)

    def derive(self, request: DeriveRequest) -> DeriveResult:
        response = self._lookup(RecordedTaskKind.DERIVE, request.input_hash)
        result = derive_result_from_payload(response.result_payload)
        ensure_result_binds_request(request, result)
        return result

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        response = self._lookup(RecordedTaskKind.EVALUATE, request.input_hash)
        result = evaluate_result_from_payload(response.result_payload)
        ensure_result_binds_request(request, result)
        return result

    def _lookup(self, task: RecordedTaskKind, input_hash: str) -> RecordedResponse:
        response = self._store.find(task, input_hash)
        if response is None:
            raise RecordedResponseError(
                f"no recorded {task.value} response for input_hash {input_hash} in "
                f"fixture file {self._fixture_path}; a replay miss is a hard failure, "
                "never a silent live call — a missing or stale hash means the prompt "
                "or input changed; re-record locally and commit the updated fixture"
            )
        return response


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _build(kind: str, factory: Callable[[], _T]) -> _T:
    """Run a constructor, converting validation failures to this module's error."""

    try:
        return factory()
    except RecordedResponseError:
        raise
    except ValueError as exc:
        raise RecordedResponseError(f"recorded {kind} is invalid: {exc}") from exc


def _validate_recorded_at(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RecordedResponseError("recorded_at must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RecordedResponseError(
            f"recorded_at must be an ISO-8601 timestamp; got {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecordedResponseError("recorded_at must be timezone-aware")


def _normalize_json_object(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecordedResponseError(f"{name} must be a JSON object")
    try:
        normalized: dict[str, Any] = json.loads(
            json.dumps(value, sort_keys=True, separators=(",", ":"))
        )
    except (TypeError, ValueError) as exc:
        raise RecordedResponseError(f"{name} must be JSON-serializable") from exc
    return normalized


def _require_mapping(name: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise RecordedResponseError(f"{name} must be a JSON object")


def _get_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RecordedResponseError(f"{key} must be a string; got {type(value).__name__}")
    return value


def _get_optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RecordedResponseError(f"{key} must be a string when present")
    return value


def _get_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecordedResponseError(f"{key} must be an integer")
    return value


def _get_str_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RecordedResponseError(f"{key} must be a list of strings")
    return tuple(value)


def _get_object_list(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RecordedResponseError(f"{key} must be a list")
    for item in value:
        if not isinstance(item, Mapping):
            raise RecordedResponseError(f"{key} entries must be JSON objects")
    return value


def _get_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise RecordedResponseError(f"{key} must be a JSON object")
    return value
