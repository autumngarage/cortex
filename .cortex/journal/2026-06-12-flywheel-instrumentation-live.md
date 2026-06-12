# Flywheel instrumentation live — staged boundary, precision-report, scheduled reaction sweep

**Date:** 2026-06-12
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/hosted-decision-reviewer, journal/2026-06-11-staged-catches-reclassified-organic-gate, journal/2026-06-11-compass-review-live-and-flywheel

> The flywheel's instrumentation half shipped and is operating on compass: staged demo traffic is structurally excluded from ground truth (#575, schema v10), `cortex precision-report` reads the corpus honestly (#395), and reactions sweep automatically every 15 minutes (#393, closed). The organic-catch falsification window now runs with metrics that cannot lie.

## What shipped (PRs #579–#582)

- **#575 / PR #579 — staged-traffic registry (schema v10).** Append-only
  `review_staged_prs` keyed by (tenant, repo, pr); the worker registers PRs
  matching the demo convention (`[cortex-demo]` title token or
  `cortex-demo-fixture` label) in the same transaction as job completion;
  PR #561 backfilled by one operator INSERT, never an UPDATE.
- **#395 / PRs #580+#581 — `cortex precision-report`.** Sentiment-authoritative
  aggregation, staged exclusion by default with visible counts, precision
  `None`-never-`0.0`, declared findings-emitted gap. #581 fixed a live catch:
  the staged JOIN conditioned on `tenant_id`, but feedback rows carry the
  static env tenant while worker writes carry per-repo uuid5 tenants (the
  #572 identity split) — the join silently never matched. Now keyed on
  (repo, pr) with a regression test reproducing the production shape;
  tenant unification added to #572's scope.
- **#393 / PR #582 — scheduled reaction sweep (issue closed).** Discovery
  from recently-succeeded review jobs (dedupe to newest per PR, capped
  visibly), capture via the existing idempotent poll, every
  `CORTEX_REACTION_POLL_SECONDS` (900) between queue drains; a crashing
  sweep is one log line, the queue outlives it; DB persistence failures
  propagate (review-hardening fixes adopted from the merge gate).

## Live evidence (compass, 2026-06-12)

- First honest precision report: **15 unscored organic replies** (cortex 12,
  touchstone 1, vesper 2), **2 staged events excluded** (#561), precision
  `n/a (no scored feedback)` — the baseline the validation window starts from.
- First scheduled sweep: `targets=26, polled=10, no_comment=16, recorded=0,
  duplicates=1, errors=0` — the duplicate is the hand-captured #561 👍
  collapsing on its idempotency key; end-to-end idempotency proven live.

## Operational findings (same session)

- **GitHub auto-deploys had been silently failing for days** (every build:
  `pip install uv==` — Railway nixpacks leaves `NIXPACKS_UV_VERSION` empty
  on the GitHub path). Fixed by pinning the variable on both services;
  proven by PR #582's auto-build succeeding. The worker service is
  CLI-deploy only (`railway up --service cortex-worker`).
- **The outrider credit burn** (~$100/2.5 days, ~38k tiny calls on a
  year-old model) was attributed to outrider's key-presence provider
  inference picking up the shared `ANTHROPIC_API` var; key removed by the
  operator; root causes + guardrails filed as outriderintel/outrider#555.
  Cortex's own spend stayed attributable to the cent via the #559 ledger.
- **Merge-gate fix-mode failure class** (4 recovery cycles in 24h: ambiguous
  fixed-no-changes, hook-blocked reviewer commits, stale-checkpoint
  conflicts) filed upstream as touchstone#463 with suggested guardrails.

## Consequences / action items

- [ ] #380 feedback classification — the 15 unscored replies are its
      complete input; first real precision number follows
- [ ] #572 now also owns tenant-identity unification across
      cost/feedback/staged tables (then re-tighten the staged join)
- [ ] touchstone#463 guardrails for the merge-gate fix-mode class
- [x] #575, #395 closed by merge; #393 closed with live evidence;
      trackers updated

**Lesson:** instrument before you measure, and make the instrument check
itself — the staged-join tenant bug was caught only because the fix ran its
gated round-trip against the live database instead of a same-tenant fixture.
A test that mirrors your assumptions passes; a test that mirrors production
catches.
