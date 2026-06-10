"""simlab demo tenant + demo script acceptance (cortex#522).

The demo-script test always runs (the documented walkthrough is a committed
artifact with required sections). The hosted-tenant tests run only when
``DATABASE_URL`` points at a real Postgres provisioned with the hosted
schema extensions — the established integration pattern::

    DATABASE_URL='postgresql://user:pass@host:5432/db?sslmode=require' \\
        uv run --extra hosted pytest tests/simlab/test_simlab_demo.py -q

Rows created here are tagged with the fixed simlab demo namespace UUIDs (and
a dedicated isolation-twin tenant); ``ledger_events`` is append-only by
design, so they are left in place — exactly why reseeding must be (and is
asserted to be) an idempotent replay.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid5

import pytest

from tests.simlab.seed_demo import (
    DEMO_QUESTION,
    DEMO_SCENARIO_ID,
    SIMLAB_DEMO_NAMESPACE,
    demo_source_id,
    demo_tenant_id,
    seed_demo,
)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_SCRIPT_PATH = REPO_ROOT / "docs" / "demo-script.md"

# The sections the 5-minute walkthrough must carry (#522 acceptance: ask →
# catch → citation trail, plus seeding and reset).
REQUIRED_DEMO_SCRIPT_SECTIONS = (
    "## Prerequisites",
    "## Seed the demo tenant",
    "## Minute 1-2 — ask",
    "## Minute 3-4 — the catch",
    "## Minute 5 — the citation trail",
    "## Reset / reseed",
)

RESEED_BUDGET_SECONDS = 120.0

requires_database = pytest.mark.skipif(
    not DATABASE_URL,
    reason=(
        "set DATABASE_URL to a Postgres with pgcrypto/pg_trgm/vector "
        "(e.g. the Railway compass Postgres) to run the simlab demo-tenant tests"
    ),
)


# ---------------------------------------------------------------------------
# The committed 5-minute demo script (always asserted)
# ---------------------------------------------------------------------------


def test_demo_script_exists_with_required_sections() -> None:
    assert DEMO_SCRIPT_PATH.is_file(), f"missing {DEMO_SCRIPT_PATH}"
    text = DEMO_SCRIPT_PATH.read_text(encoding="utf-8")
    for section in REQUIRED_DEMO_SCRIPT_SECTIONS:
        assert section in text, f"demo script is missing section {section!r}"
    # The script drives the same artifacts this suite verifies.
    assert "tests.simlab.seed_demo" in text
    assert DEMO_SCENARIO_ID in text
    assert "cortex ask" in text
    assert "cortex review" in text


def test_demo_identity_is_fixed_and_not_path_derived() -> None:
    """The demo tenant is the same UUID everywhere, by namespace derivation."""

    assert demo_tenant_id() == str(uuid5(SIMLAB_DEMO_NAMESPACE, "tenant:simlab-demo"))
    assert demo_source_id("clean-shop") == str(
        uuid5(SIMLAB_DEMO_NAMESPACE, "source:clean-shop")
    )
    # Stability pin: changing these silently would strand the hosted rows.
    assert demo_tenant_id() == "20002900-d2a5-54a4-8612-03a1a600f191"


# ---------------------------------------------------------------------------
# Hosted demo tenant (DATABASE_URL-gated)
# ---------------------------------------------------------------------------


@requires_database
def test_seed_demo_verifies_both_demo_moments_and_reseeds_idempotently(
    tmp_path: Path,
) -> None:
    first = seed_demo(DATABASE_URL, work_dir=tmp_path / "first", live_review=False)
    assert first.push_failed == 0
    assert first.ask_cited, first.ask_output
    assert "exponential backoff" in first.ask_output
    assert first.review_caught, first.review_output
    assert first.review_mode == "retrieval-only"

    # Reseed: byte-deterministic derive + content-keyed confirm keys mean the
    # hosted ledger reports replays, never duplicates — and it stays fast.
    second = seed_demo(DATABASE_URL, work_dir=tmp_path / "second", live_review=False)
    assert second.push_failed == 0
    assert second.reseed_was_replay, (
        f"reseed appended {second.push_appended} new event(s); expected a pure replay"
    )
    assert second.ask_cited, second.ask_output
    assert second.review_caught, second.review_output
    assert second.elapsed_seconds < RESEED_BUDGET_SECONDS

    assert second.tenant_id == first.tenant_id == demo_tenant_id()
    assert DEMO_QUESTION  # the question is a committed constant, not ad hoc


@requires_database
def test_demo_tenant_visibility_is_isolated_from_other_tenants(
    tmp_path: Path,
) -> None:
    """#522 acceptance: the demo tenant's visibility scope cannot see other
    tenants' sources, and other tenants cannot see the demo tenant's.

    Uses a dedicated isolation-twin tenant seeded through the same machinery
    (fixed UUIDs, so reruns replay) and the production ask read path.
    """

    from cortex.commands.ask import run_hosted_ask
    from cortex.hosted.ask_ledger import AskLedgerQuery

    demo = seed_demo(DATABASE_URL, work_dir=tmp_path / "demo", live_review=False)
    other_tenant = str(uuid5(SIMLAB_DEMO_NAMESPACE, "tenant:simlab-isolation-twin"))
    other_source = str(uuid5(SIMLAB_DEMO_NAMESPACE, "source:simlab-isolation-twin"))
    other = seed_demo(
        DATABASE_URL,
        work_dir=tmp_path / "other",
        tenant_id=other_tenant,
        source_id=other_source,
        live_review=False,
    )
    assert other.ask_cited, other.ask_output

    def candidates(tenant_id: str, visible_source_id: str) -> int:
        pack = run_hosted_ask(
            dsn=DATABASE_URL,
            query=AskLedgerQuery(
                tenant_id=tenant_id,
                query=DEMO_QUESTION,
                visible_source_ids=(visible_source_id,),
                limit=10,
            ),
        )
        return len(pack.candidates)

    # Sanity: each tenant sees its own source.
    assert candidates(demo.tenant_id, demo.source_id) > 0
    assert candidates(other.tenant_id, other.source_id) > 0
    # The boundary: naming a foreign source id yields nothing — in both
    # directions. No cross-tenant row is readable, even by explicit id.
    assert candidates(demo.tenant_id, other.source_id) == 0
    assert candidates(other.tenant_id, demo.source_id) == 0
