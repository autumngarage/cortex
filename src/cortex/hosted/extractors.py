"""Deterministic repo-native candidate extractors (cortex#351-#356).

Stage 0 derive reconstructs the decision surface from the structured and
text sources a repo already has:

- **Agent instruction files** (``CLAUDE.md`` / ``AGENTS.md``, cortex#351):
  constraint-shaped statements — imperative bullets, must/never/always/do-not
  sentences, bolded rule lines, and bullets under hard-requirements-style
  sections — each becomes one candidate whose ``SourceSpan`` covers exactly
  the rule's text.
- **ADRs** (``docs/adr/*``, ``docs/decisions/*``, or ``NNNN-*.md`` files with
  a ``Status:`` header, cortex#352): one ADR = one near-verbatim candidate
  composed of the title plus the Decision/Context sections; the ADR status
  maps onto lane semantics (only ``Accepted`` enters as the auto-promotable
  structured assignment).
- **CODEOWNERS** (cortex#353): each well-formed rule line becomes one
  ownership candidate (``<owners> own <pattern>``) with the pattern as a
  path/glob scope and each owner as an owner scope.
- **Commit messages** (cortex#354): one ``SourceDocument`` per commit
  (``document_type`` ``commit_message``, ``external_id`` the sha). Subject
  lines matching the Protocol T1.8 patterns and body lines carrying decision
  verbs become per-statement candidates with exact offsets; Closes-issue /
  BREAKING CHANGE trailers contribute ``issue_ref`` scope hints, never
  candidates of their own.
- **PR descriptions** (cortex#355, ``document_type`` ``pr_description``):
  sections matter — statements under ``## Why`` / ``## Decision`` /
  ``## Approach`` headings rank as candidates; checklists, template stubs,
  and boilerplate drop with reason codes.
- **PR review comments** (cortex#356, ``document_type``
  ``pr_review_comment``): a comment proposing a rule ("we should
  always/never...", "convention:", "going forward") becomes a candidate;
  approvals, nits, and emoji-only comments drop with reason codes. Every
  #356 candidate is stamped ``backfilled=True`` — see the cold-start caveat
  in :func:`extract_pr_review_comment_rules`.

The three text sources (#354-#356) enter the **provisional lane** under
``DEFAULT_LANE_POLICY``: commit and PR prose never auto-promotes; human
confirmation is required.

Every extractor is **deterministic — no model calls**. These are the Tier-1
sources from the derive brief; the model-backed derive boundary
(`cortex.hosted.model_interfaces`) is a different lane entirely. Determinism
invariant: the same document content always yields identical candidates,
identical span hashes, and identical ledger idempotency keys.

Gathering helpers build the text-source ``SourceDocument`` snapshots from
``git log`` and ``gh`` JSON output via subprocess (``git``/``gh`` CLIs are
local tools, not vendor SDKs; unit tests replay committed fixture captures
and never touch the network). Gathered document ordering is deterministic:
commits sort by (author timestamp, sha); review comments by (created_at,
comment id); PR numbers ascending.

Visibility invariants (no silent fallbacks):

- Source material that does not become a candidate becomes a
  ``DroppedChatter`` record with a namespaced reason code — prose without a
  constraint, headings, link-only lines, malformed CODEOWNERS lines, ADRs
  missing a Status or Decision section. Nothing is skipped silently.
- A document no extractor recognizes raises ``ExtractorError`` (registered
  in the degradation taxonomy as ``invalid_input_rejected``); it is never
  quietly ignored.
- Candidates always carry at least one source span
  (``DeriveCandidate`` makes uncited candidates unrepresentable).

Ledger identity: each candidate becomes one ``CANDIDATE_PROPOSED``
``LedgerEvent``. The idempotency key covers every field that feeds the event
hash — content-keyed span/payload material, the extractor id (actor), the
extractor-specific metadata, and the source snapshot timestamp — so the
derive store's same-key/different-hash drift error can only signal real
divergence, never a touched mtime or an extractor version bump.

Supersede hints (cortex#352): an ADR whose status is ``Superseded`` carries
``adr_superseded_by_ref`` in the candidate metadata. That hint is for the
future graph writer (decision_edges supersede links); this module documents
it and performs **no graph writes**.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cortex.hosted.eval_fixtures import FixtureScope
from cortex.hosted.lanes import DEFAULT_LANE_POLICY, DeriveSourceType, LaneAssignment
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)
from cortex.hosted.model_interfaces import DeriveCandidate, DroppedChatter
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.scopes import ScopeType

EXTRACTORS_VERSION = 1

EXTRACTOR_IDS: Mapping[DeriveSourceType, str] = MappingProxyType(
    {
        DeriveSourceType.AGENT_INSTRUCTIONS: (
            f"repo-native/agent-instructions@v{EXTRACTORS_VERSION}"
        ),
        DeriveSourceType.ADR: f"repo-native/adr@v{EXTRACTORS_VERSION}",
        DeriveSourceType.CODEOWNERS: f"repo-native/codeowners@v{EXTRACTORS_VERSION}",
        DeriveSourceType.COMMIT_MESSAGE: (
            f"repo-native/commit-message@v{EXTRACTORS_VERSION}"
        ),
        DeriveSourceType.PR_DESCRIPTION: (
            f"repo-native/pr-description@v{EXTRACTORS_VERSION}"
        ),
        DeriveSourceType.PR_REVIEW_COMMENT: (
            f"repo-native/pr-review-comment@v{EXTRACTORS_VERSION}"
        ),
    }
)

AGENT_INSTRUCTION_FILENAMES = frozenset({"CLAUDE.md", "AGENTS.md"})
CODEOWNERS_FILENAME = "CODEOWNERS"
ADR_DIRECTORY_NAMES = frozenset({"adr", "adrs", "decisions"})

# document_type values for the gathered text sources (#354-#356). Repo files
# walked by `cortex derive` carry document_type "repo-file" and route by
# filename; these route by document_type because they are not files at all.
COMMIT_MESSAGE_DOCUMENT_TYPE = "commit_message"
PR_DESCRIPTION_DOCUMENT_TYPE = "pr_description"
PR_REVIEW_COMMENT_DOCUMENT_TYPE = "pr_review_comment"

_TEXT_SOURCE_TYPES_BY_DOCUMENT_TYPE: Mapping[str, DeriveSourceType] = MappingProxyType(
    {
        COMMIT_MESSAGE_DOCUMENT_TYPE: DeriveSourceType.COMMIT_MESSAGE,
        PR_DESCRIPTION_DOCUMENT_TYPE: DeriveSourceType.PR_DESCRIPTION,
        PR_REVIEW_COMMENT_DOCUMENT_TYPE: DeriveSourceType.PR_REVIEW_COMMENT,
    }
)

# Dropped-chatter reason codes (namespaced per extractor, never silent).
DROP_HEADING_ONLY = "agent_instructions:heading_only"
DROP_CODE_BLOCK = "agent_instructions:code_block"
DROP_TABLE = "agent_instructions:table"
DROP_LINK_ONLY = "agent_instructions:link_only"
DROP_BULLET_WITHOUT_CONSTRAINT = "agent_instructions:bullet_without_constraint"
DROP_PROSE_WITHOUT_CONSTRAINT = "agent_instructions:prose_without_constraint"
DROP_ADR_MISSING_TITLE = "adr:missing_title"
DROP_ADR_MISSING_STATUS = "adr:missing_status"
DROP_ADR_MISSING_DECISION_SECTION = "adr:missing_decision_section"
DROP_CODEOWNERS_MISSING_PATTERN = "codeowners:missing_pattern"
DROP_CODEOWNERS_UNOWNED_PATTERN = "codeowners:unowned_pattern_reset"
DROP_CODEOWNERS_INVALID_OWNER = "codeowners:invalid_owner"
DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN = (
    "commit_message:subject_without_decision_pattern"
)
DROP_COMMIT_BODY_LINE_WITHOUT_DECISION_VERB = (
    "commit_message:body_line_without_decision_verb"
)
DROP_COMMIT_TRAILER_WITHOUT_CANDIDATE = "commit_message:trailer_without_candidate"
DROP_COMMIT_TRAILER_WITHOUT_ISSUE_REF = "commit_message:trailer_without_issue_ref"
DROP_COMMIT_EMPTY_MESSAGE = "commit_message:empty_message"
DROP_PR_HEADING_ONLY = "pr_description:heading_only"
DROP_PR_CODE_BLOCK = "pr_description:code_block"
DROP_PR_TABLE = "pr_description:table"
DROP_PR_LINK_ONLY = "pr_description:link_only"
DROP_PR_CHECKLIST_ITEM = "pr_description:checklist_item"
DROP_PR_TEMPLATE_STUB = "pr_description:template_stub"
DROP_PR_OUTSIDE_DECISION_SECTION = "pr_description:outside_decision_section"
DROP_PR_EMPTY_BODY = "pr_description:empty_body"
DROP_REVIEW_APPROVAL_ONLY = "pr_review_comment:approval_only"
DROP_REVIEW_NIT_ONLY = "pr_review_comment:nit_only"
DROP_REVIEW_EMOJI_ONLY = "pr_review_comment:emoji_only"
DROP_REVIEW_NO_RULE_PROPOSAL = "pr_review_comment:no_rule_proposal"
DROP_REVIEW_EMPTY_BODY = "pr_review_comment:empty_body"

# Metadata keys candidate_events() owns; extractor-specific metadata may not
# shadow them (collision would silently overwrite provenance).
_RESERVED_METADATA_KEYS = frozenset({"document_hash", "extractor", "source_type"})

# --- agent-instructions heuristics (closed, documented sets) ----------------

# Constraint-shaped language. Closed keyword set on purpose: precision over
# recall is the Tier-1 derive posture — prose that does not bind drops with a
# visible reason instead of becoming a low-confidence candidate.
_CONSTRAINT_RE = re.compile(
    r"\b(?:must(?:\s+not)?|never|always|do\s+not|don'?t|shall(?:\s+not)?"
    r"|required|forbidden|prohibited)\b",
    re.IGNORECASE,
)
# Headings whose bullets are rules even without a constraint keyword.
_RULE_SECTION_RE = re.compile(
    r"\b(?:hard\s+requirements?|hard\s+rules?|non-?negotiables?|invariants?)\b",
    re.IGNORECASE,
)
# Named-principle bullets: a bold lead like "**Spec before implementation.**".
_BOLD_LEAD_RE = re.compile(r"^\*\*[^*]+\*\*")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*?)\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(?:```|~~~)")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+")
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?][\"')\]]*(?=\s|$)")

_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")

# --- path/glob scope-token heuristics ----------------------------------------

_TOKEN_STRIP_CHARS = "\"'(),;:!?<>"
_PATH_SEGMENT_RE = re.compile(r"^\.{0,2}[A-Za-z0-9_@.-]*$")
# Documented closed set: file extensions that make a bare slashless token
# path-like in prose (e.g. "SPEC.md"). Derived from the document/config/code
# surface these instruction files actually cite.
_PATH_FILE_EXTENSION_RE = re.compile(
    r"^\.?[A-Za-z0-9_-][A-Za-z0-9_.-]*\."
    r"(?:md|py|toml|yaml|yml|json|sh|txt|cfg|ini|lock|sql|csv|env)$",
    re.IGNORECASE,
)
_GLOB_CHARS = ("*", "?", "[")
# Dotfiles like ".env" or ".pre-commit-config.yaml" count as paths only when
# they arrive through a structured channel (backticks / link targets).
_DOTFILE_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9_.-]*$")

# --- ADR heuristics -----------------------------------------------------------

_ADR_FILENAME_RE = re.compile(r"^\d{3,5}-[^/]+\.md$", re.IGNORECASE)
# Matches "Status: Accepted", "- Status: Accepted", "**Status:** Accepted".
_ADR_STATUS_LINE_RE = re.compile(
    r"^(?:\s*[-*]\s+)?\**status\**\s*:\**\s*(\S.*?)\s*$", re.IGNORECASE
)
_ADR_SUPERSEDED_BY_RE = re.compile(r"superseded\s+by\s+(.+)$", re.IGNORECASE)
_ADR_ACCEPTED_STATUS = "accepted"
_ADR_DECISION_SECTION_TITLES = frozenset({"decision", "decision outcome"})
_ADR_CONTEXT_SECTION_TITLES = frozenset({"context", "context and problem statement"})

# --- CODEOWNERS syntax ---------------------------------------------------------

_CODEOWNERS_OWNER_RE = re.compile(
    r"^@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9_.-]+)?$"
)
_CODEOWNERS_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# --- commit-message heuristics (#354; closed, documented sets) ----------------

# Protocol T1.8 commit-message patterns, verbatim from .cortex/protocol.md § 2:
# `fix: ... regression`, `refactor: ... (removes|introduces)`,
# `feat: ... (breaking|replaces)`. Conventional-commit scope/bang prefixes are
# accepted; anything outside this closed set drops with a visible reason.
_COMMIT_SUBJECT_DECISION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^fix(?:\([^)]*\))?!?:.*\bregression\b", re.IGNORECASE),
    re.compile(r"^refactor(?:\([^)]*\))?!?:.*\b(?:removes|introduces)\b", re.IGNORECASE),
    re.compile(r"^feat(?:\([^)]*\))?!?:.*\b(?:breaking|replaces)\b", re.IGNORECASE),
)
# Decision verbs that make a commit-body line decision-shaped. Closed set on
# purpose: precision over recall is the Tier-1 derive posture.
_COMMIT_DECISION_VERB_RE = re.compile(
    r"\b(?:we\s+decided|switched\s+to|no\s+longer|must|instead\s+of)\b",
    re.IGNORECASE,
)
# Issue-closing trailers (git-workflow convention) and BREAKING CHANGE
# trailers are scope hints, never candidates of their own.
_COMMIT_ISSUE_TRAILER_RE = re.compile(
    r"^(?:closes(?:-issue)?|fixes|resolves|refs?)\s*:\s*(\S.*)$", re.IGNORECASE
)
_COMMIT_BREAKING_TRAILER_RE = re.compile(
    r"^breaking[- ]change\s*:\s*(\S.*)$", re.IGNORECASE
)
_ISSUE_REF_TOKEN_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+\b")
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

# --- PR-description heuristics (#355; closed, documented sets) -----------------

# Sections whose statements rank as candidates. Closed set from the derive
# brief: Why / Decision / Approach. Everything else drops visibly.
_PR_DECISION_SECTION_TITLES = frozenset({"why", "decision", "approach"})
# Checklist items arrive as item blocks whose excerpt starts with the
# task-list marker (the list bullet itself is stripped by _instruction_blocks).
_PR_CHECKLIST_LEAD_RE = re.compile(r"^\[[ xX]\]\s")
# Template stubs and boilerplate: HTML comments left by PR templates, agent
# attribution footers, and Co-Authored-By trailers pasted into bodies.
_PR_TEMPLATE_STUB_RE = re.compile(r"^<!--")
_PR_BOILERPLATE_RE = re.compile(
    r"^(?:\U0001f916\s*generated\s+with\b|co-authored-by\s*:)", re.IGNORECASE
)

# --- PR-review-comment heuristics (#356; closed, documented sets) --------------

# Rule-proposal language: "we should always/never ...", "convention: ...",
# "going forward ...". A sentence matching any of these becomes a candidate.
_REVIEW_RULE_RE = re.compile(
    r"(?:\bwe\s+should\s+(?:always|never)\b|\bgoing\s+forward\b|\bconvention\s*:)",
    re.IGNORECASE,
)
# Pure approvals, normalized (lowercased, collapsed whitespace, trailing
# punctuation stripped) before membership lookup.
_REVIEW_APPROVAL_PHRASES = frozenset(
    {
        "lgtm",
        "sgtm",
        "wfm",
        "+1",
        "ship it",
        "approve",
        "approved",
        "looks good",
        "looks good to me",
        "nice",
        "thanks",
        "thank you",
    }
)
_REVIEW_NIT_RE = re.compile(r"^(?:nit|nitpick|typo)\b", re.IGNORECASE)
_NO_ALNUM_RE = re.compile(r"^[^A-Za-z0-9]+$")
_REVIEW_NORMALIZE_STRIP_CHARS = " .,!;:"


class ExtractorError(ValueError):
    """Raised when a source cannot be routed to or processed by an extractor."""


@dataclass(frozen=True)
class ExtractedCandidate:
    """One candidate plus its lane assignment and extractor-specific metadata."""

    candidate: DeriveCandidate
    lane: LaneAssignment
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        shadowed = _RESERVED_METADATA_KEYS & set(self.metadata)
        if shadowed:
            raise ExtractorError(
                "extractor metadata may not shadow reserved event metadata keys: "
                + ", ".join(sorted(shadowed))
            )
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class ExtractionOutcome:
    """Everything one extractor saw in one document: candidates and drops.

    The visibility invariant lives here: source material is either an
    ``ExtractedCandidate`` or a ``DroppedChatter`` with a reason code.
    """

    source_type: DeriveSourceType
    extractor_id: str
    extracted: tuple[ExtractedCandidate, ...]
    dropped: tuple[DroppedChatter, ...]

    def __post_init__(self) -> None:
        if not self.extractor_id.strip():
            raise ExtractorError("extractor_id must not be empty")
        for item in self.extracted:
            if item.lane.source_type is not self.source_type:
                raise ExtractorError(
                    f"lane assignment names source type {item.lane.source_type.value!r} "
                    f"but the outcome is {self.source_type.value!r}"
                )


@dataclass(frozen=True)
class DroppedSourceChatter:
    """A dropped-chatter record tied back to the file it came from."""

    external_id: str
    chatter: DroppedChatter


def _dropped(reason_code: str, excerpt: str) -> DroppedChatter:
    return DroppedChatter(
        reason_code=reason_code,
        excerpt_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    )


# ---------------------------------------------------------------------------
# Scope-token proposal (shared by #351 and #352; #353 derives scopes from
# CODEOWNERS syntax directly).
# ---------------------------------------------------------------------------


def proposed_path_scopes(text: str) -> tuple[FixtureScope, ...]:
    """Propose path/glob scopes from path-like tokens in rule text.

    Tokens inside backticks or markdown-link targets are *structured* signals
    and qualify with a single ``/`` or a known file extension. Bare prose
    tokens need a stronger signal (a dotted segment, two slashes, or a
    trailing slash) so conjunctions like "and/or" never become scopes.
    Deduplicates by normalized value, preserving first-seen order.
    """

    found: list[tuple[ScopeType, str]] = []
    for token in _CODE_SPAN_RE.findall(text):
        _collect_path_token(found, token.strip(), structured=True)
    for token in _MARKDOWN_LINK_RE.findall(text):
        _collect_path_token(found, token.strip(), structured=True)
    plain = _MARKDOWN_LINK_RE.sub(" ", _CODE_SPAN_RE.sub(" ", text))
    for raw in plain.split():
        token = raw.strip(_TOKEN_STRIP_CHARS).rstrip(".")
        _collect_path_token(found, token, structured=False)
    return _deduped_scopes(found)


def _collect_path_token(
    found: list[tuple[ScopeType, str]], token: str, *, structured: bool
) -> None:
    scope_type = _classify_path_token(token, structured=structured)
    if scope_type is not None:
        found.append((scope_type, token))


def _classify_path_token(token: str, *, structured: bool) -> ScopeType | None:
    if not token or "://" in token or token.startswith("#"):
        return None
    if any(glob_char in token for glob_char in _GLOB_CHARS):
        # Globs need a path shape ("/" or a "*.ext" lead); bare asterisks are
        # markdown emphasis, not patterns.
        if "/" in token or token.startswith("*."):
            return ScopeType.GLOB
        return None
    if "/" in token:
        segments = [segment for segment in token.split("/") if segment]
        if not segments or not all(_PATH_SEGMENT_RE.match(segment) for segment in segments):
            return None
        if structured or token.endswith("/") or token.count("/") >= 2:
            return ScopeType.PATH
        if any("." in segment for segment in segments):
            return ScopeType.PATH
        return None
    if _PATH_FILE_EXTENSION_RE.match(token):
        return ScopeType.PATH
    if structured and _DOTFILE_RE.match(token):
        return ScopeType.PATH
    return None


def _deduped_scopes(found: list[tuple[ScopeType, str]]) -> tuple[FixtureScope, ...]:
    scopes: list[FixtureScope] = []
    seen: set[tuple[ScopeType, str]] = set()
    for scope_type, value in found:
        scope = FixtureScope(scope_type=scope_type, value=value)
        key = (scope_type, scope.normalized_value)
        if key in seen:
            continue
        seen.add(key)
        scopes.append(scope)
    return tuple(scopes)


# ---------------------------------------------------------------------------
# #351 — CLAUDE.md / AGENTS.md instruction files
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    start: int
    text: str


@dataclass(frozen=True)
class _Block:
    kind: str  # "heading" | "code" | "table" | "item" | "paragraph"
    start: int  # offset of the block's trimmed text start
    end: int  # offset past the block's trimmed text end
    in_rules_section: bool
    section_title: str


def _content_lines(content: str) -> tuple[_Line, ...]:
    lines: list[_Line] = []
    offset = 0
    for raw in content.splitlines(keepends=True):
        lines.append(_Line(start=offset, text=raw.rstrip("\r\n")))
        offset += len(raw)
    return tuple(lines)


def _trimmed(content: str, start: int, end: int) -> tuple[int, int]:
    while start < end and content[start] in " \t\n\r":
        start += 1
    while end > start and content[end - 1] in " \t\n\r":
        end -= 1
    return start, end


def _instruction_blocks(content: str) -> tuple[_Block, ...]:
    lines = _content_lines(content)
    blocks: list[_Block] = []
    section_title = ""
    in_rules_section = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.text.strip():
            index += 1
            continue
        line_end = line.start + len(line.text)
        if _FENCE_RE.match(line.text):
            closing = index + 1
            while closing < len(lines) and not _FENCE_RE.match(lines[closing].text):
                closing += 1
            last = lines[min(closing, len(lines) - 1)]
            start, end = _trimmed(content, line.start, last.start + len(last.text))
            blocks.append(_Block("code", start, end, in_rules_section, section_title))
            index = closing + 1
            continue
        heading = _HEADING_RE.match(line.text)
        if heading:
            section_title = heading.group(2)
            in_rules_section = bool(_RULE_SECTION_RE.search(section_title))
            blocks.append(
                _Block("heading", line.start, line_end, in_rules_section, section_title)
            )
            index += 1
            continue
        if _TABLE_ROW_RE.match(line.text):
            stop = index
            while stop < len(lines) and _TABLE_ROW_RE.match(lines[stop].text):
                stop += 1
            last = lines[stop - 1]
            start, end = _trimmed(content, line.start, last.start + len(last.text))
            blocks.append(_Block("table", start, end, in_rules_section, section_title))
            index = stop
            continue
        item = _LIST_ITEM_RE.match(line.text)
        if item:
            stop = _block_stop(lines, index, allow_items=False)
            last = lines[stop - 1]
            start, end = _trimmed(
                content, line.start + item.end(), last.start + len(last.text)
            )
            blocks.append(_Block("item", start, end, in_rules_section, section_title))
            index = stop
            continue
        stop = _block_stop(lines, index, allow_items=False)
        last = lines[stop - 1]
        start, end = _trimmed(content, line.start, last.start + len(last.text))
        blocks.append(_Block("paragraph", start, end, in_rules_section, section_title))
        index = stop
    return tuple(blocks)


def _block_stop(lines: tuple[_Line, ...], index: int, *, allow_items: bool) -> int:
    stop = index + 1
    while stop < len(lines):
        text = lines[stop].text
        if not text.strip():
            break
        if _HEADING_RE.match(text) or _FENCE_RE.match(text) or _TABLE_ROW_RE.match(text):
            break
        if not allow_items and _LIST_ITEM_RE.match(text):
            break
        stop += 1
    return stop


def _is_link_only(text: str) -> bool:
    if not _MARKDOWN_LINK_RE.search(text):
        return False
    remainder = _MARKDOWN_LINK_RE.sub("", text)
    remainder = re.sub(r"[`*_>\s:.,;()\[\]-]+", "", remainder)
    return not remainder


def _sentence_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """Deterministic sentence segmentation: split after .!? followed by space.

    Naive on purpose (abbreviations split early); the constraint filter runs
    per segment, so an early split can only narrow a candidate, never invent
    one.
    """

    ranges: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_BOUNDARY_RE.finditer(text):
        ranges.append((cursor, match.end()))
        cursor = match.end()
    if text[cursor:].strip():
        ranges.append((cursor, len(text)))
    trimmed: list[tuple[int, int]] = []
    for start, end in ranges:
        while start < end and text[start] in " \t\n\r":
            start += 1
        while end > start and text[end - 1] in " \t\n\r":
            end -= 1
        if end > start:
            trimmed.append((start, end))
    return tuple(trimmed)


def extract_agent_instruction_rules(document: SourceDocument) -> ExtractionOutcome:
    """Extract discrete constraint-shaped rules from CLAUDE.md / AGENTS.md.

    Deterministic heuristics only (issue #351): bullets with constraint
    keywords, bold-lead principle bullets, any bullet under a
    hard-requirements-style section, and constraint sentences inside prose.
    Every non-rule block drops with a reason code.
    """

    content = document.content
    lane = DEFAULT_LANE_POLICY.assign(DeriveSourceType.AGENT_INSTRUCTIONS, backfilled=False)
    extracted: list[ExtractedCandidate] = []
    dropped: list[DroppedChatter] = []

    for block in _instruction_blocks(content):
        excerpt = content[block.start : block.end]
        if block.kind == "heading":
            dropped.append(_dropped(DROP_HEADING_ONLY, excerpt))
            continue
        if block.kind == "code":
            dropped.append(_dropped(DROP_CODE_BLOCK, excerpt))
            continue
        if block.kind == "table":
            dropped.append(_dropped(DROP_TABLE, excerpt))
            continue
        if _is_link_only(excerpt):
            dropped.append(_dropped(DROP_LINK_ONLY, excerpt))
            continue
        if block.kind == "item":
            is_rule = (
                block.in_rules_section
                or bool(_BOLD_LEAD_RE.match(excerpt))
                or bool(_CONSTRAINT_RE.search(excerpt))
            )
            if is_rule:
                extracted.append(
                    _instruction_rule(
                        document,
                        block.start,
                        block.end,
                        lane=lane,
                        rule_kind="bullet",
                        section_title=block.section_title,
                    )
                )
            else:
                dropped.append(_dropped(DROP_BULLET_WITHOUT_CONSTRAINT, excerpt))
            continue
        sentence_rules = [
            (block.start + start, block.start + end)
            for start, end in _sentence_ranges(excerpt)
            if _CONSTRAINT_RE.search(excerpt[start:end])
        ]
        if not sentence_rules:
            dropped.append(_dropped(DROP_PROSE_WITHOUT_CONSTRAINT, excerpt))
            continue
        extracted.extend(
            _instruction_rule(
                document,
                start,
                end,
                lane=lane,
                rule_kind="sentence",
                section_title=block.section_title,
            )
            for start, end in sentence_rules
        )

    return ExtractionOutcome(
        source_type=DeriveSourceType.AGENT_INSTRUCTIONS,
        extractor_id=EXTRACTOR_IDS[DeriveSourceType.AGENT_INSTRUCTIONS],
        extracted=tuple(extracted),
        dropped=tuple(dropped),
    )


def _instruction_rule(
    document: SourceDocument,
    start: int,
    end: int,
    *,
    lane: LaneAssignment,
    rule_kind: str,
    section_title: str,
) -> ExtractedCandidate:
    span = document.span(start_offset=start, end_offset=end)
    candidate = DeriveCandidate(
        decision_text=span.excerpt,
        spans=(span,),
        proposed_scopes=proposed_path_scopes(span.excerpt),
    )
    return ExtractedCandidate(
        candidate=candidate,
        lane=lane,
        metadata={"rule_kind": rule_kind, "section": section_title},
    )


# ---------------------------------------------------------------------------
# #352 — ADRs and decision docs, imported near-verbatim
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Section:
    title: str
    start: int
    end: int


def _adr_sections(content: str) -> tuple[_Section, ...]:
    lines = _content_lines(content)
    headings: list[tuple[int, str, int, int]] = []  # (level, title, line_index, body_start)
    for index, line in enumerate(lines):
        match = _HEADING_RE.match(line.text)
        if match:
            headings.append(
                (len(match.group(1)), match.group(2), index, line.start + len(line.text))
            )
    sections: list[_Section] = []
    for position, (level, title, _index, body_start) in enumerate(headings):
        body_end = len(content)
        for next_heading in headings[position + 1 :]:
            if next_heading[0] <= level:
                body_end = lines[next_heading[2]].start
                break
        start, end = _trimmed(content, body_start, body_end)
        sections.append(_Section(title=title, start=start, end=end))
    return tuple(sections)


def _adr_title_span(content: str) -> tuple[int, int] | None:
    for line in _content_lines(content):
        match = _HEADING_RE.match(line.text)
        if match and len(match.group(1)) == 1:
            return (line.start + match.start(2), line.start + match.end(2))
    return None


def _adr_status(content: str, sections: tuple[_Section, ...]) -> str | None:
    for line in _content_lines(content):
        match = _ADR_STATUS_LINE_RE.match(line.text)
        if match:
            return match.group(1).strip("* ").strip()
    for section in sections:
        if section.title.strip().lower() == "status" and section.end > section.start:
            first_line = content[section.start : section.end].splitlines()[0]
            stripped = first_line.strip("* ").strip()
            if stripped:
                return stripped
    return None


def _adr_section(
    sections: tuple[_Section, ...], titles: frozenset[str]
) -> _Section | None:
    for section in sections:
        if section.title.strip().lower() in titles and section.end > section.start:
            return section
    return None


def has_adr_status_header(content: str) -> bool:
    """True when the content carries an ADR-style ``Status:`` declaration."""

    return _adr_status(content, _adr_sections(content)) is not None


def extract_adr_decision(document: SourceDocument) -> ExtractionOutcome:
    """Import one ADR near-verbatim as exactly one candidate (issue #352).

    The candidate's ``decision_text`` is the concatenation of the cited span
    excerpts (title, Decision section, Context section when present), joined
    with blank lines — near-verbatim, never paraphrased. ADR ``Status:``
    drives lane semantics: only ``Accepted`` enters with the auto-promotable
    structured assignment; every other status (Proposed, Superseded,
    Deprecated, Rejected, ...) is not a currently ratified decision, so it
    enters as a backfilled advisory-only assignment — the policy-level lever
    `LanePolicy.assign` provides for non-current imports. A Superseded status
    additionally records ``adr_superseded_by_ref`` metadata as the hint for
    the future graph writer; this module performs no graph writes.
    """

    content = document.content
    dropped: list[DroppedChatter] = []
    sections = _adr_sections(content)

    title_range = _adr_title_span(content)
    if title_range is None:
        dropped.append(_dropped(DROP_ADR_MISSING_TITLE, content))
        return _adr_outcome((), dropped)

    status_raw = _adr_status(content, sections)
    if status_raw is None:
        dropped.append(_dropped(DROP_ADR_MISSING_STATUS, content))
        return _adr_outcome((), dropped)

    decision_section = _adr_section(sections, _ADR_DECISION_SECTION_TITLES)
    if decision_section is None:
        dropped.append(_dropped(DROP_ADR_MISSING_DECISION_SECTION, content))
        return _adr_outcome((), dropped)

    spans = [document.span(start_offset=title_range[0], end_offset=title_range[1])]
    spans.append(
        document.span(start_offset=decision_section.start, end_offset=decision_section.end)
    )
    context_section = _adr_section(sections, _ADR_CONTEXT_SECTION_TITLES)
    if context_section is not None:
        spans.append(
            document.span(start_offset=context_section.start, end_offset=context_section.end)
        )

    decision_text = "\n\n".join(span.excerpt for span in spans)
    status_token = re.split(r"[\s,.;]+", status_raw)[0].lower()
    accepted = status_token == _ADR_ACCEPTED_STATUS

    metadata: dict[str, Any] = {
        "adr_status": status_token,
        "adr_status_raw": status_raw,
        "adr_title": content[title_range[0] : title_range[1]],
    }
    superseded_by = _ADR_SUPERSEDED_BY_RE.search(status_raw)
    if superseded_by:
        # Supersede hint for the future graph writer (decision_edges); derive
        # documents the relationship but never writes graph state.
        metadata["adr_superseded_by_ref"] = superseded_by.group(1).strip().strip("*_")

    candidate = DeriveCandidate(
        decision_text=decision_text,
        spans=tuple(spans),
        proposed_scopes=proposed_path_scopes(decision_text),
    )
    lane = DEFAULT_LANE_POLICY.assign(DeriveSourceType.ADR, backfilled=not accepted)
    extracted = ExtractedCandidate(candidate=candidate, lane=lane, metadata=metadata)
    return _adr_outcome((extracted,), dropped)


def _adr_outcome(
    extracted: tuple[ExtractedCandidate, ...], dropped: list[DroppedChatter]
) -> ExtractionOutcome:
    return ExtractionOutcome(
        source_type=DeriveSourceType.ADR,
        extractor_id=EXTRACTOR_IDS[DeriveSourceType.ADR],
        extracted=extracted,
        dropped=tuple(dropped),
    )


# ---------------------------------------------------------------------------
# #353 — CODEOWNERS ownership/authority signals
# ---------------------------------------------------------------------------


def extract_codeowners_rules(document: SourceDocument) -> ExtractionOutcome:
    """Extract one ownership candidate per well-formed CODEOWNERS rule line.

    Comments and blank lines are well-formed non-content and are skipped by
    syntax; every *malformed* line (owner token where the pattern belongs, an
    invalid owner token) and every unowned-pattern reset line drops with a
    reason code — never silently.
    """

    content = document.content
    lane = DEFAULT_LANE_POLICY.assign(DeriveSourceType.CODEOWNERS, backfilled=False)
    extracted: list[ExtractedCandidate] = []
    dropped: list[DroppedChatter] = []

    for line in _content_lines(content):
        stripped = line.text.strip()
        if not stripped or stripped.startswith("#"):
            continue
        start, end = _trimmed(content, line.start, line.start + len(line.text))
        excerpt = content[start:end]
        tokens = _codeowners_tokens(stripped)
        pattern = tokens[0]
        owners = tokens[1:]
        if pattern.startswith("@"):
            dropped.append(_dropped(DROP_CODEOWNERS_MISSING_PATTERN, excerpt))
            continue
        if not owners:
            # GitHub's "pattern with no owners" form unsets ownership; it is
            # well-formed but carries no ownership decision to cite.
            dropped.append(_dropped(DROP_CODEOWNERS_UNOWNED_PATTERN, excerpt))
            continue
        invalid = [
            owner
            for owner in owners
            if not (_CODEOWNERS_OWNER_RE.match(owner) or _CODEOWNERS_EMAIL_RE.match(owner))
        ]
        if invalid:
            dropped.append(_dropped(DROP_CODEOWNERS_INVALID_OWNER, excerpt))
            continue
        span = document.span(start_offset=start, end_offset=end)
        pattern_scope_type = (
            ScopeType.GLOB
            if any(glob_char in pattern for glob_char in _GLOB_CHARS)
            else ScopeType.PATH
        )
        scopes = _deduped_scopes(
            [(pattern_scope_type, pattern)]
            + [(ScopeType.OWNER, owner) for owner in owners]
        )
        candidate = DeriveCandidate(
            decision_text=f"{' '.join(owners)} own {pattern}",
            spans=(span,),
            proposed_scopes=scopes,
        )
        extracted.append(
            ExtractedCandidate(
                candidate=candidate,
                lane=lane,
                metadata={"codeowners_pattern": pattern, "codeowners_owners": list(owners)},
            )
        )

    return ExtractionOutcome(
        source_type=DeriveSourceType.CODEOWNERS,
        extractor_id=EXTRACTOR_IDS[DeriveSourceType.CODEOWNERS],
        extracted=tuple(extracted),
        dropped=tuple(dropped),
    )


def _codeowners_tokens(stripped_line: str) -> list[str]:
    """Whitespace tokens up to an inline comment marker."""

    tokens: list[str] = []
    for token in stripped_line.split():
        if token.startswith("#"):
            break
        tokens.append(token)
    return tokens


# ---------------------------------------------------------------------------
# #354 — commit messages (one SourceDocument per commit, external_id = sha)
# ---------------------------------------------------------------------------


def extract_commit_message_decisions(document: SourceDocument) -> ExtractionOutcome:
    """Extract decision-shaped statements from one commit message (issue #354).

    Deterministic heuristics only: the subject line qualifies when it matches
    a Protocol T1.8 pattern; body lines qualify when they carry a decision
    verb from the closed set. Closes-issue / BREAKING CHANGE trailers never
    become candidates — they contribute ``issue_ref`` scope hints to every
    candidate extracted from the same commit. Every non-qualifying line drops
    with a reason code; trailers that could not be consumed (no candidate to
    attach to, or no issue ref to contribute) drop visibly too.
    """

    content = document.content
    lane = DEFAULT_LANE_POLICY.assign(DeriveSourceType.COMMIT_MESSAGE, backfilled=False)
    statement_ranges: list[tuple[int, int, str]] = []
    trailer_ranges: list[tuple[int, int, tuple[str, ...]]] = []
    dropped: list[DroppedChatter] = []
    subject_seen = False

    for line in _content_lines(content):
        if not line.text.strip():
            continue
        start, end = _trimmed(content, line.start, line.start + len(line.text))
        excerpt = content[start:end]
        if not subject_seen:
            subject_seen = True
            if any(pattern.search(excerpt) for pattern in _COMMIT_SUBJECT_DECISION_RES):
                statement_ranges.append((start, end, "subject"))
            else:
                dropped.append(
                    _dropped(DROP_COMMIT_SUBJECT_WITHOUT_DECISION_PATTERN, excerpt)
                )
            continue
        trailer_refs = _commit_trailer_issue_refs(excerpt)
        if trailer_refs is not None:
            trailer_ranges.append((start, end, trailer_refs))
            continue
        if _COMMIT_DECISION_VERB_RE.search(excerpt):
            statement_ranges.append((start, end, "body_line"))
        else:
            dropped.append(_dropped(DROP_COMMIT_BODY_LINE_WITHOUT_DECISION_VERB, excerpt))

    issue_refs = tuple(
        dict.fromkeys(ref for _start, _end, refs in trailer_ranges for ref in refs)
    )
    for start, end, refs in trailer_ranges:
        excerpt = content[start:end]
        if not statement_ranges:
            dropped.append(_dropped(DROP_COMMIT_TRAILER_WITHOUT_CANDIDATE, excerpt))
        elif not refs:
            dropped.append(_dropped(DROP_COMMIT_TRAILER_WITHOUT_ISSUE_REF, excerpt))
        # Trailers with refs alongside extracted statements are consumed as
        # scope hints — visible through every candidate's issue_ref scopes.

    extracted = tuple(
        _commit_statement(
            document,
            start,
            end,
            lane=lane,
            statement_kind=kind,
            issue_refs=issue_refs,
        )
        for start, end, kind in statement_ranges
    )
    return ExtractionOutcome(
        source_type=DeriveSourceType.COMMIT_MESSAGE,
        extractor_id=EXTRACTOR_IDS[DeriveSourceType.COMMIT_MESSAGE],
        extracted=extracted,
        dropped=tuple(dropped),
    )


def _commit_statement(
    document: SourceDocument,
    start: int,
    end: int,
    *,
    lane: LaneAssignment,
    statement_kind: str,
    issue_refs: tuple[str, ...],
) -> ExtractedCandidate:
    span = document.span(start_offset=start, end_offset=end)
    pairs = [(scope.scope_type, scope.value) for scope in proposed_path_scopes(span.excerpt)]
    pairs.extend((ScopeType.ISSUE_REF, ref) for ref in issue_refs)
    candidate = DeriveCandidate(
        decision_text=span.excerpt,
        spans=(span,),
        proposed_scopes=_deduped_scopes(pairs),
    )
    return ExtractedCandidate(
        candidate=candidate,
        lane=lane,
        metadata={"statement_kind": statement_kind, "issue_refs": list(issue_refs)},
    )


def _commit_trailer_issue_refs(line: str) -> tuple[str, ...] | None:
    """Return issue-ref tokens for a trailer line, or None for non-trailers."""

    match = _COMMIT_ISSUE_TRAILER_RE.match(line) or _COMMIT_BREAKING_TRAILER_RE.match(line)
    if match is None:
        return None
    return tuple(
        dict.fromkeys(token.group(0) for token in _ISSUE_REF_TOKEN_RE.finditer(match.group(1)))
    )


# ---------------------------------------------------------------------------
# #355 — PR descriptions (sections matter)
# ---------------------------------------------------------------------------


def extract_pr_description_decisions(document: SourceDocument) -> ExtractionOutcome:
    """Extract decision statements from one PR description (issue #355).

    Sections matter: item and paragraph blocks under ``## Why`` /
    ``## Decision`` / ``## Approach`` headings become candidates with exact
    spans. Checklists (task-list items), template stubs (HTML comments), and
    boilerplate (attribution footers, Co-Authored-By trailers) drop with
    reason codes wherever they appear; everything outside the decision
    sections drops as ``outside_decision_section`` — never silently.
    """

    content = document.content
    lane = DEFAULT_LANE_POLICY.assign(DeriveSourceType.PR_DESCRIPTION, backfilled=False)
    extracted: list[ExtractedCandidate] = []
    dropped: list[DroppedChatter] = []

    for block in _instruction_blocks(content):
        excerpt = content[block.start : block.end]
        if block.kind == "heading":
            dropped.append(_dropped(DROP_PR_HEADING_ONLY, excerpt))
            continue
        if block.kind == "code":
            dropped.append(_dropped(DROP_PR_CODE_BLOCK, excerpt))
            continue
        if block.kind == "table":
            dropped.append(_dropped(DROP_PR_TABLE, excerpt))
            continue
        if _PR_TEMPLATE_STUB_RE.match(excerpt) or _PR_BOILERPLATE_RE.match(excerpt):
            dropped.append(_dropped(DROP_PR_TEMPLATE_STUB, excerpt))
            continue
        if block.kind == "item" and _PR_CHECKLIST_LEAD_RE.match(excerpt):
            dropped.append(_dropped(DROP_PR_CHECKLIST_ITEM, excerpt))
            continue
        if _is_link_only(excerpt):
            dropped.append(_dropped(DROP_PR_LINK_ONLY, excerpt))
            continue
        if not _is_pr_decision_section(block.section_title):
            dropped.append(_dropped(DROP_PR_OUTSIDE_DECISION_SECTION, excerpt))
            continue
        span = document.span(start_offset=block.start, end_offset=block.end)
        extracted.append(
            ExtractedCandidate(
                candidate=DeriveCandidate(
                    decision_text=span.excerpt,
                    spans=(span,),
                    proposed_scopes=proposed_path_scopes(span.excerpt),
                ),
                lane=lane,
                metadata={"section": block.section_title, "statement_kind": block.kind},
            )
        )

    return ExtractionOutcome(
        source_type=DeriveSourceType.PR_DESCRIPTION,
        extractor_id=EXTRACTOR_IDS[DeriveSourceType.PR_DESCRIPTION],
        extracted=tuple(extracted),
        dropped=tuple(dropped),
    )


def _is_pr_decision_section(section_title: str) -> bool:
    return section_title.strip().strip(":").strip().lower() in _PR_DECISION_SECTION_TITLES


# ---------------------------------------------------------------------------
# #356 — PR review comments (lowest-precision Tier-1 source)
# ---------------------------------------------------------------------------


def extract_pr_review_comment_rules(document: SourceDocument) -> ExtractionOutcome:
    """Extract proposed rules from one PR review comment (issue #356).

    A sentence proposing a rule ("we should always/never ...",
    "convention: ...", "going forward ...") becomes one candidate with an
    exact sentence span. Comments with no rule proposal drop whole with a
    reason code: pure approvals (``lgtm``), nits (``typo``), emoji-only
    comments, and everything else as ``no_rule_proposal``.

    Cold-start caveat (Obsidian master plan): bad backfill is worse than an
    empty graph. Mined-from-review candidates are the lowest-precision Tier-1
    source, so every #356 candidate is stamped ``backfilled=True`` — which,
    per the master-plan non-negotiable ("backfilled nodes default
    advisory-only and are never auto-promotable",
    ``cortex.hosted.lane_assignment.BACKFILL_ADVISORY_ONLY_RULE``), forces
    advisory-only entry; ``LaneAssignment`` makes the violating state
    unrepresentable.
    """

    content = document.content
    # backfilled=True ALWAYS for review-mined candidates — see the cold-start
    # caveat in the docstring above.
    lane = DEFAULT_LANE_POLICY.assign(DeriveSourceType.PR_REVIEW_COMMENT, backfilled=True)

    rule_ranges = [
        (start, end)
        for start, end in _sentence_ranges(content)
        if _REVIEW_RULE_RE.search(content[start:end])
    ]
    if rule_ranges:
        extracted = tuple(
            ExtractedCandidate(
                candidate=DeriveCandidate(
                    decision_text=content[start:end],
                    spans=(document.span(start_offset=start, end_offset=end),),
                    proposed_scopes=proposed_path_scopes(content[start:end]),
                ),
                lane=lane,
                metadata={"rule_kind": "review_rule"},
            )
            for start, end in rule_ranges
        )
        return _review_comment_outcome(extracted, ())

    trimmed = content.strip()
    if _NO_ALNUM_RE.match(trimmed):
        reason = DROP_REVIEW_EMOJI_ONLY
    elif _is_review_approval_only(trimmed):
        reason = DROP_REVIEW_APPROVAL_ONLY
    elif _REVIEW_NIT_RE.match(trimmed):
        reason = DROP_REVIEW_NIT_ONLY
    else:
        reason = DROP_REVIEW_NO_RULE_PROPOSAL
    return _review_comment_outcome((), (_dropped(reason, content),))


def _review_comment_outcome(
    extracted: tuple[ExtractedCandidate, ...],
    dropped: tuple[DroppedChatter, ...],
) -> ExtractionOutcome:
    return ExtractionOutcome(
        source_type=DeriveSourceType.PR_REVIEW_COMMENT,
        extractor_id=EXTRACTOR_IDS[DeriveSourceType.PR_REVIEW_COMMENT],
        extracted=extracted,
        dropped=dropped,
    )


def _is_review_approval_only(text: str) -> bool:
    normalized = " ".join(text.lower().split()).strip(_REVIEW_NORMALIZE_STRIP_CHARS)
    return normalized in _REVIEW_APPROVAL_PHRASES


# ---------------------------------------------------------------------------
# Gathering helpers: SourceDocuments from `git log` / `gh` JSON (#354-#356)
# ---------------------------------------------------------------------------

# A runner executes one local git/gh command in a working directory and
# returns its stdout. Injection point for tests: unit tests replay committed
# fixture captures through a stub runner — no subprocess, no network.
TextCommandRunner = Callable[[Sequence[str], Path], str]

# git log record layout: sha / author / strict-ISO author date / raw body,
# unit-separated (%x1f) with a record separator (%x1e) so multi-line commit
# messages survive parsing.
_GIT_LOG_FIELD_SEP = "\x1f"
_GIT_LOG_RECORD_SEP = "\x1e"
GIT_LOG_PRETTY_FORMAT = "%H%x1f%an <%ae>%x1f%aI%x1f%B%x1e"
PR_VIEW_JSON_FIELDS = "number,title,body,author,createdAt,url"


@dataclass(frozen=True)
class GatheredDocuments:
    """Documents a gather step produced, plus its visible drop records.

    The visibility invariant extends to gathering: source material that
    cannot become a ``SourceDocument`` (an empty PR body, an empty review
    comment) is a ``DroppedSourceChatter`` record, never a silent skip. The
    drop's ``excerpt_hash`` covers the external id because there is no
    content to hash.
    """

    documents: tuple[SourceDocument, ...]
    dropped: tuple[DroppedSourceChatter, ...] = ()


def run_text_command(argv: Sequence[str], cwd: Path) -> str:
    """Run one local git/gh command and return stdout, failing closed."""

    try:
        completed = subprocess.run(
            list(argv), cwd=cwd, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise ExtractorError(f"cannot run {argv[0]!r}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ExtractorError(
            f"`{' '.join(argv)}` failed with exit code {completed.returncode}: {detail}"
        )
    return completed.stdout


def commit_message_documents(
    git_log_output: str, *, tenant_id: str, source_id: str
) -> GatheredDocuments:
    """Build one ``commit_message`` document per commit from raw ``git log``.

    Expects ``GIT_LOG_PRETTY_FORMAT`` records. Malformed records fail closed
    (a truncated capture must never half-ingest); commits with empty messages
    drop visibly. Documents are emitted in deterministic
    (author timestamp, sha) ascending order.
    """

    documents: list[SourceDocument] = []
    dropped: list[DroppedSourceChatter] = []
    parsed: list[tuple[datetime, str, str, str]] = []
    for raw_record in git_log_output.split(_GIT_LOG_RECORD_SEP):
        record = raw_record.lstrip("\n")
        if not record.strip():
            continue
        fields = record.split(_GIT_LOG_FIELD_SEP)
        if len(fields) != 4:
            raise ExtractorError(
                f"malformed git log record: expected 4 {_GIT_LOG_FIELD_SEP!r}-separated "
                f"fields, got {len(fields)} (capture must use GIT_LOG_PRETTY_FORMAT)"
            )
        sha, author, timestamp_raw, message = fields
        sha = sha.strip()
        if not _COMMIT_SHA_RE.match(sha):
            raise ExtractorError(f"malformed git log record: {sha!r} is not a commit sha")
        author = author.strip()
        if not author:
            raise ExtractorError(f"commit {sha}: author field is empty")
        timestamp = _parse_source_timestamp(timestamp_raw.strip(), label=f"commit {sha}")
        message = message.rstrip("\n")
        if not message.strip():
            dropped.append(
                DroppedSourceChatter(
                    external_id=sha, chatter=_dropped(DROP_COMMIT_EMPTY_MESSAGE, sha)
                )
            )
            continue
        parsed.append((timestamp, sha, author, message))
    parsed.sort(key=lambda record: (record[0], record[1]))
    documents.extend(
        SourceDocument(
            tenant_id=tenant_id,
            source_id=source_id,
            document_type=COMMIT_MESSAGE_DOCUMENT_TYPE,
            external_id=sha,
            permalink=f"commit:{sha}",
            author_ref=author,
            source_timestamp=timestamp,
            content=message,
        )
        for timestamp, sha, author, message in parsed
    )
    return GatheredDocuments(documents=tuple(documents), dropped=tuple(dropped))


def gather_commit_message_documents(
    project_root: Path,
    *,
    tenant_id: str,
    source_id: str,
    limit: int,
    runner: TextCommandRunner = run_text_command,
) -> GatheredDocuments:
    """Gather the last ``limit`` commit messages from the local git history."""

    if limit < 1:
        raise ExtractorError("commit gather limit must be >= 1")
    raw = runner(
        ["git", "log", "-n", str(limit), f"--pretty=format:{GIT_LOG_PRETTY_FORMAT}"],
        project_root,
    )
    return commit_message_documents(raw, tenant_id=tenant_id, source_id=source_id)


def pr_description_documents(
    payload: Mapping[str, Any], *, tenant_id: str, source_id: str
) -> GatheredDocuments:
    """Build one ``pr_description`` document from ``gh pr view`` JSON.

    Expects the ``PR_VIEW_JSON_FIELDS`` shape
    (``gh pr view N --json number,title,body,author,createdAt,url``).
    Missing or mistyped fields fail closed; an empty body drops visibly.
    """

    number = _json_field(payload, "number", int, label="gh pr view payload")
    label = f"pr-{number}"
    title = _json_field(payload, "title", str, label=label)
    body = _json_field(payload, "body", str, label=label)
    author = _json_field(payload, "author", dict, label=label)
    login = _json_field(author, "login", str, label=f"{label} author")
    created_at = _parse_source_timestamp(
        _json_field(payload, "createdAt", str, label=label), label=label
    )
    url = _json_field(payload, "url", str, label=label)
    if not body.strip():
        return GatheredDocuments(
            documents=(),
            dropped=(
                DroppedSourceChatter(
                    external_id=label, chatter=_dropped(DROP_PR_EMPTY_BODY, label)
                ),
            ),
        )
    document = SourceDocument(
        tenant_id=tenant_id,
        source_id=source_id,
        document_type=PR_DESCRIPTION_DOCUMENT_TYPE,
        external_id=label,
        permalink=url,
        author_ref=login,
        source_timestamp=created_at,
        content=body,
        metadata={"pr_number": number, "pr_title": title},
    )
    return GatheredDocuments(documents=(document,))


def pr_review_comment_documents(
    payload: object, *, pr_number: int, tenant_id: str, source_id: str
) -> GatheredDocuments:
    """Build ``pr_review_comment`` documents from GitHub REST comments JSON.

    Expects the ``gh api repos/{owner}/{repo}/pulls/<n>/comments`` array
    shape (``id``, ``user.login``, ``body``, ``created_at``, ``html_url``).
    Missing or mistyped fields fail closed; empty bodies drop visibly.
    Documents are emitted in deterministic (created_at, id) ascending order.
    """

    if not isinstance(payload, list):
        raise ExtractorError(
            f"pr-{pr_number} review comments payload must be a JSON array, "
            f"got {type(payload).__name__}"
        )
    dropped: list[DroppedSourceChatter] = []
    parsed: list[tuple[datetime, int, str, str, str]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ExtractorError(
                f"pr-{pr_number} review comment [{index}] must be a JSON object, "
                f"got {type(item).__name__}"
            )
        label = f"pr-{pr_number} review comment [{index}]"
        comment_id = _json_field(item, "id", int, label=label)
        external_id = f"pr-{pr_number}/review-comment-{comment_id}"
        body = _json_field(item, "body", str, label=label)
        user = _json_field(item, "user", dict, label=label)
        login = _json_field(user, "login", str, label=f"{label} user")
        created_at = _parse_source_timestamp(
            _json_field(item, "created_at", str, label=label), label=label
        )
        html_url = _json_field(item, "html_url", str, label=label)
        if not body.strip():
            dropped.append(
                DroppedSourceChatter(
                    external_id=external_id,
                    chatter=_dropped(DROP_REVIEW_EMPTY_BODY, external_id),
                )
            )
            continue
        parsed.append((created_at, comment_id, login, html_url, body))
    parsed.sort(key=lambda record: (record[0], record[1]))
    documents = tuple(
        SourceDocument(
            tenant_id=tenant_id,
            source_id=source_id,
            document_type=PR_REVIEW_COMMENT_DOCUMENT_TYPE,
            external_id=f"pr-{pr_number}/review-comment-{comment_id}",
            permalink=html_url,
            author_ref=login,
            source_timestamp=created_at,
            content=body,
            metadata={"pr_number": pr_number},
        )
        for created_at, comment_id, login, html_url, body in parsed
    )
    return GatheredDocuments(documents=documents, dropped=tuple(dropped))


def gather_pr_documents(
    project_root: Path,
    *,
    tenant_id: str,
    source_id: str,
    limit: int,
    runner: TextCommandRunner = run_text_command,
) -> GatheredDocuments:
    """Gather the ``limit`` most recently merged PRs via the ``gh`` CLI.

    Per PR: the description (``gh pr view``) and its review comments
    (``gh api repos/{owner}/{repo}/pulls/<n>/comments`` — gh substitutes the
    placeholders from the current repo). PR numbers are processed in
    ascending order so the gathered document sequence is deterministic.
    """

    if limit < 1:
        raise ExtractorError("PR gather limit must be >= 1")
    raw_listing = runner(
        ["gh", "pr", "list", "--state", "merged", "--limit", str(limit), "--json", "number"],
        project_root,
    )
    listing = _load_json(raw_listing, label="gh pr list output")
    if not isinstance(listing, list):
        raise ExtractorError(
            f"gh pr list output must be a JSON array, got {type(listing).__name__}"
        )
    numbers: list[int] = []
    for index, item in enumerate(listing):
        if not isinstance(item, dict):
            raise ExtractorError(
                f"gh pr list item [{index}] must be a JSON object, "
                f"got {type(item).__name__}"
            )
        numbers.append(_json_field(item, "number", int, label=f"gh pr list item [{index}]"))

    documents: list[SourceDocument] = []
    dropped: list[DroppedSourceChatter] = []
    for number in sorted(dict.fromkeys(numbers)):
        raw_view = runner(
            ["gh", "pr", "view", str(number), "--json", PR_VIEW_JSON_FIELDS], project_root
        )
        view_payload = _load_json(raw_view, label=f"gh pr view {number} output")
        if not isinstance(view_payload, dict):
            raise ExtractorError(
                f"gh pr view {number} output must be a JSON object, "
                f"got {type(view_payload).__name__}"
            )
        description = pr_description_documents(
            view_payload, tenant_id=tenant_id, source_id=source_id
        )
        documents.extend(description.documents)
        dropped.extend(description.dropped)
        raw_comments = runner(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{number}/comments"], project_root
        )
        comments = pr_review_comment_documents(
            _load_json(raw_comments, label=f"pr {number} review comments output"),
            pr_number=number,
            tenant_id=tenant_id,
            source_id=source_id,
        )
        documents.extend(comments.documents)
        dropped.extend(comments.dropped)
    return GatheredDocuments(documents=tuple(documents), dropped=tuple(dropped))


def _parse_source_timestamp(raw: str, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ExtractorError(f"{label}: invalid ISO-8601 timestamp {raw!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExtractorError(f"{label}: timestamp {raw!r} must be timezone-aware")
    return parsed


def _load_json(raw: str, *, label: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExtractorError(f"{label} is not valid JSON: {exc}") from exc


def _json_field(
    payload: Mapping[str, Any], key: str, expected: type, *, label: str
) -> Any:
    if key not in payload:
        raise ExtractorError(f"{label}: missing field {key!r}")
    value = payload[key]
    # bool subclasses int; an int field carrying True/False is a malformed payload.
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ExtractorError(
            f"{label}: field {key!r} must be {expected.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


# ---------------------------------------------------------------------------
# Dispatch + ledger event construction (the derive scaffold plug-in surface)
# ---------------------------------------------------------------------------


def classify_source(document: SourceDocument) -> DeriveSourceType:
    """Route one source document to its repo-native extractor, fail-closed.

    Recognition rules: the gathered text sources (#354-#356) route by
    ``document_type`` (``commit_message`` / ``pr_description`` /
    ``pr_review_comment``) because they are not repo files. Repo files route
    by filename (#351-#353): ``CODEOWNERS``; ``CLAUDE.md`` / ``AGENTS.md``;
    ADRs by an ``adr``/``adrs``/``decisions`` directory component or an
    ``NNNN-*.md`` filename carrying a ``Status:`` header. Anything else
    raises ``ExtractorError`` — an unrecognized source is an error, never a
    silent skip.
    """

    text_source_type = _TEXT_SOURCE_TYPES_BY_DOCUMENT_TYPE.get(document.document_type)
    if text_source_type is not None:
        return text_source_type
    parts = tuple(part for part in document.external_id.replace("\\", "/").split("/") if part)
    if not parts:
        raise ExtractorError("source document external_id has no filename")
    name = parts[-1]
    if name == CODEOWNERS_FILENAME:
        return DeriveSourceType.CODEOWNERS
    if name in AGENT_INSTRUCTION_FILENAMES:
        return DeriveSourceType.AGENT_INSTRUCTIONS
    if any(directory.lower() in ADR_DIRECTORY_NAMES for directory in parts[:-1]):
        return DeriveSourceType.ADR
    if _ADR_FILENAME_RE.match(name) and has_adr_status_header(document.content):
        return DeriveSourceType.ADR
    raise ExtractorError(
        f"no repo-native extractor recognizes {document.external_id!r}; expected "
        "CLAUDE.md/AGENTS.md, an ADR (under an adr/ or decisions/ directory, or "
        "NNNN-*.md with a Status: header), CODEOWNERS, or a gathered document "
        "typed commit_message/pr_description/pr_review_comment"
    )


_EXTRACTORS_BY_SOURCE_TYPE = MappingProxyType(
    {
        DeriveSourceType.AGENT_INSTRUCTIONS: extract_agent_instruction_rules,
        DeriveSourceType.ADR: extract_adr_decision,
        DeriveSourceType.CODEOWNERS: extract_codeowners_rules,
        DeriveSourceType.COMMIT_MESSAGE: extract_commit_message_decisions,
        DeriveSourceType.PR_DESCRIPTION: extract_pr_description_decisions,
        DeriveSourceType.PR_REVIEW_COMMENT: extract_pr_review_comment_rules,
    }
)


def extract_repo_native(document: SourceDocument) -> ExtractionOutcome:
    """Classify one document and run the matching deterministic extractor."""

    return _EXTRACTORS_BY_SOURCE_TYPE[classify_source(document)](document)


def candidate_events(
    document: SourceDocument, outcome: ExtractionOutcome
) -> tuple[LedgerEvent, ...]:
    """Render extracted candidates as ``CANDIDATE_PROPOSED`` ledger events.

    Identity invariant: the idempotency key hashes every input that feeds the
    event hash — extractor id (actor), candidate payload (spans, scopes, lane,
    text), extractor metadata, and the source snapshot path + timestamp — so
    equal keys imply equal event hashes. The derive store's
    same-key/different-hash collision error therefore only fires on real
    divergence, and re-running over unchanged sources is always an ignored
    duplicate, never a collision.
    """

    events: list[LedgerEvent] = []
    for item in outcome.extracted:
        candidate = item.candidate
        payload: dict[str, Any] = {
            "decision_text": candidate.decision_text,
            "lane_assignment": item.lane.as_payload(),
            "proposed_scopes": [scope.as_payload() for scope in candidate.proposed_scopes],
            "source_type": outcome.source_type.value,
            "spans": [
                {
                    "end_offset": span.end_offset,
                    "excerpt": span.excerpt,
                    "permalink": span.permalink,
                    "source_document_hash": span.source_document_hash,
                    "span_hash": span.span_hash,
                    "start_offset": span.start_offset,
                }
                for span in candidate.spans
            ],
        }
        metadata: dict[str, Any] = {
            "document_hash": document.document_hash,
            "extractor": outcome.extractor_id,
            **dict(item.metadata),
        }
        external_ref = (
            f"{document.external_id}"
            f"@{document.source_timestamp.isoformat()}"
            f"#{candidate.spans[0].span_hash}"
        )
        events.append(
            LedgerEvent(
                tenant_id=document.tenant_id,
                source_id=document.source_id,
                event_type=LedgerEventType.CANDIDATE_PROPOSED,
                actor=ActorRef(actor_type="derive", actor_id=outcome.extractor_id),
                occurred_at=document.source_timestamp,
                idempotency_key=derive_idempotency_key(
                    source_id=document.source_id,
                    event_type=LedgerEventType.CANDIDATE_PROPOSED,
                    source_event_external_id=external_ref,
                    payload={
                        "candidate": payload,
                        "extractor": outcome.extractor_id,
                        "metadata": metadata,
                    },
                ),
                source_event_external_id=external_ref,
                source_span_hashes=candidate.span_hashes,
                payload=payload,
                metadata=metadata,
            )
        )
    return tuple(events)


class RepoNativeExtractor:
    """Callable conforming to the derive scaffold's ``CandidateExtractor``.

    ``cortex derive`` passes this instance straight into ``run_derive``; per
    document it dispatches to the matching deterministic extractor, returns
    the candidate events, and accumulates every ``DroppedChatter`` record so
    the CLI can report drops visibly after the run.
    """

    def __init__(self) -> None:
        self._dropped: list[DroppedSourceChatter] = []

    def __call__(self, document: SourceDocument) -> tuple[LedgerEvent, ...]:
        outcome = extract_repo_native(document)
        self._dropped.extend(
            DroppedSourceChatter(external_id=document.external_id, chatter=chatter)
            for chatter in outcome.dropped
        )
        return candidate_events(document, outcome)

    @property
    def dropped(self) -> tuple[DroppedSourceChatter, ...]:
        return tuple(self._dropped)
