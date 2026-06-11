"""Ranking pins for contradiction detection (cortex#367).

Diff-scoped candidate gating is the load-bearing technical bet, and the
ranking weights are its tuning knob. These tests FREEZE the current knob
settings so a weight edit cannot land as an unreviewed side effect:

1. ``SOURCE_WEIGHTS`` exact values and ordering (structure over text).
2. Same-rank dominance: a structural-scope candidate outranks a pure-text
   candidate at every source rank within the retrieval cap — for the
   plain diff-shaped query and for every #512-shaped question template.
3. Python/SQL lockstep: both retrieval SQL builders carry the same fusion
   constants as the Python ``reciprocal_rank_fusion`` (``ask_ledger`` SQL
   inlines literals; ``decisions_for_diff`` SQL interpolates), so the two
   fusion implementations cannot drift apart silently.
4. The fixture-local Stage 0 regime ranks structure strictly above text.
   A decision sharing NO specific identifier with the diff (only generic
   prose) is still gated out entirely (suppressed below floor). Under
   recall-v3 (cortex#556) a decision that shares a SPECIFIC identifier the
   diff changed is retrieved by the content lane, but always ranks strictly
   below any structural match — structure over text holds, while the
   repo-wide-rule recall gap the PE-2 dogfood exposed is closed.

Changing any pinned value here is a deliberate ranking change: it must
ship with a protected-slice eval gate run (cortex#338) against the
committed baselines in the same PR. The assertion messages say so.
"""

from __future__ import annotations

import hashlib

import pytest

from cortex.hosted.ask_ledger import (
    RRF_K,
    SOURCE_WEIGHTS,
    AskLedgerQuery,
    CandidateSource,
    SourceRank,
    ask_ledger_retrieval_sql,
    reciprocal_rank_fusion,
)
from cortex.hosted.decisions_for_diff import (
    MAX_DECISIONS_FOR_DIFF_LIMIT,
    decisions_for_diff_retrieval_sql,
)
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    FixtureDecision,
    FixtureDiff,
    FixtureScope,
    FixtureSourceSpan,
)
from cortex.hosted.question_normalization import QUESTION_STOP_PHRASES
from cortex.hosted.replay_runner import OmissionStage, build_fixture_candidate_pack
from cortex.hosted.scopes import STRUCTURAL_SCOPE_WEIGHTS, ScopeType

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "33333333-3333-4333-8333-333333333333"
NODE_SCOPE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NODE_FULL_TEXT = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
NODE_TRIGRAM = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

GATE_NOTE = (
    "ranking weights are a frozen contract: do not change without a passing "
    "protected-slice eval gate run (cortex#338) against the committed baselines "
    "in the same PR"
)

# Every rank a candidate can occupy inside the shipped retrieval cap.
ALL_RANKS_WITHIN_CAP = tuple(range(1, MAX_DECISIONS_FOR_DIFF_LIMIT + 1))


# ---------------------------------------------------------------------------
# Weight pins
# ---------------------------------------------------------------------------


def test_source_weights_are_pinned_exactly() -> None:
    assert SOURCE_WEIGHTS == {
        CandidateSource.EXACT: 120,
        CandidateSource.SCOPE: 100,
        CandidateSource.FULL_TEXT: 70,
        CandidateSource.TRIGRAM: 55,
        CandidateSource.VECTOR: 50,
        CandidateSource.GRAPH: 35,
    }, GATE_NOTE
    assert RRF_K == 60, GATE_NOTE


def test_source_weight_ordering_prefers_structure_over_text() -> None:
    # The contradiction-detection knob (cortex#367): explicit refs, then the
    # structural scope index, then text legs, then vector, then graph hops.
    ordering = (
        CandidateSource.EXACT,
        CandidateSource.SCOPE,
        CandidateSource.FULL_TEXT,
        CandidateSource.TRIGRAM,
        CandidateSource.VECTOR,
        CandidateSource.GRAPH,
    )
    weights = [SOURCE_WEIGHTS[source] for source in ordering]
    assert weights == sorted(weights, reverse=True), GATE_NOTE
    assert len(set(weights)) == len(weights), "weight ties would make ranking ambiguous"


