"""Tests for the hand-labeling workflow (cortex#333) and the Stage 0 gate
report template (cortex#343)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
    FixtureSourceSpan,
    LabelClass,
)
from cortex.hosted.labeling import (
    GRADED_EMITTED_CLASSES,
    OVERRIDE_CONTEXT_CLASSES,
    SPOT_CHECK_FRACTION_DENOMINATOR,
    SPOT_CHECK_MINIMUM_ITEMS,
    DisagreementReport,
    LabelingError,
    apply_label,
    disagreement_report,
    label_tally,
    load_unlabeled_findings,
    spot_check_sample_size,
)

TEMPLATE_PATH = Path(__file__).parent.parent / "docs" / "templates" / "stage0-gate-report.md"

DOC_CONTENT = (
    "## Retry policy\n\nWe decided on 2026-05-14 that all outbound webhook retries "
    "use exponential backoff with jitter; fixed-interval retries are forbidden "
    "because they synchronized thundering herds during the May incident.\n"
)
DOC_HASH = hashlib.sha256(DOC_CONTENT.encode("utf-8")).hexdigest()


def _span() -> FixtureSourceSpan:
    start = DOC_CONTENT.index("all outbound")
    end = DOC_CONTENT.index("May incident.") + len("May incident.")
    return FixtureSourceSpan(
        source_document_hash=DOC_HASH,
        start_offset=start,
        end_offset=end,
        excerpt=DOC_CONTENT[start:end],
        permalink="https://github.com/acme/payments/blob/main/docs/adr/0007-retry-policy.md",
    )


def _fixture(fixture_id: str = "contradiction-001") -> EvalFixture:
    span = _span()
    decision = FixtureDecision(
        decision_id="retry-backoff-decision",
        decision_text="Outbound webhook retries use exponential backoff with jitter.",
        status=DecisionStatus.CONFIRMED,
        source_timestamp="2026-05-14T09:30:00+00:00",
        spans=(span,),
    )
    findings = (
        ExpectedFinding(
            finding_id="finding-fixed-retry",
            finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
            decision_id="retry-backoff-decision",
            cited_span_hashes=(span.span_hash,),
            summary="The diff replaces exponential backoff with a fixed retry loop.",
        ),
        ExpectedFinding(
            finding_id="finding-missing-jitter",
            finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
            decision_id="retry-backoff-decision",
            cited_span_hashes=(span.span_hash,),
            summary="The diff drops the jitter the retry decision requires.",
        ),
    )
    diff = FixtureDiff(
        repo_owner="acme",
        repo_name="payments",
        base_sha="a1b2c3d4e5f6a7b8",
        head_sha="b2c3d4e5f6a7b8c9",
        patch="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-delay = backoff(attempt)\n+delay = 5.0\n",
        changed_paths=("x.py",),
    )
    return EvalFixture(
        fixture_id=fixture_id,
        diff=diff,
        decisions=(decision,),
        expected_findings=findings,
    )


# ---------------------------------------------------------------------------
# load_unlabeled_findings + apply_label (cortex#333 core workflow)
# ---------------------------------------------------------------------------


def test_load_unlabeled_findings_lists_expected_findings_without_labels() -> None:
    fixture = _fixture()
    unlabeled = load_unlabeled_findings(fixture)
    assert [finding.finding_id for finding in unlabeled] == [
        "finding-fixed-retry",
        "finding-missing-jitter",
    ]


def test_apply_label_returns_new_fixture_and_shrinks_unlabeled_set() -> None:
    fixture = _fixture()
    labeled = apply_label(
        fixture,
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
    )
    assert labeled is not fixture
    assert fixture.labels == ()  # the input fixture is never mutated
    assert [label.finding_id for label in labeled.labels] == ["finding-fixed-retry"]
    assert [finding.finding_id for finding in load_unlabeled_findings(labeled)] == [
        "finding-missing-jitter"
    ]


def test_apply_label_accepts_taxonomy_value_strings() -> None:
    labeled = apply_label(
        _fixture(),
        finding_id="finding-fixed-retry",
        label_class="incorrect_precision",
        grader="henry",
        graded_at="2026-06-09",
    )
    assert labeled.labels[0].label is LabelClass.INCORRECT_PRECISION


def test_apply_label_rejects_unknown_label_class_visibly() -> None:
    with pytest.raises(LabelingError, match="taxonomy is fixed"):
        apply_label(
            _fixture(),
            finding_id="finding-fixed-retry",
            label_class="mostly-fine",
            grader="henry",
            graded_at="2026-06-09",
        )


def test_apply_label_rejects_unknown_finding_visibly() -> None:
    with pytest.raises(LabelingError, match="does not exist in fixture"):
        apply_label(
            _fixture(),
            finding_id="finding-nonexistent",
            label_class=LabelClass.CORRECT_USEFUL,
            grader="henry",
            graded_at="2026-06-09",
        )


@pytest.mark.parametrize(
    ("grader", "graded_at"),
    [("", "2026-06-09"), ("   ", "2026-06-09"), ("henry", ""), ("henry", "  ")],
)
def test_apply_label_rejects_missing_provenance_visibly(grader: str, graded_at: str) -> None:
    with pytest.raises(LabelingError, match="provenance is mandatory"):
        apply_label(
            _fixture(),
            finding_id="finding-fixed-retry",
            label_class=LabelClass.CORRECT_USEFUL,
            grader=grader,
            graded_at=graded_at,
        )


def test_apply_label_rejects_same_grader_relabeling_append_only() -> None:
    labeled = apply_label(
        _fixture(),
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
    )
    with pytest.raises(LabelingError, match="append-only"):
        apply_label(
            labeled,
            finding_id="finding-fixed-retry",
            label_class=LabelClass.CORRECT_NOT_USEFUL,
            grader="henry",
            graded_at="2026-06-10",
        )


def test_second_grader_appends_a_second_label_for_inter_rater_review() -> None:
    fixture = apply_label(
        _fixture(),
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
    )
    fixture = apply_label(
        fixture,
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_NOT_USEFUL,
        grader="sam",
        graded_at="2026-06-09",
    )
    assert [(label.grader, label.label) for label in fixture.labels] == [
        ("henry", LabelClass.CORRECT_USEFUL),
        ("sam", LabelClass.CORRECT_NOT_USEFUL),
    ]


def test_labeling_never_modifies_diff_decisions_or_expected_findings() -> None:
    fixture = _fixture()
    before = fixture.as_payload()
    labeled = apply_label(
        fixture,
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
        note="Exactly the incident class the ledger exists to prevent.",
    )
    after = labeled.as_payload()
    for section in ("diff", "decisions", "expected_findings"):
        before_bytes = json.dumps(before[section], sort_keys=True).encode("utf-8")
        after_bytes = json.dumps(after[section], sort_keys=True).encode("utf-8")
        assert before_bytes == after_bytes, f"labeling modified the {section} section"


def test_labeled_fixture_round_trips_through_canonical_json_with_zero_fixups(
    tmp_path: Path,
) -> None:
    path = tmp_path / "contradiction-001.json"
    path.write_text(_fixture().to_canonical_json(), encoding="utf-8")

    loaded = EvalFixture.from_json(path.read_text(encoding="utf-8"))
    labeled = apply_label(
        loaded,
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
        note="Hand graded.",
    )
    path.write_text(labeled.to_canonical_json(), encoding="utf-8")

    # The shipped loader validates the emitted file as-is: no manual fixups.
    reloaded = EvalFixture.from_json(path.read_text(encoding="utf-8"))
    assert reloaded.to_canonical_json() == labeled.to_canonical_json()
    assert reloaded.fixture_hash == labeled.fixture_hash
    assert [label.grader for label in reloaded.labels] == ["henry"]


# ---------------------------------------------------------------------------
# spot_check_sample_size (cortex#333 acceptance criterion 2)
# ---------------------------------------------------------------------------


def test_spot_check_sample_size_is_max_of_floor_and_ten_percent() -> None:
    assert SPOT_CHECK_MINIMUM_ITEMS == 10
    assert SPOT_CHECK_FRACTION_DENOMINATOR == 10
    assert spot_check_sample_size(40) == 10  # floor dominates
    assert spot_check_sample_size(100) == 10  # boundary: 10% == floor
    assert spot_check_sample_size(101) == 11  # 10% dominates, ceiling division
    assert spot_check_sample_size(250) == 25


def test_spot_check_sample_size_never_exceeds_the_batch() -> None:
    assert spot_check_sample_size(7) == 7  # whole small batch double-labeled
    assert spot_check_sample_size(1) == 1


def test_spot_check_sample_size_rejects_empty_batch_visibly() -> None:
    with pytest.raises(LabelingError, match="undefined for an empty labeling batch"):
        spot_check_sample_size(0)
    with pytest.raises(LabelingError, match="must be an integer"):
        spot_check_sample_size(True)  # bools are not item counts


# ---------------------------------------------------------------------------
# disagreement_report (cortex#333 inter-rater spot-check)
# ---------------------------------------------------------------------------


def _double_labeled_fixture() -> EvalFixture:
    fixture = apply_label(
        _fixture(),
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
    )
    fixture = apply_label(
        fixture,
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_NOT_USEFUL,
        grader="sam",
        graded_at="2026-06-09",
    )
    fixture = apply_label(
        fixture,
        finding_id="finding-missing-jitter",
        label_class=LabelClass.MISSED_EXPECTED,
        grader="henry",
        graded_at="2026-06-09",
    )
    return apply_label(
        fixture,
        finding_id="finding-missing-jitter",
        label_class=LabelClass.MISSED_EXPECTED,
        grader="sam",
        graded_at="2026-06-09",
    )


def test_disagreement_report_lists_per_finding_grader_disagreements() -> None:
    report = disagreement_report([_double_labeled_fixture()])
    assert report.double_labeled_finding_count == 2
    assert report.disagreeing_finding_count == 1

    disagreeing = next(entry for entry in report.entries if not entry.agreed)
    assert disagreeing.fixture_id == "contradiction-001"
    assert disagreeing.finding_id == "finding-fixed-retry"
    assert disagreeing.graders == ("henry", "sam")
    assert disagreeing.distinct_label_values == ("correct_not_useful", "correct_useful")

    agreeing = next(entry for entry in report.entries if entry.agreed)
    assert agreeing.finding_id == "finding-missing-jitter"
    assert report.agreement_rate == 0.5
    assert report.agreement_rate_unavailable_reason is None


def test_disagreement_report_excludes_single_grader_findings() -> None:
    fixture = apply_label(
        _fixture(),
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="henry",
        graded_at="2026-06-09",
    )
    report = disagreement_report([fixture])
    assert report.entries == ()
    assert report.agreement_rate is None
    assert report.agreement_rate_unavailable_reason is not None
    assert "never a silent 0.0" in report.agreement_rate_unavailable_reason


def test_disagreement_resolution_is_recorded_via_appended_note() -> None:
    fixture = apply_label(
        _double_labeled_fixture(),
        finding_id="finding-fixed-retry",
        label_class=LabelClass.CORRECT_USEFUL,
        grader="resolution-henry-sam",
        graded_at="2026-06-10",
        note="Discussed 2026-06-10: the finding is actionable; sam concedes.",
    )
    report = disagreement_report([fixture])
    disagreeing = next(entry for entry in report.entries if not entry.agreed)
    assert disagreeing.resolution_recorded
    assert report.unresolved_disagreements == ()

    unresolved = disagreement_report([_double_labeled_fixture()]).unresolved_disagreements
    assert [entry.finding_id for entry in unresolved] == ["finding-fixed-retry"]


def test_disagreement_report_rejects_duplicate_fixture_ids() -> None:
    fixture = _double_labeled_fixture()
    with pytest.raises(LabelingError, match="duplicate fixture_id"):
        disagreement_report([fixture, fixture])


def test_empty_disagreement_report_invariant_none_iff_reason() -> None:
    report = DisagreementReport(entries=())
    assert (report.agreement_rate is None) == (
        report.agreement_rate_unavailable_reason is not None
    )


# ---------------------------------------------------------------------------
# label_tally (cortex#333 aggregation + master-plan metrics)
# ---------------------------------------------------------------------------


def _batch_with_counts() -> list[EvalFixture]:
    """3 correct_useful, 1 correct_not_useful, 1 incorrect_precision, 2 missed_expected."""

    plan = {
        "fixture-a": [
            ("finding-fixed-retry", LabelClass.CORRECT_USEFUL),
            ("finding-missing-jitter", LabelClass.CORRECT_USEFUL),
        ],
        "fixture-b": [
            ("finding-fixed-retry", LabelClass.CORRECT_USEFUL),
            ("finding-missing-jitter", LabelClass.CORRECT_NOT_USEFUL),
        ],
        "fixture-c": [
            ("finding-fixed-retry", LabelClass.INCORRECT_PRECISION),
            ("finding-missing-jitter", LabelClass.MISSED_EXPECTED),
        ],
        "fixture-d": [
            ("finding-fixed-retry", LabelClass.MISSED_EXPECTED),
        ],
    }
    batch = []
    for fixture_id, labels in plan.items():
        fixture = _fixture(fixture_id)
        for finding_id, label_class in labels:
            fixture = apply_label(
                fixture,
                finding_id=finding_id,
                label_class=label_class,
                grader="henry",
                graded_at="2026-06-09",
            )
        batch.append(fixture)
    return batch


def test_label_tally_counts_every_class() -> None:
    tally = label_tally(_batch_with_counts())
    assert dict(tally.counts) == {
        LabelClass.CORRECT_USEFUL: 3,
        LabelClass.CORRECT_NOT_USEFUL: 1,
        LabelClass.INCORRECT_PRECISION: 1,
        LabelClass.MISSED_EXPECTED: 2,
        LabelClass.OVERRIDE_CHANGED_DECISION: 0,
        LabelClass.OVERRIDE_EMERGENCY_EXCEPTION: 0,
    }


def test_label_tally_derived_metrics_match_master_plan_definitions() -> None:
    tally = label_tally(_batch_with_counts())
    # correct = correct_useful + correct_not_useful = 4
    assert tally.correct_count == 4
    # precision_correct = correct / (correct + incorrect_precision) = 4/5
    assert tally.precision_correct == pytest.approx(0.8)
    # useful_rate = correct_useful / all_graded = 3/5
    assert tally.useful_rate == pytest.approx(0.6)
    assert tally.precision_correct_unavailable_reason is None
    assert tally.useful_rate_unavailable_reason is None


def test_missed_expected_is_a_separate_recall_signal() -> None:
    tally = label_tally(_batch_with_counts())
    assert LabelClass.MISSED_EXPECTED not in GRADED_EMITTED_CLASSES
    assert tally.missed_expected_count == 2
    # Denominators exclude the recall signal entirely.
    assert tally.graded_emitted_count == 5


def test_override_context_labels_are_visible_but_do_not_move_quality_gates() -> None:
    fixture = _fixture("fixture-override")
    fixture = apply_label(
        fixture,
        finding_id="finding-fixed-retry",
        label_class=LabelClass.OVERRIDE_CHANGED_DECISION,
        grader="henry",
        graded_at="2026-06-16",
        note="The cited decision was superseded after the PR was opened.",
    )
    fixture = apply_label(
        fixture,
        finding_id="finding-missing-jitter",
        label_class=LabelClass.OVERRIDE_EMERGENCY_EXCEPTION,
        grader="sam",
        graded_at="2026-06-16",
        note="Incident commander approved a one-off exception.",
    )

    tally = label_tally([fixture])

    assert set(OVERRIDE_CONTEXT_CLASSES) == {
        LabelClass.OVERRIDE_CHANGED_DECISION,
        LabelClass.OVERRIDE_EMERGENCY_EXCEPTION,
    }
    assert tally.override_context_count == 2
    assert tally.graded_emitted_count == 0
    assert tally.precision_correct is None
    assert tally.useful_rate is None


def test_label_tally_zero_division_returns_none_with_visible_reason() -> None:
    tally = label_tally([_fixture()])  # no labels at all
    # `is None` (not == 0.0): the contract is None-with-reason, never a silent zero.
    assert tally.precision_correct is None
    assert tally.useful_rate is None
    assert tally.precision_correct_unavailable_reason is not None
    assert "never a silent 0.0" in tally.precision_correct_unavailable_reason
    assert tally.useful_rate_unavailable_reason is not None
    assert "never a silent 0.0" in tally.useful_rate_unavailable_reason

    # missed_expected alone still leaves both metrics undefined: nothing was emitted.
    only_missed = apply_label(
        _fixture(),
        finding_id="finding-fixed-retry",
        label_class=LabelClass.MISSED_EXPECTED,
        grader="henry",
        graded_at="2026-06-09",
    )
    missed_tally = label_tally([only_missed])
    assert missed_tally.missed_expected_count == 1
    assert missed_tally.precision_correct is None
    assert missed_tally.useful_rate is None


def test_label_tally_metric_none_iff_reason_invariant() -> None:
    for tally in (label_tally(_batch_with_counts()), label_tally([_fixture()])):
        assert (tally.precision_correct is None) == (
            tally.precision_correct_unavailable_reason is not None
        )
        assert (tally.useful_rate is None) == (
            tally.useful_rate_unavailable_reason is not None
        )


def test_label_tally_rejects_duplicate_fixture_ids() -> None:
    fixture = _fixture()
    with pytest.raises(LabelingError, match="duplicate fixture_id"):
        label_tally([fixture, fixture])


# ---------------------------------------------------------------------------
# Stage 0 gate report template (cortex#343)
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = (
    "Corpus",
    "Candidate-set quality",
    "Advisory quality",
    "Citation quality",
    "Budget behavior",
    "Ledger quality",
    "Self-review",
    "Decision",
)

DECISION_OUTCOMES = (
    "proceed",
    "grind",
    "narrow-to-structured-memory-repos",
    "contextlint-fallback",
)


def _template_text() -> str:
    assert TEMPLATE_PATH.is_file(), f"missing template: {TEMPLATE_PATH}"
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_template_has_all_eight_required_sections_in_order() -> None:
    text = _template_text()
    h1_headings = re.findall(r"^# (.+)$", text, flags=re.MULTILINE)
    assert len(h1_headings) == 1, "template must parse as one markdown document"
    h2_headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    assert h2_headings == list(REQUIRED_SECTIONS)


def test_template_mentions_every_label_class_value() -> None:
    text = _template_text()
    for label_class in LabelClass:
        assert label_class.value in text, f"template missing label class {label_class.value}"


def test_template_decision_field_enumerates_exactly_the_four_outcomes() -> None:
    decision_section = _template_text().split("## Decision", 1)[1]
    checkboxes = re.findall(r"^- \[ \] \*\*(.+?)\*\*", decision_section, flags=re.MULTILINE)
    assert checkboxes == list(DECISION_OUTCOMES)
    # Each outcome carries its trigger condition next to it.
    assert decision_section.count("trigger:") == len(DECISION_OUTCOMES)


def test_template_names_the_computing_module_for_each_metric_vocabulary() -> None:
    text = _template_text()
    # Headline bar, quoted verbatim from the master plan / Stage 0 exit gate.
    assert (
        ">=70% of emitted advisory comments correct and useful on a\nhand-graded sample" in text
        or ">=70% of emitted advisory comments correct and useful on a hand-graded sample" in text
    )
    for required in (
        "useful_rate",
        "precision_correct",
        "src/cortex/hosted/labeling.py::label_tally",
        "src/cortex/hosted/labeling.py::spot_check_sample_size",
        "src/cortex/hosted/labeling.py::disagreement_report",
        "omitted_counts",
        "candidate_growth_ratio",
        "src/cortex/hosted/decisions_for_diff.py",
        "src/cortex/hosted/ledger_events.py",
        "src/cortex/hosted/ask_ledger.py",
        "src/cortex/hosted/eval_fixtures.py",
        "cortex#451",
        "cortex#439",
    ):
        assert required in text, f"template missing required reference: {required}"
