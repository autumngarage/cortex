"""Deterministic diff → changed-surface extraction (cortex#363).

Parses a unified diff into the structural ``ChangedSurface`` that
``DecisionsForDiffQuery.from_diff_metadata`` consumes — the missing input
between a raw PR diff and the shipped diff-scoped retrieval path. One
extraction path: the GitHub reviewer (#389) and the local evaluator both
feed this function; neither grows its own parser.

Extraction is purely lexical and deterministic: no model calls, no
filesystem access, no git invocation. What cannot be extracted from patch
text alone (globs, owners, services, channel refs) is left empty for the
caller to supply; that omission is part of the contract, not a silent gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cortex.hosted.scopes import ChangedSurface

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>.+) b/(?P<b>.+)$")
_MINUS_FILE_RE = re.compile(r"^--- (?:a/(?P<path>.+)|/dev/null)$")
_PLUS_FILE_RE = re.compile(r"^\+\+\+ (?:b/(?P<path>.+)|/dev/null)$")
_RENAME_FROM_RE = re.compile(r"^rename from (?P<path>.+)$")
_RENAME_TO_RE = re.compile(r"^rename to (?P<path>.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(?P<context>.*)$")

_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_PY_CLASS_RE = re.compile(r"^\s*class\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
_PY_IMPORT_RE = re.compile(r"^\s*import\s+(?P<module>[A-Za-z_][A-Za-z0-9_.]*)")
_PY_FROM_IMPORT_RE = re.compile(r"^\s*from\s+(?P<module>[A-Za-z_][A-Za-z0-9_.]*)\s+import\s")

_TOML_INI_KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)\s*=")
_YAML_JSON_KEY_RE = re.compile(r"^\s*[\"']?(?P<key>[A-Za-z_][A-Za-z0-9_.-]*)[\"']?\s*:")
_ENV_KEY_RE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)=")

_ISSUE_REF_RE = re.compile(r"(?<![A-Za-z0-9_])(?:(?P<repo>[\w.-]+/[\w.-]+))?#(?P<number>\d+)\b")

_CONFIG_SUFFIXES_TOML_INI = (".toml", ".ini", ".cfg")
_CONFIG_SUFFIXES_YAML_JSON = (".yaml", ".yml", ".json")
_PYTHON_SUFFIXES = (".py",)


class DiffSurfaceValidationError(ValueError):
    """Raised when patch text cannot be parsed into a changed surface."""


@dataclass(frozen=True)
class _FileState:
    """Parsing state for the file section currently being walked."""

    path: str
    is_python: bool
    is_toml_ini: bool
    is_yaml_json: bool
    is_env: bool


def extract_changed_surface(patch_text: str) -> ChangedSurface:
    """Extract the structural surface from a unified diff.

    Returns a ``ChangedSurface`` carrying changed paths (old and new names
    for renames), Python symbols touched (defs/classes on changed lines and
    in hunk-header context), imported packages added or removed, config
    keys on changed lines of config-shaped files, and issue refs mentioned
    on added lines.
    """

    if not patch_text.strip():
        raise DiffSurfaceValidationError(
            "patch text is empty; refusing to return a silently empty surface"
        )

    paths: list[str] = []
    symbols: list[str] = []
    packages: list[str] = []
    config_keys: list[str] = []
    issue_refs: list[str] = []
    saw_file_header = False

    current: _FileState | None = None

    for line in patch_text.splitlines():
        git_match = _DIFF_GIT_RE.match(line)
        if git_match:
            saw_file_header = True
            for path in (git_match.group("a"), git_match.group("b")):
                _append_unique(paths, path)
            current = _file_state(git_match.group("b"))
            continue

        rename_from = _RENAME_FROM_RE.match(line)
        if rename_from:
            _append_unique(paths, rename_from.group("path"))
            continue
        rename_to = _RENAME_TO_RE.match(line)
        if rename_to:
            _append_unique(paths, rename_to.group("path"))
            current = _file_state(rename_to.group("path"))
            continue

        minus_file = _MINUS_FILE_RE.match(line)
        if minus_file:
            saw_file_header = True
            if minus_file.group("path"):
                _append_unique(paths, minus_file.group("path"))
                if current is None:
                    current = _file_state(minus_file.group("path"))
            continue
        plus_file = _PLUS_FILE_RE.match(line)
        if plus_file:
            saw_file_header = True
            if plus_file.group("path"):
                _append_unique(paths, plus_file.group("path"))
                current = _file_state(plus_file.group("path"))
            continue

        hunk = _HUNK_HEADER_RE.match(line)
        if hunk:
            context = hunk.group("context").strip()
            if context and current is not None and current.is_python:
                _collect_python_symbols(context, symbols)
            continue

        if not line or line[0] not in "+-" or line.startswith(("+++", "---")):
            continue
        if current is None:
            raise DiffSurfaceValidationError(
                "changed line appears before any file header; patch is not a "
                "unified diff"
            )

        content = line[1:]
        added = line[0] == "+"

        if current.is_python:
            _collect_python_symbols(content, symbols)
            import_match = _PY_IMPORT_RE.match(content) or _PY_FROM_IMPORT_RE.match(content)
            if import_match:
                root = import_match.group("module").split(".")[0]
                _append_unique(packages, root)

        if current.is_toml_ini:
            key = _TOML_INI_KEY_RE.match(content)
            if key:
                _append_unique(config_keys, key.group("key"))
        elif current.is_yaml_json:
            key = _YAML_JSON_KEY_RE.match(content)
            if key:
                _append_unique(config_keys, key.group("key"))
        elif current.is_env:
            key = _ENV_KEY_RE.match(content)
            if key:
                _append_unique(config_keys, key.group("key"))

        if added:
            for match in _ISSUE_REF_RE.finditer(content):
                repo = match.group("repo")
                ref = f"{repo}#{match.group('number')}" if repo else f"#{match.group('number')}"
                _append_unique(issue_refs, ref)

    if not saw_file_header:
        raise DiffSurfaceValidationError(
            "no file headers found; patch is not a unified diff"
        )

    return ChangedSurface(
        paths=tuple(paths),
        symbols=tuple(symbols),
        packages=tuple(packages),
        config_keys=tuple(config_keys),
        issue_refs=tuple(issue_refs),
    )


def _file_state(path: str) -> _FileState:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    return _FileState(
        path=path,
        is_python=lower.endswith(_PYTHON_SUFFIXES),
        is_toml_ini=lower.endswith(_CONFIG_SUFFIXES_TOML_INI),
        is_yaml_json=lower.endswith(_CONFIG_SUFFIXES_YAML_JSON),
        is_env=name.startswith(".env") or name.endswith(".env"),
    )


def _collect_python_symbols(content: str, symbols: list[str]) -> None:
    for pattern in (_PY_DEF_RE, _PY_CLASS_RE):
        match = pattern.match(content)
        if match:
            _append_unique(symbols, match.group("name"))


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
