# PE-0 walkthrough — the first product experience, as actually run

**Date:** 2026-06-10 (the night of 2026-06-09)
**Status:** every step below was executed for real against the cortex repo
and the live Railway (`compass`) Postgres; the three ad-hoc glue steps are
being replaced by `cortex push` (#513) — until it lands, this document
records both the product path and the glue.

## Prerequisites

- `cortex` CLI (this repo checkout via `uv run cortex …`, or brew once
  released past this milestone)
- For the hosted half: `DATABASE_URL` to a Postgres with `pgcrypto`,
  `pg_trgm`, `vector` available, schema applied via
  `cortex.hosted.migrations.apply_schema` (the compass database is live
  with schema v6)
- `uv run --extra hosted …` for any live-DB step (psycopg ships in the
  `hosted` extra; the core install stays dependency-light)

## The loop, as run

### 1. Derive — repo decisions become candidates

```
$ cortex derive --commits 30
derive: 2 source file(s), 30 gathered document(s), 83 candidate event(s)
dropped: 1088 chatter record(s) (… x49, … x920, …)   # every drop reason-coded
store: .cortex/.index/derive-events.sqlite
```

Multi-repo results from the same morning (`--commits 20`):

| repo | authors | candidates |
|---|---|---|
| cortex | 3 | 83 |
| vesper | 3 | 84 |
| vanguard | 5 | 84 |
| outrider | 5 | 81 |

### 2. Inspect — provenance on everything

```
$ cortex candidates list
candidates: 83 proposed
ref: 20dc65ffa235  [proposed]
  lane: structured (agent_instructions)
  provenance: CLAUDE.md@2026-06-04T…#beb5d6c2… (1 span(s))
  text: **Dogfood as the readiness bar.** …
```

### 3. Push to the hosted ledger (glue → becomes `cortex push`, #513)

What the glue did, in order — all through shipped contracts:
events reconstructed from the store's `export_events()` rows → idempotent
ledger appends → source documents/spans rebuilt content-keyed from the
working tree (0 hash drift on 71 spans) → `plan_candidate_proposed` per
event → snapshot computed over the projected rows →
`graph_snapshots` + `projection.rebuilt` registered.

Result: 71 structured candidates live; 71 nodes/versions, 37 scopes.

### 4. The two refusals you SHOULD hit (they are the product working)

- Ask before a snapshot exists →
  `error: no graph snapshot registered for tenant …` — answers must name
  their replay boundary.
- Ask before confirming anything → `No cited decision found` — candidates
  never answer questions; **humans confirm decisions into existence**.
  (`cortex candidates triage`, #514, makes this ritual first-class.)

### 5. Confirm — the human moment

Two real decisions confirmed (verbatim CLAUDE.md rules) via
`DECISION_CONFIRMED` events through `plan_status_transition`; snapshot
refreshed. The envelope *requires* span citations on every confirm.

### 6. Ask — the payoff

```
$ DATABASE_URL=… cortex ask "compose sentinel touchstone"
1 cited decision:
1. **Compose by file contract, not code.** Cortex does not import Sentinel, Touchstone, …
   decision 9d10e912-… (version 4044520f-…)
   - https://github.com/autumngarage/cortex/blob/main/CLAUDE.md (span 4803ac61171d)
omitted: missing_citations=0, over_limit=0
```

A real decision, verbatim, permalink-cited, from the live database.

## What the run filed (the dogfood ledger)

- cortex#512 — the marquee phrase ("what did we *decide* about…") poisons
  its own FTS retrieval; layered fix in flight
- cortex#511 — commit-body extractor emits wrapped-line fragments
- cortex#513 / #514 / #515 / #516 — `cortex push`, `candidates triage`,
  `cortex review`, remediation-bearing refusals: the product surface this
  walkthrough's glue and dead-ends specced
- touchstone#454 — branch-guard blocks its own recommended remedy

## 7. Review — the thesis, demonstrated (added 2026-06-10, same day)

A diff adding `import touchstone` (forbidden by the confirmed
compose-by-file-contract decision):

```
$ DATABASE_URL=… cortex review --diff contradiction.diff
cortex review: 1 advisory finding(s) (state: findings_emitted; Stage 0 — blocking unrepresentable)

finding 1/1: contradicts-prior-decision [■ confirmed_cited]
  The diff adds src/cortex/hosted/siblings_bridge.py which does `import touchstone` …
  directly reversing the confirmed compose-by-file-contract decision …
  decision: 9d10e912-… (version 4044520f-…)
  citation: https://github.com/autumngarage/cortex/blob/main/CLAUDE.md
  suggested repair: Remove the `import touchstone` dependency. Replace the bridge with
  file-contract integration … matching the pattern Sentinel uses for Touchstone detection.

degraded reasons: Decision 71842fd2-… also conflicts, but its status is 'candidate',
  so no contradicts-prior-decision finding was emitted for it.
replay-key: model=anthropic/claude-cli prompt=review-evaluate/v1+… snapshot=8fca5eed…
```

Three invariants visible in one transcript: advisory-only (exit 0), the
confirmed-status evidence gate (the candidate twin was found, declined,
and disclosed), and the full replay key. Getting here surfaced one fix:
the evaluate prompt starved the model of vocabulary (finding classes,
confidence labels, the confirmed-only rule) — fixed in this commit; the
boundary's refusal of the invented class is what caught it.
