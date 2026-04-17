# 0001 — Why Cortex exists

> Projects accumulate reasoning that lives nowhere durable: *why we chose this*, *what we already tried*, *what would falsify the thesis*. Code answers *what*, git answers *when*, neither answers *why*. Cortex is the file-format protocol that makes this reasoning a first-class, durable layer.

**Status:** Accepted
**Date:** 2026-04-17
**Load-priority:** always

## Context

The author maintains `NEXT_PHASE.md`, `INVESTMENT_THESIS.md`, `*_PLAN.md`, migration postmortems, and policy decision docs by hand on sigint. This manual practice produces real value — it is the layer above code that an agent (AI or human) needs to make good decisions. But manual upkeep breaks in predictable ways: premature completion declarations, silent staleness, scattered deferrals, lessons buried far from the code they'd protect.

Two sibling tools already exist: Touchstone (the engineering standards layer — principles, hooks, scripts) and Sentinel (the autonomous loop — ASSESS → PLAN → DELEGATE per project). Neither owns this reasoning layer. Touchstone is static; Sentinel is intentionally ephemeral ("derive, don't persist"). There is no place for *persistent project memory* in the composition.

## Decision

Cortex is a file-format protocol — a `.cortex/` directory with six layers (Doctrine, Map, State, Plans, Journal, Procedures), each with a mechanical contract (immutable vs. derived), an authoring contract (one mode per layer), and a retrieval contract (which agent-memory type it serves). A reference CLI implements the protocol but the **protocol is the primary artifact**.

Cortex composes with Touchstone and Sentinel by file contract only — never by code import. All three install independently; each is useful alone; together they compound.

## Consequences

- The reasoning layer has a canonical location and versioned spec; the same `.cortex/` works across any project and is readable by humans and AI agents alike.
- Writers are multiple (Cortex CLI, Sentinel cycles, Touchstone hooks, Claude Code sessions, the human) — no tool owns the state exclusively. This requires strict contracts (append-only Journal, immutable Doctrine) to prevent corruption.
- Staleness becomes visible: derived layers carry `Generated:` headers; stale-beyond-threshold is a warning, not a silent condition.
- The cost is upfront spec design. If the spec is wrong, all three tools carry the mistake. Hence spec-first, versioned, with a deliberate v0.1.0 "draft" period before v1.0.0 freeze.
