"""Cortex — a file-format protocol for per-project memory, and the reference CLI."""

__version__ = "0.1.0.dev0"

SUPPORTED_SPEC_VERSIONS: tuple[str, ...] = ("0.3",)
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("0.2",)

__all__ = ["SUPPORTED_PROTOCOL_VERSIONS", "SUPPORTED_SPEC_VERSIONS", "__version__"]
