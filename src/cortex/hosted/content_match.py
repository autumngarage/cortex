"""Content-trigger matching for diff-scoped decision retrieval (cortex#556).

The structural retrieval lane (``scopes.py`` + ``replay_runner.
build_fixture_candidate_pack``) scopes a decision to a diff by PATH/GLOB/
SYMBOL/PACKAGE overlap. That lane misses a whole class of real rules: a
repo-wide constraint phrased in prose — "Cortex never imports touchstone" —
carries no path or package *scope* (the extractors propose scopes only from
path-shaped tokens), so a diff that adds ``import touchstone`` to an
unrelated file structurally matches nothing and the rule is suppressed
below the floor. The PE-2 dogfood proved this live: the stateless reviewer
returned "no contradictions found" on a diff that literally added
``import touchstone`` (cortex#556).

This module is the **content-trigger lane**: it matches the diff's CODE
IDENTIFIERS (imported package roots, defined symbols, changed config keys —
the structural surface ``diff_surface.extract_changed_surface`` already
pulls) against a decision's text, and reports the shared specific terms. A
shared identifier like ``touchstone`` makes the touchstone-import diff
retrieve the touchstone-forbidding decision even without a path scope. The
match is:

- **Deterministic.** Same identifiers + same decision text -> identical
  terms, identical order (sorted). No model, no randomness.
- **Case-normalized.** Terms are lowercased before comparison so
  ``Touchstone`` in prose matches ``touchstone`` in an import line.
- **Specificity-gated.** A term must clear a length floor AND not be a
  stopword AND not be a generic programming keyword. A generic word like
  ``code``, ``import``, or ``the`` MUST NOT match — that is the rule that
  keeps the lane from exploding false positives. The gate is the
  load-bearing invariant of this module.
- **Code-identifier-sourced on the diff side.** The diff contributes only
  identifiers it introduced in CODE (an imported module, a defined symbol, a
  config key), never free documentation prose. A marketing-copy edit that
  says "Payments, but friendly." introduces no code identifier and therefore
  cannot content-match every payments-domain decision — the false-positive
  failure this restriction exists to prevent.

The lane ADDS recall; it never removes the structural lane's matches. A
decision can match structurally, by content, by repo-wide inclusion, or by
any combination — the scores compose.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# A content term is an identifier-shaped token: it starts with a letter,
# then letters/digits/underscores. This deliberately excludes pure numbers
# (``2024``), punctuation, and operators — a shared digit run is not a
# specific conceptual match. Dotted/dashed module paths are split on their
# separators by the caller so ``touchstone.hooks`` contributes ``touchstone``
# and ``hooks`` independently.
_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")

# Separators inside a token that yield independent sub-terms: a module path
# ``touchstone.hooks`` and a dashed slug ``compose-by-file-contract`` each
# carry several specific terms. The caller pre-splits on these before the
# term regex runs so a buried module name is still found.
_SUBTERM_SPLIT_RE = re.compile(r"[.\-/:]+")

# The minimum length a term must reach to be specific enough to trigger a
# content match. Three-letter words are overwhelmingly generic English or
# generic code (``the``, ``and``, ``def``, ``for``); four is the empirical
# floor where domain identifiers (``repo``, ``hook``, ``slack``) begin and
# noise drops off. Derived from the domain, asserted in tests.
MIN_CONTENT_TERM_LENGTH = 4

# Generic English + code keywords that clear the length floor but carry no
# conceptual specificity. A diff and a decision sharing ``import`` or
# ``return`` or ``code`` is not a real conceptual match — every Python diff
# shares those with every Python-mentioning decision. This list is the
# specificity gate's second leg (the length floor is the first). It is
# intentionally conservative: only words that are BOTH common AND
# non-discriminating belong here, so a real domain term is never gated out.
_GENERIC_TERMS: frozenset[str] = frozenset(
    {
        # Common English >= 4 chars that are not domain-discriminating.
        "this", "that", "these", "those", "with", "from", "into", "your",
        "their", "they", "them", "then", "than", "when", "what", "which",
        "while", "where", "here", "there", "also", "such", "some", "only",
        "must", "should", "would", "could", "will", "have", "been",
        "does", "done", "make", "made", "used", "uses", "using", "like",
        "both", "each", "every", "more", "most", "other", "same", "very",
        "just", "over", "under", "above", "below", "after", "before",
        "between", "because", "about", "against", "without", "within",
        "always", "never", "still", "even", "much", "many", "none", "null",
        "true", "false",
        # Generic programming keywords/idioms that appear in nearly every
        # Python diff and most decision prose; sharing one is not a match.
        "import", "return", "class", "self", "pass",
        "async", "await", "yield", "raise", "except", "finally", "lambda",
        "global", "nonlocal", "assert", "value", "values", "param", "params",
        "args", "kwargs", "type", "types", "list", "dict", "tuple",
        "code", "file", "files",
        "line", "lines", "test", "tests", "data", "name", "names", "text",
        "function", "method", "module", "object", "string", "number",
        "result", "results", "input", "output", "error", "errors",
        # Python builtin type names and ubiquitous parameter/loop nouns —
        # sharing `float`, `bool`, `index`, or `attempt` with a decision is a
        # language-shape coincidence, not a conceptual match.
        "bool", "float", "bytes", "frozenset", "callable", "iterable",
        "mapping", "sequence", "optional", "union",
        "index", "count", "item", "items", "key", "keys", "default",
        "attempt", "attempts", "field", "fields", "entry", "entries",
        "node", "nodes", "path", "paths", "size", "length",
        # Ubiquitous symbol/entrypoint/plumbing names — sharing `main` or
        # `handler` is not a conceptual match between a diff and a decision.
        # Deliberately conservative: domain-meaningful tokens (`version`,
        # `schema`, `server`, `client`, `model`, `config`) are NOT gated, so a
        # decision genuinely about them can still content-match.
        "main", "run", "init", "setup", "handler", "handlers",
        "wrapper", "helper", "helpers", "util", "utils",
    }
)


def is_specific_term(term: str) -> bool:
    """True when ``term`` is specific enough to drive a content match.

    The specificity gate (cortex#556): a term must be a normalized
    identifier (lowercase, alphanumeric+underscore, starting with a letter),
    clear :data:`MIN_CONTENT_TERM_LENGTH`, and not be a generic English or
    programming term. A generic word like ``code`` or ``the`` returns False
    so it can never produce a spurious content match.
    """

    if len(term) < MIN_CONTENT_TERM_LENGTH:
        return False
    if not _TERM_RE.fullmatch(term):
        return False
    if term != term.lower():
        return False
    return term not in _GENERIC_TERMS


def extract_terms(text: str) -> frozenset[str]:
    """Extract the set of specific, normalized terms from arbitrary text.

    Splits dotted/dashed/slashed compounds into sub-terms (so
    ``touchstone.hooks`` yields ``touchstone`` and ``hooks``), lowercases,
    then keeps only terms that pass :func:`is_specific_term`. Returns a
    frozenset because membership and intersection are all the caller needs;
    determinism comes from sorting at the comparison site.
    """

    terms: set[str] = set()
    for chunk in _SUBTERM_SPLIT_RE.split(text):
        for raw in _TERM_RE.findall(chunk):
            normalized = raw.lower()
            if is_specific_term(normalized):
                terms.add(normalized)
    return frozenset(terms)


def extract_identifier_terms(identifiers: Iterable[str]) -> frozenset[str]:
    """The specific terms inside a set of CODE identifiers from the diff.

    The diff-side terms are deliberately NOT raw added-line prose: they are
    the structural code identifiers the diff introduced — imported package
    roots, defined symbols, changed config keys (``diff_surface`` extracts
    these). Restricting the diff side to code identifiers is the precision
    rule (cortex#556): ``import touchstone`` contributes the package root
    ``touchstone`` (a code identifier), while a documentation diff that says
    "Payments, but friendly." contributes NO code identifier — so a marketing
    edit cannot content-match every payments-domain decision. Each identifier
    is still split on dotted/dashed separators and gated for specificity, so
    ``touchstone.hooks`` yields ``touchstone`` and ``hooks``.
    """

    terms: set[str] = set()
    for identifier in identifiers:
        terms |= extract_terms(identifier)
    return frozenset(terms)


def shared_content_terms(
    diff_identifiers: Iterable[str], decision_text: str
) -> tuple[str, ...]:
    """The sorted specific terms shared by the diff's code identifiers and a decision.

    ``diff_identifiers`` are the structural code identifiers the diff
    introduced (imported package roots, defined symbols, changed config keys
    — what ``diff_surface.extract_changed_surface`` pulls), NOT raw prose. A
    non-empty result is the content-trigger signal (cortex#556): the diff
    added a code identifier the decision text names. Sorted for deterministic
    output (stable reason codes, stable scores).
    """

    shared = extract_identifier_terms(diff_identifiers) & extract_terms(decision_text)
    return tuple(sorted(shared))


def content_reason_code(term: str) -> str:
    """The reason code for a content-trigger match on ``term`` (cortex#556)."""

    return f"content:{term}"
