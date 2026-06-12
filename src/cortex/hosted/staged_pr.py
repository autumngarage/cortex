"""Staged-traffic detection and registry writes (cortex#575).

Staged traffic — demo fixtures, planted contradictions, walkthrough PRs — is
a different data regime from organic work. Findings and feedback it produces
are useful for demonstrations but poisonous as ground truth: a precision
metric computed over planted catches reports fixture-precision as
product-precision, and every downstream gate (promote/auto-demote #413/#415,
the organic-catch validation verdict #576) would inherit the contamination.

The boundary is a registry, not a column: ``review_staged_prs`` holds one
append-only row per staged PR keyed by ``(tenant, repo, pr_number)``, and
metric queries exclude members by JOIN. The append-only feedback corpus is
never rewritten — retroactive backfill (cortex PR #561) is one registry
INSERT, not an UPDATE.

The detection convention is deterministic and documented in
``docs/hosted-deploy.md``: a PR is staged when its title contains the token
``[cortex-demo]`` (case-insensitive) or it carries the label
``cortex-demo-fixture``. Title is checked first, so the recorded reason is
stable when both match. Detection is tolerant: a payload missing title or
labels is simply not staged — never an error, because organic traffic must
not fail review over an optional convention.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

STAGED_TITLE_TOKEN = "[cortex-demo]"
STAGED_LABEL = "cortex-demo-fixture"

STAGED_REASON_TITLE = "title-token"
STAGED_REASON_LABEL = "label"
STAGED_REASON_BACKFILL = "operator-backfill"
STAGED_REASONS = (STAGED_REASON_TITLE, STAGED_REASON_LABEL, STAGED_REASON_BACKFILL)


class StagedPrError(ValueError):
    """A staged-PR record that violates its own invariants."""


def detect_staged_reason(payload: Mapping[str, Any]) -> str | None:
    """Return the staged reason for a ``github.pull_request`` payload, or None.

    Reads the webhook envelope the same way the stateless reviewer does (the
    event body lives either at the top level or under ``body``) but is
    deliberately tolerant where the reviewer is fail-closed: detection is an
    optional convention, so any missing or malformed field means "not staged",
    never an exception.
    """

    if not isinstance(payload, Mapping):
        return None
    body = payload.get("body")
    event_body: Mapping[str, Any] = body if isinstance(body, Mapping) else payload
    pull_request = event_body.get("pull_request")
    if not isinstance(pull_request, Mapping):
        return None

    title = pull_request.get("title")
    if isinstance(title, str) and STAGED_TITLE_TOKEN in title.lower():
        return STAGED_REASON_TITLE

    labels = pull_request.get("labels")
    if isinstance(labels, (list, tuple)):
        for label in labels:
            if isinstance(label, Mapping):
                name = label.get("name")
                if isinstance(name, str) and name.strip().lower() == STAGED_LABEL:
                    return STAGED_REASON_LABEL
    return None


@dataclass(frozen=True)
class StagedPrRecord:
    """One staged-PR registry row, validated at construction."""

    tenant_id: str
    repo_full_name: str
    pr_number: int
    reason: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise StagedPrError("tenant_id must be a non-empty string")
        if not isinstance(self.repo_full_name, str) or not self.repo_full_name.strip():
            raise StagedPrError("repo_full_name must be a non-empty string")
        if not isinstance(self.pr_number, int) or self.pr_number <= 0:
            raise StagedPrError("pr_number must be a positive integer")
        if self.reason not in STAGED_REASONS:
            raise StagedPrError(
                f"reason must be one of {STAGED_REASONS}, got {self.reason!r}"
            )
        if not isinstance(self.recorded_at, datetime):
            raise StagedPrError("recorded_at must be a datetime")
        if self.recorded_at.tzinfo is None:
            raise StagedPrError("recorded_at must be timezone-aware")

    @property
    def idempotency_key(self) -> str:
        """Stable key over the PR identity (tenant, repo, pr_number)."""

        material = "|".join((self.tenant_id, self.repo_full_name, str(self.pr_number)))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_insert_parameters(self) -> dict[str, Any]:
        """Return DB-API named parameters for :func:`staged_pr_insert_sql`."""

        return {
            "tenant_id": self.tenant_id,
            "repo_full_name": self.repo_full_name,
            "pr_number": self.pr_number,
            "reason": self.reason,
            "recorded_at": self.recorded_at,
        }


def _validate_sql_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise StagedPrError(f"invalid SQL identifier: {name!r}")


def staged_pr_insert_sql(schema: str = "cortex_hosted") -> str:
    """Return the idempotent append statement for the staged-PR registry.

    ``ON CONFLICT ... DO NOTHING`` on the PR identity, so re-reviews,
    redeliveries, and repeated label events collapse to one row. Returns
    ``staged_pr_id`` when a row was inserted, nothing on conflict.
    """

    _validate_sql_identifier(schema)
    return f"""
INSERT INTO {schema}.review_staged_prs (
    tenant_id,
    repo_full_name,
    pr_number,
    reason,
    recorded_at
) VALUES (
    %(tenant_id)s,
    %(repo_full_name)s,
    %(pr_number)s,
    %(reason)s,
    %(recorded_at)s
)
ON CONFLICT ON CONSTRAINT review_staged_prs_pr_unique DO NOTHING
RETURNING staged_pr_id;
""".strip()
