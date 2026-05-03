"""SQLite FTS5 index build and invalidation for `cortex retrieve`."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cortex import __version__
from cortex.frontmatter import parse_frontmatter
from cortex.retrieve.cache import index_path, temp_index_path
from cortex.retrieve.chunker import chunk_markdown

SCHEMA_VERSION = 1


class FTS5UnavailableError(RuntimeError):
    """Raised when Python's sqlite3 build does not provide FTS5."""


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
    return any(part.startswith(".") for part in rel_parts)


def _existing_file_fingerprints(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    rows = conn.execute(
        "SELECT path, MAX(file_mtime), MAX(file_size) FROM chunks GROUP BY path"
    ).fetchall()
    return {str(path): (float(mtime), int(size)) for path, mtime, size in rows}


def _replace_file_chunks(conn: sqlite3.Connection, source: SourceFile) -> None:
    text = source.path.read_text()
    frontmatter, body = parse_frontmatter(text)
    body_start_line = _body_start_line(text, body)
    frontmatter_json = json.dumps(frontmatter, sort_keys=True) if frontmatter else None
    chunks = chunk_markdown(body)

    conn.execute("DELETE FROM chunks WHERE path = ?", (source.rel_path,))
    for chunk in chunks:
        conn.execute(
            """
            INSERT INTO chunks (
              path, chunk_idx, start_line, end_line, content,
              frontmatter_json, file_mtime, file_size
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )


def _body_start_line(text: str, body: str) -> int:
    if body == text:
        return 1
    prefix_len = len(text) - len(body)
    if prefix_len < 0:
        return 1
    return text[:prefix_len].count("\n") + 1
