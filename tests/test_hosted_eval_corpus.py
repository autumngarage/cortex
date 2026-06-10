"""Corpus-wide validation for the committed Stage 0 eval corpus (cortex#339).

The corpus at ``tests/fixtures/hosted_eval/corpus/`` is the unlabeled skeleton:
real diffs frozen from cortex history plus at most one clearly-marked synthetic
fixture. Labels stay empty until the cortex#333 hand-grading pass; these tests
pin the structural invariants the cortex#450 replay run will rely on.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cortex.hosted.corpus_builder import (
    CORPUS_SOURCES,
    REAL_HISTORY_SOURCE,
    SYNTHETIC_SOURCE,
    load_corpus,
)
from cortex.hosted.eval_fixtures import EvalFixture

CORPUS_DIR = Path(__file__).parent / "fixtures" / "hosted_eval" / "corpus"
MINIMUM_CORPUS_SIZE = 6
MAXIMUM_SYNTHETIC_FIXTURES = 1
REPO = "autumngarage/cortex"
REPO_BLOB_PREFIX = f"https://github.com/{REPO}/blob/"


def _corpus_paths() -> list[Path]:
    return sorted(CORPUS_DIR.glob("*.json"))


def _corpus() -> tuple[EvalFixture, ...]:
    return load_corpus(CORPUS_DIR)


def test_every_fixture_round_trips_byte_identically():
    paths = _corpus_paths()
    assert len(paths) >= MINIMUM_CORPUS_SIZE
    for path in paths:
        text = path.read_text(encoding="utf-8")
        fixture = EvalFixture.from_json(text)
        assert fixture.to_canonical_json() == text, path.name


def test_corpus_loads_through_the_fail_closed_loader():
    fixtures = _corpus()
    assert len(fixtures) == len(_corpus_paths())


def test_corpus_size_and_unique_fixture_ids():
    fixtures = _corpus()
    assert len(fixtures) >= MINIMUM_CORPUS_SIZE
    fixture_ids = [fixture.fixture_id for fixture in fixtures]
    assert len(set(fixture_ids)) == len(fixture_ids)


def test_every_fixture_is_ungraded_pending_human_labels():
    for fixture in _corpus():
        assert fixture.labels == (), (
            f"{fixture.fixture_id} carries labels; corpus grading happens through "
            "the cortex#333 hand-labeling workflow, not at assembly time"
        )


def test_every_fixture_declares_a_known_source_class():
    fixtures = _corpus()
    sources = [fixture.metadata.get("source") for fixture in fixtures]
    assert all(source in CORPUS_SOURCES for source in sources)
    synthetic_count = sources.count(SYNTHETIC_SOURCE)
    assert 0 < synthetic_count <= MAXIMUM_SYNTHETIC_FIXTURES
    real_count = sources.count(REAL_HISTORY_SOURCE)
    assert real_count >= MINIMUM_CORPUS_SIZE - MAXIMUM_SYNTHETIC_FIXTURES
    # Composition documented per repo: every item in this corpus skeleton is
    # drawn from autumngarage/cortex (sibling-repo items are future expansion).
    for fixture in fixtures:
        assert fixture.diff.repo_owner == "autumngarage"
        assert fixture.diff.repo_name == "cortex"
        if fixture.metadata["source"] == REAL_HISTORY_SOURCE:
            assert fixture.metadata["repo"] == REPO


def test_every_fixture_has_real_provenance_spans():
    for fixture in _corpus():
        assert fixture.decisions, fixture.fixture_id
        for decision in fixture.decisions:
            assert decision.spans, f"{fixture.fixture_id}/{decision.decision_id}"
            for span in decision.spans:
                # Citations point at pinned real repo documents.
                assert span.permalink.startswith(REPO_BLOB_PREFIX), span.permalink
                # Offsets and excerpt agree (SourceDocument-style offset math).
                assert len(span.excerpt) == span.end_offset - span.start_offset
                # The recorded span hash is recomputable from span material
                # (same scheme as hosted provenance span hashing).
                excerpt_hash = hashlib.sha256(span.excerpt.encode("utf-8")).hexdigest()
                recomputed = hashlib.sha256(
                    json.dumps(
                        {
                            "end_offset": span.end_offset,
                            "excerpt_hash": excerpt_hash,
                            "source_document_hash": span.source_document_hash,
                            "start_offset": span.start_offset,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                assert span.span_hash == recomputed


def test_expected_findings_cite_span_hashes_present_in_decisions():
    fixtures = _corpus()
    assert any(fixture.expected_findings for fixture in fixtures)
    assert any(not fixture.expected_findings for fixture in fixtures), (
        "the corpus must include at least one negative case (no expected findings) "
        "so false-positive behavior is measurable"
    )
    for fixture in fixtures:
        known_spans = {
            span_hash
            for decision in fixture.decisions
            for span_hash in decision.span_hashes
        }
        known_decisions = {decision.decision_id for decision in fixture.decisions}
        for finding in fixture.expected_findings:
            assert finding.decision_id in known_decisions
            assert set(finding.cited_span_hashes) <= known_spans


def test_real_history_fixtures_freeze_real_shas():
    for fixture in _corpus():
        if fixture.metadata["source"] != REAL_HISTORY_SOURCE:
            continue
        assert len(fixture.diff.base_sha) == 40
        assert len(fixture.diff.head_sha) == 40
        assert fixture.diff.patch.startswith("diff --git ")
        assert fixture.diff.changed_paths


@pytest.mark.parametrize(
    ("fixture_id", "expects_findings"),
    [
        ("standalone-boundary-respected-001", False),
        ("spec-version-drift-001", True),
        ("consolidated-journal-entries-001", True),
        ("journal-entry-deletion-001", True),
        ("touchstone-managed-principles-001", False),
        ("standalone-boundary-violation-synthetic-001", True),
    ],
)
def test_named_scenarios_are_present(fixture_id: str, expects_findings: bool) -> None:
    by_id = {fixture.fixture_id: fixture for fixture in _corpus()}
    assert fixture_id in by_id
    fixture = by_id[fixture_id]
    assert bool(fixture.expected_findings) is expects_findings
