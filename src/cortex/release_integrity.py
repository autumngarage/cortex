"""Release metadata integrity checks.

The release artifact is a git ref. Before Homebrew sees that ref, verify the
package metadata inside the ref matches the version implied by the tag.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReleaseMetadata:
    ref: str
    pyproject_version: str
    init_version: str


def read_ref_metadata(project_root: Path, ref: str) -> ReleaseMetadata:
    """Read release metadata from ``ref`` without checking it out."""

    return ReleaseMetadata(
        ref=ref,
        pyproject_version=_pyproject_version(_git_show(project_root, ref, "pyproject.toml")),
        init_version=_init_version(_git_show(project_root, ref, "src/cortex/__init__.py")),
    )


def release_integrity_errors(metadata: ReleaseMetadata, expected_version: str) -> list[str]:
    errors: list[str] = []
    if metadata.pyproject_version != expected_version:
        errors.append(
            f"{metadata.ref}: pyproject.toml version is {metadata.pyproject_version}, "
            f"expected {expected_version}"
        )
    if metadata.init_version != expected_version:
        errors.append(
            f"{metadata.ref}: src/cortex/__init__.py __version__ is {metadata.init_version}, "
            f"expected {expected_version}"
        )
    return errors


def _git_show(project_root: Path, ref: str, path: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_root), "show", f"{ref}:{path}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise RuntimeError(f"could not read {path} at {ref}: {detail}")
    return result.stdout


def _pyproject_version(text: str) -> str:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"pyproject.toml is invalid TOML: {exc}") from exc
    project = data.get("project")
    if not isinstance(project, dict):
        raise ValueError("pyproject.toml missing [project] table")
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("pyproject.toml [project].version must be a non-empty string")
    return version


def _init_version(text: str) -> str:
    try:
        module = ast.parse(text)
    except SyntaxError as exc:
        raise ValueError(f"src/cortex/__init__.py is invalid Python: {exc}") from exc
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
        raise ValueError("src/cortex/__init__.py __version__ must be a string literal")
    raise ValueError("src/cortex/__init__.py missing __version__")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="verify Cortex release metadata at a git ref")
    parser.add_argument("ref", help="git ref or commit SHA to inspect")
    parser.add_argument("expected_version", help="expected package version, without leading v")
    parser.add_argument("--repo", type=Path, default=Path("."), help="repository root")
    args = parser.parse_args(argv)

    try:
        metadata = read_ref_metadata(args.repo, args.ref)
        errors = release_integrity_errors(metadata, args.expected_version)
    except (RuntimeError, ValueError) as exc:
        print(f"release integrity check failed: {exc}", file=sys.stderr)
        return 1

    if errors:
        for error in errors:
            print(f"release integrity check failed: {error}", file=sys.stderr)
        return 1

    print(
        "release integrity OK: "
        f"{args.ref} contains package metadata {args.expected_version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
