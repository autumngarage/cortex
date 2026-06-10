"""Vendor-SDK boundary lint (cortex#348).

cortex#344's invariant: business logic talks to models through the
``derive(...)``/``evaluate(...)`` boundary with **no vendor SDK types and no
model-name branching across it**. This suite is the guardrail
(``principles/audit-weak-points.md`` step 6) that keeps the invariant true by
machine check instead of code-review vigilance:

1. **No vendor SDK imports anywhere in ``src/cortex/``.** The only model
   transport this repo allows is the ``claude`` CLI via subprocess (plus
   recorded-response playback, cortex#347). When cortex#345 lands provider
   adapters, they get named in ``VENDOR_IMPORT_ALLOWED`` — an explicit,
   reviewable list, never inferred from directory layout.
2. **No vendor model-name literals in ``src/cortex/hosted/`` outside the
   registry/router/adapter modules.** A ``"claude-..."`` literal above the
   routing boundary is model-name branching — the smell #344 bans.

Both scanners are exercised against planted violations (hermetic tmp trees)
so the lint itself is provably red-capable.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_CORTEX = Path(__file__).resolve().parents[1] / "src" / "cortex"
HOSTED_DIR = SRC_CORTEX / "hosted"

BOUNDARY_RULE = (
    "violates the cortex#344 boundary rule (no vendor SDK types and no "
    "model-name branching cross the derive/evaluate boundary; enforced by "
    "cortex#348)"
)

# Vendor LLM SDK module roots. A match is the module itself or any submodule.
VENDOR_SDK_MODULES: tuple[str, ...] = (
    "anthropic",
    "openai",
    "google.genai",
    "google.generativeai",
    "cohere",
    "mistralai",
    "litellm",
)

# Module-name prefixes covering whole SDK families (langchain, langchain_core,
# langchain_openai, langchain_anthropic, ...).
VENDOR_SDK_PREFIXES: tuple[str, ...] = ("langchain",)

# The explicit allowed-import surface (cortex#348 acceptance criterion: a
# visible list, not directory-layout inference). No provider-adapter modules
# exist yet (#345), so today NOTHING in src/cortex/ may import a vendor SDK.
# Paths are POSIX-relative to the scanned root.
VENDOR_IMPORT_ALLOWED: frozenset[str] = frozenset()

# Markers that identify concrete vendor model names in string literals.
VENDOR_MODEL_NAME_MARKERS: tuple[str, ...] = (
    "claude-",
    "gpt-3",
    "gpt-4",
    "gpt-5",
    "gemini-",
    "mistral-",
    "mixtral-",
    "command-r",
)

# Hosted modules allowed to spell concrete model names: the registry owns what
# model ids mean; cortex#345's router/adapters join this list when they land.
# Paths are POSIX-relative to the scanned root (src/cortex/hosted/).
MODEL_NAME_LITERAL_ALLOWED: frozenset[str] = frozenset({"model_registry.py"})


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_is_vendor_sdk(module: str) -> bool:
    for banned in VENDOR_SDK_MODULES:
        if module == banned or module.startswith(banned + "."):
            return True
    for prefix in VENDOR_SDK_PREFIXES:
        if module == prefix or module.startswith((prefix + ".", prefix + "_")):
            return True
    return False


def vendor_import_violations(
    root: Path, allowed: frozenset[str] = VENDOR_IMPORT_ALLOWED
) -> list[str]:
    """AST-scan a tree for vendor LLM SDK imports outside the allowed list."""

    violations: list[str] = []
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in allowed:
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Import):
                violations.extend(
                    f"{relative}:{node.lineno} imports vendor SDK {alias.name!r} — "
                    f"{BOUNDARY_RULE}"
                    for alias in node.names
                    if _module_is_vendor_sdk(alias.name)
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module is not None
                and _module_is_vendor_sdk(node.module)
            ):
                violations.append(
                    f"{relative}:{node.lineno} imports from vendor SDK "
                    f"{node.module!r} — {BOUNDARY_RULE}"
                )
    return violations


def model_name_literal_violations(
    root: Path, allowed: frozenset[str] = MODEL_NAME_LITERAL_ALLOWED
) -> list[str]:
    """AST-scan a tree for vendor model-name string literals outside the allowed list."""

    violations: list[str] = []
    for path in _python_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in allowed:
            continue
        for node in ast.walk(_parse(path)):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            lowered = node.value.lower()
            violations.extend(
                f"{relative}:{node.lineno} string literal contains vendor "
                f"model-name marker {marker!r}; model-name branching above the "
                f"routing boundary {BOUNDARY_RULE}"
                for marker in VENDOR_MODEL_NAME_MARKERS
                if marker in lowered
            )
    return violations


# ---------------------------------------------------------------------------
# The lint itself
# ---------------------------------------------------------------------------


def test_src_cortex_imports_no_vendor_sdk() -> None:
    violations = vendor_import_violations(SRC_CORTEX)
    assert violations == [], "\n".join(violations)


def test_hosted_modules_spell_no_vendor_model_names() -> None:
    violations = model_name_literal_violations(HOSTED_DIR)
    assert violations == [], "\n".join(violations)


def test_allowed_lists_stay_inside_the_scanned_trees() -> None:
    """Allowlist entries must name real (or not-yet-created) in-tree modules,
    never paths outside the scanned roots."""

    for entry in VENDOR_IMPORT_ALLOWED | MODEL_NAME_LITERAL_ALLOWED:
        assert not entry.startswith(("/", "..")), entry


# ---------------------------------------------------------------------------
# Negative verification: the scanners provably catch planted violations
# ---------------------------------------------------------------------------


def test_import_scanner_catches_planted_violations(tmp_path: Path) -> None:
    (tmp_path / "bad_import.py").write_text("import openai\n", encoding="utf-8")
    (tmp_path / "bad_from.py").write_text(
        "from anthropic import Anthropic\n", encoding="utf-8"
    )
    (tmp_path / "bad_submodule.py").write_text(
        "import google.generativeai as genai\n", encoding="utf-8"
    )
    (tmp_path / "bad_family.py").write_text(
        "from langchain_openai import ChatOpenAI\n", encoding="utf-8"
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "bad_nested.py").write_text(
        "from openai.types.chat import ChatCompletion\n", encoding="utf-8"
    )
    (tmp_path / "clean.py").write_text(
        "import subprocess\nimport json\n", encoding="utf-8"
    )

    violations = vendor_import_violations(tmp_path)
    assert len(violations) == 5, "\n".join(violations)
    assert all("cortex#344" in violation for violation in violations)
    flagged_files = {violation.split(":")[0] for violation in violations}
    assert flagged_files == {
        "bad_import.py",
        "bad_from.py",
        "bad_submodule.py",
        "bad_family.py",
        "nested/bad_nested.py",
    }


def test_import_allowlist_is_explicit_configuration(tmp_path: Path) -> None:
    (tmp_path / "adapter.py").write_text("import anthropic\n", encoding="utf-8")
    assert vendor_import_violations(tmp_path) != []
    assert vendor_import_violations(tmp_path, allowed=frozenset({"adapter.py"})) == []


def test_literal_scanner_catches_planted_model_name_branching(tmp_path: Path) -> None:
    (tmp_path / "router_above_boundary.py").write_text(
        "def pick_lane(model_id: str) -> str:\n"
        '    if model_id.startswith("claude-"):\n'
        '        return "fast"\n'
        '    return "slow"\n',
        encoding="utf-8",
    )
    violations = model_name_literal_violations(tmp_path)
    assert len(violations) == 1, "\n".join(violations)
    assert "cortex#344" in violations[0]
    assert "'claude-'" in violations[0]
    assert violations[0].startswith("router_above_boundary.py:2")


def test_literal_allowlist_covers_only_named_modules(tmp_path: Path) -> None:
    spelled = 'DEFAULT_ROUTE = "anthropic/claude-fable-5"\n'
    (tmp_path / "model_registry.py").write_text(spelled, encoding="utf-8")
    assert model_name_literal_violations(tmp_path) == []

    (tmp_path / "context_builder.py").write_text(spelled, encoding="utf-8")
    violations = model_name_literal_violations(tmp_path)
    assert len(violations) == 1
    assert violations[0].startswith("context_builder.py:1")
