"""GitHub-markdown advisory comment rendering for Stage 2 (cortex#390).

The Stage 2 reviewer posts one advisory PR comment per review. This module
is the pure rendering core: emitted findings plus the disclosure accounting
go in, a GitHub-flavored markdown comment body comes out. It does no IO and
opens no network connection — the live poster (cortex#391) and a dry-run
logger consume the same string, so the comment a reviewer reads in
production is exactly the comment a test snapshots.

Why a separate module from :mod:`cortex.hosted.finding_render`. That module
owns the *finding* contract — the stable per-finding block text shared with
the terminal ``cortex review`` surface. This module owns the *comment*
contract: the advisory header, the markdown framing around each finding, the
disclosure block, the feedback footer, and the stable hidden marker the
poster uses to update its own prior comment instead of duplicating. The two
never re-derive each other's semantics: this renderer reuses
``finding_render``'s excerpt flattening and tier glyphs, and reads the same
``EmittedFinding`` field contract — it does not recompute what a finding
*is*.

Three load-bearing properties:

- **Advisory, never blocking.** The header states it in words, and the
  ladder makes it true: every emitted finding renders as an advisory comment
  while ``BLOCKING_ENABLED`` is ``False`` (cortex#375). The comment cites
  that it never blocks a merge.
- **Fail-closed citations.** Every finding's cited decision must resolve to
  a permalink through the span index, or the render is refused with
  :class:`GitHubCommentRenderError` — an advisory comment never shows a
  citation a reader cannot click through to. This mirrors the evaluator's
  citation gate (cortex#377) and ``finding_render``'s
  :class:`~cortex.hosted.finding_render.FindingRenderError`; reaching it
  means the pack and the emitted findings drifted apart.
- **Disclosure is visible, not hidden.** Suppressed-below-floor findings,
  over-budget omitted decisions (the "touches many decisions; review
  manually" signal), unconfirmed-twin captures, and degraded reasons all
  render in the body. A no-findings review still posts an honest comment so
  the *absence* of contradictions is visible too — silence is not the same
  as "we looked and found nothing."

The hidden marker is an HTML comment carrying the PR number and head SHA:
the poster finds its prior comment by PR (a stable string), and a changed
head SHA means a new review state, so the poster updates the body in place.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cortex.hosted.ask_ledger import CitedSourceSpan
from cortex.hosted.eval_fixtures import FindingClass
from cortex.hosted.evaluator import EmittedFinding, EvaluationOutcome, EvaluationReplayKey
from cortex.hosted.finding_render import TIER_GLYPHS, one_line_excerpt

# Human-facing labels for each finding class. The render layer never shows a
# raw enum value to a PR reviewer; the completeness invariant (every
# FindingClass has a label) is asserted in tests so a new class cannot render
# blank. The semantics live in the evaluator registry — this is presentation
# only.
FINDING_CLASS_LABELS: Mapping[FindingClass, str] = {
    FindingClass.CONTRADICTS_PRIOR_DECISION: "Contradicts a prior decision",
    FindingClass.REVERSES_SUPERSEDED_PATTERN: "Reverses a superseded pattern",
    FindingClass.CITES_MISSING_PATH: "Cites a missing path",
    FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT: "Omits a load-bearing constraint",
}

# The over-budget omission stage key, mirrored from context assembly. A
# non-zero count is the "touches many decisions; review manually" signal.
OVER_BUDGET_OMISSION_KEY = "over_budget"

# The stable hidden marker. The poster greps a comment body for the PR-scoped
# prefix to find its own prior comment; the head SHA distinguishes review
# states so a new push updates the body rather than appending a new comment.
_MARKER_RE = re.compile(
    r"<!-- cortex-review:pr=(?P<pr>\d+):head=(?P<head>[0-9a-fA-F]+) -->"
)
_MARKER_TEMPLATE = "<!-- cortex-review:pr={pr}:head={head} -->"


class GitHubCommentRenderError(ValueError):
    """Raised when an advisory PR comment cannot be rendered verifiably.

    The fail-closed class for this surface: a finding whose cited decision
    does not resolve to a permalink through the span index is refused
    rendering, mirroring the evaluator's citation boundary (cortex#377). An
    advisory comment never shows a citation a reader cannot click.
    """


@dataclass(frozen=True)
class CommentMarker:
    """The PR + head-SHA identity encoded in a comment's hidden marker."""

    pr_number: int
    head_sha: str

    def __post_init__(self) -> None:
        if isinstance(self.pr_number, bool) or not isinstance(self.pr_number, int):
            raise GitHubCommentRenderError("pr_number must be an int")
        if self.pr_number < 1:
            raise GitHubCommentRenderError("pr_number must be >= 1")
        if not self.head_sha.strip():
            raise GitHubCommentRenderError("head_sha must not be empty")
        if not re.fullmatch(r"[0-9a-fA-F]+", self.head_sha):
            raise GitHubCommentRenderError(
                "head_sha must be a hex commit SHA (the head the review ran against)"
            )


