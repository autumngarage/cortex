"""Unit tests for the minimal frontmatter parser."""

from __future__ import annotations

from cortex.frontmatter import parse_frontmatter


def test_scalar_values() -> None:
    text = "---\nStatus: active\nAuthor: human\n---\nbody\n"
    data, body = parse_frontmatter(text)
    assert data == {"Status": "active", "Author": "human"}
    assert body == "body\n"


def test_block_sequence() -> None:
    text = "---\nUpdated-by:\n  - 2026-04-17T10:00 human\n  - 2026-04-17T14:22 claude\n---\nbody\n"
    data, _ = parse_frontmatter(text)
    assert data["Updated-by"] == ["2026-04-17T10:00 human", "2026-04-17T14:22 claude"]


def test_flow_sequence() -> None:
    text = "---\nIncomplete: []\nOmitted: [noisy.md, wip.md]\n---\n"
    data, _ = parse_frontmatter(text)
    assert data["Incomplete"] == []
    assert data["Omitted"] == ["noisy.md", "wip.md"]


def test_value_with_colon_preserved() -> None:
    text = "---\nGenerated: 2026-04-17T14:22:00-04:00\n---\n"
    data, _ = parse_frontmatter(text)
    assert data["Generated"] == "2026-04-17T14:22:00-04:00"


def test_no_frontmatter_returns_empty() -> None:
    data, body = parse_frontmatter("just a body\n")
    assert data == {}
    assert body == "just a body\n"


def test_unterminated_frontmatter_returns_empty() -> None:
    data, body = parse_frontmatter("---\nStatus: active\nbody without closing fence\n")
    assert data == {}
    assert body.startswith("---")


def test_quoted_values_stripped() -> None:
    text = "---\nTitle: 'quoted'\nOther: \"double\"\n---\n"
    data, _ = parse_frontmatter(text)
    assert data == {"Title": "quoted", "Other": "double"}
