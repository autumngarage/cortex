"""Versioned natural-question boilerplate stripping for hosted retrieval (cortex#512).

Found at the PE-0 milestone moment (2026-06-10): the product's marquee phrase
poisons its own retrieval. ``websearch_to_tsquery`` ANDs every non-stopword
term, so the canonical question shape "what did we **decide** about X"
requires the word *decide* to appear in the decision text — which it almost
never does. This module strips that interrogative boilerplate from the text
fed to the FTS leg of ``ask_ledger`` retrieval; the raw question still feeds
the trigram (and future vector) legs untouched.

Versioning contract (version-your-data-boundaries): the stop-phrase list is a
ranking-behavior input, so it is **versioned** via
``QUESTION_NORMALIZATION_VERSION`` and composed into
``ASK_LEDGER_RETRIEVAL_CONFIG_VERSION``. Any change to the phrase list or the
stripping mechanics MUST bump the version here — retrieval traces recorded
under different normalization behavior must never aggregate as comparable
(cortex#467 no-cross-config-aggregation rule).

Safety invariant: normalization can only *narrow* the FTS input, never empty
it — when stripping would leave fewer than one content token, the raw
question is returned unchanged. Degenerate boilerplate-only questions
("what did we decide about?") therefore degrade to today's behavior instead
of producing an unsatisfiable empty query.

Out of scope here (tracked as cortex#570): populating the embeddings table so
the vector leg survives stemming mismatches ("composition" vs "compose").
This module fixes the FTS leg only.
"""

from __future__ import annotations

# Bump on ANY change to QUESTION_STOP_PHRASES or the stripping mechanics; the
# value composes into ASK_LEDGER_RETRIEVAL_CONFIG_VERSION.
QUESTION_NORMALIZATION_VERSION = "question-norm-v1"

# Closed, versioned stop-phrase list (precision over recall — see cortex#512,
# which names the starters): leading interrogative templates whose words are
# question boilerplate, not decision content. Matched case-insensitively at
# the start of the question, longest phrase first, and only at a word
# boundary (the next character may not be alphanumeric, so "why do welds..."
# never loses "lds" to "why do we").
QUESTION_STOP_PHRASES: tuple[str, ...] = (
    "what did we decide about",
    "what did we decide on",
    "what did we decide",
    "what have we decided about",
    "what have we decided",
    "what was decided about",
    "what was decided",
    "what is our decision on",
    "what is our decision about",
    "what's our decision on",
    "what's our decision about",
    "do we have a decision on",
    "do we have a decision about",
    "did we ever decide",
    "did we decide",
    "did we ever",
    "why did we decide",
    "why do we",
    "why did we",
)

# Longest-first so "what did we decide about" wins over "what did we decide".
_PHRASES_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted(QUESTION_STOP_PHRASES, key=len, reverse=True)
)

# Separators that may trail a stripped phrase before the content starts.
_LEADING_SEPARATOR_CHARS = " \t,:;-—"


class QuestionNormalizationError(ValueError):
    """Raised when a question cannot be normalized for retrieval."""


def strip_question_boilerplate(question: str) -> str:
    """Strip leading interrogative boilerplate and trailing ``?`` for FTS.

    Returns the residue when it retains at least one content token (a token
    carrying an alphanumeric character); otherwise falls back to the raw
    (whitespace-trimmed) question so the FTS input is never emptied by
    normalization. Deterministic: same input, same output, no model calls.
    """

    if not question.strip():
        raise QuestionNormalizationError("question must not be empty")
    raw = question.strip()
    residue = raw
    lowered = residue.lower()
    for phrase in _PHRASES_LONGEST_FIRST:
        if not lowered.startswith(phrase):
            continue
        tail = residue[len(phrase) :]
        if tail[:1].isalnum():
            # Mid-word match ("why do welds...") — not boilerplate.
            continue
        residue = tail.lstrip(_LEADING_SEPARATOR_CHARS)
        break
    residue = residue.rstrip()
    while residue.endswith("?"):
        residue = residue[:-1].rstrip()
    if _has_content_token(residue):
        return residue
    return raw


def _has_content_token(text: str) -> bool:
    return any(
        any(character.isalnum() for character in token) for token in text.split()
    )
