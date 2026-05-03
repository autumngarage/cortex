"""BM25 + semantic + hybrid query helpers for the retrieve index.

S2 layers two new query paths on top of S1's BM25:

* ``query_semantic`` — pure cosine similarity over the sqlite-vec
  ``embeddings`` table.
* ``query_hybrid`` — reciprocal-rank fusion (RRF) of BM25 + semantic
  results.

Both new paths require the embeddings backfill to have run; callers should
gate on ``has_populated_embeddings`` and fall back to BM25 when not ready.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex.retrieve.index import (
    open_index_with_vec,
    retrieve_index_path,
    serialize_vector,
)

# RRF default. k=60 is the value Cormack/Clarke/Buettcher 2009 originally
# proposed and what every major hybrid-retrieval system has standardised on
# since (Microsoft Azure AI Search, Elastic, Vespa). Smaller k weights the
# top of each ranked list more heavily; larger k flattens. We expose it as
# a parameter for tests but never default-tune it without the dogfood
# evidence to back the change.
RRF_K = 60


@dataclass(frozen=True)
class RetrieveHit:
    """A ranked retrieve result."""

    path: str
    score: float
    frontmatter: dict[str, Any] | None
    excerpt: str


def query_bm25(project_root: Path, query: str, *, top_k: int) -> list[RetrieveHit]:
    """Return top BM25 hits from the FTS5 index."""

    conn = sqlite3.connect(retrieve_index_path(project_root))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
              chunks.path,
              chunks.start_line,
              chunks.content,
              chunks.frontmatter_json,
              bm25(chunks_fts) AS rank
            FROM chunks_fts
            JOIN chunks ON chunks_fts.rowid = chunks.id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (_fts5_match_query(query), max(top_k * 20, top_k)),
        ).fetchall()
    finally:
        conn.close()

    hits: list[RetrieveHit] = []
    for row in rows:
        frontmatter = None
        if row["frontmatter_json"]:
            loaded = json.loads(row["frontmatter_json"])
            if isinstance(loaded, dict):
                frontmatter = loaded
        score = _score(row["path"], row["content"], query, float(row["rank"]))
        hits.append(
            RetrieveHit(
                path=f"{row['path']}:{row['start_line']}",
                score=score,
                frontmatter=frontmatter,
                excerpt=_excerpt(row["content"], query),
            )
        )
    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:top_k]


def query_semantic(
    project_root: Path,
    query: str,
    *,
    top_k: int,
    embed_callable: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[RetrieveHit]:
    """Return top semantic hits via cosine similarity.

    ``embed_callable`` accepts ``list[str]`` and returns ``list[list[float]]``;
    when omitted the default fastembed embedder is loaded. Tests inject a
    deterministic stub.
    """

    if embed_callable is None:
        from cortex.retrieve.embeddings import Embedder

        embedder = Embedder.shared(project_root)
        embed_callable = embedder.embed

    vectors = embed_callable([query])
    if not vectors:
        return []
    query_vec = vectors[0]

    conn = open_index_with_vec(retrieve_index_path(project_root))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
              chunks.id,
              chunks.path,
              chunks.start_line,
              chunks.content,
              chunks.frontmatter_json,
              embeddings.distance AS distance
            FROM embeddings
            JOIN chunks ON chunks.id = embeddings.rowid
            WHERE embeddings.embedding MATCH ?
              AND k = ?
            ORDER BY distance
            """,
            (serialize_vector(query_vec), max(top_k * 3, top_k)),
        ).fetchall()
    finally:
        conn.close()

    hits: list[RetrieveHit] = []
    for row in rows:
        frontmatter = None
        if row["frontmatter_json"]:
            loaded = json.loads(row["frontmatter_json"])
            if isinstance(loaded, dict):
                frontmatter = loaded
        # sqlite-vec returns L2 distance for float vectors. Convert to a
        # similarity score in (0,1] so larger = better and the public JSON
        # contract stays "higher score wins".
        distance = float(row["distance"])
        score = 1.0 / (1.0 + distance)
        hits.append(
            RetrieveHit(
                path=f"{row['path']}:{row['start_line']}",
                score=score,
                frontmatter=frontmatter,
                excerpt=_excerpt(row["content"], query),
            )
        )
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:top_k]