def test_structural_scope_weights_are_pinned_exactly() -> None:
    # The second half of the knob: which structural match counts most when a
    # diff touches several scope types at once (and, in the fixture-local
    # Stage 0 regime, the candidate score itself).
    assert STRUCTURAL_SCOPE_WEIGHTS == {
        ScopeType.PATH: 100,
        ScopeType.GLOB: 98,
        ScopeType.SYMBOL: 95,
        ScopeType.CONFIG_KEY: 90,
        ScopeType.PACKAGE: 75,
        ScopeType.OWNER: 70,
        ScopeType.SERVICE: 70,
        ScopeType.ISSUE_REF: 65,
        ScopeType.CHANNEL_REF: 55,
    }, GATE_NOTE


# ---------------------------------------------------------------------------
# Same-rank dominance: structure outranks pure text at every rank in the cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rank", ALL_RANKS_WITHIN_CAP)
def test_scope_outranks_pure_full_text_at_every_rank_within_cap(rank: int) -> None:
    fused = reciprocal_rank_fusion(
        (
            SourceRank(NODE_FULL_TEXT, CandidateSource.FULL_TEXT, rank, "full_text:decision_text"),
            SourceRank(NODE_SCOPE, CandidateSource.SCOPE, rank, "scope:path:src/app.py"),
        )
    )
    assert fused[0].decision_node_id == NODE_SCOPE, GATE_NOTE
    assert fused[0].score > fused[1].score


@pytest.mark.parametrize("rank", ALL_RANKS_WITHIN_CAP)
def test_scope_outranks_pure_trigram_at_every_rank_within_cap(rank: int) -> None:
    fused = reciprocal_rank_fusion(
        (
            SourceRank(NODE_TRIGRAM, CandidateSource.TRIGRAM, rank, "trigram:decision_text"),
            SourceRank(NODE_SCOPE, CandidateSource.SCOPE, rank, "scope:path:src/app.py"),
        )
    )
    assert fused[0].decision_node_id == NODE_SCOPE, GATE_NOTE


def test_top_text_match_cannot_beat_same_rank_scope_match() -> None:
    # The strongest pure-text position (rank 1 on one text leg) still loses
    # to a scope match at rank 1: 100/(K+1) > 70/(K+1).
    fused = reciprocal_rank_fusion(
        (
            SourceRank(NODE_FULL_TEXT, CandidateSource.FULL_TEXT, 1, "full_text:decision_text"),
            SourceRank(NODE_SCOPE, CandidateSource.SCOPE, 1, "scope:symbol:retry_with_backoff"),
        )
    )
    assert [row.decision_node_id for row in fused] == [NODE_SCOPE, NODE_FULL_TEXT]
    assert fused[0].reason_codes == ("scope:symbol:retry_with_backoff",)


# ---------------------------------------------------------------------------
# #512-shaped question templates as a ranking test set
# ---------------------------------------------------------------------------


def _question_query(question: str) -> AskLedgerQuery:
    return AskLedgerQuery(
        tenant_id=TENANT_ID,
        query=question,
        visible_source_ids=(SOURCE_ID,),
    )


@pytest.mark.parametrize("phrase", QUESTION_STOP_PHRASES)
def test_question_templates_feed_content_terms_to_the_fts_leg(phrase: str) -> None:
    # Every #512 stop-phrase template strips to the bare topic, so the FTS
    # leg ranks decision content instead of gating on question boilerplate.
    query = _question_query(f"{phrase} retry backoff?")
    assert query.fts_query == "retry backoff"


