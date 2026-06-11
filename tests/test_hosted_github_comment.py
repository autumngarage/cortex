"""Tests for the Stage 2 GitHub advisory comment renderer (cortex#390).

The comment body is a stable contract: the live poster (cortex#391) and a
dry-run logger consume the identical string, so the snapshot tests here pin
the body byte-for-byte. The fixtures are shaped like the evaluator's real
output — an ``EmittedFinding`` over a ``FindingDraft``, a ``CitedSourceSpan``
carrying a real permalink, and an ``EvaluationReplayKey`` — so the render
exercises the same field contract production does (the compose-by-file-
contract catch from docs/walkthrough-pe0.md).
"""

from __future__ import annotations

import hashlib
from uuid import UUID

import pytest

from cortex.hosted.advisory_ladder import TIER_EMISSION_BEHAVIOR
from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.confidence import ConfidenceTier
from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.eval_fixtures import FindingClass
from cortex.hosted.evaluator import EmittedFinding, EvaluationReplayKey
from cortex.hosted.github_comment import (
    FINDING_CLASS_LABELS,
    CommentMarker,
    GitHubCommentRenderError,
    ReviewAccounting,
    ReviewReplayMarker,
    extract_marker,
    extract_replay_marker,
    make_marker,
    make_replay_marker,
    render_pr_comment,
)
from cortex.hosted.model_interfaces import FindingDraft

# --- fixtures shaped like the evaluator's real output ------------------------

DECISION_NODE_ID = str(UUID(int=1))
DECISION_VERSION_ID = str(UUID(int=2))
SECOND_NODE_ID = str(UUID(int=11))
SECOND_VERSION_ID = str(UUID(int=12))

SPAN_HASH = hashlib.sha256(b"render-span").hexdigest()
SECOND_SPAN_HASH = hashlib.sha256(b"render-span-2").hexdigest()
ABSENT_SPAN_HASH = hashlib.sha256(b"absent-span").hexdigest()

PERMALINK = "https://github.com/acme/app/blob/abc1234/docs/adr/0007.md#L4-L9"
SECOND_PERMALINK = "https://github.com/acme/app/blob/abc1234/CLAUDE.md#L20-L24"
EXCERPT = "Compose by file contract, not code; Cortex never imports Touchstone."

SPAN = CitedSourceSpan(
    span_hash=SPAN_HASH,
    excerpt=EXCERPT,
    permalink=PERMALINK,
    source_document_id=str(UUID(int=9001)),
    source_id=str(UUID(int=7001)),
)
SECOND_SPAN = CitedSourceSpan(
    span_hash=SECOND_SPAN_HASH,
    excerpt="Use exponential backoff with jitter for webhook retries.",
    permalink=SECOND_PERMALINK,
    source_document_id=str(UUID(int=9002)),
    source_id=str(UUID(int=7002)),
)
SPAN_INDEX = {SPAN_HASH: SPAN, SECOND_SPAN_HASH: SECOND_SPAN}

HEAD_SHA = "abc1234def5678"
PR_NUMBER = 412

MODEL_ID = "anthropic/claude-opus-4"
PROMPT_VERSION = "evaluate-stage0/v1+" + hashlib.sha256(b"prompt").hexdigest()[:12]
GRAPH_SNAPSHOT_HASH = hashlib.sha256(b"graph-snapshot").hexdigest()
GENERIC_HASH = hashlib.sha256(b"generic").hexdigest()

REPLAY_KEY = EvaluationReplayKey(
    graph_snapshot_hash=GRAPH_SNAPSHOT_HASH,
    retrieval_config_version="decisions-for-diff/v1",
    query_hash=GENERIC_HASH,
    candidate_set_hash=GENERIC_HASH,
    context_hash=GENERIC_HASH,
    input_hash=GENERIC_HASH,
    model_id=MODEL_ID,
    prompt_version=PROMPT_VERSION,
    run_id="run-001",
    estimator_version="estimator/v1",
    token_budget=8000,
)


