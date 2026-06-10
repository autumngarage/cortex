"""`cortex push` — local derive store to hosted ledger, projection, and snapshot.

Stage 0 issue #513, replacing the three hand-written PE-0 scripts with one
idempotent verb. The pipeline itself lives in ``cortex.hosted.push``
(:func:`cortex.hosted.push.run_push`); this command owns the visible-modes
boundary, mirroring ``cortex ask``:

- ``DATABASE_URL`` (or ``--database-url``) set **and** the hosted extra
  installed: open the policy-conformant connection via ``cortex.hosted.db``
  and run the push against the live hosted Postgres.
- otherwise: degrade VISIBLY (degradation taxonomy: ``degraded_capability``)
  with a non-zero exit. No local store pretends to be the hosted ledger.

Identity defaults mirror ``cortex derive``: the events in the local store
already carry the deterministic tenant/source UUIDv5 pair, and the document
rebuild uses the same ``repo-file`` / ``cortex-derive`` provenance constants
derive wrote with, so a pushed candidate is byte-identical to what a hosted
deriver would have produced.

Per-stage arithmetic is printed for every run: events appended / replayed /
skipped / failed, documents and spans upserted, nodes and transitions
projected, and the registered snapshot hash. Skips and failures each print
one line naming the idempotency key and reason — counted, never silent. A
run with failed events exits non-zero after completing every stage.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from cortex.commands.derive import DERIVE_AUTHOR_REF, DERIVE_DOCUMENT_TYPE
from cortex.hosted.db import HostedDbError, connect
from cortex.hosted.derive_store import (
    DeriveEventStore,
    DeriveStoreError,
    derive_store_path,
)
from cortex.hosted.push import HostedPushError, PushOutcome, run_push

HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE = (
    "hosted ledger not configured; set DATABASE_URL (or pass --database-url) "
    "to the hosted Postgres to push the local derive store (degradation: "
    "degraded_capability — the boundary that holds: nothing local is "
    "silently substituted for the hosted ledger)"
)


@click.command("push", context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--database-url",
    default=None,
    help=(
        "Hosted Postgres URL (Railway-style; ?sslmode=require is honored). "
        "Default: the DATABASE_URL environment variable."
    ),
)
@click.option(
    "--path",
    "project_root",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/` and the local derive store.",
)
def push_command(*, database_url: str | None, project_root: Path) -> None:
    """Push the local derive store to the hosted ledger, projection, and snapshot.

    One idempotent verb (issue #513): reconstructs the exported ledger
    events, ensures the tenant/source identity rows, rebuilds file-backed
    source documents and spans (content drift skips the candidate visibly,
    naming the path), executes one graph-write plan per event, then
    recomputes the graph snapshot over the live rows and registers it with a
    projection.rebuilt event. Running push twice is a no-op second time —
    replays are counted, never errors.
    """

    dsn = (database_url or os.environ.get("DATABASE_URL", "")).strip()
    if not dsn:
        click.echo(f"cortex push: {HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE}", err=True)
        sys.exit(2)

    root = Path(project_root).resolve()
    db_path = derive_store_path(root)
    if not db_path.exists():
        click.echo(
            f"error: no derive store found at {db_path}; run `cortex derive` first",
            err=True,
        )
        sys.exit(1)
    try:
        with DeriveEventStore(db_path) as store:
            rows = store.export_events()
    except DeriveStoreError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    if not rows:
        click.echo(
            f"push: no events in the local derive store at {db_path}; "
            "run `cortex derive` first"
        )
        return

    try:
        connection = connect(dsn)
    except HostedDbError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    try:
        outcome = run_push(
            connection,
            rows,
            project_root=root,
            document_type=DERIVE_DOCUMENT_TYPE,
            author_ref=DERIVE_AUTHOR_REF,
        )
    except (HostedPushError, HostedDbError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    finally:
        connection.close()

    _print_arithmetic(outcome, db_path)
    if outcome.failed:
        sys.exit(1)


def _print_arithmetic(outcome: PushOutcome, db_path: Path) -> None:
    """Per-stage arithmetic — every count visible, every skip/failure named."""

    click.echo(f"push: {outcome.total_events} event(s) from {db_path}")
    click.echo(
        f"events: {outcome.appended} appended, {outcome.replayed} replayed, "
        f"{len(outcome.skipped)} skipped, {len(outcome.failed)} failed"
    )
    click.echo(
        f"provenance: {outcome.documents_upserted} document(s), "
        f"{outcome.spans_upserted} span(s) upserted"
    )
    click.echo(
        f"projections: {outcome.candidates_projected} candidate node(s), "
        f"{outcome.transitions_projected} status transition(s)"
    )
    for skip in outcome.skipped:
        click.echo(f"skipped: {skip.idempotency_key[:12]} {skip.reason}")
    for failure in outcome.failed:
        click.echo(f"failed: {failure.idempotency_key[:12]} {failure.reason}", err=True)
    snapshot = outcome.snapshot
    if snapshot is None:
        click.echo("snapshot: not recomputed (no events could be reconstructed)")
        return
    state = "registered" if snapshot.registered else "already registered"
    event_state = "appended" if snapshot.event_appended else "replayed"
    click.echo(
        f"snapshot: {snapshot.snapshot_hash} {state} "
        f"({snapshot.nodes} node(s), {snapshot.versions} version(s), "
        f"{snapshot.edges} edge(s), {snapshot.scopes} scope(s)); "
        f"projection.rebuilt {event_state}"
    )
