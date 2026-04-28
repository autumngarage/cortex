"""Tests for `cortex init --seed-from` Doctrine pack seeding."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner, Result

from cortex.cli import cli


def _run_init(project: Path, *extra_args: str) -> Result:
    return CliRunner().invoke(cli, ["init", "--path", str(project), *extra_args])


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_seed_from_fresh_seed_preserves_numbered_bytes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    entries = {
        "0100-alpha.md": b"---\nSentinel-baseline: true\n---\n\n# Alpha\n",
        "0101-beta.md": b"---\nLoad-priority: always\n---\n\n# Beta\n",
        "0102-gamma.md": b"---\nPromoted-from: sentinel/defaults\n---\n\n# Gamma\n",
    }
    for name, content in entries.items():
        _write(source / name, content)

    result = _run_init(project, "--seed-from", str(source))

    assert result.exit_code == 0, result.output
    for name, content in entries.items():
        assert (project / ".cortex" / "doctrine" / name).read_bytes() == content


def test_seed_from_assigns_unnumbered_sources_lexicographically(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    _write(source / "b.md", b"# Bravo Heading\n")
    _write(source / "a.md", b"# Alpha Heading\n")
    _write(source / "c.md", b"# Charlie Heading\n")

    result = _run_init(project, "--seed-from", str(source))

    assert result.exit_code == 0, result.output
    doctrine_names = sorted(p.name for p in (project / ".cortex" / "doctrine").glob("*.md"))
    assert doctrine_names == [
        "0100-alpha-heading.md",
        "0101-bravo-heading.md",
        "0102-charlie-heading.md",
    ]


def test_seed_from_numbering_floor_ignores_existing_0099(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    assert _run_init(project).exit_code == 0
    _write(project / ".cortex" / "doctrine" / "0099-legacy.md", b"# Legacy\n")
    _write(source / "entry.md", b"# Seeded Entry\n")

    result = _run_init(project, "--force", "--seed-from", str(source))

    assert result.exit_code == 0, result.output
    assert (
        project / ".cortex" / "doctrine" / "0100-seeded-entry.md"
    ).read_bytes() == b"# Seeded Entry\n"


def test_seed_from_numbered_collision_aborts_on_existing_number(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    assert _run_init(project).exit_code == 0
    _write(project / ".cortex" / "doctrine" / "0102-bar.md", b"# Existing\n")
    _write(source / "0102-foo.md", b"# New\n")
    _write(source / "0103-next.md", b"# Should not copy\n")

    result = _run_init(project, "--force", "--seed-from", str(source))

    assert result.exit_code == 4
    assert "0102-bar.md" in result.output
    assert not (project / ".cortex" / "doctrine" / "0103-next.md").exists()


def test_seed_from_conflict_abort_writes_no_seed_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    assert _run_init(project).exit_code == 0
    existing = project / ".cortex" / "doctrine" / "0100-x.md"
    _write(existing, b"# Existing X\n")
    _write(source / "0100-x.md", b"# Different X\n")
    _write(source / "0101-y.md", b"# Should not copy\n")

    result = _run_init(project, "--force", "--seed-from", str(source))

    assert result.exit_code == 4
    assert existing.read_bytes() == b"# Existing X\n"
    assert not (project / ".cortex" / "doctrine" / "0101-y.md").exists()


def test_seed_from_merge_skip_existing_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    assert _run_init(project).exit_code == 0
    existing = project / ".cortex" / "doctrine" / "0100-x.md"
    _write(existing, b"# Existing X\n")
    _write(source / "0100-x.md", b"# Different X\n")
    _write(source / "0101-y.md", b"# New Y\n")

    first = _run_init(
        project,
        "--force",
        "--seed-from",
        str(source),
        "--merge",
        "skip-existing",
    )
    second = _run_init(
        project,
        "--force",
        "--seed-from",
        str(source),
        "--merge",
        "skip-existing",
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert existing.read_bytes() == b"# Existing X\n"
    assert (project / ".cortex" / "doctrine" / "0101-y.md").read_bytes() == b"# New Y\n"
    doctrine_names = sorted(p.name for p in (project / ".cortex" / "doctrine").glob("*.md"))
    assert doctrine_names == ["0100-x.md", "0101-y.md"]
    assert "skipped existing Doctrine entry" in second.output


def test_seed_from_skips_underscore_prefixed_files(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    _write(source / "_template.md", b"# Template\n")
    _write(source / "0100-real.md", b"# Real\n")

    result = _run_init(project, "--seed-from", str(source))

    assert result.exit_code == 0, result.output
    doctrine_names = sorted(p.name for p in (project / ".cortex" / "doctrine").glob("*.md"))
    assert doctrine_names == ["0100-real.md"]


def test_seed_from_missing_directory_exits_2(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    missing = tmp_path / "missing"

    result = _run_init(project, "--seed-from", str(missing))

    assert result.exit_code == 2
    assert "seed source is not a directory" in result.output


def test_seed_from_empty_directory_logs_no_entries(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "pack"
    project.mkdir()
    source.mkdir()
    _write(source / "notes.txt", b"# Not markdown\n")

    result = _run_init(project, "--seed-from", str(source))

    assert result.exit_code == 0, result.output
    assert "no doctrine entries found" in result.output
    assert not list((project / ".cortex" / "doctrine").glob("*.md"))


def test_init_help_mentions_seed_from_and_merge() -> None:
    result = CliRunner().invoke(cli, ["init", "--help"])

    assert result.exit_code == 0
    assert "--seed-from" in result.output
    assert "--merge" in result.output
