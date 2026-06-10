"""Tests for the cited ask surface (cortex#381) and its guardrail (cortex#382).

Covers: cited-answer composition from a context pack, verbatim NO_ANSWER
pass-through, the structurally-unrepresentable uncited answer line, the
no-browsable-index query guard, rendering with permalinks, and the
degradation-taxonomy classification of the surface's error types.
"""

from __future__ import annotations

import pytest

from cortex.hosted.ask_ledger import (
    AnswerState,
    AskLedgerCandidate,
    AskLedgerValidationError,
    CitedContextPack,
    CitedSourceSpan,
    build_cited_context_pack,
)
from cortex.hosted.ask_surface import (
    NO_ANSWER_HEADLINE,
    AnswerLine,
    AskSurfaceValidationError,
    BrowseIndexRefusedError,
    CitedAnswer,
    compose_answer,
    render_answer,
    require_query_scoped_question,
)
from cortex.hosted.degradation import DegradationMode, classify_failure

QUERY_HASH = "a" * 64
SNAPSHOT_HASH = "b" * 64
SPAN_HASH = "c" * 64
OTHER_SPAN_HASH = "d" * 64
NODE_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "22222222-2222-4222-8222-222222222222"
OTHER_NODE_ID = "33333333-3333-4333-8333-333333333333"
OTHER_VERSION_ID = "44444444-4444-4444-8444-444444444444"
DOCUMENT_ID = "55555555-5555-4555-8555-555555555555"
SOURCE_ID = "66666666-6666-4666-8666-666666666666"


def _span(span_hash: str = SPAN_HASH, permalink: str = "https://example.test/CLAUDE.md#L1") -> CitedSourceSpan:
    return CitedSourceSpan(
        span_hash=span_hash,
        excerpt="Use Postgres for the ledger.",
        permalink=permalink,
        source_document_id=DOCUMENT_ID,
        source_id=SOURCE_ID,
    )


def _candidate(
    *,
    node_id: str = NODE_ID,
    version_id: str = VERSION_ID,
    text: str = "Use Postgres for the ledger.",
    score: float = 1.5,
    spans: tuple[CitedSourceSpan, ...] | None = None,
) -> AskLedgerCandidate:
    return AskLedgerCandidate(
        decision_node_id=node_id,
        decision_version_id=version_id,
        decision_text=text,
        score=score,
        reason_codes=("full_text:decision_text",),
        cited_spans=spans if spans is not None else (_span(),),
    )


def _ready_pack(candidates: tuple[AskLedgerCandidate, ...] | None = None, limit: int = 10) -> CitedContextPack:
    return build_cited_context_pack(
        query_hash=QUERY_HASH,
        retrieval_config_version="ask-ledger-v2+test",
        graph_snapshot_hash=SNAPSHOT_HASH,
        candidates=candidates if candidates is not None else (_candidate(),),
        limit=limit,
    )


