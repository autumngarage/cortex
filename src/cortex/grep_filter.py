"""Pure frontmatter filters for `cortex grep`.

The command layer is responsible for reading files. This module only parses
filter expressions and evaluates them against already-extracted metadata.
"""

from __future__ import annotations

from dataclasses import dataclass

from cortex.frontmatter import FrontmatterValue


@dataclass(frozen=True)
class FrontmatterFilter:
    """A single `--frontmatter` predicate.

    Keys are matched case-insensitively. Values are exact and case-sensitive,
    except `*`, which means the key is present with any non-empty value.
    """

    key: str
    value: str
    negated: bool = False

    def matches(self, frontmatter: dict[str, FrontmatterValue], bold_fields: dict[str, str]) -> bool:
        matched = _positive_match(self.key, self.value, frontmatter, bold_fields)
        return not matched if self.negated else matched


def parse_frontmatter_filter(raw: str) -> FrontmatterFilter:
    """Parse `key:value`, `!key:value`, and `key:*` filter expressions."""
    negated = raw.startswith("!")
    expression = raw[1:] if negated else raw
    if ":" not in expression:
        raise ValueError("frontmatter filters must use key:value syntax")
    key, _, value = expression.partition(":")
    key = key.strip()
    value = value.strip()
    if not key:
        raise ValueError("frontmatter filter key cannot be empty")
    if not value:
        raise ValueError("frontmatter filter value cannot be empty")
    return FrontmatterFilter(key=key, value=value, negated=negated)


def matches_all(
    filters: tuple[FrontmatterFilter, ...],
    frontmatter: dict[str, FrontmatterValue],
    bold_fields: dict[str, str],
) -> bool:
    """Return True when every frontmatter filter matches."""
    return all(filter_.matches(frontmatter, bold_fields) for filter_ in filters)


def _positive_match(
    key: str,
    requested: str,
    frontmatter: dict[str, FrontmatterValue],
    bold_fields: dict[str, str],
) -> bool:
    values = _values_for_key(key, frontmatter, bold_fields)
    if requested == "*":
        return any(value != "" for value in values)
    return any(value == requested for value in values)


def _values_for_key(
    requested_key: str,
    frontmatter: dict[str, FrontmatterValue],
    bold_fields: dict[str, str],
) -> list[str]:
    requested = requested_key.casefold()
    values: list[str] = []
    for key, value in frontmatter.items():
        if key.casefold() != requested:
            continue
        if isinstance(value, list):
            values.extend(value)
        else:
            values.append(value)
    for key, value in bold_fields.items():
        if key.casefold() == requested:
            values.append(value)
    return values
