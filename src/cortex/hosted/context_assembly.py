"""Token-budgeted evaluation context assembly for hosted Cortex (cortex#330).

Turns a shipped ``DecisionsForDiffCandidatePack`` into the bounded,
replayable material an evaluator prompt consumes. The master-plan invariant
this preserves is flat per-PR cost with a visible omitted count: the token
budget caps what ships to the model, and every candidate the budget
excludes is counted in ``omitted_for_budget`` and merged into
``total_omitted`` alongside the pack's own retrieval omissions — never
silently dropped.

Partial-candidate truncation is forbidden. A candidate enters the context
whole (decision text plus every cited span) or it is omitted and counted;
truncated citations are worse than absent ones because they let the
evaluator quote provenance a reader cannot verify.

Token estimates here are budget arithmetic, never billed truth — cortex#335
owns real cost accounting. Estimates are versioned like every other data
boundary: the estimator's version string travels in the assembled context
and in ``context_hash`` material, so a changed estimation regime is a
visible boundary change rather than silent drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidate,
    DecisionsForDiffCandidatePack,
)

ESTIMATOR_VERSION = "chars-per-token-v1"
# Coarse English-text ratio; ceiling division means the estimate never
# undercounts relative to its own rule, so a context that fits the
# estimate also fits the budget arithmetic that admitted it.
_CHARS_PER_TOKEN = 4
OVER_BUDGET_OMISSION_KEY = "over_budget"

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ContextAssemblyValidationError(ValueError):
    """Raised when evaluation context cannot be assembled replayably."""


@dataclass(frozen=True)
class TokenEstimator:
    """A versioned deterministic token estimator.

    Estimates are budget arithmetic, never billed truth (cortex#335 owns
    real cost). The version is mandatory so every assembled context names
    the estimation regime that produced its arithmetic; anonymous
    estimators are unrepresentable.
    """

    version: str
    estimate: Callable[[str], int]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ContextAssemblyValidationError("estimator version must not be empty")

    def estimate_tokens(self, text: str) -> int:
        estimated = self.estimate(text)
        if isinstance(estimated, bool) or not isinstance(estimated, int) or estimated < 0:
            raise ContextAssemblyValidationError(
                f"estimator {self.version!r} must return a non-negative int, "
                f"got {estimated!r}"
            )
        return estimated


def _estimate_chars_per_token(text: str) -> int:
    return -(-len(text) // _CHARS_PER_TOKEN)


default_token_estimator = TokenEstimator(
    version=ESTIMATOR_VERSION,
    estimate=_estimate_chars_per_token,
)


def serialize_candidate_payload(candidate: DecisionsForDiffCandidate) -> str:
    """Canonical JSON for one candidate's whole evaluator-context material."""

    return json.dumps(
        candidate.as_context_payload(), sort_keys=True, separators=(",", ":")
    )


@dataclass(frozen=True)
class EvaluationContext:
    """The bounded, replayable material an evaluator prompt consumes.

    ``candidates`` preserves pack order, which is fused-score order; the
    assembled context never reorders silently. ``total_omitted`` merges the
    pack's retrieval omissions with budget omissions so one mapping answers
    "what did the evaluator not see, and why".
    """

    query_hash: str
    retrieval_config_version: str
    graph_snapshot_hash: str
    candidates: tuple[DecisionsForDiffCandidate, ...]
    token_budget: int
    estimated_tokens_used: int
    estimator_version: str
    omitted_for_budget: int
    total_omitted: Mapping[str, int]
    degraded_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_hash("query_hash", self.query_hash)
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        _validate_hash("graph_snapshot_hash", self.graph_snapshot_hash)
        _validate_budget(self.token_budget)
        if self.estimated_tokens_used < 0:
            raise ContextAssemblyValidationError("estimated_tokens_used must be >= 0")
        if self.estimated_tokens_used > self.token_budget:
            raise ContextAssemblyValidationError(
                "estimated_tokens_used must not exceed token_budget; "
                "included material must fit the budget whole"
            )
        _require_non_empty("estimator_version", self.estimator_version)
        if self.omitted_for_budget < 0:
            raise ContextAssemblyValidationError("omitted_for_budget must be >= 0")
        _validate_omission_counts("total_omitted", self.total_omitted)
        if OVER_BUDGET_OMISSION_KEY not in self.total_omitted:
            raise ContextAssemblyValidationError(
                f"total_omitted must carry {OVER_BUDGET_OMISSION_KEY!r} so budget "
                "accounting stays visible even when zero"
            )
        if self.total_omitted[OVER_BUDGET_OMISSION_KEY] < self.omitted_for_budget:
            raise ContextAssemblyValidationError(
                f"total_omitted[{OVER_BUDGET_OMISSION_KEY!r}] must include every "
                "candidate counted in omitted_for_budget"
            )
        if self.degraded_reason is not None and not self.degraded_reason.strip():
            raise ContextAssemblyValidationError(
                "degraded_reason must not be empty when present"
            )
        if not self.candidates:
            if self.estimated_tokens_used != 0:
                raise ContextAssemblyValidationError(
                    "an empty context cannot have used estimated tokens"
                )
            if self.omitted_for_budget > 0 and self.degraded_reason is None:
                raise ContextAssemblyValidationError(
                    "an empty context that omitted candidates for budget must "
                    "carry a degraded_reason"
                )
        object.__setattr__(self, "total_omitted", MappingProxyType(dict(self.total_omitted)))

    @property
    def candidate_payloads(self) -> tuple[str, ...]:
        """Canonical serialization of each included candidate, in pack order.

        Derived from ``candidates`` on demand so the serialized material can
        never drift from the candidates it claims to represent.
        """

        return tuple(serialize_candidate_payload(candidate) for candidate in self.candidates)

    @property
    def context_hash(self) -> str:
        """Replay hash over the included material.

        Binds what the evaluator saw — the serialized candidate payloads in
        order — plus the pack identity that produced them and the estimator
        version that admitted them. The raw budget number stays out: replay
        cares about the material fed to the model, and the budget arithmetic
        travels alongside in ``as_payload()``.
        """

        return _hash_mapping(
            {
                "candidate_payloads": list(self.candidate_payloads),
                "estimator_version": self.estimator_version,
                "graph_snapshot_hash": self.graph_snapshot_hash,
                "query_hash": self.query_hash,
                "retrieval_config_version": self.retrieval_config_version,
            }
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "candidate_payloads": list(self.candidate_payloads),
            "context_hash": self.context_hash,
            "degraded_reason": self.degraded_reason,
            "estimated_tokens_used": self.estimated_tokens_used,
            "estimator_version": self.estimator_version,
            "graph_snapshot_hash": self.graph_snapshot_hash,
            "included_candidate_count": len(self.candidates),
            "omitted_for_budget": self.omitted_for_budget,
            "query_hash": self.query_hash,
            "retrieval_config_version": self.retrieval_config_version,
            "token_budget": self.token_budget,
            "total_omitted": dict(self.total_omitted),
        }


