"""Server-side API-key HTTP transport adapter for the hosted reviewer (cortex#517).

The hosted Railway service cannot shell a user-session ``claude`` CLI, so
this module adds the third provider adapter behind the cortex#345 router:
:class:`ApiHttpAdapter`, registered in route tables under
``adapter_id: "api-http"`` (:data:`API_HTTP_ADAPTER_ID`) exactly like
``ClaudeCliAdapter``. Scope note: the repo CLAUDE.md rule that synthesis
shells out to the ``claude`` CLI governs *CLI synthesis*; this is the hosted
product surface where cortex#345 already placed routing.

Contract:

- **No vendor SDK** (cortex#348): the transport is raw HTTPS via stdlib
  ``urllib.request``. The provider's Messages REST shape (endpoint, version
  header, request/response body) is an ADAPTER-INTERNAL detail that never
  crosses the cortex#344 boundary.
- **Configuration over hardcoding:** model name, endpoint, version header,
  API-key env var name, output ceiling, timeout, and retry/backoff knobs all
  come from ``RouteConfig.params`` (defaults below). Unknown param keys are
  refused — a typo'd knob must fail loudly, not silently fall back to a
  default. The endpoint must be ``https://``.
- **API key from the service environment** (Railway variables): the env var
  named by the ``api_key_env`` param (default ``ANTHROPIC_API_KEY``). An
  unset/blank key raises :class:`ApiKeyMissingError` — a taxonomy-registered
  ``degraded_capability`` naming the variable, with the
  ``model_api_key_missing`` remediation hint, never a bare traceback. The
  key value itself never appears in any error message or log line.
- **One output contract:** the model text must be the same strict JSON the
  CLI adapter enforces. This module imports ``routing``'s prompt renderers
  and parse/validation helpers instead of duplicating them, so results built
  through either adapter serialize byte-identically into the one recording
  format (cortex#347).
- **Bounded, visible retries:** HTTP 429 and 5xx are retried with capped
  exponential backoff (each retry logged); anything else fails immediately.
  Exhausted retries raise :class:`ApiHttpOutputError` — a ``RoutingError``
  subclass, so the router records the failed call's cost and emits a
  ``RouteFallbackRecord`` when another route is configured.
- **Cost visibility (cortex#335):** the response's ``usage`` block becomes
  ``TokenUsage`` with ``CostBasis.REPORTED_TOKENS``; a missing block
  degrades visibly to ``UNREPORTED_TOKENS`` — never to zero tokens. Contract
  violations that parsed far enough to report tokens carry that usage so the
  failed call's cost record still accounts for the spend.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from cortex.hosted.cost import CostBasis, TokenUsage
from cortex.hosted.model_interfaces import (
    DeriveRequest,
    EvaluateRequest,
    ModelInterfaceValidationError,
)
from cortex.hosted.provenance import ProvenanceValidationError
from cortex.hosted.routing import (
    AdapterOutcome,
    ClaudeCliOutputError,
    RouteConfig,
    RoutingError,
    _derive_prompt,
    _derive_result_from_model_payload,
    _evaluate_prompt,
    _evaluate_result_from_model_payload,
    _excerpt,
    _json_object,
    _usage_from_envelope,
)

_LOGGER = logging.getLogger(__name__)

API_HTTP_ADAPTER_ID = "api-http"
"""The route-table adapter id deployments register this adapter under."""

DEFAULT_API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_API_ENDPOINT = "https://api.anthropic.com/v1/messages"
DEFAULT_API_VERSION = "2023-06-01"
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_API_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_BACKOFF_CAP_SECONDS = 30.0

API_KEY_REMEDIATION = (
    "set the route's API-key environment variable (default ANTHROPIC_API_KEY; "
    "the 'api_key_env' route param names an alternative) in the service "
    "environment — for the hosted deployment, a Railway service variable"
)
"""The one actionable next step for ``model_api_key_missing`` refusals.

