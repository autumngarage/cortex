# Cortex scope and anti-goals — the reviewer product

**Date:** 2026-06-10
**Owns:** the canonical scope and anti-goals for the Cortex reviewer
product. One doc, one owner (per documentation-ownership): later issues,
reviews, and feature proposals cite this document instead of restating
scope. Sequencing authority is `cortex_master_plan.md` (Obsidian,
canonical 2026-06-09); product strategy detail lives in the product &
technical vision doc in the same vault. End-to-end user journeys live in
[customer-journeys.md](./customer-journeys.md); pricing mechanics in
[../HOSTED-PRICING.md](../HOSTED-PRICING.md).

**Enforcement:** #441 is the enforcement issue. It is rescoped to
enforcement-only and blocked by this document — every check it implements
(CI, doctor-style audit, or review-time gate) must cite a specific
section of this file. This document defines the rules; #441 ships the
teeth.

---

## What Cortex is

Cortex is **the decision ledger plus the reviewer that enforces it**: a
provenance-first record of what the team decided — captured from the
surfaces where decisions are actually made — and a soft evaluator that
reviews every new change against that record, leaving advisory findings
that cite the exact decision and where it was made. Advisory by default;
any individual decision earns the right to block only after its own
measured precision clears a high bar. The open `.cortex/` file protocol
and the reference CLI are the local foundation of that product: the
ledger's portable, git-native source of truth.

---

## The named invariant: broad inputs, narrow output

This section is the canonical home of the guardrail. It is stated as a
falsifiable invariant: an output either appears in the allowed list below
or it is a scope violation.

**Broad inputs.** Cortex may ingest from anywhere decisions are made or
revealed: repo-native sources (instruction files, ADRs, `CODEOWNERS`,
commits, PR descriptions and review comments, patterns in code), ledger
events, source documents and spans, decision scopes, and — in later
stages — connected sources (Slack, GitHub issues, Linear, meeting
notes). Capture breadth is a feature; it is what a PR-bound tool
structurally cannot assemble.

**Narrow output.** Cortex acts only through these output surfaces:

1. **Cited decisions** — answers to "what did we decide about X?" that
   name the decision, its provenance (source, author, timestamp, link),
   and its supersede state.
2. **Advisory findings** — PR review comments or check annotations that
   cite the specific decision a change contradicts, with a confidence
   tier. A promoted decision may block, per-decision, only after its
   measured precision earns it.
3. **Explicit no-answers** — when the ledger has no confirmed, cited
   decision, Cortex says so. A refusal with a reason is a correct
   output; a guess is not.
4. **Staged candidates** — decision candidates awaiting human
   confirmation. Candidates never answer questions and never block;
   humans confirm decisions into existence.
5. **Audit and diagnostic output** — usage receipts, degradation
   notices, and doctor-style reports that say what ran, what was
   skipped, and which safety boundary still holds.

**Forbidden outputs** (each falsifies the invariant):

- An uncited judgment, answer, or finding.
- A browsable destination surface: dashboard, wiki, or knowledge-base
  view of the decision graph.
- Generated or modified code.
- Autonomous action beyond posting the outputs above (merging, running
  agents, executing repository changes).
- A silent fallback — any degraded mode that does not report itself.

