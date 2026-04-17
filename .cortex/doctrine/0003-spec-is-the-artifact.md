# 0003 — The spec is the primary artifact; the CLI implements it

> Cortex is a protocol first and a CLI second. Changes to `.cortex/` layer contracts happen in `SPEC.md` (with a version bump) before they happen in code. This discipline keeps the protocol coherent when multiple writers (Cortex CLI, Sentinel, Touchstone hooks, Claude Code, humans) participate.

**Status:** Accepted
**Date:** 2026-04-17

## Context

Normal tool development writes code first, documents afterward. That pattern fails for Cortex because multiple writers participate in the `.cortex/` filesystem. If the CLI's behavior drifts from the spec, every non-CLI writer (Sentinel hook, human author, another language's future reader) silently miscomposes.

We also want the option to eventually extract the spec to its own repo (`autumngarage/cortex-spec`) if a second implementation appears (e.g., a JavaScript reader). A spec-first posture keeps that door open; a code-first posture closes it.

## Decision

- `SPEC.md` is the primary artifact. It carries its own semantic version (`Spec version: X.Y.Z`).
- Any change to `.cortex/` layer contracts — directory layout, required fields, semantics of existing tags, new layer types — happens in SPEC.md first, with a version bump per SPEC.md §6.
- The Cortex CLI declares which spec major versions it supports. Readers encountering an unknown major version warn on read and refuse to write.
- PR review (see AGENTS.md) explicitly flags drift between spec and implementation: if the CLI's behavior changes, either the spec must update or the commit must explain why no spec change applies.
- The spec stays in this repo (not a separate one) until a second independent implementation appears. At that point extraction is mechanical.

## Consequences

- Upfront discipline cost: the author must update SPEC.md before code, which feels slower when implementing. But it prevents silent drift that would corrupt `.cortex/` contents over time.
- Spec versioning becomes visible in releases. A CLI v0.2.0 that bumps to spec v0.2.0 is a coordinated release; spec-only changes can ship separately.
- Third-party or agent-driven writers to `.cortex/` have a stable reference. This is what makes Cortex a protocol and not just a tool.
- Cost of getting the v0.1.0 spec wrong is bounded by the draft period — we expect to iterate SPEC.md before freezing to v1.0.0.
