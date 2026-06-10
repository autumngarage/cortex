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
    assert DEFAULT_SOURCE_RELATIVE_PATHS == ("CLAUDE.md", "AGENTS.md", "docs/adr")


def test_resolve_explicit_missing_source_is_an_error(fixture_repo: Path) -> None:
    with pytest.raises(DeriveSourceError, match=r"does-not-exist\.md"):
        resolve_source_files(fixture_repo, (Path("does-not-exist.md"),))


def test_cli_scaffold_runs_with_empty_extractor_and_creates_store(
    fixture_repo: Path,
) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["derive", "--path", str(fixture_repo)])
    assert result.exit_code == 0, result.output
    assert "3 source file(s)" in result.output
    assert "0 candidate event(s)" in result.output
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