class TestComposeAnswer:
    def test_ready_pack_composes_cited_lines(self) -> None:
        answer = compose_answer(_ready_pack())
        assert answer.answer_state is AnswerState.READY
        assert len(answer.lines) == 1
        line = answer.lines[0]
        assert line.citations[0].permalink == "https://example.test/CLAUDE.md#L1"
        assert line.decision_node_id == NODE_ID
        assert line.decision_version_id == VERSION_ID

    def test_answer_text_is_decision_text_verbatim_no_synthesis(self) -> None:
        pack = _ready_pack()
        answer = compose_answer(pack)
        assert tuple(line.text for line in answer.lines) == tuple(
            candidate.decision_text for candidate in pack.candidates
        )

    def test_no_answer_passes_through_verbatim(self) -> None:
        pack = build_cited_context_pack(
            query_hash=QUERY_HASH,
            retrieval_config_version="ask-ledger-v2+test",
            graph_snapshot_hash=SNAPSHOT_HASH,
            candidates=(),
            limit=5,
        )
        answer = compose_answer(pack)
        assert answer.answer_state is AnswerState.NO_ANSWER
        assert answer.no_answer_reason == pack.no_answer_reason == "no_cited_support"
        assert answer.lines == ()
        assert dict(answer.omitted_counts) == dict(pack.omitted_counts)

    def test_ready_answer_preserves_omitted_counts(self) -> None:
        candidates = tuple(
            _candidate(node_id=node, version_id=version, score=score)
            for node, version, score in (
                (NODE_ID, VERSION_ID, 2.0),
                (OTHER_NODE_ID, OTHER_VERSION_ID, 1.0),
            )
        )
        pack = build_cited_context_pack(
            query_hash=QUERY_HASH,
            retrieval_config_version="ask-ledger-v2+test",
            graph_snapshot_hash=SNAPSHOT_HASH,
            candidates=candidates,
            limit=1,
        )
        answer = compose_answer(pack)
        assert len(answer.lines) == 1
        assert answer.omitted_counts["over_limit"] == 1

    def test_compose_rejects_non_pack_material(self) -> None:
        with pytest.raises(AskSurfaceValidationError, match="CitedContextPack"):
            compose_answer({"candidates": []})  # type: ignore[arg-type]

    def test_answer_lines_bounded_by_pack_limit(self) -> None:
        candidates = tuple(
            _candidate(node_id=node, version_id=version, score=score)
            for node, version, score in (
                (NODE_ID, VERSION_ID, 3.0),
                (OTHER_NODE_ID, OTHER_VERSION_ID, 2.0),
            )
        )
        answer = compose_answer(
            build_cited_context_pack(
                query_hash=QUERY_HASH,
                retrieval_config_version="ask-ledger-v2+test",
                graph_snapshot_hash=SNAPSHOT_HASH,
                candidates=candidates,
                limit=1,
            )
        )
        assert len(answer.lines) == 1


class TestStructuralGuardrail:
    """cortex#382: an uncited answer is unrepresentable, not just unrendered."""

    def test_answer_line_without_citation_is_unrepresentable(self) -> None:
        with pytest.raises(AskSurfaceValidationError, match="at least one citation"):
            AnswerLine(
                decision_node_id=NODE_ID,
                decision_version_id=VERSION_ID,
                text="Use Postgres for the ledger.",
                citations=(),
            )

    def test_answer_line_rejects_non_span_citations(self) -> None:
        with pytest.raises(AskSurfaceValidationError, match="CitedSourceSpan"):
            AnswerLine(
                decision_node_id=NODE_ID,
                decision_version_id=VERSION_ID,
                text="Use Postgres for the ledger.",
                citations=({"permalink": "https://example.test"},),  # type: ignore[arg-type]
            )

    def test_ready_answer_requires_lines(self) -> None:
        with pytest.raises(AskSurfaceValidationError, match="at least one cited line"):
            CitedAnswer(
                query_hash=QUERY_HASH,
                retrieval_config_version="ask-ledger-v2+test",
                graph_snapshot_hash=SNAPSHOT_HASH,
                answer_state=AnswerState.READY,
                lines=(),
            )

    def test_no_answer_refuses_lines(self) -> None:
        line = AnswerLine(
            decision_node_id=NODE_ID,
            decision_version_id=VERSION_ID,
            text="Use Postgres for the ledger.",
            citations=(_span(),),
        )
        with pytest.raises(AskSurfaceValidationError, match="verbatim"):
            CitedAnswer(
                query_hash=QUERY_HASH,
                retrieval_config_version="ask-ledger-v2+test",
                graph_snapshot_hash=SNAPSHOT_HASH,
                answer_state=AnswerState.NO_ANSWER,
                lines=(line,),
                no_answer_reason="no_cited_support",
            )

    def test_no_answer_requires_reason(self) -> None:
        with pytest.raises(AskSurfaceValidationError, match="requires a reason"):
            CitedAnswer(
                query_hash=QUERY_HASH,
                retrieval_config_version="ask-ledger-v2+test",
                graph_snapshot_hash=SNAPSHOT_HASH,
                answer_state=AnswerState.NO_ANSWER,
                lines=(),
            )

    def test_uncited_candidate_cannot_enter_a_pack(self) -> None:
        """The guard consumes the shipped citation machinery, not a copy."""

        with pytest.raises(AskLedgerValidationError, match="citations"):
            CitedContextPack(
                query_hash=QUERY_HASH,
                retrieval_config_version="ask-ledger-v2+test",
                graph_snapshot_hash=SNAPSHOT_HASH,
                candidates=(_candidate(spans=()),),
            )

    def test_negative_omitted_counts_rejected(self) -> None:
        with pytest.raises(AskSurfaceValidationError, match="non-negative"):
            CitedAnswer(
                query_hash=QUERY_HASH,
                retrieval_config_version="ask-ledger-v2+test",
                graph_snapshot_hash=SNAPSHOT_HASH,
                answer_state=AnswerState.NO_ANSWER,
                lines=(),
                omitted_counts={"missing_citations": -1},
                no_answer_reason="no_cited_support",
            )


