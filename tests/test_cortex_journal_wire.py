"""Tests for ``scripts/cortex-journal-wire.sh`` staging helpers."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from cortex.commands.init import init_command

REPO_ROOT = Path(__file__).resolve().parent.parent
WIRE_SCRIPT = REPO_ROOT / "scripts" / "cortex-journal-wire.sh"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _git_init(target: Path) -> None:
    _git("init", "-q", "--initial-branch=main", cwd=target)
    _git("config", "user.email", "t@e.co", cwd=target)
    _git("config", "user.name", "T", cwd=target)
    _git("config", "commit.gpgsign", "false", cwd=target)
    (target / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=target)
    _git("commit", "-q", "-m", "initial", cwd=target)


def _run_bash(script: str, *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = dict(**(env or {}))
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**dict(**__import__("os").environ), **merged},
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    runner = CliRunner()
    result = runner.invoke(init_command, ["--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    _git_init(tmp_path)
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-q", "-m", "add cortex scaffold", cwd=tmp_path)
    return tmp_path


def _make_cortex_shim(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir / "calls.log"
    log.write_text("", encoding="utf-8")
    shim = bin_dir / "cortex"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log!s}
            if [ "$1" = "journal" ] && [ "$2" = "post-merge" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "stage" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "verify" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "stage" ]; then
              path="$PWD/.cortex/journal/staged-pr-merged.md"
              mkdir -p "$(dirname "$path")"
              printf '# PR #42 merged\\n\\n**Type:** pr-merged\\n\\nBody.\\n' > "$path"
              printf '%s\\n' "$path"
              exit 0
            fi
            if [ "$1" = "journal" ] && [ "$2" = "verify" ]; then
              exit 0
            fi
            exit 1
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)


def _make_stale_cortex_shim(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir / "stale-cortex.calls.log"
    log.write_text("", encoding="utf-8")
    shim = bin_dir / "cortex"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log!s}
            if [ "$1" = "journal" ] && [ "$2" = "post-merge" ] && [ "${{3:-}}" = "--help" ]; then
              echo "unknown command" >&2
              exit 2
            fi
            exit 1
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log


def _make_uv_cortex_shim(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = bin_dir / "uv.calls.log"
    log.write_text("", encoding="utf-8")
    shim = bin_dir / "uv"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$*" >> {log!s}
            if [ "${{1:-}}" != "run" ] || [ "${{2:-}}" != "cortex" ]; then
              printf 'unexpected uv invocation: %s\\n' "$*" >&2
              exit 7
            fi
            shift 2
            if [ "$1" = "journal" ] && [ "$2" = "post-merge" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "stage" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "verify" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "stage" ]; then
              path="$PWD/.cortex/journal/staged-pr-merged.md"
              mkdir -p "$(dirname "$path")"
              printf '# PR #42 merged\\n\\n**Type:** pr-merged\\n\\nBody.\\n' > "$path"
              printf '%s\\n' "$path"
              exit 0
            fi
            if [ "$1" = "journal" ] && [ "$2" = "verify" ]; then
              exit 0
            fi
            exit 1
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return log


def test_should_stage_when_stage_mode_configured(project: Path) -> None:
    (project / ".cortex" / "config.toml").write_text(
        '[journal.t1_9]\nmode = "stage"\n',
        encoding="utf-8",
    )
    bin_dir = project / "bin"
    _make_cortex_shim(bin_dir)
    script = textwrap.dedent(
        f"""
        source {WIRE_SCRIPT!s}
        if cortex_journal_wire_should_stage "$PWD" main main 0; then
          echo yes
        else
          echo no
        fi
        """
    )
    result = _run_bash(
        script,
        cwd=project,
        env={"PATH": f"{bin_dir}:{__import__('os').environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "yes"


def test_should_stage_uses_uv_when_path_cortex_is_stale(project: Path) -> None:
    (project / ".cortex" / "config.toml").write_text(
        '[journal.t1_9]\nmode = "stage"\n',
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'cortex-fixture'\nversion = '0.0.0'\n",
        encoding="utf-8",
    )
    bin_dir = project / "bin"
    stale_log = _make_stale_cortex_shim(bin_dir)
    uv_log = _make_uv_cortex_shim(bin_dir)
    script = textwrap.dedent(
        f"""
        source {WIRE_SCRIPT!s}
        if cortex_journal_wire_should_stage "$PWD" main main 0; then
          echo yes
        else
          echo no
        fi
        """
    )
    result = _run_bash(
        script,
        cwd=project,
        env={"PATH": f"{bin_dir}:{__import__('os').environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "yes"
    assert "journal stage --help" in stale_log.read_text(encoding="utf-8")
    assert "run cortex journal verify --help" in uv_log.read_text(encoding="utf-8")


def test_should_not_stage_for_post_merge_writer_mode(project: Path) -> None:
    script = textwrap.dedent(
        f"""
        source {WIRE_SCRIPT!s}
        if cortex_journal_wire_should_stage "$PWD" main main 0; then
          echo yes
        else
          echo no
        fi
        """
    )
    result = _run_bash(script, cwd=project)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "no"


def test_stage_for_pr_amends_branch_tip(project: Path) -> None:
    (project / ".cortex" / "config.toml").write_text(
        '[journal.t1_9]\nmode = "stage"\n',
        encoding="utf-8",
    )
    bin_dir = project / "bin"
    _make_cortex_shim(bin_dir)
    _git("checkout", "-q", "-b", "feat/wire", cwd=project)
    (project / "feature.txt").write_text("x\n", encoding="utf-8")
    _git("add", "feature.txt", cwd=project)
    _git("commit", "-q", "-m", "feat: wire test", cwd=project)
    head_before = _git("rev-parse", "HEAD", cwd=project).stdout.strip()

    script = textwrap.dedent(
        f"""
        source {WIRE_SCRIPT!s}
        cortex_journal_wire_stage_for_pr "$PWD" 42
        """
    )
    result = _run_bash(
        script,
        cwd=project,
        env={"PATH": f"{bin_dir}:{__import__('os').environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    head_after = _git("rev-parse", "HEAD", cwd=project).stdout.strip()
    assert head_after != head_before
    assert (project / ".cortex" / "journal" / "staged-pr-merged.md").is_file()
    log = (bin_dir / "calls.log").read_text(encoding="utf-8")
    assert "journal stage --type pr-merged --pr 42" in log


def test_stage_for_pr_uses_uv_when_path_cortex_is_stale(project: Path) -> None:
    (project / ".cortex" / "config.toml").write_text(
        '[journal.t1_9]\nmode = "stage"\n',
        encoding="utf-8",
    )
    (project / "pyproject.toml").write_text(
        "[project]\nname = 'cortex-fixture'\nversion = '0.0.0'\n",
        encoding="utf-8",
    )
    bin_dir = project / "bin"
    stale_log = _make_stale_cortex_shim(bin_dir)
    uv_log = _make_uv_cortex_shim(bin_dir)
    _git("checkout", "-q", "-b", "feat/wire-uv", cwd=project)
    (project / "feature.txt").write_text("x\n", encoding="utf-8")
    _git("add", "feature.txt", cwd=project)
    _git("commit", "-q", "-m", "feat: wire uv test", cwd=project)

    script = textwrap.dedent(
        f"""
        source {WIRE_SCRIPT!s}
        cortex_journal_wire_stage_for_pr "$PWD" 42
        """
    )
    result = _run_bash(
        script,
        cwd=project,
        env={"PATH": f"{bin_dir}:{__import__('os').environ['PATH']}"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert (project / ".cortex" / "journal" / "staged-pr-merged.md").is_file()
    assert "run cortex journal stage --type pr-merged --pr 42" in uv_log.read_text(
        encoding="utf-8"
    )
    assert "journal stage --type pr-merged --pr 42" not in stale_log.read_text(
        encoding="utf-8"
    )


def test_verify_before_merge_blocks_when_verify_fails(project: Path) -> None:
    (project / ".cortex" / "config.toml").write_text(
        '[journal.t1_9]\nmode = "stage"\n',
        encoding="utf-8",
    )
    bin_dir = project / "bin"
    _make_cortex_shim(bin_dir)
    failing_shim = bin_dir / "cortex"
    failing_shim.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [ "$1" = "journal" ] && [ "$2" = "post-merge" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "stage" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "verify" ] && [ "$3" = "--help" ]; then exit 0; fi
            if [ "$1" = "journal" ] && [ "$2" = "verify" ]; then
              echo "error: missing staged entry" >&2
              exit 1
            fi
            exit 1
            """
        ),
        encoding="utf-8",
    )
    failing_shim.chmod(0o755)

    script = textwrap.dedent(
        f"""
        source {WIRE_SCRIPT!s}
        cortex_journal_wire_verify_before_merge "$PWD" 99 main main
        """
    )
    result = _run_bash(
        script,
        cwd=project,
        env={"PATH": f"{bin_dir}:{__import__('os').environ['PATH']}"},
    )
    assert result.returncode == 1
    assert "merge blocked" in result.stderr
