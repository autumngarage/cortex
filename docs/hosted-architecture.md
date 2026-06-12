# Hosted Stage 0 architecture — as built

**Date:** 2026-06-09
**Owns:** the as-built map of the hosted decision-ledger substrate, the
reconciliation of the remaining Stage 0 foundation backlog against it, and the
forward-looking shape of derive / evaluator / eval-harness work.
**Does not own:** the storage-boundary decision rationale
([docs/hosted-ledger.md](./hosted-ledger.md) owns that), the product strategy
(`cortex_master_plan.md` in the Obsidian vault, canonical 2026-06-09), or the
build sequence (`.cortex/plans/hosted-decision-reviewer.md` § Build sequence).
Closes cortex#310.

**Inputs read for this brief** (the four required classes):

1. All nine substrate modules: `src/cortex/hosted/{schema, storage,
   ledger_events, provenance, scopes, ask_ledger, decisions_for_diff,
   embeddings, visibility}.py` (shipped via PRs #477–#483, closing #460–#466
   and #468).
2. Their tests: `tests/test_hosted_*.py` (string-assertion suites, one per
   module).
3. [docs/hosted-ledger.md](./hosted-ledger.md) (storage boundary, event
   invariant, visibility boundary, provenance snapshots).
4. [.cortex/plans/hosted-decision-reviewer.md](../.cortex/plans/hosted-decision-reviewer.md)
   (stage spine, waves, exit gates).

> **Historical caveat (as written 2026-06-09; resolved 2026-06-11).** At
> writing time the substrate was non-executing — SQL strings and dataclass
> validation only, no Postgres driver, migration runner, or CLI wiring. That
> is no longer true: cortex#472 shipped the psycopg driver + migration
> runner, schema v9 is applied on the compass Railway database, and the live
> API + worker services run against it (see
> [hosted-deploy.md](./hosted-deploy.md) and the module-map addendum below).
> The sections below describe the contract layer as designed at writing time.

---

## 1. As-built architecture

### 1.1 Layer model

The substrate is one event log plus rebuildable projections, exactly as
`docs/hosted-ledger.md` commits to:

```
                       writes (append-only)
  ───────────────────────────────────────────────────────────
   ledger_events            ← source of truth (DB triggers forbid UPDATE/DELETE)
  ───────────────────────────────────────────────────────────
   projections (rebuildable from the log + provenance snapshots):
     decision_nodes / decision_versions / decision_edges   (current graph)
     decision_scopes                                       (structural index)
     embeddings                                            (vector projection)
     retrieval_traces                                      (replay/debug record)
     graph_snapshots                                       (snapshot registry)
  ───────────────────────────────────────────────────────────
   immutable provenance (not projections — snapshots):
     source_documents / source_spans                       (citation substrate)
  ───────────────────────────────────────────────────────────
   tenancy + authorization:
     tenants / repos / sources(.visibility)                (deny-by-default)
```

### 1.2 Module map

| Module | Role | Load-bearing exports |
|---|---|---|
| `schema.py` | Versioned Postgres DDL (`HOSTED_SCHEMA_VERSION = 6` at writing; 9 as of 2026-06-11), in-DDL migration blocks, append-only + provenance-immutability triggers, all indexes | `create_schema_sql()` |
| `storage.py` | Storage-boundary guardrail: Postgres is the only canonical store; SQLite allowed only under approved roles (`local-replay-export`, `retrieve-index-cache`) | `validate_canonical_store`, `validate_rebuildable_cache_store`, `HOSTED_STORAGE_DECISION` |
| `ledger_events.py` | The one write envelope: `LedgerEvent` (frozen, validated), 8 event types, per-type required fields, deterministic `event_hash` over `as_immutable_payload()`, retry-safe `derive_idempotency_key` | `LedgerEvent`, `LedgerEventType`, `ledger_event_insert_sql()` |
| `provenance.py` | Immutable source snapshots + citable spans: `document_hash` keyed by content (re-ingest with drift ⇒ new snapshot), `span_hash` over offsets + excerpt hash so citations survive re-derivation | `SourceDocument`, `SourceSpan`, idempotent insert SQL |
| `scopes.py` | 9-type structural scope vocabulary (`path, glob, symbol, package, config_key, owner, service, issue_ref, channel_ref`), normalization, per-type structural weights, `ChangedSurface` (diff → query scopes) | `DecisionScope`, `QueryScope`, `ChangedSurface`, `decisions_for_diff_scope_sql()` |
| `visibility.py` | Fail-closed retrieval authorization: non-empty `visible_source_ids` required before any SQL, deny flags (`deleted, revoked, slack_channel_excluded, repo_installation_revoked`), shared CTEs every retrieval query starts from, citation-visibility guard | `SourceVisibilityScope`, `visible_source_documents_ctes()`, `visible_decision_version_exists_sql()` |
| `ask_ledger.py` | Cited read path: hybrid retrieval (exact 120 / scope 100 / full-text 70 / trigram 55 / vector 50 / graph 35, RRF k=60), `CitedContextPack` that is structurally fail-closed (`AnswerState.NO_ANSWER` / `no_cited_support`), `as_trace()` to `retrieval_traces` | `AskLedgerQuery`, `CitedContextPack`, `ASK_LEDGER_RETRIEVAL_CONFIG_VERSION` |
| `decisions_for_diff.py` | Diff-scoped candidate gating: `from_diff_metadata(...)` entry point, structural scope candidates first, bounded hybrid top-K capped at 30 with `omitted_counts`, candidate-pack hashing for replay | `DecisionsForDiffQuery`, `DecisionsForDiffCandidatePack` |
| `embeddings.py` | Rebuildable vector projection keyed by `(item, model, dimension, epoch)`, HNSW config with versioned `config_version`, recall checks (`VectorRecallSample`, floor 0.95) | `EmbeddingProjectionRow`, `VectorIndexConfig`, `HOSTED_VECTOR_INDEX_CONFIG_VERSION` |
| `lanes.py` | Promotion-lane policy contract (#315): structured / provisional / dropped lanes, per-source-type rules, closed per-lane status-transition set with the recording ledger event per transition, auto-promotion boundary; backfilled-never-auto-promotable encoded as an unrepresentable state | `DEFAULT_LANE_POLICY`, `LaneAssignment`, `validate_status_transition()`, `validate_auto_promotion()` |
| `confidence.py` | Confidence model separate from lifecycle state (#316): advisory-default ladder (`suggest` floor / `advisory` / `confirmed_cited` blocking-eligible), K/W raw-count gate (Wilson bound deferred to #379), monotonic promotion with evidence, loud demotion | `ConfidenceState`, `ConfidenceTier`, `apply_tier_transition()`, `BLOCKING_CONFIRMATION_COUNT_K` |

### 1.3 Load-bearing invariants (enforced today, at the contract layer)

- **Append-only ledger.** `ledger_events`, `source_documents`, and
  `source_spans` carry BEFORE UPDATE/DELETE triggers that raise. Corrections
  append; nothing mutates. (`schema.py`)
- **Fail-closed provenance.** `finding.emitted` requires span hashes + graph
  snapshot hash + model/prompt pair, enforced twice: Python validation in
  `LedgerEvent.__post_init__` and DB CHECKs. `decision_versions` requires
  `cardinality(source_span_hashes) > 0`. An uncited answer is structurally
  unrepresentable: `CitedContextPack` flips to `NO_ANSWER` instead.
- **Fail-closed visibility.** Retrieval cannot run without explicit
  authorized source IDs; deny flags are excluded in shared CTEs before any
  ranking; cited spans must themselves resolve to visible documents
  (`visible_decision_version_exists_sql`).
- **Replay keys everywhere.** `event_hash` is deterministic over the
  immutable payload; idempotency keys are `UNIQUE (tenant_id,
  idempotency_key)` with `ON CONFLICT DO NOTHING`; retrieval packs hash their
  candidate sets; embedding rows carry `item_hash` + epoch.
- **Source-timestamp vs arrival.** Every event stores both `occurred_at`
  (source time, timezone-required) and `ingested_at` (arrival). The *columns*
  for #313's ordering rule exist; the ordering *logic* does not yet (§ 2).
- **One storage boundary.** `storage.py` makes growing a second product
  substrate a raised error, not a code-review argument.

### 1.4 What did NOT exist yet at writing (since closed where noted)

- No Postgres driver, connection policy, or migration runner (→ #472 —
  since shipped; the worker runs live against compass).
- No write-path orchestration: nothing composes a `decision.confirmed` event
  with its `decision_versions` row and `decision_nodes` update in a
  transaction (→ #314).
- No graph-snapshot hash computation; the table and CHECKs exist, the
  algorithm does not (→ #323).
- No derive, no evaluator, no eval harness, no CLI surface over any of this
  (→ Waves 1–9 in the plan).
- Glob scopes never match concrete paths — both structural SQL surfaces join
  on exact `normalized_value` equality (→ #484, with #472 — both since
  shipped).

---

## 2. Backlog reconciliation (the Wave-1 fan-out gate)

Per-issue disposition of the remaining foundation backlog against the
substrate. Closed 2026-06-09 as shipped, needing no disposition: #311, #312,
#317, #321, #324 (and #364–#366, #383 in the gating/read-value family).

| Issue | Disposition | Detail |
|---|---|---|
| #313 source-timestamp ordering | **Still needed as written** | `occurred_at`/`ingested_at` columns shipped; the supersede-ordering logic that prefers source time over webhook arrival is unwritten. Builds on `ledger_events` as-is. |
| #314 immutable-with-supersede write APIs | **Still needed as written** | DB enforces immutability + the deferred `decision_nodes_current_version_fk` for the node↔version cycle; no transactional orchestration exists. This is the write-path composition layer over `ledger_event_insert_sql`. |
| #315 lane policy contract | **Rescoped 2026-06-09 (body current)** | Lifecycle states shipped as the `decision_nodes.status` enum (`candidate/confirmed/rejected/superseded/stale`); #315 owns only the promotion-lane policy on top. |
| #316 confidence model | **Still needed as written** | `decision_nodes.confidence` is a non-empty text column with no vocabulary, transitions, or separation rules. The model is genuinely unbuilt. |
| #318 exact-hash dedup | **Still needed as written** | Delivery-level idempotency shipped (event keys); candidate-level identity (same decision proposed twice from different deliveries) is open. Identity basis must be named in the issue before build (Wave 8). |
| #319 multiple provenance edges | **Needs the noted residual only** | Arrays of span hashes per version + `derived_from` edge type shipped; the write-path behavior when dedup merges candidates (keep every provenance edge) is open. Paired with #318 in Wave 8. |
| #320 deterministic graph rebuild | **Still needed as written** | `projection.rebuilt` event type + `graph_snapshots` registry shipped; the rebuild procedure and its determinism proof (vs #323 hashes, #313 ordering) are open. |
| #322 decision-version stamping | **Rescoped 2026-06-09 (body current)** | Residuals only: first-class decision-version stamp on `finding.emitted` + optional DB NOT NULL hardening. After #370/#376. |
| #323 graph-snapshot hash computation | **Still needed as written** | Table, CHECKs, and required-fields plumbing shipped; the canonical hash algorithm over graph state is unwritten. Until it lands, no trace can carry a *real* snapshot hash. |
| #325 replay acceptance umbrella | **Rescoped 2026-06-09 (body current)** | Byte-for-byte replay acceptance test over the shipped replay-key contract; #336 is the runner. |
| #326 hash(inputs) call-site stamping | **Rescoped 2026-06-09 (body current)** | Ledger fields exist; call sites (derive/evaluator) do not. After #327, alongside #350/#370. |
| #327 prompt/model version registry | **Still needed as written** | Nothing exists; `model_id`/`prompt_version` fields await it. Wave 1 — every model-backed artifact after it depends on the registry's identifiers. |
| #328 banking / selective re-derivation | **Needs rescope at pickup** | The embeddings projection already implements the pattern for one artifact class (`item_hash` + model + epoch ⇒ skip unchanged). The policy issue should generalize from that precedent, not invent fresh. |
| #329 no-silent-failure degradation modes | **Still needed as written** | Strong precedent shipped (four fail-closed error types + `NO_ANSWER` + `omitted_counts`); the *taxonomy document* unifying them and naming reviewer degradation behavior is open. Wave 2; #377 consumes it. |
| #330 budgeted context assembly | **Still needed as written** | Bounded candidate packs with omitted counts shipped; token-budgeted assembly from a pack into evaluator context (with visible omissions) is open. Wave 5; consumes `DecisionsForDiffCandidatePack`. |
| #331 omitted-decision diagnostics | **Still needed as written** | `omitted_counts` + `reason_codes` data shipped in packs and traces; the diagnostic surfacing (replay report lines, thresholds) is open. Wave 7. |

**Gate consequence:** every disposition above is either "build on the named
shipped contract" or "the issue body already says exactly this." No remaining
foundation issue requires substrate changes; none may rebuild substrate. The
fan-out is unblocked.

---

## 3. Forward-looking design (what Stage 0 still has to prove)

### 3.1 `cortex derive` (next artifact: #350)

Tier-1 repo-native extraction (`CLAUDE.md`/`AGENTS.md`, ADRs near-verbatim,
CODEOWNERS, commit messages, PR descriptions/review comments) emitting
`candidate.proposed` **LedgerEvents through the shipped envelope** — same
validation, same idempotency derivation, same hash material as hosted. The
local event store is SQLite **only** under the approved
`local-replay-export` role (`storage.py` enforces this), and is an export
format, not a second product store. Lane assignment (#358 implementing
#315's policy) tags each candidate structured / provisional / dropped, and
dropped chatter is logged with reason codes, never written as graph state.
Backfilled candidates are advisory-only and never auto-promotable (#362 —
non-negotiable).

### 3.2 Soft evaluator (next artifacts: #370 after #330/#363)

Input: a token-budgeted context pack assembled (#330) from
`DecisionsForDiffQuery.from_diff_metadata(...)` candidates, where #363's
diff extractor supplies the `ChangedSurface`. Output: findings emitted as
`finding.emitted` events — which means the evaluator *cannot ship its first
finding* without span hashes, a graph-snapshot hash (#323), and registry
identifiers (#327); the substrate makes uncited findings unrepresentable.
First finding classes: `contradicts-prior-decision` (#371) and
`reverses-superseded-pattern` (#372); `cites-missing-path` (#373) and
`omitted-load-bearing-constraint` (#374) run in shadow. Confidence ladder
(#375) maps #316's model onto advisory-default tiers; #377 enforces #329's
fail-closed taxonomy at the evaluator layer.

### 3.3 Eval harness (next artifacts: #332, then #333/#336)

Frozen fixture format (#332) is the contract everything serializes to:
fixtures carry diffs, decision sets, labels, and expected findings;
recorded-response fixtures (#347) make CI deterministic with zero live
calls; the replay runner (#336) must reproduce findings byte-for-byte from
`(diff, decision-version, graph-snapshot hash, model, prompt-version)` —
the same replay key the ledger already stamps. The CI gate (#338) makes
eval regression a blocking deploy step. The Stage 0 exit artifact is the
#337 report (template #343, self-review #451): ≥70% of emitted advisory
comments correct-and-useful on a hand-graded sample, with
candidate-set/citation/budget/ledger sub-bars.

### 3.4 Sequencing

Stage and wave authority: `cortex_master_plan.md` (canonical 2026-06-09)
via the milestone structure, operationalized in
[.cortex/plans/hosted-decision-reviewer.md](../.cortex/plans/hosted-decision-reviewer.md)
§ Build sequence. Wave 1 is #310 (this document), #332, #344, #327, #363;
#350 follows in Wave 2 building on this brief.

## Module map addendum (2026-06-10 — the Stage 0 build-out)

Everything below landed after this brief's as-built snapshot, in the wave
bundles of 2026-06-09/10. Grouped by family; each follows the substrate's
idioms (frozen dataclasses, fail-closed validation, taxonomy-registered
error types):

- **Model boundary:** `model_interfaces` (derive/evaluate protocols,
  input-hash binding), `model_registry` (self-certifying prompt versions),
  `routing` (route table, claude-CLI + recorded adapters), `cascade`
  (cheap→strong escalation), `cost` (per-call accounting, budgets),
  `banking` (exact-key reuse policy), `recorded_responses` (the one
  recording format).
- **Evaluation:** `evaluator` (the soft core + finding-class evidence
  gates), `advisory_ladder` (tiering; blocking unrepresentable),
  `context_assembly` (token-budgeted, whole-candidate, visible omissions),
  `citation_check`, `candidate_metrics`, `quality_series` (FP vs tone,
  disjoint by construction), `route_comparison`, `replay_runner`
  (byte-deterministic), `eval_fixtures` + `corpus_builder` + the committed
  real-history corpus, `labeling`.
- **Graph:** `graph_writes` (immutable-with-supersede plans; merge =
  supersede pair per #487), `graph_snapshot` (canonical hash),
  `graph_rebuild` (deterministic fold), `event_ordering`
  (source-timestamp supersede order), `candidate_dedup` (provenance
  retained), `lanes` + `confidence` + `lane_assignment` (policy:
  backfilled never auto-promotes).
- **Derive:** `derive_store` (SQLite local-replay-export), `extractors`
  (six deterministic repo-native sources), `question_normalization`.
- **Execution:** `db` (connection policy), `migrations` (applies the
  shipped DDL; live-verified on Railway compass), `push` (local store →
  hosted ledger/projection/snapshot), `degradation` (the failure
  taxonomy + remediation hints).
- **CLI verbs:** `derive`, `candidates list/confirm/reject/triage`,
  `push`, `ask`, `review` — the PE-0 loop
  ([walkthrough](./walkthrough-pe0.md)).