class TestBrowseGuard:
    """cortex#382: answers require a question; browse-shaped queries refused."""

    @pytest.mark.parametrize(
        "question",
        [
            "",
            "   ",
            "*",
            "???",
            "list all decisions",
            "show me everything",
            "dump the ledger",
            "browse decisions",
            "enumerate every record",
            "what are all the decisions",
        ],
    )
    def test_browse_shaped_questions_refused(self, question: str) -> None:
        with pytest.raises(BrowseIndexRefusedError):
            require_query_scoped_question(question)

    @pytest.mark.parametrize(
        "question",
        [
            "what did we decide about retry backoff?",
            "why did we choose Postgres over SQLite?",
            "list all decisions about webhook signatures",
            "what did we decide about the staging environment",
        ],
    )
    def test_query_scoped_questions_accepted(self, question: str) -> None:
        assert require_query_scoped_question(question) == question.strip()

    def test_refusal_names_the_guardrail_issues(self) -> None:
        with pytest.raises(BrowseIndexRefusedError, match=r"cortex#382"):
            require_query_scoped_question("list all decisions")


class TestRendering:
    def test_render_includes_permalink_for_every_citation(self) -> None:
        spans = (
            _span(),
            _span(span_hash=OTHER_SPAN_HASH, permalink="https://example.test/ADR-1.md#L4"),
        )
        answer = compose_answer(_ready_pack(candidates=(_candidate(spans=spans),)))
        text = render_answer(answer)
        for span in spans:
            assert span.permalink in text

    def test_render_no_answer_is_honest_and_counts_omissions(self) -> None:
        pack = build_cited_context_pack(
            query_hash=QUERY_HASH,
            retrieval_config_version="ask-ledger-v2+test",
            graph_snapshot_hash=SNAPSHOT_HASH,
            candidates=(_candidate(spans=()),),
            limit=5,
        )
        text = render_answer(compose_answer(pack))
        assert NO_ANSWER_HEADLINE in text
        assert "no_cited_support" in text
        assert "missing_citations=1" in text

    def test_render_ready_names_decision_identity(self) -> None:
        text = render_answer(compose_answer(_ready_pack()))
        assert NODE_ID in text
        assert VERSION_ID in text
        assert "1 cited decision" in text


class TestTaxonomyRegistration:
    def test_validation_error_classifies_as_invalid_input(self) -> None:
        assert classify_failure(AskSurfaceValidationError("probe")) is (
            DegradationMode.INVALID_INPUT_REJECTED
        )

    def test_browse_refusal_classifies_as_fail_closed(self) -> None:
        assert classify_failure(BrowseIndexRefusedError("probe")) is (
            DegradationMode.FAIL_CLOSED_REFUSAL
        )
