"""BM25 query helpers for the retrieve index."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex.retrieve.index import retrieve_index_path


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
