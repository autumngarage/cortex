# Stage 0 gate report — proceed / grind / narrow / Contextlint fallback

> Template owned by cortex#343. Filled in exactly once by cortex#337 as the
> single Stage 0 proceed/no-proceed artifact; the Self-review section is
> supplied by cortex#451. Fill every field; do not delete sections or invent
> new structure. Hand grading only — model output is never ground truth.
>
> The headline quantitative bar, quoted verbatim from `cortex_master_plan.md`
> (as carried in `.cortex/plans/hosted-decision-reviewer.md` § Stage 0 exit
> gate): ">=70% of emitted advisory comments correct and useful on a
> hand-graded sample", with citation/budget/ledger sub-bars.
>
> Every metric below names the module/function that computes it. A value the
> repo cannot compute yet is marked operator-recorded with its source named —
> no orphan metrics, no silent gaps.

**Report metadata (required)**

- Report date: `YYYY-MM-DD`
- Graders: per-label provenance via `FixtureLabel.grader` / `FixtureLabel.graded_at`
  (`src/cortex/hosted/eval_fixtures.py`)
- Fixture corpus identity: one `EvalFixture.fixture_hash` per fixture
  (`src/cortex/hosted/eval_fixtures.py::EvalFixture.fixture_hash`)
- Replay key for the graded run: `(diff, decision-version, graph-snapshot
  hash, model, prompt-version)` — the same key the ledger stamps
  (`src/cortex/hosted/ledger_events.py::LedgerEvent`)

## Corpus

| Field | Value | Computed by |
|---|---|---|
| Fixture count | | fixture files loaded via `src/cortex/hosted/eval_fixtures.py::EvalFixture.from_json` |
| Repos covered (owner/name; must include Cortex and one external multi-author repo) | | `FixtureDiff.repo_owner` / `FixtureDiff.repo_name` (`src/cortex/hosted/eval_fixtures.py`) |
| `correct_useful` count | | `src/cortex/hosted/labeling.py::label_tally` → `LabelTally.counts` |
| `correct_not_useful` count | | `src/cortex/hosted/labeling.py::label_tally` → `LabelTally.counts` |
| `incorrect_precision` count | | `src/cortex/hosted/labeling.py::label_tally` → `LabelTally.counts` |
| `missed_expected` count (recall signal, reported separately) | | `src/cortex/hosted/labeling.py::label_tally` → `LabelTally.missed_expected_count` |
| Unlabeled expected findings (must be 0 before grading closes) | | `src/cortex/hosted/labeling.py::load_unlabeled_findings` |
| Spot-check sample size (at least 10 items or 10% of the batch, whichever is larger) | | `src/cortex/hosted/labeling.py::spot_check_sample_size` |
| Inter-rater agreement rate | | `src/cortex/hosted/labeling.py::disagreement_report` → `DisagreementReport.agreement_rate` |
| Unresolved grader disagreements (must be 0; resolutions carry notes) | | `DisagreementReport.unresolved_disagreements` (`src/cortex/hosted/labeling.py`) |

## Candidate-set quality

Sub-bar, quoted verbatim from the Stage 0 exit gate: "`decisions_for_diff`
returns bounded cited candidates".