``cortex.hosted.degradation`` registers this string in
``REMEDIATION_BY_REASON`` (the import runs that way around so this module
never imports the taxonomy)."""

_RETRYABLE_STATUS_FLOOR = 500
_RATE_LIMIT_STATUS = 429

_KNOWN_PARAM_KEYS = frozenset(
    {
        "api_key_env",
        "api_model",
        "api_version",
        "backoff_base_seconds",
        "backoff_cap_seconds",
        "endpoint",
        "max_output_tokens",
        "max_retries",
        "timeout_seconds",
    }
)


class ApiKeyMissingError(RoutingError):
    """Raised at call time when the configured API-key env var is unset/blank.

    Classified ``degraded_capability`` in the degradation taxonomy
    (mirroring ``ClaudeCliUnavailableError``): the transport is missing,
    named visibly, and the router may fall back to another route.
    """


class ApiHttpOutputError(RoutingError):
    """Raised when the HTTP transport or its response violates the contract.

    Carries the transport-reported ``usage`` when the response parsed far
    enough to report tokens, so the failed call's cost record can still
    account for the spend (same shape as ``ClaudeCliOutputError``).
    """

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class HttpResponse(Protocol):
    """The slice of an HTTP response object this adapter needs."""

    def read(self) -> bytes: ...

    def close(self) -> None: ...


class UrlOpener(Protocol):
    """Injectable transport seam matching ``urllib.request.urlopen``'s shape.

    Tests inject a recorded opener here; production uses the default, which
    is a thin wrapper over ``urllib.request.urlopen``.
    """

    def __call__(self, request: urllib.request.Request, *, timeout: float) -> HttpResponse: ...


def _default_opener(request: urllib.request.Request, *, timeout: float) -> HttpResponse:
    # The endpoint is validated https-only in _ResolvedParams.from_route.
    return cast("HttpResponse", urllib.request.urlopen(request, timeout=timeout))


def _live_environ() -> Mapping[str, str]:
    return os.environ


@dataclass(frozen=True)
class _ResolvedParams:
    """Route params resolved against the adapter defaults, fail-closed."""

    api_key_env: str
    api_model: str
    api_version: str
    endpoint: str
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    backoff_base_seconds: float
    backoff_cap_seconds: float

    @classmethod
    def from_route(cls, route: RouteConfig) -> _ResolvedParams:
        params = route.params
        unknown = sorted(set(params) - _KNOWN_PARAM_KEYS)
        if unknown:
            raise RoutingError(
                f"api-http route params contain unknown key(s) {unknown!r}; "
                f"known keys are {sorted(_KNOWN_PARAM_KEYS)!r} — refusing to "
                "silently ignore a possibly misspelled knob"
            )
        endpoint = _str_param(params, "endpoint", DEFAULT_API_ENDPOINT)
        if not endpoint.startswith("https://"):
            raise RoutingError(
                f"api-http route param 'endpoint' must be an https:// URL; got {endpoint!r}"
            )
        return cls(
            api_key_env=_str_param(params, "api_key_env", DEFAULT_API_KEY_ENV),
            # The provider-side model name defaults to the route's registry
            # model id; deployments whose registry ids differ from provider
            # ids map them here, in config — never in business logic.
            api_model=_str_param(params, "api_model", route.model_id),
            api_version=_str_param(params, "api_version", DEFAULT_API_VERSION),
            endpoint=endpoint,
            max_output_tokens=_int_param(
                params, "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS, minimum=1
            ),
            timeout_seconds=_float_param(
                params, "timeout_seconds", DEFAULT_API_TIMEOUT_SECONDS, require_positive=True
            ),
            max_retries=_int_param(params, "max_retries", DEFAULT_MAX_RETRIES, minimum=0),
            backoff_base_seconds=_float_param(
                params, "backoff_base_seconds", DEFAULT_BACKOFF_BASE_SECONDS
            ),
            backoff_cap_seconds=_float_param(
                params, "backoff_cap_seconds", DEFAULT_BACKOFF_CAP_SECONDS
            ),
        )


@dataclass(frozen=True)
class ApiHttpAdapter:
    """Direct-HTTPS provider adapter for hosted (server-side) deployments.

    Satisfies the router's ``ProviderAdapter`` protocol; everything
    provider-specific is confined to this class and ``RouteConfig.params``.
    ``opener``, ``sleep``, and ``environ`` are dependency-injection seams for
    tests — production constructs ``ApiHttpAdapter()`` and gets the live
    transport, real backoff sleeps, and the process environment.
    """

    opener: UrlOpener = _default_opener
    sleep: Callable[[float], None] = time.sleep
    environ: Mapping[str, str] = field(default_factory=_live_environ)

    def run_derive(self, request: DeriveRequest, route: RouteConfig) -> AdapterOutcome:
        model_text, usage = self._invoke(_derive_prompt(request), route)
        try:
            payload = _json_object(model_text, context="api-http derive output")
            result = _derive_result_from_model_payload(
                payload, request=request, model_id=route.model_id
            )
        except (RoutingError, ModelInterfaceValidationError, ProvenanceValidationError) as exc:
            raise ApiHttpOutputError(
                f"api-http derive output violated the contract: {exc}", usage=usage
            ) from exc
        return AdapterOutcome(
            result=result,
            cost_basis=(
                CostBasis.REPORTED_TOKENS if usage is not None else CostBasis.UNREPORTED_TOKENS
            ),
            usage=usage,
        )

    def run_evaluate(self, request: EvaluateRequest, route: RouteConfig) -> AdapterOutcome:
        model_text, usage = self._invoke(_evaluate_prompt(request), route)
        try:
            payload = _json_object(model_text, context="api-http evaluate output")
            result = _evaluate_result_from_model_payload(
                payload, request=request, model_id=route.model_id
            )
        except (RoutingError, ModelInterfaceValidationError) as exc:
            raise ApiHttpOutputError(
                f"api-http evaluate output violated the contract: {exc}", usage=usage
            ) from exc
        return AdapterOutcome(
            result=result,
            cost_basis=(
                CostBasis.REPORTED_TOKENS if usage is not None else CostBasis.UNREPORTED_TOKENS
            ),
            usage=usage,
        )

    # --- transport internals (the REST shape never leaves this class) -------

    def _invoke(self, prompt: str, route: RouteConfig) -> tuple[str, TokenUsage | None]:
        params = _ResolvedParams.from_route(route)
        api_key = (self.environ.get(params.api_key_env) or "").strip()
        if not api_key:
            raise ApiKeyMissingError(
                f"api-http adapter requires an API key in the {params.api_key_env!r} "
                f"environment variable, which is unset or blank; {API_KEY_REMEDIATION}"
            )
        body = json.dumps(
            {
                "max_tokens": params.max_output_tokens,
                "messages": [{"content": prompt, "role": "user"}],
                "model": params.api_model,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
        http_request = urllib.request.Request(
            params.endpoint,
            data=body,
            headers={
                "anthropic-version": params.api_version,
                "content-type": "application/json",
                "x-api-key": api_key,
            },
            method="POST",
        )
        envelope = self._post(http_request, params)
        try:
            usage = _usage_from_envelope(envelope)
        except ClaudeCliOutputError as exc:
            # The reused validator's message names the field that failed;
            # this wrapper names the transport that produced it.
            raise ApiHttpOutputError(
                f"api-http response usage block violated the contract: {exc}"
            ) from exc
        if envelope.get("type") == "error":
            raise ApiHttpOutputError(
                "api-http response reported a provider error: "
                f"{_excerpt(json.dumps(envelope.get('error'), sort_keys=True, default=str))}",
                usage=usage,
            )
        if envelope.get("stop_reason") == "max_tokens":
            raise ApiHttpOutputError(
                "api-http response was truncated at the output ceiling "
                f"(max_output_tokens={params.max_output_tokens}); a truncated "
                "JSON document is never parsed — raise the route's "
                "'max_output_tokens' param",
                usage=usage,
            )
        return _text_from_envelope(envelope, usage=usage), usage

    def _post(
        self, http_request: urllib.request.Request, params: _ResolvedParams
    ) -> Mapping[str, Any]:
        attempts = params.max_retries + 1
        last_failure = "no attempt ran"
        for attempt in range(1, attempts + 1):
            try:
                raw = self.opener(http_request, timeout=params.timeout_seconds)
            except urllib.error.HTTPError as exc:
                status = exc.code
                detail = _excerpt(_error_body(exc))
                if status != _RATE_LIMIT_STATUS and status < _RETRYABLE_STATUS_FLOOR:
                    raise ApiHttpOutputError(
                        f"api-http request to {params.endpoint} was refused with "
                        f"HTTP {status} (not retryable): {detail}"
                    ) from exc
                last_failure = f"HTTP {status}: {detail}"
                if attempt < attempts:
                    delay = min(
                        params.backoff_cap_seconds,
                        params.backoff_base_seconds * 2 ** (attempt - 1),
                    )
                    _LOGGER.warning(
                        "api-http attempt %d/%d to %s failed (%s); retrying in %.2fs",
                        attempt,
                        attempts,
                        params.endpoint,
                        last_failure,
                        delay,
                    )
                    self.sleep(delay)
                continue
            except (OSError, http.client.HTTPException) as exc:
                raise ApiHttpOutputError(
                    f"api-http request to {params.endpoint} failed before a "
                    f"response: {exc}"
                ) from exc
            try:
                raw_body = raw.read()
            except (OSError, http.client.HTTPException) as exc:
                raise ApiHttpOutputError(
                    f"api-http response read from {params.endpoint} failed: {exc}"
                ) from exc
            finally:
                raw.close()
            return _envelope_from_body(raw_body, endpoint=params.endpoint)
        raise ApiHttpOutputError(
            f"api-http request to {params.endpoint} failed after {attempts} "
            f"attempt(s); last failure: {last_failure}; retries are bounded — "
            "this failure is recorded and the router may fall back to the "
            "next configured route"
        )


def _envelope_from_body(raw_body: bytes, *, endpoint: str) -> Mapping[str, Any]:
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ApiHttpOutputError(
            f"api-http response body from {endpoint} is not valid UTF-8: {exc}"
        ) from exc
    try:
        return _json_object(text, context=f"api-http response body from {endpoint}")
    except ClaudeCliOutputError as exc:
        raise ApiHttpOutputError(str(exc)) from exc


def _text_from_envelope(envelope: Mapping[str, Any], *, usage: TokenUsage | None) -> str:
    content = envelope.get("content")
    if not isinstance(content, list):
        raise ApiHttpOutputError(
            "api-http response 'content' must be a list of content blocks; "
            f"got {type(content).__name__}",
            usage=usage,
        )
    texts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            raise ApiHttpOutputError(
                "api-http response content blocks must be JSON objects", usage=usage
            )
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            raise ApiHttpOutputError(
                "api-http response text block is missing a string 'text' field",
                usage=usage,
            )
        texts.append(text)
    if not texts:
        raise ApiHttpOutputError(
            "api-http response contained no text content blocks; the strict-JSON "
            "output contract cannot be checked against an empty response",
            usage=usage,
        )
    return "".join(texts)


def _error_body(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException, ValueError):
        return "<unreadable error body>"


def _str_param(params: Mapping[str, Any], key: str, default: str) -> str:
    value = params.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise RoutingError(
            f"api-http route param {key!r} must be a non-empty string; got {value!r}"
        )
    return value


def _int_param(params: Mapping[str, Any], key: str, default: int, *, minimum: int) -> int:
    value = params.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise RoutingError(
            f"api-http route param {key!r} must be an integer >= {minimum}; got {value!r}"
        )
    return value


def _float_param(
    params: Mapping[str, Any],
    key: str,
    default: float,
    *,
    require_positive: bool = False,
) -> float:
    value = params.get(key, default)
    floor_excluded = require_positive
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or value < 0
        or (floor_excluded and value == 0)
    ):
        bound = "> 0" if floor_excluded else ">= 0"
        raise RoutingError(
            f"api-http route param {key!r} must be a number {bound}; got {value!r}"
        )
    return float(value)
