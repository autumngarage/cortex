"""Project-local Cortex configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_AUDIT_SCAN_FILES = ["CLAUDE.md", "AGENTS.md", "README.md"]


@dataclass(frozen=True)
class AuditInstructionsConfig:
    """Configuration for `cortex doctor --audit-instructions`."""

    homebrew_tap: str | None = None
    siblings: tuple[str, ...] = ()
    pypi_package: str | None = None
    github_repos: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    scan_files: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_AUDIT_SCAN_FILES))
    discovery_mode: bool = True
    warnings: tuple[str, ...] = ()


def load_audit_instructions_config(project_root: Path) -> AuditInstructionsConfig:
    """Load `.cortex/config.toml`'s `[audit-instructions]` section.

    Missing config, malformed config, and absent section all degrade to
    discovery mode. Doctor surfaces broken claims found in content; it does
    not warn merely because a project has not opted into explicit config.
    """

    path = project_root / ".cortex" / "config.toml"
    if not path.is_file():
        return AuditInstructionsConfig()

    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        return AuditInstructionsConfig(warnings=(f"could not read .cortex/config.toml: {exc}",))
    except tomllib.TOMLDecodeError as exc:
        return AuditInstructionsConfig(warnings=(f"could not parse .cortex/config.toml: {exc}",))

    raw = data.get("audit-instructions")
    if not isinstance(raw, dict):
        return AuditInstructionsConfig()

    scan_files = _string_tuple(raw.get("scan_files"), default=tuple(DEFAULT_AUDIT_SCAN_FILES))
    return AuditInstructionsConfig(
        homebrew_tap=_optional_string(raw.get("homebrew_tap")),
        siblings=_string_tuple(raw.get("siblings")),
        pypi_package=_optional_string(raw.get("pypi_package")),
        github_repos=_string_tuple(raw.get("github_repos")),
        urls=_string_tuple(raw.get("urls")),
        scan_files=scan_files,
        discovery_mode=False,
    )


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_tuple(value: Any, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else default
    if not isinstance(value, list):
        return default
    items: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
    return tuple(items)
