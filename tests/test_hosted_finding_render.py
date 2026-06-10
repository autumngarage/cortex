"""Tests for the shared finding-block renderer (cortex#376).

The block text is a stable contract shared by `cortex review` (terminal)
and the Stage 2 GitHub comment renderer (cortex#390): the snapshot tests
here pin it byte-for-byte so the two surfaces can never quietly disagree.
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
from cortex.hosted.evaluator import EmittedFinding
from cortex.hosted.finding_render import (
    CITATION_EXCERPT_CHARS,
    TIER_GLYPHS,
    FindingRenderError,
    one_line_excerpt,
    render_finding_block,
    render_finding_block_lines,
)
from cortex.hosted.model_interfaces import FindingDraft

DECISION_NODE_ID = str(UUID(int=1))
DECISION_VERSION_ID = str(UUID(int=2))
SPAN_HASH = hashlib.sha256(b"render-span").hexdigest()
PERMALINK = "https://github.com/acme/app/blob/main/docs/adr/0007.md"
EXCERPT = "We use exponential backoff with jitter for webhook retries."

SPAN = CitedSourceSpan(
    span_hash=SPAN_HASH,
    excerpt=EXCERPT,
    permalink=PERMALINK,
    source_document_id=str(UUID(int=9001)),
    source_id=str(UUID(int=7001)),
)
SPAN_INDEX = {SPAN_HASH: SPAN}


def _emitted(
    *,
    tier: ConfidenceTier = ConfidenceTier.ADVISORY,
    suggested_repair: str | None = "Keep exponential backoff or supersede the decision.",
    span_hashes: tuple[str, ...] = (SPAN_HASH,),
) -> EmittedFinding:
    return EmittedFinding(
        finding=FindingDraft(
            finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
            decision_node_id=DECISION_NODE_ID,
            cited_span_hashes=span_hashes,
            summary="The diff replaces exponential backoff with a fixed delay.",
            confidence_label=tier.value,
            suggested_repair=suggested_repair,
        ),
        decision_version_id=DECISION_VERSION_ID,
        tier=tier,
        behavior=TIER_EMISSION_BEHAVIOR[tier],
    )


# --- the stable block contract (consumed by cortex review and cortex#390) ----


def test_render_block_snapshot_with_citation_repair_and_tier_glyph() -> None:
    block = render_finding_block(
        _emitted(), index=1, total=2, span_by_hash=SPAN_INDEX
    )
    assert block == (
        "finding 1/2: contradicts-prior-decision "
        "[▲ advisory -> advisory_comment]\n"
        "  The diff replaces exponential backoff with a fixed delay.\n"
        f"  decision: {DECISION_NODE_ID} (version {DECISION_VERSION_ID})\n"
        f"  citation: {PERMALINK}\n"
        f'    "{EXCERPT}"\n'
        "  suggested repair: Keep exponential backoff or supersede the decision."
    )


def test_render_block_lines_match_the_joined_block() -> None:
    lines = render_finding_block_lines(
        _emitted(), index=1, total=1, span_by_hash=SPAN_INDEX
    )
    block = render_finding_block(_emitted(), index=1, total=1, span_by_hash=SPAN_INDEX)
    assert "\n".join(lines) == block


def test_render_block_omits_repair_line_when_absent() -> None:
    block = render_finding_block(
        _emitted(suggested_repair=None), index=1, total=1, span_by_hash=SPAN_INDEX
    )
    assert "suggested repair:" not in block
    assert block.endswith(f'    "{EXCERPT}"')


def test_render_block_glyph_per_tier() -> None:
    for tier, glyph in TIER_GLYPHS.items():
        block = render_finding_block(
            _emitted(tier=tier), index=1, total=1, span_by_hash=SPAN_INDEX
        )
        assert f"[{glyph} {tier.value} -> " in block


def test_tier_glyphs_cover_every_confidence_tier() -> None:
    assert set(TIER_GLYPHS) == set(ConfidenceTier)


# --- fail-closed citation rendering -------------------------------------------


def test_missing_span_refuses_to_render() -> None:
    with pytest.raises(FindingRenderError, match="unverifiable citation"):
        render_finding_block_lines(
            _emitted(), index=1, total=1, span_by_hash={}
        )


def test_finding_render_error_classifies_as_fail_closed_refusal() -> None:
    assert (
        classify_failure(FindingRenderError("boundary probe"))
        is DegradationMode.FAIL_CLOSED_REFUSAL
    )


def test_index_and_total_are_validated() -> None:
    with pytest.raises(FindingRenderError, match="index must be >= 1"):
        render_finding_block_lines(_emitted(), index=0, total=1, span_by_hash=SPAN_INDEX)
    with pytest.raises(FindingRenderError, match="must be >= index"):
        render_finding_block_lines(_emitted(), index=3, total=2, span_by_hash=SPAN_INDEX)


# --- excerpt preview ------------------------------------------------------------


def test_one_line_excerpt_flattens_whitespace() -> None:
    assert one_line_excerpt("a\n  b\t c") == "a b c"


def test_one_line_excerpt_truncates_past_the_preview_budget() -> None:
    long_excerpt = "x" * (CITATION_EXCERPT_CHARS + 10)
    preview = one_line_excerpt(long_excerpt)
    assert preview == "x" * CITATION_EXCERPT_CHARS + "…"
    assert len(preview) == CITATION_EXCERPT_CHARS + 1
