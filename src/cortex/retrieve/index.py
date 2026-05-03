"""SQLite FTS5 index build and invalidation for `cortex retrieve`.

S2 extends the schema with a sqlite-vec ``embeddings`` virtual table, kept
in sync with ``chunks`` row IDs. Schema migration is on-open: if a v1 index
is opened with v2 code and no ``embeddings`` table exists, we leave the FTS
side untouched and (on demand) backfill embeddings from current chunk
content. Older clients can keep using v1 BM25 paths.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import struct
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cortex import __version__
from cortex.frontmatter import parse_frontmatter
from cortex.retrieve.cache import index_path, temp_index_path
from cortex.retrieve.chunker import chunk_markdown

# Schema lineage:
#   v1 — chunks + chunks_fts + meta (S1, v0.7.0).
#   v2 — adds embeddings vec0 virtual table + embedding_model + embedding_dim
#        meta keys (S2, v0.8.0). v1 indexes still load read-only via BM25.
SCHEMA_VERSION = 2
INDEXED_DIRS = frozenset(("doctrine", "journal", "plans", "digests"))
INDEXED_TOP_LEVEL_FILES = frozenset(("map.md", "state.md"))


class FTS5UnavailableError(RuntimeError):
    """Raised when Python's sqlite3 build does not provide FTS5."""


class SqliteVecUnavailableError(RuntimeError):
    """Raised when sqlite-vec extension cannot be loaded into a connection."""


@dataclass(frozen=True)
class SourceFile:
    """A markdown file selected for indexing."""

    path: Path
    rel_path: str
    mtime: float
    size: int


@dataclass(frozen=True)
class RebuildResult:
    """Result of a retrieve-index rebuild."""

    path: Path
    indexed_files: int
    indexed_chunks: int
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]


@dataclass(frozen=True)
class EmbeddingBackfillResult:
    """Result of an embeddings-table backfill."""

    embedded_chunks: int
    skipped_chunks: int


def ensure_fts5_available() -> None:
    """Detect FTS5 support explicitly."""

    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE fts5_probe USING fts5(content)")
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise FTS5UnavailableError("FTS5 extension not available") from exc
        raise


def retrieve_index_path(project_root: Path) -> Path:
    return index_path(project_root)


def retrieve_index_exists(project_root: Path) -> bool:
    return retrieve_index_path(project_root).exists()


def rebuild_index(project_root: Path) -> RebuildResult:
    """Incrementally rebuild the retrieve index and atomically publish it."""

    ensure_fts5_available()
    project_root = project_root.resolve()
    cortex_dir = project_root / ".cortex"
    target = retrieve_index_path(project_root)
    tmp = temp_index_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with _rebuild_lock(target):
        if tmp.exists():
            tmp.unlink()
        if target.exists():
            shutil.copy2(target, tmp)

        try:
            conn = sqlite3.connect(tmp)
            try:
                _ensure_schema(conn)
                sources = _discover_sources(cortex_dir)
                existing = _existing_file_fingerprints(conn)
                source_by_rel = {source.rel_path: source for source in sources}

                changed = [
                    source
                    for source in sources
                    if existing.get(source.rel_path) != (source.mtime, source.size)
                ]
                deleted = sorted(set(existing) - set(source_by_rel))

                for rel_path in deleted:
                    conn.execute("DELETE FROM chunks WHERE path = ?", (rel_path,))
                for source in changed:
                    _replace_file_chunks(conn, source)
                if changed or deleted:
                    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
                _set_meta(conn, "schema_version", str(SCHEMA_VERSION))
                _set_meta(conn, "cortex_version", __version__)
                _set_meta(conn, "built_at", datetime.now(UTC).astimezone().isoformat(timespec="seconds"))
                conn.commit()
                indexed_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            finally:
                conn.close()
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()

    return RebuildResult(
        path=target,
        indexed_files=len(sources),
        indexed_chunks=int(indexed_chunks),
        changed_paths=tuple(source.rel_path for source in changed),
        deleted_paths=tuple(deleted),
    )


def is_stale(project_root: Path) -> bool:
    """Return True when the index is absent or diverges from working-tree files."""

    target = retrieve_index_path(project_root)
    if not target.exists():
        return True
    conn = sqlite3.connect(target)
    try:
        try:
            existing = _existing_file_fingerprints(conn)
        except sqlite3.DatabaseError:
            return True
    finally:
        conn.close()
    sources = _discover_sources(project_root.resolve() / ".cortex")
    current = {source.rel_path: (source.mtime, source.size) for source in sources}
    return existing != current


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY,
          path TEXT NOT NULL,
          chunk_idx INTEGER NOT NULL,
          start_line INTEGER NOT NULL,
          end_line INTEGER NOT NULL,
          content TEXT NOT NULL,
          frontmatter_json TEXT,
          file_mtime REAL NOT NULL,
          file_size INTEGER NOT NULL,
          content_hash TEXT,
          UNIQUE(path, chunk_idx)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
          content, path,
          content='chunks', content_rowid='id'
        );
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    # v2 migration: add `content_hash` column to v1 chunks tables (used to
    # detect chunks whose embedding can be reused after a chunk re-insert).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)").fetchall()}
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN content_hash TEXT")
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


