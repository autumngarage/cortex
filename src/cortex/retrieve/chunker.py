"""Markdown-aware chunking for Cortex retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_TOKENS = 700
MAX_TOKENS = 800
OVERLAP_TOKENS = 125

SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)


@dataclass(frozen=True)
class MarkdownChunk:
    """A retrievable markdown chunk with source line bounds."""

    chunk_idx: int
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True)
class Paragraph:
    """A paragraph block with line bounds."""

    text: str
    start_line: int
    end_line: int


def estimate_tokens(text: str) -> int:
    """Estimate token count with the S1 word-count heuristic."""

    words = re.findall(r"\S+", text)
    return int(len(words) * 1.3)


def chunk_markdown(text: str) -> list[MarkdownChunk]:
    """Split markdown into section-first chunks.

    Invariant: every returned chunk has non-empty content and start/end line
    bounds that point back into the source document.
    """

    sections = _split_sections(text)
    chunks: list[MarkdownChunk] = []
    for section_text, start_line in sections:
        section_text = section_text.strip("\n")
        if not section_text.strip():
            continue
        end_line = start_line + len(section_text.splitlines()) - 1
        if estimate_tokens(section_text) <= MAX_TOKENS:
            chunks.append(
                MarkdownChunk(
                    chunk_idx=len(chunks),
                    start_line=start_line,
                    end_line=end_line,
                    content=section_text,
                )
            )
            continue
        for split in _split_long_section(section_text, start_line):
            chunks.append(
                MarkdownChunk(
                    chunk_idx=len(chunks),
                    start_line=split.start_line,
                    end_line=split.end_line,
                    content=split.text,
                )
            )
    return chunks


def _split_sections(text: str) -> list[tuple[str, int]]:
    lines = text.splitlines()
    if not lines:
        return []

    starts = [idx for idx, line in enumerate(lines) if line.startswith("## ")]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)

    sections: list[tuple[str, int]] = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        section = "\n".join(lines[start:end])
        sections.append((section, start + 1))
    return sections


def _paragraphs(text: str, start_line: int) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        if idx >= len(lines):
            break
        first = idx
        parts: list[str] = []
        while idx < len(lines) and lines[idx].strip():
            parts.append(lines[idx])
            idx += 1
        paragraphs.append(
            Paragraph(
                text="\n".join(parts),
                start_line=start_line + first,
                end_line=start_line + idx - 1,
            )
        )
    return paragraphs


def _split_long_section(text: str, start_line: int) -> list[Paragraph]:
    paragraphs = _paragraphs(text, start_line)
    if not paragraphs:
        return []

    chunks: list[list[Paragraph]] = []
    current: list[Paragraph] = []
    current_tokens = 0
    for paragraph in paragraphs:
        paragraph_tokens = estimate_tokens(paragraph.text)
        if current and current_tokens + paragraph_tokens > TARGET_TOKENS:
            chunks.append(current)
            current = [paragraph]
            current_tokens = paragraph_tokens
            continue
        current.append(paragraph)
        current_tokens += paragraph_tokens
    if current:
        chunks.append(current)

    rendered: list[Paragraph] = []
    for idx, group in enumerate(chunks):
        render_group = list(group)
        if idx + 1 < len(chunks):
            overlap: list[Paragraph] = []
            overlap_tokens = 0
            for paragraph in chunks[idx + 1]:
                overlap.append(paragraph)
                overlap_tokens += estimate_tokens(paragraph.text)
                if overlap_tokens >= OVERLAP_TOKENS:
                    break
            render_group.extend(overlap)
        content = "\n\n".join(p.text for p in render_group)
        rendered.append(
            Paragraph(
                text=content,
                start_line=group[0].start_line,
                end_line=render_group[-1].end_line,
            )
        )
    return rendered
