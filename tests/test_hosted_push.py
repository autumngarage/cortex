"""Tests for the `cortex push` substrate (cortex#513).

The hosted SQL path is exercised against ``FakeHostedDb`` — an in-memory,
transactional emulation of exactly the Postgres slice the push pipeline
touches (idempotent ledger appends, insert-or-select provenance upserts,
projection writes, snapshot registration). The fake honors commit/rollback
staging so the GraphWritePlan replay contract and the one-transaction-per-
event discipline are tested for real, not assumed. Live-Postgres coverage
stays env-gated in ``tests/test_hosted_push_integration.py``.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4, uuid5

import pytest

from cortex.commands.confirm import build_decision_event, load_candidate_rows
from cortex.commands.derive import DERIVE_AUTHOR_REF, DERIVE_DOCUMENT_TYPE
from cortex.hosted.derive_store import DeriveEventStore
from cortex.hosted.extractors import RepoNativeExtractor
from cortex.hosted.ledger_events import ActorRef, LedgerEvent, LedgerEventType
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.push import (
    PUSH_UUID_NAMESPACE,
    CandidateProvenance,
    HostedPushError,
    PushOutcome,
    PushSkip,
    candidate_confidence,
    candidate_scopes,
    decision_node_id_for_candidate,
    decision_version_id_for_candidate,
    graph_snapshot_insert_sql,
    rebuild_candidate_provenance,
    reconstruct_ledger_event,
    run_push,
    source_insert_sql,
    tenant_insert_sql,
    tenant_slug,
)

TENANT_ID = str(uuid4())
SOURCE_ID = str(uuid4())
RULES_MARKDOWN = (
    "# Working agreements\n"
    "\n"
    "- Always use exponential backoff for HTTP retries; never retry more "
    "than five times.\n"
)


# ---------------------------------------------------------------------------
# FakeHostedDb — the in-memory Postgres slice (shared with the CLI tests)
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]], rowcount: int) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows)


class _State:
    def __init__(self) -> None:
        self.tenants: dict[str, str] = {}
        self.sources: dict[str, dict[str, str]] = {}
        # (tenant_id, idempotency_key) -> (event_id, event_hash)
        self.ledger: dict[tuple[str, str], tuple[str, str]] = {}
        # (tenant, source, external_id, content_hash) -> (id, content_hash, document_hash)
        self.documents: dict[tuple[str, str, str, str], tuple[str, str, str]] = {}
        # (tenant, span_hash) -> (id, source_document_hash, span_hash)
        self.spans: dict[tuple[str, str], tuple[str, str, str]] = {}
        self.nodes: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, dict[str, Any]] = {}
        self.edges: list[tuple[str, str, str]] = []
        # (node_id, scope_type, normalized_value) -> row dict
        self.scopes: dict[tuple[str, str, str], dict[str, Any]] = {}
        # (tenant, graph_snapshot_hash) -> snapshot_id
        self.snapshots: dict[tuple[str, str], str] = {}


class FakeHostedDb:
    """Transactional in-memory emulation of the push pipeline's SQL surface."""

    def __init__(self) -> None:
        self.state = _State()
        self._tx: _State | None = None
        self.closed = False

    def _current(self) -> _State:
        if self._tx is None:
            self._tx = copy.deepcopy(self.state)
        return self._tx

    def commit(self) -> None:
        if self._tx is not None:
            self.state = self._tx
            self._tx = None

    def rollback(self) -> None:
        self._tx = None

    def close(self) -> None:
        self.closed = True

    def execute(self, query: str, params: Mapping[str, Any] | None = None) -> FakeCursor:
        state = self._current()
        p = dict(params or {})
        q = query.strip()
        if q.startswith("INSERT INTO cortex_hosted.tenants"):
            tenant = str(p["tenant_id"])
            if tenant not in state.tenants and p["slug"] not in state.tenants.values():
                state.tenants[tenant] = str(p["slug"])
            return FakeCursor([], 0)
        if q.startswith("INSERT INTO cortex_hosted.sources"):
            state.sources.setdefault(
                str(p["source_id"]),
                {
                    "tenant_id": str(p["tenant_id"]),
                    "source_type": str(p["source_type"]),
                    "external_id": str(p["external_id"]),
                },
            )
            return FakeCursor([], 0)
        if q.startswith("INSERT INTO cortex_hosted.ledger_events"):
            key = (str(p["tenant_id"]), str(p["idempotency_key"]))
            if key in state.ledger:
                return FakeCursor([], 0)
            event_id = str(uuid4())
            state.ledger[key] = (event_id, str(p["event_hash"]))
            return FakeCursor([(event_id, str(p["event_hash"]))], 1)
        if "INSERT INTO cortex_hosted.source_documents" in q:
            doc_key = (
                str(p["tenant_id"]),
                str(p["source_id"]),
                str(p["external_id"]),
                str(p["content_hash"]),
            )
            doc_row = state.documents.get(doc_key)
            if doc_row is None:
                doc_row = (str(uuid4()), str(p["content_hash"]), str(p["document_hash"]))
                state.documents[doc_key] = doc_row
            return FakeCursor([doc_row], 1)
        if "INSERT INTO cortex_hosted.source_spans" in q:
            span_key = (str(p["tenant_id"]), str(p["span_hash"]))
            span_row = state.spans.get(span_key)
            if span_row is None:
                span_row = (str(uuid4()), str(p["source_document_hash"]), str(p["span_hash"]))
                state.spans[span_key] = span_row
            return FakeCursor([span_row], 1)
        if q.startswith("INSERT INTO cortex_hosted.decision_nodes"):
            state.nodes[str(p["decision_node_id"])] = {
                "tenant_id": str(p["tenant_id"]),
                "status": "candidate",
                "confidence": str(p["confidence"]),
                "current_version_id": str(p["decision_version_id"]),
                "repo_id": p["repo_id"],
            }
            return FakeCursor([], 1)
        if q.startswith("INSERT INTO cortex_hosted.decision_versions"):
            state.versions[str(p["decision_version_id"])] = {
                "tenant_id": str(p["tenant_id"]),
                "decision_node_id": str(p["decision_node_id"]),
                "decision_text": str(p["decision_text"]),
                "source_span_hashes": list(p["source_span_hashes"]),
                "scope": json.loads(str(p["scope"])),
                "decided_at": p["occurred_at"],
            }
            return FakeCursor([], 1)
        if q.startswith("INSERT INTO cortex_hosted.decision_scopes"):
            scope_key = (
                str(p["decision_node_id"]),
                str(p["scope_type"]),
                str(p["normalized_value"]),
            )
            state.scopes.setdefault(
                scope_key,
                {"tenant_id": str(p["tenant_id"]), "scope_value": str(p["scope_value"])},
            )
            return FakeCursor([], 1)
        if q.startswith("UPDATE cortex_hosted.decision_nodes"):
            node = state.nodes.get(str(p["decision_node_id"]))
            if node is None or node["tenant_id"] != str(p["tenant_id"]):
                return FakeCursor([], 0)
            node["status"] = str(p["new_status"])
            return FakeCursor([], 1)
        if q.startswith("INSERT INTO cortex_hosted.graph_snapshots"):
            event = state.ledger.get((str(p["tenant_id"]), str(p["idempotency_key"])))
            snap_key = (str(p["tenant_id"]), str(p["graph_snapshot_hash"]))
            if event is None or snap_key in state.snapshots:
                return FakeCursor([], 0)
            snapshot_id = str(uuid4())
            state.snapshots[snap_key] = snapshot_id
            return FakeCursor([(snapshot_id,)], 1)
        if q.startswith("SELECT graph_snapshot_id"):
            existing = state.snapshots.get(
                (str(p["tenant_id"]), str(p["graph_snapshot_hash"]))
            )
            return FakeCursor([] if existing is None else [(existing,)], -1)
        if q.startswith("SELECT decision_node_id, status"):
            return FakeCursor(
                [
                    (
                        node_id,
                        node["status"],
                        node["confidence"],
                        node["current_version_id"],
                        node["repo_id"],
                    )
                    for node_id, node in sorted(state.nodes.items())
                    if node["tenant_id"] == str(p["tenant_id"])
                ],
                -1,
            )
        if q.startswith("SELECT decision_version_id"):
            return FakeCursor(
                [
                    (
                        version_id,
                        version["decision_node_id"],
                        version["decision_text"],
                        version["source_span_hashes"],
                        version["scope"],
                        version["decided_at"],
                    )
                    for version_id, version in sorted(state.versions.items())
                    if version["tenant_id"] == str(p["tenant_id"])
                ],
                -1,
            )
        if q.startswith("SELECT from_node_id"):
            return FakeCursor(list(state.edges), -1)
        if q.startswith("SELECT decision_node_id, scope_type"):
            return FakeCursor(
                [
                    (node_id, scope_type, row["scope_value"], normalized)
                    for (node_id, scope_type, normalized), row in sorted(state.scopes.items())
                    if row["tenant_id"] == str(p["tenant_id"])
                ],
                -1,
            )
        raise AssertionError(f"unrouted SQL in FakeHostedDb: {q.splitlines()[0]}")


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def write_rules_file(root: Path, content: str = RULES_MARKDOWN) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "CLAUDE.md"
    path.write_text(content, encoding="utf-8")
    return path


