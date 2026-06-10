"""Tests for the no-silent-failure degradation taxonomy (cortex#329)."""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import pkgutil
from pathlib import Path
from typing import cast

import pytest

import cortex.hosted
from cortex.hosted.ask_ledger import (
    AnswerState,
    AskLedgerValidationError,
    build_cited_context_pack,
)
from cortex.hosted.decisions_for_diff import DecisionsForDiffValidationError
from cortex.hosted.degradation import (
    OPTIONAL_FAILURE_SOURCES,
    DegradationMode,
    DegradationReport,
    DegradationTaxonomyError,
    classified_failure_types,
    classify_failure,
    unregistered_optional_failure_sources,
)
from cortex.hosted.diff_surface import DiffSurfaceValidationError
from cortex.hosted.embeddings import HostedEmbeddingValidationError
from cortex.hosted.eval_fixtures import FixtureValidationError
from cortex.hosted.ledger_events import LedgerEventValidationError
from cortex.hosted.model_registry import RegistryValidationError
from cortex.hosted.provenance import ProvenanceValidationError
from cortex.hosted.scopes import ScopeValidationError
from cortex.hosted.storage import StoreBoundaryError
from cortex.hosted.visibility import VisibilityBoundaryValidationError

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "degradation-modes.md"
SHA256_PROBE = "a" * 64

