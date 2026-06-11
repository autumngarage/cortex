"""Operator-INTERNAL review cost ledger (cortex#547).

The append-only dataset that lets us understand what each hosted PR review
actually cost in provider dollars (tokens x provider list rate) — so we can
price the product to be profitable. This is strictly **operator-internal**:
it is never exposed to a customer surface, never billed, and never the same as
the customer-facing credits meter (``docs/HOSTED-PRICING.md``). The credits
meter is the customer's view; this table is ours.

Boundary:

- The cost basis is the versioned price table in :mod:`cortex.hosted.cost`
  (provider list price), the same single source of pricing truth the router's
  ledger uses. This module only *persists* what that basis computed.
- One row per successful review, keyed by ``(tenant_id, repo, pr, head_sha,
  model)``. Webhook redeliveries on the same head SHA re-evaluate the same
  diff, so the write is ``ON CONFLICT DO NOTHING`` — idempotent, never a
  double-count.
- The table is append-only by construction (no update/delete path); a
  re-priced cohort appends new rows under a new model/price regime rather than
  rewriting history, mirroring the ledger's immutability discipline.

The matching DDL lives in ``cortex.hosted.schema.create_schema_sql`` (schema
``v8``); this module owns the row dataclass and the idempotent insert SQL.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ReviewCostError(ValueError):
    """Raised when a review cost record cannot support trustworthy accounting."""


@dataclass(frozen=True)
class ReviewCostRecord:
    """One successful review's operator-internal provider-dollar cost.

    ``usd`` is the known provider cost (tokens x list rate from the versioned
    price table). On the offline recorded-playback path the live cost is 0 by
    definition; that 0 is still recorded so the dataset is complete and the
    absence of metered spend is visible rather than missing.
    """

    tenant_id: str
    repo_full_name: str
    pr_number: int
    head_sha: str
    model_id: str
    input_tokens: int
    output_tokens: int
    usd: float
    occurred_at: datetime

    def __post_init__(self) -> None:
        try:
            UUID(self.tenant_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ReviewCostError("tenant_id must be a UUID") from exc
        for name, value in (
            ("repo_full_name", self.repo_full_name),
            ("head_sha", self.head_sha),
            ("model_id", self.model_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ReviewCostError(f"{name} must be a non-empty string")
        if isinstance(self.pr_number, bool) or not isinstance(self.pr_number, int):
            raise ReviewCostError("pr_number must be an integer")
        if self.pr_number <= 0:
            raise ReviewCostError("pr_number must be > 0")
        for name, count in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ReviewCostError(f"{name} must be a non-negative integer")
        if (
            isinstance(self.usd, bool)
            or not isinstance(self.usd, int | float)
            or self.usd < 0
        ):
            raise ReviewCostError("usd must be a non-negative number")
        if not isinstance(self.occurred_at, datetime):
            raise ReviewCostError("occurred_at must be a datetime")
        if self.occurred_at.tzinfo is None:
            raise ReviewCostError("occurred_at must be timezone-aware")

    @property
    def idempotency_key(self) -> str:
        """Stable key for ``(tenant, repo, pr, head_sha, model)``.

        Redeliveries on the same head SHA re-evaluate the same diff with the
        same model, so this key collapses them to one row. A new push (new head
        SHA) or a re-price under a different model id produces a new key — a new
        row, never a silent overwrite.
        """

        material = "|".join(
            (
                self.tenant_id,
                self.repo_full_name,
                str(self.pr_number),
                self.head_sha,
                self.model_id,
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_insert_parameters(self) -> dict[str, Any]:
        """Return DB-API named parameters for :func:`review_cost_insert_sql`."""

        return {
            "tenant_id": self.tenant_id,
            "repo_full_name": self.repo_full_name,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": self.usd,
            "occurred_at": self.occurred_at,
            "idempotency_key": self.idempotency_key,
        }


def review_cost_insert_sql(schema: str = "cortex_hosted") -> str:
    """Return the idempotent append statement for review cost records.

    ``ON CONFLICT (idempotency_key) DO NOTHING`` so a webhook redelivery on the
    same ``(tenant, repo, pr, head_sha, model)`` never double-counts. Returns
    ``review_cost_record_id`` when a row was inserted, nothing on conflict.
    """

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.review_cost_records (
    tenant_id,
    repo_full_name,
    pr_number,
    head_sha,
    model_id,
    input_tokens,
    output_tokens,
    usd,
    occurred_at,
    idempotency_key
) VALUES (
    %(tenant_id)s,
    %(repo_full_name)s,
    %(pr_number)s,
    %(head_sha)s,
    %(model_id)s,
    %(input_tokens)s,
    %(output_tokens)s,
    %(usd)s,
    %(occurred_at)s,
    %(idempotency_key)s
)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING review_cost_record_id;
""".strip()


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ReviewCostError(f"invalid SQL identifier: {name!r}")