| Field | Value | Computed by |
|---|---|---|
| `omitted_counts` per graded query (surfaced, never silently dropped) | | `DecisionsForDiffCandidatePack.omitted_counts` (`src/cortex/hosted/decisions_for_diff.py`) |
| `candidate_growth_ratio` distribution (`candidate_pool_size / graph_node_count`) | | `DecisionsForDiffCandidatePack.candidate_growth_ratio` (`src/cortex/hosted/decisions_for_diff.py`) |
| `candidate_set_hash` recorded for every graded run | | `DecisionsForDiffCandidatePack.candidate_set_hash` (`src/cortex/hosted/decisions_for_diff.py`) |
| `reason_codes` coverage per candidate (why each candidate was retrieved) | | `DecisionsForDiffCandidatePack.reason_codes` (`src/cortex/hosted/decisions_for_diff.py`) |
| Retrieval-failure vs evaluator-failure split on missed findings | | replay runner (cortex#336) over `RetrievalTrace` rows (`src/cortex/hosted/ask_ledger.py::RetrievalTrace`) |

## Advisory quality

Headline bar, quoted verbatim from `cortex_master_plan.md` via the Stage 0
exit gate: ">=70% of emitted advisory comments correct and useful on a
hand-graded sample".

| Field | Value | Computed by |
|---|---|---|
| `useful_rate` = `correct_useful` / all graded emitted findings | | `src/cortex/hosted/labeling.py::label_tally` → `LabelTally.useful_rate` |
| `useful_rate` vs the >=70% bar (pass requires `useful_rate >= 0.70`) | | comparison against the bar above; rate from `LabelTally.useful_rate` |
| `precision_correct` = correct / (correct + `incorrect_precision`), correct = `correct_useful` + `correct_not_useful` | | `LabelTally.precision_correct` (`src/cortex/hosted/labeling.py`) |
| Zero-division visibility: any metric reported as None must carry its reason verbatim | | `LabelTally.useful_rate_unavailable_reason` / `LabelTally.precision_correct_unavailable_reason` (`src/cortex/hosted/labeling.py`) |

## Citation quality

Sub-bar, quoted verbatim from the Stage 0 exit gate: "`ask_ledger` answers
with citations or honestly says it does not know". Evidence here is
fail-closed: uncited findings are structurally unrepresentable, so this
section records that the guards held, not that reviewers were careful.

| Field | Value | Computed by |
|---|---|---|
| Findings carried without at least one cited span hash (must be 0 by construction) | | `ExpectedFinding.cited_span_hashes` validator (`src/cortex/hosted/eval_fixtures.py`); `finding.emitted` required fields (`src/cortex/hosted/ledger_events.py::LedgerEvent`) |
| `NO_ANSWER` / `no_cited_support` rate on ask-ledger queries during the run | | `CitedContextPack` / `AnswerState` (`src/cortex/hosted/ask_ledger.py`) |
| Cited spans resolving to visible documents (deny-by-default held) | | `visible_decision_version_exists_sql` (`src/cortex/hosted/visibility.py`) |
| Tampered or drifting span hashes rejected on fixture reload | | `FixtureSourceSpan.from_payload` recomputation check (`src/cortex/hosted/eval_fixtures.py`) |

## Budget behavior

Sub-bar context: bounded candidate packs must surface what they omitted; an
omission nobody can see is the failure mode this section guards.

| Field | Value | Computed by |
|---|---|---|
| `omitted_counts` surfaced on every bounded pack and trace (zero silent truncations) | | `DecisionsForDiffCandidatePack.as_trace` (`src/cortex/hosted/decisions_for_diff.py`) |
| Top-K cap respected (candidates <= limit; overflow visible in `omitted_counts`) | | `MAX_DECISIONS_FOR_DIFF_LIMIT` / `DEFAULT_DECISIONS_FOR_DIFF_LIMIT` (`src/cortex/hosted/decisions_for_diff.py`) |
| `candidate_pool_size` vs `graph_node_count` per graded query | | `DecisionsForDiffCandidatePack` fields (`src/cortex/hosted/decisions_for_diff.py`) |
| Run cost (tokens / dollars) for the graded sample | | operator-recorded from provider usage output; no repo module computes this yet — named here so it is not an orphan gap |

## Ledger quality

Sub-bar, quoted verbatim from the Stage 0 exit gate: "every finding carries a
replay key" and "historical replay separates retrieval failure from evaluator
failure".

| Field | Value | Computed by |
|---|---|---|
| `event_hash` present and deterministic for every graded finding event | | `LedgerEvent.event_hash` over `as_immutable_payload()` (`src/cortex/hosted/ledger_events.py`) |
| Idempotency keys unique per tenant (retry-safe writes) | | `derive_idempotency_key` (`src/cortex/hosted/ledger_events.py`) |
| `graph_snapshot_hash` + `retrieval_config_version` + `query_input_hash` + `candidate_set_hash` stored per retrieval | | `RetrievalTrace` (`src/cortex/hosted/ask_ledger.py`); `DecisionsForDiffCandidatePack.as_trace` (`src/cortex/hosted/decisions_for_diff.py`) |
| Byte-for-byte replay result over `(diff, decision-version, graph-snapshot hash, model, prompt-version)` | | replay runner (cortex#336) against the replay key fields above |
| Cited answers vs honest `NO_ANSWER` outcomes on ledger questions asked during the run | | `CitedContextPack` (`src/cortex/hosted/ask_ledger.py`) |

## Self-review

Required, non-optional. Owned by cortex#451: Cortex graded against its own
PRs, by the people who wrote them. Every claim cites fixture IDs, finding
IDs, and the labels assigned (`FixtureLabel`, `src/cortex/hosted/eval_fixtures.py`).

- **What Cortex caught:** findings on Cortex's own PRs labeled
  `correct_useful` —
- **What Cortex missed:** expected findings labeled `missed_expected` on
  Cortex's own PRs —
- **What would have been noisy:** findings labeled `correct_not_useful` (and
  any `incorrect_precision`) on Cortex's own PRs —

## Decision

Check exactly one outcome. Whichever is chosen requires cited evidence rows
from the sections above — the decision cannot be retrofitted to vibes.

- [ ] **proceed** — trigger: `useful_rate >= 0.70` on the hand-graded sample
  AND the candidate-set, citation, budget, and ledger sub-bar sections all
  pass on both repos. Next step: Stage 1 hosted core (tracker cortex#485).
  Evidence:
- [ ] **grind** — trigger: the headline bar missed, and the replay split
  (Candidate-set quality, last row) attributes the misses to evaluator
  failure while retrieval produced the right cited candidates. Next step:
  improve the evaluator and re-run this report from a fresh copy.
  Evidence:
- [ ] **narrow-to-structured-memory-repos** — trigger: the bar passes on
  structured-memory repos (Cortex-style corpora) but fails on the external
  multi-author repo because its decision corpus is too sparse to retrieve
  against. Next step: narrow Stage 1 scope to structured-memory repos.
  Evidence:
- [ ] **contextlint-fallback** — trigger: the bar fails with healthy
  retrieval AND healthy citations — the advisory approach itself did not
  produce useful comments. Next step: pivot per cortex#439.
  Evidence:
