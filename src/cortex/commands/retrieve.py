"""`cortex retrieve` — BM25 / semantic / hybrid search over the derived index."""

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

_NOTICE_FILE_REL = Path(".cortex") / ".index" / ".notices"
_HYBRID_DEFAULT_NOTICE_KEY = "hybrid-default-flip"


@click.command("retrieve")
@click.argument("query")
@click.option(
    "--mode",
    type=click.Choice(("hybrid", "semantic", "bm25")),
    default=None,
    help=(
        "Retrieval mode. Default flips between bm25 (no embeddings yet) and "
        "hybrid (BM25+semantic RRF) once embeddings are built."
    ),
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
    help="Skip the staleness check, the chunks-index rebuild, AND the embeddings backfill.",
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
    mode: str | None,
    top_k: int,
    as_json: bool,
    no_rebuild: bool,
    target_path: Path,
) -> None:
    """Search `.cortex/` using the derived BM25 / semantic / hybrid index."""

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
            backfill_embeddings,
            ensure_fts5_available,
            has_populated_embeddings,
            is_stale,
            rebuild_index,
            retrieve_index_exists,
        )
        from cortex.retrieve.query import (
            hit_to_json,
            query_bm25,
            query_hybrid,
            query_semantic,
        )

        ensure_fts5_available()
        # Step 1: keep the chunks index fresh (S1 path).
        if no_rebuild:
            if not retrieve_index_exists(project_root):
                click.echo(
                    "warning: retrieve index does not exist; --no-rebuild skipped build",
                    err=True,
                )
                _emit_hits([], as_json=as_json)
                return
            if is_stale(project_root):
                click.echo(
                    "warning: retrieve index may be stale; --no-rebuild skipped refresh",
                    err=True,
                )
        else:
            if is_stale(project_root):
                rebuild_index(project_root)

        # Step 2: decide effective mode + (lazily) backfill embeddings.
        effective_mode, embed_callable = _resolve_mode(
            project_root,
            requested=mode,
            no_rebuild=no_rebuild,
            has_embeddings_now=has_populated_embeddings(project_root),
            backfill_embeddings_fn=backfill_embeddings,
        )

        # Step 3: run the chosen retrieval path.
        if effective_mode == "bm25":
            hits = query_bm25(project_root, query, top_k=top_k)
        elif effective_mode == "semantic":
            hits = query_semantic(
                project_root, query, top_k=top_k, embed_callable=embed_callable
            )
        elif effective_mode == "hybrid":
            hits = query_hybrid(
                project_root, query, top_k=top_k, embed_callable=embed_callable
            )
        else:  # pragma: no cover - defensive
            raise RuntimeError(f"unhandled retrieve mode: {effective_mode}")

    except FTS5UnavailableError:
        click.echo(FTS5_FALLBACK_MESSAGE, err=True)
        _run_grep_fallback(project_root, query, as_json=as_json)
        return
    except Exception as exc:
        click.echo(f"error: cortex retrieve failed: {exc}", err=True)
        sys.exit(1)

    _emit_hits(hits, as_json=as_json, hit_to_json=hit_to_json)


def _emit_hits(hits, *, as_json: bool, hit_to_json=None) -> None:  # type: ignore[no-untyped-def]
    if as_json:
        if hit_to_json is None:
            click.echo(json.dumps([]))
        else:
            click.echo(json.dumps([hit_to_json(hit) for hit in hits]))
        return
    if not hits:
        click.echo("no results found")
        return
    for hit in hits:
        click.echo(f"{hit.path}  score={hit.score:.4f}")
        click.echo(hit.excerpt)
        click.echo()


