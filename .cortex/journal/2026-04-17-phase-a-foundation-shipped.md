# Phase A (foundation) shipped

**Date:** 2026-04-17
**Type:** migration
**Cites:** [`../../PLAN.md`](../../PLAN.md), doctrine/[0003-spec-is-the-artifact](../doctrine/0003-spec-is-the-artifact.md), [`journal/2026-04-17-spec-v0.1.0-drafted.md`](2026-04-17-spec-v0.1.0-drafted.md)

> Phase A exit criteria met. Repo is bootstrapped, SPEC.md v0.1.0 is drafted, PLAN.md + README.md + docs/PRIOR_ART.md + CLAUDE.md + AGENTS.md are written, and this repo's own `.cortex/` is populated with three Doctrine entries and two Journal entries. Next work is Phase B (walking-skeleton CLI), tracked at `plans/phase-b-walking-skeleton.md`.

## Context

End of the session that began by renaming Touchstone (formerly toolkit) and transferring it plus Sentinel to the `autumngarage` org. The rename project ran phases 1–7; after it completed, the discussion turned to whether a third tool made sense. That discussion converged on Cortex (reflective / memory layer), settled the architectural tradeoffs (compose by file contract, no shared provider layer, protocol-before-implementation), and ended with a spec-first build instead of immediate code.

## What happened / what we decided

Shipped today as Phase A:

- **Repo and distribution path.** `autumngarage/cortex` created; `autumngarage/homebrew-cortex` tap not yet created (deferred to Phase B release wiring). Repo bootstrapped with `touchstone new --type python --reviewer codex`, dogfooding Touchstone.
- **SPEC.md v0.1.0 (draft).** Six-layer protocol with contracts per layer. Cross-layer invariants enforced: grounding citations, deferred-item tracking, measurable Success Criteria, visible staleness.
- **PLAN.md.** Phases A through E with exit criteria and measurable Success Criteria. Phase A marked complete with this commit.
- **docs/PRIOR_ART.md.** Traces every design rule in SPEC.md to a cited source — ADRs (Nygard), RFCs, SRE postmortems (Google), Diataxis, Zettelkasten, Memex, MemGPT, Voyager, WAL + checkpoint (ext4/Postgres), git object model, Hickey's value semantics.
- **README.md, CLAUDE.md, AGENTS.md.** Customized beyond Touchstone bootstrap placeholders with Cortex-specific posture: spec-first, compose-by-file-contract, regeneration-is-visible.
- **Dogfood `.cortex/`.** Three Doctrine entries (0001–0003 encoding the load-bearing architectural decisions), the prior "spec drafted" Journal entry, the Phase-B Plan doc, and now this journal entry + state.md.

## Consequences / action items

- [x] Phase A exit criteria met on every axis defined in PLAN.md
- [ ] **Phase B kickoff** — start with Python package scaffold (`pyproject.toml`, `src/cortex/__init__.py`, click entrypoint). See `plans/phase-b-walking-skeleton.md` Work Items for the full sequence.
- [ ] Create `autumngarage/homebrew-cortex` tap repo when ready to cut v0.1.0 release (end of Phase B).
- [ ] Revisit SPEC.md v0.1.0 after first dogfood on Sentinel's repo (Phase C); expect a minor version bump.

## What we'd do differently

Not applicable for a bootstrap. The sequence that worked well and is worth repeating on future projects of this shape:

1. Spend the first real work on prior art — both internal (sigint's existing manual practice) and external (ADR/Diataxis/MemGPT literature). The time spent is <10% of the session but materially shaped the spec's design rules.
2. Write the spec before any code. For a protocol-shaped tool, code-first would have produced a CLI whose behavior became the unspecifiable spec — precisely the drift Cortex is designed to prevent in its downstream users.
3. Dogfood from day 1 by populating this repo's own `.cortex/`. Forced the spec to survive at least one real authoring pass before being committed.
