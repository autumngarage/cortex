"""`cortex retrieve` — BM25 / semantic / hybrid search over the derived index."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import click

from cortex.commands._auto_sync import (
    auto_sync_disabled_from_context,
    maybe_auto_sync_stale_inputs,
)
from cortex.compat import warn_if_incompatible
from cortex.usage import UsageCounter, increment_usage

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
        "Search algorithm. `bm25` is keyword-based and always available. "
        "`hybrid` combines BM25 with semantic (vector) search for better recall — "
        "requires embeddings to be built. `semantic` uses vector search only. "
        "Defaults to `hybrid` once embeddings exist, `bm25` otherwise."
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
    help="Emit stable JSON: [{path, score, frontmatter, excerpt}]. Suitable for scripting.",
)
@click.option(
    "--for-agent",
    "for_agent",
    is_flag=True,
    default=False,
    help=(
        "Emit citation-first JSON for agents: path, line range, metadata, "
        "top blockquote summary, capped excerpt, and next-step hint."
    ),
)
@click.option(
    "--excerpt-chars",
    type=click.IntRange(min=80),
    default=600,
    show_default=True,
    help="Maximum excerpt characters per result in --for-agent output.",
)
@click.option(
    "--build-embeddings",
    is_flag=True,
    default=False,
    help=(
        "Explicitly build/backfill semantic embeddings before semantic or "
        "hybrid retrieval. Without this, missing embeddings fall back to BM25."
    ),
)
@click.option(
    "--no-rebuild",
    is_flag=True,
    default=False,
    help="Skip the index staleness check and rebuild before querying. Faster, but may miss recent entries.",
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
    for_agent: bool,
    excerpt_chars: int,
    build_embeddings: bool,
    no_rebuild: bool,
    target_path: Path,
) -> None:
    """Search `.cortex/` entries by keyword, semantic similarity, or both.

    Run ``cortex refresh-index --retrieve`` first to build the search index.
    Use ``--mode bm25`` for keyword-only search (no setup required beyond the
    index), ``--mode hybrid`` for best recall (requires embedding provider).
    """

    project_root = Path(target_path).resolve()
    # Stale-input auto-update (cortex#261) scoped to THIS command's --path.
    # Two artifacts must not be conflated: this hook owns the generated state
    # layers (state.md / .index.json), while `--no-rebuild` below governs the
    # *retrieve* sqlite index. We pass `rebuild_retrieve_index=not no_rebuild`
    # so a stale-state refresh never force-rebuilds the retrieve index when the
    # operator asked for --no-rebuild; retrieve's own staleness handling (the
    # `no_rebuild` branch further down) still applies to the retrieve index.
    # The retrieve stdout is pure JSON under --json/--for-agent, so the sync
    # narrative is routed to stderr in those modes.
    maybe_auto_sync_stale_inputs(
        project_root,
        "retrieve",
        disabled=auto_sync_disabled_from_context(),
        json_mode=as_json or for_agent,
        rebuild_retrieve_index=not no_rebuild,
    )
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    warn_if_incompatible(cortex_dir)
    if build_embeddings and no_rebuild:
        raise click.UsageError("--build-embeddings cannot be combined with --no-rebuild.")
    if build_embeddings and mode == "bm25":
        raise click.UsageError(
            "--build-embeddings applies to semantic/hybrid retrieval; "
            "use `cortex refresh-index --retrieve --semantic` to build without querying."
        )

    try:
        from cortex.retrieve.index import (
            FTS5UnavailableError,
            backfill_embeddings,
            count_embedding_index_gaps,
            ensure_fts5_available,
            get_indexed_chunk_count,
            has_populated_embeddings,
            is_stale,
            rebuild_index,
            retrieve_index_exists,
            retrieve_index_path,
        )
        from cortex.retrieve.query import (
            hit_to_agent_json,
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
                _emit_hits([], as_json=as_json, for_agent=for_agent)
                return
            if is_stale(project_root):
                click.echo(
                    "warning: retrieve index may be stale; --no-rebuild skipped refresh",
                    err=True,
                )
        else:
            if is_stale(project_root):
                rebuild_index(project_root)

        # Bail early when the corpus has no indexed content.
        total_chunks = get_indexed_chunk_count(project_root)
        if total_chunks == 0:
            if as_json or for_agent:
                click.echo(json.dumps([]))
            else:
                click.echo(
                    "no results — the .cortex/ corpus has no journal, doctrine, or plan\n"
                    "entries indexed yet. Add content and run `cortex refresh-index` to enable retrieval."
                )
            return

        # Step 2: decide effective mode + (lazily) backfill embeddings.
        effective_mode, embed_callable = _resolve_mode(
            project_root,
            requested=mode,
            no_rebuild=no_rebuild,
            build_embeddings=build_embeddings,
            has_embeddings_now=has_populated_embeddings(project_root),
            backfill_embeddings_fn=backfill_embeddings,
            embedding_gap_count_fn=count_embedding_index_gaps,
            indexed_chunks=total_chunks,
            index_path=retrieve_index_path(project_root),
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
        counted_mode = mode or effective_mode
        increment_usage(project_root, _usage_counter_for_mode(counted_mode))

    except FTS5UnavailableError:
        click.echo(FTS5_FALLBACK_MESSAGE, err=True)
        _run_grep_fallback(
            project_root,
            query,
            as_json=as_json,
            for_agent=for_agent,
            excerpt_chars=excerpt_chars,
        )
        return
    except Exception as exc:
        click.echo(f"error: cortex retrieve failed: {exc}", err=True)
        sys.exit(1)

    _emit_hits(
        hits,
        as_json=as_json,
        for_agent=for_agent,
        hit_to_json=hit_to_json,
        hit_to_agent_json=hit_to_agent_json,
        project_root=project_root,
        excerpt_chars=excerpt_chars,
        query=query,
        total_count=total_chunks,
    )


def _usage_counter_for_mode(mode: str) -> UsageCounter:
    if mode == "bm25":
        return "retrieve_bm25"
    if mode == "semantic":
        return "retrieve_semantic"
    if mode == "hybrid":
        return "retrieve_hybrid"
    raise ValueError(f"unknown retrieve mode: {mode}")


def _emit_hits(  # type: ignore[no-untyped-def]
    hits,
    *,
    as_json: bool,
    for_agent: bool,
    hit_to_json=None,
    hit_to_agent_json=None,
    project_root: Path | None = None,
    excerpt_chars: int = 600,
    query: str | None = None,
    total_count: int | None = None,
) -> None:
    if for_agent:
        if hit_to_agent_json is None or project_root is None:
            click.echo(json.dumps([]))
        else:
            click.echo(
                json.dumps(
                    [
                        hit_to_agent_json(
                            hit,
                            project_root=project_root,
                            excerpt_chars=excerpt_chars,
                        )
                        for hit in hits
                    ]
                )
            )
        return
    if as_json:
        if hit_to_json is None:
            click.echo(json.dumps([]))
        else:
            click.echo(json.dumps([hit_to_json(hit) for hit in hits]))
        return
    if not hits:
        if query is not None and total_count is not None:
            click.echo(f'no matches for "{query}" — corpus has {total_count} indexed entries')
        else:
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
    build_embeddings: bool,
    has_embeddings_now: bool,
    backfill_embeddings_fn,
    embedding_gap_count_fn,
    indexed_chunks: int,
    index_path: Path,
):
    """Pick the effective mode + an embedder when needed.

    Returns ``(effective_mode, embed_callable_or_None)``.

    Cost rule:
        * Existing embeddings allow semantic/hybrid lookup.
        * Missing embeddings never trigger model load/backfill unless
          ``build_embeddings`` is true.
        * BM25 remains the default low-cost lookup path.
    """

    from cortex.retrieve.embeddings import (
        EMBED_MODEL_NAME,
        EmbeddingUnavailableError,
        probe_embedder,
    )

    explicit = requested is not None
    desired = requested or ("hybrid" if has_embeddings_now or build_embeddings else "bm25")

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

    if not has_embeddings_now and not build_embeddings:
        if explicit:
            click.echo(
                "warning: semantic/hybrid retrieval requires built embeddings; "
                "falling back to --mode bm25. Run `cortex refresh-index "
                "--retrieve --semantic` or retry with `--build-embeddings` "
                "to opt into semantic backfill.",
                err=True,
            )
        return "bm25", None

    # Probe before doing anything heavy. probe_embedder caches in-process.
    probe = probe_embedder(project_root)
    if not probe.available:
        _stderr_fallback_notice(probe.error, explicit=explicit)
        return "bm25", None

    if has_embeddings_now and not build_embeddings:
        gap_count = embedding_gap_count_fn(project_root)
        if gap_count is None:
            click.echo(
                "warning: could not verify semantic embeddings completeness; "
                "falling back to --mode bm25. Run `cortex refresh-index "
                "--retrieve --semantic` to rebuild semantic embeddings.",
                err=True,
            )
            return "bm25", None
        if gap_count > 0:
            click.echo(
                f"warning: semantic embeddings have {gap_count} index gap(s) "
                "(missing chunk embeddings or orphan vector rows); falling back "
                "to --mode bm25. Run `cortex refresh-index --retrieve "
                "--semantic` or retry with `--build-embeddings` to backfill.",
                err=True,
            )
            return "bm25", None

    # Backfill only when the caller explicitly opted in. This also fills
    # chunks added after a prior semantic build.
    if build_embeddings:
        try:
            result = backfill_embeddings_fn(project_root)
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
        _emit_embedding_meter(
            indexed_chunks=indexed_chunks,
            embedded_chunks=getattr(result, "embedded_chunks", 0),
            model_name=EMBED_MODEL_NAME,
            index_path=index_path,
            cache_dir=probe.cache_dir,
        )
        if not explicit:
            _maybe_emit_hybrid_default_notice(project_root)

    return desired, None


def _emit_embedding_meter(
    *,
    indexed_chunks: int,
    embedded_chunks: int,
    model_name: str,
    index_path: Path,
    cache_dir: Path | None,
) -> None:
    """Surface semantic-index work without contaminating JSON stdout."""

    cache = str(cache_dir) if cache_dir is not None else "unavailable"
    click.echo(
        "cortex retrieve: semantic embeddings ready "
        f"(indexed_chunks={indexed_chunks}, embedded_chunks={embedded_chunks}, "
        f"model={model_name}, index={index_path}, cache={cache})",
        err=True,
    )


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


def _run_grep_fallback(
    project_root: Path,
    query: str,
    *,
    as_json: bool,
    for_agent: bool,
    excerpt_chars: int,
) -> None:
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
    if for_agent:
        excerpt = result.stdout.strip()
        hits = []
        if excerpt:
            capped = excerpt[:excerpt_chars].rstrip()
            omitted = len(excerpt) > excerpt_chars
            hits.append(
                {
                    "path": "cortex grep fallback",
                    "citation": "cortex grep fallback",
                    "line_range": {"start": None, "end": None},
                    "score": 0.0,
                    "layer": None,
                    "type": None,
                    "status": None,
                    "frontmatter": None,
                    "summary": None,
                    "excerpt": capped,
                    "excerpt_omitted": omitted,
                    "omission": f"excerpt truncated to {excerpt_chars} characters" if omitted else None,
                    "excerpt_limit_chars": excerpt_chars,
                    "next_step": "Use `cortex grep` directly, then open the returned file path.",
                }
            )
        click.echo(json.dumps(hits))
        if result.stderr:
            click.echo(result.stderr, err=True, nl=False)
        return
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
