"""`cortex init` — scaffold a SPEC-v0.3.1-dev-conformant `.cortex/` directory.

Creates:

- `.cortex/SPEC_VERSION`            → current spec version (major.minor.patch[-dev])
- `.cortex/protocol.md`             → copy of the Cortex Protocol shipped with this CLI
- `.cortex/templates/...`           → copy of the template tree shipped with this CLI
- `.cortex/doctrine/`               → empty; seeded with `.gitkeep`
- `.cortex/plans/`                  → empty; seeded with `.gitkeep`
- `.cortex/journal/`                → empty; seeded with `.gitkeep`
- `.cortex/procedures/`             → empty; seeded with `.gitkeep`
- `.cortex/map.md`                  → seven-field stub with `Incomplete: [all sources]`
- `.cortex/state.md`                → seven-field stub with `Incomplete: [all sources]`

Refuses to overwrite an existing `.cortex/SPEC_VERSION` unless `--force` is
passed. With `--force`, the scaffold files (SPEC_VERSION, protocol.md,
templates/, map.md/state.md stubs) are overwritten; existing doctrine, plan,
journal, and procedure content is never deleted.
"""

from __future__ import annotations

import shutil
import sys
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import click

CURRENT_SPEC_VERSION = "0.3.1-dev"

SCAFFOLD_SUBDIRS = ("doctrine", "plans", "journal", "procedures")


def _package_data_root() -> Path:
    """Resolve the filesystem path to the cortex._data directory.

    Uses `importlib.resources.files(...)` which works for installed wheels
    and for editable installs (uv sync). Callers should treat the returned
    path as read-only.
    """
    root = resources.files("cortex._data")
    # `files()` returns a MultiplexedPath or Traversable; for our shipped
    # data (real filesystem, not inside a zip) `Path(str(root))` is safe.
    return Path(str(root))


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def _derived_stub(title: str, layer: str, generator: str) -> str:
    """Render a seven-field derived-layer stub (map or state)."""
    now = _now_iso()
    return f"""---
Generated: {now}
Generator: {generator} (scaffolded by `cortex init`; real synthesis ships in Phase C)
Sources:
  - (none — pending Phase C synthesis)
Corpus: 0 files (no synthesis yet)
Omitted: []
Incomplete:
  - All sources — scaffolded at project init; `cortex refresh-{layer}` will regenerate from primary sources in Phase C.
Conflicts-preserved: []
Spec: {CURRENT_SPEC_VERSION.split("-")[0]}
---

# {title}

> **Stub — pending Phase C synthesis.** This file is a scaffold placeholder written by `cortex init`. Real content arrives when `cortex refresh-{layer}` ships.
"""


def _copy_tree(src: Path, dst: Path, *, overwrite: bool) -> list[Path]:
    """Copy every file under `src` into `dst`, preserving relative structure.

    Skips `__init__.py` files that are part of the _data package machinery.
    Returns the list of destination paths written.
    """
    written: list[Path] = []
    for entry in src.rglob("*"):
        if entry.is_dir():
            continue
        if entry.name == "__init__.py":
            continue
        rel = entry.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            # Caller is responsible for checking SPEC_VERSION *before* calling.
            # If a file exists here despite SPEC_VERSION absence, treat it as
            # user content and leave it alone.
            continue
        shutil.copyfile(entry, target)
        written.append(target)
    return written


def _ensure_subdir(path: Path) -> None:
    """Create `path` if missing; drop a `.gitkeep` so git tracks empty dirs."""
    path.mkdir(parents=True, exist_ok=True)
    gitkeep = path / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()


@click.command("init")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite the scaffold files even if `.cortex/SPEC_VERSION` already exists. "
    "Doctrine, Plan, Journal, and Procedure contents are never deleted.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root where `.cortex/` will be created.",
)
def init_command(*, force: bool, target_path: Path) -> None:
    """Scaffold a SPEC-v0.3.1-dev-conformant `.cortex/` directory in the target project."""
    target_path = Path(target_path).resolve()
    if not target_path.exists():
        click.echo(f"error: target path does not exist: {target_path}", err=True)
        sys.exit(2)

    cortex_dir = target_path / ".cortex"
    spec_version_file = cortex_dir / "SPEC_VERSION"

    cortex_has_any_content = cortex_dir.exists() and any(cortex_dir.iterdir())

    if spec_version_file.exists() and not force:
        existing = spec_version_file.read_text().strip()
        click.echo(
            f"error: `.cortex/SPEC_VERSION` already exists ({existing}) at {cortex_dir}. "
            "Use `--force` to rewrite the scaffold; existing doctrine/plan/journal/"
            "procedure content is preserved either way.",
            err=True,
        )
        sys.exit(1)

    if cortex_has_any_content and not spec_version_file.exists() and not force:
        # `.cortex/` exists with files but no SPEC_VERSION — ambiguous state.
        # Writing a fresh scaffold on top would leave a mix of shipped files and
        # pre-existing content under a "conformant" SPEC_VERSION marker. Refuse.
        click.echo(
            f"error: {cortex_dir} already contains content but has no `SPEC_VERSION` marker. "
            "This looks like an incomplete or hand-authored Cortex directory. "
            "Use `--force` to write the scaffold over any scaffold-level files "
            "(SPEC_VERSION, protocol.md, templates/, map.md, state.md); "
            "doctrine/plan/journal/procedure content is preserved either way.",
            err=True,
        )
        sys.exit(1)

    data_root = _package_data_root()

    cortex_dir.mkdir(exist_ok=True)

    # When we get here, either the directory is fresh/empty, or --force is set.
    # In both cases the scaffold-level files are written/overwritten so the
    # advertised "spec v{CURRENT_SPEC_VERSION} conformant" marker is truthful.
    # Non-scaffold files (doctrine/, plans/, journal/, procedures/ contents) are
    # never touched because we never write into those subdirs except .gitkeep.

    # 1. SPEC_VERSION
    spec_version_file.write_text(CURRENT_SPEC_VERSION + "\n")

    # 2. protocol.md
    protocol_src = data_root / "protocol.md"
    protocol_dst = cortex_dir / "protocol.md"
    shutil.copyfile(protocol_src, protocol_dst)

    # 3. templates/ tree (overwrite scaffold template files; we're past the guard)
    templates_src = data_root / "templates"
    templates_dst = cortex_dir / "templates"
    _copy_tree(templates_src, templates_dst, overwrite=True)

    # 4. subdirectories with .gitkeep (.gitkeep is scaffold; empty dirs stay empty)
    for sub in SCAFFOLD_SUBDIRS:
        _ensure_subdir(cortex_dir / sub)

    # 5. map.md and state.md stubs (scaffold files; overwrite)
    for layer, title, generator in (
        ("map", "Project Map", "cortex init v0.1.0"),
        ("state", "Project State", "cortex init v0.1.0"),
    ):
        (cortex_dir / f"{layer}.md").write_text(_derived_stub(title, layer, generator))

    click.echo(f"Scaffolded {cortex_dir} (spec v{CURRENT_SPEC_VERSION}).")
    click.echo("Next steps:")
    click.echo("  1. Author doctrine/0001-why-<project>-exists.md (see templates/doctrine/candidate.md for shape).")
    click.echo("  2. Import `@.cortex/protocol.md` and `@.cortex/state.md` into your AGENTS.md or CLAUDE.md.")
    click.echo("  3. Run `cortex doctor` to validate the scaffold (Phase B — coming soon).")
