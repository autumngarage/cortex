# State hand markers use per-section pairs

**Date:** 2026-04-27
**Type:** decision
**Trigger:** T2.1
**Cites:** plans/cortex-v1, .cortex/state.md, src/cortex/state_render.py

> `cortex refresh-state` preserves hand-authored content with one marker pair per intended region, rather than a single outer pair around every manual section.

## Context

Tier 2 of `plans/cortex-v1` makes `state.md` a derived artifact. Three sections are regenerated from primary sources (`## Active plans`, `## Shipped recently`, and `## Stale-now / handle-later`), but `## Current work` still carries human judgment about release sequencing and dogfood framing. The renderer therefore needs a narrow preservation contract that survives repeated regeneration without letting hand-authored prose swallow computed sections.

## What we decided

Hand-authored State regions use per-section marker pairs:

```md
<!-- cortex:hand -->
## Current work

...
<!-- cortex:end-hand -->
```

Multiple pairs may coexist in one `state.md`. The renderer preserves only marker comments that appear on their own lines, so inline prose that names the marker strings is not treated as a preservation boundary.

## Consequences / action items

- [x] Wrap this repo's `## Current work` section in `.cortex/state.md`.
- [x] Add regression tests for one marker pair and multiple marker pairs in `tests/test_refresh_state.py`.
