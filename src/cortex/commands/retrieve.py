"""`cortex retrieve` — BM25 search over the derived FTS5 index."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from cortex.compat import warn_if_incompatible

FTS5_FALLBACK_MESSAGE = (
    "FTS5 extension not available in this sqlite3 build; cortex retrieve "
    "requires FTS5. Falling back to ripgrep for this query. Install a "
    "sqlite3 build with FTS5 or use 'cortex grep' directly."
)


@click.command("retrieve")
@click.argument("query")
@click.option(
    "--mode",
    type=click.Choice(("bm25",)),
    default="bm25",
    show_default=True,
    help="Retrieval mode. S1 supports only bm25.",
)
@click.option(
    "--top-k",
    type=click.IntRange(min=1),
    default=10,
    show_default=True,
    help="Maximum number of results to return.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the stable JSON contract: [{path, score, frontmatter, excerpt}].",
)
@click.option(
    "--no-rebuild",
    is_flag=True,
    default=False,
    help="Skip the staleness check and query the current index.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def retrieve_command(
    *,
    query: str,
    mode: str,
    top_k: int,
    as_json: bool,
    no_rebuild: bool,
    target_path: Path,
) -> None:
    """Search `.cortex/` using the derived BM25 index."""

    _ = mode
    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    warn_if_incompatible(cortex_dir)

    try:
        from cortex.retrieve.index import (
            FTS5UnavailableError,
            is_stale,
            rebuild_index,
            retrieve_index_exists,
        )
        from cortex.retrieve.query import hit_to_json, query_bm25

        if no_rebuild:
            if not retrieve_index_exists(project_root):
                click.echo(
                    "warning: retrieve index does not exist; --no-rebuild skipped build",
                    err=True,
                )
                hits = []
            elif is_stale(project_root):
                click.echo(
                    "warning: retrieve index may be stale; --no-rebuild skipped refresh",
                    err=True,
                )
                hits = query_bm25(project_root, query, top_k=top_k)
            else:
                hits = query_bm25(project_root, query, top_k=top_k)
        else:
            if is_stale(project_root):
                rebuild_index(project_root)
            hits = query_bm25(project_root, query, top_k=top_k)
    except FTS5UnavailableError:
        click.echo(FTS5_FALLBACK_MESSAGE, err=True)
        _run_grep_fallback(project_root, query, as_json=as_json)
        return
    except Exception as exc:
        click.echo(f"error: cortex retrieve failed: {exc}", err=True)
        sys.exit(1)

    if as_json:
        click.echo(json.dumps([hit_to_json(hit) for hit in hits]))
        return

    if not hits:
        click.echo("no results found")
        return
    for hit in hits:
        click.echo(f"{hit.path}  score={hit.score:.4f}")
        click.echo(hit.excerpt)
        click.echo()


def _run_grep_fallback(project_root: Path, query: str, *, as_json: bool) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cortex.cli",
            "grep",
            query,
            "--path",
            str(project_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if as_json:
        excerpt = result.stdout.strip()
        hits = []
        if excerpt:
            hits.append(
                {
                    "path": "cortex grep fallback",
                    "score": 0.0,
                    "frontmatter": None,
                    "excerpt": excerpt,
                }
            )
        click.echo(json.dumps(hits))
        if result.stderr:
            click.echo(result.stderr, err=True, nl=False)
        return
    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, err=True, nl=False)
