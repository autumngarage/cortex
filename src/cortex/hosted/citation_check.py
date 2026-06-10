"""Post-hoc eval-harness citation checker for fixture replay (cortex#334).

Three citation layers exist on purpose; this module is layer 2 only, and the
boundary is load-bearing (recorded in the #310 Stage 0 as-built brief):

1. **cortex#377** — runtime fail-closed *inside* the soft evaluator (#370):
   uncited findings are never emitted. Not this module.
2. **cortex#334 (this module)** — the post-hoc eval-harness checker: it
   independently verifies, over frozen fixtures, that every expected or
   emitted finding carries citations that resolve to span material present
   in the fixture's decisions. If #377 works perfectly this checker never
   fires — that redundancy is the point: the harness catches regressions in
   #377 itself.
3. **cortex#382** — the read-surface guardrail: cited, query-scoped answers
   only, no browsable index. Not this module either.

The checker CONSUMES citation validity from the shipped substrate; it never
re-implements it. Span hashes are computed by the fixture's own span
material (``eval_fixtures.FixtureSourceSpan.span_hash``, the same material
contract as ``provenance.SourceSpan.span_hash``), and this module only
resolves cited hashes against that material by lookup. The write side is
already fail-closed (``ledger_events.py`` requires source spans + model
version on ``FINDING_EMITTED``); this is the independent read-side audit of
the same invariant, run over frozen fixtures.
``tests/test_hosted_citation_check.py`` audits that no duplicate
span-validation logic exists here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from cortex.hosted.eval_fixtures import EvalFixture, ExpectedFinding, FixtureSourceSpan
from cortex.hosted.model_interfaces import FindingDraft


class CitationCheckError(ValueError):
    """Raised when checker inputs or verdicts cannot support an honest audit."""


class CitationFailureCode(StrEnum):
    """Reason codes for per-finding citation failures."""

    DANGLING_DECISION_CITATION = "dangling-decision-citation"
    """The finding cites a decision id absent from the fixture's decision set."""

    DANGLING_SPAN_CITATION = "dangling-span-citation"
    """A cited span hash resolves to no span in the fixture's decisions."""

    NO_RESOLVABLE_CITATIONS = "no-resolvable-citations"
    """Zero of the finding's cited span hashes resolve — the hard fail."""


@dataclass(frozen=True)
class CitationFailure:
    """One named citation failure, with the offending material in ``detail``."""

    code: CitationFailureCode
    finding_id: str
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, CitationFailureCode):
            raise CitationCheckError("code must be a CitationFailureCode")
        _require_non_empty("finding_id", self.finding_id)
        _require_non_empty("detail", self.detail)


@dataclass(frozen=True)
class CitationVerdict:
    """Per-finding citation verdict; consistency is enforced, not assumed."""

    finding_id: str
    decision_id: str
    decision_resolved: bool
    cited_span_hashes: tuple[str, ...]
    resolved_span_hashes: tuple[str, ...]
    unresolved_span_hashes: tuple[str, ...]
    resolved_spans: tuple[FixtureSourceSpan, ...]
    failures: tuple[CitationFailure, ...]

    def __post_init__(self) -> None:
        _require_non_empty("finding_id", self.finding_id)
        _require_non_empty("decision_id", self.decision_id)
        if not self.cited_span_hashes:
            raise CitationCheckError("cited_span_hashes must not be empty")
        resolved = set(self.resolved_span_hashes)
        unresolved = set(self.unresolved_span_hashes)
        if resolved & unresolved:
            raise CitationCheckError("resolved and unresolved span hashes must be disjoint")
        if resolved | unresolved != set(self.cited_span_hashes):
            raise CitationCheckError(
                "resolved and unresolved span hashes must partition the cited hashes"
            )
        # Resolution evidence comes from the substrate's own span_hash, never
        # recomputed here: each resolved hash must be backed by fixture span
        # material that hashes to it.
        if tuple(span.span_hash for span in self.resolved_spans) != self.resolved_span_hashes:
            raise CitationCheckError(
                "resolved_spans must carry the fixture span material backing "
                "resolved_span_hashes, in order"
            )
        for failure in self.failures:
            if failure.finding_id != self.finding_id:
                raise CitationCheckError("failures must reference this verdict's finding_id")
        by_code = {
            code: sum(1 for failure in self.failures if failure.code is code)
            for code in CitationFailureCode
        }
        if by_code[CitationFailureCode.DANGLING_DECISION_CITATION] != (
            0 if self.decision_resolved else 1
        ):
            raise CitationCheckError(
                "exactly one dangling-decision failure is required iff the cited "
                "decision did not resolve"
            )
        if by_code[CitationFailureCode.DANGLING_SPAN_CITATION] != len(
            self.unresolved_span_hashes
        ):
            raise CitationCheckError(
                "every unresolved span hash requires exactly one dangling-span failure"
            )
        if by_code[CitationFailureCode.NO_RESOLVABLE_CITATIONS] != (
            0 if self.resolved_span_hashes else 1
        ):
            raise CitationCheckError(
                "zero resolvable citations is a hard fail and must be recorded as one"
            )

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def hard_failed(self) -> bool:
        """True when the finding had zero resolvable citations."""

        return any(
            failure.code is CitationFailureCode.NO_RESOLVABLE_CITATIONS
            for failure in self.failures
        )


