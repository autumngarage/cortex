"""Tests for the frozen eval-fixture format (cortex#332)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cortex.hosted.eval_fixtures import (
    EVAL_FIXTURE_SCHEMA_VERSION,
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
    FixtureLabel,
    FixtureSourceSpan,
    FixtureValidationError,
    LabelClass,
)

EXAMPLE_PATH = (
    Path(__file__).parent / "fixtures" / "hosted_eval" / "contradiction-001.json"
)

DOC_CONTENT = (
    "## Retry policy\n\nWe decided on 2026-05-14 that all outbound webhook retries "
    "use exponential backoff with jitter; fixed-interval retries are forbidden "
    "because they synchronized thundering herds during the May incident.\n"
)
DOC_HASH = hashlib.sha256(DOC_CONTENT.encode("utf-8")).hexdigest()


def _example_span() -> FixtureSourceSpan:
    start = DOC_CONTENT.index("all outbound")
    end = DOC_CONTENT.index("May incident.") + len("May incident.")
    return FixtureSourceSpan(
        source_document_hash=DOC_HASH,
        start_offset=start,
        end_offset=end,
        excerpt=DOC_CONTENT[start:end],
        permalink="https://github.com/acme/payments/blob/main/docs/adr/0007-retry-policy.md",
    )


def _example_fixture() -> EvalFixture:
    span = _example_span()
    decision = FixtureDecision(
        decision_id="retry-backoff-decision",
        decision_text=(
            "Outbound webhook retries use exponential backoff with jitter; "
            "fixed-interval retries are forbidden."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-05-14T09:30:00+00:00",
        spans=(span,),
        scopes=(),
    )
    finding = ExpectedFinding(
        finding_id="finding-fixed-retry",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="retry-backoff-decision",
        cited_span_hashes=(span.span_hash,),
        summary=(
            "The diff replaces exponential backoff with a fixed 5s retry loop in "
            "webhook_client.py, contradicting the confirmed retry-policy decision."
        ),
        suggested_repair="Restore exponential backoff with jitter (see cited ADR span).",
    )
    label = FixtureLabel(
        finding_id="finding-fixed-retry",
        label=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
        note="Exactly the incident class the ledger exists to prevent.",
    )
    diff = FixtureDiff(
        repo_owner="acme",
        repo_name="payments",
        base_sha="a1b2c3d4e5f6a7b8",
        head_sha="b2c3d4e5f6a7b8c9",
        patch=(
            "--- a/src/payments/webhook_client.py\n"
            "+++ b/src/payments/webhook_client.py\n"
            "@@ -10,7 +10,7 @@\n"
            "-    delay = backoff_with_jitter(attempt)\n"
            "+    delay = 5.0\n"
        ),
        changed_paths=("src/payments/webhook_client.py",),
        symbols=("backoff_with_jitter",),
    )
    return EvalFixture(
        fixture_id="contradiction-001",
        diff=diff,
        decisions=(decision,),
        expected_findings=(finding,),
        labels=(label,),
        metadata={"scenario": "fixed-retry-contradiction", "source": "hand-authored"},
    )


def test_example_fixture_file_round_trips_byte_identical() -> None:
    raw = EXAMPLE_PATH.read_text(encoding="utf-8")
    fixture = EvalFixture.from_json(raw)
    assert fixture.to_canonical_json() == raw
    reloaded = EvalFixture.from_json(fixture.to_canonical_json())
    assert reloaded.to_canonical_json() == raw
    assert reloaded.fixture_hash == fixture.fixture_hash


def test_example_fixture_matches_module_construction() -> None:
    raw = EXAMPLE_PATH.read_text(encoding="utf-8")
    assert _example_fixture().to_canonical_json() == raw


def test_unknown_schema_version_fails_visibly() -> None:
    payload = json.loads(_example_fixture().to_canonical_json())
    payload["fixture_schema_version"] = EVAL_FIXTURE_SCHEMA_VERSION + 1
    with pytest.raises(FixtureValidationError, match="unknown fixture_schema_version"):
        EvalFixture.from_payload(payload)


def test_missing_schema_version_fails_visibly() -> None:
    payload = json.loads(_example_fixture().to_canonical_json())
    del payload["fixture_schema_version"]
    with pytest.raises(FixtureValidationError, match="fixture_schema_version"):
        EvalFixture.from_payload(payload)


def test_label_taxonomy_is_the_owned_four_class_set() -> None:
    assert {label.value for label in LabelClass} == {
        "correct_useful",
        "correct_not_useful",
        "incorrect_precision",
        "missed_expected",
    }
    # The FP-vs-tone split #342 reports on: tone problems are correctness-true,
    # so the two classes must remain distinct labels.
    assert len({label.value for label in LabelClass}) == len(list(LabelClass))


def test_expected_finding_must_cite_known_span() -> None:
    fixture = _example_fixture()
    bogus = ExpectedFinding(
        finding_id="finding-uncited",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="retry-backoff-decision",
        cited_span_hashes=(hashlib.sha256(b"elsewhere").hexdigest(),),
        summary="Cites a span the fixture does not contain.",
    )
    with pytest.raises(FixtureValidationError, match="cites span hashes absent"):
        EvalFixture(
            fixture_id=fixture.fixture_id,
            diff=fixture.diff,
            decisions=fixture.decisions,
            expected_findings=(*fixture.expected_findings, bogus),
        )


def test_expected_finding_requires_at_least_one_citation() -> None:
    with pytest.raises(FixtureValidationError, match="at least one cited span hash"):
        ExpectedFinding(
            finding_id="finding-vibes",
            finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
            decision_id="retry-backoff-decision",
            cited_span_hashes=(),
            summary="No citation.",
        )


def test_decision_requires_provenance_span() -> None:
    with pytest.raises(FixtureValidationError, match="at least one provenance span"):
        FixtureDecision(
            decision_id="span-free",
            decision_text="No provenance.",
            status=DecisionStatus.CONFIRMED,
            source_timestamp="2026-06-09T00:00:00+00:00",
            spans=(),
        )


def test_label_must_reference_known_finding() -> None:
    fixture = _example_fixture()
    with pytest.raises(FixtureValidationError, match="unknown finding"):
        EvalFixture(
            fixture_id=fixture.fixture_id,
            diff=fixture.diff,
            decisions=fixture.decisions,
            expected_findings=fixture.expected_findings,
            labels=(
                FixtureLabel(
                    finding_id="finding-nonexistent",
                    label=LabelClass.MISSED_EXPECTED,
                    grader="henry",
                    graded_at="2026-06-09",
                ),
            ),
        )


def test_superseded_by_requires_superseded_status_and_known_target() -> None:
    span = _example_span()
    with pytest.raises(FixtureValidationError, match="requires status 'superseded'"):
        FixtureDecision(
            decision_id="bad-supersede",
            decision_text="Wrong status.",
            status=DecisionStatus.CONFIRMED,
            source_timestamp="2026-06-09T00:00:00+00:00",
            spans=(span,),
            superseded_by="retry-backoff-decision",
        )

    old = FixtureDecision(
        decision_id="old-decision",
        decision_text="Old rule.",
        status=DecisionStatus.SUPERSEDED,
        source_timestamp="2026-04-01T00:00:00+00:00",
        spans=(span,),
        superseded_by="missing-decision",
    )
    fixture = _example_fixture()
    with pytest.raises(FixtureValidationError, match="unknown decisions"):
        EvalFixture(
            fixture_id="bad-target",
            diff=fixture.diff,
            decisions=(*fixture.decisions, old),
        )


def test_span_hash_recorded_in_payload_is_verified() -> None:
    span = _example_span()
    payload = span.as_payload()
    payload["span_hash"] = hashlib.sha256(b"tampered").hexdigest()
    with pytest.raises(FixtureValidationError, match="span_hash does not match"):
        FixtureSourceSpan.from_payload(payload)


def test_span_hash_matches_hosted_provenance_material() -> None:
    """Fixture span hashes use the same material as provenance.SourceSpan."""

    span = _example_span()
    expected = hashlib.sha256(
        json.dumps(
            {
                "end_offset": span.end_offset,
                "excerpt_hash": hashlib.sha256(span.excerpt.encode("utf-8")).hexdigest(),
                "source_document_hash": span.source_document_hash,
                "start_offset": span.start_offset,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert span.span_hash == expected


def test_duplicate_decision_ids_rejected() -> None:
    fixture = _example_fixture()
    with pytest.raises(FixtureValidationError, match="must be unique"):
        EvalFixture(
            fixture_id="dupes",
            diff=fixture.diff,
            decisions=(*fixture.decisions, *fixture.decisions),
        )
