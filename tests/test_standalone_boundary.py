"""Standalone-boundary guardrail (cortex#503).

Cortex is a standalone product. Touchstone, Sentinel, and Conductor are
siblings/consumers that compose with it through marker files and on-disk
artifacts — the dependency arrow points *toward* cortex, never from it
(Doctrine 0002, `.cortex/doctrine/0002-compose-by-file-contract-not-code.md`).

This module enforces the boundary mechanically so it cannot erode one
convenient import at a time:

1. No quartet package is imported anywhere under ``src/cortex/**``.
2. No quartet binary is invoked via ``subprocess``/``os.system`` outside the
   explicitly-allowed detection surface (``src/cortex/siblings.py``), and that
   detection surface degrades gracefully when the binary is absent.
3. ``conductor`` is never a subprocess target anywhere in src — cortex
   synthesis shells out to the ``claude`` CLI directly (same boundary family;
   ``claude`` invocations are allowed and not flagged here).
4. Packaging metadata declares no quartet distribution as a dependency
   (mirrors the brew ``depends_on`` rule from cortex#272).

The scan is static (AST + TOML parsing only); dynamic command construction is
out of scope by design — the literal-command check plus the import check cover
the coupling shapes Doctrine 0002 forbids.
"""

from __future__ import annotations

import ast
import functools
import re
import shlex
import tomllib
from pathlib import Path

from cortex.siblings import detect_sibling, format_sibling_block

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "cortex"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Quartet distributions/binaries that must never become code dependencies.
QUARTET_NAMES = frozenset({"touchstone", "sentinel", "conductor", "alchemist"})

# The only module allowed to *detect* sibling CLIs (presence + version probe,
# never functional output). Doctrine 0002 names this exact pattern as the
# permitted composition surface.
DETECTION_SURFACE = SRC_ROOT / "siblings.py"

# Siblings the detection surface may probe. `conductor` and `alchemist` are
# deliberately excluded: cortex synthesis uses the `claude` CLI directly and
# never routes through conductor (Doctrine 0002 decision + cortex#272).
DETECTABLE_SIBLINGS = frozenset({"touchstone", "sentinel"})

# Callable names that execute external commands when reached through the
# `subprocess` or `os` modules (or imported bare from them).
_EXEC_CALLABLE_NAMES = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
        "system",
        "popen",
        "execv",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnlp",
        "spawnv",
        "spawnvp",
    }
)

_DOCTRINE_CITATION = (
    "Doctrine 0002 — 'compose by file contract, not code' "
    "(.cortex/doctrine/0002-compose-by-file-contract-not-code.md): cortex is a "
    "standalone product. Siblings compose with cortex by reading marker files and "
    "on-disk artifacts; cortex never imports their code, never requires their "
    "binaries, and never packages them as dependencies. Detection of sibling CLIs "
    "is confined to src/cortex/siblings.py and must degrade gracefully when the "
    "binary is absent. Synthesis shells out to the `claude` CLI directly, never "
    "through conductor (cortex#272)."
)


def _boundary_failure(summary: str, violations: list[str]) -> str:
    listed = "\n".join(f"  - {violation}" for violation in violations)
    return f"{summary}\n{listed}\n{_DOCTRINE_CITATION}"


@functools.cache
def _parsed_src_modules() -> tuple[tuple[Path, ast.Module], ...]:
    """Parse every Python module under src/cortex exactly once.

    Fails closed: an empty scan surface means the path layout changed and the
    guardrail is no longer guarding anything — that must be a loud failure,
    not a vacuously green suite.
    """
    paths = sorted(SRC_ROOT.rglob("*.py"))
    assert paths, (
        f"standalone-boundary scan found no Python modules under {SRC_ROOT}; "
        "the guardrail surface moved and this test must be updated. "
        + _DOCTRINE_CITATION
    )
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in paths
    )


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _execution_call_sites(tree: ast.Module) -> list[ast.Call]:
    """Collect Call nodes that execute external commands via subprocess/os."""
    module_aliases: set[str] = set()
    bare_exec_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"subprocess", "os"}:
                    module_aliases.add(alias.asname or alias.name)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module in {"subprocess", "os"}
        ):
            for alias in node.names:
                if alias.name in _EXEC_CALLABLE_NAMES:
                    bare_exec_names.add(alias.asname or alias.name)

    sites: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in _EXEC_CALLABLE_NAMES
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        ) or (isinstance(func, ast.Name) and func.id in bare_exec_names):
            sites.append(node)
    return sites


def _command_head(call: ast.Call) -> str | None:
    """Statically resolve the binary a subprocess call targets, when literal.

    Returns the basename of the command token, or None when the command is
    built dynamically (e.g. siblings.py passes the path returned by
    ``shutil.which``) — dynamic commands are out of scope for this static
    check; the import check and the literal check together cover the
    coupling shapes the doctrine forbids.
    """
    command: ast.expr | None = call.args[0] if call.args else None
    if command is None:
        for keyword in call.keywords:
            if keyword.arg == "args":
                command = keyword.value
                break
    if command is None:
        return None

    head: str | None = None
    if isinstance(command, ast.List | ast.Tuple) and command.elts:
        first = command.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            head = first.value
    elif isinstance(command, ast.Constant) and isinstance(command.value, str):
        try:
            tokens = shlex.split(command.value)
        except ValueError:
            tokens = command.value.split()
        head = tokens[0] if tokens else None
    elif isinstance(command, ast.JoinedStr) and command.values:
        first_part = command.values[0]
        if isinstance(first_part, ast.Constant) and isinstance(first_part.value, str):
            tokens = first_part.value.split()
            head = tokens[0] if tokens else None

    if head is None:
        return None
    return Path(head).name.lower()


