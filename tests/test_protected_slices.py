"""The versioned, append-only eval gate (cortex#338).

Protected slices are quality measurements that may only move one way.
This suite recomputes them over the committed corpus
(``tests/fixtures/hosted_eval/corpus/``) using the model-free retrieval
emulation, and fails when any slice regresses below its committed
baseline (``tests/fixtures/hosted_eval/baselines.json``).

Raising a baseline is a deliberate act: edit ``baselines.json`` in the
same PR, appending a line to its ``history`` list saying what improved
and why. Lowering a baseline is the one-directional promotion rule being
broken — the diff itself is the audit trail, and review should treat it
as a regression being ratified, never a routine update.

The gate is deliberately model-free (retrieval-presence slices only), so
CI needs no recordings and no live calls; finding-precision slices join
once graded corpus labels exist (cortex#378).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.hosted.candidate_metrics import compute_candidate_set_metrics
from cortex.hosted.eval_fixtures import EvalFixture
from cortex.hosted.replay_runner import build_fixture_candidate_pack

CORPUS_DIR = Path(__file__).parent / "fixtures" / "hosted_eval" / "corpus"
BASELINES_PATH = Path(__file__).parent / "fixtures" / "hosted_eval" / "baselines.json"

BASELINE_SCHEMA_VERSION = 1


def _load_corpus() -> list[EvalFixture]:
    fixtures = [
        EvalFixture.from_json(path.read_text(encoding="utf-8"))
        for path in sorted(CORPUS_DIR.glob("*.json"))
    ]
    assert fixtures, "protected slices need a corpus; the corpus directory is empty"
    return fixtures


def _load_baselines() -> dict[str, object]:
    payload = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload.get("baseline_schema_version") == BASELINE_SCHEMA_VERSION, (
        "unknown baselines schema version; the gate refuses to guess"
    )
    return payload


def _baseline_presence(payload: dict[str, object]) -> dict[str, bool]:
    raw = payload["presence_by_fixture"]
    assert isinstance(raw, dict)
    return {str(key): bool(value) for key, value in raw.items()}


def _presence_by_fixture() -> dict[str, bool]:
    """For each fixture: are ALL expected findings' decisions in the pack?"""

    presence: dict[str, bool] = {}
    for fixture in _load_corpus():
        emulation = build_fixture_candidate_pack(fixture)
        relevant = {
            emulation.decision_node_id_by_decision_id[finding.decision_id]
            for finding in fixture.expected_findings
        }
        if not relevant:
            # Negative fixtures (no expected findings) have no presence
            # requirement; they are protected by the no-spam slices later.
            presence[fixture.fixture_id] = True
            continue
        metrics = compute_candidate_set_metrics(
            pack=emulation.pack, relevant_decision_ids=relevant
        )
        presence[fixture.fixture_id] = metrics.omitted_relevant_count == 0
    return presence


def test_baselines_file_is_well_formed() -> None:
    payload = _load_baselines()
    assert isinstance(payload.get("presence_by_fixture"), dict)
    assert isinstance(payload.get("aggregate_presence_rate"), (int, float))
    history = payload.get("history")
    assert isinstance(history, list) and history, (
        "baselines.json must carry an append-only history list"
    )


def test_every_corpus_fixture_has_a_baseline_entry() -> None:
    payload = _load_baselines()
    baseline_ids = set(_baseline_presence(payload))
    corpus_ids = {fixture.fixture_id for fixture in _load_corpus()}
    missing = corpus_ids - baseline_ids
    assert not missing, (
        f"corpus fixtures without baselines: {sorted(missing)} — add them to "
        "baselines.json (with a history line) in this PR"
    )


def test_per_fixture_presence_never_regresses() -> None:
    payload = _load_baselines()
    baselines = _baseline_presence(payload)
    current = _presence_by_fixture()
    regressions = sorted(
        fixture_id
        for fixture_id, was_present in baselines.items()
        if was_present and not current.get(fixture_id, False)
    )
    assert not regressions, (
        f"protected-slice regression: expected decisions fell out of the "
        f"candidate pack for {regressions}; retrieval/ranking changes must "
        "not lose previously-present decisions (cortex#338)"
    )


def test_aggregate_presence_rate_never_regresses() -> None:
    payload = _load_baselines()
    raw_rate = payload["aggregate_presence_rate"]
    assert isinstance(raw_rate, (int, float))
    baseline_rate = float(raw_rate)
    current = _presence_by_fixture()
    rate = sum(current.values()) / len(current)
    assert rate >= baseline_rate, (
        f"aggregate presence rate regressed: {rate:.3f} < baseline "
        f"{baseline_rate:.3f} (cortex#338 one-directional promotion)"
    )


def test_improvements_are_promotable() -> None:
    """When current beats baseline, the gate PASSES but nudges promotion.

    One-directional: improvements should be ratified by raising the
    baseline (with history) so the new level becomes the floor.
    """

    payload = _load_baselines()
    baselines = _baseline_presence(payload)
    current = _presence_by_fixture()
    newly_present = sorted(
        fixture_id
        for fixture_id, was_present in baselines.items()
        if not was_present and current.get(fixture_id, False)
    )
    if newly_present:
        pytest.skip(
            f"improvement available to promote: {newly_present} now present — "
            "raise baselines.json in a follow-up PR"
        )
