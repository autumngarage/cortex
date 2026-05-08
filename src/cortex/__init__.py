"""Cortex — a file-format protocol for per-project memory, and the reference CLI."""

__version__ = "1.5.1"
SPEC_VERSION_LITERAL = "1.1.0"

SUPPORTED_SPEC_VERSIONS: tuple[str, ...] = ("0.3", "0.4", "0.5", "1.0", "1.1")
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("0.2", "0.3")

__all__ = [
    "SPEC_VERSION_LITERAL",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "SUPPORTED_SPEC_VERSIONS",
    "__version__",
]