def _literal_subprocess_targets() -> list[tuple[Path, int, str]]:
    """All (module, line, binary-basename) literal subprocess targets in src."""
    targets: list[tuple[Path, int, str]] = []
    execution_sites_seen = 0
    for path, tree in _parsed_src_modules():
        for call in _execution_call_sites(tree):
            execution_sites_seen += 1
            head = _command_head(call)
            if head is not None:
                targets.append((path, call.lineno, head))
    # Fail closed: src is known to shell out (git via shell.py, sibling
    # version probes via siblings.py). Zero detected execution sites means
    # the scanner regressed, not that the codebase stopped shelling out.
    assert execution_sites_seen > 0, (
        "standalone-boundary scanner found zero subprocess/os execution sites "
        "under src/cortex — the call-site detection logic regressed and the "
        "guardrail is vacuous. " + _DOCTRINE_CITATION
    )
    return targets


def test_no_quartet_imports_in_src_modules() -> None:
    violations: list[str] = []
    for path, tree in _parsed_src_modules():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in QUARTET_NAMES:
                        violations.append(
                            f"{_relative(path)}:{node.lineno} imports {alias.name!r}"
                        )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module is not None
                and node.module.split(".")[0] in QUARTET_NAMES
            ):
                violations.append(
                    f"{_relative(path)}:{node.lineno} imports from {node.module!r}"
                )
    assert not violations, _boundary_failure(
        "Quartet package imported inside src/cortex:", violations
    )


def test_no_quartet_binary_invocation_outside_detection_surface() -> None:
    violations = [
        f"{_relative(path)}:{lineno} invokes {head!r}"
        for path, lineno, head in _literal_subprocess_targets()
        if head in QUARTET_NAMES
        and not (path == DETECTION_SURFACE and head in DETECTABLE_SIBLINGS)
    ]
    assert not violations, _boundary_failure(
        "Quartet binary invoked outside the allowed detection surface "
        f"({_relative(DETECTION_SURFACE)}):",
        violations,
    )


def test_conductor_is_never_a_subprocess_target_anywhere_in_src() -> None:
    # Stricter than the detection-surface rule: conductor (and alchemist) may
    # not even be *detected*. Cortex synthesis invokes the `claude` CLI
    # directly; routing through conductor would add a quartet runtime
    # dependency the brew formula must never declare (cortex#272).
    violations = [
        f"{_relative(path)}:{lineno} invokes {head!r}"
        for path, lineno, head in _literal_subprocess_targets()
        if head in QUARTET_NAMES - DETECTABLE_SIBLINGS
    ]
    assert not violations, _boundary_failure(
        "conductor/alchemist invoked from src (claude-CLI synthesis rule):",
        violations,
    )


def test_sibling_detection_degrades_gracefully_when_binary_absent(tmp_path: Path) -> None:
    # Real call, no mocking: the binary name cannot exist on PATH and the
    # marker cannot exist under tmp_path, so this exercises the genuine
    # absent-sibling path end to end.
    status = detect_sibling(
        "cortex-test-binary-that-must-not-exist",
        project_marker=".cortex-test-marker-that-must-not-exist",
        cwd=tmp_path,
    )
    assert status.cli_path is None and status.version is None, _boundary_failure(
        "Absent sibling binary must report clean absence, not a probe result:",
        [f"cli_path={status.cli_path!r} version={status.version!r}"],
    )
    assert status.project_marker_present is False, _boundary_failure(
        "Absent sibling marker must report absence:",
        [f"project_marker_present={status.project_marker_present!r}"],
    )
    block = format_sibling_block([status])
    assert "not installed" in block, _boundary_failure(
        "Absent sibling must render as informational absence (never an error):",
        [block],
    )


def _distribution_name(requirement: str) -> str:
    match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    assert match is not None, _boundary_failure(
        "Unparseable requirement in pyproject.toml:", [repr(requirement)]
    )
    # PEP 503 normalization so e.g. "Conductor" or "conductor_cli" variants
    # of an exact quartet name cannot slip past a string-equality check.
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def test_no_quartet_packages_in_packaging_metadata() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    surfaces: dict[str, list[str]] = {
        "project.dependencies": list(data["project"]["dependencies"]),
    }
    for group, requirements in data["project"].get("optional-dependencies", {}).items():
        surfaces[f"project.optional-dependencies.{group}"] = list(requirements)
    for group, requirements in data.get("dependency-groups", {}).items():
        # PEP 735 entries may be {include-group = ...} tables; only
        # requirement strings name distributions.
        surfaces[f"dependency-groups.{group}"] = [
            requirement for requirement in requirements if isinstance(requirement, str)
        ]

    violations = [
        f"{surface}: {requirement!r}"
        for surface, requirements in surfaces.items()
        for requirement in requirements
        if _distribution_name(requirement) in QUARTET_NAMES
    ]
    assert not violations, _boundary_failure(
        "Quartet distribution declared as a packaging dependency:", violations
    )
