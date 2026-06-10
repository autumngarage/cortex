"""Cited answer surface over ask-ledger context packs (cortex#381 + #382).

This module turns a :class:`~cortex.hosted.ask_ledger.CitedContextPack` —
however it was produced (live hosted SQL, recorded rows, fixtures) — into a
rendered, cited answer to "what did we decide about X?". It synthesizes
nothing: every answer line is the candidate's own ``decision_text`` plus the
candidate's citations with permalinks, and ``AnswerState.NO_ANSWER`` passes
through verbatim as the honest no-answer rendering (cortex#383 residual).

The no-browsable-index guardrail (cortex#382)
---------------------------------------------

Stage 0 read-surface inventory — a falsifiable list, not a slogan. The read
surfaces that can show decision content in Stage 0 are:

1. **``cortex ask`` (this surface).** Query-scoped by construction
   (:func:`require_query_scoped_question` refuses empty and browse-shaped
   questions), citation-bearing by construction (:class:`AnswerLine` cannot
   exist without >= 1 citation), and capped by construction (answers derive
   only from a ``CitedContextPack``, which is bounded by
   ``build_cited_context_pack``'s ``limit`` and records ``omitted_counts``).
2. **``cortex candidates list`` (``cortex.commands.confirm``).** Not part of
   the decision corpus: it enumerates the operator's *local, rebuildable*
   derive export (``CANDIDATE_PROPOSED`` events awaiting human confirmation,
   derived from the operator's own repo) so a human can confirm or reject.
   It never reads the hosted ledger and renders provenance on every row.

No endpoint or command lists or pages a tenant's decision corpus. Every
hosted read response is query-scoped, capped, and citation-bearing. The
guard layers are: lexical (browse-shaped questions refused before any
retrieval), structural (an uncited answer line is unrepresentable — this
module reuses the shipped citation machinery in ``CitedContextPack`` /
``CitedSourceSpan`` rather than introducing a parallel validator), and
bounded (even a browse-shaped question that evades the lexical guard cannot
enumerate the corpus, because the pack stays limit-capped with visible
``omitted_counts``).

Cross-link: cortex#441 is the enforcement-only product-level sibling
(broad inputs / narrow output); this module is the Stage 0 technical
enforcement of the read-surface half. Layer boundary: runtime evaluator
fail-closed behavior is owned by cortex#377 and post-hoc eval citation
checking by cortex#334 — neither is re-implemented here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from uuid import UUID

from cortex.hosted.ask_ledger import (
    AnswerState,
    CitedContextPack,
    CitedSourceSpan,
)

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'/-]*")

# Vocabulary for the lexical browse guard. A question whose word tokens all
# fall inside this set carries no topical scope — it can only be a demand to
# enumerate the corpus ("list all decisions", "show me everything"), so the
# surface refuses it. Any token outside the set counts as a topical scope
# and the question proceeds to retrieval.
_BROWSE_VOCABULARY = frozenset(
    {
        # enumeration verbs
        "list",
        "show",
        "dump",
        "browse",
        "enumerate",
        "print",
        "display",
        "give",
        "get",
        "fetch",
        "return",
        "see",
        # universal quantifiers
        "all",
        "every",
        "everything",
        "entire",
        "whole",
        "full",
        "complete",
        "any",
        "anything",
        # corpus nouns
        "decision",
        "decisions",
        "ledger",
        "index",
        "corpus",
        "entry",
        "entries",
        "record",
        "records",
        "contents",
        "catalog",
        "catalogue",
        "database",
        # scope-free connectives
        "me",
        "the",
        "a",
        "an",
        "of",
        "in",
        "our",
        "your",
        "please",
        "and",
        "or",
        "from",
        "to",
        "us",
        "it",
        "them",
        "what",
        "which",
        "are",
        "is",
        "there",
        "we",
        "you",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "made",
        "make",
        "recorded",
        "stored",
        "saved",
        "so",
        "far",
        "decide",
        "decided",
        "about",
        "regarding",
        "know",
        "tell",
        "exist",
        "exists",
    }
)

NO_ANSWER_HEADLINE = "No cited decision found"


class AskSurfaceValidationError(ValueError):
    """Raised when answer material is malformed (e.g. an uncited answer line).

    Guardrail half of cortex#382 (enforcement-only product sibling:
    cortex#441): the cited-answer types refuse construction rather than
    rendering decision content without citations.
    """


class BrowseIndexRefusedError(ValueError):
    """Raised when a question asks the surface to enumerate the ledger.

    The ask surface never renders a browsable index of the decision corpus
    (cortex#382; enforcement-only product sibling: cortex#441). Answers
    require a query scope; empty and browse-shaped questions are refused
    before any retrieval runs.
    """


def require_query_scoped_question(question: str) -> str:
    """Return the stripped question, refusing browse-shaped or empty input.

    The lexical layer of the cortex#382 guardrail (see module docstring for
    the structural and bounded layers). Fails closed: no token-bearing
    question is ever coerced into a query; it is either query-scoped or
    refused with the reason.
    """

    stripped = question.strip()
    if not stripped:
        raise BrowseIndexRefusedError(
            "a question is required: the ask surface answers questions about "
            "specific decisions and never renders an uncited listing of the "
            "ledger (cortex#382; see also cortex#441)"
        )
    tokens = _WORD_RE.findall(stripped.lower())
    if not tokens:
        raise BrowseIndexRefusedError(
            f"question {stripped!r} carries no searchable words; the ask "
            "surface is not a browsable index of the ledger (cortex#382)"
        )
    if all(token in _BROWSE_VOCABULARY for token in tokens):
        raise BrowseIndexRefusedError(
            f"question {stripped!r} names no topic — it reads as a request to "
            "enumerate the decision corpus, and the ask surface never renders "
            "a browsable index (cortex#382; see also cortex#441). Ask about a "
            "specific decision instead, e.g. 'what did we decide about retry "
            "backoff?'"
        )
    return stripped


@dataclass(frozen=True)
class AnswerLine:
    """One cited answer line: candidate decision text plus its citations.

    Structurally unrepresentable without a citation (cortex#382): a line
    with zero citations raises at construction, so no rendering path can
    ever hold uncited decision content.
    """

    decision_node_id: str
    decision_version_id: str
    text: str
    citations: tuple[CitedSourceSpan, ...]

    def __post_init__(self) -> None:
        _require_uuid("decision_node_id", self.decision_node_id)
        _require_uuid("decision_version_id", self.decision_version_id)
        _require_non_empty("text", self.text)
        if not self.citations:
            raise AskSurfaceValidationError(
                "an answer line requires at least one citation; uncited "
                "decision content is structurally unrepresentable on the ask "
                "surface (cortex#382)"
            )
        for citation in self.citations:
            if not isinstance(citation, CitedSourceSpan):
                raise AskSurfaceValidationError(
                    f"citations must be CitedSourceSpan instances, got "
                    f"{type(citation).__name__}; the ask surface consumes the "
                    "shipped citation machinery, never a parallel validator"
                )


@dataclass(frozen=True)
class CitedAnswer:
    """A composed answer: cited lines only, or a verbatim honest no-answer.

    Invariants:

    - ``READY`` requires at least one line; every line carries >= 1 citation
      (enforced by :class:`AnswerLine` itself).
    - ``NO_ANSWER`` requires zero lines and a non-empty reason — the
      no-answer state passes through verbatim, never dressed up as content.
    - ``omitted_counts`` always travels with the answer so bounded omission
      stays visible.
    """

    query_hash: str
    retrieval_config_version: str
    graph_snapshot_hash: str
    answer_state: AnswerState
    lines: tuple[AnswerLine, ...]
    omitted_counts: Mapping[str, int] = field(default_factory=dict)
    no_answer_reason: str | None = None

    def __post_init__(self) -> None:
        _validate_hash("query_hash", self.query_hash)
        _require_non_empty("retrieval_config_version", self.retrieval_config_version)
        _validate_hash("graph_snapshot_hash", self.graph_snapshot_hash)
        for line in self.lines:
            if not isinstance(line, AnswerLine):
                raise AskSurfaceValidationError(
                    f"answer lines must be AnswerLine instances, got {type(line).__name__}"
                )
        if self.answer_state is AnswerState.READY and not self.lines:
            raise AskSurfaceValidationError("a ready answer requires at least one cited line")
        if self.answer_state is AnswerState.NO_ANSWER:
            if self.lines:
                raise AskSurfaceValidationError(
                    "a no-answer result must not carry answer lines; NO_ANSWER "
                    "passes through verbatim (cortex#383 residual)"
                )
            if self.no_answer_reason is None or not self.no_answer_reason.strip():
                raise AskSurfaceValidationError("a no-answer result requires a reason")
        for name, count in dict(self.omitted_counts).items():
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise AskSurfaceValidationError(
                    f"omitted_counts[{name!r}] must be a non-negative integer"
                )
        object.__setattr__(self, "omitted_counts", MappingProxyType(dict(self.omitted_counts)))


def compose_answer(pack: CitedContextPack) -> CitedAnswer:
    """Compose a :class:`CitedAnswer` from a cited context pack — no synthesis.

    Answer lines derive ONLY from cited candidate material: each line is the
    candidate's ``decision_text`` verbatim plus that candidate's citations.
    ``AnswerState.NO_ANSWER`` passes through verbatim with its reason and
    ``omitted_counts`` intact. Requiring a real ``CitedContextPack`` is the
    structural guard: the pack's own invariants (citations on every
    candidate, bounded candidate set) were already enforced by
    ``cortex.hosted.ask_ledger`` — this module adds no parallel validator.
    """

    if not isinstance(pack, CitedContextPack):
        raise AskSurfaceValidationError(
            f"compose_answer requires a CitedContextPack, got {type(pack).__name__}; "
            "the citation and bounding invariants live in the pack, so the "
            "surface refuses anything that did not pass through them"
        )
    lines = tuple(
        AnswerLine(
            decision_node_id=candidate.decision_node_id,
            decision_version_id=candidate.decision_version_id,
            text=candidate.decision_text,
            citations=candidate.cited_spans,
        )
        for candidate in pack.candidates
    )
    return CitedAnswer(
        query_hash=pack.query_hash,
        retrieval_config_version=pack.retrieval_config_version,
        graph_snapshot_hash=pack.graph_snapshot_hash,
        answer_state=pack.answer_state,
        lines=lines,
        omitted_counts=dict(pack.omitted_counts),
        no_answer_reason=pack.no_answer_reason,
    )


def render_answer(answer: CitedAnswer) -> str:
    """Render a cited answer as plain text. Citations carry permalinks always."""

    rendered: list[str] = []
    if answer.answer_state is AnswerState.NO_ANSWER:
        rendered.append(f"{NO_ANSWER_HEADLINE} (reason: {answer.no_answer_reason}).")
    else:
        count = len(answer.lines)
        plural = "" if count == 1 else "s"
        rendered.append(f"{count} cited decision{plural}:")
        for index, line in enumerate(answer.lines, start=1):
            rendered.append(f"{index}. {line.text}")
            rendered.append(
                f"   decision {line.decision_node_id} (version {line.decision_version_id})"
            )
            for citation in line.citations:
                rendered.append(
                    f"   - {citation.permalink} (span {citation.span_hash[:12]})"
                )
    rendered.append(_render_omitted_counts(answer.omitted_counts))
    return "\n".join(rendered)


def _render_omitted_counts(omitted_counts: Mapping[str, int]) -> str:
    if not omitted_counts:
        return "omitted: none recorded"
    parts = ", ".join(f"{name}={count}" for name, count in sorted(omitted_counts.items()))
    return f"omitted: {parts}"


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AskSurfaceValidationError(f"{name} must not be empty")


def _require_uuid(name: str, value: str) -> None:
    try:
        UUID(value)
    except (TypeError, ValueError) as exc:
        raise AskSurfaceValidationError(f"{name} must be a UUID") from exc


def _validate_hash(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise AskSurfaceValidationError(f"{name} must be a sha256 hex string")
