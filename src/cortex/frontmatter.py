"""Minimal YAML-frontmatter extractor for `.cortex/` validation.

Cortex deliberately ships without a full YAML dependency; the frontmatter
shapes shipped by the Protocol and SPEC are a constrained subset that this
parser covers exactly:

- A scalar: ``key: value``
- A flow-sequence: ``key: [a, b]``
- A block-sequence::

    key:
      - item-1
      - item-2

Anything richer (nested mappings, multi-line folded scalars, anchors, tags)
is out of scope — if a future SPEC field needs it, we add proper YAML at
that point. Until then, keeping this in-repo means Cortex has zero runtime
dependencies beyond click.
"""

from __future__ import annotations

import re

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.DOTALL)

FrontmatterValue = str | list[str]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_flow_sequence(value: str) -> list[str]:
    # `[a, b, c]` → ["a", "b", "c"]. Empty `[]` → [].
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [_strip_quotes(item.strip()) for item in inner.split(",")]


def parse_frontmatter(text: str) -> tuple[dict[str, FrontmatterValue], str]:
    """Return ``(frontmatter, body)``.

    If ``text`` does not begin with a ``---`` fence, returns ``({}, text)``.
    A malformed or unterminated frontmatter block also returns ``({}, text)``
    — callers enforcing "must have frontmatter" should treat empty-dict as a
    missing-frontmatter signal.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    block, body = match.group(1), match.group(2)
    data: dict[str, FrontmatterValue] = {}
    current_list_key: str | None = None

    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue

        # Block-sequence continuation: `  - item`
        if current_list_key is not None and re.match(r"^\s+-\s+", line):
            item = re.sub(r"^\s+-\s+", "", line)
            value = data[current_list_key]
            if isinstance(value, list):
                value.append(_strip_quotes(item.strip()))
            continue

        # Any non-list line resets list context.
        current_list_key = None

        if ":" not in line:
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()

        if not rest:
            # Opens a block-sequence.
            data[key] = []
            current_list_key = key
            continue

        if rest.startswith("[") and rest.endswith("]"):
            data[key] = _parse_flow_sequence(rest)
            continue

        data[key] = _strip_quotes(rest)

    return data, body
