"""External-claim audit for `cortex doctor --audit-instructions`."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cortex.config import load_audit_instructions_config

NETWORK_TIMEOUT_SECONDS = 5
SUBPROCESS_TIMEOUT_SECONDS = 8
VERSION_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
SIBLING_RE = re.compile(r"(?<![\w/])~/(?:[Rr]epos)/[A-Za-z0-9._-]+")
URL_RE = re.compile(r"https?://[^\s<>'\")\]]+")


@dataclass(frozen=True)
class TextLocation:
    path: Path
    line_number: int

    def render(self, project_root: Path) -> str:
        try:
            rel = self.path.relative_to(project_root)
        except ValueError:
            rel = self.path
        return f"{rel}:{self.line_number}"


@dataclass(frozen=True)
class Finding:
    level: str
    message: str
    source_file: Path | None = None
    line_number: int | None = None

    def to_json(self, project_root: Path) -> dict[str, Any]:
        source = None
        if self.source_file is not None:
            try:
                source = str(self.source_file.relative_to(project_root))
            except ValueError:
                source = str(self.source_file)
        return {
            "level": self.level,
            "message": self.message,
            "source_file": source,
            "line_number": self.line_number,
        }


@dataclass(frozen=True)
class ScanResult:
    files: tuple[Path, ...]
    text_by_file: dict[Path, str]
    sibling_refs: dict[str, tuple[TextLocation, ...]]
    url_refs: dict[str, tuple[TextLocation, ...]]
    version_refs: dict[str, tuple[TextLocation, ...]]


@dataclass(frozen=True)
class AuditInstructionsReport:
    checked: int
    findings: tuple[Finding, ...]

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.level == "warning")

    def to_json(self, project_root: Path) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "warnings": len(self.warnings),
            "findings": [finding.to_json(project_root) for finding in self.findings],
        }


def audit_instructions(project_root: Path) -> AuditInstructionsReport:
    """Scan instruction files and verify configured/discovered external claims."""

    project_root = project_root.resolve()
    config = load_audit_instructions_config(project_root)
    scan = scan_instruction_files(project_root, config.scan_files)

    findings: list[Finding] = []
    checked = 0
    findings.extend(Finding("warning", warning) for warning in config.warnings)

    sibling_claims = _merge_claim_locations(config.siblings, scan.sibling_refs)
    sibling_findings = audit_filesystem_siblings(sibling_claims)
    checked += len(sibling_claims)
    findings.extend(sibling_findings)

    url_claims = _merge_claim_locations(config.urls, scan.url_refs)
    network_tasks: list[tuple[str, str, tuple[TextLocation, ...]]] = [
        ("url", url, locations) for url, locations in url_claims.items()
    ]
    checked += len(url_claims)

    if config.pypi_package:
        pypi_url = f"https://pypi.org/simple/{config.pypi_package}/"
        network_tasks.append(("pypi", pypi_url, ()))
        checked += 1

    findings.extend(_run_network_checks(network_tasks, config.pypi_package))

    if config.homebrew_tap:
        checked += 1
        findings.extend(audit_homebrew_tap(config.homebrew_tap, scan))

    if config.github_repos:
        checked += len(config.github_repos)
        findings.extend(audit_github_releases(config.github_repos, scan))

    return AuditInstructionsReport(checked=checked, findings=tuple(findings))


def scan_instruction_files(project_root: Path, scan_files: tuple[str, ...]) -> ScanResult:
    text_by_file: dict[Path, str] = {}
    sibling_refs: dict[str, list[TextLocation]] = {}
    url_refs: dict[str, list[TextLocation]] = {}
    version_refs: dict[str, list[TextLocation]] = {}

    files: list[Path] = []
    for rel in scan_files:
        path = project_root / rel
        if not path.is_file():
            continue
        files.append(path)
        text = path.read_text(errors="replace")
        text_by_file[path] = text
        for line_number, line in enumerate(text.splitlines(), start=1):
            for raw in SIBLING_RE.findall(line):
                sibling = raw.rstrip(".,;:")
                sibling_refs.setdefault(sibling, []).append(TextLocation(path, line_number))
            for raw in URL_RE.findall(line):
                url = raw.rstrip(".,;:")
                url_refs.setdefault(url, []).append(TextLocation(path, line_number))
            for version in VERSION_RE.findall(line):
                version_refs.setdefault(version, []).append(TextLocation(path, line_number))

    return ScanResult(
        files=tuple(files),
        text_by_file=text_by_file,
        sibling_refs={key: tuple(value) for key, value in sibling_refs.items()},
        url_refs={key: tuple(value) for key, value in url_refs.items()},
        version_refs={key: tuple(value) for key, value in version_refs.items()},
    )


def audit_filesystem_siblings(siblings: dict[str, tuple[TextLocation, ...]]) -> list[Finding]:
    findings: list[Finding] = []
    for raw, locations in sorted(siblings.items()):
        expanded = Path(os.path.expanduser(raw))
        if expanded.is_dir():
            findings.append(Finding("ok", f"filesystem sibling: {expanded} exists"))
            continue
        location = locations[0] if locations else None
        findings.append(_warning(f"filesystem sibling: {raw} missing", location))
    return findings


def audit_homebrew_tap(tap: str, scan: ScanResult) -> list[Finding]:
    if shutil.which("brew") is None:
        return [Finding("warning", "homebrew tap: brew not installed, skipping homebrew_tap check")]

    try:
        completed = subprocess.run(
            ["brew", "tap-info", "--json", tap],
            check=False,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [Finding("warning", f"homebrew tap: {tap} check failed: {exc}")]

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        return [Finding("warning", f"homebrew tap: {tap} unavailable ({detail})")]

    try:
        payload = json.loads(completed.stdout or "null")
    except json.JSONDecodeError as exc:
        return [Finding("warning", f"homebrew tap: {tap} returned invalid JSON: {exc}")]

    version = _find_first_version(payload)
    findings = [Finding("ok", f"homebrew tap: {tap}" + (f" (formula at {version})" if version else ""))]
    if version:
        findings.extend(_version_mismatch_findings("homebrew formula version mismatch", version, scan))
    return findings


def audit_github_releases(repos: tuple[str, ...], scan: ScanResult) -> list[Finding]:
    if shutil.which("gh") is None:
        return [Finding("warning", "gh not installed, skipping github_repos checks")]

    findings: list[Finding] = []
    for repo in repos:
        try:
            completed = subprocess.run(
                ["gh", "release", "list", "--repo", repo, "--limit", "1", "--json", "tagName"],
                check=False,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            findings.append(Finding("warning", f"github release: {repo} check failed: {exc}"))
            continue
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
            findings.append(Finding("warning", f"github release: {repo} unavailable ({detail})"))
            continue
        try:
            payload = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            findings.append(Finding("warning", f"github release: {repo} returned invalid JSON: {exc}"))
            continue
        tag = _github_tag_from_payload(payload)
        if tag is None:
            findings.append(Finding("warning", f"github release: {repo} returned no release tag"))
            continue
        findings.append(Finding("ok", f"github release: {repo} latest is {tag}"))
        findings.extend(_version_mismatch_findings("github release version mismatch", tag, scan))
    return findings


def _run_network_checks(
    tasks: list[tuple[str, str, tuple[TextLocation, ...]]],
    pypi_package: str | None,
) -> list[Finding]:
    if not tasks:
        return []

    findings: list[Finding] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {
            pool.submit(_head_status, url): (kind, url, locations) for kind, url, locations in tasks
        }
        for future in as_completed(future_map):
            kind, url, locations = future_map[future]
            status, error = future.result()
            label = f"pypi package: {pypi_package}" if kind == "pypi" else f"url: {url}"
            if status is not None and 200 <= status < 400:
                findings.append(Finding("ok", f"{label} ({status})"))
                continue
            location = locations[0] if locations else None
            if status is not None:
                findings.append(_warning(f"{label} returned {status}", location))
            else:
                findings.append(_warning(f"{label} check failed: {error}", location))
    return findings


def _head_status(url: str) -> tuple[int | None, str | None]:
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            return int(response.status), None
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except Exception as exc:
        return None, str(exc)


def _version_mismatch_findings(label: str, latest: str, scan: ScanResult) -> list[Finding]:
    latest_normalized = latest if latest.startswith("v") else f"v{latest}"
    findings: list[Finding] = []
    for version, locations in sorted(scan.version_refs.items()):
        if version == latest_normalized:
            continue
        for location in locations:
            findings.append(
                _warning(
                    f"{label}: {location.path.name} mentions {version}, latest is {latest_normalized}",
                    location,
                )
            )
    return findings


def _github_tag_from_payload(payload: Any) -> str | None:
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            tag = first.get("tagName")
            if isinstance(tag, str):
                return tag
    if isinstance(payload, dict):
        tag = payload.get("tagName")
        if isinstance(tag, str):
            return tag
    return None


def _find_first_version(payload: Any) -> str | None:
    if isinstance(payload, str):
        match = VERSION_RE.search(payload)
        if match:
            return match.group(0)
        bare = re.search(r"\b\d+\.\d+\.\d+\b", payload)
        return f"v{bare.group(0)}" if bare else None
    if isinstance(payload, dict):
        for key in ("version", "versions", "installed", "stable"):
            version = _find_first_version(payload.get(key))
            if version:
                return version
        for value in payload.values():
            version = _find_first_version(value)
            if version:
                return version
    if isinstance(payload, list):
        for value in payload:
            version = _find_first_version(value)
            if version:
                return version
    return None


def _merge_claim_locations(
    configured: tuple[str, ...], discovered: dict[str, tuple[TextLocation, ...]]
) -> dict[str, tuple[TextLocation, ...]]:
    merged: dict[str, tuple[TextLocation, ...]] = dict(discovered)
    for value in configured:
        merged.setdefault(value, ())
    return merged


def _warning(message: str, location: TextLocation | None) -> Finding:
    if location is None:
        return Finding("warning", message)
    return Finding("warning", message, source_file=location.path, line_number=location.line_number)


def format_audit_instructions_human(
    report: AuditInstructionsReport, project_root: Path, *, include_ok: bool = False
) -> str:
    """Render human output.

    Clean reports intentionally collapse to the final summary line. Warning
    reports include all findings so failures have enough context to fix.
    """

    warnings = report.warnings
    if not warnings and not include_ok:
        return _summary_line(report)

    lines = ["audit-instructions:"]
    for finding in report.findings:
        if finding.level == "ok" and not include_ok and warnings:
            continue
        glyph = "✓" if finding.level == "ok" else "⚠"
        suffix = ""
        if finding.source_file is not None and finding.line_number is not None:
            suffix = f" ({TextLocation(finding.source_file, finding.line_number).render(project_root)})"
        lines.append(f"  {glyph} {finding.message}{suffix}")
    lines.append(_summary_line(report))
    return "\n".join(lines)


def _summary_line(report: AuditInstructionsReport) -> str:
    warning_count = len(report.warnings)
    if warning_count == 0:
        return f"audit-instructions: checked {report.checked} claims, all verified"
    noun = "warning" if warning_count == 1 else "warnings"
    return f"audit-instructions: checked {report.checked} claims, {warning_count} {noun}"
