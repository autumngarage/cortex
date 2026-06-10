"""Cost and budget accounting at the hosted model boundary (cortex#335).

This module is the one documented place that defines the cost unit for
hosted model calls: **USD derived from raw per-call token counts (input and
output) multiplied by per-model prices**, with the price table versioned
(``ModelPriceTable.version``) so old run records stay interpretable after a
re-price. Transport-claimed cost fields (for example the claude CLI's
``total_cost_usd``) are deliberately not trusted as the cost basis — the
versioned price table is the single source of pricing truth.

Visibility rules, all fail-closed:

- When the transport cannot report token counts, the record says so
  explicitly (``CostBasis.UNREPORTED_TOKENS`` / ``NO_RESPONSE`` with
  ``usage=None`` and ``usd=None``) instead of recording zeros.
- Recorded-response playback records zero **live** cost
  (``CostBasis.RECORDED_PLAYBACK``, ``usd == 0.0``) so replayed runs
  (cortex#336) report zero live-LLM spend.
- Failed calls record too, marked ``CallOutcome.FAILED`` with a non-empty
  ``failure_reason``.
- A predictable budget breach raises :class:`BudgetExceededError` *before*
  the call (the caller checks :meth:`RunLedger.ensure_budget_allows_call`);
  a breach only discoverable after the response is recorded as a visible
  over-budget marker on the appended entry, never silently dropped.
- A run that cannot write its cost record (for example: reported tokens for
  a model missing from the price table) fails visibly with
  :class:`CostValidationError` rather than completing silently.

The per-run summary payload (:meth:`RunLedger.summary_payload`) is the
JSON-serializable shape the future eval reports (cortex#349) consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cortex.hosted.model_registry import RegistryValidationError, parse_prompt_version

_SHA256_HEX_LENGTH = 64
TOKENS_PER_PRICE_UNIT = 1_000_000
"""Per-model prices are expressed in USD per this many tokens."""


class CostValidationError(ValueError):
    """Raised when cost material cannot support trustworthy accounting."""


class BudgetExceededError(CostValidationError):
    """Raised before a call when the run budget is already exhausted."""


class CallOutcome(StrEnum):
    """Whether the routed call produced a usable result."""

    OK = "ok"
    FAILED = "failed"


class CostBasis(StrEnum):
    """How a record's cost was (or could not be) established."""

    REPORTED_TOKENS = "reported-tokens"
    """Live call; the transport reported token counts; USD is computable."""

    UNREPORTED_TOKENS = "unreported-tokens"
    """Live call; the transport could not report tokens; cost is explicitly
    unknown (``usd=None``), never zero."""

    RECORDED_PLAYBACK = "recorded-playback"
    """No live call; played back from a recording; live cost is exactly 0."""

    NO_RESPONSE = "no-response"
    """The call failed before any usage could be observed."""


