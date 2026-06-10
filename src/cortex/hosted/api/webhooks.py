"""GitHub webhook verification and job translation (cortex#470).

Signature verification is HMAC-SHA256 over the raw request body against
``GITHUB_WEBHOOK_SECRET``, compared with :func:`hmac.compare_digest`
(constant-time). Verification never has a bypass: a missing header, a
malformed header, or a digest mismatch all verify false, and the API shell
answers 401 without naming which part failed.

A verified delivery becomes a :class:`cortex.hosted.jobs.JobRequest` whose
idempotency key is derived from the delivery GUID (``X-GitHub-Delivery``) —
the ledger idempotency idiom applied at the queue boundary, so GitHub
"Redeliver" produces zero duplicate jobs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

from cortex.hosted.jobs import DEFAULT_MAX_ATTEMPTS, JobRequest

SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"

# GitHub event names are lowercase identifiers (pull_request, issue_comment,
# ping, ...). Anything else is a malformed delivery, not a new feature.
_EVENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Delivery GUIDs are UUID-shaped; bound the accepted length so a hostile
# header cannot become an unbounded idempotency key.
_MAX_DELIVERY_LENGTH = 100

GITHUB_JOB_TYPE_PREFIX = "github."


class WebhookValidationError(ValueError):
    """Raised when a verified delivery is still structurally malformed."""


def verify_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    """Constant-time HMAC-SHA256 verification of a webhook body.

    Returns ``False`` for a missing header, a header without the
    ``sha256=`` scheme, or a digest mismatch. Never raises on attacker
    -controlled input; raises only when the *server* secret is unusable.
    """

    if not secret:
        raise WebhookValidationError("webhook secret must not be empty")
    if signature_header is None:
        return False
    scheme, _, received_digest = signature_header.partition("=")
    if scheme != "sha256" or not received_digest:
        return False
    expected_digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_digest, received_digest.lower())


def job_request_from_delivery(
    *,
    event: str,
    delivery: str,
    body: Mapping[str, Any],
    received_at_iso: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> JobRequest:
    """Translate a verified GitHub delivery into an idempotent job request."""

    if not _EVENT_NAME_RE.match(event):
        raise WebhookValidationError(f"malformed {EVENT_HEADER} header: {event!r}")
    if not delivery.strip():
        raise WebhookValidationError(f"missing {DELIVERY_HEADER} header value")
    if len(delivery) > _MAX_DELIVERY_LENGTH:
        raise WebhookValidationError(
            f"{DELIVERY_HEADER} header exceeds {_MAX_DELIVERY_LENGTH} characters"
        )
    return JobRequest(
        job_type=f"{GITHUB_JOB_TYPE_PREFIX}{event}",
        idempotency_key=f"github-delivery:{delivery}",
        payload={
            "event": event,
            "delivery": delivery,
            "received_at": received_at_iso,
            "body": dict(body),
        },
        metadata={"transport": "github-webhook"},
        max_attempts=max_attempts,
    )


def parse_json_body(body: bytes) -> Mapping[str, Any]:
    """Parse a webhook body as a JSON object, rejecting anything else."""

    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebhookValidationError(f"request body is not valid JSON: {exc}") from exc
    if not isinstance(decoded, Mapping):
        raise WebhookValidationError("request body must be a JSON object")
    return decoded
