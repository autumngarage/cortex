# Security & data handling

> **Cortex does not host your team's memory.** Your decisions live in your
> own repository, in an open file format you own and can read without us.
> Cortex is the reviewer that reads them at the merge gate and comments —
> it does not become your system of record.

This page is the canonical, customer-facing statement of what Cortex
stores, where, and how it is protected. It is written to be the answer to
a security review.

## Target default posture: stateless review

For the GitHub reviewer, the default mode is **shared hosted**. The review
handler stores the repository's full decision graph, full pull request diff,
and fetched source excerpts in Cortex's shared database before it evaluates a
pull request. The path is:

1. A pull request opens.
2. Cortex fetches your `.cortex/` decision files at the relevant commit
   and the PR diff, using the GitHub App installation token.
3. It evaluates the diff against your decisions **in memory**.
4. It posts an advisory comment citing the exact decision.
5. It **forgets** — nothing about your decisions or your code is written
   to Cortex's database.

In stateless mode, the only durable data Cortex holds for you is
operational bookkeeping (which installation, which jobs ran) and
**content-free feedback labels** — a decision *hash* plus whether a
reviewer found a comment useful, never the decision text. Your decisions
and your code are never persisted on our infrastructure. The stateless
review path touches no database at all: a regression test booby-traps the
database connection and asserts a full review still produces its cited
comment.

The deployed worker does read one operator-internal rollout gate before
entering that stateless path: a content-free `owner/repo` enable/disable event
stream that controls which installed repositories may receive PR comments. A
repo with no rollout event is disabled, acknowledged, and skipped before any
GitHub fetch or model call.

This is possible because **your repository is the source of truth.** Git
and `.cortex/` already hold the canonical record; Cortex is a reader, not
a store.

## What Cortex stores, by tier

Cortex offers an isolation ladder. You choose the rung; higher rungs store
less of your data on our infrastructure.

| Tier | Where your decisions live | What Cortex's database holds |
|---|---|---|
| **Stateless** (planned default) | Your repo only | Operational bookkeeping + content-free feedback labels |
| **Dedicated schema** | Your repo + an isolated per-tenant schema | Your decision graph, in a schema only your installation can reach |
| **BYO-store** | Your repo + a database **you** own | Nothing but operational bookkeeping; decisions live in your database |
| **Shared hosted** (planned) | Your repo + our shared store | Your decision graph, with query scoping and planned database-layer tenant isolation |

The cross-source features (capturing decisions made in Slack or meetings)
and the feedback-learning loop require a stored graph; those are the
reason to opt up from stateless. Everything that reaches a model provider
is the same in every tier: **the diff under review plus the bounded slice
of relevant decisions** — never your whole codebase, never the whole
graph.

## What we never do

- **We never train on your data.** Feedback labels improve *your* tenant's
  precision; any cross-customer learning is limited to content-free
  structural priors — never your decisions, never your code.
- **We never become your system of record.** Cortex points back at the
  real one (your repo); it does not replace it.
- **We never store more than the tier you chose requires.** Source file
  contents are fetched on demand and not retained; webhook payloads are
  reduced to content-free skeletons after processing.

## Protections

- **Transport:** TLS in transit; `sslmode=require` honored on database
  connections.
- **Tenant isolation:** every data query is designed to be scoped by tenant
  and source visibility. Before Cortex offers shared-tier hosting to
  external tenants, the shared tier will add database-enforced row-level
  security and composite tenant foreign keys as tested backstops.
- **Least privilege:** the GitHub App requests only Contents (read),
  Pull requests (read/write), and Metadata (read). No access to Actions,
  secrets, administration, or other repositories.
- **Secrets:** credentials live in the deployment platform's secret store
  and a password manager — never in the repository, never in logs, never
  in a stored row. Logs are content-free by contract.
- **Portability & deletion:** your decision graph exports in full as open,
  replayable JSON at any time; on offboarding, your data is deleted with
  an audited procedure leaving only a tombstone recording that the
  deletion happened.

## Compliance posture

Cortex is pre-launch and engaging design partners. Our compliance approach
follows the data: because the planned default tier stores almost nothing
of yours, the surface a compliance program must cover is small by
construction.

- **Now:** this page, a subprocessor list, a data-handling description, and
  the data-minimization work above.
- **At first paying customer:** a Data Processing Agreement and SOC 2
  Type 1.
- **At scale:** SOC 2 Type 2, third-party penetration test.

The architecture is the compliance strategy: **store less, isolate by
tier, and the audit boundary shrinks with you.**

---

*This document is maintained as the canonical source for Cortex's security
posture. Engineering tracking for the controls described here lives in the
trust & security section of the active plan
([`.cortex/plans/hosted-decision-reviewer.md`](../.cortex/plans/hosted-decision-reviewer.md)).*
