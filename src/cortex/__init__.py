"""Cortex — a file-format protocol for per-project memory, and the reference CLI."""

__version__ = "0.3.0"

SUPPORTED_SPEC_VERSIONS: tuple[str, ...] = ("0.3", "0.4", "0.5")
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("0.2",)

__all__ = ["SUPPORTED_PROTOCOL_VERSIONS", "SUPPORTED_SPEC_VERSIONS", "__version__"]