@pytest.mark.parametrize("phrase", QUESTION_STOP_PHRASES)
def test_question_shaped_queries_rank_scope_above_pure_text(phrase: str) -> None:
    # The #512-shaped retrieval set, pinned as a ranking property: under any
    # natural-question phrasing, a candidate found via the structural-scope
    # index outranks a candidate found only by text similarity at the same
    # source rank. The phrasing changes the FTS input, never the dominance.
    query = _question_query(f"{phrase} retry backoff?")
    assert query.fts_query  # the leg has content to rank with
    fused = reciprocal_rank_fusion(
        (
            SourceRank(NODE_FULL_TEXT, CandidateSource.FULL_TEXT, 1, "full_text:decision_text"),
            SourceRank(NODE_TRIGRAM, CandidateSource.TRIGRAM, 1, "trigram:decision_text"),
            SourceRank(NODE_SCOPE, CandidateSource.SCOPE, 1, "scope:path:src/payments/retry.py"),
        )
    )
    assert fused[0].decision_node_id == NODE_SCOPE, GATE_NOTE


# ---------------------------------------------------------------------------
# Python/SQL lockstep: the SQL fusion must carry the same constants
# ---------------------------------------------------------------------------


def test_ask_ledger_sql_fusion_weights_match_source_weights() -> None:
    # ask_ledger_retrieval_sql inlines the weights as literals; if a Python
    # weight changes without the SQL, the two fusion paths diverge silently.
    sql = ask_ledger_retrieval_sql()
    for source, weight in SOURCE_WEIGHTS.items():
        assert f"WHEN '{source.value}' THEN {weight}.0" in sql, (
            f"ask_ledger SQL fusion weight for {source.value!r} drifted from "
            f"SOURCE_WEIGHTS ({GATE_NOTE})"
        )
    assert f"/ ({RRF_K}.0 + source_rank)" in sql


def test_decisions_for_diff_sql_fusion_weights_match_source_weights() -> None:
    sql = decisions_for_diff_retrieval_sql()
    for source in (
        CandidateSource.SCOPE,
        CandidateSource.FULL_TEXT,
        CandidateSource.TRIGRAM,
        CandidateSource.VECTOR,
        CandidateSource.GRAPH,
    ):
        assert f"WHEN '{source.value}' THEN {SOURCE_WEIGHTS[source]}.0" in sql, (
            f"decisions_for_diff SQL fusion weight for {source.value!r} drifted "
            f"from SOURCE_WEIGHTS ({GATE_NOTE})"
        )
    assert f"/ ({RRF_K}.0 + source_rank)" in sql
    # Diff retrieval has no exact-ref leg: a diff never names a decision id.
    assert "WHEN 'exact'" not in sql


# ---------------------------------------------------------------------------
# Fixture-local regime: text-only similarity is gated out, not just outranked
# ---------------------------------------------------------------------------

_PATCH = """\
diff --git a/src/payments/retry.py b/src/payments/retry.py
index 1111111..2222222 100644
--- a/src/payments/retry.py
+++ b/src/payments/retry.py
@@ -1,2 +1,2 @@
-def retry_with_backoff(attempt: int) -> float:
-    return 2.0 ** attempt
+def retry_with_backoff(attempt: int, jitter: bool = False) -> float:
+    return 0.5
"""


def _span(doc: str, excerpt: str) -> FixtureSourceSpan:
    return FixtureSourceSpan(
        source_document_hash=hashlib.sha256(doc.encode("utf-8")).hexdigest(),
        start_offset=0,
        end_offset=len(excerpt),
        excerpt=excerpt,
        permalink=f"https://github.com/acme/payments/blob/main/{doc}",
    )


def _decision(
    decision_id: str, *, decision_text: str, scopes: tuple[FixtureScope, ...]
) -> FixtureDecision:
    return FixtureDecision(
        decision_id=decision_id,
        decision_text=decision_text,
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-06-01T09:00:00+00:00",
        spans=(_span(f"docs/adr/{decision_id}.md", f"excerpt for {decision_id}"),),
        scopes=scopes,
    )