The shipped hosted substrate already enforces parts of this at the code
level; see [Scope consequences already shipped](#scope-consequences-already-shipped)
below.

---

## Anti-goals

Each anti-goal names at least one concrete thing Cortex will not do even
if asked, so a feature proposal can be rejected against it, plus one
sentence of why.

1. **Not a general chat agent.** Cortex will not answer questions
   ungrounded in the ledger — "write me a regex" in Slack gets a
   refusal, not an answer. Every conversational surface exists to query
   or curate the ledger; open-ended chat dilutes precision and spends
   credits without adding a single decision.

2. **Not a code generator.** Cortex will not author or apply the fix a
   finding suggests — applying changes belongs to the human or their
   agent. Writing code would make Cortex an author whose own output
   needs reviewing, collapsing the neutral-evaluator position that makes
   the merge-gate check trustworthy.

3. **Not a wiki, knowledge base, or dashboard.** Cortex will not ship a
   browsable decision-graph UI; the product split in
   [customer-journeys.md](./customer-journeys.md) names the web
   dashboard as the deliberately absent surface, and any journey that
   requires one should be rejected at review. The graph is an engine
   for evaluation, never a destination to browse — a destination
   surface competes with the team's real systems of record and turns
   capture into curation theater.

4. **Not a per-surface second memory system.** The Slack bot is a thin
   ledger console — ask, stage, confirm, supersede — not a separate
   memory product, and Cortex will not maintain a Slack-local (or
   GitHub-local) store of decisions. Two memory stores drift; one
   append-only ledger stays the single thing every surface reads and
   writes.

5. **No uncited output.** Cortex will not emit a finding or answer that
   does not name the decision it rests on and link to where it was
   made — when evidence is missing, the output is an explicit
   no-answer. An uncited judgment is a vibe: it cannot be audited,
   overridden, or used as training signal.

6. **Not an autonomous agent.** Cortex will not run agents, execute
   code, or merge anything — even a "just auto-apply the obvious fix"
   feature request fails this test. The merge gate is valuable
   precisely because Cortex is a neutral check on the agents that act,
   not another actor to supervise.

7. **Not a code reviewer.** Cortex will not judge code correctness,
   style, or performance; it evaluates whether the change contradicts
   what the team decided, and composes alongside code review and CI.
   Correctness tooling already exists — Cortex's value is the
   cross-source decision record those tools structurally cannot see.

## Author-agnostic evaluation

Evaluation keys on the **diff and decision scope**, never on whether a
human or an agent authored the change. Cortex will not implement
verdicts that differ by author identity — a "stricter on bot PRs"
evaluation mode fails this test. Marketing may ride the "review your AI
agents" wedge; the substrate stays author-agnostic, because findings
must be reproducible from the diff and the ledger alone, and a check
that keys on authorship stops being evidence and starts being policy
about who wrote the code.

## The open, portable standard

The decision format is an open, versioned, portable specification; the
`.cortex/` file protocol and git remain the source of truth, and the
hosted service is a workflow, inference, and audit layer around those
files — never a proprietary memory store. No feature may make
hosted-only data the sole copy of a decision; export is always
available. Exportability is the one property a lock-in-motivated
platform vendor will not offer, so it is load-bearing, not decoration.

---

## Scope consequences already shipped

The hosted substrate already encodes this scope at the code level. The
items below are named here as scope statements; **the code and its tests
stay authoritative for behavior** — this document does not restate SQL,
schema, or signatures.

- **Fail-closed no-answer.** `AnswerState.NO_ANSWER` in
  `src/cortex/hosted/ask_ledger.py`: a question without confirmed,
  cited evidence resolves to an explicit no-answer with a reason, never
  a guess (anti-goal 5; allowed output 3).
- **Fail-closed visibility.** `src/cortex/hosted/visibility.py`:
  retrieval refuses rather than widening when source authorization is
  absent (broad inputs do not imply broad disclosure).
- **Bounded retrieval with visible omission.** Diff-scoped retrieval is
  capped at 30 (`src/cortex/hosted/decisions_for_diff.py`) and reports
  `omitted_counts` — the evaluator judges a bounded, declared slice,
  never a silently truncated one (narrow output; forbidden output:
  silent fallback).

---

## Portfolio decision (2026-06-09): Cortex-the-reviewer is primary

Recorded per issue #307. **Conductor, Sentinel, and Touchstone are
frozen as product bets; Cortex-the-reviewer is the primary product
bet.** All three remain maintained dev-tooling — they keep working,
keep getting fixes, and keep composing with Cortex by file contract,
never code imports ([Doctrine 0002](../../.cortex/doctrine/0002-compose-by-file-contract-not-code.md)).
What changes is investment: new product work concentrates on the
reviewer loop (ledger → evaluator → GitHub → Slack), and the other
three tools are not being developed toward standalone commercial
products.

The boundary is enforced, not aspirational: the standalone-boundary
guardrail (`tests/test_standalone_boundary.py`, cortex#503) asserts
that `src/cortex/**` carries no quartet imports, no required subprocess
coupling, and no quartet packaging dependencies — the dependency arrow
points toward Cortex, never from it.