def _emitted(
    *,
    decision_node_id: str = DECISION_NODE_ID,
    decision_version_id: str = DECISION_VERSION_ID,
    finding_class: FindingClass = FindingClass.CONTRADICTS_PRIOR_DECISION,
    tier: ConfidenceTier = ConfidenceTier.CONFIRMED_CITED,
    span_hashes: tuple[str, ...] = (SPAN_HASH,),
    summary: str = (
        "This bridge imports Touchstone directly, reversing the confirmed "
        "compose-by-file-contract decision."
    ),
    suggested_repair: str | None = (
        "Remove the `import touchstone` dependency; integrate by reading "
        "`.touchstone-config` if it exists, or supersede the decision."
    ),
) -> EmittedFinding:
    return EmittedFinding(
        finding=FindingDraft(
            finding_class=finding_class,
            decision_node_id=decision_node_id,
            cited_span_hashes=span_hashes,
            summary=summary,
            confidence_label=tier.value,
            suggested_repair=suggested_repair,
        ),
        decision_version_id=decision_version_id,
        tier=tier,
        behavior=TIER_EMISSION_BEHAVIOR[tier],
    )


def _render(
    findings: tuple[EmittedFinding, ...],
    *,
    accounting: ReviewAccounting | None = None,
    span_by_hash: dict[str, CitedSourceSpan] | None = None,
) -> str:
    return render_pr_comment(
        findings,
        accounting=accounting if accounting is not None else ReviewAccounting(),
        replay_key=REPLAY_KEY,
        pr_number=PR_NUMBER,
        head_sha=HEAD_SHA,
        span_by_hash=SPAN_INDEX if span_by_hash is None else span_by_hash,
    )


# --- a single contradiction finding renders with a clickable permalink -------


def test_single_contradiction_renders_markdown_permalink() -> None:
    body = _render((_emitted(),))
    # The permalink renders as a real markdown link, not bare text.
    assert f"[{EXCERPT}]({PERMALINK})" in body
    assert "advisory" in body.lower()
    assert "never block" in body.lower()


def test_single_contradiction_renders_repair_blockquote() -> None:
    body = _render((_emitted(),))
    assert "> **Suggested repair**" in body
    assert "> Remove the `import touchstone` dependency" in body


def test_finding_label_is_human_not_raw_enum() -> None:
    body = _render((_emitted(),))
    assert "Contradicts a prior decision" in body
    assert "contradicts-prior-decision" not in body


def test_header_counts_distinct_decisions_not_findings() -> None:
    # Two findings against the SAME decision still says "1 recorded decision".
    findings = (
        _emitted(span_hashes=(SPAN_HASH,)),
        _emitted(
            span_hashes=(SPAN_HASH,),
            summary="A second conflict against the same compose decision.",
            suggested_repair=None,
        ),
    )
    body = _render(findings)
    assert "against 1 recorded decision" in body
    assert "against 1 recorded decisions" not in body


# --- multiple findings --------------------------------------------------------


def test_multiple_findings_each_render_with_their_citation() -> None:
    findings = (
        _emitted(),
        _emitted(
            decision_node_id=SECOND_NODE_ID,
            decision_version_id=SECOND_VERSION_ID,
            finding_class=FindingClass.REVERSES_SUPERSEDED_PATTERN,
            span_hashes=(SECOND_SPAN_HASH,),
            summary="This reintroduces the fixed-delay retry we superseded.",
            suggested_repair=None,
        ),
    )
    body = _render(findings)
    assert "#### 1. Contradicts a prior decision" in body
    assert "#### 2. Reverses a superseded pattern" in body
    assert f"]({PERMALINK})" in body
    assert f"]({SECOND_PERMALINK})" in body
    assert "against 2 recorded decisions" in body


def test_finding_without_repair_omits_the_blockquote() -> None:
    body = _render((_emitted(suggested_repair=None),))
    assert "Suggested repair" not in body


def test_finding_tier_glyph_renders_in_heading() -> None:
    body = _render((_emitted(tier=ConfidenceTier.ADVISORY),))
    assert "#### 1. Contradicts a prior decision ▲" in body


# --- no-findings rendering ----------------------------------------------------


def test_no_findings_renders_an_honest_clean_comment() -> None:
    body = _render(())
    assert "No contradictions found" in body
    # Still posts the advisory framing + footer so the absence is visible.
    assert "advisory" in body.lower()
    assert "👍" in body
    assert make_marker(PR_NUMBER, HEAD_SHA) in body


def test_no_findings_with_no_decisions_says_nothing_in_scope() -> None:
    body = _render(())
    assert "No recorded decisions matched" in body


# --- disclosure accounting is visible -----------------------------------------


