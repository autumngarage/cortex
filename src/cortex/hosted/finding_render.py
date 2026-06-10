"""Cited advisory finding rendering for the hosted reviewer (cortex#376).

One render path for every advisory surface. ``cortex review`` consumes
these blocks for the terminal today, and the Stage 2 GitHub PR comment
renderer (cortex#390) consumes the same blocks for advisory comments — the
block text is a stable, tested contract, so the two surfaces can never
quietly disagree about what a finding looks like.

The contract per block (exactly the text ``cortex review`` shipped before
the extraction):

- header line: ``finding {index}/{total}: {class} [{glyph} {tier} ->
  {behavior}]``,
- the summary, indented two spaces,
- the cited decision node id and version,
- one ``citation:`` permalink line per cited span, each followed by a
  quoted one-line excerpt preview (the permalink is the verifiable source;
  the excerpt is a preview, not the record),
- the suggested repair, when the finding carries one.

Fail-closed: a finding citing a span hash absent from the span index is
refused rendering with :class:`FindingRenderError` — an advisory surface
never renders a citation a reader cannot verify. The evaluator's citation
gate (cortex#377) makes this unreachable for its own emissions; reaching it
means the pack and outcome drifted apart.

Shadow findings (cortex#373/#374) are unrepresentable here by construction:
the renderer accepts :class:`~cortex.hosted.evaluator.EmittedFinding` only,
and a shadow capture has no emission behavior to render.
"""

from __future__ import annotations

from collections.abc import Mapping

from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.confidence import ConfidenceTier
from cortex.hosted.decisions_for_diff import DecisionsForDiffCandidatePack
from cortex.hosted.evaluator import EmittedFinding

# Tier glyphs for the per-finding header. One entry per ladder rung; the
# completeness invariant is asserted in tests so a new tier cannot render
# blank.
TIER_GLYPHS: Mapping[ConfidenceTier, str] = {
    ConfidenceTier.SUGGEST: "·",
    ConfidenceTier.ADVISORY: "▲",
    ConfidenceTier.CONFIRMED_CITED: "■",
}

# Citation excerpts render on one line under the permalink; the permalink is
# the verifiable source, so the inline excerpt is a preview, not the record.
CITATION_EXCERPT_CHARS = 160


class FindingRenderError(ValueError):
    """Raised when a finding block cannot be rendered verifiably."""


def build_span_index(
    pack: DecisionsForDiffCandidatePack,
) -> dict[str, CitedSourceSpan]:
    """Index every cited span in the pack by its span hash."""

    return {
        span.span_hash: span
        for candidate in pack.candidates
        for span in candidate.cited_spans
    }


def one_line_excerpt(excerpt: str) -> str:
    """Flatten an excerpt to one line, truncating past the preview budget."""

    flattened = " ".join(excerpt.split())
    if len(flattened) <= CITATION_EXCERPT_CHARS:
        return flattened
    return flattened[:CITATION_EXCERPT_CHARS] + "…"


def render_finding_block_lines(
    emitted: EmittedFinding,
    *,
    index: int,
    total: int,
    span_by_hash: Mapping[str, CitedSourceSpan],
) -> tuple[str, ...]:
    """Render one emitted finding as its block of report lines.

    ``index``/``total`` are 1-based presentation positions among the emitted
    findings. Every citation resolves through ``span_by_hash`` or the render
    is refused — nothing here recomputes or fabricates provenance.
    """

    if index < 1:
        raise FindingRenderError(f"index must be >= 1; got {index}")
    if total < index:
        raise FindingRenderError(f"total ({total}) must be >= index ({index})")
    finding = emitted.finding
    lines = [
        f"finding {index}/{total}: "
        f"{finding.finding_class.value} "
        f"[{TIER_GLYPHS[emitted.tier]} {emitted.tier.value} -> "
        f"{emitted.behavior.value}]",
        f"  {finding.summary}",
        f"  decision: {emitted.decision_node_id} "
        f"(version {emitted.decision_version_id})",
    ]
    for span_hash in finding.cited_span_hashes:
        span = span_by_hash.get(span_hash)
        if span is None:
            raise FindingRenderError(
                f"emitted finding cites span hash {span_hash} that the span "
                "index does not carry; refusing to render an unverifiable "
                "citation"
            )
        lines.append(f"  citation: {span.permalink}")
        lines.append(f'    "{one_line_excerpt(span.excerpt)}"')
    if finding.suggested_repair is not None:
        lines.append(f"  suggested repair: {finding.suggested_repair}")
    return tuple(lines)


def render_finding_block(
    emitted: EmittedFinding,
    *,
    index: int,
    total: int,
    span_by_hash: Mapping[str, CitedSourceSpan],
) -> str:
    """The newline-joined form of :func:`render_finding_block_lines`."""

    return "\n".join(
        render_finding_block_lines(
            emitted, index=index, total=total, span_by_hash=span_by_hash
        )
    )
