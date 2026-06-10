"""Graph-size false-positive regression matrix (cortex#368).

The failure mode this guards: as a tenant's decision graph grows, an
unbounded (or weakly bounded) gating path would feed the evaluator more
and more candidates, and advisory findings-per-diff would scale with
graph size instead of diff relevance — the noisy-reviewer death spiral.

The matrix synthesizes decision packs at N = 10 / 100 / 1000 (in-memory,
no DB, no model) and pins the bounded-pack invariants at every scale:

- the pack size cap is a constant (``MAX_DECISIONS_FOR_DIFF_LIMIT``);
  growth lands in named omitted counts, never in the pack;
- omission accounting is conservative: every synthesized decision is
  packed or attributed to exactly one named omission stage;
- ``candidate_growth_ratio`` stays sane (within [0, 1], shrinking as
  irrelevant decisions flood in);
- with a scripted worst-case evaluator (one finding per visible
  candidate), findings-per-diff does NOT grow with graph size on
  irrelevant-decision floods — and stays pinned to the cap when the
  flood is structurally relevant;
- the labeled relevant decision stays present at rank 1 at every scale
  (the cortex#341 ``relevant_present`` gate signal).

Scales are deliberately three orders of magnitude (derive limits from
domain; test at scale boundaries): 10 is a young tenant, 100 a working
repo, 1000 the flood regime.
"""

from __future__ import annotations

import hashlib
from functools import cache

import pytest

from cortex.hosted.candidate_metrics import compute_candidate_set_metrics
from cortex.hosted.decisions_for_diff import MAX_DECISIONS_FOR_DIFF_LIMIT
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
    FixtureScope,
    FixtureSourceSpan,
)
from cortex.hosted.model_interfaces import (
    EvaluateRequest,
    EvaluateResult,
    FindingDraft,
)
from cortex.hosted.model_registry import RegisteredPrompt
from cortex.hosted.replay_runner import (
    FixtureRetrievalEmulation,
    OmissionStage,
    ReplayResult,
    build_fixture_candidate_pack,
    run_fixture,
)
from cortex.hosted.scopes import ScopeType

# Three orders of magnitude: young tenant / working repo / flood regime.
SCALES = (10, 100, 1000)

# Large enough that context assembly never drops a packed candidate for
# budget: the matrix isolates graph-size effects from budget effects.
BIG_BUDGET = 1_000_000

PROMPT_VERSION = RegisteredPrompt(
    prompt_id="evaluate-graph-size",
    version_number=1,
    template_text="Judge DIFF against DECISIONS.",
    description="Graph-size regression matrix prompt.",
).prompt_version

