---
Status: active
Written: 2026-06-09
Author: human + codex
Goal-hash: ec1bc286
Updated-by:
  - 2026-06-09T00:00 codex (created from the Obsidian Cortex master plan and GitHub roadmap issues #444-#475)
Cites: journal/2026-06-09-hosted-decision-reviewer-plan-adopted, state.md § Current work, docs/HOSTED-PRICING.md
---

# Build hosted decision reviewer

> **Cortex now builds toward a hosted decision ledger and reviewer: local proof, Railway-hosted core, GitHub advisory review, then Slack ask/remember/confirm.**

## Why (grounding)

Grounded in
[`journal/2026-06-09-hosted-decision-reviewer-plan-adopted`](../journal/2026-06-09-hosted-decision-reviewer-plan-adopted.md),
which records the decision to let the external Obsidian plan guide the internal
repo plan and GitHub issues.

This plan internalizes the external Obsidian planning source:

`~/Documents/Vaults/Personal/Hobby/Projects/Cortex/cortex_master_plan.md`

That file owns the product strategy and links to the detailed companion notes:
product/technical vision, roadmap, database/search plan, system diagram, and
business plan. The GitHub task breakdown is tracked in
[autumngarage/cortex#444](https://github.com/autumngarage/cortex/issues/444)
and follow-up issues #445-#475.

The prior active `.cortex` plans focused on the shipped file-format CLI and
context-integrity release track. That history remains useful, but it is no
longer the active product sequence. The new product spine is:

`local proof -> hosted core -> GitHub reviewer -> Slack ledger console`

## Approach

Keep `.cortex/` as the operational memory for the repo while letting the
Obsidian plan guide product direction. Do not duplicate the full Obsidian
strategy here; this plan should point agents to the right source, name the
current build order, and map the work to GitHub issues.

Build in four evidence-gated stages:

1. **Local proof:** ledger, Postgres-shaped schema, hybrid search,
   `ask_ledger`, `propose_decision`, `decisions_for_diff`, and historical PR
   replay with cited findings.
2. **Hosted core:** Railway API service, worker service, Postgres, migrations,
   secrets, logs, healthchecks, backups, restore drill, and environment
   separation.
3. **GitHub reviewer:** PR webhook, diff-scoped retrieval, advisory comments,
   feedback capture, and Cortex-on-Cortex dogfood.
4. **Slack ledger console:** `@cortex what did we decide about X?`,
   `@cortex here is what we decided...`, and explicit confirm/reject/stale
   flows through the same ledger API.

The non-negotiables from Obsidian carry here: cited answers only, advisory by
default, one ledger API for all surfaces, Postgres as canonical store,
append-only events, human-confirmed writes, no passive Slack ingestion early,
and no outsourcing Cortex memory/search/evaluator ownership to Hermes.

## Success Criteria

- The active session-start state points to this plan as the master current work
  and no older `.cortex/plans/*.md` file remains `Status: active` for the
  superseded CLI/context-integrity launch track.
- GitHub issue #444 links back to the Obsidian master plan and the staged issue
  breakdown is aligned to: local proof, hosted core, GitHub reviewer, Slack
  ledger console.
- Stage 0 local proof can answer ledger questions and replay historical PRs
  with citations, retrieval traces, and separate retrieval-vs-evaluator grading.
- Hosted Railway core has API, worker, and Postgres services plus backups,
  restore drill, healthchecks, logs, and environment separation.
- GitHub advisory reviewer dogfoods on Cortex PRs without spam and stores
  feedback/overrides in the ledger.
- Slack ledger console supports ask, remember, confirm, reject, merge,
  supersede, and mark-stale without passive workspace ingestion.

## Work items

- [ ] Align GitHub roadmap issues #444-#475 to the Obsidian master plan stages.
- [ ] Stage 0 local proof: database/search/ledger substrate and evaluator
  issues #445, #450, #451, and #460-#468.
- [ ] Stage 1 hosted core: Railway project, API, worker, Postgres, deploy,
  backup, restore, and observability issues.
- [ ] Stage 2 GitHub reviewer: advisory PR review and dogfood issues #452 and
  #453.
- [ ] Stage 3 Slack ledger console: ask/remember/confirm issue #455 and
  Hermes-boundary issues #456-#459 only as gateway-risk research.
- [ ] Keep `README.md` and `SPEC.md` focused on the shipped CLI/protocol until
  hosted behavior exists.

## Follow-ups (deferred)

- journal/2026-06-09-hosted-decision-reviewer-plan-adopted resolves blocking
  checks, passive Slack ingestion, Linear/Granola connectors, MCP supply loop,
  enterprise/on-prem packaging, and marketplace billing as deferred until the
  local proof, hosted core, GitHub reviewer, and Slack ledger console are
  useful; GitHub issue #444 keeps the broader backlog visible.

## Known limitations at exit

- This plan does not replace the detailed Obsidian notes; it routes repo agents
  to them.
- This plan does not change `SPEC.md` or the current `.cortex/` file-format
  protocol.
- This plan does not claim the hosted product exists yet.