@dataclass(frozen=True)
class ReviewAccounting:
    """The disclosure accounting one advisory comment makes visible.

    A pure value object the caller assembles from an
    :class:`~cortex.hosted.evaluator.EvaluationOutcome` (or any equivalent
    source) so this renderer stays free of evaluator orchestration. Every
    field is a count or reason the comment surfaces in its disclosure block:
    silently dropping any of them is the failure mode this object exists to
    prevent.

    - ``suppressed_below_floor`` — valid findings the emission floor held
      back (``EvaluationOutcome.suppressed_below_floor``).
    - ``omitted_for_budget`` — decisions context assembly omitted because the
      pack exceeded the token budget; non-zero is the "touches many
      decisions; review manually" signal
      (``EvaluationOutcome.omitted_for_budget``).
    - ``unconfirmed_twin_count`` — findings whose cited decision had an
      unconfirmed twin that retrieval could not resolve, captured rather than
      emitted (the cortex#373/#374 shadow lanes surface here as a count).
    - ``degraded_reasons`` — every visible degraded-capability reason the
      run carried (``EvaluationOutcome.degraded_reasons``).
    """

    suppressed_below_floor: int = 0
    omitted_for_budget: int = 0
    unconfirmed_twin_count: int = 0
    degraded_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("suppressed_below_floor", self.suppressed_below_floor),
            ("omitted_for_budget", self.omitted_for_budget),
            ("unconfirmed_twin_count", self.unconfirmed_twin_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise GitHubCommentRenderError(f"{name} must be a non-negative int")
        for reason in self.degraded_reasons:
            if not reason.strip():
                raise GitHubCommentRenderError("degraded_reasons entries must not be empty")

    @property
    def has_disclosure(self) -> bool:
        """Whether anything beyond the emitted findings needs surfacing."""

        return bool(
            self.suppressed_below_floor
            or self.omitted_for_budget
            or self.unconfirmed_twin_count
            or self.degraded_reasons
        )

    @classmethod
    def from_outcome(cls, outcome: EvaluationOutcome) -> ReviewAccounting:
        """Project an :class:`EvaluationOutcome` onto the disclosure fields.

        The one mapping from evaluator arithmetic to the comment's disclosure
        block, so the terminal ``cortex review`` surface and the Stage 2 PR
        comment can never compute the same disclosure differently. Each field
        is read from a derived count on the outcome — nothing is recomputed:
        the suppression floor, the over-budget omission, the shadow captures
        (the cortex#373/#374 unconfirmed-twin lanes), and every visible
        degraded reason. The model's own omitted-decision count surfaces as a
        degraded reason so a "we did not see every decision" signal is never
        dropped.
        """

        degraded_reasons = tuple(outcome.degraded_reasons)
        if outcome.model_omitted_decision_count:
            degraded_reasons += (
                f"the evaluate model reported "
                f"{outcome.model_omitted_decision_count} decision(s) it could not "
                "fully weigh in the budgeted context",
            )
        return cls(
            suppressed_below_floor=outcome.suppressed_below_floor,
            omitted_for_budget=outcome.omitted_for_budget,
            unconfirmed_twin_count=outcome.shadow_finding_count,
            degraded_reasons=degraded_reasons,
        )


def make_marker(pr_number: int, head_sha: str) -> str:
    """The stable hidden marker for one PR review state.

    Validates through :class:`CommentMarker` so a malformed marker is never
    embedded; the poster relies on the marker being exact to find and update
    its prior comment.
    """

    marker = CommentMarker(pr_number=pr_number, head_sha=head_sha)
    return _MARKER_TEMPLATE.format(pr=marker.pr_number, head=marker.head_sha)


def extract_marker(body: str) -> CommentMarker | None:
    """Parse the hidden marker from a comment body, or ``None`` if absent.

    Returns the PR + head-SHA identity so the poster can decide: same PR and
    same head means update is a no-op state; same PR, different head means a
    new review state to write; no marker means this is not a Cortex comment.
    """

    match = _MARKER_RE.search(body)
    if match is None:
        return None
    return CommentMarker(pr_number=int(match.group("pr")), head_sha=match.group("head"))


def render_pr_comment(
    findings: Sequence[EmittedFinding],
    *,
    accounting: ReviewAccounting,
    replay_key: EvaluationReplayKey,
    pr_number: int,
    head_sha: str,
    span_by_hash: Mapping[str, CitedSourceSpan],
) -> str:
    """Render the advisory PR comment body for one review.

    Pure: string in, string out, no IO. The live poster and a dry-run logger
    consume the identical body. Fail-closed: a finding whose cited span does
    not resolve through ``span_by_hash`` refuses the whole render with
    :class:`GitHubCommentRenderError` — the comment never ships a citation a
    reader cannot verify.

    The body always carries the hidden marker (so the poster can update its
    own prior comment), an advisory header, the disclosure accounting, and
    the feedback + provenance footer. A no-findings review renders an honest
    "no contradictions found" body — the absence is posted, not swallowed.
    """

    decision_count = _distinct_decision_count(findings)
    blocks: list[str] = [make_marker(pr_number, head_sha)]
    blocks.append(_render_header(len(findings), decision_count))
    if findings:
        for index, emitted in enumerate(findings, start=1):
            blocks.append(
                _render_finding(emitted, index=index, span_by_hash=span_by_hash)
            )
    else:
        blocks.append(_render_no_findings(decision_count))
    blocks.append(_render_disclosure(accounting))
    blocks.append(_render_footer(replay_key))
    return "\n\n".join(blocks)


def _render_header(finding_count: int, decision_count: int) -> str:
    decisions = _plural(decision_count, "recorded decision", "recorded decisions")
    lines = [
        f"### Cortex reviewed this PR against {decisions}",
        "",
        "This is an **advisory** review. Cortex never blocks a merge — every "
        "finding below is a citation to a decision already recorded for this "
        "project, surfaced for you to weigh.",
    ]
    if finding_count:
        findings = _plural(finding_count, "potential conflict", "potential conflicts")
        lines.append("")
        lines.append(f"Cortex flagged **{findings}** with recorded decisions:")
    return "\n".join(lines)


def _render_finding(
    emitted: EmittedFinding,
    *,
    index: int,
    span_by_hash: Mapping[str, CitedSourceSpan],
) -> str:
    finding = emitted.finding
    label = FINDING_CLASS_LABELS.get(finding.finding_class)
    if label is None:
        # Presentation completeness is asserted in tests; reaching this means
        # a new finding class shipped without a label — fail closed rather
        # than show a reviewer a raw enum value.
        raise GitHubCommentRenderError(
            f"no human label registered for finding class "
            f"{finding.finding_class.value!r}; refusing to render a raw enum"
        )
    glyph = TIER_GLYPHS[emitted.tier]
    lines = [
        f"#### {index}. {label} {glyph}",
        "",
        finding.summary,
        "",
        _render_citations(finding.cited_span_hashes, span_by_hash),
    ]
    if finding.suggested_repair is not None:
        lines.append("")
        lines.append("> **Suggested repair**")
        lines.append(f"> {finding.suggested_repair}")
    return "\n".join(lines)


def _render_citations(
    cited_span_hashes: tuple[str, ...],
    span_by_hash: Mapping[str, CitedSourceSpan],
) -> str:
    lines: list[str] = []
    for span_hash in cited_span_hashes:
        span = span_by_hash.get(span_hash)
        if span is None:
            raise GitHubCommentRenderError(
                f"finding cites span hash {span_hash} absent from the span "
                "index; refusing to render an advisory comment with an "
                "unverifiable citation"
            )
        excerpt = one_line_excerpt(span.excerpt)
        lines.append(f"- Cited decision: [{excerpt}]({span.permalink})")
    return "\n".join(lines)


def _render_no_findings(decision_count: int) -> str:
    decisions = _plural(decision_count, "cited decision", "cited decisions")
    if decision_count:
        body = (
            f"No contradictions found against the {decisions} this PR touches. "
            "Cortex looked and found nothing to flag — this comment records "
            "that the review ran, so the absence is visible."
        )
    else:
        body = (
            "No recorded decisions matched the surfaces this PR changes, so "
            "there was nothing to check against. This comment records that the "
            "review ran."
        )
    return f"#### No contradictions found\n\n{body}"


def _render_disclosure(accounting: ReviewAccounting) -> str:
    if not accounting.has_disclosure:
        return (
            "_Disclosure: nothing was suppressed, omitted for budget, or "
            "degraded — every recorded decision in scope was checked._"
        )
    lines = ["**Disclosure**", ""]
    if accounting.suppressed_below_floor:
        suppressed = _plural(
            accounting.suppressed_below_floor,
            "lower-confidence finding",
            "lower-confidence findings",
        )
        lines.append(
            f"- {suppressed} fell below the advisory confidence floor and "
            "were not shown."
        )
    if accounting.omitted_for_budget:
        decisions = _plural(
            accounting.omitted_for_budget, "recorded decision", "recorded decisions"
        )
        lines.append(
            f"- This PR touches many decisions: {decisions} could not fit the "
            "review budget and were not checked — **review them manually**."
        )
    if accounting.unconfirmed_twin_count:
        twins = _plural(
            accounting.unconfirmed_twin_count,
            "finding had an unconfirmed twin decision",
            "findings had unconfirmed twin decisions",
        )
        lines.append(
            f"- {twins} that retrieval could not resolve; captured but not "
            "shown until confirmed."
        )
    for reason in accounting.degraded_reasons:
        lines.append(f"- Degraded: {reason}")
    return "\n".join(lines)


def _render_footer(replay_key: EvaluationReplayKey) -> str:
    provenance = (
        f"model `{replay_key.model_id}` · "
        f"prompt `{replay_key.prompt_version}` · "
        f"snapshot `{_abbrev(replay_key.graph_snapshot_hash)}`"
    )
    return "\n".join(
        [
            "---",
            "React 👍 / 👎 or reply to tell Cortex whether this review was "
            "useful — feedback tunes future reviews.",
            "",
            f"<sub>{provenance}</sub>",
        ]
    )


def _distinct_decision_count(findings: Sequence[EmittedFinding]) -> int:
    """Distinct cited decisions across all findings (header/no-findings copy).

    Counts decision node ids the findings cite, so a review that flags two
    findings against one decision says "1 recorded decision", not "2".
    """

    return len({emitted.decision_node_id for emitted in findings})


def _abbrev(value: str) -> str:
    return value[:12] if len(value) > 12 else value


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"
