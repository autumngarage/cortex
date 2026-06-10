"""Tests for `cortex candidates triage` (cortex#514) — the confirm ritual.

Covers: the interactive y/n/s/q loop over CliRunner input streams
(structured lane first), lane filtering and limits, envelope parity with the
per-ref confirm path (one write path), span-less confirm rejection, the
`--accept-refs FILE` scripted batch (validate-all-then-persist), the
explicit `--accept-structured` structured-lane batch accept (never
provisional), the impossibility of auto-confirm affordances, and the
end-of-session summary with the push -> ask next-step hint chain.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.confirm import (
    TRIAGE_NEXT_STEPS,
    CandidateCommandError,
    CandidateRow,
    candidates_group,
    count_pending_candidates,
    load_candidate_rows,
    order_for_triage,
    parse_accept_refs,
    triage_command,
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
    lane: str = "structured",
    source_type: str = "agent-instructions",
    span_extras: dict[str, object] | None = None,
) -> LedgerEvent:
    payload = {
        "decision_text": text,
        "lane_assignment": {
            "lane": lane,
            "source_type": source_type,
            "advisory_only": lane != "structured",
            "backfilled": False,
        },
        "source_type": source_type,
        "spans": [
            {"span_hash": span_hash, **(span_extras or {})} for span_hash in span_hashes
        ],
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


def _rows_of_type(root: Path, event_type: LedgerEventType) -> list[dict[str, object]]:
    return [row for row in _export(root) if row["event_type"] == event_type.value]


class TestInteractive:
    def test_triage_without_store_fails_visibly(self, project: Path) -> None:
        result = CliRunner().invoke(cli, ["candidates", "triage", "--path", str(project)])
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "no derive store found" in combined
        assert "cortex derive" in combined

    def test_confirm_reject_skip_quit_with_summary_and_hint_chain(
        self, project: Path
    ) -> None:
        events = [
            _candidate_event(text=f"Decision {index}.", external_id=f"CLAUDE.md#s{index}")
            for index in range(4)
        ]
        _seed_store(project, *events)
        result = CliRunner().invoke(
            cli,
            ["candidates", "triage", "--by", "henry", "--path", str(project)],
            input="y\nn\ns\nq\n",
        )
        assert result.exit_code == 0
        output = result.output
        assert "confirmed: candidate" in output
        assert "rejected: candidate" in output
        assert "quit: 1 candidate(s) left unreviewed" in output
        assert "triage: 1 confirmed, 1 rejected, 1 skipped (2 still pending)" in output
        # The next-step hint chain: push, then ask (cortex#514).
        assert TRIAGE_NEXT_STEPS in output
        assert "cortex push" in output
        assert "cortex ask" in output
        confirmations = _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)
        rejections = _rows_of_type(project, LedgerEventType.DECISION_REJECTED)
        assert len(confirmations) == 1
        assert len(rejections) == 1
        assert confirmations[0]["actor_type"] == "human"
        assert confirmations[0]["actor_id"] == "henry"

    def test_triage_emits_the_same_envelope_as_the_confirm_command(
        self, project: Path
    ) -> None:
        """One write path: triage rows are shape-identical to per-ref confirms."""

        triage_event = _candidate_event(text="Via triage.", external_id="CLAUDE.md#t")
        command_event = _candidate_event(text="Via confirm.", external_id="CLAUDE.md#c")
        _seed_store(project, triage_event, command_event)
        runner = CliRunner()
        assert (
            runner.invoke(
                cli,
                ["candidates", "triage", "--by", "henry", "--path", str(project), "--limit", "1"],
                input="y\n",
            ).exit_code
            == 0
        )
        assert (
            runner.invoke(
                cli,
                [
                    "candidates",
                    "confirm",
                    command_event.event_hash[:12],
                    "--by",
                    "henry",
                    "--path",
                    str(project),
                ],
            ).exit_code
            == 0
        )
        confirmations = _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)
        assert len(confirmations) == 2
        by_candidate = {
            json.loads(str(row["payload"]))["candidate_event_hash"]: row
            for row in confirmations
        }
        triage_row = by_candidate[triage_event.event_hash]
        command_row = by_candidate[command_event.event_hash]
        assert set(json.loads(str(triage_row["payload"]))) == set(
            json.loads(str(command_row["payload"]))
        )
        assert triage_row["source_span_hashes"] == [SPAN_HASH]
        assert triage_row["actor_type"] == command_row["actor_type"] == "human"

    def test_structured_lane_is_presented_first(self, project: Path) -> None:
        provisional = _candidate_event(
            text="Provisional idea.",
            external_id="commit#1",
            lane="provisional",
            source_type="commit-message",
        )
        structured = _candidate_event(text="Structured rule.", external_id="CLAUDE.md#r")
        # Seed the provisional candidate first so store order alone would
        # present it first; the lane ordering must override that.
        _seed_store(project, provisional, structured)
        result = CliRunner().invoke(
            cli, ["candidates", "triage", "--path", str(project)], input="q\n"
        )
        assert result.exit_code == 0
        assert "[1/2]" in result.output
        first_block = result.output.split("[1/2]", 1)[1]
        assert structured.event_hash[:12] in first_block.split("confirm?", 1)[0]
        assert "lane: structured" in first_block.split("confirm?", 1)[0]

    def test_lane_filter_limits_the_queue(self, project: Path) -> None:
        provisional = _candidate_event(
            text="Provisional idea.",
            external_id="commit#1",
            lane="provisional",
            source_type="commit-message",
        )
        structured = _candidate_event(text="Structured rule.", external_id="CLAUDE.md#r")
        _seed_store(project, provisional, structured)
        result = CliRunner().invoke(
            cli,
            ["candidates", "triage", "--lane", "provisional", "--path", str(project)],
            input="y\n",
        )
        assert result.exit_code == 0
        assert "[1/1]" in result.output
        assert provisional.event_hash[:12] in result.output
        assert structured.event_hash[:12] not in result.output

    def test_limit_caps_the_session(self, project: Path) -> None:
        events = [
            _candidate_event(text=f"Decision {index}.", external_id=f"CLAUDE.md#s{index}")
            for index in range(3)
        ]
        _seed_store(project, *events)
        result = CliRunner().invoke(
            cli,
            ["candidates", "triage", "--limit", "1", "--path", str(project)],
            input="y\n",
        )
        assert result.exit_code == 0
        assert "[1/1]" in result.output
        assert "(2 still pending)" in result.output

    def test_decided_candidates_are_not_re_presented(self, project: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        runner = CliRunner()
        assert (
            runner.invoke(
                cli, ["candidates", "confirm", event.event_hash[:12], "--path", str(project)]
            ).exit_code
            == 0
        )
        result = runner.invoke(cli, ["candidates", "triage", "--path", str(project)])
        assert result.exit_code == 0
        assert "no pending candidates to review" in result.output

    def test_empty_store_names_derive_as_the_next_step(self, project: Path) -> None:
        _seed_store(project)
        result = CliRunner().invoke(cli, ["candidates", "triage", "--path", str(project)])
        assert result.exit_code == 0
        assert "no candidates proposed" in result.output
        assert "cortex derive" in result.output

    def test_spanless_confirm_fails_closed_and_stays_pending(self, project: Path) -> None:
        """The envelope enforces span citations on confirm; triage surfaces it."""

        event = _candidate_event(span_hashes=())
        _seed_store(project, event)
        result = CliRunner().invoke(
            cli, ["candidates", "triage", "--path", str(project)], input="y\n"
        )
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "requires at least one source span hash" in combined
        assert "stays pending" in combined
        assert "1 failed" in combined
        assert not _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)

    def test_renders_decision_text_lane_provenance_and_permalink(
        self, project: Path
    ) -> None:
        event = _candidate_event(
            span_extras={
                "permalink": "CLAUDE.md#L10-L12",
                "excerpt": "Use Postgres for the ledger.",
            }
        )
        _seed_store(project, event)
        result = CliRunner().invoke(
            cli, ["candidates", "triage", "--path", str(project)], input="s\n"
        )
        assert result.exit_code == 0
        assert "Use Postgres for the ledger." in result.output
        assert "lane: structured (agent-instructions)" in result.output
        assert "provenance: CLAUDE.md@" in result.output
        assert "permalink: CLAUDE.md#L10-L12" in result.output

    def test_missing_permalink_is_visible_not_blank(self, project: Path) -> None:
        _seed_store(project, _candidate_event())
        result = CliRunner().invoke(
            cli, ["candidates", "triage", "--path", str(project)], input="s\n"
        )
        assert result.exit_code == 0
        assert "permalink: (permalink not recorded)" in result.output


class TestAcceptRefs:
    def test_batch_confirms_listed_refs(self, project: Path, tmp_path: Path) -> None:
        first = _candidate_event(text="First.", external_id="CLAUDE.md#1")
        second = _candidate_event(text="Second.", external_id="CLAUDE.md#2")
        _seed_store(project, first, second)
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text(
            "# scripted batch confirm\n"
            f"{first.event_hash}\n"
            "\n"
            f"{second.event_hash[:12]}  # short ref\n",
            encoding="utf-8",
        )
        result = CliRunner().invoke(
            cli,
            [
                "candidates",
                "triage",
                "--accept-refs",
                str(refs_file),
                "--by",
                "henry",
                "--path",
                str(project),
            ],
        )
        assert result.exit_code == 0
        assert "triage: 2 confirmed" in result.output
        assert TRIAGE_NEXT_STEPS in result.output
        confirmations = _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)
        assert len(confirmations) == 2
        assert all(row["source_span_hashes"] == [SPAN_HASH] for row in confirmations)

    def test_unknown_ref_writes_nothing(self, project: Path, tmp_path: Path) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text(f"{event.event_hash}\n{'f' * 12}\n", encoding="utf-8")
        result = CliRunner().invoke(
            cli,
            ["candidates", "triage", "--accept-refs", str(refs_file), "--path", str(project)],
        )
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "nothing was written" in combined
        assert "no proposed candidate matches" in combined
        assert not _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)

    def test_spanless_candidate_aborts_the_whole_batch(
        self, project: Path, tmp_path: Path
    ) -> None:
        """Validate-all-then-persist: one bad candidate, zero writes."""

        good = _candidate_event(text="Good.", external_id="CLAUDE.md#good")
        spanless = _candidate_event(
            text="Bad.", external_id="CLAUDE.md#bad", span_hashes=()
        )
        _seed_store(project, good, spanless)
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text(
            f"{good.event_hash}\n{spanless.event_hash}\n", encoding="utf-8"
        )
        result = CliRunner().invoke(
            cli,
            ["candidates", "triage", "--accept-refs", str(refs_file), "--path", str(project)],
        )
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "failed envelope validation" in combined
        assert "nothing was written" in combined
        assert not _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)

    def test_rejected_candidate_in_refs_is_refused(
        self, project: Path, tmp_path: Path
    ) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        runner = CliRunner()
        assert (
            runner.invoke(
                cli, ["candidates", "reject", event.event_hash[:12], "--path", str(project)]
            ).exit_code
            == 0
        )
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text(f"{event.event_hash}\n", encoding="utf-8")
        result = runner.invoke(
            cli,
            ["candidates", "triage", "--accept-refs", str(refs_file), "--path", str(project)],
        )
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "already rejected" in combined
        assert "supersede" in combined
        assert not _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)

    def test_already_confirmed_ref_is_a_visible_noop(
        self, project: Path, tmp_path: Path
    ) -> None:
        event = _candidate_event()
        _seed_store(project, event)
        runner = CliRunner()
        assert (
            runner.invoke(
                cli, ["candidates", "confirm", event.event_hash[:12], "--path", str(project)]
            ).exit_code
            == 0
        )
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text(f"{event.event_hash}\n", encoding="utf-8")
        result = runner.invoke(
            cli,
            ["candidates", "triage", "--accept-refs", str(refs_file), "--path", str(project)],
        )
        assert result.exit_code == 0
        assert "1 already confirmed" in result.output
        assert len(_rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)) == 1

    def test_empty_refs_file_is_an_error(self, project: Path, tmp_path: Path) -> None:
        _seed_store(project, _candidate_event())
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text("# only comments\n\n", encoding="utf-8")
        result = CliRunner().invoke(
            cli,
            ["candidates", "triage", "--accept-refs", str(refs_file), "--path", str(project)],
        )
        assert result.exit_code == 1
        assert "contains no event refs" in _combined_output(result)

    def test_lane_and_limit_do_not_apply_to_refs(
        self, project: Path, tmp_path: Path
    ) -> None:
        _seed_store(project, _candidate_event())
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text("deadbeef\n", encoding="utf-8")
        result = CliRunner().invoke(
            cli,
            [
                "candidates",
                "triage",
                "--accept-refs",
                str(refs_file),
                "--lane",
                "structured",
                "--path",
                str(project),
            ],
        )
        assert result.exit_code == 2
        assert "do not apply" in _combined_output(result)


class TestAcceptStructured:
    def test_confirms_structured_lane_only(self, project: Path) -> None:
        structured = _candidate_event(text="Structured rule.", external_id="CLAUDE.md#r")
        provisional = _candidate_event(
            text="Provisional idea.",
            external_id="commit#1",
            lane="provisional",
            source_type="commit-message",
        )
        _seed_store(project, structured, provisional)
        result = CliRunner().invoke(
            cli,
            ["candidates", "triage", "--accept-structured", "--by", "henry", "--path", str(project)],
        )
        assert result.exit_code == 0
        assert "triage: 1 confirmed" in result.output
        assert "(1 still pending)" in result.output
        confirmations = _rows_of_type(project, LedgerEventType.DECISION_CONFIRMED)
        assert len(confirmations) == 1
        payload = json.loads(str(confirmations[0]["payload"]))
        assert payload["candidate_event_hash"] == structured.event_hash
        # The provisional candidate is untouched and still pending.
        assert count_pending_candidates(project) == 1

    def test_provisional_lane_combination_is_refused(self, project: Path) -> None:
        _seed_store(project, _candidate_event())
        result = CliRunner().invoke(
            cli,
            [
                "candidates",
                "triage",
                "--accept-structured",
                "--lane",
                "provisional",
                "--path",
                str(project),
            ],
        )
        assert result.exit_code == 2
        combined = _combined_output(result)
        assert "never applies to the provisional lane" in combined
        assert "human confirmation is required" in combined

    def test_mutually_exclusive_with_accept_refs(
        self, project: Path, tmp_path: Path
    ) -> None:
        refs_file = tmp_path / "refs.txt"
        refs_file.write_text("deadbeef\n", encoding="utf-8")
        result = CliRunner().invoke(
            cli,
            [
                "candidates",
                "triage",
                "--accept-structured",
                "--accept-refs",
                str(refs_file),
                "--path",
                str(project),
            ],
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in _combined_output(result)

    def test_flag_help_names_the_policy_rule(self) -> None:
        result = CliRunner().invoke(cli, ["candidates", "triage", "--help"])
        assert result.exit_code == 0
        assert "human-authored normative artifacts" in result.output
        assert "provisional lane never" in result.output


class TestNoAutoConfirm:
    def test_no_auto_confirm_affordance_exists_on_triage(self) -> None:
        """Auto-confirm of any kind remains impossible (issue #514, AC 1)."""

        for param in triage_command.params:
            name = (param.name or "").lower()
            assert "auto" not in name
            assert name != "yes"
            assert "yes_to_all" not in name
            for opt in getattr(param, "opts", ()):
                assert "auto" not in opt.lower()
                assert "yes" not in opt.lower()

    def test_group_wide_guard_still_holds(self) -> None:
        for command in candidates_group.commands.values():
            for param in command.params:
                name = (param.name or "").lower()
                assert "auto" not in name
                assert name != "yes"


class TestHelpers:
    def test_order_for_triage_sorts_structured_first_and_respects_limit(self) -> None:
        def _row(event_hash: str, lane: str) -> CandidateRow:
            return CandidateRow(
                event_hash=event_hash,
                tenant_id=TENANT_ID,
                source_id=SOURCE_ID,
                idempotency_key=f"key-{event_hash}",
                external_id=None,
                span_hashes=(SPAN_HASH,),
                payload={"lane_assignment": {"lane": lane}},
            )

        rows = (
            _row("a" * 64, "provisional"),
            _row("b" * 64, "structured"),
            _row("c" * 64, "weird-lane"),
            _row("d" * 64, "structured"),
        )
        ordered = order_for_triage(rows, {})
        assert [row.event_hash[0] for row in ordered] == ["b", "d", "a", "c"]
        # Decided candidates drop out; limit caps after ordering.
        ordered = order_for_triage(rows, {"b" * 64: "confirmed"}, limit=2)
        assert [row.event_hash[0] for row in ordered] == ["d", "a"]

    def test_parse_accept_refs_strips_comments_and_blanks(self) -> None:
        refs = parse_accept_refs("# header\nabc123def456\n\n  feed00d00d  # tail\n")
        assert refs == ("abc123def456", "feed00d00d")

    def test_parse_accept_refs_rejects_effectively_empty_files(self) -> None:
        with pytest.raises(CandidateCommandError, match="no event refs"):
            parse_accept_refs("# nothing\n\n")

    def test_count_pending_candidates_without_store_is_none(self, project: Path) -> None:
        assert count_pending_candidates(project) is None

    def test_count_pending_candidates_counts_only_undecided(self, project: Path) -> None:
        first = _candidate_event(text="First.", external_id="CLAUDE.md#1")
        second = _candidate_event(text="Second.", external_id="CLAUDE.md#2")
        _seed_store(project, first, second)
        assert count_pending_candidates(project) == 2
        runner = CliRunner()
        assert (
            runner.invoke(
                cli, ["candidates", "confirm", first.event_hash[:12], "--path", str(project)]
            ).exit_code
            == 0
        )
        assert count_pending_candidates(project) == 1

    def test_load_candidate_rows_roundtrip_still_holds_for_triage_writes(
        self, project: Path
    ) -> None:
        """Invariant: a triage confirm flips the status map, append-only."""

        event = _candidate_event()
        _seed_store(project, event)
        assert (
            CliRunner()
            .invoke(cli, ["candidates", "triage", "--path", str(project)], input="y\n")
            .exit_code
            == 0
        )
        candidates, statuses = load_candidate_rows(_export(project))
        assert len(candidates) == 1
        assert statuses[event.event_hash] == "confirmed"
