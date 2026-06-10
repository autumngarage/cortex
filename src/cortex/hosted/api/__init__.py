"""Hosted Cortex HTTP API shell (cortex#470).

Deliberately import-light: submodules (``config``, ``webhooks``, ``app``)
are imported directly by callers so that importing the package never pulls
the HTTP transport or the degradation taxonomy into modules that only need
configuration types.
"""
