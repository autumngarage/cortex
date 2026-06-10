"""simlab generator acceptance (cortex#520).

The #520 bars, as tests: the three named archetypes ship as canonical-JSON
specs that round-trip byte-identically; one spec file materializes one repo
with deterministic git history; the same spec twice yields the identical
derive ``event_hash`` set; and `cortex derive` runs against every
materialized archetype in CI with snapshot-pinned candidate counts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.simlab.generator import (
    SimlabDeriveOutcome,
    derive_materialized,
    materialize_archetype,
)
from tests.simlab.specs import (
    ARCHETYPES_DIR,
    SCENARIOS_DIR,
    SHIPPED_ARCHETYPE_IDS,
    ArchetypeSpec,
    ScenarioSpec,
    SimlabSpecError,
    SpecCommit,
    load_archetype_specs,
    load_scenario_specs,
)

# Snapshot-pinned derive output per archetype (#520 acceptance). These move
# only when the specs or the production extractors deliberately change.
PINNED_DERIVE_COUNTS: dict[str, dict[str, int]] = {
    "chatty-startup": {"candidates": 7, "dropped": 17},
    "clean-shop": {"candidates": 8, "dropped": 10},
    "legacy-migration": {"candidates": 14, "dropped": 9},
}

PINNED_SOURCE_TYPE_COUNTS: dict[str, dict[str, int]] = {
    "chatty-startup": {
        "agent_instructions": 1,
        "commit_message": 4,
        "pr_description": 1,
        "pr_review_comment": 1,
    },
    "clean-shop": {"adr": 3, "agent_instructions": 3, "codeowners": 2},
    "legacy-migration": {
        "adr": 4,
        "agent_instructions": 2,
        "codeowners": 3,
        "commit_message": 5,
    },
}


@pytest.fixture(scope="module")
def archetypes() -> dict[str, ArchetypeSpec]:
    return load_archetype_specs()


@pytest.fixture(scope="module")
def derived_once(
    archetypes: dict[str, ArchetypeSpec], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, SimlabDeriveOutcome]:
    """Materialize + derive each archetype once for the whole module."""

    base = tmp_path_factory.mktemp("simlab-derive-once")
    outcomes: dict[str, SimlabDeriveOutcome] = {}
    for archetype_id, spec in archetypes.items():
        repo = materialize_archetype(spec, base / archetype_id)
        outcomes[archetype_id] = derive_materialized(repo)
    return outcomes


# ---------------------------------------------------------------------------
# Spec format: canonical JSON, versioned, fail-closed
# ---------------------------------------------------------------------------


def test_three_named_archetypes_ship(archetypes: dict[str, ArchetypeSpec]) -> None:
    assert set(SHIPPED_ARCHETYPE_IDS) <= set(archetypes)


@pytest.mark.parametrize("path", sorted(ARCHETYPES_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_archetype_specs_round_trip_byte_identically(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    spec = ArchetypeSpec.from_json(text)
    assert spec.to_canonical_json() == text


@pytest.mark.parametrize("path", sorted(SCENARIOS_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_scenario_specs_round_trip_byte_identically(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    spec = ScenarioSpec.from_json(text)
    assert spec.to_canonical_json() == text


def test_loader_refuses_unknown_schema_version() -> None:
    payload = json.loads(
        (ARCHETYPES_DIR / "clean-shop.json").read_text(encoding="utf-8")
    )
    payload["simlab_spec_schema_version"] = 99
    with pytest.raises(SimlabSpecError, match="unknown simlab_spec_schema_version"):
        ArchetypeSpec.from_payload(payload)


def test_loader_refuses_wrong_kind() -> None:
    payload = json.loads(
        (ARCHETYPES_DIR / "clean-shop.json").read_text(encoding="utf-8")
    )
    payload["kind"] = "scenario"
    with pytest.raises(SimlabSpecError, match="kind must be 'archetype'"):
        ArchetypeSpec.from_payload(payload)


def test_commit_paths_cannot_escape_the_repo() -> None:
    with pytest.raises(SimlabSpecError, match="'\\.\\.' segments"):
        SpecCommit(
            message="bad",
            author_name="X",
            author_email="x@simlab.test",
            authored_at="2026-01-01T00:00:00+00:00",
            files={"../escape.md": "nope\n"},
        )


def test_commit_timestamps_must_be_timezone_aware() -> None:
    """now()-shaped specs are unrepresentable: every timestamp is a literal."""

    with pytest.raises(SimlabSpecError, match="timezone-aware"):
        SpecCommit(
            message="bad",
            author_name="X",
            author_email="x@simlab.test",
            authored_at="2026-01-01T00:00:00",
            files={"a.md": "content\n"},
        )


def test_scenario_specs_cover_every_shipped_archetype() -> None:
    scenarios = load_scenario_specs()
    by_archetype = {spec.archetype_id for spec in scenarios}
    assert set(SHIPPED_ARCHETYPE_IDS) <= by_archetype


# ---------------------------------------------------------------------------
# Determinism: same spec twice → same repo, same derive event_hash set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("archetype_id", SHIPPED_ARCHETYPE_IDS)
def test_double_materialization_is_byte_deterministic(
    archetype_id: str,
    archetypes: dict[str, ArchetypeSpec],
    derived_once: dict[str, SimlabDeriveOutcome],
    tmp_path: Path,
) -> None:
    """The #520 acceptance bar: identical event_hash sets across runs.

    The second materialization happens in a different directory at a later
    wall-clock time; equality therefore proves commit shas, file mtimes, and
    every event-identity input flow from the spec, never from the clock or
    the path.
    """

    spec = archetypes[archetype_id]
    first = derived_once[archetype_id]
    second_repo = materialize_archetype(spec, tmp_path / "again")
    second = derive_materialized(second_repo)

    assert second.event_hashes == first.event_hashes
    assert len(second.event_hashes) == second.candidate_count


@pytest.mark.parametrize("archetype_id", SHIPPED_ARCHETYPE_IDS)
def test_double_materialization_same_head_sha(
    archetype_id: str, archetypes: dict[str, ArchetypeSpec], tmp_path: Path
) -> None:
    spec = archetypes[archetype_id]
    first = materialize_archetype(spec, tmp_path / "one")
    second = materialize_archetype(spec, tmp_path / "two")
    assert first.head_sha == second.head_sha


def test_materialize_refuses_non_empty_target(
    archetypes: dict[str, ArchetypeSpec], tmp_path: Path
) -> None:
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "pre-existing.txt").write_text("here first\n", encoding="utf-8")
    with pytest.raises(SimlabSpecError, match="not empty"):
        materialize_archetype(archetypes["clean-shop"], target)


# ---------------------------------------------------------------------------
# Snapshot-pinned derive counts per archetype (#520 acceptance)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("archetype_id", SHIPPED_ARCHETYPE_IDS)
def test_derive_candidate_counts_are_pinned(
    archetype_id: str, derived_once: dict[str, SimlabDeriveOutcome]
) -> None:
    outcome = derived_once[archetype_id]
    pinned = PINNED_DERIVE_COUNTS[archetype_id]
    assert outcome.candidate_count == pinned["candidates"]
    assert outcome.dropped_count == pinned["dropped"]

    by_source_type: dict[str, int] = {}
    for event in outcome.result.events:
        source_type = str(event.payload.get("source_type"))
        by_source_type[source_type] = by_source_type.get(source_type, 0) + 1
    assert by_source_type == PINNED_SOURCE_TYPE_COUNTS[archetype_id]


def test_chatty_archetype_drops_more_chatter_than_it_keeps(
    derived_once: dict[str, SimlabDeriveOutcome],
) -> None:
    """The chatty archetype exists to exercise the visibility of drops."""

    outcome = derived_once["chatty-startup"]
    assert outcome.dropped_count > outcome.candidate_count
    reason_codes = {record.chatter.reason_code for record in outcome.dropped}
    assert "commit_message:subject_without_decision_pattern" in reason_codes


def test_every_candidate_event_validates_against_the_ledger_envelope(
    derived_once: dict[str, SimlabDeriveOutcome],
) -> None:
    """Invariant: simlab feeds the pipeline only envelope-valid events."""

    for outcome in derived_once.values():
        for event in outcome.result.events:
            assert event.event_type.value == "candidate.proposed"
            assert event.source_span_hashes, "candidates must cite spans"
