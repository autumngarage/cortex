"""Project-local Cortex configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

JournalT19Mode = Literal["stage", "post-merge-writer"]

DEFAULT_AUDIT_SCAN_FILES = ["CLAUDE.md", "AGENTS.md", "README.md"]


@dataclass(frozen=True)
class AuditInstructionsConfig:
    """Configuration for `cortex doctor --audit-instructions`."""

    homebrew_tap: str | None = None
    siblings: tuple[str, ...] = ()
    pypi_package: str | None = None
    github_repos: tuple[str, ...] = ()
    github_releases: tuple[str, ...] = ()
    paas_repos: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    scan_files: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_AUDIT_SCAN_FILES))
    discovery_mode: bool = True
    warnings: tuple[str, ...] = ()
    self_repo: str | None = None
    # fnmatch glob patterns for URLs known to return 403 to a bare HEAD/GET but
    # which render fine for real clients (image-Accept-gated badges, anonymous-
    # gated forums). A matching URL's 403 is downgraded to a visible "expected
    # 403 (allowlisted)" info line instead of a warning. Only 403 is suppressed;
    # any other non-2xx on an allowlisted URL still warns. Absent = today's
    # behavior (every non-2xx warns).
    expected_403: tuple[str, ...] = ()


@dataclass(frozen=True)
class JournalT19Config:
    """Configuration for T1.9 (`pr-merged`) journal automation."""

    mode: JournalT19Mode = "post-merge-writer"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefreshIndexConfig:
    """Configuration for `cortex refresh-index`."""

    candidate_patterns: tuple[str, ...] = ()
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
        github_releases=_string_tuple(raw.get("github_releases")),
        paas_repos=_string_tuple(raw.get("paas_repos")),
        urls=_string_tuple(raw.get("urls")),
        scan_files=scan_files,
        discovery_mode=False,
        self_repo=_optional_string(raw.get("self_repo")),
        expected_403=_string_tuple(raw.get("expected_403")),
    )


def load_journal_t19_config(project_root: Path) -> JournalT19Config:
    """Load ``[journal.t1_9]`` from ``.cortex/config.toml``.

    Absent config preserves the legacy post-merge writer hook so existing
    projects keep today's behavior until they opt into source-PR staging.
    """

    path = project_root / ".cortex" / "config.toml"
    if not path.is_file():
        return JournalT19Config()

    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        return JournalT19Config(warnings=(f"could not read .cortex/config.toml: {exc}",))
    except tomllib.TOMLDecodeError as exc:
        return JournalT19Config(warnings=(f"could not parse .cortex/config.toml: {exc}",))

    journal_raw = data.get("journal")
    if not isinstance(journal_raw, dict):
        return JournalT19Config()

    raw = journal_raw.get("t1_9")
    if not isinstance(raw, dict):
        return JournalT19Config()

    mode_raw = raw.get("mode")
    if mode_raw is None:
        return JournalT19Config()
    if mode_raw == "stage":
        return JournalT19Config(mode="stage")
    if mode_raw == "post-merge-writer":
        return JournalT19Config(mode="post-merge-writer")
    return JournalT19Config(
        warnings=(f"unknown journal.t1_9.mode {mode_raw!r}; using post-merge-writer",),
    )


def load_refresh_index_config(project_root: Path) -> RefreshIndexConfig:
    """Load `.cortex/config.toml`'s `[refresh-index]` section."""

    path = project_root / ".cortex" / "config.toml"
    if not path.is_file():
        return RefreshIndexConfig()

    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        return RefreshIndexConfig(warnings=(f"could not read .cortex/config.toml: {exc}",))
    except tomllib.TOMLDecodeError as exc:
        return RefreshIndexConfig(warnings=(f"could not parse .cortex/config.toml: {exc}",))

    raw = data.get("refresh-index")
    if not isinstance(raw, dict):
        return RefreshIndexConfig()

    return RefreshIndexConfig(candidate_patterns=_string_tuple(raw.get("candidate_patterns")))


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