def _resolve_mode(  # type: ignore[no-untyped-def]
    project_root: Path,
    *,
    requested: str | None,
    no_rebuild: bool,
    has_embeddings_now: bool,
    backfill_embeddings_fn,
):
    """Pick the effective mode + an embedder when needed.

    Returns ``(effective_mode, embed_callable_or_None)``.

    Default-mode flip rule (per brief):
        * If no embeddings table is populated (and ``--no-rebuild``), default
          stays at ``bm25``. Explicit ``--mode semantic|hybrid`` triggers a
          fallback notice + bm25.
        * If no embeddings table is populated and ``--no-rebuild`` is FALSE,
          we attempt the embedder probe + backfill. Success → flips default
          to ``hybrid`` and emits the one-time notice. Failure → bm25 with
          a clear stderr line.
        * If embeddings table is populated, default is ``hybrid``. Explicit
          ``--mode bm25`` always honored.
    """

    from cortex.retrieve.embeddings import EmbeddingUnavailableError, probe_embedder

    explicit = requested is not None
    desired = requested or ("hybrid" if has_embeddings_now else "bm25")

    # If user explicitly chose bm25, no embedder work to do.
    if desired == "bm25":
        return "bm25", None

    # --no-rebuild path: never run the backfill.
    if no_rebuild:
        if has_embeddings_now:
            probe = probe_embedder(project_root)
            if probe.available:
                return desired, None
            _stderr_fallback_notice(probe.error, explicit=explicit)
            return "bm25", None
        # Embeddings not built and we can't build — fall back loudly.
        if explicit:
            click.echo(
                "warning: no embeddings table yet and --no-rebuild prevents the "
                "backfill; falling back to --mode bm25.",
                err=True,
            )
        return "bm25", None

    # Probe before doing anything heavy. probe_embedder caches in-process.
    probe = probe_embedder(project_root)
    if not probe.available:
        _stderr_fallback_notice(probe.error, explicit=explicit)
        return "bm25", None

    # Backfill if the embeddings table isn't populated yet.
    if not has_embeddings_now:
        try:
            backfill_embeddings_fn(project_root)
        except (
            EmbeddingUnavailableError,
            RuntimeError,
            ValueError,
        ) as exc:
            click.echo(
                f"warning: embeddings backfill failed ({exc}); falling back to --mode bm25.",
                err=True,
            )
            return "bm25", None
        # Default-mode flip notice — only when the user didn't ask explicitly
        # and we just backfilled (i.e. behaviour is changing under them).
        if not explicit:
            _maybe_emit_hybrid_default_notice(project_root)

    return desired, None


def _stderr_fallback_notice(err, *, explicit: bool) -> None:  # type: ignore[no-untyped-def]
    """Print the bare-repo / runtime-missing fallback line to stderr."""

    if err is None:
        return
    leader = "warning"
    if explicit:
        leader = "error" if False else "warning"  # always warn — never crash
    message = (
        f"{leader}: {err.user_message()}; falling back to BM25-only. "
        "Cross-platform note: aarch64 Linux lacks onnxruntime PyPI wheels."
    )
    click.echo(message, err=True)


def _notice_path(project_root: Path) -> Path:
    return project_root / _NOTICE_FILE_REL


def _maybe_emit_hybrid_default_notice(project_root: Path) -> None:
    """Emit the one-time notice that default just flipped from bm25 → hybrid."""

    path = _notice_path(project_root)
    seen: set[str] = set()
    if path.exists():
        try:
            seen = {line.strip() for line in path.read_text().splitlines() if line.strip()}
        except OSError:
            seen = set()
    if _HYBRID_DEFAULT_NOTICE_KEY in seen:
        return
    click.echo(
        "cortex retrieve: embeddings ready — default mode flipped to `hybrid` "
        "(reciprocal-rank fusion of BM25 + semantic). Override with "
        "`--mode bm25` or `--mode semantic` per call.",
        err=True,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        seen.add(_HYBRID_DEFAULT_NOTICE_KEY)
        path.write_text("\n".join(sorted(seen)) + "\n")
    except OSError:
        # If we can't write the notice marker, surface it once more next
        # time — that's acceptable; it's "no silent failures" in reverse.
        pass


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
    if result.returncode != 0:
        if result.stdout:
            click.echo(result.stdout, nl=False)
        if result.stderr:
            click.echo(result.stderr, err=True, nl=False)
        sys.exit(result.returncode)
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
