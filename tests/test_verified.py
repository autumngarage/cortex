from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from cortex.manifest import build_manifest
from cortex.verified import bullet_age_days, parse_verified


def _minimal_project(tmp_path: Path, *, state_body: str) -> Path:
    cortex = tmp_path / ".cortex"
    (cortex / "doctrine").mkdir(parents=True)
    (cortex / "plans").mkdir()
    (cortex / "journal").mkdir()
    (cortex / "state.md").write_text(state_body)
    return tmp_path


def test_parse_verified_well_formed() -> None:
    assert parse_verified("- Foo. Verified: 2026-04-26") == date(2026, 4, 26)


def test_parse_verified_full_timestamp() -> None:
    assert parse_verified("- Bar. Verified: 2026-04-26T12:00:00-04:00") == date(2026, 4, 26)


def test_parse_verified_no_marker() -> None:
    assert parse_verified("- Plain bullet") is None


def test_parse_verified_multi_line_bullet() -> None:
    assert parse_verified("- Top line\n  more text\n  Verified: 2026-04-26") == date(
        2026,
        4,
        26,
    )


def test_bullet_age_days() -> None:
    assert bullet_age_days(date(2026, 4, 1), today=date(2026, 4, 26)) == 25


def test_manifest_fresh_verified_bullet_renders_unchanged(tmp_path: Path) -> None:
    project = _minimal_project(
        tmp_path,
        state_body="# Project State\n\n- Fresh fact. Verified: 2026-04-26\n",
    )

    rendered = build_manifest(project, 8000, now=datetime(2026, 4, 27, tzinfo=UTC)).render()

    assert "- Fresh fact. Verified: 2026-04-26" in rendered
    assert "⚠ verified" not in rendered


def test_manifest_stale_doctrine_bullet_gets_inline_warning(tmp_path: Path) -> None:
    project = _minimal_project(tmp_path, state_body="# Project State\n")
    (project / ".cortex" / "doctrine" / "0001-old.md").write_text(
        "# 0001 — Old\n\n"
        "**Status:** Accepted\n"
        "**Date:** 2026-04-01\n"
        "**Load-priority:** always\n\n"
        "## Context\n"
        "- Old fact. Verified: 2026-01-01\n"
    )

    rendered = build_manifest(project, 8000, now=datetime(2026, 4, 27, tzinfo=UTC)).render()

    assert "- Old fact. ⚠ verified 116d ago Verified: 2026-01-01" in rendered


def test_manifest_verified_threshold_respected_from_config(tmp_path: Path) -> None:
    project = _minimal_project(
        tmp_path,
        state_body="# Project State\n\n- Old but allowed. Verified: 2026-01-01\n",
    )
    (project / ".cortex" / "config.toml").write_text(
        "[manifest]\nverified_threshold_days = 200\n"
    )

    rendered = build_manifest(project, 8000, now=datetime(2026, 4, 27, tzinfo=UTC)).render()

    assert "- Old but allowed. Verified: 2026-01-01" in rendered
    assert "⚠ verified" not in rendered
