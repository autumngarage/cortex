"""Content-free structured logging helpers for hosted Cortex services.

Hosted logs are operator telemetry, not a data warehouse. This module keeps
the logging boundary narrow: call sites name an event and content-free fields,
and the helper refuses known content-bearing field names before anything is
written.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from cortex.hosted.db import redacted_url

BANNED_LOG_FIELD_NAMES = frozenset(
    {
        "body",
        "comment_body",
        "content",
        "database_url",
        "decision_text",
        "diff",
        "dsn",
        "payload",
        "private_key",
        "private_key_pem",
        "raw_excerpt",
        "secret",
        "source_excerpt",
        "token",
    }
)

_URLISH_SECRET_RE = re.compile(r"\b(?:postgres|postgresql)://\S+", re.IGNORECASE)
_PEM_RE = re.compile(
    r"-----BEGIN [^-]+-----.*?-----END [^-]+-----",
    re.IGNORECASE | re.DOTALL,
)


class HostedLogError(ValueError):
    """Raised when a hosted log line would carry content-bearing fields."""


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Write one structured JSON log line after content-free validation."""

    validate_log_fields(fields)
    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def validate_log_fields(fields: Mapping[str, Any], *, path: str = "") -> None:
    """Refuse banned field names recursively.

    The guard is intentionally key-based. Values can still carry unexpected
    text, so call sites should log ids, counts, hashes, statuses, and reason
    codes. This fail-closed key guard catches the high-risk accidental cases:
    raw webhook bodies, rendered comments, excerpts, DSNs, keys, and tokens.
    """

    for key, value in fields.items():
        normalized = str(key).strip().lower()
        current = f"{path}.{normalized}" if path else normalized
        if normalized in BANNED_LOG_FIELD_NAMES:
            raise HostedLogError(f"hosted log field {current!r} is not content-free")
        if isinstance(value, Mapping):
            validate_log_fields(value, path=current)
        elif isinstance(value, list | tuple):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    validate_log_fields(item, path=f"{current}[{index}]")


def redact_for_log(value: object) -> str:
    """Return a bounded string with obvious secret-bearing material redacted."""

    text = str(value)
    text = _PEM_RE.sub("<redacted pem>", text)

    def _redact_url(match: re.Match[str]) -> str:
        return redacted_url(match.group(0))

    text = _URLISH_SECRET_RE.sub(_redact_url, text)
    if len(text) > 600:
        return text[:597] + "..."
    return text


def exception_for_log(exc: BaseException) -> str:
    """Content-bounded exception summary for logs."""

    return f"{type(exc).__name__}: {redact_for_log(exc)}"
