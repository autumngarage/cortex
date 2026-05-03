"""Embedding model loading and inference for semantic retrieval (S2).

Design notes:

- **Model choice.** ``BAAI/bge-small-en-v1.5`` (384-dim) via ``fastembed``'s
  CPU-only ONNX runtime. Picked because:
    * Smallest of the bge family that still tops MTEB English retrieval; the
      base/large variants give marginal lift for 4-10x the model size.
    * Pure CPU execution (no GPU bootstrapping), runs fine on M-series + x86.
    * Apache-2.0; redistributable via the brew formula path.
  Alternatives considered:
    * ``sentence-transformers/all-MiniLM-L6-v2`` — 384-dim, smaller (~22 MB),
      slightly weaker on MTEB. Acceptable second choice; a future doctrine
      bump could swap if model bloat becomes an issue.
    * ``intfloat/e5-small-v2`` — needs ``query:`` / ``passage:`` prefixes
      that complicate the call site without a measured win on Cortex's
      corpus.
    * Cloud embedders (Voyage, Cohere) — explicitly out of scope per
      Doctrine 0006 and ``plans/cortex-retrieve.md`` § Embedder selection.
      v0.1 ships ``builtin`` only.

- **Cache path (council delta #4).** fastembed's default lands models in
  ``~/.cache/fastembed/`` which can collide with multi-user installs. We
  force ``~/.cache/cortex/models/``. If ``~/.cache/`` is unwritable
  (permission denied or HOME unset), we fall back to a per-project
  ``.cortex/.index/models/`` location and emit a stderr notice.

- **Failure modes (no silent failures).** Every import / download / probe
  failure surfaces as a typed ``EmbeddingUnavailableError`` carrying the
  reason. ``cortex retrieve`` translates that into a clear stderr line and
  falls back to BM25. The in-process cache (see ``probe_embedder``) avoids
  re-probing on every query in the same session.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIMENSION = 384

# Module-level state for the in-process probe cache. The "no silent failures"
# principle requires we DO surface the first failure, but re-probing on every
# subsequent query in the same process would spam stderr and slow things
# down. Cache the probe outcome (success or typed error) per process.
_probe_lock = threading.Lock()
_probe_cache: EmbedderProbe | None = None


class EmbeddingUnavailableError(RuntimeError):
    """Raised when the semantic embedder cannot be used."""

    def __init__(self, reason: str, *, install_hint: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.install_hint = install_hint

    def user_message(self) -> str:
        msg = f"semantic retrieval unavailable: {self.reason}"
        if self.install_hint:
            msg = f"{msg} ({self.install_hint})"
        return msg


@dataclass(frozen=True)
class EmbedderProbe:
    """Outcome of probing the embedder runtime."""

    available: bool
    error: EmbeddingUnavailableError | None
    cache_dir: Path | None


def cortex_model_cache_dir(project_root: Path | None = None) -> Path:
    """Return the directory used for fastembed model artifacts.

    Precedence (council delta #4):
        1. ``CORTEX_MODEL_CACHE_DIR`` env var (escape hatch).
        2. ``~/.cache/cortex/models/`` if writable.
        3. ``<project_root>/.cortex/.index/models/`` if step 2 fails.

    The project-local fallback is gitignored because ``.cortex/.index/`` is
    already in ``.cortex/.gitignore``.
    """

    override = os.environ.get("CORTEX_MODEL_CACHE_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    home_cache = Path(os.path.expanduser("~/.cache/cortex/models"))
    if _is_writable(home_cache):
        home_cache.mkdir(parents=True, exist_ok=True)
        return home_cache

    if project_root is not None:
        local = project_root / ".cortex" / ".index" / "models"
        try:
            local.mkdir(parents=True, exist_ok=True)
            print(
                f"cortex: ~/.cache not writable; using project-local model "
                f"cache at {local}",
                file=sys.stderr,
            )
            return local
        except OSError:
            pass

    raise EmbeddingUnavailableError(
        "no writable model cache location found",
        install_hint="set CORTEX_MODEL_CACHE_DIR to a writable directory",
    )


def _is_writable(path: Path) -> bool:
    """Return True if ``path`` (or its parent) accepts writes."""

    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Walk up to find an existing parent we can probe.
        parent = path.parent
        while parent and not parent.exists():
            parent = parent.parent
        return parent is not None and os.access(parent, os.W_OK)
    return os.access(path, os.W_OK)


def probe_embedder(project_root: Path | None = None, *, force: bool = False) -> EmbedderProbe:
    """Probe whether semantic embedding is available in this process.

    Caches the result per-process so repeated ``cortex retrieve`` calls in
    the same Python session don't re-probe (and re-print warnings).
    """

    global _probe_cache
    with _probe_lock:
        if _probe_cache is not None and not force:
            return _probe_cache
        probe = _do_probe(project_root)
        _probe_cache = probe
        return probe


def reset_probe_cache() -> None:
    """Reset the in-process probe cache. Test-only helper."""

    global _probe_cache
    with _probe_lock:
        _probe_cache = None


def _do_probe(project_root: Path | None) -> EmbedderProbe:
    try:
        cache_dir = cortex_model_cache_dir(project_root)
    except EmbeddingUnavailableError as exc:
        return EmbedderProbe(False, exc, None)

    try:
        import sqlite_vec  # type: ignore[import-not-found] # noqa: F401
    except ImportError as exc:
        return EmbedderProbe(
            False,
            EmbeddingUnavailableError(
                f"sqlite-vec extension not importable ({exc.__class__.__name__}: {exc})",
                install_hint="install with `pip install 'cortex[semantic]'` or "
                "`pip install sqlite-vec fastembed`",
            ),
            cache_dir,
        )

    try:
        from fastembed import TextEmbedding  # type: ignore[import-not-found]
    except ImportError as exc:
        return EmbedderProbe(
            False,
            EmbeddingUnavailableError(
                f"fastembed/onnxruntime not importable ({exc.__class__.__name__}: {exc})",
                install_hint=(
                    "install with `pip install 'cortex[semantic]'`. "
                    "Note: aarch64 Linux lacks onnxruntime PyPI wheels"
                ),
            ),
            cache_dir,
        )

    # Tiny end-to-end probe: instantiate model + embed one short string.
    # This catches "model file fails to download" (network down + uncached)
    # and "ONNX backend broken" failures up-front instead of on first query.
    try:
        embedder = TextEmbedding(model_name=EMBED_MODEL_NAME, cache_dir=str(cache_dir))
        next(iter(embedder.embed(["probe"])))
    except Exception as exc:
        return EmbedderProbe(
            False,
            EmbeddingUnavailableError(
                f"embedding model load failed ({exc.__class__.__name__}: {exc})",
                install_hint=(
                    "first model load needs network to download "
                    f"{EMBED_MODEL_NAME} (~25 MB) into {cache_dir}; "
                    "subsequent runs are offline"
                ),
            ),
            cache_dir,
        )

    return EmbedderProbe(True, None, cache_dir)


def load_embedder(project_root: Path | None = None) -> Embedder:
    """Return a callable embedder, raising ``EmbeddingUnavailableError`` if not."""

    probe = probe_embedder(project_root)
    if not probe.available or probe.error is not None:
        raise probe.error or EmbeddingUnavailableError("semantic retrieval unavailable")
    return Embedder(project_root=project_root, cache_dir=probe.cache_dir)


class Embedder:
    """Thin wrapper around fastembed for the call site.

    Holds a single ``TextEmbedding`` instance per process (model load is the
    heavy step). Exposes ``embed(texts)`` returning ``list[list[float]]``
    with deterministic ordering matching the input list.
    """

    _instance: Embedder | None = None
    _instance_lock = threading.Lock()

    def __init__(self, *, project_root: Path | None, cache_dir: Path | None) -> None:
        self._project_root = project_root
        self._cache_dir = cache_dir
        self._model = None

    @classmethod
    def shared(cls, project_root: Path | None = None) -> Embedder:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = load_embedder(project_root)
            return cls._instance

    @classmethod
    def reset_shared(cls) -> None:
        """Test-only: clear the singleton."""

        with cls._instance_lock:
            cls._instance = None

    def _model_or_raise(self) -> Any:
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise EmbeddingUnavailableError(
                    f"fastembed not importable ({exc})",
                    install_hint="install with `pip install 'cortex[semantic]'`",
                ) from exc
            cache = str(self._cache_dir) if self._cache_dir else None
            self._model = TextEmbedding(model_name=EMBED_MODEL_NAME, cache_dir=cache)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings for ``texts`` in input order."""

        if not texts:
            return []
        model = self._model_or_raise()
        out: list[list[float]] = []
        for vector in model.embed(texts):
            # fastembed yields numpy arrays; cast to plain lists so the
            # SQLite layer doesn't depend on numpy types.
            out.append([float(x) for x in vector])
        return out
