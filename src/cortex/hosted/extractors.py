"""Deterministic repo-native candidate extractors (cortex#351, #352, #353).

Stage 0 derive reconstructs the decision surface from the three
highest-precision structured sources a repo already has:

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

Every extractor is **deterministic — no model calls**. These are the Tier-1
structured sources from the derive brief; the model-backed derive boundary
(`cortex.hosted.model_interfaces`) is a different lane entirely. Determinism
invariant: the same document content always yields identical candidates,
identical span hashes, and identical ledger idempotency keys.

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
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    }
)

AGENT_INSTRUCTION_FILENAMES = frozenset({"CLAUDE.md", "AGENTS.md"})
CODEOWNERS_FILENAME = "CODEOWNERS"
ADR_DIRECTORY_NAMES = frozenset({"adr", "adrs", "decisions"})

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
# Dispatch + ledger event construction (the derive scaffold plug-in surface)
# ---------------------------------------------------------------------------


def classify_source(document: SourceDocument) -> DeriveSourceType:
    """Route one source document to its repo-native extractor, fail-closed.

    Recognition rules (issues #351-#353): ``CODEOWNERS`` by filename;
    ``CLAUDE.md`` / ``AGENTS.md`` by filename; ADRs by an ``adr``/``adrs``/
    ``decisions`` directory component or an ``NNNN-*.md`` filename carrying a
    ``Status:`` header. Anything else raises ``ExtractorError`` — an
    unrecognized source is an error, never a silent skip.
    """

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
        "NNNN-*.md with a Status: header), or CODEOWNERS"
    )


_EXTRACTORS_BY_SOURCE_TYPE = MappingProxyType(
    {
        DeriveSourceType.AGENT_INSTRUCTIONS: extract_agent_instruction_rules,
        DeriveSourceType.ADR: extract_adr_decision,
        DeriveSourceType.CODEOWNERS: extract_codeowners_rules,
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
