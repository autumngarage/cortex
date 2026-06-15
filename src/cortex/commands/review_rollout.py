"""Operator CLI for per-repo hosted review rollout (cortex#397)."""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

import click

from cortex.hosted.db import HostedDbError, connect
from cortex.hosted.review_rollout import (
    ReviewRolloutError,
    ReviewRolloutEvent,
    ReviewRolloutStatus,
    ReviewRolloutStore,
    normalize_repo_full_name,
)

STATE_TO_ENABLED = {
    "enabled": True,
    "enable": True,
    "on": True,
    "disabled": False,
    "disable": False,
    "off": False,
}


@click.group(
    "review-rollout",
    context_settings={"help_option_names": ["-h", "--help"]},
)
def review_rollout_group() -> None:
    """Operator-internal per-repo hosted review rollout."""


@review_rollout_group.command("set", context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("repo")
@click.argument("state", type=click.Choice(tuple(STATE_TO_ENABLED)))
@click.option(
    "--actor",
    default=None,
    help="Operator identity. Defaults to GITHUB_ACTOR or USER when available.",
)
@click.option(
    "--reason",
    required=True,
    help="Why this rollout state is being recorded.",
)
@click.option(
    "--idempotency-key",
    default=None,
    help="Optional retry key for this exact config event.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON.",
)
def review_rollout_set_command(
    *,
    repo: str,
    state: str,
    actor: str | None,
    reason: str,
    idempotency_key: str | None,
    as_json: bool,
) -> None:
    """Append an enable/disable event for REPO."""

    dsn = _database_url_or_exit()
    operator = _operator_actor(actor)
    try:
        event = ReviewRolloutEvent(
            repo_full_name=repo,
            enabled=STATE_TO_ENABLED[state],
            actor=operator,
            reason=reason,
            occurred_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
        )
        connection = connect(dsn)
        try:
            store = ReviewRolloutStore(connection)
            event_id = store.record(event)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    except (HostedDbError, ReviewRolloutError) as exc:
        _die(str(exc))
    except _psycopg_error() as exc:
        _die(f"review rollout write failed: {exc}")

    payload = {
        "repo_full_name": event.repo_full_name,
        "enabled": event.enabled,
        "inserted": event_id is not None,
        "event_id": event_id,
        "idempotency_key": event.stable_idempotency_key,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    state_label = "enabled" if event.enabled else "disabled"
    if event_id is None:
        click.echo(
            f"review rollout: {event.repo_full_name} already recorded as "
            f"{state_label} for idempotency key {event.stable_idempotency_key}"
        )
    else:
        click.echo(f"review rollout: {event.repo_full_name} {state_label} ({event_id})")


@review_rollout_group.command("status", context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("repo")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON.",
)
def review_rollout_status_command(*, repo: str, as_json: bool) -> None:
    """Show the derived rollout status for REPO."""

    dsn = _database_url_or_exit()
    try:
        normalized = normalize_repo_full_name(repo)
        connection = connect(dsn)
        try:
            status = ReviewRolloutStore(connection).status_for(normalized)
        finally:
            connection.close()
    except (HostedDbError, ReviewRolloutError) as exc:
        _die(str(exc))
    except _psycopg_error() as exc:
        _die(f"review rollout status query failed: {exc}")

    if as_json:
        click.echo(json.dumps(status.as_payload(), sort_keys=True))
        return
    click.echo(render_review_rollout_status(status))


def render_review_rollout_status(status: ReviewRolloutStatus) -> str:
    state = "enabled" if status.enabled else "disabled"
    if not status.configured:
        return f"review rollout: {status.repo_full_name} disabled (no rollout event; default off)"
    when = status.occurred_at.isoformat() if status.occurred_at is not None else "unknown time"
    return (
        f"review rollout: {status.repo_full_name} {state} "
        f"by {status.actor} at {when} - {status.reason}"
    )


def _database_url_or_exit() -> str:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        _die(
            "DATABASE_URL is not set; review-rollout reads/writes the hosted "
            "Postgres rollout event stream"
        )
    return dsn


def _operator_actor(raw: str | None) -> str:
    actor = (
        raw
        or os.environ.get("GITHUB_ACTOR")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or ""
    ).strip()
    if not actor:
        raise click.BadParameter(
            "--actor is required when GITHUB_ACTOR/USER/LOGNAME are unset",
            param_hint="--actor",
        )
    return actor


def _psycopg_error() -> type[Exception]:
    import psycopg

    return psycopg.Error


def _die(message: str) -> None:
    click.echo(f"error: {message}", err=True)
    sys.exit(1)
