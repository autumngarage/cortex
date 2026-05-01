"""ASCII hero banner for `cortex init`, `doctor`, and other splash moments.

Embedded standard-font figlet glyphs with ANSI color. No runtime figlet
dependency. Color emitted only when stderr is a TTY and NO_COLOR is unset.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

_CORTEX_GLYPHS: tuple[str, ...] = (
    r"  ____           _            ",
    r" / ___|___  _ __| |_ _____  __",
    r"| |   / _ \| '__| __/ _ \ \/ /",
    r"| |__| (_) | |  | ||  __/>  < ",
    " \\____\\___/|_|   \\__\\___/_/\\_\\",
)

# Pale aqua distinguishes cortex from touchstone's peach, sentinel's sage,
# and conductor's lavender when the tools print side by side.
_ANSI_AQUA = "\033[38;5;152m"
_ANSI_PALE_CYAN = "\033[38;5;159m"
_ANSI_RESET = "\033[0m"


def _color_enabled(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR") == "0":
        return False
    try:
        return bool(stream.isatty())
    except ValueError:
        return False


def render_banner(
    subtitle: str | None = None,
    version: str | None = None,
    *,
    use_color: bool = True,
) -> list[str]:
    lines: list[str] = [""]
    for glyph in _CORTEX_GLYPHS:
        if use_color:
            lines.append(f"  {_ANSI_AQUA}{glyph}{_ANSI_RESET}")
        else:
            lines.append(f"  {glyph}")

    sub_parts: list[str] = []
    if subtitle:
        sub_parts.append(subtitle)
    if version:
        sub_parts.append(f"v{version}")
    if sub_parts:
        sub_text = "  ·  ".join(sub_parts)
        if use_color:
            lines.append(f"  {_ANSI_PALE_CYAN}{sub_text}{_ANSI_RESET}")
        else:
            lines.append(f"  {sub_text}")

    if use_color:
        lines.append(f"  {_ANSI_PALE_CYAN}by Autumn Garage{_ANSI_RESET}")
    else:
        lines.append("  by Autumn Garage")
    lines.append("")
    return lines


def print_banner(
    subtitle: str | None = None,
    version: str | None = None,
    *,
    stream: TextIO | None = None,
) -> None:
    target = stream if stream is not None else sys.stderr
    use_color = _color_enabled(target)
    for line in render_banner(subtitle, version, use_color=use_color):
        print(line, file=target)


def cortex_version() -> str | None:
    from cortex import __version__

    return str(__version__) if __version__ else None


SUBTITLE_INIT = "project memory for AI-assisted work"
SUBTITLE_DOCTOR = "manifest health check"
