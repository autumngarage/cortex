# Hosted pricing model

This document owns the pricing and cost model for a future hosted Cortex product.
It does not change the Cortex file format, SPEC, or local CLI contract.

## Product split

Cortex has two product surfaces:

- **Local Cortex** is the open file protocol and reference CLI. It validates,
  compiles, retrieves, and budgets project context from `.cortex/` files. It can
  run without an LLM.
- **Hosted Cortex** is the GitHub and Slack workflow surface. It receives
  webhooks, answers conversations, reviews pull requests, posts comments, stores
  audit logs, and coordinates model-backed analysis.

The hosted product is not a proprietary memory store. Git and `.cortex/` remain
the source of truth; the hosted service is a workflow, inference, and audit
layer around those files.

## Pricing shape

Hosted Cortex should use credits for AI work by default:

- plans include a platform entitlement plus a monthly credit grant;
- deterministic Cortex checks are included in the platform entitlement;
- each successful LLM-backed invocation consumes credits;
- credits are consumed from actual provider usage, not approximate Cortex
  manifest estimates;
- work pauses, asks for confirmation, or uses auto-top-up when a cap is reached;
- BYOK customers pay a platform/orchestration fee, but provider token costs flow
  through their own model account.

A pure flat-rate plan is risky because normal high-value workflows have variable
model cost. A pure per-token plan is also weak because the hosted app has fixed
integration costs even when model usage is low. The durable model is a base
platform plan plus metered credits for semantic AI work.

## Cost sources

The main variable cost is model inference:

- input tokens for PR diffs, Cortex evidence, GitHub issues, Slack threads, and
  retrieved context;
- output tokens for answers, findings, summaries, and comments;
- reasoning, cached, embedding, reranker, or tool-planning tokens when a provider
  exposes them;
- multi-pass workflows, such as judge plus verifier plus comment synthesis;
- successful retry or fallback attempts that produce a usable model result.

The hosted platform also has non-token costs:

- GitHub and Slack webhook receivers;
- installation auth, token refresh, permission mapping, and rate-limit handling;
- job queues, workers, retries, and idempotency records;
- database rows for installs, jobs, usage ledgers, audit trails, and spend caps;
- object storage for run traces, retrieved evidence, and redacted artifacts;
- search indexes, embedding indexes, and cache invalidation;
- dashboard, billing, invoices, support, abuse controls, and operational alerts.

GitHub and Slack API calls are usually not directly billed per request, but they
consume rate-limit budget and hosted compute. They belong in the platform fee,
not in the AI credit meter, unless a customer asks for a large paid backfill or
indexing job.

## The three LLM roles (what the model is used for)

