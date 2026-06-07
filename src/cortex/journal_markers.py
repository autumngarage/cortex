"""Shared unresolved-template marker patterns for journal automation."""

from __future__ import annotations

import re

UNRESOLVED_MARKER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mustache placeholder `{{ ... }}`", re.compile(r"\{\{.*?\}\}", re.DOTALL)),
    ("HTML comment `<!-- ... -->`", re.compile(r"<!--.*?-->", re.DOTALL)),
    ("`fill on edit` sentinel", re.compile(r"(?i)fill on edit")),
    (
        "`none recorded` edit sentinel",
        re.compile(r"(?i)none recorded[^\n]*fill on edit"),
    ),
    (
        "unfilled checklist placeholder `- [ ] {{ ... }}`",
        re.compile(r"^- \[ \] (?:\{\{|<[a-z]).*$", re.MULTILINE),
    ),
)
