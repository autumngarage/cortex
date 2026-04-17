# Engineering Principles (HARD REQUIREMENTS)

These are non-negotiable. Every code change must satisfy all of them.

## No band-aids
Fix root causes, not symptoms. If a fix patches a symptom, say so explicitly: *"This patches the symptom. The root cause is X and fixing it properly would require Y. Which do you want?"*

## Design for scale-up
Use percentages, ratios, and relative values — not magic numbers or hard-coded absolute values. Code should work correctly regardless of scale (10 users or 10 million, $100 or $100K).

## Derive, don't persist
Compute from the source of truth when possible. Persisted derived state goes stale silently. If you must persist, document the source of truth and the refresh mechanism.

## No silent failures
Every exception is either re-raised or logged with enough context to debug from production logs alone. No `except: pass`. No swallowed errors. No default values returned on failure without a log line. No fallback behavior that masks broken state.

**The rule:** if something fails, the failure must be visible to someone — an operator, a log aggregator, a monitoring dashboard. A failure that nobody can see is the most dangerous kind.

## Every fix gets a test
Bug fixes must include a test that reproduces the exact failure mode. A bug fix without a test means the bug can recur silently. The test should fail on the old code and pass on the new code — if it passes on both, it's not testing the right thing.

## Think in invariants
Assert correctness properties in tests, not just happy-path behavior. What must always be true? What relationship between values must hold? Test the invariants, not just the outputs.

## One code path
Avoid creating separate code paths for different modes (test/prod, paper/live, dev/staging). Divergent paths drift apart silently, and bugs in one path don't surface until it's too late. If modes must differ, the divergence should be as late and as narrow as possible (e.g., the final I/O call), not a fork at the top of the pipeline.

## Current decisions from current data
Never blend pre-change and post-change data in any read that drives a decision. When a model, algorithm, or data source is materially changed, establish a boundary (cohort, epoch, version) and ensure every downstream consumer honors it. Aggregating across the boundary dilutes signal with noise from the old regime.

## Audit one weak-point class at a time
When you find a structural bug pattern, don't just fix the one you noticed. Audit the whole class:
1. Search for all instances of the pattern across the codebase
2. Produce a ranked punch-list (production impact first)
3. Fix in tiers — highest impact first
4. Add a guardrail test (AST-based, lint rule, or integration test) that catches future regressions

This discipline prevents re-auditing the same code twice and catches bugs before they compound.
