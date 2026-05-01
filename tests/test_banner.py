"""Tests for the Cortex ASCII banner."""

from __future__ import annotations

from cortex.banner import render_banner

CANONICAL_WORDMARK = "\n".join(
    (
        r"  ____           _            ",
        r" / ___|___  _ __| |_ _____  __",
        r"| |   / _ \| '__| __/ _ \ \/ /",
        r"| |__| (_) | |  | ||  __/>  < ",
        " \\____\\___/|_|   \\__\\___/_/\\_\\",
    )
)


def test_render_banner_includes_wordmark_and_attribution() -> None:
    lines = render_banner(use_color=False)

    assert len(lines) >= 7
    assert any(line.strip() == "by Autumn Garage" for line in lines)


def test_render_banner_without_color_has_no_ansi_escapes() -> None:
    rendered = "\n".join(render_banner(use_color=False))

    assert "\033[" not in rendered


def test_render_banner_subtitle_line_includes_subtitle_and_version() -> None:
    lines = render_banner(subtitle="x", version="0.1.0", use_color=False)

    assert "  x  ·  v0.1.0" in lines


def test_render_banner_glyphs_match_canonical_block() -> None:
    lines = render_banner(use_color=False)
    glyphs = "\n".join(line[2:] for line in lines[1:6])

    assert glyphs == CANONICAL_WORDMARK