Hosted Cortex uses an LLM for exactly three jobs. Two are real code
protocols (`DeriveModel`, `EvaluateModel` in `model_interfaces.py`); the
third is the conversational role (cortex#549). Each is a separately
configurable, separately metered route in the cascade (cortex#547), so the
cheapest model that holds quality serves each role.

1. **Derive — "is this a binding decision, or just chatter?"** Turns noisy
   human sources (Slack threads, commit bodies, PR discussion, meeting
   notes) into candidate decisions. Clean structured sources
   (CLAUDE.md/AGENTS.md, ADRs, CODEOWNERS) are extracted **deterministically
   with no LLM**; the model is only the firehose classifier that separates a
   decision from conversation. High volume, cheapest model tier.
2. **Evaluate — "does this PR contradict a decision?"** The review itself:
   judges whether a diff reverses or violates a relevant decision and emits
   the structured, cited verdict (decision, span, summary, repair). The
   high-stakes, precision-critical role; the frontier judge, gated by the
   cascade so it fires only on plausible conflicts.
3. **Converse — natural language in, natural language out.** The
   human-facing dialogue: parsing `@cortex what did we decide about X?`,
   rendering a natural cited answer, handling `@cortex here's what we
   decided`, and PR-thread replies. Grounded-generation only — it phrases
   over already-cited decisions and never introduces an uncited one.

The structure and citations of every output are deterministic; the LLM does
these three judgments/generations. Derive **builds** the memory, Evaluate
**enforces** it, Converse **talks about** it.

## Usage-based pricing is the core model (decision 2026-06-10)

Cortex charges **per usage** (metered credits over a platform base), not
flat-rate, because model cost is variable: the cascade keeps it low, but a
heavy repo or a chatty Slack team spikes it. Usage-based pricing makes
revenue scale **with** cost — a heavy user can never cost more than they
pay. Three consequences this decision locks in:

- **The platform holds the model key by default.** To meter-and-markup
  model usage, Cortex must pay the provider and bill per usage. So the
  default is: Cortex holds the Anthropic/OpenRouter key, pays the provider,
  charges credits with margin.
- **BYOK is the enterprise *exception*, not the default.** A customer who
  brings their own key pays the provider directly, so Cortex cannot meter
  that model cost — BYOK trades the usage margin for closing a
  security-sensitive account, compensated by a higher platform fee.
- **The cascade turns cost reduction into margin expansion.** Tier-0 PRs
  cost ~$0 but still count as activity (near-100% margin on the bulk);
  cheaper models over time (the cortex#547 downward ratchet) drop
  cost-per-review while price holds — margin grows as the model curve
  falls. The same `CostRecord` metering (cortex#335) that controls cost is
  the billing substrate; it must be accurate, per-tenant, and versioned so
  a pricing/cascade change never retroactively re-bills.

Gateway note: charging per usage *and* routing through OpenRouter stacks
two markups (gateway + ours). OpenRouter is for bootstrap breadth; the
high-volume metered tier moves to a direct provider for margin — a config
change (cortex#345), not a rewrite.

## What consumes credits

Credits are consumed when hosted Cortex asks an LLM or hosted semantic provider
to make a judgment, synthesize prose, or build model-derived retrieval data —
i.e. any of the three roles above firing.

Examples:

- semantic PR disagreement review;
- verifier pass for a candidate PR finding;
- GitHub inline review comment drafting;
- Slack natural-language Q&A;
- Slack thread or incident synthesis;
- release summary generation;
- Doctrine or Journal candidate drafting;
- Plan satisfaction review;
- embedding or reranker backfill when hosted Cortex pays for the provider.

No AI credits should be consumed for:

- local CLI usage;
- deterministic `cortex doctor` checks;
- manifest building with local estimates;
- exact grep/BM25 lookup;
- webhook receipt, dedupe, and job scheduling;
- GitHub comment posting itself;
- provider attempts that fail before returning a usable result.

## Auto PR review cost path

An automatic PR review that looks for disagreements works as a metered hosted
job:

1. GitHub sends a pull request webhook.
2. Hosted Cortex verifies the webhook, dedupes by repo, PR, and head SHA, then
   enqueues a job.
3. Cortex fetches PR metadata, changed files, diff hunks, existing comments,
   linked issues, and relevant `.cortex/` files.
4. Deterministic Context CI checks run first: SPEC version bump, generated-layer
   freshness, append-only Journal, immutable Doctrine, Plan success criteria,
   and other structural rules.
5. Cortex retrieves the smallest useful evidence set from Doctrine, Plans,
   State, SPEC, and recent Journal entries.
6. An LLM judge pass compares the diff with the cited Cortex evidence and emits
   only concrete candidate disagreements.
7. A verifier pass rejects weak or uncited candidates and writes the final inline
   comment text.
8. Cortex posts the GitHub review comment or check annotation and stores a usage
   receipt.

Only steps that call an LLM or hosted semantic provider consume AI credits. A
review can consume credits even when it leaves no comment, because the useful
result may be "no grounded disagreement found."

The customer-facing receipt should make this explicit:

```text
Cortex PR review
Deterministic checks: included
Evidence retrieved: 9 Cortex chunks, 14 diff hunks
Judge pass: 38k input / 1.2k output
Verifier/comment pass: 7k input / 500 output
Result: 1 inline comment
Credits used: 47
```

## Slack cost path

Slack has two modes:

- deterministic commands such as `/cortex status`, `/cortex doctor`, exact grep,
  and simple manifest metadata are included in the platform entitlement;
- conversational requests consume credits when Cortex needs an LLM to interpret
  the question, select evidence, synthesize an answer, draft an action, or ask a
  clarifying question.

Slack can become the largest spend driver because follow-up questions are easy.
Hosted Cortex needs caps by workspace, channel, thread, repo, user, and single
action. Above a configured threshold, it should ask for confirmation before a
deep search or cross-repo synthesis.

## Billing controls

Credit billing must be precise enough for invoices. Cortex's local token
estimates are budget heuristics and are not billing records.

Hosted Cortex needs:

- provider-native usage accounting for each successful model call;
- model and pricing version captured in every usage ledger row;
- per-action preflight estimates for expensive runs;
- hard caps and optional auto-top-up at org, workspace, repo, user, and job
  scopes;
- idempotency keys so webhook redeliveries do not double-charge;
- clear receipts for every AI action;
- no hidden prompt or source retention beyond the configured audit policy;
- admin controls to disable Slack conversation billing, PR auto-review billing,
  or high-cost models independently.

The invariant: deterministic Context CI should feel included and predictable;
semantic AI work should feel deliberately metered, explainable, and capped.
