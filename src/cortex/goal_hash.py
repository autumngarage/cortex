"""Goal-hash normalization per SPEC.md § 4.9.

Plans carry a ``Goal-hash:`` in frontmatter so that two writers aiming at
the same effort tend to converge on the same hash. Normalization is
deliberately mechanical — no embeddings, no synonym tables — so that
``cortex doctor`` can recompute the hash and catch drift.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9 ]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_goal_hash(title: str) -> str:
    """Normalize a Plan title to its 8-hex-char ``Goal-hash`` per SPEC § 4.9.

    Steps (strict, in order):
    1. NFKD-decompose and drop non-ASCII bytes (collapse diacritics).
    2. Lowercase.
    3. Strip everything that is not ``[a-z0-9 ]``.
    4. Collapse runs of whitespace to a single space; trim.
    5. ``sha256(utf-8 bytes)`` → first 8 hex characters.
    """
    decomposed = unicodedata.normalize("NFKD", title)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    cleaned = _NON_ALNUM_SPACE_RE.sub("", lowered)
    collapsed = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()[:8]
