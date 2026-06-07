"""Production Context CI profile for ``cortex doctor --production``.

Maps structural and integrity findings to stable diagnostic codes with
suggested repair commands. JSON output is the machine-readable contract for
CI and Autumn Garage composition partners.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast

from cortex.doctor_checks import run_plain_checks
from cortex.usage import read_usage
from cortex.validation import Issue, Severity, run_all_checks

DiagnosticSeverity = Literal["error", "warning", "info"]


class ProductionReport(TypedDict):
    profile: str
    project_root: str
    errors: int
    warnings: int
    exit_class: str
    usage: dict[str, object] | None
    diagnostics: list[dict[str, object]]

_REPAIR_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"rerun `cortex refresh-state`"), "cortex refresh-state"),
    (re.compile(r"hand-maintain.*map\.md"), "hand-maintain .cortex/map.md"),
    (re.compile(r"run `cortex journal stage"), "cortex journal stage pr-merged --pr <N>"),
    (re.compile(r"run `cortex update`"), "cortex update"),
    (re.compile(r"run `cortex refresh-index`"), "cortex refresh-index"),
)

_MISSING_SOURCE_PATTERNS = (
    ".cortex/` directory does not exist",
    "missing source directory",
    "derived layer `",
    " does not exist",
    "not found",
)

_UNRESOLVED_PROVENANCE_PATTERNS = (
    "provenance",
    "sources-hash",
    "generated against head",
    "head snapshot",
    "generator was",
)

_BUDGET_EXCEEDED_PATTERNS = (
    "budget",
    "over budget",
    "token",
    "tokens",
    "omitted by the manifest budget",
    "truncated by budget",
)

_POLICY_VIOLATION_PATTERNS = (
    "spec §",
    "invariant",
    "invalid",
    "malformed",
    "must ",
    "lacks ",
    "does not cite",
    "unsupported",
    "unknown key",
    "constraint statement",
)


@dataclass(frozen=True)
class ProductionDiagnostic:
    code: str
    severity: DiagnosticSeverity
    path: str
    message: str
    repair_command: str | None = None

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }
        if self.repair_command is not None:
            out["repair_command"] = self.repair_command
        return out


def _code_for_issue(issue: Issue) -> str:
    message = issue.message.lower()
    path = (issue.path or "").lower()
    if "generated layer has no yaml frontmatter" in message:
        return "manual-edit-to-generated"
    if "generated layer missing" in message and "provenance field" in message:
        return "manual-edit-to-generated"
    if path.endswith(("state.md", "map.md")) and (
        "derived layer has no yaml frontmatter" in message
        or "missing required metadata field" in message
    ):
        return "manual-edit-to-generated"
    if "sources-hash" in message or "generated before source changed" in message:
        return "stale-derived"
    if "source content changed" in message or "layer is stale" in message:
        return "stale-derived"
    if any(pattern in message for pattern in _MISSING_SOURCE_PATTERNS):
        return "missing-source"
    if " missing" in message and path.startswith(".cortex/"):
        return "missing-source"
    if any(pattern in message for pattern in _BUDGET_EXCEEDED_PATTERNS):
        return "budget-exceeded"
    if any(pattern in message for pattern in _UNRESOLVED_PROVENANCE_PATTERNS):
        return "unresolved-provenance"
    if "map.md" in path and "hand-maintain" in message:
        return "map-hand-maintain"
    if "append-only" in message:
        return "append-only-violation"
    if "immutable" in message or ("doctrine" in message and "mutation" in message):
        return "immutable-violation"
    if "orphan" in message or "deferral" in message:
        return "orphan-deferral"
    if "placeholder" in message or "template" in message:
        return "journal-template-pollution"
    if any(pattern in message for pattern in _POLICY_VIOLATION_PATTERNS):
        return "policy-violation"
    if issue.severity is Severity.ERROR:
        return "structural-error"
    return "policy-warning"


def _repair_command_for_message(message: str) -> str | None:
    for pattern, command in _REPAIR_HINTS:
        if pattern.search(message):
            return command
    return None


def issue_to_diagnostic(issue: Issue) -> ProductionDiagnostic:
    severity: DiagnosticSeverity = (
        "error" if issue.severity is Severity.ERROR else "warning"
    )
    return ProductionDiagnostic(
        code=_code_for_issue(issue),
        severity=severity,
        path=issue.path,
        message=issue.message,
        repair_command=_repair_command_for_message(issue.message),
    )


def usage_summary(project_root: Path) -> dict[str, object] | None:
    usage = read_usage(project_root)
    counts_raw = usage.get("counts")
    since_raw = usage.get("since")
    if not isinstance(counts_raw, dict):
        return None
    grep_raw = counts_raw.get("grep")
    retrieve_bm25_raw = counts_raw.get("retrieve_bm25")
    retrieve_semantic_raw = counts_raw.get("retrieve_semantic")
    retrieve_hybrid_raw = counts_raw.get("retrieve_hybrid")
    manifest_raw = counts_raw.get("manifest")
    if not isinstance(grep_raw, int):
        return None
    if not isinstance(retrieve_bm25_raw, int):
        return None
    if not isinstance(retrieve_semantic_raw, int):
        return None
    if not isinstance(retrieve_hybrid_raw, int):
        return None
    if not isinstance(manifest_raw, int):
        return None
    grep_count = grep_raw
    retrieve_bm25 = retrieve_bm25_raw
    retrieve_semantic = retrieve_semantic_raw
    retrieve_hybrid = retrieve_hybrid_raw
    manifest_count = manifest_raw
    retrieve_total = retrieve_bm25 + retrieve_semantic + retrieve_hybrid
    ratio: float | None = None
    if retrieve_total > 0:
        ratio = round(grep_count / retrieve_total, 2)
    return {
        "since": since_raw,
        "grep": grep_count,
        "retrieve_total": retrieve_total,
        "retrieve_bm25": retrieve_bm25,
        "retrieve_semantic": retrieve_semantic,
        "retrieve_hybrid": retrieve_hybrid,
        "manifest": manifest_count,
        "grep_to_retrieve_ratio": ratio,
    }


def run_production_checks(project_root: Path) -> list[ProductionDiagnostic]:
    """Run the production doctor profile checks."""

    issues = run_all_checks(project_root)
    issues.extend(run_plain_checks(project_root))
    return [issue_to_diagnostic(issue) for issue in sorted(issues, key=lambda i: (i.severity.value, i.path, i.message))]


def production_report(project_root: Path) -> ProductionReport:
    diagnostics = run_production_checks(project_root)
    errors = sum(1 for item in diagnostics if item.severity == "error")
    warnings = sum(1 for item in diagnostics if item.severity == "warning")
    return cast(
        ProductionReport,
        {
            "profile": "production",
            "project_root": str(project_root),
            "errors": errors,
            "warnings": warnings,
            "exit_class": "fail" if errors or warnings else "pass",
            "usage": usage_summary(project_root),
            "diagnostics": [item.to_dict() for item in diagnostics],
        },
    )
