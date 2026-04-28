"""Parse and age per-fact ``Verified:`` markers."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

VERIFIED_RE = re.compile(
    r"(?:^|\s)Verified:\s*(?P<date>\d{4}-\d{2}-\d{2})(?:[T ][^\s]+)?\s*$"
)


def parse_verified(line: str) -> date | None:
    """Return the trailing ``Verified:`` date from a bullet, if present."""
    match = VERIFIED_RE.search(line)
    if match is None:
        return None
    return date.fromisoformat(match.group("date"))


def bullet_age_days(verified: date, today: date | None = None) -> int:
    """Return whole days elapsed since ``verified``."""
    today = today or datetime.now(UTC).date()
    return (today - verified).days


def format_warning(age_days: int, threshold: int) -> str | None:
    """Return an inline staleness warning when ``age_days`` exceeds ``threshold``."""
    if age_days <= threshold:
        return None
    return f"⚠ verified {age_days}d ago"
