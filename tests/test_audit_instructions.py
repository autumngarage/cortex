"""Tests for `cortex doctor --audit-instructions`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from cortex.cli import cli


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def _write_config(project: Path, body: str) -> None:
    config = project / ".cortex" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(body)


def _run(project: Path, *args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli, ["doctor", "--path", str(project), "--audit-instructions", *args])
    stderr = getattr(result, "stderr", "") or ""
    return result.exit_code, result.output + stderr


def _install_success_boundaries(monkeypatch: Any) -> None:
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["brew", "tap-info", "--json"]:
            return subprocess.CompletedProcess(args, 0, stdout='[{"formulae":[{"version":"0.3.0"}]}]', stderr="")
        if args[:3] == ["gh", "release", "list"]:
            return subprocess.CompletedProcess(args, 0, stdout='[{"tagName":"v0.3.0"}]', stderr="")
        raise AssertionError(f"unexpected subprocess: {args}")

    def fake_urlopen(request: Any, timeout: int) -> _Response:
        assert timeout == 5
        return _Response(200)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def test_clean_project_no_findings_prints_summary(tmp_path: Path, monkeypatch: Any) -> None:
    sibling = tmp_path / "repos" / "touchstone"
    sibling.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_config(
        tmp_path,
        """
[audit-instructions]
homebrew_tap = "autumngarage/cortex"
siblings = ["~/repos/touchstone"]
pypi_package = "cortex-cli"
github_repos = ["autumngarage/cortex"]
urls = ["https://github.com/autumngarage/cortex/releases"]
""",
    )
    (tmp_path / "CLAUDE.md").write_text("Current release is v0.3.0.\n")
    (tmp_path / "README.md").write_text("See https://github.com/autumngarage/cortex/releases\n")
    _install_success_boundaries(monkeypatch)

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert output == "audit-instructions: checked 5 claims, all verified\n"


def test_stale_homebrew_formula_version_reports_source_line(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _write_config(tmp_path, '[audit-instructions]\nhomebrew_tap = "autumngarage/cortex"\n')
    (tmp_path / "CLAUDE.md").write_text("Install v0.2.5 from the tap.\n")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/brew" if name == "brew" else None)
    monkeypatch.setattr(
        "subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, stdout='[{"formulae":[{"version":"0.3.0"}]}]', stderr=""
        ),
    )

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert "homebrew formula version mismatch" in output
    assert "CLAUDE.md mentions v0.2.5, latest is v0.3.0" in output
    assert "(CLAUDE.md:1)" in output


def test_missing_sibling_reports_reference_line(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_config(tmp_path, '[audit-instructions]\nsiblings = ["~/repos/missing"]\n')
    (tmp_path / "CLAUDE.md").write_text("Sibling lives at ~/repos/missing.\n")

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert "filesystem sibling: ~/repos/missing missing" in output
    assert "(CLAUDE.md:1)" in output


def test_discovery_mode_audits_discovered_sibling(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "repos" / "touchstone").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("Coordinate with ~/repos/touchstone.\n")

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert output == "audit-instructions: checked 1 claims, all verified\n"


def test_url_404_warns(tmp_path: Path, monkeypatch: Any) -> None:
    _write_config(tmp_path, '[audit-instructions]\nurls = ["https://example.invalid/missing"]\n')

    def fake_urlopen(request: Any, timeout: int) -> _Response:
        return _Response(404)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert "url: https://example.invalid/missing returned 404" in output


def test_strict_exits_1_on_warning(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_config(tmp_path, '[audit-instructions]\nsiblings = ["~/repos/missing"]\n')

    exit_code, output = _run(tmp_path, "--strict")

    assert exit_code == 1
    assert "1 warning" in output


def test_json_shape_parses(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_config(tmp_path, '[audit-instructions]\nsiblings = ["~/repos/missing"]\n')

    exit_code, output = _run(tmp_path, "--json")

    assert exit_code == 0
    payload = json.loads(output)
    assert payload["checked"] == 1
    assert payload["warnings"] == 1
    assert payload["findings"][0]["level"] == "warning"


def test_always_prints_summary_for_zero_claims(tmp_path: Path) -> None:
    _write_config(tmp_path, "[audit-instructions]\nscan_files = []\n")

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert output == "audit-instructions: checked 0 claims, all verified\n"


def test_network_timeout_warns_without_crashing(tmp_path: Path, monkeypatch: Any) -> None:
    _write_config(tmp_path, '[audit-instructions]\nurls = ["https://example.invalid/slow"]\n')

    def fake_urlopen(_request: Any, timeout: int) -> _Response:
        raise TimeoutError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert "check failed: timed out" in output


def test_brew_and_gh_absent_degrade_gracefully(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("PATH", "")
    _write_config(
        tmp_path,
        """
[audit-instructions]
homebrew_tap = "autumngarage/cortex"
github_repos = ["autumngarage/cortex"]
""",
    )

    exit_code, output = _run(tmp_path)

    assert exit_code == 0
    assert "brew not installed, skipping homebrew_tap check" in output
    assert "gh not installed, skipping github_repos checks" in output