@dataclass(frozen=True)
class CitationReport:
    """Fixture-level citation report: per-finding pass/fail, offenders named."""

    fixture_id: str
    fixture_hash: str
    verdicts: tuple[CitationVerdict, ...]

    def __post_init__(self) -> None:
        _require_non_empty("fixture_id", self.fixture_id)
        _require_non_empty("fixture_hash", self.fixture_hash)

    @property
    def passed(self) -> bool:
        return all(verdict.passed for verdict in self.verdicts)

    @property
    def failures(self) -> tuple[CitationFailure, ...]:
        return tuple(failure for verdict in self.verdicts for failure in verdict.failures)

    @property
    def failed_finding_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(verdict.finding_id for verdict in self.verdicts if not verdict.passed)
        )

    @property
    def hard_failed_finding_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(verdict.finding_id for verdict in self.verdicts if verdict.hard_failed)
        )


def check_finding_citations(
    finding: ExpectedFinding | FindingDraft, fixture: EvalFixture
) -> CitationVerdict:
    """Verify every cited span hash resolves to span material in the fixture.

    Accepts either an ``ExpectedFinding`` (the fixture's own expectation) or
    a ``FindingDraft`` emitted by the evaluator during replay. Resolution is
    pure lookup against the fixture's decision spans; a finding with zero
    resolvable citations is a hard fail naming the finding id.
    """

    if isinstance(finding, ExpectedFinding):
        finding_id = finding.finding_id
        decision_id = finding.decision_id
    elif isinstance(finding, FindingDraft):
        # Drafts carry no harness id; reference them by the decision they cite.
        finding_id = f"draft:{finding.decision_node_id}"
        decision_id = finding.decision_node_id
    else:
        raise CitationCheckError(
            f"unsupported finding type {type(finding).__name__!r}; the checker "
            "accepts ExpectedFinding or FindingDraft only"
        )

    span_index: dict[str, FixtureSourceSpan] = {
        span.span_hash: span for decision in fixture.decisions for span in decision.spans
    }
    known_decision_ids = {decision.decision_id for decision in fixture.decisions}

    cited = tuple(dict.fromkeys(finding.cited_span_hashes))
    resolved = tuple(value for value in cited if value in span_index)
    unresolved = tuple(value for value in cited if value not in span_index)

    failures: list[CitationFailure] = []
    decision_resolved = decision_id in known_decision_ids
    if not decision_resolved:
        failures.append(
            CitationFailure(
                code=CitationFailureCode.DANGLING_DECISION_CITATION,
                finding_id=finding_id,
                detail=(
                    f"finding {finding_id!r} cites decision {decision_id!r}, which is "
                    f"absent from fixture {fixture.fixture_id!r}'s decision set"
                ),
            )
        )
    for span_hash in unresolved:
        failures.append(
            CitationFailure(
                code=CitationFailureCode.DANGLING_SPAN_CITATION,
                finding_id=finding_id,
                detail=(
                    f"finding {finding_id!r} cites span hash {span_hash}, which "
                    f"resolves to no span in fixture {fixture.fixture_id!r}'s decisions"
                ),
            )
        )
    if not resolved:
        failures.append(
            CitationFailure(
                code=CitationFailureCode.NO_RESOLVABLE_CITATIONS,
                finding_id=finding_id,
                detail=(
                    f"finding {finding_id!r} has zero resolvable citations in fixture "
                    f"{fixture.fixture_id!r}; an uncited finding can never pass the harness"
                ),
            )
        )

    return CitationVerdict(
        finding_id=finding_id,
        decision_id=decision_id,
        decision_resolved=decision_resolved,
        cited_span_hashes=tuple(finding.cited_span_hashes),
        resolved_span_hashes=resolved,
        unresolved_span_hashes=unresolved,
        resolved_spans=tuple(span_index[value] for value in resolved),
        failures=tuple(failures),
    )


def check_fixture(
    fixture: EvalFixture, *, emitted: Sequence[FindingDraft] = ()
) -> CitationReport:
    """Check the fixture's expected findings plus any replay-emitted drafts.

    ``emitted`` carries the evaluator's ``FindingDraft`` output during
    fixture replay; expected findings are always checked so the harness
    re-verifies the fixture's own citations rather than trusting them.
    """

    findings: tuple[ExpectedFinding | FindingDraft, ...] = (
        *fixture.expected_findings,
        *emitted,
    )
    return CitationReport(
        fixture_id=fixture.fixture_id,
        fixture_hash=fixture.fixture_hash,
        verdicts=tuple(check_finding_citations(finding, fixture) for finding in findings),
    )


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CitationCheckError(f"{name} must be a non-empty string")