@dataclass(frozen=True)
class TokenUsage:
    """Raw per-call token counts as reported by the transport."""

    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise CostValidationError(f"{name} must be a non-negative integer")

    def as_payload(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass(frozen=True)
class ModelPrice:
    """USD price for one model, per :data:`TOKENS_PER_PRICE_UNIT` tokens."""

    model_id: str
    usd_per_million_input_tokens: float
    usd_per_million_output_tokens: float

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise CostValidationError("model_id must not be empty")
        for name, value in (
            ("usd_per_million_input_tokens", self.usd_per_million_input_tokens),
            ("usd_per_million_output_tokens", self.usd_per_million_output_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
                raise CostValidationError(f"{name} must be a non-negative number")


@dataclass(frozen=True)
class ModelPriceTable:
    """Versioned per-model price list — the cost unit's source of truth.

    ``version`` travels with the harness so a run record produced under an
    old price table stays interpretable (cortex#335 acceptance criterion).
    """

    version: str
    prices: tuple[ModelPrice, ...]

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise CostValidationError("price table version must not be empty")
        seen: set[str] = set()
        for price in self.prices:
            if price.model_id in seen:
                raise CostValidationError(
                    f"duplicate price for model {price.model_id!r}; each model "
                    "has exactly one price per table version"
                )
            seen.add(price.model_id)

    def price_for(self, model_id: str) -> ModelPrice:
        for price in self.prices:
            if price.model_id == model_id:
                return price
        raise CostValidationError(
            f"no price registered for model {model_id!r} in price table "
            f"{self.version!r}; refusing to record an unpriced live call"
        )

    def usd_for(self, model_id: str, usage: TokenUsage) -> float:
        price = self.price_for(model_id)
        return (
            usage.input_tokens * price.usd_per_million_input_tokens
            + usage.output_tokens * price.usd_per_million_output_tokens
        ) / TOKENS_PER_PRICE_UNIT


@dataclass(frozen=True)
class CostRecord:
    """One routed model call's cost accounting, success or failure."""

    task_kind: str
    model_id: str
    prompt_version: str
    input_hash: str
    cost_basis: CostBasis
    wall_ms: int
    outcome: CallOutcome
    usage: TokenUsage | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.task_kind.strip():
            raise CostValidationError("task_kind must not be empty")
        if not self.model_id.strip():
            raise CostValidationError("model_id must not be empty")
        try:
            parse_prompt_version(self.prompt_version)
        except RegistryValidationError as exc:
            raise CostValidationError(str(exc)) from exc
        if len(self.input_hash) != _SHA256_HEX_LENGTH:
            raise CostValidationError("input_hash must be a sha256 hex string")
        if not isinstance(self.wall_ms, int) or isinstance(self.wall_ms, bool) or self.wall_ms < 0:
            raise CostValidationError("wall_ms must be a non-negative integer")
        if self.cost_basis is CostBasis.REPORTED_TOKENS and self.usage is None:
            raise CostValidationError(
                "cost_basis 'reported-tokens' requires usage; if the transport "
                "could not report tokens, use 'unreported-tokens' so the record "
                "says so explicitly instead of implying zero"
            )
        if self.cost_basis is not CostBasis.REPORTED_TOKENS and self.usage is not None:
            raise CostValidationError(
                f"cost_basis {self.cost_basis.value!r} must not carry usage; "
                "token counts are only recorded when the transport reported them"
            )
        if self.outcome is CallOutcome.FAILED:
            if self.failure_reason is None or not self.failure_reason.strip():
                raise CostValidationError(
                    "failed calls must record a non-empty failure_reason; "
                    "an unexplained failure is a silent failure"
                )
        elif self.failure_reason is not None:
            raise CostValidationError("successful calls must not carry a failure_reason")

    def as_payload(self) -> dict[str, Any]:
        return {
            "cost_basis": self.cost_basis.value,
            "failure_reason": self.failure_reason,
            "input_hash": self.input_hash,
            "model_id": self.model_id,
            "outcome": self.outcome.value,
            "prompt_version": self.prompt_version,
            "task_kind": self.task_kind,
            "usage": None if self.usage is None else self.usage.as_payload(),
            "wall_ms": self.wall_ms,
        }


@dataclass(frozen=True)
class RunBudget:
    """Ceilings for one run; ``None`` means that ceiling is not set."""

    max_usd: float | None = None
    max_calls: int | None = None

    def __post_init__(self) -> None:
        if self.max_usd is not None and (
            isinstance(self.max_usd, bool)
            or not isinstance(self.max_usd, int | float)
            or self.max_usd < 0
        ):
            raise CostValidationError("max_usd must be a non-negative number or None")
        if self.max_calls is not None and (
            not isinstance(self.max_calls, int)
            or isinstance(self.max_calls, bool)
            or self.max_calls < 0
        ):
            raise CostValidationError("max_calls must be a non-negative integer or None")


@dataclass(frozen=True)
class RecordedCost:
    """A ledger entry: the record plus the USD the ledger derived for it."""

    record: CostRecord
    usd: float | None
    over_budget: bool

    def as_payload(self) -> dict[str, Any]:
        payload = self.record.as_payload()
        payload["over_budget"] = self.over_budget
        payload["usd"] = self.usd
        return payload


class RunLedger:
    """Accumulates one run's cost records against a budget.

    Persistence of the summary payload to a committed run-ledger artifact is
    the harness runner's job (cortex#336/#349); this class owns the in-run
    accounting and the budget boundary.
    """

    def __init__(
        self,
        *,
        run_id: str,
        price_table: ModelPriceTable,
        budget: RunBudget | None = None,
    ) -> None:
        if not run_id.strip():
            raise CostValidationError("run_id must not be empty")
        self._run_id = run_id
        self._price_table = price_table
        self._budget = budget if budget is not None else RunBudget()
        self._entries: list[RecordedCost] = []

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def entries(self) -> tuple[RecordedCost, ...]:
        return tuple(self._entries)

    @property
    def call_count(self) -> int:
        return len(self._entries)

    @property
    def known_usd_total(self) -> float:
        """Sum of entries whose USD is known; unknown-cost entries excluded
        from the sum but counted visibly in :attr:`unknown_cost_call_count`."""

        return sum(entry.usd for entry in self._entries if entry.usd is not None)

    @property
    def unknown_cost_call_count(self) -> int:
        return sum(1 for entry in self._entries if entry.usd is None)

    @property
    def over_budget(self) -> bool:
        return any(entry.over_budget for entry in self._entries)

    def ensure_budget_allows_call(self, *, task_kind: str) -> None:
        """Raise :class:`BudgetExceededError` when a breach is predictable.

        Call counts and already-accumulated USD are known before the call,
        so those ceilings refuse the call up front. A per-call cost that
        only becomes known after the response is handled by :meth:`append`'s
        over-budget marker instead.
        """

        max_calls = self._budget.max_calls
        if max_calls is not None and self.call_count >= max_calls:
            raise BudgetExceededError(
                f"run {self._run_id!r}: budget ceiling of {max_calls} call(s) "
                f"already reached; refusing {task_kind!r} call "
                f"{self.call_count + 1}"
            )
        max_usd = self._budget.max_usd
        if max_usd is not None and self.known_usd_total >= max_usd:
            raise BudgetExceededError(
                f"run {self._run_id!r}: accumulated cost "
                f"${self.known_usd_total:.6f} already meets the ${max_usd:.6f} "
                f"ceiling; refusing {task_kind!r} call"
            )

    def append(self, record: CostRecord) -> RecordedCost:
        """Derive USD for the record, mark over-budget visibly, and store it.

        Raises :class:`CostValidationError` (visibly, failing the run) when
        a reported-token record names a model missing from the price table.
        """

        usd: float | None
        if record.cost_basis is CostBasis.REPORTED_TOKENS:
            assert record.usage is not None  # enforced by CostRecord
            usd = self._price_table.usd_for(record.model_id, record.usage)
        elif record.cost_basis is CostBasis.RECORDED_PLAYBACK:
            usd = 0.0
        else:
            usd = None

        max_usd = self._budget.max_usd
        over_budget = (
            max_usd is not None and usd is not None and self.known_usd_total + usd > max_usd
        )
        entry = RecordedCost(record=record, usd=usd, over_budget=over_budget)
        self._entries.append(entry)
        return entry

    def summary_payload(self) -> dict[str, Any]:
        """The per-run summary the eval reports (cortex#349) consume."""

        records = [entry.as_payload() for entry in self._entries]
        reported = [
            entry.record.usage
            for entry in self._entries
            if entry.record.usage is not None
        ]
        return {
            "budget": {
                "max_calls": self._budget.max_calls,
                "max_usd": self._budget.max_usd,
            },
            "call_count": self.call_count,
            "failed_call_count": sum(
                1 for entry in self._entries if entry.record.outcome is CallOutcome.FAILED
            ),
            "known_usd_total": self.known_usd_total,
            "models": sorted({entry.record.model_id for entry in self._entries}),
            "ok_call_count": sum(
                1 for entry in self._entries if entry.record.outcome is CallOutcome.OK
            ),
            "over_budget": self.over_budget,
            "price_table_version": self._price_table.version,
            "recorded_playback_call_count": sum(
                1
                for entry in self._entries
                if entry.record.cost_basis is CostBasis.RECORDED_PLAYBACK
            ),
            "records": records,
            "reported_input_tokens": sum(usage.input_tokens for usage in reported),
            "reported_output_tokens": sum(usage.output_tokens for usage in reported),
            "run_id": self._run_id,
            "unknown_cost_call_count": self.unknown_cost_call_count,
        }