def test_over_budget_omission_shows_manual_review_signal() -> None:
    body = _render(
        (_emitted(),),
        accounting=ReviewAccounting(omitted_for_budget=7),
    )
    assert "touches many decisions" in body.lower()
    assert "review them manually" in body.lower()
    assert "7 recorded decisions" in body


def test_suppressed_below_floor_count_is_surfaced() -> None:
    body = _render(
        (_emitted(),),
        accounting=ReviewAccounting(suppressed_below_floor=3),
    )
    assert "3 lower-confidence findings" in body
    assert "confidence floor" in body


def test_unconfirmed_twin_and_degraded_reasons_surface() -> None:
    body = _render(
        (_emitted(),),
        accounting=ReviewAccounting(
            unconfirmed_twin_count=1,
            degraded_reasons=("evaluate prompt context truncated to budget",),
        ),
    )
    assert "unconfirmed twin" in body
    assert "Degraded: evaluate prompt context truncated to budget" in body


def test_empty_accounting_states_nothing_was_dropped() -> None:
    body = _render((_emitted(),))
    assert "nothing was suppressed" in body.lower()


def test_disclosure_singular_plural_agreement() -> None:
    body = _render(
        (_emitted(),),
        accounting=ReviewAccounting(
            suppressed_below_floor=1, omitted_for_budget=1, unconfirmed_twin_count=1
        ),
    )
    assert "1 lower-confidence finding " in body
    assert "1 recorded decision " in body
    assert "1 finding had an unconfirmed twin decision" in body


# --- the footer carries feedback affordance + provenance ----------------------


def test_footer_has_feedback_affordance_and_abbreviated_provenance() -> None:
    body = _render((_emitted(),))
    assert "👍" in body and "👎" in body
    assert f"model `{MODEL_ID}`" in body
    assert f"prompt `{PROMPT_VERSION}`" in body
    # The *visible* footer abbreviates the snapshot hash for humans.
    assert f"snapshot `{GRAPH_SNAPSHOT_HASH[:12]}`" in body
    # The full hash never appears in the visible <sub> footer; it lives only in
    # the hidden replay marker (cortex#394) so the feedback loop can bind to it.
    footer = body.split("<sub>", 1)[1]
    assert GRAPH_SNAPSHOT_HASH not in footer


# --- the hidden marker round-trips --------------------------------------------


def test_make_and_extract_marker_round_trip() -> None:
    marker_str = make_marker(PR_NUMBER, HEAD_SHA)
    parsed = extract_marker(marker_str)
    assert parsed == CommentMarker(pr_number=PR_NUMBER, head_sha=HEAD_SHA)


def test_extract_marker_from_full_comment_body() -> None:
    body = _render((_emitted(),))
    parsed = extract_marker(body)
    assert parsed is not None
    assert parsed.pr_number == PR_NUMBER
    assert parsed.head_sha == HEAD_SHA


def test_extract_marker_returns_none_for_unmarked_body() -> None:
    assert extract_marker("Just a regular PR comment with no marker.") is None


# --- the hidden replay marker (cortex#394) ------------------------------------


def test_make_and_extract_replay_marker_round_trip() -> None:
    marker = make_replay_marker(
        model_id=MODEL_ID, prompt_version=PROMPT_VERSION, snapshot_hash=GRAPH_SNAPSHOT_HASH
    )
    parsed = extract_replay_marker(marker)
    assert parsed == ReviewReplayMarker(
        model_id=MODEL_ID, prompt_version=PROMPT_VERSION, snapshot_hash=GRAPH_SNAPSHOT_HASH
    )


def test_extract_replay_marker_from_full_comment_body() -> None:
    body = _render((_emitted(),))
    parsed = extract_replay_marker(body)
    assert parsed is not None
    assert parsed.model_id == MODEL_ID
    assert parsed.prompt_version == PROMPT_VERSION
    # The full 64-hex snapshot is recoverable from the hidden marker (the
    # feedback loop binds to it), even though the visible footer abbreviates.
    assert parsed.snapshot_hash == GRAPH_SNAPSHOT_HASH


def test_extract_replay_marker_returns_none_for_unmarked_body() -> None:
    assert extract_replay_marker("a plain comment") is None


def test_replay_marker_rejects_full_64hex_only() -> None:
    with pytest.raises(GitHubCommentRenderError, match="snapshot_hash"):
        make_replay_marker(
            model_id=MODEL_ID, prompt_version=PROMPT_VERSION, snapshot_hash=GRAPH_SNAPSHOT_HASH[:12]
        )


