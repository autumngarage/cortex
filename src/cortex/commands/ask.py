"""`cortex ask <question>` — the cited "what did we decide about X?" surface.

Stage 0 issue #381 (foundation: the shipped ``cortex.hosted.ask_ledger``,
which this command consumes and never rebuilds) plus the #382 query guard.

Stage 0 reality: SQL is non-executing locally — the hosted DB is live on
Railway but this CLI must not require it. The command therefore has exactly
two visible modes, never a silent middle:

- ``DATABASE_URL`` set **and** the hosted extra installed (``psycopg``
  importable): run the real ``ask_ledger`` hybrid retrieval SQL against the
  hosted Postgres — the first live read path — then compose and render the
  cited answer locally via ``cortex.hosted.ask_surface``.
- otherwise: degrade VISIBLY with "hosted ledger not configured; set
  DATABASE_URL" (degradation taxonomy: ``degraded_capability``) and a
  non-zero exit. No local fallback pretends to be the ledger.

Identity defaults mirror ``cortex derive``: ``--tenant-id`` / ``--source-id``
default to the deterministic UUIDv5 pair derived from the resolved project
root, so ``cortex ask`` reads the same tenant/source identity that
``cortex derive`` writes.

No-browsable-index guardrail (cortex#382, sibling cortex#441): the question
argument is required by the CLI grammar, and
``require_query_scoped_question`` refuses empty or browse-shaped questions
before any retrieval. Citations render with permalinks always.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID

import click

from cortex.commands.confirm import CandidateCommandError, count_pending_candidates
from cortex.commands.derive import default_source_id, default_tenant_id
from cortex.hosted.ask_ledger import (
    AnswerState,
    AskLedgerQuery,
    AskLedgerValidationError,
    CitedContextPack,
    ask_ledger_retrieval_sql,
    build_ask_ledger_context_pack,
)
from cortex.hosted.ask_surface import (
    BrowseIndexRefusedError,
    compose_answer,
    render_answer,
    require_query_scoped_question,
)
from cortex.hosted.degradation import remediation_for
from cortex.hosted.derive_store import DeriveStoreError

# Refusal messages carry exactly one actionable next command (cortex#516);
# the hints come from the one module-level table in
# cortex.hosted.degradation.REMEDIATION_BY_REASON, never ad-hoc strings.
HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE = (
    f"hosted ledger not configured; {remediation_for('database_url_missing')} "
    "to run the cited ask surface (degradation: degraded_capability — the "
    "boundary that holds: no local store is silently substituted for the ledger)"
)
HOSTED_EXTRA_MISSING_MESSAGE = (
    "DATABASE_URL is set but the hosted extra is not installed (`psycopg` is "
    f"not importable); {remediation_for('hosted_driver_missing')} to run the "
    "live ask surface (degradation: degraded_capability)"
)


class HostedAskError(RuntimeError):
    """Raised when the live hosted read path fails in a nameable way."""


def snapshot_missing_message(tenant_id: str) -> str:
    """Refusal text for a tenant with no registered graph snapshot.

    Carries the one actionable next command from the shared remediation
    table (cortex#516) so the correct refusal is a next step, not a dead end.
    """

    return (
        f"no graph snapshot registered for tenant {tenant_id}; "
        "the hosted graph projection has not been built yet, so a "
        "cited answer cannot name its snapshot boundary; "
        f"remediation: {remediation_for('snapshot_missing')}"
    )


def latest_graph_snapshot_sql(schema: str = "cortex_hosted") -> str:
    """Return SQL fetching the newest registered snapshot hash for a tenant."""

    # The schema identifier is validated by ask_ledger_retrieval_sql's caller
    # path as well; revalidate locally so this statement is safe standalone.
    if not schema.replace("_", "").isalnum() or schema[0].isdigit():
        raise HostedAskError(f"invalid SQL identifier: {schema!r}")
    return (
        f"SELECT graph_snapshot_hash FROM {schema}.graph_snapshots "
        "WHERE tenant_id = %(tenant_id)s ORDER BY created_at DESC LIMIT 1"
    )


def hosted_extra_installed() -> bool:
    """True when the optional Postgres driver for the hosted read path exists."""

    return importlib.util.find_spec("psycopg") is not None


def _connect(dsn: str) -> Any:
    """Open a psycopg connection with visible errors.

    Seam note: a dedicated ``cortex.hosted.db`` connection layer may land on
    a sibling branch; this command deliberately does NOT import it (and
    duplicates nothing from it — this is a minimal connect with the same
    visible-error discipline). When that module merges, this function is the
    single seam to replace with the shared connector.
    """

    import psycopg  # type: ignore[import-not-found] # lazy: the hosted extra is optional

    try:
        return psycopg.connect(dsn)
    except psycopg.Error as exc:
        raise HostedAskError(f"cannot connect to the hosted ledger: {exc}") from exc


def run_hosted_ask(
    *,
    dsn: str,
    query: AskLedgerQuery,
    schema: str = "cortex_hosted",
) -> CitedContextPack:
    """Execute the live ask-ledger read path and build the cited pack."""

    import psycopg  # lazy: the hosted extra is optional (ignore lives on first import above)

    with closing(_connect(dsn)) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    latest_graph_snapshot_sql(schema),
                    {"tenant_id": query.tenant_id},
                )
                snapshot_row = cursor.fetchone()
                if snapshot_row is None:
                    raise HostedAskError(snapshot_missing_message(query.tenant_id))
                graph_snapshot_hash = str(snapshot_row[0])
                cursor.execute(ask_ledger_retrieval_sql(schema), query.as_sql_parameters())
                column_names = [description[0] for description in cursor.description or ()]
                rows: list[Mapping[str, object]] = [
                    dict(zip(column_names, row, strict=True)) for row in cursor.fetchall()
                ]
        except psycopg.Error as exc:
            raise HostedAskError(f"hosted ask query failed: {exc}") from exc
    return build_ask_ledger_context_pack(
        query=query,
        graph_snapshot_hash=graph_snapshot_hash,
        rows=rows,
    )


def _validated_uuid_option(value: str | None, *, option_name: str) -> str | None:
    if value is None:
        return None
    try:
        UUID(value)
    except ValueError as exc:
        raise click.BadParameter(f"{value!r} is not a UUID", param_hint=option_name) from exc
    return value


def no_cited_support_remediation(project_root: Path) -> str:
    """Next-step line for the honest no-answer (cortex#516).

    The pending-candidate count comes from the local derive store when one
    exists ("N candidates await review"); when the store is absent the hint
    renders without a count, and a store read failure is reported inline —
    the count is enrichment, its failure is never silent and never blocks
    the refusal itself.
    """

    hint = remediation_for("no_cited_support")
    try:
        pending = count_pending_candidates(project_root)
    except (CandidateCommandError, DeriveStoreError) as exc:
        return f"remediation: {hint} (pending-candidate count unavailable: {exc})"
    if pending:
        return f"remediation: {pending} candidate(s) await review — {hint}"
    return f"remediation: {hint}"


@click.command("ask", context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("question")
@click.option(
    "--tenant-id",
    default=None,
    help=(
        "Tenant UUID to read. Default: the same deterministic UUIDv5 of the "
        "resolved project root that `cortex derive` writes with."
    ),
)
@click.option(
    "--source-id",
    default=None,
    help=(
        "Visible source UUID to authorize. Default: the same deterministic "
        "UUIDv5 of the resolved project root that `cortex derive` writes with."
    ),
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=10,
    show_default=True,
    help="Maximum cited candidates in the answer (the pack stays capped).",
)
@click.option(
    "--path",
    "project_root",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root used to derive the default tenant/source identity.",
)
def ask_command(
    *,
    question: str,
    tenant_id: str | None,
    source_id: str | None,
    limit: int,
    project_root: Path,
) -> None:
    """Answer "what did we decide about X?" with cited ledger decisions.

    Answers derive only from cited candidate material (decision text plus
    citations with permalinks); when nothing cited qualifies, the honest
    no-answer renders verbatim with its omitted counts. The surface never
    lists the ledger: a question is required, and empty or browse-shaped
    questions are refused (cortex#382; see also cortex#441).
    """

    try:
        scoped_question = require_query_scoped_question(question)
    except BrowseIndexRefusedError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        click.echo(f"cortex ask: {HOSTED_LEDGER_NOT_CONFIGURED_MESSAGE}", err=True)
        sys.exit(2)
    if not hosted_extra_installed():
        click.echo(f"cortex ask: {HOSTED_EXTRA_MISSING_MESSAGE}", err=True)
        sys.exit(2)

    root = Path(project_root).resolve()
    tenant = _validated_uuid_option(tenant_id, option_name="--tenant-id") or default_tenant_id(root)
    source = _validated_uuid_option(source_id, option_name="--source-id") or default_source_id(root)

    try:
        query = AskLedgerQuery(
            tenant_id=tenant,
            query=scoped_question,
            visible_source_ids=(source,),
            limit=limit,
        )
        pack = run_hosted_ask(dsn=dsn, query=query)
    except (AskLedgerValidationError, HostedAskError) as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    answer = compose_answer(pack)
    click.echo(render_answer(answer))
    if (
        answer.answer_state is AnswerState.NO_ANSWER
        and answer.no_answer_reason == "no_cited_support"
    ):
        # The refusal is correct; the remediation makes it a next step
        # instead of a dead end (cortex#516).
        click.echo(no_cited_support_remediation(root))
