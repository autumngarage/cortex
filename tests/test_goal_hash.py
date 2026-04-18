"""Unit tests for goal-hash normalization (SPEC § 4.9)."""

from __future__ import annotations

from cortex.goal_hash import normalize_goal_hash


def test_spec_example_matches() -> None:
    # SPEC.md § 4.9 worked example.
    assert normalize_goal_hash("Sharpen Cortex's Vision") == "1cc12b25"


def test_case_insensitive() -> None:
    assert normalize_goal_hash("SHARPEN cortex's vision") == normalize_goal_hash(
        "Sharpen Cortex's Vision"
    )


def test_whitespace_collapsed() -> None:
    # Per SPEC § 4.9 step 3, only ASCII space survives the strip; tabs are
    # dropped, not collapsed. This test exercises multi-space collapse only.
    assert normalize_goal_hash("  Sharpen    Cortex's  Vision  ") == "1cc12b25"


def test_diacritics_stripped() -> None:
    # "naïve" → "naive" via NFKD; result must match the plain form.
    assert normalize_goal_hash("A Naïve Plan") == normalize_goal_hash("A Naive Plan")


def test_punctuation_dropped_without_inserting_space() -> None:
    # "Cortex's" → "cortexs" — apostrophe drops, no space inserted.
    assert normalize_goal_hash("Cortex's") == normalize_goal_hash("Cortexs")