def query_hybrid(
    project_root: Path,
    query: str,
    *,
    top_k: int,
    embed_callable: Callable[[list[str]], list[list[float]]] | None = None,
    rrf_k: int = RRF_K,
) -> list[RetrieveHit]:
    """Return reciprocal-rank-fused BM25 + semantic results.

    Each ranker contributes ``1 / (rrf_k + rank)`` to the candidate's fused
    score (rank is 1-indexed). Pull more from each ranker than ``top_k``
    so the fusion has room to swap candidates around.
    """

    fan_out = max(top_k * 3, top_k)
    bm25_hits = query_bm25(project_root, query, top_k=fan_out)
    sem_hits = query_semantic(
        project_root, query, top_k=fan_out, embed_callable=embed_callable
    )
    return rrf_fuse(bm25_hits, sem_hits, top_k=top_k, rrf_k=rrf_k)


def rrf_fuse(
    bm25_hits: list[RetrieveHit],
    semantic_hits: list[RetrieveHit],
    *,
    top_k: int,
    rrf_k: int = RRF_K,
) -> list[RetrieveHit]:
    """Reciprocal-rank fusion over two ranked hit lists.

    ``score(d) = sum_over_rankers ( 1 / (rrf_k + rank_in_ranker(d)) )``
    where ``rank_in_ranker`` is 1-indexed and a doc absent from a ranker
    contributes 0 from that ranker.

    Documents are deduplicated by ``RetrieveHit.path``; the prose excerpt
    and frontmatter come from whichever ranker found the doc first
    (preferring BM25 for its line/lexical specificity, then semantic).
    """

    fused_scores: dict[str, float] = {}
    canonical: dict[str, RetrieveHit] = {}

    def _accumulate(hits: list[RetrieveHit]) -> None:
        for rank, hit in enumerate(hits, start=1):
            contribution = 1.0 / (rrf_k + rank)
            fused_scores[hit.path] = fused_scores.get(hit.path, 0.0) + contribution
            canonical.setdefault(hit.path, hit)

    _accumulate(bm25_hits)
    _accumulate(semantic_hits)

    fused: list[RetrieveHit] = []
    for path, score in sorted(
        fused_scores.items(), key=lambda kv: kv[1], reverse=True
    )[:top_k]:
        base = canonical[path]
        fused.append(
            RetrieveHit(
                path=base.path,
                score=score,
                frontmatter=base.frontmatter,
                excerpt=base.excerpt,
            )
        )
    return fused


def hit_to_json(hit: RetrieveHit) -> dict[str, Any]:
    """Return the public JSON contract for a hit."""

    return {
        "path": hit.path,
        "score": hit.score,
        "frontmatter": hit.frontmatter,
        "excerpt": hit.excerpt,
    }


def _excerpt(content: str, query: str) -> str:
    terms = [term.lower() for term in query.split() if term.strip()]
    lines = content.splitlines()
    if not lines:
        return ""
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(term in lowered for term in terms):
            start = max(0, idx - 1)
            end = min(len(lines), idx + 2)
            return "\n".join(lines[start:end]).strip()
    return "\n".join(lines[:3]).strip()


def _score(path: str, content: str, query: str, rank: float) -> float:
    """Combine FTS5 BM25 with source-path relevance.

    The `.cortex/` path is part of the retrievable text contract: queries like
    "doctrine" or a Doctrine number should surface entries whose layer/path
    matches even when the term is common in plan citations.
    """

    score = -rank
    terms = [term.lower() for term in query.split() if term.strip()]
    path_lower = path.lower()
    content_lower = content.lower()
    for term in terms:
        if term in path_lower:
            score += 10.0
        if path_lower.startswith(f"{term}/"):
            score += 5.0
        if term in content_lower:
            score += 1.0
    return score


def _fts5_match_query(query: str) -> str:
    """Return a literal-term FTS5 MATCH expression.

    Raw Cortex queries often contain punctuation (`v0.3.0`, dates, paths).
    Quoting each whitespace-delimited term preserves FTS5 tokenization without
    letting punctuation be parsed as query syntax.
    """

    terms = [term for term in query.split() if term.strip()]
    if not terms:
        return '""'
    quoted: list[str] = []
    for term in terms:
        escaped = term.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)
