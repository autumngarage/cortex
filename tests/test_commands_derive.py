"""Tests for the `cortex derive` scaffold (issue #350).

Covers: CLI wiring and defaults, deterministic tenant/source UUID derivation,
source resolution (explicit vs built-in defaults), fail-closed envelope
validation that names the offending source file, the pluggable extractor
boundary, and run-twice / delete-and-rerun determinism of event hashes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.derive import (
    DEFAULT_SOURCE_RELATIVE_PATHS,
    DeriveSourceError,
    default_source_id,
    default_tenant_id,
    empty_extractor,
    resolve_source_files,
    run_derive,
)
from cortex.hosted.derive_store import DeriveEventStore, derive_store_path
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)
from cortex.hosted.provenance import SourceDocument

OTHER_TENANT_ID = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cortex").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# Project instructions\n\nUse Postgres for the ledger.\n")
    (root / "AGENTS.md").write_text("# Agents\n\nFollow the cortex protocol.\n")
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgres.md").write_text("# ADR 0001\n\nWe chose Postgres.\n")
    return root


def _candidate_per_document(document: SourceDocument) -> tuple[LedgerEvent, ...]:
    """Deterministic test extractor: one candidate per document, content-keyed."""

    payload = {"document_hash": document.document_hash, "external_id": document.external_id}
    return (
        LedgerEvent(
            tenant_id=document.tenant_id,
            source_id=document.source_id,
            event_type=LedgerEventType.CANDIDATE_PROPOSED,
            actor=ActorRef(actor_type="derive", actor_id="test-extractor"),
            occurred_at=datetime(2026, 6, 9, tzinfo=UTC),
            idempotency_key=derive_idempotency_key(
                source_id=document.source_id,
                event_type=LedgerEventType.CANDIDATE_PROPOSED,
                source_event_external_id=document.external_id,
                payload=payload,
            ),
            payload=payload,
        ),
    )


def test_default_tenant_and_source_ids_are_deterministic_uuids(tmp_path: Path) -> None:
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    tenant_a = default_tenant_id(repo_a)
    assert tenant_a == default_tenant_id(repo_a)
    assert UUID(tenant_a).version == 5
    assert tenant_a != default_tenant_id(repo_b)
    # Tenant and source identities are distinct even for the same repo path.
    assert tenant_a != default_source_id(repo_a)


def test_resolve_defaults_picks_up_claude_agents_and_adr_dir(fixture_repo: Path) -> None:
    files = resolve_source_files(fixture_repo, ())
    relative = [str(path.relative_to(fixture_repo.resolve())) for path in files]
    assert relative == ["CLAUDE.md", "AGENTS.md", "docs/adr/0001-use-postgres.md"]


def test_resolve_defaults_skips_missing_entries(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    (root / ".cortex").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# Only file\n")
    files = resolve_source_files(root, ())
    assert [path.name for path in files] == ["CLAUDE.md"]
    assert DEFAULT_SOURCE_RELATIVE_PATHS == (
        "CLAUDE.md",
        "AGENTS.md",
        "docs/adr",
        "docs/decisions",
    )


def test_resolve_explicit_missing_source_is_an_error(fixture_repo: Path) -> None:
    with pytest.raises(DeriveSourceError, match=r"does-not-exist\.md"):
        resolve_source_files(fixture_repo, (Path("does-not-exist.md"),))


def test_cli_runs_repo_native_extractors_and_reports_drops(
    fixture_repo: Path,
) -> None:
    """The fixture has no constraint-shaped material: zero candidates, all
    blocks visibly dropped with reason codes (never silently skipped)."""

    runner = CliRunner()
    result = runner.invoke(cli, ["derive", "--path", str(fixture_repo)])
    assert result.exit_code == 0, result.output
    assert "3 source file(s)" in result.output
    assert "0 candidate event(s)" in result.output
    assert "dropped:" in result.output
    assert "adr:missing_status" in result.output
    db_path = derive_store_path(fixture_repo)
    assert db_path.exists()
    with DeriveEventStore(db_path) as store:
        assert store.event_hashes() == frozenset()


def _combined_output(result: object) -> str:
    output = getattr(result, "output", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return output + stderr


def test_cli_requires_cortex_directory(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["derive", "--path", str(tmp_path)])
    assert result.exit_code == 2
    assert "run `cortex init` first" in _combined_output(result)


def test_cli_rejects_non_uuid_tenant_id(fixture_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["derive", "--path", str(fixture_repo), "--tenant-id", "not-a-uuid"]
    )
    assert result.exit_code != 0
    assert "not a UUID" in _combined_output(result)


def test_cli_reports_missing_explicit_source(fixture_repo: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli, ["derive", "--path", str(fixture_repo), "--source", "missing.md"]
    )
    assert result.exit_code == 1
    combined = _combined_output(result)
    assert "missing.md" in combined
    assert "does not exist" in combined


def test_cli_names_offending_file_on_envelope_validation_failure(
    fixture_repo: Path,
) -> None:
    (fixture_repo / "EMPTY.md").write_text("")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["derive", "--path", str(fixture_repo), "--source", "EMPTY.md"]
    )
    assert result.exit_code == 1
    combined = _combined_output(result)
    assert "EMPTY.md" in combined
    assert "envelope validation" in combined
    # Fail-closed: nothing was persisted for the failed run.
    assert not derive_store_path(fixture_repo).exists()


def test_cli_reports_no_default_sources_visibly(tmp_path: Path) -> None:
    root = tmp_path / "empty-repo"
    (root / ".cortex").mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(cli, ["derive", "--path", str(root)])
    assert result.exit_code == 0, result.output
    assert "no default sources found" in result.output
    assert "0 source file(s)" in result.output


def test_run_derive_validates_all_sources_before_persisting(fixture_repo: Path) -> None:
    (fixture_repo / "EMPTY-A.md").write_text("")
    (fixture_repo / "EMPTY-B.md").write_text("")
    sources = resolve_source_files(
        fixture_repo, (Path("EMPTY-A.md"), Path("EMPTY-B.md"), Path("CLAUDE.md"))
    )
    with pytest.raises(DeriveSourceError) as excinfo:
        run_derive(
            project_root=fixture_repo,
            source_files=sources,
            tenant_id=default_tenant_id(fixture_repo),
            source_id=default_source_id(fixture_repo),
            extractor=empty_extractor,
        )
    message = str(excinfo.value)
    # Every failing source is named; none silently dropped.
    assert "EMPTY-A.md" in message
    assert "EMPTY-B.md" in message
    assert not derive_store_path(fixture_repo).exists()


def test_run_derive_rejects_extractor_events_with_wrong_type(fixture_repo: Path) -> None:
    def wrong_type_extractor(document: SourceDocument) -> tuple[LedgerEvent, ...]:
        return (
            LedgerEvent(
                tenant_id=document.tenant_id,
                source_id=document.source_id,
                event_type=LedgerEventType.FEEDBACK_RECORDED,
                actor=ActorRef(actor_type="derive", actor_id="rogue"),
                occurred_at=datetime(2026, 6, 9, tzinfo=UTC),
                idempotency_key="rogue-key",
                payload={},
                graph_snapshot_hash="c" * 64,
            ),
        )

    with pytest.raises(DeriveSourceError, match=r"CLAUDE\.md.*candidate\.proposed"):
        run_derive(
            project_root=fixture_repo,
            source_files=resolve_source_files(fixture_repo, (Path("CLAUDE.md"),)),
            tenant_id=default_tenant_id(fixture_repo),
            source_id=default_source_id(fixture_repo),
            extractor=wrong_type_extractor,
        )


def test_run_derive_rejects_extractor_events_for_foreign_tenant(
    fixture_repo: Path,
) -> None:
    def foreign_tenant_extractor(document: SourceDocument) -> tuple[LedgerEvent, ...]:
        return (
            LedgerEvent(
                tenant_id=OTHER_TENANT_ID,
                source_id=document.source_id,
                event_type=LedgerEventType.CANDIDATE_PROPOSED,
                actor=ActorRef(actor_type="derive", actor_id="rogue"),
                occurred_at=datetime(2026, 6, 9, tzinfo=UTC),
                idempotency_key="foreign-key",
                payload={},
            ),
        )

    with pytest.raises(DeriveSourceError, match=r"CLAUDE\.md.*tenant_id"):
        run_derive(
            project_root=fixture_repo,
            source_files=resolve_source_files(fixture_repo, (Path("CLAUDE.md"),)),
            tenant_id=default_tenant_id(fixture_repo),
            source_id=default_source_id(fixture_repo),
            extractor=foreign_tenant_extractor,
        )


def test_derive_is_deterministic_across_reruns_and_store_deletion(
    fixture_repo: Path,
) -> None:
    tenant_id = default_tenant_id(fixture_repo)
    source_id = default_source_id(fixture_repo)

    def run() -> tuple[frozenset[str], tuple[dict[str, object], ...]]:
        result = run_derive(
            project_root=fixture_repo,
            source_files=resolve_source_files(fixture_repo, ()),
            tenant_id=tenant_id,
            source_id=source_id,
            extractor=_candidate_per_document,
        )
        with DeriveEventStore(result.db_path) as store:
            return store.event_hashes(), store.export_events()

    first_hashes, first_export = run()
    assert len(first_hashes) == 3

    # Run twice over the same inputs: byte-identical event hash sets, all
    # duplicates ignored by the (tenant_id, idempotency_key) unique index.
    second_hashes, second_export = run()
    assert second_hashes == first_hashes
    assert second_export == first_export

    # Delete the store and re-run: the export is rebuilt identically.
    derive_store_path(fixture_repo).unlink()
    third_hashes, third_export = run()
    assert third_hashes == first_hashes
    assert third_export == first_export


def test_rerun_reports_duplicates_not_new_inserts(fixture_repo: Path) -> None:
    tenant_id = default_tenant_id(fixture_repo)
    source_id = default_source_id(fixture_repo)
    sources = resolve_source_files(fixture_repo, ())
    first = run_derive(
        project_root=fixture_repo,
        source_files=sources,
        tenant_id=tenant_id,
        source_id=source_id,
        extractor=_candidate_per_document,
    )
    second = run_derive(
        project_root=fixture_repo,
        source_files=sources,
        tenant_id=tenant_id,
        source_id=source_id,
        extractor=_candidate_per_document,
    )
    assert (first.inserted, first.ignored) == (3, 0)
    assert (second.inserted, second.ignored) == (0, 3)


def test_run_derive_persists_exactly_the_emitted_events(fixture_repo: Path) -> None:
    result = run_derive(
        project_root=fixture_repo,
        source_files=resolve_source_files(fixture_repo, (Path("CLAUDE.md"),)),
        tenant_id=default_tenant_id(fixture_repo),
        source_id=default_source_id(fixture_repo),
        extractor=_candidate_per_document,
    )
    with DeriveEventStore(result.db_path) as store:
        exported = store.export_events()
    assert [row["event_hash"] for row in exported] == [
        event.event_hash for event in result.events
    ]
    assert exported[0] == result.events[0].as_insert_parameters()


# ---------------------------------------------------------------------------
# Repo-native extractor end-to-end (issues #351 + #352 + #353)
# ---------------------------------------------------------------------------


@pytest.fixture
def populated_repo(tmp_path: Path) -> Path:
    """A fixture repo exercising all three repo-native source types."""

    root = tmp_path / "repo-native"
    (root / ".cortex").mkdir(parents=True)
    (root / "CLAUDE.md").write_text(
        "# Project instructions\n"
        "\n"
        "This project is a reference CLI.\n"
        "\n"
        "## Hard requirements\n"
        "\n"
        "- Never store credentials in `config/` files.\n"
        "- All ledger writes must go through `src/db/client.py`.\n"
        "\n"
        "Deploys must not run on Fridays.\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "# Agents\n"
        "\n"
        "- **One code path.** Share business logic across modes.\n",
        encoding="utf-8",
    )
    adr_dir = root / "docs" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "0001-use-postgres.md").write_text(
        "# 1. Use Postgres\n"
        "\n"
        "Status: Accepted\n"
        "\n"
        "## Context\n"
        "\n"
        "We need durable storage.\n"
        "\n"
        "## Decision\n"
        "\n"
        "We will use Postgres for the hosted ledger.\n",
        encoding="utf-8",
    )
    (adr_dir / "0002-drop-redis.md").write_text(
        "# 2. Drop Redis\n"
        "\n"
        "Status: Superseded by 0001\n"
        "\n"
        "## Decision\n"
        "\n"
        "We drop Redis in favor of Postgres LISTEN/NOTIFY.\n",
        encoding="utf-8",
    )
    github_dir = root / ".github"
    github_dir.mkdir()
    (github_dir / "CODEOWNERS").write_text(
        "# ownership\n"
        "*.py @backend-team\n"
        "docs/orphan\n",
        encoding="utf-8",
    )
    return root


def test_cli_repo_native_end_to_end_emits_candidates_with_stable_keys(
    populated_repo: Path,
) -> None:
    """`cortex derive` over all three source types: candidates persisted with
    stable idempotency keys, drops reported, reruns fully idempotent."""

    runner = CliRunner()
    first = runner.invoke(cli, ["derive", "--path", str(populated_repo)])
    assert first.exit_code == 0, first.output
    assert "5 source file(s)" in first.output
    assert "7 candidate event(s)" in first.output
    assert "(7 inserted, 0 duplicate)" in first.output
    assert "dropped:" in first.output
    assert "codeowners:unowned_pattern_reset x1" in first.output

    with DeriveEventStore(derive_store_path(populated_repo)) as store:
        exported = store.export_events()
    assert len(exported) == 7
    keys = [row["idempotency_key"] for row in exported]
    assert len(set(keys)) == 7

    second = runner.invoke(cli, ["derive", "--path", str(populated_repo)])
    assert second.exit_code == 0, second.output
    assert "(0 inserted, 7 duplicate)" in second.output
    with DeriveEventStore(derive_store_path(populated_repo)) as store:
        assert store.export_events() == exported


def test_cli_repo_native_events_carry_lane_and_span_provenance(
    populated_repo: Path,
) -> None:
    import json

    runner = CliRunner()
    result = runner.invoke(cli, ["derive", "--path", str(populated_repo)])
    assert result.exit_code == 0, result.output
    with DeriveEventStore(derive_store_path(populated_repo)) as store:
        exported = store.export_events()

    payloads = [json.loads(str(row["payload"])) for row in exported]
    by_source_type: dict[str, list[dict[str, object]]] = {}
    for payload in payloads:
        by_source_type.setdefault(str(payload["source_type"]), []).append(payload)
    assert sorted(by_source_type) == ["adr", "agent_instructions", "codeowners"]
    assert len(by_source_type["agent_instructions"]) == 4
    assert len(by_source_type["adr"]) == 2
    assert len(by_source_type["codeowners"]) == 1

    # Every candidate carries spans, and stored span hashes match the payload.
    for row, payload in zip(exported, payloads, strict=True):
        spans = payload["spans"]
        assert isinstance(spans, list) and spans
        assert row["source_span_hashes"] == [span["span_hash"] for span in spans]

    # Lane policy semantics: accepted ADR auto-promotes, superseded does not.
    adr_lanes = {
        str(payload["decision_text"]).splitlines()[0]: payload["lane_assignment"]
        for payload in by_source_type["adr"]
    }
    accepted = adr_lanes["1. Use Postgres"]
    superseded = adr_lanes["2. Drop Redis"]
    assert isinstance(accepted, dict) and isinstance(superseded, dict)
    assert accepted["auto_promotable"] is True
    assert accepted["backfilled"] is False
    assert superseded["auto_promotable"] is False
    assert superseded["advisory_only"] is True

    codeowners = by_source_type["codeowners"][0]
    assert codeowners["decision_text"] == "@backend-team own *.py"
    lane = codeowners["lane_assignment"]
    assert isinstance(lane, dict)
    assert lane["auto_promotable"] is True


def test_resolve_defaults_pick_one_codeowners_by_github_precedence(
    populated_repo: Path,
) -> None:
    # Add a second, lower-precedence CODEOWNERS; only .github/ should win.
    (populated_repo / "CODEOWNERS").write_text("*.md @docs-team\n", encoding="utf-8")
    files = resolve_source_files(populated_repo, ())
    codeowners = [path for path in files if path.name == "CODEOWNERS"]
    assert len(codeowners) == 1
    assert codeowners[0] == (populated_repo / ".github" / "CODEOWNERS").resolve()


def test_cli_unrecognized_explicit_source_fails_closed(populated_repo: Path) -> None:
    (populated_repo / "notes.md").write_text("# Notes\n\nFreeform text.\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["derive", "--path", str(populated_repo), "--source", "notes.md"]
    )
    assert result.exit_code == 1
    combined = _combined_output(result)
    assert "notes.md" in combined
    assert "no repo-native extractor recognizes" in combined
    assert not derive_store_path(populated_repo).exists()
