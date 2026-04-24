---
Generated: 2026-04-17T15:30:00-07:00
Generator: hand-authored (regeneration infrastructure ships in Phase E via `cortex refresh-map` per plans/cortex-v1; the 2026-04-23 reorder + 2026-04-24 consolidation moved LLM synthesis from the original Phase C into Phase E)
Sources:
  - (none — pending Phase E synthesis)
Corpus: 0 files (no synthesis yet)
Omitted: []
Incomplete:
  - All sources — Phase E ships `cortex refresh-map` which will regenerate this file from directory tree, `pyproject.toml`/`package.json`/`Cargo.toml`, git log, and Doctrine entries. Until then, this is a best-effort stub.
Conflicts-preserved: []
Spec: 0.3.1
---

# Project Map

> **Stub — pending Phase E synthesis.** This repo is spec-stage: the directory structure is self-evident from the root `README.md`, `SPEC.md`, the single plan at `.cortex/plans/cortex-v1.md`, and the `.cortex/` layout itself. When the CLI ships `cortex refresh-map` (Phase E), this file becomes a regenerated structural summary. Until then, treat the root-level docs as the authoritative map.

## Pending structural narrative

When `cortex refresh-map` lands, it will produce sections covering:

- **Top-level artifacts.** `SPEC.md`, `README.md`, `docs/PRIOR_ART.md`, `.cortex/` (incl. `.cortex/plans/cortex-v1.md` as the single active plan) — each with a one-sentence purpose.
- **`.cortex/` layout.** Doctrine (scope and why), Plans (active work), Journal (append-only decision trail), Templates (write scaffolds), Protocol (agent contract), State (priorities).
- **Touchstone integration.** `principles/`, `scripts/`, `.pre-commit-config.yaml`, `.touchstone-config` — all project-owned but synced from the upstream Touchstone package.
- **Future CLI source layout.** `src/cortex/` (when Phase B scaffolds it), `tests/`, `pyproject.toml`.

No structural narrative is synthesized at this stage; consumers should read the root-level documents directly.
