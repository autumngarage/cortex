"""Tests for the post-hoc eval-harness citation checker (cortex#334)."""

from __future__ import annotations

import hashlib
import inspect

import pytest

import cortex.hosted.citation_check as citation_check_module
from cortex.hosted.citation_check import (
    CitationCheckError,
    CitationFailure,
    CitationFailureCode,
    CitationVerdict,
    check_finding_citations,
    check_fixture,
)
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
    FixtureSourceSpan,
)
from cortex.hosted.model_interfaces import FindingDraft

DOC_CONTENT = (
    "## Retry policy\n\nWe decided that outbound webhook retries use exponential "
    "backoff with jitter; fixed-interval retries are forbidden.\n\n## Storage\n\n"
    "Postgres is the only canonical hosted store; SQLite is cache-only.\n"
)
DOC_HASH = hashlib.sha256(DOC_CONTENT.encode("utf-8")).hexdigest()
BOGUS_HASH = hashlib.sha256(b"not-in-any-fixture").hexdigest()
SECOND_BOGUS_HASH = hashlib.sha256(b"also-not-in-any-fixture").hexdigest()


def _span(needle: str) -> FixtureSourceSpan:
    start = DOC_CONTENT.index(needle)
    return FixtureSourceSpan(
        source_document_hash=DOC_HASH,
        start_offset=start,
        end_offset=start + len(needle),
        excerpt=needle,
        permalink="https://github.com/acme/payments/blob/main/docs/adr/0007.md",
    )


RETRY_SPAN = _span("outbound webhook retries use exponential backoff with jitter")
STORAGE_SPAN = _span("Postgres is the only canonical hosted store")


def _fixture() -> EvalFixture:
    retry_decision = FixtureDecision(
        decision_id="retry-backoff-decision",
        decision_text="Outbound webhook retries use exponential backoff with jitter.",
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-05-14T09:30:00+00:00",
        spans=(RETRY_SPAN,),
    )
    storage_decision = FixtureDecision(
        decision_id="storage-boundary-decision",
        decision_text="Postgres is the only canonical hosted store.",
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-05-20T10:00:00+00:00",
        spans=(STORAGE_SPAN,),
    )
    finding = ExpectedFinding(
        finding_id="finding-fixed-retry",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="retry-backoff-decision",
        cited_span_hashes=(RETRY_SPAN.span_hash,),
        summary="The diff replaces exponential backoff with a fixed retry loop.",
    )
    diff = FixtureDiff(
        repo_owner="acme",
        repo_name="payments",
        base_sha="a1b2c3d4e5f6a7b8",
        head_sha="b2c3d4e5f6a7b8c9",
        patch="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-delay = backoff(n)\n+delay = 5.0\n",
        changed_paths=("src/payments/webhook_client.py",),
    )
    return EvalFixture(
        fixture_id="contradiction-001",
        diff=diff,
        decisions=(retry_decision, storage_decision),
        expected_findings=(finding,),
    )


def _draft(
    *,
    decision_node_id: str = "retry-backoff-decision",
    cited_span_hashes: tuple[str, ...] = (RETRY_SPAN.span_hash,),
) -> FindingDraft:
    return FindingDraft(
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_node_id=decision_node_id,
        cited_span_hashes=cited_span_hashes,
        summary="Replay-emitted finding under citation audit.",
        confidence_label="high",
    )


def test_fully_cited_expected_finding_passes() -> None:
    fixture = _fixture()
    verdict = check_finding_citations(fixture.expected_findings[0], fixture)
    assert verdict.passed
    assert verdict.failures == ()
    assert verdict.decision_resolved
    assert verdict.resolved_span_hashes == (RETRY_SPAN.span_hash,)
    assert verdict.unresolved_span_hashes == ()
    # The verdict reuses the fixture's span material, not a re-derivation.
    assert verdict.resolved_spans == (RETRY_SPAN,)


def test_check_fixture_report_shape_for_fully_cited_fixture() -> None:
    fixture = _fixture()
    report = check_fixture(fixture)
    assert report.fixture_id == fixture.fixture_id
    assert report.fixture_hash == fixture.fixture_hash
    assert len(report.verdicts) == len(fixture.expected_findings)
    assert report.passed
    assert report.failures == ()
    assert report.failed_finding_ids == ()
    assert report.hard_failed_finding_ids == ()


