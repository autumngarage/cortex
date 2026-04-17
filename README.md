# Cortex

> **Project memory that derives and persists.** The reflective layer of the autumngarage composition — Touchstone is the foundation, Sentinel is the loop, Cortex is the memory.

**Status:** spec-stage. The [SPEC.md](./SPEC.md) defines the `.cortex/` file-format protocol at v0.1.0. A reference CLI is planned per [PLAN.md](./PLAN.md); no executable ships yet.

---

## What it is

Cortex defines a `.cortex/` directory per project holding six layers of documents:

| Layer | Question it answers | Mechanical contract |
|---|---|---|
| **Doctrine** | why does this project exist? | Immutable, ADR-style, numbered, never deleted |
| **Map** | what's here, structurally? | Derived; regenerated from code + git |
| **State** | where are we right now? | Derived; regenerated from metrics + journals |
| **Plans** | what are we doing about it? | Mutable for status; named trails through Doctrine/State |
| **Journal** | what happened, what did we learn? | Append-only; write-ahead log of decisions |
| **Procedures** | how do we do X safely? | Versioned how-tos and interface contracts |

Each layer has a single authoring mode (Diataxis discipline), a single trigger (what real event causes a write), and a single retrieval contract (what question an agent queries for). See [SPEC.md](./SPEC.md) for the full contract per layer.

---

## Why

Projects accumulate knowledge that lives nowhere durable. Code answers *what*; git history answers *when*; neither answers *why we chose this* or *what we've already tried*. In practice, engineers maintain this by hand — plan docs, thesis docs, migration postmortems, policy decisions — and that manual upkeep breaks in predictable ways: premature-completion declarations, silent staleness, scattered deferrals, buried lessons.

Cortex is the file-format protocol that turns this manual practice into a spec. One canonical location (`.cortex/`), one set of rules for who writes what when, and layer contracts that make staleness visible instead of tolerated.

The design is rooted in three traditions:
- **Write-ahead log + checkpoint** semantics from ext4 / PostgreSQL for Journal and Map
- **Architecture Decision Records** (Nygard) for Doctrine's immutable-with-supersede discipline
- **Diataxis** authoring-mode separation and **Memex** named trails for Plans

See [`docs/PRIOR_ART.md`](./docs/PRIOR_ART.md) for the full research synthesis backing the spec.

---

## Composition with Touchstone and Sentinel

Cortex stands alone and composes by file contract, not code dependency:

- **Without Sentinel or Touchstone.** Cortex still maintains the `.cortex/` protocol. Humans write Doctrine and Plans; humans run `cortex refresh-map` to regenerate when useful. Valid on any git-tracked project.
- **With Sentinel present.** Sentinel's scan reads Doctrine + State as additional context. Sentinel's end-of-cycle hook writes Journal entries for significant events. Map regeneration consumes `.sentinel/runs/*` for richer state.
- **With Touchstone present.** Touchstone's pre-merge hook can draft Journal entries for architecturally significant merges. `touchstone status` can include Cortex freshness.

The interface between tools is the filesystem layout in [SPEC.md](./SPEC.md). No shared libraries, no hard dependencies, graceful-degrade everywhere.

---

## Install

Not yet. The CLI ships in Phase B per [PLAN.md](./PLAN.md). When it does:

```bash
brew tap autumngarage/cortex
brew install cortex
cortex init     # in any project
```

In the meantime, the `.cortex/` protocol is hand-authorable by following [SPEC.md](./SPEC.md).

---

## Status and plan

See [PLAN.md](./PLAN.md). Phase A (foundation + spec) is in progress. Phase B is the walking-skeleton CLI. Phase C is the first synthesis command (`cortex refresh-map`). Phase D adds Plans/Journal authoring helpers. Phase E wires integration with Sentinel and Touchstone.

The spec at v0.1.0 is draft. Breaking changes to layer contracts will bump to v0.2.0 before v1.0.0 freezes the protocol.

---

## License

MIT.
