"""Degraded-mode remediation hints on the CLI refusal surfaces (cortex#516).

Every user-facing refusal in `cortex ask` / `cortex derive` /
`cortex candidates` carries exactly one actionable next command, drawn from
the single module-level table in ``cortex.hosted.degradation``
(``REMEDIATION_BY_REASON``). These tests exercise each surface's degraded
mode and assert the hint arrives — including the live pending-candidate
count on the ``no_cited_support`` no-answer when the local derive store can
cheaply provide it, and the visible (never silent) fallback when it cannot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands import ask as ask_module
from cortex.commands.ask import (
    HOSTED_EXTRA_MISSING_MESSAGE,
    HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE,
    no_cited_support_remediation,
    snapshot_missing_message,
)
from cortex.hosted.ask_ledger import AskLedgerQuery, build_cited_context_pack
from cortex.hosted.degradation import remediation_for
from cortex.hosted.derive_store import (
    DeriveEventStore,
    DeriveStoreError,
    derive_store_path,
)
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
SPAN_HASH = "e" * 64
SHA256_PROBE = "a" * 64
QUESTION = "what did we decide about retries?"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".cortex").mkdir(parents=True)
    return root


def _combined_output(result: object) -> str:
    output = getattr(result, "output", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return output + stderr


def _candidate_event(*, text: str, external_id: str) -> LedgerEvent:
    payload = {
        "decision_text": text,
        "lane_assignment": {
            "lane": "structured",
            "source_type": "agent-instructions",
            "advisory_only": False,
            "backfilled": False,
        },
        "source_type": "agent-instructions",
        "spans": [{"span_hash": SPAN_HASH}],
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
        source_span_hashes=(SPAN_HASH,),
        payload=payload,
    )


def _seed_pending_candidates(root: Path, count: int) -> None:
    events = [
        _candidate_event(text=f"Decision {index}.", external_id=f"CLAUDE.md#s{index}")
        for index in range(count)
    ]
    with DeriveEventStore(derive_store_path(root)) as store:
        store.append_events(events)


def _no_answer_pack() -> object:
    return build_cited_context_pack(
        query_hash=SHA256_PROBE,
        retrieval_config_version="ask-ledger-retrieval/v1",
        graph_snapshot_hash=SHA256_PROBE,
        candidates=(),
        limit=5,
    )


class TestAskRefusals:
    def test_missing_database_url_names_env_var_and_compass_doc(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = CliRunner().invoke(cli, ["ask", QUESTION, "--path", str(project)])
        assert result.exit_code == 2
        combined = _combined_output(result)
        assert remediation_for("database_url_missing") in combined
        assert "DATABASE_URL" in combined
        assert "docs/hosted-ledger.md" in combined
        # The constant itself is built from the shared table.
        assert remediation_for("database_url_missing") in (
            HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE
        )

    def test_missing_driver_carries_the_install_hint(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://example.test/cortex")
        monkeypatch.setattr(ask_module, "hosted_extra_installed", lambda: False)
        result = CliRunner().invoke(cli, ["ask", QUESTION, "--path", str(project)])
        assert result.exit_code == 2
        combined = _combined_output(result)
        assert remediation_for("hosted_driver_missing") in combined
        assert "cortex[hosted]" in combined
        assert remediation_for("hosted_driver_missing") in HOSTED_EXTRA_MISSING_MESSAGE

    def test_snapshot_missing_message_names_cortex_push(self) -> None:
        message = snapshot_missing_message(TENANT_ID)
        assert "no graph snapshot registered" in message
        assert remediation_for("snapshot_missing") in message
        assert "cortex push" in message

    def test_snapshot_missing_refusal_reaches_the_user(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://example.test/cortex")
        monkeypatch.setattr(ask_module, "hosted_extra_installed", lambda: True)

        def _refuse(**kwargs: object) -> object:
            query = kwargs["query"]
            assert isinstance(query, AskLedgerQuery)
            raise ask_module.HostedAskError(snapshot_missing_message(query.tenant_id))

        monkeypatch.setattr(ask_module, "run_hosted_ask", _refuse)
        result = CliRunner().invoke(cli, ["ask", QUESTION, "--path", str(project)])
        assert result.exit_code == 1
        combined = _combined_output(result)
        assert "remediation:" in combined
        assert "cortex push" in combined


class TestNoCitedSupportHint:
    def test_no_answer_renders_triage_hint_with_live_pending_count(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_pending_candidates(project, 2)
        monkeypatch.setenv("DATABASE_URL", "postgresql://example.test/cortex")
        monkeypatch.setattr(ask_module, "hosted_extra_installed", lambda: True)
        monkeypatch.setattr(ask_module, "run_hosted_ask", lambda **_: _no_answer_pack())
        result = CliRunner().invoke(cli, ["ask", QUESTION, "--path", str(project)])
        assert result.exit_code == 0
        assert "No cited decision found" in result.output
        assert "remediation: 2 candidate(s) await review" in result.output
        assert remediation_for("no_cited_support") in result.output

    def test_no_answer_without_local_store_renders_hint_without_count(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "postgresql://example.test/cortex")
        monkeypatch.setattr(ask_module, "hosted_extra_installed", lambda: True)
        monkeypatch.setattr(ask_module, "run_hosted_ask", lambda **_: _no_answer_pack())
        result = CliRunner().invoke(cli, ["ask", QUESTION, "--path", str(project)])
        assert result.exit_code == 0
        assert f"remediation: {remediation_for('no_cited_support')}" in result.output
        assert "await review" not in result.output

    def test_count_failure_is_reported_inline_never_silent(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(_: Path) -> int:
            raise DeriveStoreError("store unreadable probe")

        monkeypatch.setattr(ask_module, "count_pending_candidates", _boom)
        line = no_cited_support_remediation(project)
        assert remediation_for("no_cited_support") in line
        assert "pending-candidate count unavailable" in line
        assert "store unreadable probe" in line

    def test_zero_pending_candidates_renders_hint_without_count(
        self, project: Path
    ) -> None:
        _seed_pending_candidates(project, 0)
        line = no_cited_support_remediation(project)
        assert line == f"remediation: {remediation_for('no_cited_support')}"


class TestDeriveAndCandidatesRefusals:
    def test_derive_outside_cortex_project_names_init(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(cli, ["derive", "--path", str(tmp_path)])
        assert result.exit_code == 2
        combined = _combined_output(result)
        assert remediation_for("cortex_dir_missing") in combined

    def test_derive_with_no_default_sources_names_source_flag(
        self, project: Path
    ) -> None:
        result = CliRunner().invoke(cli, ["derive", "--path", str(project)])
        assert result.exit_code == 0
        assert remediation_for("derive_no_sources") in result.output
        assert "--source" in result.output

    def test_candidates_without_store_names_derive(self, project: Path) -> None:
        result = CliRunner().invoke(cli, ["candidates", "list", "--path", str(project)])
        assert result.exit_code == 1
        assert remediation_for("derive_store_missing") in _combined_output(result)
