"""Facts-file handoff contract for `cortex journal draft --facts-file`.

The facts-file path is intentionally narrow: callers pass a compact packet of
already-curated facts (not full repo context), Cortex validates the packet, and
renders a deterministic Journal draft from the existing template.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

TIER1_EVENT_TYPES: tuple[str, ...] = (
    "pr-merged",
    "decision",
    "incident",
    "release",
    "plan-transition",
)

FACTS_DRAFT_SUPPORTED_TYPES: tuple[str, ...] = (
    "pr-merged",
    "decision",
    "release",
)

_DECISION_TRIGGERS: tuple[str, ...] = (
    "T1.1",
    "T1.4",
    "T1.5",
    "T1.8",
    "T2.1",
    "T2.2",
    "T2.3",
    "T2.4",
    "T2.5",
    "— (human-authored)",
)

_PLAN_TRANSITION_STATUSES: tuple[str, ...] = (
    "active",
    "blocked",
    "deferred",
    "shipped",
    "cancelled",
    "superseded",
)

_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")
_H1_TEMPLATE_RE = re.compile(r"^# \{\{[^}]+\}\}.*$", re.MULTILINE)


@dataclass(frozen=True)
class FactsValidationIssue:
    field: str
    message: str


JSON_SCHEMA_DRAFT_2020_12: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://autumngarage.dev/cortex/journal-facts.schema.json",
    "title": "Cortex Journal Draft Facts Packet",
    "type": "object",
    "required": ["type", "title"],
    "properties": {
        "type": {"enum": list(FACTS_DRAFT_SUPPORTED_TYPES)},
        "title": {"type": "string", "minLength": 1},
    },
    "allOf": [
        {
            "if": {"properties": {"type": {"const": "pr-merged"}}, "required": ["type"]},
            "then": {
                "required": [
                    "pr_number",
                    "branch",
                    "commit_range",
                    "changed_files",
                    "behavior_summary",
                    "tests_run",
                    "cortex_refs",
                    "followups",
                ],
                "properties": {
                    "pr_number": {"type": "integer", "minimum": 1},
                    "branch": {"type": "string", "minLength": 1},
                    "commit_range": {"type": "string", "minLength": 1},
                    "changed_files": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "diffstat": {"type": "string", "minLength": 1},
                    "behavior_summary": {"type": "string", "minLength": 1},
                    "tests_run": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "cortex_refs": {"$ref": "#/$defs/cortex_refs"},
                    "followups": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        {
            "if": {"properties": {"type": {"const": "decision"}}, "required": ["type"]},
            "then": {
                "required": ["trigger", "summary", "context", "decision", "action_items", "cortex_refs"],
                "properties": {
                    "trigger": {"enum": list(_DECISION_TRIGGERS)},
                    "summary": {"type": "string", "minLength": 1},
                    "context": {"type": "string", "minLength": 1},
                    "decision": {"type": "string", "minLength": 1},
                    "action_items": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "cortex_refs": {"$ref": "#/$defs/cortex_refs"},
                },
            },
        },
        {
            "if": {"properties": {"type": {"const": "release"}}, "required": ["type"]},
            "then": {
                "required": [
                    "tag",
                    "summary",
                    "artifact",
                    "what_shipped",
                    "downstream_docs",
                    "cortex_refs",
                    "followups",
                ],
                "properties": {
                    "tag": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "artifact": {
                        "type": "object",
                        "required": ["kind", "location", "version", "release_notes"],
                        "properties": {
                            "kind": {"type": "string", "minLength": 1},
                            "location": {"type": "string", "minLength": 1},
                            "version": {"type": "string", "minLength": 1},
                            "release_notes": {"type": "string", "minLength": 1},
                        },
                        "additionalProperties": False,
                    },
                    "what_shipped": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "downstream_docs": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "cortex_refs": {"$ref": "#/$defs/cortex_refs"},
                    "followups": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        {
            "if": {"properties": {"type": {"const": "incident"}}, "required": ["type"]},
            "then": {
                "required": [
                    "summary",
                    "context",
                    "impact",
                    "timeline",
                    "root_cause",
                    "went_well",
                    "went_poorly",
                    "action_items",
                    "cortex_refs",
                ],
                "properties": {
                    "summary": {"type": "string", "minLength": 1},
                    "context": {"type": "string", "minLength": 1},
                    "impact": {"type": "string", "minLength": 1},
                    "timeline": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "root_cause": {"type": "string", "minLength": 1},
                    "went_well": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "went_poorly": {"type": "array", "items": {"type": "string", "minLength": 1}},
                    "action_items": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                    },
                    "cortex_refs": {"$ref": "#/$defs/cortex_refs"},
                },
            },
        },
        {
            "if": {"properties": {"type": {"const": "plan-transition"}}, "required": ["type"]},
            "then": {
                "required": [
                    "plan",
                    "from_status",
                    "to_status",
                    "reason",
                    "outcome",
                    "deferred_items",
                    "cortex_refs",
                ],
                "properties": {
                    "plan": {"type": "string", "minLength": 1},
                    "from_status": {"enum": list(_PLAN_TRANSITION_STATUSES)},
                    "to_status": {"enum": list(_PLAN_TRANSITION_STATUSES)},
                    "reason": {"type": "string", "minLength": 1},
                    "outcome": {"type": "string", "minLength": 1},
                    "deferred_items": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                    },
                    "cortex_refs": {"$ref": "#/$defs/cortex_refs"},
                },
            },
        },
    ],
    "$defs": {
        "cortex_refs": {
            "type": "object",
            "properties": {
                "plans": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "doctrine": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "spec": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "journal": {"type": "array", "items": {"type": "string", "minLength": 1}},
            },
            "additionalProperties": False,
        }
    },
    "additionalProperties": False,
}


class FactsFileError(Exception):
    """Raised when a facts file cannot be parsed, validated, or rendered."""

    def __init__(
        self,
        *,
        code: str,
        issues: list[FactsValidationIssue],
    ) -> None:
        super().__init__(code)
        self.code = code
        self.issues = issues

    def as_structured_error(self, *, facts_file: Path, journal_type: str) -> dict[str, object]:
        return {
            "error": "journal-facts-file-invalid",
            "reason": self.code,
            "facts_file": str(facts_file),
            "journal_type": journal_type,
            "issues": [
                {
                    "field": issue.field,
                    "message": issue.message,
                }
                for issue in self.issues
            ],
        }


def load_and_validate_facts_file(path: Path, *, expected_type: str) -> dict[str, object]:
    """Load a JSON facts file and validate it against the handoff contract."""
    try:
        raw = path.read_text()
    except OSError as exc:
        raise FactsFileError(
            code="io-error",
            issues=[FactsValidationIssue(field="$", message=f"could not read file: {exc}")],
        ) from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FactsFileError(
            code="malformed-json",
            issues=[
                FactsValidationIssue(
                    field="$",
                    message=(
                        "malformed JSON facts file "
                        f"(line {exc.lineno}, column {exc.colno}): {exc.msg}. "
                        "Only JSON is supported; YAML is not accepted."
                    ),
                )
            ],
        ) from exc

    if not isinstance(parsed, dict):
        raise FactsFileError(
            code="invalid-shape",
            issues=[FactsValidationIssue(field="$", message="top-level JSON value must be an object")],
        )

    packet: dict[str, object] = {str(key): value for key, value in parsed.items()}
    issues = validate_facts_packet(packet, expected_type=expected_type)
    if issues:
        raise FactsFileError(code="validation-error", issues=issues)
    return packet


def validate_facts_packet(packet: Mapping[str, object], *, expected_type: str) -> list[FactsValidationIssue]:
    """Return validation issues for a parsed facts packet."""
    issues: list[FactsValidationIssue] = []

    packet_type_raw = packet.get("type")
    packet_type = _expect_string(packet_type_raw, "type", issues)
    _expect_string(packet.get("title"), "title", issues)

    if packet_type is None:
        return issues

    if packet_type not in TIER1_EVENT_TYPES:
        issues.append(
            FactsValidationIssue(
                field="type",
                message=(
                    f"unknown event type {packet_type!r}; expected one of "
                    f"{', '.join(TIER1_EVENT_TYPES)}"
                ),
            )
        )
        return issues

    if packet_type != expected_type:
        issues.append(
            FactsValidationIssue(
                field="type",
                message=(
                    "facts-file event type does not match CLI event type "
                    f"({packet_type!r} != {expected_type!r})"
                ),
            )
        )
        return issues

    if packet_type == "pr-merged":
        _validate_pr_merged(packet, issues)
    elif packet_type == "decision":
        _validate_decision(packet, issues)
    elif packet_type == "release":
        _validate_release(packet, issues)
    elif packet_type == "incident":
        _validate_incident(packet, issues)
    elif packet_type == "plan-transition":
        _validate_plan_transition(packet, issues)

    return issues


def render_facts_draft(*, template: str, packet: Mapping[str, object], today: str) -> str:
    """Render a validated packet into a journal body using the existing template."""
    packet_type_raw = packet.get("type")
    if not isinstance(packet_type_raw, str):
        raise FactsFileError(
            code="validation-error",
            issues=[FactsValidationIssue(field="type", message="type must be a string")],
        )

    if packet_type_raw not in FACTS_DRAFT_SUPPORTED_TYPES:
        raise FactsFileError(
            code="unsupported-type",
            issues=[
                FactsValidationIssue(
                    field="type",
                    message=(
                        f"facts-file drafting is not implemented for {packet_type_raw!r}; "
                        f"supported types: {', '.join(FACTS_DRAFT_SUPPORTED_TYPES)}"
                    ),
                )
            ],
        )

    if packet_type_raw == "pr-merged":
        body = _render_pr_merged(template, packet, today=today)
    elif packet_type_raw == "decision":
        body = _render_decision(template, packet, today=today)
    else:
        body = _render_release(template, packet, today=today)

    leftovers = sorted(set(_PLACEHOLDER_RE.findall(body)))
    if leftovers:
        raise FactsFileError(
            code="render-error",
            issues=[
                FactsValidationIssue(
                    field="$",
                    message=(
                        "template rendering left unresolved placeholders: "
                        f"{', '.join(leftovers)}"
                    ),
                )
            ],
        )

    return body


def _render_pr_merged(template: str, packet: Mapping[str, object], *, today: str) -> str:
    title = _string_value(packet, "title")
    pr_number = _int_value(packet, "pr_number")
    branch = _string_value(packet, "branch")
    commit_range = _string_value(packet, "commit_range")
    behavior_summary = _string_value(packet, "behavior_summary")
    changed_files = _string_list_value(packet, "changed_files")
    tests_run = _string_list_value(packet, "tests_run")
    followups = _string_list_value(packet, "followups")
    refs = _mapping_value(packet, "cortex_refs")

    body = template.replace("{{ YYYY-MM-DD }}", today)
    body = body.replace("{{ nnn }}", str(pr_number))
    body = body.replace("{{ short title }}", title)
    body = re.sub(r"^\*\*Cites:\*\* .+$", f"**Cites:** {_format_cites(refs)}", body, count=1, flags=re.MULTILINE)
    body = re.sub(
        r"^\*\*Merge-commit:\*\* .+$",
        f"**Merge-commit:** {_commit_range_tail(commit_range)}",
        body,
        count=1,
        flags=re.MULTILINE,
    )
    body = re.sub(r"^\*\*Branch:\*\* .+$", f"**Branch:** {branch}", body, count=1, flags=re.MULTILINE)
    body = re.sub(
        r"^> \{\{ One sentence:.*\}\}$",
        f"> {behavior_summary}",
        body,
        count=1,
        flags=re.MULTILINE,
    )

    shipped_lines = [
        f"- {behavior_summary}",
        f"- Commit range: `{commit_range}`",
        "- Changed files:",
        *[f"  - `{path}`" for path in changed_files],
    ]
    diffstat = packet.get("diffstat")
    if isinstance(diffstat, str) and diffstat.strip():
        shipped_lines.append(f"- Diffstat: `{diffstat.strip()}`")
    if tests_run:
        shipped_lines.append("- Tests run:")
        shipped_lines.extend(f"  - {item}" for item in tests_run)
    else:
        shipped_lines.append("- Tests run: _(none supplied)_")
    body = re.sub(
        r"\{\{ Bulleted list of the user-visible or protocol-visible changes in this PR\..*?\}\}",
        "\n".join(shipped_lines),
        body,
        count=1,
        flags=re.DOTALL,
    )

    plans_refs = _format_ref_group(refs, "plans")
    doctrine_refs = _format_ref_group(refs, "doctrine")
    journal_refs = _format_ref_group(refs, "journal")
    body = re.sub(
        r"^(- \*\*Plans:\*\*) \{\{.*\}\}$",
        rf"\1 {plans_refs}",
        body,
        count=1,
        flags=re.MULTILINE,
    )
    body = re.sub(
        r"^(- \*\*Doctrine:\*\*) \{\{.*\}\}$",
        rf"\1 {doctrine_refs}",
        body,
        count=1,
        flags=re.MULTILINE,
    )
    body = re.sub(
        r"^(- \*\*Journal linkage:\*\*) \{\{.*\}\}$",
        rf"\1 {journal_refs}",
        body,
        count=1,
        flags=re.MULTILINE,
    )

    body = re.sub(
        r"^- \[ \] \{\{ item .*?\}\}$",
        _checkbox_list(followups),
        body,
        count=1,
        flags=re.MULTILINE,
    )
    return re.sub(
        r"\{\{ Optional — fill when the PR cycle itself surfaced a process lesson \(not a code lesson; those go in decision or incident entries\)\. Omit if nothing\. \}\}",
        "None recorded.",
        body,
        count=1,
    )


def _render_decision(template: str, packet: Mapping[str, object], *, today: str) -> str:
    title = _string_value(packet, "title")
    trigger = _string_value(packet, "trigger")
    summary = _string_value(packet, "summary")
    context = _string_value(packet, "context")
    decision = _string_value(packet, "decision")
    action_items = _string_list_value(packet, "action_items")
    refs = _mapping_value(packet, "cortex_refs")

    body = template.replace("{{ YYYY-MM-DD }}", today)
    body = _H1_TEMPLATE_RE.sub(f"# {title}", body, count=1)
    body = body.replace(
        "{{ T1.1 | T1.4 | T1.5 | T1.8 | T2.1 | T2.2 | T2.3 | T2.4 | T2.5 | — (human-authored) }}",
        trigger,
    )
    body = body.replace(
        "{{ plans/<slug>, doctrine/<nnnn>-<slug>, journal/<date>-<slug> }}",
        _format_cites(refs),
    )
    body = body.replace("{{ One-sentence summary of what was decided. }}", summary)
    body = body.replace(
        "{{ What was the situation? What evidence or constraint prompted the decision? Cite specific files, PRs, metrics. }}",
        context,
    )
    body = body.replace(
        "{{ The decision itself, stated as a claim in active voice. If multiple options were weighed, name them and say why the chosen one won. }}",
        decision,
    )
    return re.sub(
        r"- \[ \] \{\{ Concrete follow-up — link to issue/PR if filed \}\}\n- \[ \] \{\{ Guardrail test or doc update, if applicable \}\}",
        _checkbox_list(action_items),
        body,
        count=1,
        flags=re.DOTALL,
    )


def _render_release(template: str, packet: Mapping[str, object], *, today: str) -> str:
    title = _string_value(packet, "title")
    tag = _string_value(packet, "tag")
    summary = _string_value(packet, "summary")
    what_shipped = _string_list_value(packet, "what_shipped")
    downstream_docs = _string_list_value(packet, "downstream_docs")
    followups = _string_list_value(packet, "followups")
    refs = _mapping_value(packet, "cortex_refs")
    artifact = _mapping_value(packet, "artifact")

    body = template.replace("{{ YYYY-MM-DD }}", today)
    body = _H1_TEMPLATE_RE.sub(f"# {title}", body, count=1)
    body = body.replace("{{ git tag, e.g. v0.3.0 }}", tag)
    body = body.replace("{{ vX.Y.Z }}", _string_from_mapping(artifact, "version"))
    body = body.replace(
        "{{ Homebrew tap | PyPI release | Docker image | GitHub Release | git tag | other }}",
        _string_from_mapping(artifact, "kind"),
    )
    body = body.replace(
        "{{ e.g. `autumngarage/cortex` tap formula, `pip install cortex==0.3.0`, `ghcr.io/autumngarage/cortex:0.3.0`, https://github.com/autumngarage/cortex/releases/tag/v0.3.0 }}",
        _string_from_mapping(artifact, "location"),
    )
    body = body.replace(
        "{{ link to GitHub Release page or release-notes section }}",
        _string_from_mapping(artifact, "release_notes"),
    )
    body = re.sub(r"^\*\*Cites:\*\* .+$", f"**Cites:** {_format_cites(refs)}", body, count=1, flags=re.MULTILINE)
    body = re.sub(
        r"^> \{\{ One sentence:.*\}\}$",
        f"> {summary}",
        body,
        count=1,
        flags=re.MULTILINE,
    )
    body = re.sub(
        r"\{\{ Bulleted list of user-visible changes in this release\..*?\}\}",
        "\n".join(f"- {line}" for line in what_shipped),
        body,
        count=1,
        flags=re.DOTALL,
    )

    docs_section = "\n".join(f"- `{doc}`" for doc in downstream_docs) if downstream_docs else "_None._"
    body = re.sub(
        r"- \{\{ CLAUDE\.md.*?\}\}\n- \{\{ README\.md.*?\}\}\n- \{\{ Homebrew tap repo.*?\}\}\n- \{\{ docs/PITCH\.md.*?\}\}\n- \{\{ \.\.\. \}\}",
        docs_section,
        body,
        count=1,
        flags=re.DOTALL,
    )

    return re.sub(
        r"^- \[ \] \{\{ item .*?\}\}$",
        _checkbox_list(followups),
        body,
        count=1,
        flags=re.MULTILINE,
    )


def _validate_pr_merged(packet: Mapping[str, object], issues: list[FactsValidationIssue]) -> None:
    _expect_only_keys(
        packet,
        {
            "type",
            "title",
            "pr_number",
            "branch",
            "commit_range",
            "changed_files",
            "diffstat",
            "behavior_summary",
            "tests_run",
            "cortex_refs",
            "followups",
        },
        issues,
    )
    _expect_positive_int(packet.get("pr_number"), "pr_number", issues)
    _expect_string(packet.get("branch"), "branch", issues)
    _expect_string(packet.get("commit_range"), "commit_range", issues)
    _expect_string_list(packet.get("changed_files"), "changed_files", issues, allow_empty=False)
    if "diffstat" in packet:
        _expect_string(packet.get("diffstat"), "diffstat", issues)
    _expect_string(packet.get("behavior_summary"), "behavior_summary", issues)
    _expect_string_list(packet.get("tests_run"), "tests_run", issues, allow_empty=True)
    _validate_cortex_refs(packet.get("cortex_refs"), "cortex_refs", issues)
    _expect_string_list(packet.get("followups"), "followups", issues, allow_empty=True)


def _validate_decision(packet: Mapping[str, object], issues: list[FactsValidationIssue]) -> None:
    _expect_only_keys(
        packet,
        {
            "type",
            "title",
            "trigger",
            "summary",
            "context",
            "decision",
            "action_items",
            "cortex_refs",
        },
        issues,
    )
    trigger = _expect_string(packet.get("trigger"), "trigger", issues)
    if trigger is not None and trigger not in _DECISION_TRIGGERS:
        issues.append(
            FactsValidationIssue(
                field="trigger",
                message=(
                    f"trigger must be one of: {', '.join(_DECISION_TRIGGERS)}"
                ),
            )
        )
    _expect_string(packet.get("summary"), "summary", issues)
    _expect_string(packet.get("context"), "context", issues)
    _expect_string(packet.get("decision"), "decision", issues)
    _expect_string_list(packet.get("action_items"), "action_items", issues, allow_empty=False)
    _validate_cortex_refs(packet.get("cortex_refs"), "cortex_refs", issues)


def _validate_release(packet: Mapping[str, object], issues: list[FactsValidationIssue]) -> None:
    _expect_only_keys(
        packet,
        {
            "type",
            "title",
            "tag",
            "summary",
            "artifact",
            "what_shipped",
            "downstream_docs",
            "cortex_refs",
            "followups",
        },
        issues,
    )
    _expect_string(packet.get("tag"), "tag", issues)
    _expect_string(packet.get("summary"), "summary", issues)
    _validate_release_artifact(packet.get("artifact"), "artifact", issues)
    _expect_string_list(packet.get("what_shipped"), "what_shipped", issues, allow_empty=False)
    _expect_string_list(packet.get("downstream_docs"), "downstream_docs", issues, allow_empty=True)
    _validate_cortex_refs(packet.get("cortex_refs"), "cortex_refs", issues)
    _expect_string_list(packet.get("followups"), "followups", issues, allow_empty=True)


def _validate_incident(packet: Mapping[str, object], issues: list[FactsValidationIssue]) -> None:
    _expect_only_keys(
        packet,
        {
            "type",
            "title",
            "summary",
            "context",
            "impact",
            "timeline",
            "root_cause",
            "went_well",
            "went_poorly",
            "action_items",
            "cortex_refs",
        },
        issues,
    )
    _expect_string(packet.get("summary"), "summary", issues)
    _expect_string(packet.get("context"), "context", issues)
    _expect_string(packet.get("impact"), "impact", issues)
    _expect_string_list(packet.get("timeline"), "timeline", issues, allow_empty=False)
    _expect_string(packet.get("root_cause"), "root_cause", issues)
    _expect_string_list(packet.get("went_well"), "went_well", issues, allow_empty=True)
    _expect_string_list(packet.get("went_poorly"), "went_poorly", issues, allow_empty=True)
    _expect_string_list(packet.get("action_items"), "action_items", issues, allow_empty=False)
    _validate_cortex_refs(packet.get("cortex_refs"), "cortex_refs", issues)


def _validate_plan_transition(packet: Mapping[str, object], issues: list[FactsValidationIssue]) -> None:
    _expect_only_keys(
        packet,
        {
            "type",
            "title",
            "plan",
            "from_status",
            "to_status",
            "reason",
            "outcome",
            "deferred_items",
            "cortex_refs",
        },
        issues,
    )
    _expect_string(packet.get("plan"), "plan", issues)
    from_status = _expect_string(packet.get("from_status"), "from_status", issues)
    to_status = _expect_string(packet.get("to_status"), "to_status", issues)
    if from_status is not None and from_status not in _PLAN_TRANSITION_STATUSES:
        issues.append(
            FactsValidationIssue(
                field="from_status",
                message=(
                    f"from_status must be one of: {', '.join(_PLAN_TRANSITION_STATUSES)}"
                ),
            )
        )
    if to_status is not None and to_status not in _PLAN_TRANSITION_STATUSES:
        issues.append(
            FactsValidationIssue(
                field="to_status",
                message=f"to_status must be one of: {', '.join(_PLAN_TRANSITION_STATUSES)}",
            )
        )
    _expect_string(packet.get("reason"), "reason", issues)
    _expect_string(packet.get("outcome"), "outcome", issues)
    _expect_string_list(packet.get("deferred_items"), "deferred_items", issues, allow_empty=True)
    _validate_cortex_refs(packet.get("cortex_refs"), "cortex_refs", issues)


def _validate_release_artifact(value: object, field: str, issues: list[FactsValidationIssue]) -> None:
    if not isinstance(value, Mapping):
        issues.append(FactsValidationIssue(field=field, message="must be an object"))
        return
    _expect_only_keys(
        value,
        {"kind", "location", "version", "release_notes"},
        issues,
        field_prefix=f"{field}.",
    )
    _expect_string(value.get("kind"), f"{field}.kind", issues)
    _expect_string(value.get("location"), f"{field}.location", issues)
    _expect_string(value.get("version"), f"{field}.version", issues)
    _expect_string(value.get("release_notes"), f"{field}.release_notes", issues)


def _validate_cortex_refs(value: object, field: str, issues: list[FactsValidationIssue]) -> None:
    if not isinstance(value, Mapping):
        issues.append(FactsValidationIssue(field=field, message="must be an object"))
        return
    _expect_only_keys(
        value,
        {"plans", "doctrine", "spec", "journal"},
        issues,
        field_prefix=f"{field}.",
    )
    for key in ("plans", "doctrine", "spec", "journal"):
        if key in value:
            _expect_string_list(value.get(key), f"{field}.{key}", issues, allow_empty=True)


def _expect_only_keys(
    value: Mapping[str, object],
    allowed: set[str],
    issues: list[FactsValidationIssue],
    *,
    required: set[str] | None = None,
    field_prefix: str = "",
) -> None:
    for key in value:
        if key not in allowed:
            issues.append(
                FactsValidationIssue(
                    field=f"{field_prefix}{key}",
                    message="unknown field (schema is intentionally narrow)",
                )
            )
    if required is None:
        return
    for key in required:
        if key not in value:
            issues.append(
                FactsValidationIssue(
                    field=f"{field_prefix}{key}",
                    message="missing required field",
                )
            )


def _expect_string(value: object, field: str, issues: list[FactsValidationIssue]) -> str | None:
    if value is None:
        issues.append(FactsValidationIssue(field=field, message="missing required field"))
        return None
    if not isinstance(value, str):
        issues.append(FactsValidationIssue(field=field, message="must be a string"))
        return None
    if not value.strip():
        issues.append(FactsValidationIssue(field=field, message="must not be empty"))
        return None
    return value


def _expect_positive_int(value: object, field: str, issues: list[FactsValidationIssue]) -> int | None:
    if value is None:
        issues.append(FactsValidationIssue(field=field, message="missing required field"))
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(FactsValidationIssue(field=field, message="must be an integer"))
        return None
    if value <= 0:
        issues.append(FactsValidationIssue(field=field, message="must be greater than zero"))
        return None
    return value


def _expect_string_list(
    value: object,
    field: str,
    issues: list[FactsValidationIssue],
    *,
    allow_empty: bool,
) -> list[str] | None:
    if value is None:
        issues.append(FactsValidationIssue(field=field, message="missing required field"))
        return None
    if not isinstance(value, list):
        issues.append(FactsValidationIssue(field=field, message="must be a list of strings"))
        return None
    out: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            issues.append(
                FactsValidationIssue(
                    field=f"{field}[{index}]",
                    message="must be a non-empty string",
                )
            )
            continue
        out.append(item)
    if not allow_empty and not out:
        issues.append(FactsValidationIssue(field=field, message="must contain at least one item"))
    return out


def _string_value(packet: Mapping[str, object], field: str) -> str:
    value = packet[field]
    if not isinstance(value, str):
        raise AssertionError(f"{field} should be string after validation")
    return value


def _int_value(packet: Mapping[str, object], field: str) -> int:
    value = packet[field]
    if not isinstance(value, int):
        raise AssertionError(f"{field} should be int after validation")
    return value


def _string_list_value(packet: Mapping[str, object], field: str) -> list[str]:
    value = packet[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AssertionError(f"{field} should be list[str] after validation")
    return value


def _mapping_value(packet: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = packet[field]
    if not isinstance(value, Mapping):
        raise AssertionError(f"{field} should be object after validation")
    return value


def _string_from_mapping(packet: Mapping[str, object], field: str) -> str:
    value = packet[field]
    if not isinstance(value, str):
        raise AssertionError(f"{field} should be string after validation")
    return value


def _commit_range_tail(commit_range: str) -> str:
    if ".." not in commit_range:
        return commit_range
    _, tail = commit_range.split("..", 1)
    return tail or commit_range


def _checkbox_list(items: list[str]) -> str:
    if not items:
        return "_None._"
    return "\n".join(f"- [ ] {item}" for item in items)


def _format_cites(refs: Mapping[str, object]) -> str:
    citations: list[str] = []
    for group in ("plans", "doctrine", "spec", "journal"):
        value = refs.get(group)
        if isinstance(value, list):
            for ref in value:
                if isinstance(ref, str):
                    citations.append(_citation(group, ref))
    if not citations:
        return "_(none supplied)_"
    return ", ".join(citations)


def _format_ref_group(refs: Mapping[str, object], group: str) -> str:
    value = refs.get(group)
    if not isinstance(value, list):
        return "none recorded"
    rendered = [_citation(group, ref) for ref in value if isinstance(ref, str)]
    if not rendered:
        return "none recorded"
    return ", ".join(rendered)


def _citation(group: str, value: str) -> str:
    if group == "spec":
        return value if value.startswith("SPEC") else f"SPEC.md {value}".strip()
    if value.startswith(f"{group}/"):
        return value
    return f"{group}/{value}"
