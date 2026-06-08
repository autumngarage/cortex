# Hosted Cortex credit pricing direction

**Date:** 2026-06-08
**Type:** decision
**Trigger:** user-directed product clarification
**Cites:** docs/HOSTED-PRICING.md, doctrine/0008-context-integrity-build-system

> Hosted Cortex should price LLM-backed GitHub and Slack work with credits by
> default, while keeping local Cortex and deterministic Context CI separate from
> the AI usage meter.

## Context

The business-case discussion clarified that the primary commercial surface is
likely not the local CLI alone. The valuable hosted workflows are GitHub PR
review and Slack conversation: Cortex receives an invocation, gathers relevant
project memory, asks an LLM to judge or synthesize, and posts a useful answer or
comment.

Those workflows make flat-rate pricing risky. Normal use cases such as PR
disagreement review, Slack Q&A, incident synthesis, release summaries, and
promotion backlog cleanup can all consume variable model tokens. Treating those
as unusual overages would make the pricing model misrepresent the product.

## Decision

Document a base-plus-credits hosted pricing model:

- Local Cortex remains the open protocol and reference CLI.
- Hosted Cortex covers GitHub App, Slack App, audit logs, queues, auth,
  dashboards, and deterministic Context CI through a platform entitlement.
- LLM-backed actions consume credits from actual provider usage.
- Deterministic checks, manifest building, exact grep/BM25 lookup, webhook
  receipt, and comment posting do not consume AI credits.
- Auto PR review consumes credits for the judge and verifier/comment passes even
  when no comment is posted, because the paid result is semantic judgment.
- Slack natural-language conversations consume credits; deterministic slash
  commands do not.
- BYOK should remain possible, with Cortex charging for orchestration/platform
  value while provider token cost flows through the customer's account.

## Consequences

- Product docs should say clearly that Cortex core does not need an LLM, but the
  smart hosted reviewer and Slack assistant do.
- Future implementation must use provider-native usage records for billing; the
  local `~4 chars/token` manifest estimator is not invoice-grade.
- Hosted usage requires preflight estimates, spend caps, idempotency keys, and
  receipts so credits are explainable and bounded.
- The hosted product must not become a hidden memory cloud. Git and `.cortex/`
  remain the source of truth.