@contextmanager
def _rebuild_lock(target: Path) -> Iterator[None]:
    lock = target.with_name(f"{target.name}.lock")
    deadline = time.monotonic() + 30
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for retrieve index lock at {lock}") from exc
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        with suppress(FileNotFoundError):
            lock.unlink()


def _discover_sources(cortex_dir: Path) -> list[SourceFile]:
    if not cortex_dir.is_dir():
        return []
    sources: list[SourceFile] = []
    for path in sorted(cortex_dir.rglob("*.md")):
        if not path.is_file() or _is_excluded(path, cortex_dir):
            continue
        stat = path.stat()
        sources.append(
            SourceFile(
                path=path,
                rel_path=path.relative_to(cortex_dir).as_posix(),
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
        )
    return sources


def _is_excluded(path: Path, cortex_dir: Path) -> bool:
    rel_parts = path.relative_to(cortex_dir).parts
    if any(part.startswith(".") for part in rel_parts):
        return True
    if len(rel_parts) == 1:
        return rel_parts[0] not in INDEXED_TOP_LEVEL_FILES
    return rel_parts[0] not in INDEXED_DIRS


def _existing_file_fingerprints(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    rows = conn.execute(
        "SELECT path, MAX(file_mtime), MAX(file_size) FROM chunks GROUP BY path"
    ).fetchall()
    return {str(path): (float(mtime), int(size)) for path, mtime, size in rows}


def _replace_file_chunks(conn: sqlite3.Connection, source: SourceFile) -> None:
    import hashlib

    text = source.path.read_text()
    frontmatter, body = parse_frontmatter(text)
    body_start_line = _body_start_line(text, body)
    frontmatter_json = json.dumps(frontmatter, sort_keys=True) if frontmatter else None
    chunks = chunk_markdown(body)

    conn.execute("DELETE FROM chunks WHERE path = ?", (source.rel_path,))
    for chunk in chunks:
        digest = hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
        conn.execute(
            """
            INSERT INTO chunks (
              path, chunk_idx, start_line, end_line, content,
              frontmatter_json, file_mtime, file_size, content_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.rel_path,
                chunk.chunk_idx,
                body_start_line + chunk.start_line - 1,
                body_start_line + chunk.end_line - 1,
                chunk.content,
                frontmatter_json,
                source.mtime,
                source.size,
                digest,
            ),
        )


def _body_start_line(text: str, body: str) -> int:
    if body == text:
        return 1
    prefix_len = len(text) - len(body)
    if prefix_len < 0:
        return 1
    return text[:prefix_len].count("\n") + 1


# ---------------------------------------------------------------------------
# Embeddings (S2)
# ---------------------------------------------------------------------------


def load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension into ``conn``.

    Raises :class:`SqliteVecUnavailableError` with a clear reason when the
    extension is not importable or the SQLite build refuses extensions
    (the latter happens on some hardened distros).
    """

    try:
        import sqlite_vec  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SqliteVecUnavailableError(
            f"sqlite-vec not importable: {exc}"
        ) from exc

    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except (AttributeError, sqlite3.OperationalError) as exc:
        raise SqliteVecUnavailableError(
            f"sqlite-vec load failed: {exc}"
        ) from exc
    finally:
        with suppress(AttributeError, sqlite3.OperationalError):
            conn.enable_load_extension(False)


def open_index_with_vec(path: Path) -> sqlite3.Connection:
    """Open the chunks index with sqlite-vec loaded."""

    conn = sqlite3.connect(path)
    try:
        load_sqlite_vec(conn)
    except SqliteVecUnavailableError:
        conn.close()
        raise
    return conn


def has_embeddings_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = 'embeddings'"
    ).fetchone()
    return row is not None


def ensure_embeddings_table(
    conn: sqlite3.Connection, *, dimension: int
) -> None:
    """Create the embeddings vec0 virtual table if missing.

    Caller is responsible for having sqlite-vec loaded into ``conn``.
    """

    if has_embeddings_table(conn):
        return
    conn.execute(
        f"CREATE VIRTUAL TABLE embeddings USING vec0(embedding float[{dimension}])"
    )


def serialize_vector(vector: list[float]) -> bytes:
    """Pack a python float list into the little-endian float32 blob sqlite-vec expects."""

    return struct.pack(f"{len(vector)}f", *vector)


def chunks_missing_embeddings(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Return ``(rowid, content)`` for chunks lacking an embedding row."""

    if not has_embeddings_table(conn):
        # All chunks need embedding once the table is created.
        return [
            (int(row[0]), str(row[1]))
            for row in conn.execute("SELECT id, content FROM chunks ORDER BY id").fetchall()
        ]
    rows = conn.execute(
        """
        SELECT chunks.id, chunks.content
        FROM chunks
        LEFT JOIN embeddings ON chunks.id = embeddings.rowid
        WHERE embeddings.rowid IS NULL
        ORDER BY chunks.id
        """
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def backfill_embeddings(
    project_root: Path,
    *,
    embed_callable: Callable[[list[str]], list[list[float]]] | None = None,
    dimension: int | None = None,
    model_name: str | None = None,
) -> EmbeddingBackfillResult:
    """Populate the embeddings table for every chunk that lacks one.

    Parameters
    ----------
    project_root:
        Project containing ``.cortex/.index/chunks.sqlite``.
    embed_callable:
        Function accepting ``list[str]`` and returning ``list[list[float]]``.
        When ``None``, loads the default fastembed embedder. Tests inject a
        deterministic stub here.
    dimension / model_name:
        Override the default ``BAAI/bge-small-en-v1.5`` (384-dim) — used by
        tests with stub embedders to avoid the heavy model load.
    """

    target = retrieve_index_path(project_root)
    if not target.exists():
        return EmbeddingBackfillResult(0, 0)

    if embed_callable is None:
        from cortex.retrieve.embeddings import (
            EMBED_DIMENSION,
            EMBED_MODEL_NAME,
            Embedder,
        )

        embedder = Embedder.shared(project_root)
        embed_callable = embedder.embed
        dimension = dimension or EMBED_DIMENSION
        model_name = model_name or EMBED_MODEL_NAME
    else:
        if dimension is None:
            raise ValueError("dimension is required when embed_callable is provided")
        model_name = model_name or "test-embedder"

    conn = open_index_with_vec(target)
    embedded = 0
    skipped = 0
    try:
        ensure_embeddings_table(conn, dimension=dimension)
        # Existing meta dimension MUST match: refuse to mix dimensions in
        # one index. This catches "switched embedder, forgot to wipe index".
        existing_dim = _get_meta(conn, "embedding_dim")
        if existing_dim and int(existing_dim) != dimension:
            raise ValueError(
                f"embedding dimension mismatch: index built at {existing_dim}, "
                f"caller provided {dimension}. Run `cortex refresh-index --retrieve` "
                "with `CORTEX_CACHE_DIR` cleared to rebuild from scratch."
            )

        pending = chunks_missing_embeddings(conn)
        if not pending:
            _set_meta(conn, "embedding_model", model_name or "test-embedder")
            _set_meta(conn, "embedding_dim", str(dimension))
            conn.commit()
            return EmbeddingBackfillResult(0, 0)

        # Embed in batches so we don't pin the entire corpus in memory at
        # once. fastembed itself batches internally; this is a safety belt.
        batch_size = 32
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            texts = [content for _, content in batch]
            vectors = embed_callable(texts)
            if len(vectors) != len(batch):
                raise RuntimeError(
                    f"embedder returned {len(vectors)} vectors for {len(batch)} chunks"
                )
            for (rowid, _content), vector in zip(batch, vectors, strict=True):
                if len(vector) != dimension:
                    raise ValueError(
                        f"embedding dimension mismatch: chunk {rowid} got "
                        f"{len(vector)}, expected {dimension}"
                    )
                conn.execute(
                    "INSERT INTO embeddings(rowid, embedding) VALUES (?, ?)",
                    (rowid, serialize_vector(vector)),
                )
                embedded += 1

        _set_meta(conn, "embedding_model", model_name or "test-embedder")
        _set_meta(conn, "embedding_dim", str(dimension))
        _set_meta(conn, "schema_version", str(SCHEMA_VERSION))
        conn.commit()
    finally:
        conn.close()

    return EmbeddingBackfillResult(embedded, skipped)


def has_populated_embeddings(project_root: Path) -> bool:
    """Return True when the index has ≥1 row in the embeddings table.

    Used by ``cortex retrieve`` to decide whether to default to ``hybrid``
    or stay on ``bm25`` (the per-brief default-mode flip rule).
    """

    target = retrieve_index_path(project_root)
    if not target.exists():
        return False
    conn = sqlite3.connect(target)
    try:
        if not has_embeddings_table(conn):
            return False
        row = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()
        return bool(row and int(row[0]) > 0)
    except sqlite3.DatabaseError:
        return False
    finally:
        conn.close()


def _get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def get_index_meta(project_root: Path) -> dict[str, str]:
    """Return the meta key/value pairs from the chunks index (best-effort)."""

    target = retrieve_index_path(project_root)
    if not target.exists():
        return {}
    conn = sqlite3.connect(target)
    try:
        rows = conn.execute("SELECT key, value FROM meta").fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    return {str(k): str(v) for k, v in rows}