def test_replay_marker_rejects_delimiter_in_model_id() -> None:
    with pytest.raises(GitHubCommentRenderError, match="model_id"):
        make_replay_marker(
            model_id="bad:id", prompt_version=PROMPT_VERSION, snapshot_hash=GRAPH_SNAPSHOT_HASH
        )


def test_marker_distinguishes_review_states_by_head_sha() -> None:
    old = extract_marker(make_marker(PR_NUMBER, "aaa111"))
    new = extract_marker(make_marker(PR_NUMBER, "bbb222"))
    assert old is not None and new is not None
    # Same PR, different head -> a new review state the poster should update to.
    assert old.pr_number == new.pr_number
    assert old.head_sha != new.head_sha


def test_marker_rejects_non_hex_head_sha() -> None:
    with pytest.raises(GitHubCommentRenderError, match="hex commit SHA"):
        make_marker(PR_NUMBER, "not-a-sha")


def test_marker_rejects_non_positive_pr_number() -> None:
    with pytest.raises(GitHubCommentRenderError, match="pr_number must be >= 1"):
        make_marker(0, HEAD_SHA)


# --- fail-closed citation rendering -------------------------------------------


def test_missing_span_refuses_to_render() -> None:
    with pytest.raises(GitHubCommentRenderError, match="unverifiable citation"):
        _render((_emitted(span_hashes=(ABSENT_SPAN_HASH,)),), span_by_hash={})


def test_render_error_classifies_as_fail_closed_refusal() -> None:
    assert (
        classify_failure(GitHubCommentRenderError("boundary probe"))
        is DegradationMode.FAIL_CLOSED_REFUSAL
    )


def test_every_finding_class_has_a_human_label() -> None:
    assert set(FINDING_CLASS_LABELS) == set(FindingClass)


# --- byte-stable snapshot -----------------------------------------------------


def test_render_is_byte_stable_for_the_same_inputs() -> None:
    findings = (_emitted(),)
    accounting = ReviewAccounting(suppressed_below_floor=2, omitted_for_budget=5)
    first = _render(findings, accounting=accounting)
    second = _render(findings, accounting=accounting)
    assert first == second


def test_full_comment_snapshot() -> None:
    body = _render(
        (_emitted(),),
        accounting=ReviewAccounting(suppressed_below_floor=2, omitted_for_budget=5),
    )
    assert body == (
        f"<!-- cortex-review:pr={PR_NUMBER}:head={HEAD_SHA} -->\n"
        "\n"
        f"<!-- cortex-review-replay:model={MODEL_ID}:prompt={PROMPT_VERSION}:"
        f"snapshot={GRAPH_SNAPSHOT_HASH} -->\n"
        "\n"
        "### Cortex reviewed this PR against 1 recorded decision\n"
        "\n"
        "This is an **advisory** review. Cortex never blocks a merge — every "
        "finding below is a citation to a decision already recorded for this "
        "project, surfaced for you to weigh.\n"
        "\n"
        "Cortex flagged **1 potential conflict** with recorded decisions:\n"
        "\n"
        "#### 1. Contradicts a prior decision ■\n"
        "\n"
        "This bridge imports Touchstone directly, reversing the confirmed "
        "compose-by-file-contract decision.\n"
        "\n"
        f"- Cited decision: [{EXCERPT}]({PERMALINK})\n"
        "\n"
        "> **Suggested repair**\n"
        "> Remove the `import touchstone` dependency; integrate by reading "
        "`.touchstone-config` if it exists, or supersede the decision.\n"
        "\n"
        "**Disclosure**\n"
        "\n"
        "- 2 lower-confidence findings fell below the advisory confidence "
        "floor and were not shown.\n"
        "- This PR touches many decisions: 5 recorded decisions could not fit "
        "the review budget and were not checked — **review them manually**.\n"
        "\n"
        "---\n"
        "React 👍 / 👎 or reply to tell Cortex whether this review was useful "
        "— feedback tunes future reviews.\n"
        "\n"
        f"<sub>model `{MODEL_ID}` · prompt `{PROMPT_VERSION}` · "
        f"snapshot `{GRAPH_SNAPSHOT_HASH[:12]}`</sub>"
    )
