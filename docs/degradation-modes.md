# Reviewer degradation modes — the no-silent-failure taxonomy

**Date:** 2026-06-09
**Owns:** the behavioral vocabulary for visible reviewer failure
(`DegradationMode`), the per-exception-type classification contract
(`classify_failure`), and the reporting shape (`DegradationReport`) in
[`src/cortex/hosted/degradation.py`](../src/cortex/hosted/degradation.py).
**Does not own:** the substrate invariants themselves
([docs/hosted-architecture.md](./hosted-architecture.md) § 1.3 owns the
as-built map) or the evaluator behavior that applies this taxonomy
(cortex#377, Wave 5).
Closes cortex#329.

The engineering-principles rule this operationalizes: *fallback behavior may
continue only when it reports what failed, what was skipped, and what safety
boundary still holds.* Every mode below names a behavior the shipped
substrate already exhibits; the taxonomy gives those behaviors one
vocabulary so the evaluator (cortex#377, Wave 5) and the Stage 2 GitHub
reviewer can surface them uniformly instead of leaking per-module exception
names to users.

Two consumption rules:

1. **`classify_failure` maps raised substrate exceptions and the
   `AnswerState.NO_ANSWER` state to a mode.** Dispatch is by exact concrete
   type. Unknown exception types raise `DegradationTaxonomyError` — an
   unclassified failure is never treated as benign, and a subclass never
   inherits its parent's classification without review.
2. **`DegradationReport` is how a continuing code path declares a
   degradation.** It requires a mode, a non-empty `reason_code` (why), a
   non-empty `source` (what failed), and `safety_boundary_held=True`. A
   report cannot be constructed with `safety_boundary_held=False`: if no
   boundary held, the failure is an incident that must propagate, not a
   degradation that may continue.

The per-type classification table is the dispatch table in
`degradation.py` (`classified_failure_types()` exposes it); this document
owns the behavioral definitions.

---

## `fail_closed_refusal`

**Trigger.** The system has an answerable-looking request but refuses to
answer because the material required to answer safely is absent: no cited
support, no explicit source authorization, or an operation that would cross
a declared boundary.

**Shipped examples.**

- `ask_ledger.build_cited_context_pack` returns a `CitedContextPack` with
  `AnswerState.NO_ANSWER` and `no_answer_reason="no_cited_support"` when no
  candidate survives the citation filter — an uncited answer is structurally
  unrepresentable (`src/cortex/hosted/ask_ledger.py`).
- `visibility.normalize_visible_source_ids` raises
  `VisibilityBoundaryValidationError` when retrieval is attempted without at
  least one explicitly authorized source ID — deny-by-default, before any
  SQL (`src/cortex/hosted/visibility.py`).
- `storage.validate_canonical_store` raises `StoreBoundaryError` when hosted
  code tries to use a store other than Postgres for product semantics
  (`src/cortex/hosted/storage.py`).
- `replay_runner.run_fixture` raises `ReplayError` naming the fixture id when
  a recorded evaluate response is missing — replay refuses to fall back to a
  live model call (`src/cortex/hosted/replay_runner.py`, cortex#336).

**What the user sees.** An explicit refusal with the reason: "no decision
in the ledger supports an answer to this", or an authorization error naming
the missing boundary. Never an empty-but-confident answer.

**Never allowed.** Synthesizing an answer without citations; widening
visibility to make a query succeed; downgrading the refusal to a warning
while still emitting output.

## `bounded_omission`

**Trigger.** The system answers, but the candidate set was larger than the
bound it is allowed to return, and the dropped remainder is counted and
visible.

**Shipped examples.**

- `decisions_for_diff.build_decisions_for_diff_candidate_pack` caps
  candidates at `MAX_DECISIONS_FOR_DIFF_LIMIT` (30) and records what was
  dropped in `omitted_counts`, which travels into the retrieval trace
  (`src/cortex/hosted/decisions_for_diff.py`).
- `ask_ledger.build_cited_context_pack` records `missing_citations` and
  `over_limit` counts in `omitted_counts` on every pack — even when the
  answer ships (`src/cortex/hosted/ask_ledger.py`).
- Dropped derive chatter is the same behavior at the write side: lane policy
  logs dropped candidates with reason codes instead of writing them as graph
  state (docs/hosted-architecture.md § 3.1).

**What the user sees.** A normal answer plus visible omission diagnostics:
counts per omission reason in the trace/replay report (cortex#331 owns the
diagnostic surfacing).

**Never allowed.** Dropping candidates without incrementing a visible
count; truncating context silently to fit a budget; an `omitted_counts`
that undercounts what was actually dropped.

## `invalid_input_rejected`

**Trigger.** Material fails validation before any model call or any write —
the operation never starts, so there is no partial output to clean up.

**Shipped examples.**

- `LedgerEvent.__post_init__` raises `LedgerEventValidationError` when a
  `finding.emitted` event lacks span hashes, a graph snapshot hash, or the
  (model_id, prompt_version) pair (`src/cortex/hosted/ledger_events.py`).
- `provenance.SourceDocument.span` raises `ProvenanceValidationError` on
  out-of-range offsets (`src/cortex/hosted/provenance.py`).
- `diff_surface.extract_changed_surface` raises
  `DiffSurfaceValidationError` on unparseable patch text
  (`src/cortex/hosted/diff_surface.py`).
- `eval_fixtures.EvalFixture` raises `FixtureValidationError` on fixtures
  whose findings cite spans absent from the fixture's decisions
  (`src/cortex/hosted/eval_fixtures.py`).
- `model_interfaces` (cortex#344) raises `ModelInterfaceValidationError`
  when boundary material cannot support replayable model calls.

**What the user sees.** The validation error itself, naming the field and
the constraint. The reviewer reports the input as rejected; it does not
review a best-effort repair of it.

**Never allowed.** Coercing or defaulting invalid fields to make the call
proceed; catching the validation error and continuing with a partial
object; retrying the same invalid input.

## `drift_detected`

**Trigger.** Recorded identity material — a hash, a version stamp — does
not match the content it claims to describe. The two sides have drifted,
and results spanning the drift are not comparable.

**Shipped examples.**

- `model_registry.ModelPromptRegistry.resolve_prompt_version` raises
  `RegistryValidationError` when a stamped prompt-version's hash prefix does
  not match the registered template content — "prompt drift detected;
  refuse to treat the verdicts as comparable"
  (`src/cortex/hosted/model_registry.py`).
- `RegisteredPrompt.from_payload` raises on a recorded `content_hash` that
  the stored template text no longer hashes to
  (`src/cortex/hosted/model_registry.py`).
- At review time, every `RegistryValidationError` means a verdict's
  (model, prompt) identity cannot be trusted as stated — unregistered
  stamps and non-dense versions are the same broken-identity behavior, so
  the type classifies as drift. The kindred hash/version mismatches in
  other modules (`eval_fixtures` span-hash recompute mismatch,
  fixture-schema-version mismatch) classify under their type's dominant
  reviewer-runtime behavior (`invalid_input_rejected`); cortex#377 may
  sub-classify those sites where the evaluator can see the behavior
  directly.

**What the user sees.** A refusal to compare or replay across the drift
boundary, naming both sides of the mismatch. Verdicts produced before the
drift stay in the ledger; they are not silently blended with post-drift
verdicts (the version-your-data-boundaries principle).

**Never allowed.** Treating pre- and post-drift outputs as comparable;
re-stamping content to make hashes match; guessing which side of the
mismatch is "current".

## `degraded_capability`

**Trigger.** An optional dependency or lane is unavailable, and the system
continues in an explicitly reduced mode that it declares — what failed,
what is skipped, what boundary still holds.

**Shipped examples.**

- `embeddings.VectorRecallReport.passed` returning `False` (recall below
  the 0.95 floor) means the vector lane is unreliable; the declared reduced
  mode is retrieval without the vector source — the other ranked sources
  (exact/scope/full-text/trigram/graph in `ask_ledger.SOURCE_WEIGHTS`)
  still run, and the citation boundary is unaffected
  (`src/cortex/hosted/embeddings.py`).
- `degradation.unregistered_optional_failure_sources()` is this module
  dogfooding the mode: when `cortex.hosted.model_interfaces` (cortex#344)
  is not yet importable, its exception type is not registered — declared
  visibly, and provably safe because an exception class that does not
  exist cannot be raised.

**What the user sees.** A declared reduced mode: "vector retrieval
disabled (recall 0.91 < 0.95 floor); lexical and structural retrieval
active." Output produced in a reduced mode still satisfies every boundary
above — cited, bounded, visible.

**Never allowed.** Continuing without declaring the reduction; classifying
an *unexpected* `ImportError` as degraded capability (`classify_failure`
deliberately has no `ImportError` mapping — only a call site that probes an
optional dependency on purpose may declare this mode); letting a reduced
mode skip citation or visibility boundaries.

---

## Relationship to later stages

- **cortex#377 (Wave 5)** applies this taxonomy inside the soft evaluator:
  every evaluator failure path must classify, and every continuing path
  must emit a `DegradationReport`.
- **Stage 2 (GitHub reviewer)** surfaces these modes in advisory comments —
  the user-facing strings quote `DegradationMode` values so operators can
  grep a PR comment back to the code path that degraded.
- **cortex#331 (Wave 7)** owns the omitted-decision diagnostic surfacing
  that makes `bounded_omission` legible in replay reports.

### Cross-module registrations added at bundle integration (2026-06-09)

- `lanes.LanePolicyValidationError`, `confidence.ConfidenceValidationError`,
  and `labeling.LabelingError` classify as `invalid_input_rejected` —
  policy/evidence/label material rejected before any state change.
- `derive_store.DeriveStoreError` classifies as `drift_detected`: its
  marquee failure is a same-idempotency-key / different-event-hash
  collision, i.e. recorded state disagreeing with a re-derivation.
- `graph_writes.GraphWriteValidationError` classifies as
  `invalid_input_rejected` — a write plan that would violate graph
  invariants is refused before any statement executes.

### Wave 4-6 bundle registrations (2026-06-09)

- `cost.BudgetExceededError` -> `fail_closed_refusal`: a call refused before
  spend to hold the budget boundary; `cost.CostValidationError` ->
  `invalid_input_rejected`.
- `routing.RoutingError` -> `invalid_input_rejected` (config/contract
  rejected); `ClaudeCliUnavailableError` -> `degraded_capability` (transport
  missing, named visibly); `ClaudeCliOutputError` -> `fail_closed_refusal`
  (unparseable model output is refused, never fabricated);
  `RecordedResponseMissingError` -> `fail_closed_refusal` (a missing
  recording never falls back to a live call).
- `recorded_responses.RecordedResponseError` -> `drift_detected` (hash or
  schema-version mismatch in recorded material).

### Eval corpus builder registration (2026-06-10)

- `corpus_builder.CorpusBuilderError` -> `invalid_input_rejected`: corpus
  material that cannot be frozen into a replayable fixture (unmerged PR,
  empty diff, ambiguous citation excerpt, non-canonical fixture bytes) is
  rejected before anything is written.
- `context_assembly/citation_check/candidate_metrics/graph_snapshot/
  event_ordering` validation errors -> `invalid_input_rejected`.
- `extractors.ExtractorError` (cortex#351-#353) classifies as
  `invalid_input_rejected`: a source no repo-native extractor recognizes is
  rejected before any extraction or write. Recognized-but-noisy material is
  not a failure at all — it surfaces as `DroppedChatter` with a reason code
  (the write-side `bounded_omission` behavior above).
- `banking.BankingValidationError`, `cascade.CascadeValidationError`, and
  `quality_series.QualitySeriesValidationError` classify as
  `invalid_input_rejected` — policy/composition/series material rejected
  before any decision or rate is produced.
- `route_comparison.RouteComparisonValidationError` classifies as
  `invalid_input_rejected`.

### Wave 5 evaluator registrations (2026-06-10)

- `advisory_ladder.AdvisoryLadderError` -> `invalid_input_rejected`: an
  unknown confidence label or ladder-vocabulary violation is rejected before
  the finding can be placed on the ladder (cortex#375).
- `evaluator.EvaluatorValidationError` -> `invalid_input_rejected`:
  evaluator material that violates the soft-evaluator contract (finding-class
  evidence, registry shape, outcome arithmetic) is rejected before any
  emission or ledger draft exists (cortex#370-#372).
- `evaluator.UncitedFindingError` -> `fail_closed_refusal`: a finding whose
  provenance is absent from the candidate pack (unresolvable decision ref or
  span hash the pack never offered) is refused emission outright — the
  citation boundary holds (cortex#377), mirroring `ask_ledger`'s
  `no_cited_support` refusal.
- `finding_render.FindingRenderError` -> `fail_closed_refusal`: a finding
  block whose cited span hash is absent from the span index is refused
  rendering (cortex#376) — an advisory surface never renders a citation a
  reader cannot verify; the render-side mirror of the cortex#377 boundary.

### Stage 2 GitHub comment registration (2026-06-10, cortex#390)

- `github_comment.GitHubCommentRenderError` -> `fail_closed_refusal`: the
  advisory PR-comment renderer refuses to build a comment whose cited
  decision does not resolve to a permalink through the span index — one
  surface further out than `FindingRenderError`, same citation boundary.
  Remediation: re-run the review against the current candidate pack so the
  cited spans resolve; a finding that cannot resolve provenance is logged and
  never posted uncited, so the missing-span render only fires when the pack
  and the emitted findings drifted apart.

### Graph-hardening registrations (2026-06-10, cortex#318/#319/#320)

- `candidate_dedup.CandidateDedupError` -> `invalid_input_rejected`:
  malformed identity material or a non-`candidate.proposed` event is refused
  before any dedup fold or graph write — nothing partial is produced.
- `graph_rebuild.GraphRebuildError` -> `invalid_input_rejected`: a replay
  whose event log cannot fold into a valid projection (missing replay-contract
  payload keys, unknown node references, same-idempotency-key/different-hash
  content) is refused outright; no partial rebuilt graph is ever returned.

### Read-value surface registrations (2026-06-10, cortex#381/#382)

- `ask_surface.AskSurfaceValidationError` -> `invalid_input_rejected`:
  malformed answer material (an uncited answer line, a no-answer carrying
  lines) is refused at construction before any rendering.
- `ask_surface.BrowseIndexRefusedError` -> `fail_closed_refusal`: a
  browse-shaped or empty question is refused to hold the no-browsable-index
  boundary (cortex#382) — the corpus is never enumerated to make a query
  succeed.

### Retrieval question-normalization registration (2026-06-10, cortex#512)

- `question_normalization.QuestionNormalizationError` ->
  `invalid_input_rejected`: an empty question is rejected before any
  boilerplate stripping or retrieval — nothing partial reaches the FTS leg.

### Executable-path registrations (2026-06-09, cortex#472)

- `db.HostedDbError` classifies as `fail_closed_refusal`: a connection that
  cannot satisfy the hosted policy (missing driver, invalid URL, unreachable
  host, auth failure) is refused with a named reason before any partial
  state exists.
- `migrations.HostedMigrationError` classifies as `fail_closed_refusal`: a
  missing extension, a newer-than-this-build recorded schema version, or an
  unverifiable `schema_migrations` record blocks the migration visibly and
  rolls back — the runner never reports a success it cannot read back.

### Server transport registrations (2026-06-10, cortex#517)

- `api_transport.ApiKeyMissingError` -> `degraded_capability`: the route's
  configured API-key environment variable (default `ANTHROPIC_API_KEY`,
  overridable via the `api_key_env` route param) is unset or blank in the
  service environment. The refusal names the variable and carries the
  `model_api_key_missing` remediation hint — never a bare traceback, and
  never a request sent without credentials.
- `api_transport.ApiHttpOutputError` -> `fail_closed_refusal`: a transport
  failure (non-retryable HTTP status, exhausted 429/5xx retries, network
  error, truncated output) or a response violating the strict-JSON output
  contract is refused, never fabricated — mirroring `ClaudeCliOutputError`,
  including carrying reported token usage so the failed call's cost record
  still accounts for the spend.

### Push registration (2026-06-10, cortex#513)

- `push.HostedPushError` -> `drift_detected`: its marquee failure is a
  derive-export row whose recomputed event hash, or a working-tree file
  whose content-keyed document hash, no longer matches what the export
  recorded — `cortex push` refuses to replay content that disagrees with
  its recorded identity, naming both sides. (Span drift on a file-backed
  candidate is not an error at all: the candidate is excluded as a counted,
  path-naming skip — the write-side `bounded_omission` behavior.)
### Stage 1 service-shell registrations (2026-06-10, cortex#470/#471)

- `jobs.HostedJobError` -> `invalid_input_rejected`: a job that would
  violate the canonical queue contract (empty job type or idempotency key,
  non-JSON-object payload, malformed claim row, invalid backoff parameters)
  is rejected before any row is written or any handler runs.
- `api.config.ServiceConfigError` -> `invalid_input_rejected`: a malformed
  service environment (non-integer `PORT`, non-UUID `CORTEX_TENANT_ID`,
  blank-but-set secret, unpaired tenant/source mapping) refuses startup
  before any request is served. Missing *optional* variables are not errors
  — they degrade per endpoint (degraded `/healthz` body, 503 webhook
  refusal) with the gap named in the response.
- `api.webhooks.WebhookValidationError` -> `invalid_input_rejected`: a
  structurally malformed delivery (bad event-name header, oversized
  delivery GUID, non-object JSON body) is rejected with a 400 before any
  job row exists. Signature mismatches are not raised at all — they are
  answered 401 with no detail about which part failed.

### Remediation hints (2026-06-10, cortex#516)

Errors are the onboarding surface of a fail-closed product: a refusal that
names the problem but not the next command is a dead end. Two additions
operationalize that:

- `DegradationReport` carries an optional `remediation` field — exactly one
  actionable next command. It must be non-empty when present (a blank hint
  fails validation as `DegradationTaxonomyError`) and appears in
  `as_payload()` output only when set, so consumers distinguish "no hint
  registered" by key absence, never by a null.
- Hints live in one module-level table,
  `degradation.REMEDIATION_BY_REASON`, looked up via `remediation_for`
  (fail-closed: unknown reason codes raise instead of returning a generic
  hint). The CLI refusal surfaces — `cortex ask` (missing `DATABASE_URL`,
  missing driver, missing graph snapshot, the `no_cited_support` no-answer),
  `cortex derive` (missing `.cortex/`, no default sources), and
  `cortex candidates` (missing derive store) — draw from the same table, so
  there are no scattered per-call-site hint strings. The `no_cited_support`
  hint additionally carries the live pending-candidate count from the local
  derive store when it is cheaply available ("N candidates await review —
  run `cortex candidates triage`"); a count failure is reported inline,
  never silently dropped.