def derive_candidate_events(
    root: Path, *, tenant_id: str = TENANT_ID, source_id: str = SOURCE_ID
) -> tuple[LedgerEvent, ...]:
    """Extract genuine candidate events the way `cortex derive` does."""

    path = root / "CLAUDE.md"
    document = SourceDocument(
        tenant_id=tenant_id,
        source_id=source_id,
        document_type=DERIVE_DOCUMENT_TYPE,
        external_id="CLAUDE.md",
        permalink="CLAUDE.md",
        author_ref=DERIVE_AUTHOR_REF,
        source_timestamp=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        content=path.read_text(encoding="utf-8"),
    )
    events = RepoNativeExtractor()(document)
    assert events, "fixture markdown must extract at least one candidate"
    return events


def export_rows(
    db_path: Path, events: tuple[LedgerEvent, ...]
) -> tuple[dict[str, Any], ...]:
    """Round-trip events through a real derive store, as the CLI does."""

    with DeriveEventStore(db_path) as store:
        store.append_events(events)
        return store.export_events()


def assert_push_arithmetic_balances(outcome: PushOutcome) -> None:
    """Invariant: every exported event lands in exactly one visible bucket."""

    assert (
        outcome.appended + outcome.replayed + len(outcome.skipped) + len(outcome.failed)
        == outcome.total_events
    )