def assemble_evaluation_context(
    pack: DecisionsForDiffCandidatePack,
    *,
    token_budget: int,
    estimator: TokenEstimator = default_token_estimator,
) -> EvaluationContext:
    """Assemble a token-budgeted evaluator context from a candidate pack.

    Inclusion is a prefix of pack order: the pack arrives ranked by fused
    score, that order is kept, and assembly stops at the first candidate
    whose whole serialized payload does not fit the remaining budget.
    Skipping ahead to a smaller, lower-scored candidate would silently
    invert the ranking, so it is never done.

    Candidates are included whole or omitted and counted — partial-candidate
    truncation is forbidden because truncated citations are worse than
    absent ones. A budget too small for even the first candidate yields an
    empty context with a ``degraded_reason``, never an exception: an empty
    cited context is the honest answer.
    """

    _validate_budget(token_budget)
    included: list[DecisionsForDiffCandidate] = []
    estimated_used = 0
    first_blocked_cost: int | None = None
    for candidate in pack.candidates:
        cost = estimator.estimate_tokens(serialize_candidate_payload(candidate))
        if estimated_used + cost > token_budget:
            first_blocked_cost = cost
            break
        included.append(candidate)
        estimated_used += cost
    omitted_for_budget = len(pack.candidates) - len(included)
    degraded_reason: str | None = None
    if pack.candidates and not included:
        degraded_reason = (
            f"token_budget {token_budget} is below the first candidate's "
            f"estimated {first_blocked_cost} tokens ({estimator.version}); "
            "emitting an empty cited context instead of truncating a candidate"
        )
    return EvaluationContext(
        query_hash=pack.query_hash,
        retrieval_config_version=pack.retrieval_config_version,
        graph_snapshot_hash=pack.graph_snapshot_hash,
        candidates=tuple(included),
        token_budget=token_budget,
        estimated_tokens_used=estimated_used,
        estimator_version=estimator.version,
        omitted_for_budget=omitted_for_budget,
        total_omitted=_merge_omitted_counts(pack.omitted_counts, omitted_for_budget),
        degraded_reason=degraded_reason,
    )


def _merge_omitted_counts(
    pack_counts: Mapping[str, int], omitted_for_budget: int
) -> dict[str, int]:
    merged: dict[str, int] = {}
    for key, value in pack_counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ContextAssemblyValidationError(
                f"pack omitted_counts[{key!r}] must be a non-negative int to "
                "merge budget omissions"
            )
        merged[key] = value
    # Sum on collision: a pack that already tracked budget omissions keeps
    # them; overwriting would silently drop counted candidates.
    merged[OVER_BUDGET_OMISSION_KEY] = (
        merged.get(OVER_BUDGET_OMISSION_KEY, 0) + omitted_for_budget
    )
    return merged


def _validate_budget(token_budget: int) -> None:
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise ContextAssemblyValidationError("token_budget must be an int")
    if token_budget < 1:
        raise ContextAssemblyValidationError("token_budget must be >= 1")


def _validate_omission_counts(name: str, value: Mapping[str, int]) -> None:
    if not isinstance(value, Mapping):
        raise ContextAssemblyValidationError(f"{name} must be a mapping")
    for key, count in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ContextAssemblyValidationError(f"{name} keys must be non-empty strings")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ContextAssemblyValidationError(
                f"{name}[{key!r}] must be a non-negative int"
            )


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise ContextAssemblyValidationError(f"{name} must not be empty")


def _validate_hash(name: str, value: str) -> None:
    if not _SHA256_RE.match(value):
        raise ContextAssemblyValidationError(f"{name} must be a sha256 hex string")


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
