"""DEMO ONLY — a deliberate decision-contradiction to exercise Compass Review.

This module imports touchstone directly, which the confirmed compose-by-file-
contract decision forbids. It exists solely so Compass Review (the hosted
Cortex reviewer) has a real contradiction to catch on a live PR. Do not merge.
"""

import touchstone


def sync_principles() -> None:
    touchstone.sync(".")