# ---------------------------------------------------------------------------
# Reconstruction
# ---------------------------------------------------------------------------


def test_reconstruct_round_trips_derive_store_rows(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    events = derive_candidate_events(root)
    rows = export_rows(tmp_path / "store.sqlite", events)

    reconstructed = [reconstruct_ledger_event(row) for row in rows]
    assert {event.event_hash for event in reconstructed} == {
        event.event_hash for event in events
    }
    by_hash = {event.event_hash: event for event in events}
    for event in reconstructed:
        assert event.as_insert_parameters() == by_hash[event.event_hash].as_insert_parameters()


def test_reconstruct_refuses_stored_event_hash_drift(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    events = derive_candidate_events(root)
    rows = export_rows(tmp_path / "store.sqlite", events)

    drifted = dict(rows[0])
    drifted["payload"] = json.dumps({"decision_text": "tampered"})
    with pytest.raises(HostedPushError, match="drifted"):
        reconstruct_ledger_event(drifted)


def test_reconstruct_accepts_iso_occurred_at(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    events = derive_candidate_events(root)
    rows = export_rows(tmp_path / "store.sqlite", events)

    row = dict(rows[0])
    assert isinstance(row["occurred_at"], datetime)
    row["occurred_at"] = row["occurred_at"].isoformat()
    assert reconstruct_ledger_event(row).event_hash == row["event_hash"]


def test_reconstruct_names_the_malformed_field(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    rows = export_rows(tmp_path / "store.sqlite", derive_candidate_events(root))

    row = dict(rows[0])
    row["occurred_at"] = "not-a-timestamp"
    with pytest.raises(HostedPushError, match="occurred_at"):
        reconstruct_ledger_event(row)


# ---------------------------------------------------------------------------
# Deterministic projection identifiers
# ---------------------------------------------------------------------------


def test_decision_ids_are_deterministic_and_distinct() -> None:
    candidate_hash = "ab" * 32
    node_id = decision_node_id_for_candidate(candidate_hash)
    version_id = decision_version_id_for_candidate(candidate_hash)
    assert node_id == decision_node_id_for_candidate(candidate_hash)
    assert version_id == decision_version_id_for_candidate(candidate_hash)
    assert node_id != version_id
    assert node_id == str(uuid5(PUSH_UUID_NAMESPACE, f"decision-node:{candidate_hash}"))


def test_decision_ids_refuse_non_sha256_material() -> None:
    with pytest.raises(HostedPushError, match="sha256"):
        decision_node_id_for_candidate("not-a-hash")


# ---------------------------------------------------------------------------
# Confidence and scope resolution
# ---------------------------------------------------------------------------


def test_candidate_confidence_prefers_explicit_string() -> None:
    assert candidate_confidence({"confidence": "suggest"}, citation_count=1) == "suggest"


def test_candidate_confidence_reads_confidence_state_tier() -> None:
    assert (
        candidate_confidence({"confidence": {"tier": "advisory"}}, citation_count=1)
        == "advisory"
    )


def test_candidate_confidence_derives_from_lane_assignment(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    event = derive_candidate_events(root)[0]
    confidence = candidate_confidence(
        event.payload, citation_count=len(event.source_span_hashes)
    )
    assert confidence == "advisory"


def test_candidate_confidence_fails_without_material() -> None:
    with pytest.raises(HostedPushError, match="confidence"):
        candidate_confidence({"decision_text": "x"}, citation_count=1)


def test_candidate_scopes_accept_real_extractor_payloads(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(
        root,
        "# Working agreements\n\n- Never edit `src/cortex/cli.py` without a test.\n",
    )
    event = derive_candidate_events(root)[0]
    triples = candidate_scopes(event.payload)
    for scope_type, scope_value, normalized in triples:
        assert scope_type
        assert scope_value
        assert normalized


def test_candidate_scopes_reject_forged_normalization() -> None:
    payload = {
        "proposed_scopes": [
            {"scope_type": "path", "value": "src/cortex/cli.py", "normalized_value": "forged"}
        ]
    }
    with pytest.raises(HostedPushError, match="re-derived normalization"):
        candidate_scopes(payload)


# ---------------------------------------------------------------------------
# Provenance rebuild
# ---------------------------------------------------------------------------


def test_rebuild_provenance_round_trips_file_backed_spans(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    event = derive_candidate_events(root)[0]
    rebuilt = rebuild_candidate_provenance(
        event,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert isinstance(rebuilt, CandidateProvenance)
    assert frozenset(span.span_hash for span in rebuilt.spans) == frozenset(
        event.source_span_hashes
    )
    assert rebuilt.document.document_hash == event.payload["spans"][0]["source_document_hash"]


def test_rebuild_provenance_skips_on_content_drift(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    event = derive_candidate_events(root)[0]
    (root / "CLAUDE.md").write_text(
        RULES_MARKDOWN + "\n- New rule added after derive.\n", encoding="utf-8"
    )
    rebuilt = rebuild_candidate_provenance(
        event,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert isinstance(rebuilt, PushSkip)
    assert "CLAUDE.md" in rebuilt.reason
    assert "content drift" in rebuilt.reason


def test_rebuild_provenance_skips_missing_file(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    event = derive_candidate_events(root)[0]
    (root / "CLAUDE.md").unlink()
    rebuilt = rebuild_candidate_provenance(
        event,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert isinstance(rebuilt, PushSkip)
    assert "CLAUDE.md" in rebuilt.reason
    assert "missing" in rebuilt.reason


def test_rebuild_provenance_skips_non_file_backed_sources(tmp_path: Path) -> None:
    span_hash = "ab" * 32
    document_hash = "cd" * 32
    event = LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=ActorRef(actor_type="derive", actor_id="commit-extractor"),
        occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key="commit-candidate",
        source_span_hashes=(span_hash,),
        payload={
            "decision_text": "use exponential backoff",
            "source_type": "commit_message",
            "spans": [
                {
                    "start_offset": 0,
                    "end_offset": 10,
                    "permalink": "commit:abc123",
                    "source_document_hash": document_hash,
                    "span_hash": span_hash,
                    "excerpt": "use exponential backoff"[:10],
                }
            ],
        },
    )
    rebuilt = rebuild_candidate_provenance(
        event,
        project_root=tmp_path,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert isinstance(rebuilt, PushSkip)
    assert "not file-backed" in rebuilt.reason


def test_rebuild_provenance_refuses_traversal_permalinks(tmp_path: Path) -> None:
    span_hash = "ab" * 32
    event = LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=ActorRef(actor_type="derive", actor_id="x"),
        occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key="traversal-candidate",
        source_span_hashes=(span_hash,),
        payload={
            "decision_text": "x",
            "source_type": "adr",
            "spans": [
                {
                    "start_offset": 0,
                    "end_offset": 1,
                    "permalink": "../outside.md",
                    "source_document_hash": "cd" * 32,
                    "span_hash": span_hash,
                }
            ],
        },
    )
    with pytest.raises(HostedPushError, match="repo-relative"):
        rebuild_candidate_provenance(
            event,
            project_root=tmp_path,
            document_type=DERIVE_DOCUMENT_TYPE,
            author_ref=DERIVE_AUTHOR_REF,
        )


# ---------------------------------------------------------------------------
# SQL surfaces
# ---------------------------------------------------------------------------


def test_identity_and_snapshot_sql_are_idempotent_and_validated() -> None:
    assert "ON CONFLICT DO NOTHING" in tenant_insert_sql()
    assert "ON CONFLICT DO NOTHING" in source_insert_sql()
    assert "ON CONFLICT (tenant_id, graph_snapshot_hash) DO NOTHING" in (
        graph_snapshot_insert_sql()
    )
    for builder in (tenant_insert_sql, source_insert_sql, graph_snapshot_insert_sql):
        with pytest.raises(HostedPushError, match="invalid SQL identifier"):
            builder("bad-schema; DROP TABLE")


# ---------------------------------------------------------------------------
# run_push — the full pipeline against the fake hosted store
# ---------------------------------------------------------------------------


def test_run_push_appends_then_replays_idempotently(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    events = derive_candidate_events(root)
    rows = export_rows(tmp_path / "store.sqlite", events)
    db = FakeHostedDb()

    first = run_push(
        db,
        rows,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert_push_arithmetic_balances(first)
    assert first.appended == len(events)
    assert first.replayed == 0
    assert first.failed == ()
    assert first.candidates_projected == len(events)
    assert first.documents_upserted == len(events)
    assert first.spans_upserted >= len(events)
    assert first.snapshot is not None
    assert first.snapshot.registered is True
    assert first.snapshot.event_appended is True
    assert first.snapshot.nodes == len(events)
    assert TENANT_ID in db.state.tenants
    assert db.state.tenants[TENANT_ID] == tenant_slug(TENANT_ID)
    assert SOURCE_ID in db.state.sources

    second = run_push(
        db,
        rows,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert_push_arithmetic_balances(second)
    assert second.appended == 0
    assert second.replayed == len(events)
    assert second.failed == ()
    assert second.snapshot is not None
    assert second.snapshot.snapshot_hash == first.snapshot.snapshot_hash
    assert second.snapshot.registered is False
    assert second.snapshot.event_appended is False
    assert len(db.state.snapshots) == 1


def test_run_push_projects_confirmation_as_status_transition(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    events = derive_candidate_events(root)
    store_path = tmp_path / "store.sqlite"
    rows = export_rows(store_path, events)
    db = FakeHostedDb()
    run_push(
        db,
        rows,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )

    candidates, _ = load_candidate_rows(rows)
    confirm_event = build_decision_event(
        candidates[0],
        event_type=LedgerEventType.DECISION_CONFIRMED,
        actor_id="reviewer",
        occurred_at=datetime.now(tz=UTC),
    )
    with DeriveEventStore(store_path) as store:
        store.append_events([confirm_event])
        rows_with_confirm = store.export_events()

    outcome = run_push(
        db,
        rows_with_confirm,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert_push_arithmetic_balances(outcome)
    assert outcome.appended == 1
    assert outcome.transitions_projected == 1
    assert outcome.replayed == len(events)
    node_id = decision_node_id_for_candidate(candidates[0].event_hash)
    assert db.state.nodes[node_id]["status"] == "confirmed"
    # The graph changed, so a new snapshot hash is registered.
    assert outcome.snapshot is not None
    assert outcome.snapshot.registered is True
    assert len(db.state.snapshots) == 2


def test_run_push_transition_without_candidate_fails_visibly_and_rolls_back(
    tmp_path: Path,
) -> None:
    from cortex.commands.confirm import CandidateRow

    orphan = CandidateRow(
        event_hash="ef" * 32,
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        idempotency_key="orphan-candidate",
        external_id="CLAUDE.md",
        span_hashes=("ab" * 32,),
        payload={"decision_text": "orphaned decision"},
    )
    confirm_event = build_decision_event(
        orphan,
        event_type=LedgerEventType.DECISION_CONFIRMED,
        actor_id="reviewer",
        occurred_at=datetime.now(tz=UTC),
    )
    rows = export_rows(tmp_path / "store.sqlite", (confirm_event,))
    db = FakeHostedDb()

    outcome = run_push(
        db,
        rows,
        project_root=tmp_path,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert_push_arithmetic_balances(outcome)
    assert outcome.appended == 0
    assert len(outcome.failed) == 1
    assert outcome.failed[0].idempotency_key == confirm_event.idempotency_key
    assert "expected 1" in outcome.failed[0].reason
    # The failed plan's ledger append rolled back: a retry can still push it.
    assert (TENANT_ID, confirm_event.idempotency_key) not in db.state.ledger


def test_run_push_skips_drifted_candidate_visibly(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    events = derive_candidate_events(root)
    rows = export_rows(tmp_path / "store.sqlite", events)
    (root / "CLAUDE.md").write_text("# Rewritten after derive\n", encoding="utf-8")
    db = FakeHostedDb()

    outcome = run_push(
        db,
        rows,
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert_push_arithmetic_balances(outcome)
    assert outcome.appended == 0
    assert len(outcome.skipped) == len(events)
    assert all("CLAUDE.md" in skip.reason for skip in outcome.skipped)
    assert db.state.nodes == {}
    # No candidate event reached the ledger; the only appended row is the
    # snapshot stage's projection.rebuilt event over the (empty) live graph.
    candidate_keys = {event.idempotency_key for event in events}
    assert not candidate_keys & {key for _, key in db.state.ledger}
    assert outcome.snapshot is not None
    assert outcome.snapshot.nodes == 0


def test_run_push_refuses_multi_tenant_exports(tmp_path: Path) -> None:
    root_a = tmp_path / "repo-a"
    root_b = tmp_path / "repo-b"
    write_rules_file(root_a)
    write_rules_file(root_b)
    events_a = derive_candidate_events(root_a, tenant_id=str(uuid4()), source_id=str(uuid4()))
    events_b = derive_candidate_events(root_b, tenant_id=str(uuid4()), source_id=str(uuid4()))
    rows = export_rows(tmp_path / "store.sqlite", (*events_a, *events_b))

    with pytest.raises(HostedPushError, match="multiple tenants"):
        run_push(
            FakeHostedDb(),
            rows,
            project_root=root_a,
            document_type=DERIVE_DOCUMENT_TYPE,
            author_ref=DERIVE_AUTHOR_REF,
        )


def test_run_push_skips_unpushable_event_types_visibly(tmp_path: Path) -> None:
    event = LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.PROJECTION_REBUILT,
        actor=ActorRef(actor_type="cli", actor_id="elsewhere"),
        occurred_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
        idempotency_key="foreign-rebuild",
        payload={"graph_snapshot_hash": "ab" * 32},
        graph_snapshot_hash="ab" * 32,
    )
    rows = export_rows(tmp_path / "store.sqlite", (event,))
    db = FakeHostedDb()

    outcome = run_push(
        db,
        rows,
        project_root=tmp_path,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert_push_arithmetic_balances(outcome)
    assert len(outcome.skipped) == 1
    assert "not pushable" in outcome.skipped[0].reason


def test_run_push_counts_unreconstructable_rows_and_continues(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_rules_file(root)
    events = derive_candidate_events(root)
    rows = export_rows(tmp_path / "store.sqlite", events)
    tampered = dict(rows[0])
    tampered["payload"] = json.dumps({"decision_text": "tampered"})
    db = FakeHostedDb()

    outcome = run_push(
        db,
        (tampered, *rows[1:]),
        project_root=root,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert_push_arithmetic_balances(outcome)
    assert len(outcome.failed) == 1
    assert "drifted" in outcome.failed[0].reason
    assert outcome.appended == len(events) - 1


def test_run_push_with_no_reconstructable_rows_skips_the_snapshot(tmp_path: Path) -> None:
    tampered = {"idempotency_key": "broken", "payload": "not-json"}
    outcome = run_push(
        FakeHostedDb(),
        (tampered,),
        project_root=tmp_path,
        document_type=DERIVE_DOCUMENT_TYPE,
        author_ref=DERIVE_AUTHOR_REF,
    )
    assert outcome.snapshot is None
    assert len(outcome.failed) == 1
    assert_push_arithmetic_balances(outcome)