PATCH = """\
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
    decision_id: str, *, scopes: tuple[FixtureScope, ...], text: str
) -> FixtureDecision:
    return FixtureDecision(
        decision_id=decision_id,
        decision_text=text,
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-06-01T09:00:00+00:00",
        spans=(_span(f"docs/adr/{decision_id}.md", f"excerpt for {decision_id}"),),
        scopes=scopes,
    )


# The anchor governs the changed surface (path 100 + symbol 95 = 195: always
# rank 1). The secondary is a weaker structural match (path only, 100).
ANCHOR = _decision(
    "rel-anchor-backoff",
    scopes=(
        FixtureScope(scope_type=ScopeType.PATH, value="src/payments/retry.py"),
        FixtureScope(scope_type=ScopeType.SYMBOL, value="retry_with_backoff"),
    ),
    text="Retries in src/payments/retry.py use exponential backoff.",
)
SECONDARY = _decision(
    "rel-secondary-budget",
    scopes=(FixtureScope(scope_type=ScopeType.PATH, value="src/payments/retry.py"),),
    text="Retry budgets for the payments path are capped at five attempts.",
)
EXPECTED = ExpectedFinding(
    finding_id="f-contradicts-backoff",
    finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
    decision_id=ANCHOR.decision_id,
    cited_span_hashes=ANCHOR.span_hashes,
    summary="The diff replaces exponential backoff with a fixed delay.",
)


def _flood_decision(index: int, *, structurally_matched: bool) -> FixtureDecision:
    kind = "hit" if structurally_matched else "miss"
    scope = (
        FixtureScope(scope_type=ScopeType.PATH, value="src/payments/retry.py")
        if structurally_matched
        else FixtureScope(scope_type=ScopeType.PATH, value=f"docs/flood/note-{index:04d}.md")
    )
    return _decision(
        f"flood-{kind}-{index:04d}",
        scopes=(scope,),
        text=f"Flood decision {index:04d} about an unrelated surface.",
    )


@cache
def _fixture(n_flood: int, *, structurally_matched: bool) -> EvalFixture:
    flood = tuple(
        _flood_decision(index, structurally_matched=structurally_matched)
        for index in range(n_flood)
    )
    return EvalFixture(
        fixture_id=f"graph-size-{'hit' if structurally_matched else 'miss'}-{n_flood:04d}",
        diff=FixtureDiff(
            repo_owner="acme",
            repo_name="payments",
            base_sha="abc1234",
            head_sha="def5678",
            patch=PATCH,
        ),
        decisions=(ANCHOR, SECONDARY, *flood),
        expected_findings=(EXPECTED,),
    )


@cache
def _emulation(n_flood: int, *, structurally_matched: bool) -> FixtureRetrievalEmulation:
    return build_fixture_candidate_pack(
        _fixture(n_flood, structurally_matched=structurally_matched)
    )


class _CredulousEvaluateModel:
    """Worst-case scripted evaluator: one finding per visible candidate.

    If gating ever lets graph size leak into the evaluator's input, this
    model converts every leaked candidate straight into an advisory finding
    — so findings-per-diff growing with N is unmissable.
    """

    def evaluate(self, request: EvaluateRequest) -> EvaluateResult:
        findings = tuple(
            FindingDraft(
                finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
                decision_node_id=candidate.decision_node_id,
                cited_span_hashes=tuple(
                    sorted(span.span_hash for span in candidate.cited_spans)
                ),
                summary=f"Scripted finding about {candidate.decision_node_id}.",
                confidence_label="high",
            )
            for candidate in request.candidate_pack.candidates
        )
        return EvaluateResult(
            findings=findings,
            model_id="scripted/credulous-evaluator",
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
        )


def _run(n_flood: int, *, structurally_matched: bool) -> ReplayResult:
    return run_fixture(
        _fixture(n_flood, structurally_matched=structurally_matched),
        _CredulousEvaluateModel(),
        prompt_version=PROMPT_VERSION,
        token_budget=BIG_BUDGET,
    )


# ---------------------------------------------------------------------------
# Bounded-pack invariants under an IRRELEVANT flood
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_flood", SCALES)
def test_pack_size_cap_is_constant_under_irrelevant_flood(n_flood: int) -> None:
    emulation = _emulation(n_flood, structurally_matched=False)
    packed = {
        emulation.decision_id_by_node_id[candidate.decision_node_id]
        for candidate in emulation.pack.candidates
    }
    # The pack holds exactly the structurally relevant decisions — identical
    # membership at every scale — and never exceeds the shipped cap.
    assert packed == {ANCHOR.decision_id, SECONDARY.decision_id}
    assert len(emulation.pack.candidates) <= MAX_DECISIONS_FOR_DIFF_LIMIT


@pytest.mark.parametrize("n_flood", SCALES)
def test_omitted_counts_grow_instead_of_the_pack(n_flood: int) -> None:
    emulation = _emulation(n_flood, structurally_matched=False)
    counts = dict(emulation.pack.omitted_counts)
    # Growth lands in the named suppressed count, one per flood decision...
    assert counts[OmissionStage.SUPPRESSED_BELOW_FLOOR.value] == n_flood
    assert counts[OmissionStage.OVER_LIMIT.value] == 0
    assert counts[OmissionStage.STATUS_FILTERED.value] == 0
    # ...and every flood decision is attributed to that stage by id.
    flood_ids = {f"flood-miss-{index:04d}" for index in range(n_flood)}
    assert {
        decision_id
        for decision_id, stage in emulation.omission_stage_by_decision_id.items()
        if stage is OmissionStage.SUPPRESSED_BELOW_FLOOR
    } == flood_ids


@pytest.mark.parametrize("n_flood", SCALES)
@pytest.mark.parametrize("structurally_matched", (False, True))
def test_omission_accounting_is_conservative(
    n_flood: int, structurally_matched: bool
) -> None:
    # Invariant: packed + omitted-by-stage == every decision synthesized.
    # A decision that vanished without a named stage would be the silent
    # failure this matrix exists to catch.
    fixture = _fixture(n_flood, structurally_matched=structurally_matched)
    emulation = _emulation(n_flood, structurally_matched=structurally_matched)
    assert len(emulation.pack.candidates) + len(emulation.omission_stage_by_decision_id) == len(
        fixture.decisions
    )
    counts = dict(emulation.pack.omitted_counts)
    assert sum(counts.values()) == len(emulation.omission_stage_by_decision_id)
    assert emulation.pack.candidate_pool_size == len(emulation.pack.candidates) + counts[
        OmissionStage.OVER_LIMIT.value
    ]


def test_candidate_growth_ratio_stays_sane_and_shrinks_under_flood() -> None:
    ratios: list[float] = []
    for n_flood in SCALES:
        pack = _emulation(n_flood, structurally_matched=False).pack
        assert 0.0 < pack.candidate_growth_ratio <= 1.0
        # The pool is the structurally relevant set, independent of N.
        assert pack.candidate_pool_size == 2
        assert pack.graph_node_count == 2 + n_flood
        ratios.append(pack.candidate_growth_ratio)
    # Irrelevant graph growth dilutes the ratio; it must never grow with N.
    assert ratios == sorted(ratios, reverse=True)
    assert ratios[0] > ratios[-1]


@pytest.mark.parametrize("n_flood", SCALES)
def test_relevant_decision_stays_present_at_rank_one(n_flood: int) -> None:
    emulation = _emulation(n_flood, structurally_matched=False)
    anchor_node = emulation.decision_node_id_by_decision_id[ANCHOR.decision_id]
    metrics = compute_candidate_set_metrics(
        pack=emulation.pack,
        relevant_decision_ids=(anchor_node,),
    )
    # The cortex#341 gate signal holds at every scale: present, never
    # omitted, and still the top-ranked candidate.
    assert metrics.relevant_present
    assert metrics.omitted_relevant_count == 0
    assert metrics.reciprocal_rank == 1.0


# ---------------------------------------------------------------------------
# Findings-per-diff under the scripted worst-case evaluator
# ---------------------------------------------------------------------------


def test_findings_per_diff_constant_under_irrelevant_flood() -> None:
    findings_by_scale: dict[int, int] = {}
    for n_flood in SCALES:
        result = _run(n_flood, structurally_matched=False)
        # The expected contradiction is still found amid the flood...
        assert result.matched_count == 1
        # ...and even a credulous evaluator cannot emit more findings than
        # the gated pack shows it: anchor (matched) + secondary (unexpected).
        findings_by_scale[n_flood] = result.matched_count + result.unexpected_count
        assert result.unexpected_count == 1
        assert result.needs_manual_review is False
    assert len(set(findings_by_scale.values())) == 1, (
        f"findings-per-diff must not grow with graph size on irrelevant "
        f"floods; got {findings_by_scale}"
    )


def test_findings_per_diff_bounded_by_cap_under_matched_flood() -> None:
    # Even when the flood IS structurally matched (worst case: every flood
    # decision claims the changed path), findings are bounded by the pack
    # cap — growth past the cap lands in over_limit, not in findings.
    findings_by_scale: dict[int, int] = {}
    for n_flood in SCALES:
        result = _run(n_flood, structurally_matched=True)
        emulation = _emulation(n_flood, structurally_matched=True)
        pool = 2 + n_flood
        expected_pack = min(pool, MAX_DECISIONS_FOR_DIFF_LIMIT)
        assert len(emulation.pack.candidates) == expected_pack
        assert emulation.pack.omitted_counts[OmissionStage.OVER_LIMIT.value] == (
            pool - expected_pack
        )
        findings = result.matched_count + result.unexpected_count
        assert findings == expected_pack
        assert findings <= MAX_DECISIONS_FOR_DIFF_LIMIT
        # The anchor outranks every path-only flood decision (195 > 100).
        assert result.matched_count == 1
        findings_by_scale[n_flood] = findings
    # Past the cap, scale stops mattering entirely.
    assert findings_by_scale[100] == findings_by_scale[1000] == MAX_DECISIONS_FOR_DIFF_LIMIT


@pytest.mark.parametrize("n_flood", SCALES)
def test_replay_results_are_deterministic_at_every_scale(n_flood: int) -> None:
    first = _run(n_flood, structurally_matched=False)
    second = _run(n_flood, structurally_matched=False)
    assert first.to_canonical_json() == second.to_canonical_json()