def test_dangling_span_hash_fails_naming_the_offending_hash() -> None:
    fixture = _fixture()
    draft = _draft(cited_span_hashes=(RETRY_SPAN.span_hash, BOGUS_HASH))
    verdict = check_finding_citations(draft, fixture)
    assert not verdict.passed
    assert not verdict.hard_failed  # one citation still resolves
    assert verdict.unresolved_span_hashes == (BOGUS_HASH,)
    codes = [failure.code for failure in verdict.failures]
    assert codes == [CitationFailureCode.DANGLING_SPAN_CITATION]
    assert BOGUS_HASH in verdict.failures[0].detail


def test_zero_resolvable_citations_is_a_hard_fail_naming_the_finding() -> None:
    fixture = _fixture()
    draft = _draft(cited_span_hashes=(BOGUS_HASH, SECOND_BOGUS_HASH))
    verdict = check_finding_citations(draft, fixture)
    assert not verdict.passed
    assert verdict.hard_failed
    hard = [
        failure
        for failure in verdict.failures
        if failure.code is CitationFailureCode.NO_RESOLVABLE_CITATIONS
    ]
    assert len(hard) == 1
    assert verdict.finding_id in hard[0].detail
    # And the fixture-level report names the finding among hard failures.
    report = check_fixture(fixture, emitted=(draft,))
    assert report.hard_failed_finding_ids == (verdict.finding_id,)


def test_dangling_decision_id_fails_with_dangling_citation_code() -> None:
    fixture = _fixture()
    draft = _draft(decision_node_id="decision-nobody-recorded")
    verdict = check_finding_citations(draft, fixture)
    assert not verdict.passed
    assert not verdict.decision_resolved
    codes = [failure.code for failure in verdict.failures]
    assert codes == [CitationFailureCode.DANGLING_DECISION_CITATION]
    assert "decision-nobody-recorded" in verdict.failures[0].detail


def test_check_fixture_combines_expected_and_emitted_verdicts() -> None:
    fixture = _fixture()
    bad_draft = _draft(cited_span_hashes=(BOGUS_HASH,))
    good_draft = _draft(cited_span_hashes=(STORAGE_SPAN.span_hash,))
    # good_draft cites the storage span but the retry decision: span resolution
    # is fixture-wide, so it still passes the citation check.
    report = check_fixture(fixture, emitted=(bad_draft, good_draft))
    assert len(report.verdicts) == 3
    assert not report.passed
    assert report.failed_finding_ids == ("draft:retry-backoff-decision",)
    assert report.hard_failed_finding_ids == ("draft:retry-backoff-decision",)


def test_unsupported_finding_type_is_rejected() -> None:
    fixture = _fixture()
    with pytest.raises(CitationCheckError, match="unsupported finding type"):
        check_finding_citations("not-a-finding", fixture)  # type: ignore[arg-type]


def test_verdict_enforces_partition_and_hard_fail_invariants() -> None:
    fixture = _fixture()
    verdict = check_finding_citations(fixture.expected_findings[0], fixture)
    with pytest.raises(CitationCheckError, match="partition"):
        CitationVerdict(
            finding_id=verdict.finding_id,
            decision_id=verdict.decision_id,
            decision_resolved=True,
            cited_span_hashes=verdict.cited_span_hashes,
            resolved_span_hashes=(),
            unresolved_span_hashes=(),
            resolved_spans=(),
            failures=(),
        )
    with pytest.raises(CitationCheckError, match="hard fail"):
        CitationVerdict(
            finding_id=verdict.finding_id,
            decision_id=verdict.decision_id,
            decision_resolved=True,
            cited_span_hashes=(BOGUS_HASH,),
            resolved_span_hashes=(),
            unresolved_span_hashes=(BOGUS_HASH,),
            resolved_spans=(),
            failures=(
                # Dangling-span failure present, but the mandatory
                # zero-resolvable hard fail is missing.
                CitationFailure(
                    code=CitationFailureCode.DANGLING_SPAN_CITATION,
                    finding_id=verdict.finding_id,
                    detail=f"cites span hash {BOGUS_HASH}, which resolves to no span",
                ),
            ),
        )


def test_no_duplicate_span_validation_logic_in_the_harness() -> None:
    """cortex#334: the checker consumes provenance validity, never re-implements it.

    Span hashes must come from the substrate (``eval_fixtures`` /
    ``provenance``); the checker is pure lookup. Hash or pattern machinery
    appearing here would be duplicate span-validation logic.
    """

    source = inspect.getsource(citation_check_module)
    for token in ("hashlib", "sha256", "import re"):
        assert token not in source, f"citation_check must not re-implement spans: {token}"
    assert "from cortex.hosted.eval_fixtures import" in source
