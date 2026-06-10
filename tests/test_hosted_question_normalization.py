"""Tests for versioned question-boilerplate stripping (cortex#512).

The live PE-0 failure: "what did we decide about composing with sentinel and
touchstone?" returned no_cited_support because websearch_to_tsquery required
the boilerplate word "decide" to appear in the decision text. These tests pin
the canonical-phrase -> residue table, the never-strip-below-one-content-token
fallback, word-boundary safety, and the version constant that composes into
ASK_LEDGER_RETRIEVAL_CONFIG_VERSION.
"""

from __future__ import annotations

import pytest

from cortex.hosted.question_normalization import (
    QUESTION_NORMALIZATION_VERSION,
    QUESTION_STOP_PHRASES,
    QuestionNormalizationError,
    strip_question_boilerplate,
)


@pytest.mark.parametrize(
    ("question", "residue"),
    [
        # The PE-0 marquee phrase (cortex#512 repro 2).
        (
            "what did we decide about composing with sentinel and touchstone?",
            "composing with sentinel and touchstone",
        ),
        # Already-clean queries pass through untouched (repros 1 and 3).
        ("compose sentinel touchstone", "compose sentinel touchstone"),
        ("sentinel touchstone composition", "sentinel touchstone composition"),
        # Canonical phrase table: starters named in the issue.
        ("What did we decide about hosted storage?", "hosted storage"),
        ("did we ever settle the retry backoff?", "settle the retry backoff"),
        ("why do we shell out to the claude CLI?", "shell out to the claude CLI"),
        # Longest-first matching: the "about" variant wins over the bare verb.
        ("what did we decide on retry backoff", "retry backoff"),
        ("what did we decide, exactly, about retries?", "exactly, about retries"),
        # Trailing question marks strip independently of any phrase.
        ("hosted storage???", "hosted storage"),
        # Case-insensitive matching, punctuation separators consumed.
        ("WHY DID WE choose Postgres?", "choose Postgres"),
        ("what's our decision on schema migrations?", "schema migrations"),
        ("do we have a decision on slack ingestion?", "slack ingestion"),
    ],
)
def test_canonical_phrases_strip_to_residues(question: str, residue: str) -> None:
    assert strip_question_boilerplate(question) == residue


@pytest.mark.parametrize(
    "question",
    [
        # Boilerplate-only questions: stripping would empty the FTS input.
        "what did we decide about?",
        "did we ever?",
        "why do we",
        # Residue of pure punctuation is not a content token.
        "what did we decide about ---?",
    ],
)
def test_never_strips_below_one_content_token(question: str) -> None:
    # The fallback is the raw (whitespace-trimmed) question, verbatim.
    assert strip_question_boilerplate(question) == question.strip()


def test_phrase_match_requires_a_word_boundary() -> None:
    # "why do welds..." starts with the letters of "why do we" but the match
    # would split a word; the question must pass through unstripped.
    assert (
        strip_question_boilerplate("why do welds fail under load?")
        == "why do welds fail under load"
    )


def test_strip_is_deterministic() -> None:
    question = "what did we decide about composing with sentinel and touchstone?"
    assert strip_question_boilerplate(question) == strip_question_boilerplate(question)


def test_empty_question_is_rejected() -> None:
    with pytest.raises(QuestionNormalizationError, match="must not be empty"):
        strip_question_boilerplate("   ")


def test_normalization_version_is_pinned_and_phrases_are_versioned() -> None:
    # The version is the data boundary for the stop-phrase list: changing the
    # list without bumping it would silently aggregate traces across ranking
    # behaviors (cortex#467). Pin both so a list edit forces a visible choice.
    assert QUESTION_NORMALIZATION_VERSION == "question-norm-v1"
    assert "what did we decide about" in QUESTION_STOP_PHRASES
    assert "did we ever" in QUESTION_STOP_PHRASES
    assert "why do we" in QUESTION_STOP_PHRASES
    assert len(QUESTION_STOP_PHRASES) == len(set(QUESTION_STOP_PHRASES))