def test_generic_text_similar_decision_is_gated_out() -> None:
    # "Contradiction detection, not document search": a decision whose prose
    # echoes only GENERIC diff tokens (return/float/attempt) and which governs
    # none of the changed surface never reaches the evaluator. The recall-v3
    # content lane (cortex#556) is specificity-gated, so generic overlap does
    # NOT pull this decision in — the strongest form of "decisions, not docs".
    structural = _decision(
        "governs-retry-path",
        decision_text="Retries in src/payments/retry.py use exponential backoff.",
        scopes=(
            FixtureScope(scope_type=ScopeType.PATH, value="src/payments/retry.py"),
            FixtureScope(scope_type=ScopeType.SYMBOL, value="retry_with_backoff"),
        ),
    )
    generic_text_only = _decision(
        "prose-about-retries",
        decision_text=(
            "attempt return float retry payments — prose echoing only generic "
            "diff tokens without governing its surface or naming a symbol."
        ),
        scopes=(FixtureScope(scope_type=ScopeType.PATH, value="docs/runbook.md"),),
    )
    fixture = EvalFixture(
        fixture_id="ranking-pin-structure-vs-text",
        diff=FixtureDiff(
            repo_owner="acme",
            repo_name="payments",
            base_sha="abc1234",
            head_sha="def5678",
            patch=_PATCH,
        ),
        decisions=(structural, generic_text_only),
    )

    emulation = build_fixture_candidate_pack(fixture)

    packed = {
        emulation.decision_id_by_node_id[candidate.decision_node_id]
        for candidate in emulation.pack.candidates
    }
    assert packed == {"governs-retry-path"}
    assert (
        emulation.omission_stage_by_decision_id["prose-about-retries"]
        is OmissionStage.SUPPRESSED_BELOW_FLOOR
    )
    # PATH (100) + SYMBOL (95): the structural score is the scope-weight sum.
    assert emulation.pack.candidates[0].score == 195.0


def test_specific_identifier_match_is_retrieved_but_ranks_below_structure() -> None:
    # recall-v3 (cortex#556): a decision that shares a SPECIFIC identifier the
    # diff changed (`retry_with_backoff`) is retrieved by the content lane even
    # without a matching path scope — the recall the PE-2 dogfood needed. But
    # structure over text holds: the structural decision still ranks first.
    structural = _decision(
        "governs-retry-path",
        decision_text="Retries in src/payments/retry.py use exponential backoff.",
        scopes=(
            FixtureScope(scope_type=ScopeType.PATH, value="src/payments/retry.py"),
            FixtureScope(scope_type=ScopeType.SYMBOL, value="retry_with_backoff"),
        ),
    )
    names_symbol_off_path = _decision(
        "rule-naming-the-symbol",
        decision_text=(
            "retry_with_backoff must keep exponential backoff; never replace it "
            "with a fixed delay."
        ),
        scopes=(FixtureScope(scope_type=ScopeType.PATH, value="docs/runbook.md"),),
    )
    fixture = EvalFixture(
        fixture_id="ranking-pin-specific-content",
        diff=FixtureDiff(
            repo_owner="acme",
            repo_name="payments",
            base_sha="abc1234",
            head_sha="def5678",
            patch=_PATCH,
        ),
        decisions=(structural, names_symbol_off_path),
    )

    emulation = build_fixture_candidate_pack(fixture)

    ordered = [
        emulation.decision_id_by_node_id[candidate.decision_node_id]
        for candidate in emulation.pack.candidates
    ]
    assert ordered == ["governs-retry-path", "rule-naming-the-symbol"]
    # Structural 195 dominates the content match (one specific term, weight 8).
    by_id = {
        emulation.decision_id_by_node_id[c.decision_node_id]: c
        for c in emulation.pack.candidates
    }
    assert by_id["governs-retry-path"].score == 195.0
    assert by_id["rule-naming-the-symbol"].reason_codes == (
        "content:retry_with_backoff",
    )