EXPECTED_CLASSIFICATIONS: tuple[tuple[type[Exception], DegradationMode], ...] = (
    (AskLedgerValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (DecisionsForDiffValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (DiffSurfaceValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (FixtureValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (HostedEmbeddingValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (LedgerEventValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (ProvenanceValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (RegistryValidationError, DegradationMode.DRIFT_DETECTED),
    (ScopeValidationError, DegradationMode.INVALID_INPUT_REJECTED),
    (StoreBoundaryError, DegradationMode.FAIL_CLOSED_REFUSAL),
    (VisibilityBoundaryValidationError, DegradationMode.FAIL_CLOSED_REFUSAL),
)


@pytest.mark.parametrize(
    ("failure_type", "expected_mode"),
    EXPECTED_CLASSIFICATIONS,
    ids=[failure_type.__qualname__ for failure_type, _ in EXPECTED_CLASSIFICATIONS],
)
def test_every_substrate_failure_type_classifies(
    failure_type: type[Exception], expected_mode: DegradationMode
) -> None:
    assert classify_failure(failure_type("boundary probe")) is expected_mode


def test_no_answer_state_classifies_as_fail_closed_refusal() -> None:
    assert classify_failure(AnswerState.NO_ANSWER) is DegradationMode.FAIL_CLOSED_REFUSAL


def test_ready_state_refuses_classification() -> None:
    with pytest.raises(DegradationTaxonomyError, match="not a failure"):
        classify_failure(AnswerState.READY)


@pytest.mark.parametrize(
    "unknown",
    [ValueError("plain"), RuntimeError("plain"), KeyError("plain"), ImportError("plain")],
    ids=["ValueError", "RuntimeError", "KeyError", "ImportError"],
)
def test_unknown_failure_types_raise(unknown: Exception) -> None:
    with pytest.raises(DegradationTaxonomyError, match="unclassified failure type"):
        classify_failure(unknown)


def test_subclasses_never_inherit_classification() -> None:
    class RefinedLedgerError(LedgerEventValidationError):
        """A refinement whose behavior has not been reviewed for the taxonomy."""

    with pytest.raises(DegradationTaxonomyError, match="unclassified failure type"):
        classify_failure(RefinedLedgerError("boundary probe"))


def test_model_interface_validation_error_classifies_when_module_ships() -> None:
    if importlib.util.find_spec("cortex.hosted.model_interfaces") is None:
        # The skip is the declared degraded_capability path: the pending
        # registration must be visible, and nothing raisable is unclassified.
        assert ("cortex.hosted.model_interfaces", "ModelInterfaceValidationError") in (
            unregistered_optional_failure_sources()
        )
        pytest.skip("cortex.hosted.model_interfaces not yet merged (cortex#344)")
    module = importlib.import_module("cortex.hosted.model_interfaces")
    failure_type = module.ModelInterfaceValidationError
    assert classify_failure(failure_type("boundary probe")) is (
        DegradationMode.INVALID_INPUT_REJECTED
    )
    assert unregistered_optional_failure_sources() == ()


def test_optional_sources_bookkeeping_is_consistent() -> None:
    registered_names = {
        failure_type.__qualname__ for failure_type in classified_failure_types()
    }
    for module_name, class_name, _ in OPTIONAL_FAILURE_SOURCES:
        if importlib.util.find_spec(module_name) is None:
            assert (module_name, class_name) in unregistered_optional_failure_sources()
            assert class_name not in registered_names
        else:
            assert (module_name, class_name) not in unregistered_optional_failure_sources()
            assert class_name in registered_names


def test_every_hosted_error_type_is_classified() -> None:
    """Guardrail: a new substrate error type must be added to the taxonomy.

    Scans every module in cortex.hosted for ValueError subclasses defined
    there and asserts each classifies. DegradationTaxonomyError is excluded
    deliberately: it is the taxonomy's own failure, and classifying it as a
    handled degradation would let classification bugs masquerade as handled.
    """

    found: set[type[Exception]] = set()
    for module_info in pkgutil.iter_modules(cortex.hosted.__path__):
        module = importlib.import_module(f"cortex.hosted.{module_info.name}")
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, ValueError)
                and value.__module__ == module.__name__
            ):
                found.add(value)
    found.discard(DegradationTaxonomyError)
    assert found, "expected the cortex.hosted substrate to define error types"
    for error_type in sorted(found, key=lambda exc_type: exc_type.__qualname__):
        assert isinstance(classify_failure(error_type("boundary probe")), DegradationMode)


def test_every_mode_is_documented() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    for mode in DegradationMode:
        assert mode.value in text, f"docs/degradation-modes.md missing mode {mode.value!r}"
    # The taxonomy's named consumers must stay cited in the doc.
    assert "cortex#377" in text
    assert "Stage 2" in text


def test_fail_closed_read_path_classifies_under_taxonomy() -> None:
    """The shipped no-cited-support refusal is a fail_closed_refusal."""

    pack = build_cited_context_pack(
        query_hash=SHA256_PROBE,
        retrieval_config_version="ask-ledger-retrieval/v1",
        graph_snapshot_hash=SHA256_PROBE,
        candidates=(),
        limit=5,
    )
    assert pack.answer_state is AnswerState.NO_ANSWER
    reason = pack.no_answer_reason
    assert reason is not None
    assert reason == "no_cited_support"
    assert classify_failure(pack.answer_state) is DegradationMode.FAIL_CLOSED_REFUSAL
    report = DegradationReport(
        mode=classify_failure(pack.answer_state),
        reason_code=reason,
        source="cortex.hosted.ask_ledger.build_cited_context_pack",
        safety_boundary_held=True,
    )
    assert report.as_payload() == {
        "mode": "fail_closed_refusal",
        "reason_code": "no_cited_support",
        "safety_boundary_held": True,
        "source": "cortex.hosted.ask_ledger.build_cited_context_pack",
    }


def test_report_strips_and_keeps_fields() -> None:
    report = DegradationReport(
        mode=DegradationMode.BOUNDED_OMISSION,
        reason_code="  over_limit  ",
        source="  cortex.hosted.decisions_for_diff  ",
        safety_boundary_held=True,
    )
    assert report.reason_code == "over_limit"
    assert report.source == "cortex.hosted.decisions_for_diff"
    assert report.mode is DegradationMode.BOUNDED_OMISSION


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_report_requires_reason_code(blank: str) -> None:
    with pytest.raises(DegradationTaxonomyError, match="reason_code"):
        DegradationReport(
            mode=DegradationMode.DRIFT_DETECTED,
            reason_code=blank,
            source="cortex.hosted.model_registry",
            safety_boundary_held=True,
        )


@pytest.mark.parametrize("blank", ["", "   "], ids=["empty", "whitespace"])
def test_report_requires_source(blank: str) -> None:
    with pytest.raises(DegradationTaxonomyError, match="source"):
        DegradationReport(
            mode=DegradationMode.DRIFT_DETECTED,
            reason_code="prompt_hash_mismatch",
            source=blank,
            safety_boundary_held=True,
        )


def test_report_refuses_a_broken_safety_boundary() -> None:
    with pytest.raises(DegradationTaxonomyError, match="raise the failure instead"):
        DegradationReport(
            mode=DegradationMode.DEGRADED_CAPABILITY,
            reason_code="vector_recall_below_floor",
            source="cortex.hosted.embeddings",
            safety_boundary_held=False,
        )


def test_report_rejects_unknown_modes() -> None:
    with pytest.raises(DegradationTaxonomyError, match="unknown degradation mode"):
        DegradationReport(
            mode=cast(DegradationMode, "partial_silent_fallback"),
            reason_code="nope",
            source="nowhere",
            safety_boundary_held=True,
        )


def test_report_coerces_raw_mode_strings() -> None:
    report = DegradationReport(
        mode=cast(DegradationMode, "drift_detected"),
        reason_code="prompt_hash_mismatch",
        source="cortex.hosted.model_registry",
        safety_boundary_held=True,
    )
    assert report.mode is DegradationMode.DRIFT_DETECTED


def test_report_is_immutable() -> None:
    report = DegradationReport(
        mode=DegradationMode.FAIL_CLOSED_REFUSAL,
        reason_code="no_cited_support",
        source="cortex.hosted.ask_ledger",
        safety_boundary_held=True,
    )
    field_name = "reason_code"
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(report, field_name, "rewritten")


def test_classified_failure_types_cover_the_expected_table() -> None:
    classified = set(classified_failure_types())
    for failure_type, _ in EXPECTED_CLASSIFICATIONS:
        assert failure_type in classified
