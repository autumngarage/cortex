---
Status: active
Written: 2026-06-09
Author: human + codex
Goal-hash: ec1bc286
Updated-by:
  - 2026-06-09T00:00 codex (created from the Obsidian Cortex master plan and GitHub roadmap issues #444-#475)
  - 2026-06-09T17:30 claude (recorded the #477-#483 substrate merge wave; full-backlog review: wave ordering, per-stage exit gates, issue-range fix #306-#475, milestone/label numbering resolution, hygiene pass across 164 issues)
  - 2026-06-10T10:30 claude (PE-0 complete: Stage 0 Waves 1-7 + most of 8 built and merged across bundles #505/#507/#509/#518; first live cited answer and first real contradiction catch on Railway compass; pickup pointer moved to the Wave 8/9 tail + Stage 1 frontier)
Cites: journal/2026-06-09-hosted-decision-reviewer-plan-adopted, journal/2026-06-09-roadmap-refinement-and-issue-hygiene, state.md § Current work, docs/HOSTED-PRICING.md
---

# Build hosted decision reviewer

> **Cortex now builds toward a hosted decision ledger and reviewer: local proof, Railway-hosted core, GitHub advisory review, then Slack ask/remember/confirm.**

## Pickup pointer

- **Current wave — Stage 0 Wave 8/9 tail + gate:** #322/#326/#338/#367/
  #368/#373/#374/#376/#339, then #450 batch replay -> #378 hand-grading ->
  #337 verdict.
- **Stage 1 frontier:** #470 API shell, #471 worker, #473/#474 ops, #469/#475
  env docs, #517 server transport.
- **Stage 0 tracker:** [cortex#445](https://github.com/autumngarage/cortex/issues/445).
  Master tracker: [cortex#444](https://github.com/autumngarage/cortex/issues/444).
- **Dispatch rule:** every open product issue carries `alchemist-skip` (applied
  2026-06-09). Remove it only when the issue's wave is current and its body is
  dispatch-ready (rescoped, dependencies named, ACs falsifiable).

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
and the detailed issues #306-#475 (plus post-plan additions #484+).

The prior active `.cortex` plans focused on the shipped file-format CLI and
context-integrity release track. That history remains useful, but it is no
longer the active product sequence. The new product spine is:

`local proof -> hosted core -> GitHub reviewer -> Slack ledger console`

## Stage authority and numbering (resolved 2026-06-09)

**GitHub milestones are the single stage authority**, following
`cortex_master_plan.md` (canonical 2026-06-09):

| Stage | Milestone | Tracker |
|---|---|---|
| Stage 0 — local proof | `Stage 0 - Local evaluator proof` (m1) | #445 |
| Stage 1 — hosted core | `Stage 1 - Hosted core on Railway` (m7) | #485 |
| Stage 2 — GitHub reviewer | `Stage 2 - GitHub reviewer` (m2) | #446 |
| Stage 3 — Slack ledger console | `Stage 3 - Slack ledger console` (m6) | #455 |
| Future — connected sources + blocking | m3 | #447 |
| Future — MCP supply loop + enterprise | m4 | #448 |
| GTM and fork decision (parallel track) | m5 | #449 |

The retired `cortex_roadmap.md` numbering (stage-1 = GitHub app, stage-2 =
connected sources, stage-3 = MCP/enterprise, "Stage 0.5" = Slack before
GitHub) is dead: its `stage-N` labels were deleted 2026-06-09 and stale
"Roadmap fit" body lines were rewritten. Where the two Obsidian docs conflict
on ordering (Slack console before vs after the GitHub App), the master plan's
build order wins.

## Approach

Keep `.cortex/` as the operational memory for the repo while letting the
Obsidian plan guide product direction. Do not duplicate the full Obsidian
strategy here; this plan points agents to the right source, names the current
build order, and maps the work to GitHub issues.

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

## Current state (2026-06-09)

The Postgres-shaped substrate shipped via PRs #477-#483 (closed #460-#466,
#468; ten more issues closed 2026-06-09 as shipped-by-that-wave: #306, #311,
#312, #317, #321, #324, #364, #365, #366, #383): append-only `ledger_events`
with DB-enforced immutability, decision graph tables (`HOSTED_SCHEMA_VERSION
= 6`), span-level provenance with fail-closed `FINDING_EMITTED`, the 9-type
scope index, hybrid RRF retrieval capped at 30 with omitted counts, fail-closed
cited answer packs, and tenant/source visibility boundaries.

**Caveat the roadmap starts from:** the substrate is non-executing — SQL
strings and dataclasses with string-assertion tests; no Postgres driver,
migration runner, or CLI wiring exists yet. The first executable SQL path
lands with #472 (Stage 1); #467 and #484 need it for full closure.

## Build sequence

Wave ordering is dependency-respecting; issues within a wave are parallel.
The per-stage trackers (#445, #485, #446, #455) carry the same ordering as
checklists — they are the canonical per-issue lists; this section owns only
the sequence and the gates.

### Stage 0 — local proof (milestone m1, tracker #445)

- **Wave 1 — contracts + the missing gating input:** #310 (as-built brief;
  fan-out gate, blocks #350), #332, #344, #327, #363.
- **Wave 1p — positioning/GTM groundwork (parallel, no code):** #307, #309,
  #308, #443, #437; file #384 publisher verification (wall-clock long pole).
- **Wave 2 — derive scaffold, labeling, routing, policy roots:** #350, #333,
  #343, #345, #335, #329.
- **Wave 3 — lane policy + confidence + high-precision extractors:** #315,
  #358 (carries #361's dropped-chatter logging), #362, #316, #352, #351, #353.
- **Wave 4 — text extractors + fixtures, corpus, guardrails:** #354, #355,
  #356, #347, #348, #339, #334.
- **Wave 5 — evaluator core (the load-bearing bet):** #330, #370, #375, #371,
  #372, #377. `contradicts-prior-decision` and `reverses-superseded-pattern`
  ship first per the master plan.
- **Wave 6 — replay machinery + write path + shadow finding types:** #336,
  #323, #314, #313, #376, #373, #374, #346, #487 (merge-event schema
  decision; blocks Stage 3 curation #491).
- **Wave 7 — read-value surfaces + metrics:** #381, #382, #359, #341, #342,
  #331, #369.
- **Wave 8 — hardening, rebuild, CI gates:** #320, #319, #318, #326, #322,
  #328, #338, #349, #360.
- **Wave 9 — dogfood run + the gate:** #450, #367, #368, #378, #325, #337,
  #451 (plus the external multi-author repo run).
- **Wave 10 — gate-dependent tail:** #357, #340; #436 only if the gate
  passes, #439 only if it fails.
- **Sequenced research (front-loaded gateway risk):** #456 -> #459 -> #457 ->
  #458, consumed at Stage 3 entry. Default answer is official Slack SDK/Bolt.
- **With #472:** #484 (glob scope matching), #467 full closure.

**Exit gate:** derive works on repo-native sources; `ask_ledger` answers with
citations or honestly says it does not know; `decisions_for_diff` returns
bounded cited candidates; historical replay separates retrieval failure from
evaluator failure; every finding carries a replay key; the loop works on
Cortex **and one external multi-author repo**. Quantitative bar: **>=70% of
emitted advisory comments correct and useful on a hand-graded sample**, with
citation/budget/ledger sub-bars. Gate artifact: the #337 report (template
#343, self-review section #451) deciding proceed / grind / narrow /
Contextlint fallback (#439). **Do not host or build webhooks before this
passes.**

### Stage 1 — hosted core on Railway (milestone m7, tracker #485)

- **Wave 1 — definitional docs (may draft in late Stage 0):** #469, #475.
- **Wave 2 — API shell + schema application (parallel, disjoint):** #470,
  #472 (applies the shipped `create_schema_sql()` DDL; psycopg driver +
  connection policy — the first executable SQL path).
- **Wave 3 — worker:** #471 (the canonical queue substrate for ALL hosted job
  types; Stage 2's #388 layers on it).
- **Wave 4 — recovery + observability close-out:** #473, #474.

**Exit gate:** Railway has API, worker, and Postgres services; backups with a
TESTED restore path; deploys, logs, healthchecks, secrets, and environment
separation work; the hosted API runs the same ledger/search/evaluator path as
local — one code path, no hosted fork.

### Stage 2 — GitHub reviewer (milestone m2, tracker #446)

- **Wave 1 — bars, permissions, partner-facing prerequisites:** #453, #385,
  #402, #442 (#384 paperwork already filed in Stage 0).
- **Wave 2 — auth + ingestion:** #386, #387.
- **Wave 3 — evaluation pipeline wiring:** #388 (PR-evaluation job type on
  #471's queue), #389 (diff fetch feeding #363's extractor — one path).
- **Wave 4 — IDs before comments:** #391, then #390.
- **Wave 5 — dedup, feedback capture, rollout config:** #392, #393, #397.
- **Wave 6 — ground truth + classification + reporting:** #394, #380
  (precision-wrong vs tone classification before labels move gates), #395.
- **Wave 7 — dogfood gate:** #452 graded against #453's bars, before any
  external install.
- **Wave 8 — external rollout + money-down gate:** #396, #401, #434, then
  #398.

**Exit gate:** webhook receives PR events; the diff path uses
`decisions_for_diff`; advisory comments carry citations; feedback/overrides
append ledger events; Cortex dogfoods on its own PRs without spam (stable
finding IDs, deduped reruns); every PR comment stores its retrieval trace and
replay key. **Business gate (#398, both required):** 3 design partners
describe the pain unprompted AND >=1 puts money down. No blocking checks.

### Stage 3 — Slack ledger console (milestone m6, tracker #455)

- **Wave 0 (research lands earlier, in Stage 0):** #456/#459 -> #457 -> #458.
- **Wave 1 — console foundation:** ask surface + propose/stage surface
  (#455 children) + the `decision.merged` event-type decision.
- **Wave 2 — curation + privacy/dogfood:** confirm/reject/merge/supersede/
  stale actions; visibility boundaries, degraded modes, single-workspace
  dogfood.
- **Wave 3 — spine completion gate:** #454 (rewritten as a checklist).

**Exit gate:** cited answers; staged candidates with full provenance
(permalink, author, timestamp, model/prompt version, scope); curation updates
the ledger; Slack-created decisions consumed by the GitHub reviewer with zero
channel-specific evaluator code. Quantitative bar: >=10 confirmed ledger
writes and >=20 cited ask-answers with zero uncited confident answers.

### Future buckets (deferred until #454 passes)

- **Connected sources + earned blocking (m3, tracker #447):** policy trio
  (#409, #408, #410) gates all connectors; #403 extends the Stage 2 ingestion
  path (one code path); blocking keystone #413 consumes #379 + Stage 2
  feedback history; >=90% Wilson lower bound, >=20 fires, >=2 distinct
  accepting authors per decision; precision-wrong override auto-demotes.
- **MCP supply loop + enterprise (m4, tracker #448):** #424 design first;
  #425 exposes the shipped `decisions_for_diff` kernel, not a rebuild;
  content-free egress schema before any federation data flows.

### GTM track (m5, tracker #449 — parallel, not a final bucket)

#443 lands in Stage 0 Wave 1p; #437 maintained throughout; #436/#439 fire on
the Stage 0 gate outcome; #442/#402 and #434 gate Stage 2 onboarding;
#399/#400 wait until reviewer AND console are useful in dogfood; #438
month-six kill/pivot runs calendar-gated from Stage 2 partner onboarding,
consuming #443 + #337 + #395.

## Path to first customer (added 2026-06-10)

Five overlapping phases from PE-0 to the first design-partner
conversation. P1-P3 run in parallel; P4 follows the P1 gate per the
do-not-host rule; P5 requires P4 plus the dogfood bar.

- **P1 — Close the Stage 0 gate.** The Wave 8/9 tail (#322, #326, #338,
  #367, #368, #373, #374, #376, #339), the #450 batch replay over the
  corpus, LLM-judge pre-grading + founder spot-check against the >=70%
  bar (#378), and the #337 report verdict. Founder effort: ~20 minutes of
  grading, one proceed/grind/narrow/fallback call.
- **P2 — Dogfood deep on our own projects.** vesper, vanguard, and
  outrider each get the full live loop (triage → push to their own
  compass tenants → confirm → ask → review on real diffs); cortex runs
  `cortex review` on its own PRs pre-merge. Every friction point files an
  issue same-day (the PE-0 pattern). Cross-repo evidence feeds #339's
  sibling-corpus completion.
- **P3 — Simlab: the simulated testing environment.** Deterministic fake
  projects (#520), scripted PR scenarios with known expected findings —
  the end-to-end regression harness and the demo rails (#521), and a
  standing isolated demo tenant with a 5-minute customer script (#522).
  Simlab is both the safety net for fast iteration and the demo
  environment for P5 conversations.
- **P4 — Hosted core up and running (Stage 1, tracker #485).** API shell
  + webhook receiver (#470), worker (#471), server-side model transport
  (#517), env/secret docs (#469/#475), backups + observability drills
  (#473/#474). Exit: the same loop served over HTTP from compass, one
  code path with local.
- **P5 — First customer conversation.** Prerequisites: P1 verdict =
  proceed; #452/#453 dogfood-on-cortex-PRs bar met; the App registered
  (docs/setup/github-app.md — owner task) with Marketplace verification
  filed (#384); the outreach pack (#402 expectations one-pager, #442
  legal surfaces, #396 install playbook) and the #437 warm-referral map.
  The conversation target is a design partner per Journey 4 in
  docs/product/customer-journeys.md — pilot/LOI, not self-serve.

## Success Criteria

- The active session-start state points to this plan as the master current
  work and no older `.cortex/plans/*.md` file remains `Status: active` for the
  superseded CLI/context-integrity launch track.
- GitHub issue #444 links back to the Obsidian master plan and the staged
  issue breakdown is aligned to the four-stage spine via the per-stage
  trackers (#445, #485, #446, #455) — one canonical checklist per stage.
- Stage 0 local proof passes its exit gate with the #337 report as the
  artifact (>=70% hand-graded advisory bar; replay keys; cited-or-no-answer).
- Hosted Railway core passes its exit gate (API/worker/Postgres, tested
  restore, environment separation, one code path with local).
- GitHub advisory reviewer dogfoods on Cortex PRs without spam (#452 against
  #453's bars) and stores feedback/overrides in the ledger; #398 money-down
  gate recorded in #443's fork-signal ledger.
- Slack ledger console passes its quantitative bar and #454's spine gate
  closes the loop without passive workspace ingestion.

## Work items

- [x] Align GitHub roadmap issues to the Obsidian master plan stages —
  completed 2026-06-09: milestones made the single stage authority, retired
  `stage-N` labels deleted, 12 shipped/duplicate issues closed with evidence,
  7 milestone moves, per-stage trackers reconciled (#485 created), ~60 issue
  bodies rescoped against the shipped substrate.
- [x] Stage 0 substrate: database/search/ledger schema, provenance, scope
  index, retrieval, visibility (#460-#466, #468) — shipped via PRs #477-#483,
  2026-06-09.
- [ ] Stage 0 local proof — Waves 1-7 and most of 8 SHIPPED (derive with
  six extractors, eval harness, evaluator with both thesis finding classes,
  replay runner, read-value surfaces, push/triage/review verbs; first live
  catch 2026-06-10 per the walkthrough). Remaining: #322/#326/#338/#367/
  #368/#373/#374/#376/#339 + gate artifacts #450 → #378 → #337.
- [ ] Stage 1 hosted core: #469-#475 (tracker #485), including the first
  executable SQL path (#472).
- [ ] Stage 2 GitHub reviewer: #384-#397 build, #452/#453 dogfood,
  #398/#401/#402 gates (tracker #446).
- [ ] Stage 3 Slack ledger console: #455 + children, #454 spine gate.
- [ ] Keep `README.md` and `SPEC.md` focused on the shipped CLI/protocol until
  hosted behavior exists.

## Follow-ups (deferred)

- journal/2026-06-09-hosted-decision-reviewer-plan-adopted resolves blocking
  checks, passive Slack ingestion, Linear/Granola connectors, MCP supply loop,
  enterprise/on-prem packaging, and marketplace billing as deferred until the
  local proof, hosted core, GitHub reviewer, and Slack ledger console are
  useful; GitHub issue #444 keeps the broader backlog visible, and the Future
  buckets above name their activation conditions.

## Known limitations at exit

- This plan does not replace the detailed Obsidian notes; it routes repo
  agents to them.
- This plan does not change `SPEC.md` or the current `.cortex/` file-format
  protocol.
- This plan does not claim the hosted product exists yet — the substrate is
  schema/SQL-as-strings until #472 lands the first execution path.
