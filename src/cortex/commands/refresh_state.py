"""`cortex refresh-state` — regenerate `.cortex/state.md` deterministically.

Set ``CORTEX_DETERMINISTIC=1`` in tests to freeze ``Generated:`` to
``2000-01-01T00:00:00+00:00``. The renderer otherwise uses the current
timestamp while keeping ordering deterministic.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from cortex.compat import require_compatible
from cortex.config import load_refresh_index_config
from cortex.index import refresh_index
from cortex.state_render import build_state_inputs, render_state

SKIP_REWRITE_NOTICE = (
    "==> refresh-state: source content unchanged; skipping rewrite "
    "(use --force to override)"
)

_FRONTMATTER_BLOCK_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_SOURCE_HASH_HEADER_RE = re.compile(r"(?m)^Sources-hash:[ \t]*(?P<inline>.*?)[ \t]*$")
_SOURCE_HASH_ITEM_RE = re.compile(r"^(?P<path>\S.+?):\s+(?P<digest>[0-9a-f]{64})$")


@dataclass(frozen=True)
class RefreshStateResult:
    """Outcome of a state refresh attempt."""

    ok: bool
    path: Path
    wrote: bool


@click.command("refresh-state")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the regenerated state.md to stdout without writing it.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Rewrite state.md even when the existing Sources-hash matches.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def refresh_state_command(*, dry_run: bool, force: bool, target_path: Path) -> None:
    """Regenerate `.cortex/state.md` from journal entries, plans, and doctrine.

    Preserves hand-authored regions wrapped in ``<!-- cortex:hand -->`` /
    ``<!-- cortex:end-hand -->`` markers and updates the seven-field provenance
    header. Use ``--dry-run`` to preview the output before writing. Also see
    ``cortex refresh-index --retrieve`` to rebuild the search index used by
    ``cortex retrieve``.
    """
    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    require_compatible(cortex_dir)
    deterministic = os.environ.get("CORTEX_DETERMINISTIC") == "1"
    if not dry_run:
        _refresh_index_after_write(project_root)
    inputs = build_state_inputs(project_root, deterministic=deterministic)
    rendered = render_state(inputs)

    if dry_run:
        click.echo(rendered, nl=False)
        return

    result = write_state_if_changed(
        project_root,
        rendered=rendered,
        sources_hash=inputs.source_hashes,
        force=force,
    )
    if not result.ok:
        sys.exit(1)
    if result.wrote:
        _refresh_retrieve_index_if_present(project_root)
        click.echo(str(result.path))


def refresh_state(project_root: Path, *, force: bool = False) -> RefreshStateResult:
    """Regenerate state.md and write only when source-derived content changed."""

    cortex_dir = project_root / ".cortex"
    require_compatible(cortex_dir)
    try:
        inputs = build_state_inputs(project_root)
        rendered = render_state(inputs)
    except Exception as exc:
        click.echo(f"error: refresh-state failed: {exc}", err=True)
        return RefreshStateResult(ok=False, path=cortex_dir / "state.md", wrote=False)
    return write_state_if_changed(
        project_root,
        rendered=rendered,
        sources_hash=inputs.source_hashes,
        force=force,
    )


def write_state_if_changed(
    project_root: Path,
    *,
    rendered: str,
    sources_hash: dict[str, str],
    force: bool,
) -> RefreshStateResult:
    """Write rendered state unless the existing Sources-hash proves it is unchanged."""

    state_path = project_root / ".cortex" / "state.md"
    if not force and state_path.is_file():
        existing_hash, warning = _read_existing_sources_hash(state_path)
        if warning is not None:
            click.echo(f"warning: {state_path}: {warning}; rewriting state.md", err=True)
        elif existing_hash is not None and existing_hash == sources_hash:
            click.echo(SKIP_REWRITE_NOTICE, err=True)
            return RefreshStateResult(ok=True, path=state_path, wrote=False)

    try:
        state_path.write_text(rendered)
    except OSError as exc:
        click.echo(f"error: could not write state.md: {exc}", err=True)
        return RefreshStateResult(ok=False, path=state_path, wrote=False)
    return RefreshStateResult(ok=True, path=state_path, wrote=True)


def _read_existing_sources_hash(state_path: Path) -> tuple[dict[str, str] | None, str | None]:
    """Return (mapping, warning). None mapping means the field is absent."""

    try:
        text = state_path.read_text()
    except OSError as exc:
        return None, f"could not read existing Sources-hash: {exc}"
    frontmatter = _FRONTMATTER_BLOCK_RE.match(text)
    if frontmatter is None:
        return None, None
    block = frontmatter.group(1)
    header = _SOURCE_HASH_HEADER_RE.search(block)
    if header is None:
        return None, None
    if header.group("inline"):
        return None, "malformed Sources-hash field; expected block mapping"

    lines = block[header.end() :].splitlines()
    result: dict[str, str] = {}
    for raw_line in lines:
        if not raw_line.strip():
            continue
        if not raw_line.startswith((" ", "\t")):
            break
        line = raw_line.strip()
        match = _SOURCE_HASH_ITEM_RE.match(line)
        if match is None:
            return None, f"malformed Sources-hash entry `{line}`"
        result[match.group("path").strip()] = match.group("digest")
    return result, None


def _refresh_index_after_write(project_root: Path) -> None:
    """Best-effort inline index refresh; silent on success."""

    config = load_refresh_index_config(project_root)
    try:
        result = refresh_index(project_root, config)
    except Exception as exc:
        click.echo(f"warning: could not refresh .cortex/.index.json: {exc}", err=True)
        return
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)


def _refresh_retrieve_index_if_present(project_root: Path) -> None:
    try:
        from cortex.retrieve.index import rebuild_index, retrieve_index_exists

        if not retrieve_index_exists(project_root):
            return
        rebuild_index(project_root)
    except Exception as exc:
        click.echo(f"warning: could not refresh .cortex/.index/chunks.sqlite: {exc}", err=True)
