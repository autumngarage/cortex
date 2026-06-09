# Hosted decision reviewer plan adopted

**Date:** 2026-06-09
**Type:** decision
**Trigger:** T2.1
**Cites:** plans/hosted-decision-reviewer.md, plans/cortex-v1.md, plans/context-integrity-production.md, https://github.com/autumngarage/cortex/issues/444

> Cortex's internal memory now treats the Obsidian hosted decision-reviewer plan as the product direction and uses `.cortex/plans/hosted-decision-reviewer.md` as the repo-operational bridge.

## Context

The external Obsidian planning session reframed Cortex from a shipped
file-format CLI/protocol into a hosted product: a decision ledger and reviewer
that can answer questions, remember decisions, review GitHub PRs, and expose a
Slack ledger console. The active `.cortex` plans still pointed at the older
CLI/protocol and context-integrity release tracks, which made session startup
misleading for future agents.

The detailed task breakdown already lives in GitHub issues #444-#475. The
Obsidian files own product strategy and rationale; `.cortex` should not fork
that strategy, but it does need a tracked active plan that tells repo agents
which work is current.

## What we decided

We added `plans/hosted-decision-reviewer.md` as the active internal plan and
made it the bridge from repo memory to the Obsidian product plan and GitHub
issue list.

The older active plans, `plans/cortex-v1.md` and
`plans/context-integrity-production.md`, are retained as historical and
substrate context but superseded as active master plans.

## Consequences / action items

- [ ] Keep the Obsidian master plan as the product-strategy owner.
- [ ] Keep `.cortex/plans/hosted-decision-reviewer.md` as the repo-operational plan.
- [ ] Keep GitHub issues #444-#475 derived from the Obsidian plan and stage-gated against the internal plan.
