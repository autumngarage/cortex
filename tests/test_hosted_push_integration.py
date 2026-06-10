"""Live-Postgres integration for `cortex push` (cortex#513).

Runs only when ``DATABASE_URL`` points at a real Postgres provisioned with
the pgcrypto, pg_trgm, and vector extensions (the Railway compass Postgres,
or a local pgvector-enabled image) and the ``hosted`` extra is installed::

    DATABASE_URL='postgresql://user:pass@host:5432/db?sslmode=require' \\
        uv run --extra hosted pytest tests/test_hosted_push_integration.py -q

One test, reproducing the PE-0 chain end to end under a fresh per-run
tenant/source identity: derive fixture -> push -> push again (idempotent
no-op) -> confirm -> push -> ask answers with citations. Rows created here
are tagged with per-run UUIDs; ``ledger_events`` is append-only by design,
so they are left in place (the same convention as
``tests/test_hosted_db_integration.py``).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.confirm import load_candidate_rows
from cortex.hosted.db import connect
from cortex.hosted.derive_store import DeriveEventStore, derive_store_path
from cortex.hosted.migrations import apply_schema

DATABASE_URL = os.environ.get("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "set DATABASE_URL to a Postgres with pgcrypto/pg_trgm/vector "
        "(e.g. the Railway compass Postgres) to run the push integration test"
    ),
)

RULES_MARKDOWN = (
    "# Working agreements\n"
    "\n"
    "- Always use exponential backoff for HTTP retries; never retry more "
    "than five times.\n"
)


def _combined_output(result: object) -> str:
    output = getattr(result, "output", "") or ""
    stderr = getattr(result, "stderr", "") or ""
    return output + stderr


def test_pe0_chain_derive_push_confirm_push_ask(tmp_path: Path) -> None:
    """The PE-0 loop the issue specs: a local file becomes a live cited answer."""

    connection = connect(DATABASE_URL)
    try:
        apply_schema(connection)
    finally:
        connection.close()

    tenant_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    root = tmp_path / "repo"
    (root / ".cortex").mkdir(parents=True)
    (root / "CLAUDE.md").write_text(RULES_MARKDOWN, encoding="utf-8")
    runner = CliRunner()

    derive = runner.invoke(
        cli,
        [
            "derive",
            "--path",
            str(root),
            "--tenant-id",
            tenant_id,
            "--source-id",
            source_id,
        ],
    )
    assert derive.exit_code == 0, _combined_output(derive)

    first_push = runner.invoke(cli, ["push", "--path", str(root)])
    assert first_push.exit_code == 0, _combined_output(first_push)
    assert "0 failed" in first_push.output
    assert "0 skipped" in first_push.output
    assert "candidate node(s)" in first_push.output
    assert "snapshot: " in first_push.output
    assert "projection.rebuilt appended" in first_push.output

    # Acceptance: running push twice is a no-op second time, reported as
    # replays — never errors.
    second_push = runner.invoke(cli, ["push", "--path", str(root)])
    assert second_push.exit_code == 0, _combined_output(second_push)
    assert "0 appended" in second_push.output
    assert "0 failed" in second_push.output
    assert "already registered" in second_push.output
    assert "projection.rebuilt replayed" in second_push.output

    with DeriveEventStore(derive_store_path(root)) as store:
        candidates, _ = load_candidate_rows(store.export_events())
    assert candidates, "the fixture must have derived at least one candidate"
    for candidate in candidates:
        confirm = runner.invoke(
            cli,
            ["candidates", "confirm", candidate.event_hash[:12], "--path", str(root)],
        )
        assert confirm.exit_code == 0, _combined_output(confirm)

    third_push = runner.invoke(cli, ["push", "--path", str(root)])
    assert third_push.exit_code == 0, _combined_output(third_push)
    assert f"{len(candidates)} status transition(s)" in third_push.output
    assert "0 failed" in third_push.output

    # Acceptance: after push, `cortex ask` answers with citations against
    # the same tenant/source identity the derive store carries.
    ask = runner.invoke(
        cli,
        [
            "ask",
            "exponential backoff for HTTP retries?",
            "--path",
            str(root),
            "--tenant-id",
            tenant_id,
            "--source-id",
            source_id,
        ],
    )
    assert ask.exit_code == 0, _combined_output(ask)
    assert "cited decision" in ask.output
    assert "exponential backoff" in ask.output
    assert "CLAUDE.md" in ask.output
