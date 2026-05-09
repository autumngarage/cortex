"""`cortex refresh-index` — rebuild `.cortex/.index.json`."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cortex.compat import require_compatible
from cortex.config import load_refresh_index_config
from cortex.index import refresh_index


@click.command("refresh-index")
@click.option(
    "--retrieve",
    "include_retrieve",
    is_flag=True,
    default=False,
    help="Also rebuild the full-text BM25 search index used by `cortex retrieve`.",
)
@click.option(
    "--semantic",
    "include_semantic",
    is_flag=True,
    default=False,
    help="With --retrieve, explicitly build/backfill semantic embeddings.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def refresh_index_command(
    *,
    include_retrieve: bool,
    include_semantic: bool,
    target_path: Path,
) -> None:
    """Rebuild `.cortex/.index.json` — the promotion queue and entry index.

    Required before ``cortex promote``. Pass ``--retrieve`` to also rebuild
    the BM25 search index used by ``cortex retrieve``. Add ``--semantic`` to
    explicitly build/backfill embeddings.
    """

    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)
    if include_semantic and not include_retrieve:
        raise click.UsageError("--semantic requires --retrieve.")

    require_compatible(cortex_dir)
    config = load_refresh_index_config(project_root)
    result = refresh_index(project_root, config)
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)
    if include_retrieve:
        try:
            from cortex.retrieve.index import (
                backfill_embeddings,
                get_indexed_chunk_count,
                rebuild_index,
            )

            retrieve_result = rebuild_index(project_root)
            click.echo(
                f"retrieve index: {retrieve_result.path} "
                f"(indexed_chunks={retrieve_result.indexed_chunks})"
            )
            if include_semantic:
                from cortex.retrieve.embeddings import EMBED_MODEL_NAME, probe_embedder

                probe = probe_embedder(project_root)
                if not probe.available or probe.error is not None:
                    message = (
                        probe.error.user_message()
                        if probe.error is not None
                        else "semantic retrieval unavailable"
                    )
                    click.echo(f"error: {message}", err=True)
                    sys.exit(1)
                semantic_result = backfill_embeddings(project_root)
                cache = str(probe.cache_dir) if probe.cache_dir is not None else "unavailable"
                click.echo(
                    "semantic index: "
                    f"indexed_chunks={get_indexed_chunk_count(project_root)}, "
                    f"embedded_chunks={semantic_result.embedded_chunks}, "
                    f"model={EMBED_MODEL_NAME}, "
                    f"index={retrieve_result.path}, "
                    f"cache={cache}"
                )
        except Exception as exc:
            click.echo(f"error: could not rebuild retrieve index: {exc}", err=True)
            sys.exit(1)
    click.echo(str(result.path))
