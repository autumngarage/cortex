"""`cortex grep` — frontmatter-aware ripgrep wrapper per Protocol § 1.

Mid-session retrieval in the Cortex Protocol is grep, not semantic search
(Doctrine 0005 #1). This command shells out to ``rg`` (ripgrep) over the
project's ``.cortex/`` directory and prefixes each matched file with a
single-line metadata summary extracted from its frontmatter so the agent
sees *what layer and shape* a match came from without a second read.

Degrades gracefully when ``rg`` is not on PATH — prints an error telling
the user to install ripgrep and exits 3.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import click

from cortex.compat import warn_if_incompatible
from cortex.frontmatter import parse_frontmatter

LAYER_CHOICES = ("doctrine", "plans", "journal", "procedures", "templates")


def _find_rg() -> str | None:
    return shutil.which("rg")


def _parse_rg_json(stdout: str) -> tuple[dict[str, list[tuple[str, int, str]]], int]:
    """Group ``rg --json`` output by file.

    Returns ``(grouped, malformed_count)`` where ``grouped`` maps
    ``file_path → [(kind, line_number, text), ...]`` and ``malformed_count``
    is the number of NDJSON records that failed to decode. The caller
    surfaces a stderr warning when that count is non-zero so a partial or
    corrupt ripgrep stream doesn't masquerade as a clean "no matches".
    Non-event records (``begin``/``end``/``summary``) are ignored.
    """
    grouped: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    malformed = 0
    for raw_line in stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        kind = record.get("type")
        if kind not in ("match", "context"):
            continue
        data = record.get("data") or {}
        path_field = data.get("path") or {}
        path = path_field.get("text")
        line_no = data.get("line_number")
        text_field = data.get("lines") or {}
        text = text_field.get("text", "")
        if path and isinstance(line_no, int):
            grouped[path].append((kind, line_no, text))
    return grouped, malformed


def _summarize_file(path: Path, project_root: Path) -> str:
    """One-line metadata summary extracted from ``path``'s frontmatter."""
    try:
        text = path.read_text()
    except OSError:
        return ""
    rel = path.relative_to(project_root)
    frontmatter, _body = parse_frontmatter(text)

    # Bold-inline scalars that Doctrine/Journal/Procedures use (SPEC § 6).
    bold_fields: dict[str, str] = {}
    header_lines = text.splitlines()[:40]
    for line in header_lines:
        if line.startswith("**") and ":**" in line:
            key, _, value = line[2:].partition(":**")
            bold_fields[key.strip()] = value.strip()

    candidates = ["Status", "Type", "Date", "Written", "Load-priority"]
    pairs: list[str] = []
    for key in candidates:
        resolved: str | None = None
        fm_value = frontmatter.get(key)
        if isinstance(fm_value, str):
            resolved = fm_value
        elif key in bold_fields:
            resolved = bold_fields[key]
        if resolved:
            pairs.append(f"{key}: {resolved}")
    meta = " | ".join(pairs) if pairs else "(no frontmatter fields)"
    return f"{rel}  [{meta}]"


@click.command(
    "grep",
    context_settings={"ignore_unknown_options": True, "help_option_names": ["-h", "--help"]},
)
@click.argument("pattern")
@click.option(
    "--layer",
    type=click.Choice(LAYER_CHOICES),
    default=None,
    help="Restrict the search to one `.cortex/` subdirectory.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
@click.argument("rg_args", nargs=-1, type=click.UNPROCESSED)
def grep_command(*, pattern: str, layer: str | None, target_path: Path, rg_args: tuple[str, ...]) -> None:
    """Search `.cortex/` for PATTERN with ripgrep, annotated with per-file frontmatter.

    Extra arguments after ``--`` are forwarded to ``rg`` so that flags like
    ``-i`` (case insensitive), ``-C 2`` (context), or ``--type md`` can be
    composed. Example::

        cortex grep "retry backoff" -- -i -C 2
    """
    target_path = Path(target_path).resolve()
    cortex_dir = target_path / ".cortex"
    if not cortex_dir.exists():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    rg = _find_rg()
    if rg is None:
        click.echo(
            "error: ripgrep (`rg`) not found on PATH. Install it via `brew install ripgrep` "
            "(or your OS package manager) and retry.",
            err=True,
        )
        sys.exit(3)

    warn_if_incompatible(cortex_dir)

    search_root = cortex_dir / layer if layer else cortex_dir
    if not search_root.exists():
        click.echo(
            f"warning: {search_root} does not exist; nothing to search.",
            err=True,
        )
        return

    # ``--json`` gives newline-delimited records with ``{type: match|context|...}``
    # so context lines (emitted by ``-C/-A/-B``) are unambiguous instead of
    # being line-noise-separated from match lines.
    # ``--`` terminator so patterns beginning with `-` (e.g. ``- [ ]`` for
    # Markdown checkboxes) are not parsed as ripgrep flags.
    cmd = [rg, "--json", *rg_args, "--", pattern, str(search_root)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode == 2:
        # ripgrep convention: 2 = error (bad pattern, etc.).
        click.echo(result.stderr, err=True, nl=False)
        sys.exit(2)

    grouped, malformed = _parse_rg_json(result.stdout)
    if malformed:
        click.echo(
            f"warning: {malformed} `rg --json` record(s) could not be decoded; "
            "output may be incomplete.",
            err=True,
        )
    if not grouped:
        click.echo(f"no matches for {pattern!r} under {search_root.relative_to(target_path)}")
        return

    for file_path, lines in grouped.items():
        summary = _summarize_file(Path(file_path), target_path)
        if summary:
            click.echo(summary)
        for kind, line_no, text in lines:
            marker = ":" if kind == "match" else "-"
            click.echo(f"  {line_no}{marker}{text.rstrip()}")
