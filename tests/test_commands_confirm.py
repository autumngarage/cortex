"""Tests for `cortex candidates` (issue #359) — human confirmation CLI.

Covers: list rendering with lane + provenance over the local derive store,
confirm/reject event emission through the one ledger envelope, span-citation
enforcement on confirm, ref resolution (unknown/ambiguous/short), repeat and
contradictory decisions, and the human-only write surface (no auto-confirm
affordance exists).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.confirm import (
    CandidateCommandError,
    candidates_group,
    load_candidate_rows,
    resolve_candidate,
)
from cortex.hosted.derive_store import DeriveEventStore, derive_store_path
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
SPAN_HASH = "e" * 64


def _candidate_event(
    *,
    text: str = "Use Postgres for the ledger.",
    external_id: str = "CLAUDE.md@2026-06-09T00:00:00+00:00#span",
    span_hashes: tuple[str, ...] = (SPAN_HASH,),
) -> LedgerEvent:
    payload = {
        "decision_text": text,
        "lane_assignment": {
            "lane": "structured",
            "source_type": "agent-instructions",
            "advisory_only": False,
            "backfilled": False,
        },
        "source_type": "agent-instructions",
        "spans": [{"span_hash": span_hash} for span_hash in span_hashes],
    }
    return LedgerEvent(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        event_type=LedgerEventType.CANDIDATE_PROPOSED,
        actor=ActorRef(actor_type="derive", actor_id="repo-native/test"),
        occurred_at=datetime(2026, 6, 9, tzinfo=UTC),
        idempotency_key=derive_idempotency_key(
            source_id=SOURCE_ID,
            event_type=LedgerEventType.CANDIDATE_PROPOSED,
            source_event_external_id=external_id,
            payload=payload,
        ),
        source_event_external_id=external_id,
        source_span_hashes=span_hashes,
        payload=payload,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cortex").mkdir(parents=True)
    return root


def _combined_output(result: object) -> str:
    output = getattr(result, "output", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return output + stderr


def _seed_store(root: Path, *events: LedgerEvent) -> None:
    with DeriveEventStore(derive_store_path(root)) as store:
        store.append_events(list(events))


def _export(root: Path) -> tuple[dict[str, object], ...]:
    with DeriveEventStore(derive_store_path(root)) as store:
        return store.export_events()


class TestList:
    def test_list_without_store_fails_visibly(self, project: Path) -> None:
        result = CliRunner().invoke(cli, ["candidates", "list", "--path", str(project)])
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "no derive store found" in combined
        assert "cortex derive" in combined

    def test_list_renders_lane_provenance_and_ref(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        result = CliRunner().invoke(cli, ["candidates", "list", "--path", str(project)])
        assert result.exit_code == 0
        assert event.event_hash[:12] in result.output
        assert "structured" in result.output
        assert "CLAUDE.md@" in result.output
        assert "Use Postgres for the ledger." in result.output
        assert "[proposed]" in result.output

    def test_list_with_empty_store_reports_none(self, project: Path) -> None:
        _seed_store(project)
        result = CliRunner().invoke(cli, ["candidates", "list", "--path", str(project)])
        assert result.exit_code == 0
        assert "none proposed" in result.output

    def test_list_shows_confirmed_status(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        runner = CliRunner()
        confirm = runner.invoke(
            cli,
            ["candidates", "confirm", event.event_hash[:12], "--by", "henry", "--path", str(project)],
        )
        assert confirm.exit_code == 0
        result = runner.invoke(cli, ["candidates", "list", "--path", str(project)])
        assert "[confirmed]" in result.output


class TestConfirm:
    def test_confirm_emits_decision_confirmed_citing_candidate_spans(
        self, project: Path
    ) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        result = CliRunner().invoke(
            cli,
            ["candidates", "confirm", event.event_hash[:12], "--by", "henry", "--path", str(project)],
        )
        assert result.exit_code == 0
        assert "confirmed: candidate" in result.output
        assert "1 span citation(s)" in result.output
        rows = _export(project)
        confirmations = [
            row for row in rows if row["event_type"] == LedgerEventType.DECISION_CONFIRMED.value
        ]
        assert len(confirmations) == 1
        confirmation = confirmations[0]
        assert confirmation["source_span_hashes"] == [SPAN_HASH]
        assert confirmation["actor_type"] == "human"
        assert confirmation["actor_id"] == "henry"
        payload = json.loads(str(confirmation["payload"]))
        assert payload["candidate_event_hash"] == event.event_hash
        assert payload["decision_text"] == "Use Postgres for the ledger."

    def test_confirm_without_spans_fails_closed_via_envelope(self, project: Path) -> None:
        """The envelope enforces spans on confirm; the CLI surfaces it."""

        event = _candidate_event(span_hashes=())
        _seed_store(project, event)
        result = CliRunner().invoke(
            cli,
            ["candidates", "confirm", event.event_hash[:12], "--path", str(project)],
        )
        assert result.exit_code == 1
        assert "requires at least one source span hash" in _combined_output(result)
        assert not [
            row
            for row in _export(project)
            if row["event_type"] == LedgerEventType.DECISION_CONFIRMED.value
        ]

    def test_confirm_twice_is_a_visible_noop(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        runner = CliRunner()
        first = runner.invoke(
            cli, ["candidates", "confirm", event.event_hash[:12], "--path", str(project)]
        )
        assert first.exit_code == 0
        second = runner.invoke(
            cli, ["candidates", "confirm", event.event_hash[:12], "--path", str(project)]
        )
        assert second.exit_code == 0
        assert "already confirmed; nothing to do" in second.output
        confirmations = [
            row
            for row in _export(project)
            if row["event_type"] == LedgerEventType.DECISION_CONFIRMED.value
        ]
        assert len(confirmations) == 1

    def test_reject_after_confirm_is_refused_visibly(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        runner = CliRunner()
        assert (
            runner.invoke(
                cli, ["candidates", "confirm", event.event_hash[:12], "--path", str(project)]
            ).exit_code
            == 0
        )
        result = runner.invoke(
            cli, ["candidates", "reject", event.event_hash[:12], "--path", str(project)]
        )
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "already confirmed" in combined
        assert "supersede" in combined

    def test_unknown_ref_fails_visibly(self, project: Path) -> None:
        _seed_store(project, _candidate_event())
        result = CliRunner().invoke(
            cli, ["candidates", "confirm", "f" * 12, "--path", str(project)]
        )
        assert result.exit_code == 1
        assert "no proposed candidate matches" in _combined_output(result)

    def test_short_ref_is_rejected(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        result = CliRunner().invoke(
            cli, ["candidates", "confirm", event.event_hash[:4], "--path", str(project)]
        )
        assert result.exit_code == 1
        assert "too short" in _combined_output(result)

    def test_no_auto_confirm_affordance_exists(self) -> None:
        """Human-confirmed writes only (issue #359, non-negotiable)."""

        for command in candidates_group.commands.values():
            for param in command.params:
                name = (param.name or "").lower()
                assert "auto" not in name
                assert name != "yes"
                for opt in getattr(param, "opts", ()):  # e.g. "--auto-confirm"
                    assert "auto" not in opt.lower()


class TestReject:
    def test_reject_emits_decision_rejected(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        result = CliRunner().invoke(
            cli,
            ["candidates", "reject", event.event_hash[:12], "--by", "henry", "--path", str(project)],
        )
        assert result.exit_code == 0
        assert "rejected: candidate" in result.output
        rows = _export(project)
        rejections = [
            row for row in rows if row["event_type"] == LedgerEventType.DECISION_REJECTED.value
        ]
        assert len(rejections) == 1
        payload = json.loads(str(rejections[0]["payload"]))
        assert payload["candidate_event_hash"] == event.event_hash

    def test_rejected_status_shows_in_list(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        runner = CliRunner()
        assert (
            runner.invoke(
                cli, ["candidates", "reject", event.event_hash[:12], "--path", str(project)]
            ).exit_code
            == 0
        )
        result = runner.invoke(cli, ["candidates", "list", "--path", str(project)])
        assert "[rejected]" in result.output


class TestResolution:
    def test_ambiguous_prefix_lists_matches(self) -> None:
        first = _candidate_event()
        second = _candidate_event(
            text="Ship the webhook verifier.",
            external_id="AGENTS.md@2026-06-09T00:00:00+00:00#span",
        )
        candidates, _ = load_candidate_rows(
            tuple(
                {
                    "event_type": LedgerEventType.CANDIDATE_PROPOSED.value,
                    "event_hash": "abcdef120000" + suffix * 52,
                    "tenant_id": event.tenant_id,
                    "source_id": event.source_id,
                    "idempotency_key": event.idempotency_key,
                    "source_event_external_id": event.source_event_external_id,
                    "source_span_hashes": list(event.source_span_hashes),
                    "payload": json.dumps(dict(event.payload)),
                }
                for suffix, event in (("a", first), ("b", second))
            )
        )
        with pytest.raises(CandidateCommandError, match="ambiguous"):
            resolve_candidate(candidates, "abcdef12")

    def test_full_hash_resolves(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        result = CliRunner().invoke(
            cli, ["candidates", "confirm", event.event_hash, "--path", str(project)]
        )
        assert result.exit_code == 0
