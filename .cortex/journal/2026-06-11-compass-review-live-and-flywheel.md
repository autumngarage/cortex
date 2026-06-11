# Compass Review is LIVE — hosted reviewer posting + the feedback flywheel turning

**Date:** 2026-06-11
**Type:** decision
**Trigger:** T2.1 (the Stage 2 product going live — the milestone the whole hosted plan builds toward)
**Cites:** plans/hosted-decision-reviewer.md, docs/walkthrough-pe0.md, docs/HOSTED-PRICING.md

> The hosted decision reviewer is deployed on Railway and POSTING. Compass
> Review (GitHub App id 4023580, installed on 3 orgs) caught a real decision
> contradiction on PR #561 — 3 cited `contradicts-prior-decision` findings
> posted as `compass-review[bot]` — and the human feedback (a 👍 and a reply)
> was captured into the append-only ground-truth corpus, keyed to the exact
> (model, prompt, snapshot) that produced the finding. The improve-with-data
> flywheel has turned once, live.

## What is live (operating, not just built)

- **Worker** (`cortex-worker` Railway service, schema v9 on compass): polls the
  job queue, reviews `github.pull_request` events stateless (fetch `.cortex/` +
  diff → derive in memory → evaluate via the Anthropic API → render → post as
  the App). `CORTEX_REVIEW_DRY_RUN=false` (posting), `token_budget=32000`,
  dogfood tenant mapping set.
- **Model transport**: the api-http route (#517) calls api.anthropic.com with
  `ANTHROPIC_API_KEY` (the `ANTHROPIC_API` shared var); model
  `anthropic/claude-sonnet-4-6` (override `CORTEX_REVIEW_MODEL`). Locally the
  claude CLI is used; the headless server needs the API key + API credits.
- **Feedback capture** (#394, schema v9): replies (issue_comment webhook) and
  reactions (poll, no webhook for them) on Compass Review comments → append-only
  `review_feedback_events`, replay-keyed. Absence is never approval. Sentiment
  stored `unclassified` pending the #549 converse pass.
- **Telemetry**: `cortex cost-report` (real $/review: ~0.3¢ no-findings → ~2.7¢
  3-findings) and `cortex ops-report` (throughput/success/latency/errors) —
  both operator-internal (never customer-facing; customers see credits).

## Learnings the live system generated about itself (all fixed)

- #556 — structural-only scoping missed repo-wide rules (the touchstone-import
  catch was empty until the content + repo-global recall lanes landed).
- routing fence-strip — the Messages API fences its JSON; the claude CLI does
  not. `_json_object` now unwraps one fence.
- #563 — the hosted reviewer used the 8k *session* budget as its judge budget,
  checking only 3 of 22 decisions; raised to 32k, configurable.

## Pickup pointer (what's next)

The flywheel is instrumented; build the actuators that read the corpus:
1. **`cortex precision-report`** (#395) — per-decision / per-class precision from
   `review_feedback_events` (the success-rate metric).
2. **Scheduled reaction polling** — the worker should sweep recent comments
   (the 👍 poll was run by hand once).
3. **Sentiment classification** (#549) — cheap converse pass fills `unclassified`.
4. **#386** — real installation→tenant resolution (the static env mapping is
   dogfood-only; blocks any non-our tenant).
5. **touchstone#455** — the babysit loop (open-pr.sh awaits + surfaces the
   review) — now unblocked since the reviewer posts.

Then: promote-to-blocking / auto-demote (#413/#415) by measured precision; rot
alarms (#423); the security tiers before any external tenant (#530-#544).

## Open operator notes

- Worker is live-posting on real PRs across 6 active repos (sentinel, alchemist,
  touchstone, cortex, vesper, conductor). Decide: keep live (real feedback) or
  scope to a couple repos while precision is tuned.
- Dev secrets in `/tmp` (cortex-dbsetup.env, cortex-webhook-secret) and the App
  `.pem` in 1Password — rotate per #535 before external tenants.
