"""Tests for the content-trigger matcher (cortex#556).

Two load-bearing invariants:

1. **Specificity gate.** A generic token must never produce a content match,
   while a specific domain identifier shared between the diff's CODE
   identifiers and a decision's text must.
2. **Code-identifier source.** The diff side is the structural code
   identifiers the diff introduced (imports/symbols/config keys), NOT free
   documentation prose — so a marketing-copy edit cannot content-match every
   domain decision by sharing a common word.

These tests pin both directions plus determinism / case-normalization.
"""

from __future__ import annotations

from cortex.hosted.content_match import (
    MIN_CONTENT_TERM_LENGTH,
    content_reason_code,
    extract_identifier_terms,
    extract_terms,
    is_specific_term,
    shared_content_terms,
)

_COMPOSE_DECISION_TEXT = (
    "Cortex does not import Sentinel, Touchstone, or anything they own. "
    "Compose by file contract, not code."
)


# ---------------------------------------------------------------------------
# Specificity gate
# ---------------------------------------------------------------------------


def test_specific_domain_identifier_passes_the_gate() -> None:
    assert is_specific_term("touchstone")
    assert is_specific_term("sentinel")
    assert is_specific_term("tenacity")


def test_short_terms_are_rejected_below_the_length_floor() -> None:
    # Three-letter tokens are generic; the floor is four.
    assert not is_specific_term("def")
    assert not is_specific_term("for")
    assert not is_specific_term("api")
    assert MIN_CONTENT_TERM_LENGTH == 4
    # A four/five-letter domain term at the boundary still passes.
    assert is_specific_term("slack")
    assert is_specific_term("repo")


def test_generic_english_and_keywords_are_rejected() -> None:
    for generic in (
        "code",
        "import",
        "return",
        "class",
        "this",
        "that",
        "with",
        "value",
        "file",
        "test",
        "function",
        # Builtin types and ubiquitous nouns are gated too (cortex#556 false-
        # positive hardening): sharing one is a language-shape coincidence.
        "float",
        "bool",
        "index",
        "attempt",
        "path",
        "node",
    ):
        assert not is_specific_term(generic), generic


def test_uppercase_and_nonidentifier_tokens_are_rejected() -> None:
    # extract_* lowercases before gating, so the gate itself only accepts
    # already-normalized identifiers — an uppercased or punctuated raw token
    # is not specific-by-construction.
    assert not is_specific_term("Touchstone")
    assert not is_specific_term("touch-stone")
    assert not is_specific_term("2026")
    assert not is_specific_term("")


# ---------------------------------------------------------------------------
# Term extraction
# ---------------------------------------------------------------------------


def test_extract_terms_is_case_normalized() -> None:
    assert "touchstone" in extract_terms("We never import Touchstone code.")


def test_extract_terms_splits_dotted_and_dashed_compounds() -> None:
    terms = extract_terms("touchstone.hooks and compose-by-file-contract")
    assert "touchstone" in terms
    assert "hooks" in terms
    assert "compose" in terms
    assert "contract" in terms
    # "by" and "file" are gated (too short / generic) — the compound still
    # yields its specific parts without leaking generic ones.
    assert "by" not in terms
    assert "file" not in terms


def test_extract_terms_drops_pure_numbers_and_short_tokens() -> None:
    terms = extract_terms("In 2026 we use 3 retries")
    assert "2026" not in terms
    assert "retries" in terms


def test_extract_identifier_terms_splits_module_paths() -> None:
    # A dotted import target contributes each specific sub-identifier.
    terms = extract_identifier_terms(["touchstone.hooks", "sentinel"])
    assert terms == frozenset({"touchstone", "hooks", "sentinel"})


def test_extract_identifier_terms_gates_generic_identifiers() -> None:
    # A symbol named with a generic word does not become a content term.
    assert extract_identifier_terms(["value", "data", "main"]) == frozenset()


# ---------------------------------------------------------------------------
# Shared-term matching (the lane's signal)
# ---------------------------------------------------------------------------


def test_touchstone_import_identifier_shares_the_touchstone_term() -> None:
    # The diff side is code identifiers (the `touchstone` package root from
    # `import touchstone`), matched against the decision text.
    shared = shared_content_terms(["touchstone"], _COMPOSE_DECISION_TEXT)
    assert "touchstone" in shared


def test_documentation_prose_word_is_not_a_code_identifier() -> None:
    # The diff-side input is code identifiers; a marketing edit introduces
    # none, so even a shared domain noun like "payments" cannot match. The
    # empty identifier list models exactly that (a docs-only diff).
    assert shared_content_terms([], "All payments go through src/payments.") == ()


def test_unrelated_identifiers_and_decision_share_nothing() -> None:
    shared = shared_content_terms(["widgets", "gadgets"], _COMPOSE_DECISION_TEXT)
    assert shared == ()


def test_shared_terms_are_sorted_and_deterministic() -> None:
    identifiers = ["zebra", "alpha"]
    decision = "We forbid zebra and alpha modules in this layer."
    first = shared_content_terms(identifiers, decision)
    second = shared_content_terms(identifiers, decision)
    assert first == second
    assert first == ("alpha", "zebra")
    assert list(first) == sorted(first)


def test_generic_shared_identifiers_do_not_trigger_a_match() -> None:
    # The diff introduces only generic identifiers (value/return); even though
    # the decision text contains them, no match — the specificity gate holds.
    decision = "Every function must import its dependencies and return a value."
    assert shared_content_terms(["value", "main", "data"], decision) == ()


def test_content_reason_code_namespaces_the_term() -> None:
    assert content_reason_code("touchstone") == "content:touchstone"
