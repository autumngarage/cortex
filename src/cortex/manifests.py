"""Project dependency manifest detection helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ManifestInfo:
    filename: str
    detail: str | None = None
    error: str | None = None


def detect_project_manifest(project_root: Path) -> ManifestInfo | None:
    """Return the first recognized project manifest in priority order."""
    root = project_root.resolve()
    for filename, extractor in (
        ("pyproject.toml", _extract_toml_version_or_name),
        ("package.json", _extract_package_json_version_or_name),
        ("Cargo.toml", _extract_toml_version_or_name),
        ("go.mod", _extract_go_module),
        ("Gemfile", _extract_none),
        ("Package.swift", _extract_swift_package_name),
        ("pom.xml", _extract_none),
        ("build.gradle", _extract_none),
        ("build.gradle.kts", _extract_none),
    ):
        path = root / filename
        if not path.exists():
            continue
        try:
            text = path.read_text()
        except OSError as exc:
            return ManifestInfo(filename=filename, error=str(exc))
        return ManifestInfo(filename=filename, detail=extractor(text))
    return None


def _extract_toml_version_or_name(text: str) -> str | None:
    return _first_match(text, r'^version\s*=\s*"([^"]+)"\s*$', r'^name\s*=\s*"([^"]+)"\s*$')


def _extract_package_json_version_or_name(text: str) -> str | None:
    return _first_match(text, r'"version"\s*:\s*"([^"]+)"', r'"name"\s*:\s*"([^"]+)"')


def _extract_go_module(text: str) -> str | None:
    return _first_match(text, r"^module\s+(\S+)\s*$")


def _extract_swift_package_name(text: str) -> str | None:
    package_match = re.search(r"\bPackage\s*\(", text)
    if not package_match:
        return None
    name_match = re.search(r'\bname\s*:\s*"([^"]+)"', text[package_match.end() :])
    return name_match.group(1) if name_match else None


def _extract_none(_text: str) -> str | None:
    return None


def _first_match(text: str, *patterns: str) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            return match.group(1)
    return None
