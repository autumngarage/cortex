# Vision-sharpening session lost to a Claude Code crash

**Date:** 2026-04-17
**Type:** incident
**Cites:** doctrine/0001-why-cortex-exists

> Multi-hour vision-sharpening session — deep research plus a working draft being critiqued by `codex` and `gemini` in parallel — disappeared when Claude Code crashed. Nothing had been committed, nothing written to `.cortex/`, nothing saved to memory. The next session opened to `git status` clean and state.md unchanged. The thread was irrecoverable except via the user's memory of what we had been doing.

## Context

The work in flight was *sharpening Cortex's vision* — positioning against adjacent tools (MemGPT/Letta, Cursor, Aider, PKM systems) and clarifying the composition with Touchstone (foundation) and Sentinel (loop) so Cortex (memory) has no overlap. Deep research, a working draft, and parallel critique from two other CLI agents.

The crash wiped all of it. On restart:

- `git status`: clean (no uncommitted work).
- `git log`: unchanged from before the session.
- `.cortex/journal/`: no new entry.
- `.cortex/plans/`: no draft plan.
- Memory system: empty for this project.
- No trace of what Codex said, what Gemini said, where we had landed, or what the next step was.

The user's first message on restart was *"sorry we crashed where did we leave off"* — and the honest answer was *I don't know*.

## Why this matters for Cortex

This is the *exact* pain Cortex exists to prevent. The spec's core claim is that durable project memory — decisions, active work, lessons — should not live only in chat context or in a tool's process memory. It should live in files, in a known layout, written continuously as work happens.

The session that was lost would have produced Journal and Plan writes *naturally* if Cortex existed and was being used. The loss is evidence, not just frustration:

- **Write-ahead discipline isn't optional.** The Journal-as-WAL framing (`PRIOR_ART.md` §4) is load-bearing — not a nice-to-have. An agent that does multi-hour synthesis work without committing intermediate state is one crash away from total loss. This is why the spec makes Journal append-only and writes explicit, not lazy.
- **Plans need to be checkpointed as they evolve, not only when finished.** A draft vision iterating with two critics is itself a Plan with status `in-progress`. If the Plan had been written to `.cortex/plans/vision.md` at each round, the crash would have cost the active conversation, not the work product.
- **Memory should capture cross-agent collaboration explicitly.** Pushback from Codex and Gemini was part of the thinking. The spec currently has no shape for "inputs from peer agents" — every round's critique would have been a Journal entry citing the Plan. This is worth testing the spec against directly as the vision work resumes.

## What we decided (going forward)

- Restart the vision work with a concrete commit discipline: at each research phase, each draft round, each critique, a Journal entry or Plan checkpoint lands before the next step starts. Dogfood the spec on the work that re-grounds the spec.
- This session's pain is the most honest "why Cortex" example the project has. The README's current *why* is abstract ("projects accumulate knowledge that lives nowhere durable"). This incident is concrete and should probably surface in the README or Doctrine.

## What we'd do differently

1. **Never let research or drafting run untouched for more than one phase without a file write.** The cost of a `git commit` or even a `.cortex/journal/` entry per round is negligible against the cost of a crash.
2. **Treat multi-agent critique loops as a specific artifact type.** Codex-said / Gemini-said / we-decided is structured enough to warrant its own Plan shape or Journal subtype. Spec gap worth noting.
3. **The CLI, once it exists, should make "checkpoint what we have so far" a one-liner.** If `cortex journal draft` costs more than the perceived value of the state, we won't use it — and we'll be back here. This is a usability constraint on Phase D, not just a feature.
