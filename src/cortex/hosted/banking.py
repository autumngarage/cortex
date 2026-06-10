"""Banking and selective re-derivation policy (cortex#328).

The roadmap's cache discipline: ``cache key = hash(inputs) + model-id +
prompt-version``. The embeddings projection already lives by it for one
artifact class (``item_hash`` + model + epoch ⇒ skip unchanged); this
module generalizes the *policy* for every model-backed artifact so banked
results are reused only when nothing that fed them changed, and every
reuse/re-derivation decision is visible and attributed.

What this module is NOT: storage. Recorded results live in the
``recorded_responses`` format; this is the decide layer above any store.
Monotonic safety property: a banked result is reusable only under an
*exact* key match — there is no "close enough" tier here (semantic
near-duplicate reuse is Future work, cortex#421, and inherits its
reversibility requirements).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

BANKING_POLICY_VERSION = 1

_KEY_COMPONENTS = ("task", "input_hash", "model_id", "prompt_version")


class BankingValidationError(ValueError):
    """Raised when banking material cannot support attributable reuse."""


@dataclass(frozen=True)
class BankKey:
    """The exact identity a banked result is valid for."""

    task: str
    input_hash: str
    model_id: str
    prompt_version: str

    def __post_init__(self) -> None:
        for name in _KEY_COMPONENTS:
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise BankingValidationError(f"{name} must be a non-empty string")
        if self.task not in ("derive", "evaluate"):
            raise BankingValidationError(
                f"task must be 'derive' or 'evaluate'; got {self.task!r}"
            )

    @property
    def bank_key(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {name: getattr(self, name) for name in _KEY_COMPONENTS},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class BankDecision:
    """One visible reuse / re-derive decision."""

    action: str  # "reuse" | "re-derive"
    requested: BankKey
    banked: BankKey | None
    drifted_components: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.action not in ("reuse", "re-derive"):
            raise BankingValidationError(f"unknown action {self.action!r}")
        if self.action == "reuse" and (self.banked is None or self.drifted_components):
            raise BankingValidationError(
                "reuse requires a banked key with zero drifted components"
            )
        if self.action == "re-derive" and self.banked is not None and not self.drifted_components:
            raise BankingValidationError(
                "re-derive against an identical banked key is unattributable; "
                "name the drifted components"
            )

    def as_payload(self) -> dict[str, object]:
        return {
            "action": self.action,
            "banked_bank_key": None if self.banked is None else self.banked.bank_key,
            "drifted_components": list(self.drifted_components),
            "policy_version": BANKING_POLICY_VERSION,
            "requested_bank_key": self.requested.bank_key,
        }


def drifted_components(requested: BankKey, banked: BankKey) -> tuple[str, ...]:
    """Name exactly which key components differ (the attribution)."""

    return tuple(
        name
        for name in _KEY_COMPONENTS
        if getattr(requested, name) != getattr(banked, name)
    )


def decide(requested: BankKey, banked: BankKey | None) -> BankDecision:
    """The whole policy: exact match reuses; anything else re-derives, attributed.

    - No banked result → re-derive (cold).
    - Identical key → reuse (the cache discipline's only hit case).
    - Any drift (input content, model route, prompt version) → re-derive,
      with the drifted components named so #349's route comparisons and the
      eval reports can aggregate invalidation causes instead of guessing.
    """

    if banked is None:
        return BankDecision(
            action="re-derive", requested=requested, banked=None, drifted_components=()
        )
    drift = drifted_components(requested, banked)
    if not drift:
        return BankDecision(
            action="reuse", requested=requested, banked=banked, drifted_components=()
        )
    return BankDecision(
        action="re-derive", requested=requested, banked=banked, drifted_components=drift
    )
