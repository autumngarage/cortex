---
Generated: 2026-04-24T16:55:00-04:00
Generator: hand-authored (`cortex refresh-map` LLM regeneration was in original Phase E but is **deferred from the v1.0 path** to v1.x per the 2026-04-24 production-release rerank — solo author already knows the map; real value lands when a contributor lands on the dogfood target's repo or a fresh agent on a clone gets visibly confused by this stub. See plans/cortex-v1 ## Follow-ups (deferred).)
Sources:
  - .cortex/plans/cortex-v1.md `## Follow-ups (deferred)` #1 (the deferral target)
  - .cortex/journal/2026-04-24-production-release-rerank.md (the deferral decision)
Corpus: 0 files (no synthesis yet)
Omitted: []
Incomplete:
  - All sources — `cortex refresh-map` is deferred from v1.0 to v1.x. Until shipped, treat the root-level docs (README.md, SPEC.md, plans/cortex-v1.md, the .cortex/ layout itself) as the authoritative map. Revisit map regeneration when a contributor lands on the dogfood target's repo and the stub becomes a real onboarding gap.
Conflicts-preserved: []
Spec: 0.3.1
---

# Project Map

> **Stub — `cortex refresh-map` deferred from v1.0 to v1.x.** This repo is spec-stage: the directory structure is self-evident from the root `README.md`, `SPEC.md`, the single plan at `.cortex/plans/cortex-v1.md`, and the `.cortex/` layout itself. The 2026-04-24 production-release rerank (see `journal/2026-04-24-production-release-rerank`) parked LLM-driven map synthesis off the v1.0 path because solo-author value is near-zero — the author already knows the map. Revisit when a contributor lands on the dogfood target's repo or a fresh agent on a clone gets visibly confused. Until then, treat the root-level docs as the authoritative map.

## Pending structural narrative (when refresh-map ships in v1.x)

When `cortex refresh-map` ships, it will produce sections covering:

- **Top-level artifacts.** `SPEC.md`, `README.md`, `docs/PRIOR_ART.md`, `.cortex/` (incl. `.cortex/plans/cortex-v1.md` as the single active plan) — each with a one-sentence purpose.
- **`.cortex/` layout.** Doctrine (scope and why), Plans (active work), Journal (append-only decision trail), Templates (write scaffolds), Protocol (agent contract), State (priorities).
- **Touchstone integration.** `principles/`, `scripts/`, `.pre-commit-config.yaml`, `.touchstone-config` — all project-owned but synced from the upstream Touchstone package.
- **Future CLI source layout.** `src/cortex/` (when Phase B scaffolds it), `tests/`, `pyproject.toml`.

No structural narrative is synthesized at this stage; consumers should read the root-level documents directly.
