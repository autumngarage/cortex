# SPEC-to-test traceability matrix

> Per [Doctrine 0003](../.cortex/doctrine/0003-spec-is-the-artifact.md),
> SPEC is the primary deliverable. This matrix is the proof that the
> reference CLI implements the spec — every normative § maps to a test,
> a doctor check, or a documented deferral.
>
> Last updated: 2026-05-02 (against [SPEC.md](../SPEC.md) v0.5.0). This
> matrix is hand-authored from a snapshot read of SPEC + code; an
> automated `cortex doctor --audit-spec` regenerator is parked as a v1.x
> follow-up. Every citation in this file resolves to a real symbol or
> test in the source tree as of HEAD.

## How to read this matrix

| Column | Meaning |
|---|---|
| **SPEC §** | Section of [`SPEC.md`](../SPEC.md) the row covers. |
| **Requirement** | One-line restatement of the normative rule. |
| **Enforcement** | The function in `src/cortex/<file>.py` that detects a violation, or `n/a` if no enforcement exists. |
| **Test** | A test in `tests/<file>.py` that exercises the rule, or `n/a`. |
| **Notes** | Citation when both Enforcement and Test are `n/a`, plus relevant caveats. |

A row with both Enforcement = n/a AND Test = n/a must carry a deferred
citation in Notes (linking to a journal entry or `.cortex/plans/cortex-v1.md`
`## Follow-ups (deferred)` line). Rows flagged **GAP — no parking entry**
are unrecorded gaps that the v1.0 ceremony must either close or formally
defer.

## Matrix

### § 2 Directory layout

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 2 | `.cortex/SPEC_VERSION` MUST be present and declare a supported version. | `check_scaffold` in `src/cortex/validation.py` | `test_missing_spec_version_reports_error`, `test_unsupported_spec_version_reports_error` in `tests/test_doctor.py` | Supported set comes from `SUPPORTED_SPEC_VERSIONS` in `src/cortex/__init__.py`. |
| § 2 | `.cortex/protocol.md` MUST be present (Protocol § 1 fallback contract). | `check_scaffold` in `src/cortex/validation.py` | `test_init_copies_protocol_markdown` in `tests/test_init.py` | Doctor emits ERROR when missing. |
| § 2 | `.cortex/templates/` MUST exist and contain Protocol templates. | `check_scaffold` in `src/cortex/validation.py` | `test_init_copies_full_templates_tree` in `tests/test_init.py`; `test_templates_tree_matches_canonical` in `tests/test_data_sync.py` | Warning if missing or empty. |
| § 2 | Required subdirs MUST exist: `doctrine/`, `plans/`, `journal/`, `procedures/`. | `check_scaffold` in `src/cortex/validation.py` (loops `SCAFFOLD_SUBDIRS`) | `test_init_creates_all_required_subdirs` in `tests/test_init.py` | |
| § 2 | `.cortex/.index.json` absence MUST NOT error; corpus over fallback threshold MUST warn. | `check_cli_less_fallback` in `src/cortex/doctor_checks.py` | `test_cli_less_fallback_threshold_warns` in `tests/test_doctor_invariants.py` | Defaults: 20 Doctrine, 100 Journal. |
| § 2 | `.cortex/.index.json` MUST NOT be hand-edited (parseability + invariants enforced). | `check_promotion_queue` in `src/cortex/doctor_checks.py` | `test_promotion_queue_dangling_source_warns` in `tests/test_doctor_invariants.py`; `test_status_reports_unreadable_index`, `test_status_reports_non_list_promotion_queue`, `test_status_reports_missing_promotion_queue_field` in `tests/test_status.py` | Doctor warns on parse failure, dangling sources, age/promoted_to inconsistencies. |
| § 2 | Tools reading `.cortex/` MUST bail or warn on unknown major SPEC version. | `warn_if_incompatible` / `require_compatible` in `src/cortex/compat.py` | `test_unsupported_spec_version_warns` in `tests/test_manifest.py`; `test_spec_version_guard_warns_on_unsupported`, `test_spec_version_guard_warns_on_missing` in `tests/test_grep.py`; `test_writer_refuses_unsupported_spec_version`, `test_writer_refuses_missing_spec_version` in `tests/test_journal_draft.py` | Readers warn; writers exit 2. |

### § 3 Layer contracts

#### § 3.1 Doctrine

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 3.1 | Doctrine `Status:` MUST be `Proposed`, `Accepted`, or `Superseded-by <n>`. | `_check_doctrine_entry_body` (via `check_doctrine`) in `src/cortex/validation.py` (`DOCTRINE_STATUS_RE`) | `test_invalid_doctrine_status_rejected` in `tests/test_doctor.py` | |
| § 3.1 | Doctrine entries MUST carry `Status`, `Date`, `Load-priority` fields (YAML or bold-inline). | `_check_doctrine_entry_body` in `src/cortex/validation.py` (`DOCTRINE_REQUIRED_FIELDS`) | `test_doctrine_entry_missing_load_priority_reports_error`, `test_doctrine_yaml_frontmatter_accepted`, `test_superseded_doctrine_exempt_from_load_priority` in `tests/test_doctor.py` | Superseded entries exempt from `Load-priority` for immutability. |
| § 3.1 | `Load-priority:` MUST be one of `default`, `always`. | `_check_doctrine_entry_body` in `src/cortex/validation.py` (`DOCTRINE_LOAD_PRIORITY_VALUES`) | `test_doctrine_entry_missing_load_priority_reports_error` in `tests/test_doctor.py` | |
| § 3.1 | Doctrine MUST be immutable; mutation is a warning (only `Status:` flip permitted, for supersede). | `check_immutable_doctrine` in `src/cortex/doctor_checks.py` | `test_doctrine_mutation_detected_but_supersede_status_allowed` in `tests/test_doctor_invariants.py` | Walks `git log --diff-filter=M`. |
| § 3.1 | `Load-priority: always` over-pinning past Doctrine budget MUST be flagged. | n/a | n/a | **GAP — no parking entry.** Doctor today flags missing/invalid `Load-priority`, not budget exhaustion. Manifest computes the budget but does not warn on over-pinning. Proposed disposition: formally-defer-to-v1.x (low impact today; revisit when a project ships >5 `always` entries). |

#### § 3.2 Map

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 3.2 | Map is derived; carries the seven metadata fields per § 4.5 (Generated, Generator, Sources, Corpus, Omitted, Incomplete, Conflicts-preserved). | `check_derived_layer` in `src/cortex/validation.py` (uses `SEVEN_FIELDS`); also `check_generated_layers` in `src/cortex/doctor_checks.py` | `test_init_stubs_map_and_state_with_seven_fields`, `test_init_stub_generator_tracks_current_version` in `tests/test_init.py`; `test_derived_layer_missing_field_reports_error` in `tests/test_doctor.py`; `test_generated_layer_contract_warnings` in `tests/test_doctor_invariants.py` | Stub initialised by `cortex init`. |
| § 3.2 | Map regenerated on structural change. | n/a | n/a | LLM `cortex refresh-map` deferred from v1.0 — see `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)`: "`cortex refresh-map` (LLM synthesis) — was Phase E capstone… Parked per `journal/2026-04-24-production-release-rerank` #1." Stub init only today; no regeneration command on the v1.0 path. |

#### § 3.3 State

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 3.3 | State carries the seven metadata fields per § 4.5. | `check_derived_layer` in `src/cortex/validation.py`; `check_generated_layers` in `src/cortex/doctor_checks.py` | `test_refresh_state_seven_field_header_complete` in `tests/test_refresh_state.py`; `test_generated_layer_contract_warnings` in `tests/test_doctor_invariants.py` | |
| § 3.3 | State is regenerated (deterministic refresh, marker-preserved). | `cortex refresh-state` (entry: `src/cortex/commands/refresh_state.py`); body in `src/cortex/state_render.py` | `test_refresh_state_is_idempotent`, `test_refresh_state_preserves_marker_region_verbatim`, `test_refresh_state_preserves_multiple_marker_pairs`, `test_refresh_state_auto_walked_sections` in `tests/test_refresh_state.py` | |
| § 3.3 | Staleness rule: warn when `Generated` older than threshold (default 24h per spec; CLI default 7d). | `check_generated_layers` in `src/cortex/doctor_checks.py` (`DEFAULT_GENERATED_FRESHNESS_DAYS`) | n/a | Threshold drift between SPEC default (24h) and CLI default (7d) — flagged as a follow-up: revisit alongside `.cortex/config.toml` schema reference doc (v1.0 work item per `.cortex/plans/cortex-v1.md`). |

#### § 3.4 Plans

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 3.4 | Plan frontmatter MUST contain `Status`, `Written`, `Author`, `Goal-hash`, `Updated-by`, `Cites`. | `check_plans` in `src/cortex/validation.py` (uses `PLAN_REQUIRED_FIELDS`) | `test_empty_plan_frontmatter_value_rejected`, `test_plan_missing_cites_rejected`, `test_plan_missing_updated_by_rejected` in `tests/test_doctor.py` | |
| § 3.4 | `Status:` MUST be one of `active`, `shipped`, `cancelled`, `deferred`, `blocked`. | `check_plans` in `src/cortex/validation.py` (`PLAN_STATUS_VALUES`) | `test_valid_plan_is_clean` in `tests/test_doctor.py` (positive coverage) | |
| § 3.4 | `Updated-by:` MUST be a non-empty block-sequence list. | `check_plans` in `src/cortex/validation.py` | `test_plan_missing_updated_by_rejected` in `tests/test_doctor.py` | |
| § 3.4 | Plan MUST contain required H2 sections: `Why (grounding)`, `Success Criteria`, `Approach`, `Work items`. | `check_plans` in `src/cortex/validation.py` (`PLAN_REQUIRED_SECTIONS`, fence-aware via `_collect_h2_headings`) | `test_plan_missing_success_criteria_reports_error`, `test_prose_mention_does_not_satisfy_required_section` in `tests/test_doctor.py` | |
| § 3.4 | Plan MUST have an H1 title (drives `Goal-hash` recomputation). | `check_plans` in `src/cortex/validation.py` (`_extract_h1`, fence-aware) | `test_plan_missing_h1_title_rejected`, `test_fenced_h1_does_not_satisfy_title_check` in `tests/test_doctor.py` | |
| § 3.4 | Shipped/cancelled plans auto-archive after 30 days (visibility only on v1.0 path). | `check_retention_visibility` in `src/cortex/doctor_checks.py` (warning) | `test_retention_visibility_plan_and_warm_journal` in `tests/test_doctor_invariants.py` | Destructive automation deferred — see `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)`: "Retention automation (cleanup, not visibility)… Revisit post-v1.0." |

#### § 3.5 Journal

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 3.5 | Journal MUST be append-only — modifications to existing entries are warnings (only `Updated-by:` diffs allowed). | `check_append_only_journal` in `src/cortex/doctor_checks.py` | `test_append_only_journal_violation_detected`, `test_journal_updated_by_frontmatter_diff_allowed` in `tests/test_doctor_invariants.py` | Walks `git log --diff-filter=M`. |
| § 3.5 | Journal filename MUST match `YYYY-MM-DD-<slug>.md`. | `check_journal` in `src/cortex/validation.py` (`JOURNAL_FILENAME_RE`) | `test_invalid_journal_filename_warns` in `tests/test_doctor.py`; `test_normalize_slug_handles_unicode_and_punctuation`, `test_draft_default_slug_uses_type_and_time` in `tests/test_journal_draft.py` | |
| § 3.5 | Journal entries auto-move to `journal/archive/<year>/` after 365 days (warm-corpus visibility on v1.0 path). | `check_retention_visibility` in `src/cortex/doctor_checks.py` (`DEFAULT_JOURNAL_WARM_MAX`) | `test_retention_visibility_plan_and_warm_journal` in `tests/test_doctor_invariants.py` | Destructive cleanup deferred — same parking citation as § 3.4 row above. |
| § 3.5 | `Type:` enum is one of `decision | incident | migration | reversal | promotion | plan-transition | sentinel-cycle | pr-merged | release | digest`. | Partially via `audit.EXPECTED_TYPE` (T1.1/1.5/1.8 → decision; T1.9 → pr-merged; T1.10 → release) | `test_audit_matches_when_journal_has_matching_type`, `test_t1_10_decision_journal_does_not_satisfy_release_fire` in `tests/test_audit.py` | No standalone whole-enum validator; the audit cross-checks `Type:` only for triggers it understands. T1.2 (incident), T1.6 (sentinel-cycle), T1.7 (doctrine candidate) audits are deferred — see `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)`: "T1.2 / T1.6 / T1.7 audits stay deferred per `journal/2026-04-24-production-release-rerank` #8." |

#### § 3.6 Procedures

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 3.6 | Procedures live in `procedures/` with archive subdir. | `check_scaffold` in `src/cortex/validation.py` (`SCAFFOLD_SUBDIRS` includes `procedures`) | `test_init_creates_all_required_subdirs` in `tests/test_init.py` | |
| § 3.6 | Procedures `Doc version:` / `Last change:` / `Spec:` header convention. | n/a | n/a | **GAP — no parking entry.** No procedures-specific frontmatter validator exists. Proposed disposition: formally-defer-to-v1.x (no procedures shipped in this repo or dogfood targets yet — validator without consumers is premature). |

### § 4 Cross-layer rules

#### § 4.1 Plans cite their grounding

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.1 | Plan `Why (grounding)` MUST link to a Doctrine, State, or Journal entry. | `check_plans` in `src/cortex/validation.py` (`PLAN_GROUNDING_LINK_RE`) | `test_plan_without_grounding_link_warns` in `tests/test_doctor.py` | |

#### § 4.2 Deferrals tracked end-to-end

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.2 | Items in active Plans' `## Follow-ups (deferred)` MUST cite `plans/<slug>`, `journal/<date>-<slug>`, or `doctrine/<nnnn>-<slug>`. | `check_plans` in `src/cortex/validation.py` (`PLAN_FOLLOWUP_CITATION_RE`, `_resolves_to_existing_layer_entry`) | Whole-file coverage in `tests/test_doctor_orphan_deferrals.py` (19 tests including `test_orphan_warns_on_uncited_bullet`, `test_orphan_clean_when_bullet_cites_existing_journal`, `test_orphan_clean_when_bullet_cites_existing_plan`, `test_orphan_clean_when_bullet_cites_existing_doctrine`, `test_orphan_warns_on_self_citation`, `test_orphan_warns_on_malformed_journal_citation`, `test_orphan_warns_on_malformed_doctrine_citation`) | Skips shipped/cancelled plans by design. |

#### § 4.3 Success criteria measurable

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.3 | Plan `Success Criteria` MUST name a concrete signal (numeric, link, code/path ref, or `PR #N`). | `_success_criteria_is_measurable` (called from `check_plans`) in `src/cortex/validation.py` (`_MEASURABLE_SIGNAL_RE`) | `test_prose_only_success_criteria_rejected`, `test_fenced_success_criteria_does_not_satisfy_empty_check` in `tests/test_doctor.py` | |

#### § 4.4 Promotion link traversal

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.4 | New entry MUST carry `Promoted-from:` (canonical link). | `_read_promoted_from`, `_normalize_promoted_from` and the round-trip writer in `src/cortex/doctrine.py`; the writer in `src/cortex/commands/promote.py` | `test_promote_round_trip_writes_doctrine_index_and_journal`, `test_promote_canonicalizes_source_ref_for_promoted_from_link` in `tests/test_promote.py` | |
| § 4.4 | Append-only Journal sources MUST NOT be retrofitted with `Promoted-to:`; reverse traversal is derived from `.index.json`. | `check_append_only_journal` in `src/cortex/doctor_checks.py` (would warn on Journal mutation); `_promoted_to_by_source` in `src/cortex/index.py` (derived reverse map) | `test_append_only_journal_violation_detected`, `test_journal_updated_by_frontmatter_diff_allowed` in `tests/test_doctor_invariants.py` | The "`Promoted-to:` MUST NOT appear on Journal/Doctrine sources" half is enforced by the immutability checks above; no dedicated linter rejects the field name on those layers. |
| § 4.4 | `Promoted-to:` MAY appear on Plan / Procedure (mutable) sources. | n/a (permissive; not a constraint to enforce) | n/a | Spec is permissive ("MAY"); no rule to test. |

#### § 4.5 / § 4.5.1 Generated-layer provenance

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.5 | Generated layers (`map.md`, `state.md`, digests) MUST declare seven fields; missing fields fail `cortex doctor`. | `check_derived_layer` in `src/cortex/validation.py` (`SEVEN_FIELDS`); `check_generated_layers` in `src/cortex/doctor_checks.py` (also walks digests) | `test_derived_layer_missing_field_reports_error` in `tests/test_doctor.py`; `test_generated_layer_contract_warnings`, `test_generated_layers_scans_digest_journal_entries_per_spec_5_2` in `tests/test_doctor_invariants.py` | |
| § 4.5.1 | `Verified: <ISO-8601 date>` end-of-bullet markers parsed; staleness surfaced with inline warning past threshold (default 90d). | `parse_verified`, `bullet_age_days`, `format_warning` in `src/cortex/verified.py`; rendered by `_annotate_verified_tags` in `src/cortex/manifest.py`; threshold via `_verified_threshold_days` | `test_parse_verified_well_formed`, `test_parse_verified_full_timestamp`, `test_parse_verified_no_marker`, `test_parse_verified_multi_line_bullet`, `test_bullet_age_days`, `test_manifest_fresh_verified_bullet_renders_unchanged`, `test_manifest_stale_doctrine_bullet_gets_inline_warning`, `test_manifest_verified_threshold_respected_from_config` in `tests/test_verified.py` | |

#### § 4.6 Typed links

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.6 | Use named relations (`supersedes`, `superseded-by`, `promoted-from`, …) over raw markdown links. | n/a | n/a | Permissive — "raw markdown links are allowed but discouraged." No machine-checkable rule. |
| § 4.6 | `superseded-by` (correct spelling, not `supersded-by`) is the form enforced from v0.3.1-dev onward. | `_check_doctrine_entry_body` via `DOCTRINE_STATUS_RE = ^(Proposed|Accepted|Superseded-by\s+\d+)\s*$` in `src/cortex/validation.py` | `test_invalid_doctrine_status_rejected`, `test_superseded_doctrine_exempt_from_load_priority` in `tests/test_doctor.py` | The regex anchors the canonical spelling — typo fails the Status check. |
| § 4.6 | `promoted-to` MUST NOT appear on Journal (append-only) or Doctrine (immutable) sources. | Indirect: `check_append_only_journal` / `check_immutable_doctrine` in `src/cortex/doctor_checks.py` block the mutation that would add the field. | `test_append_only_journal_violation_detected`, `test_doctrine_mutation_detected_but_supersede_status_allowed` in `tests/test_doctor_invariants.py` | No dedicated typed-link linter today; immutability checks catch the symptom (any added field). |

#### § 4.7 Promotion queue operational rules

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.7 | Promotion queue is persisted in `.index.json` and surfaced at every invocation. | `read_index` in `src/cortex/index.py`; `cortex status` reads queue depth (`src/cortex/status.py`) | `test_status_reads_promotion_queue` in `tests/test_status.py`; `test_promotion_summary_with_index`, `test_promotion_summary_without_index` in `tests/test_manifest.py` | |
| § 4.7 | Each candidate has a state (`proposed | approved | not-yet | duplicate-of | skip-forever | needs-more-evidence`). | n/a | n/a | **GAP — no parking entry.** Index reader/writer treats `state` as a string but does not validate the enum, and no candidate-state transition logic ships on the v1.0 path. Proposed disposition: formally-defer-to-v1.x — interactive per-candidate prompt UX is itself deferred (see `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)` #6, also `README.md` line 92), and validating the enum without a writer that exercises the transitions is premature. |
| § 4.7 | WIP limit (default 10) MUST throttle new candidate generation. | n/a | n/a | **GAP — no parking entry.** Same disposition as candidate-state row above; the writer that would honor the limit is the deferred per-candidate-prompt UX. |
| § 4.7 | Candidate aging: `proposed > 14d` transitions to `stale-proposed` and surfaces in `cortex doctor`. | Partial — `check_promotion_queue` in `src/cortex/doctor_checks.py` validates `age_days`/`last_touched` consistency but does not transition state or warn on age threshold. | `test_promotion_queue_dangling_source_warns` in `tests/test_doctor_invariants.py` (queue-shape coverage; not the 14d transition) | Same disposition as the WIP-limit row; revisit when the per-candidate UX ships. |
| § 4.7 | Promotion debt (`<n> proposed, <k> stale`) MUST appear in the agent manifest. | `_promotion_summary` in `src/cortex/manifest.py` | `test_promotion_summary_with_index`, `test_promotion_summary_without_index`, `test_promotion_summary_with_non_object_index_is_visible`, `test_promotion_summary_with_non_object_queue_items_is_visible` in `tests/test_manifest.py` | "Stale" count exposed as `<k>` only once aging transition lands; today the manifest reports total/proposed only. |
| § 4.7 | `review-complexity: trivial | editorial` split. | n/a | n/a | Same disposition as candidate-state row — deferred with the interactive UX. |

#### § 4.8 Single authority rule for reads

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.8 | Root agent files (`AGENTS.md`, `CLAUDE.md`) MUST NOT duplicate Cortex claims without `grounds-in:` citation. | n/a (single-authority drift detection) | n/a | Council de-scoped. See `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)`: "Doctor checks de-scoped from Tier 4 (council de-scope — Gemini): single-authority-rule drift (§ 4.8)… Captured in `journal/2026-04-28-codesight-cross-pollination-and-council-review`." |
| § 4.8 (adjacent) | Unscoped LLM/API/provider constraints in `CLAUDE.md`/`AGENTS.md` MUST carry an `(applies to: runtime|toolchain|both)` qualifier. | `check_claude_agents` in `src/cortex/validation.py` (`CONSTRAINT_KEYWORD_RE`, `LLM_KEYWORD_RE`, `SCOPE_QUALIFIER_RE`, fence-aware via `_strip_frontmatter_and_fences`) | `test_claude_md_unscoped_llm_constraint_warns`, `test_claude_md_scoped_llm_constraint_clean`, `test_agents_md_independently_checked`, `test_constraint_in_code_fence_ignored`, `test_unrelated_imperative_not_flagged`, `test_no_claude_or_agents_md_no_warning`, `test_plural_llm_keywords_are_flagged` in `tests/test_doctor.py` | Adjacent guard for autumngarage cycle-4 finding F2 (sentinel applying CLAUDE.md globally). Not the spec's single-authority rule, but the only § 4.8-adjacent enforcement live today. |
| § 4.8 (adjacent) | Doctrine 0007 canonical-ownership: root `ROADMAP.md|STATUS.md|PLAN.md|NEXT.md|TODO.md` MUST NOT duplicate `.cortex/state.md` / active plans. | `check_canonical_ownership` in `src/cortex/doctor_checks.py` | `test_canonical_ownership_warning_and_overrides`, `test_canonical_ownership_runs_on_plain_doctor` in `tests/test_doctor_invariants.py` | Not the SPEC § 4.8 rule itself but the same family — Doctrine 0007 ratifies one specific canonical-ownership case after the 2026-05-02 dogfood incident. |

#### § 4.9 Multi-writer Plan visibility

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.9 | `Goal-hash:` MUST be recomputable from H1 title via the documented normalization (lowercase → strip non-`[a-z0-9 ]` → collapse whitespace → sha256 first 8 hex). | `normalize_goal_hash` in `src/cortex/goal_hash.py`; verified in `check_plans` (`src/cortex/validation.py`) | `test_spec_example_matches`, `test_case_insensitive`, `test_whitespace_collapsed`, `test_diacritics_stripped`, `test_punctuation_dropped_without_inserting_space` in `tests/test_goal_hash.py`; `test_goal_hash_mismatch_reports_error` in `tests/test_doctor.py`; `test_spawn_writes_plan_with_computed_goal_hash` in `tests/test_plan_spawn.py` | |
| § 4.9 | Two Plans sharing a `Goal-hash:` MUST surface as a collision warning. | `check_plans` in `src/cortex/validation.py` (collision pass at end) | `test_goal_hash_collision_warns` in `tests/test_doctor.py` | |

### § 5 Retention and consolidation

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 5.1 | Doctrine never archived; Journal hot/warm/cold tiers per age; Plans `shipped`/`cancelled` move after 30d; Map/State always regenerated; Procedures `cortex doctor` flags dead refs. | Visibility only via `check_retention_visibility` in `src/cortex/doctor_checks.py` (Plan + warm-Journal warnings) | `test_retention_visibility_plan_and_warm_journal` in `tests/test_doctor_invariants.py` | Destructive cleanup deferred — see `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)`: "Retention automation (cleanup, not visibility) — visibility ships in v0.6.0… actual destructive cleanup… defers… Revisit post-v1.0." Procedure dead-ref check itself is not implemented; same parking citation. |
| § 5.2 | Monthly digest cadence; overdue surfaces in `cortex` interactive flow. | `cortex status` digest staleness check (`src/cortex/status.py`) | `test_status_digest_overdue_flag`, `test_status_digest_fresh` in `tests/test_status.py` | Proposal-and-approval interactive flow deferred — same parking citation as § 5.1. |
| § 5.3 | Digest depth cap one level deep; quarterly digests MUST also cite ≥5 raw Journal entries. | n/a | n/a | **GAP — no parking entry.** No depth-traversal check exists; quarterly ≥5-raw rule unenforced. Proposed disposition: formally-defer-to-v1.x (no monthly/quarterly digests have been written in this dogfood corpus yet — validator is theoretical until digests exist). |
| § 5.4 | `cortex doctor --audit-digests` samples claims and verifies each traces back to a source entry. | `audit_digests` in `src/cortex/audit.py`; `--audit-digests` flag in `src/cortex/commands/doctor.py` | `test_audit_digests_ignores_frontmatter_lists`, `test_audit_digests_flags_missing_citations`, `test_audit_digests_clean_when_citations_present` in `tests/test_audit.py`; `test_cli_audit_digests_flag` in `tests/test_audit.py` | First-slice implementation; full claim-trace expansion deferred — see `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)`: "Doctor checks de-scoped from Tier 4 (council de-scope — Gemini)… full § 5.4 claim-trace audit beyond the first slice." |
| § 5.5 | Failure modes the spec prevents (unbounded hot load, unreviewed candidates, silent digest drift, consolidation skipped). | Composed from §§ 4.5, 4.7, 5.4 above; no separate enforcement. | n/a | Narrative summary section; covered transitively by the rows above. |

### § 6 File format conventions

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 6 | YAML frontmatter or bold-inline scalars are accepted by parsers (Doctrine, Journal, Procedures). | `parse_frontmatter` in `src/cortex/frontmatter.py`; `_read_doctrine_field` in `src/cortex/validation.py` (YAML-then-bold-inline fallback) | `test_scalar_values`, `test_block_sequence`, `test_flow_sequence`, `test_value_with_colon_preserved`, `test_no_frontmatter_returns_empty`, `test_unterminated_frontmatter_returns_empty`, `test_quoted_values_stripped` in `tests/test_frontmatter.py`; `test_doctrine_yaml_frontmatter_accepted` in `tests/test_doctor.py` | |
| § 6 | ISO8601 dates only. | Implicit via `date.fromisoformat` in `src/cortex/doctor_checks.py` `_parse_date` / `src/cortex/verified.py` (`VERIFIED_RE` requires `YYYY-MM-DD`) | `test_parse_verified_well_formed`, `test_parse_verified_full_timestamp` in `tests/test_verified.py` | No standalone date-format linter; non-ISO values silently fail to parse where they appear. |
| § 6 | Single H1 title, H2 sections; deeper nesting is a "smell" (advisory). | `_extract_h1` (fence-aware) and `_collect_h2_headings` in `src/cortex/validation.py` enforce H1-presence and H2 section names; deeper-nesting smell is unchecked. | `test_plan_missing_h1_title_rejected`, `test_fenced_h1_does_not_satisfy_title_check`, `test_plan_missing_success_criteria_reports_error` in `tests/test_doctor.py` | Advisory aspect (deeper nesting) intentionally unchecked. |
| § 6 | Checkbox syntax `- [ ]` / `- [x]` for work items. | `cortex plan status` parser (`src/cortex/plans.py`) | `test_zero_work_items_reports_zero_completion_and_not_stale`, `test_all_complete_reports_100_and_not_stale`, `test_mixed_items_count_in_progress_as_half_done`, `test_in_progress_items_cannot_push_completion_over_100` in `tests/test_plan_status.py` | |
| § 6 | Blockquote summary at top (one to three sentences). | n/a | n/a | **GAP — no parking entry.** No structural validator. Proposed disposition: ship-fix-in-v1.0 if a small AST check is cheap; otherwise formally-defer-to-v1.x (low value relative to other v1.0 work). |

### § 7 Versioning

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 7 | Tools MUST declare which spec major versions they support. | `SUPPORTED_SPEC_VERSIONS` in `src/cortex/__init__.py` | `test_version_prints_supported_spec_versions`, `test_version_prints_supported_protocol_versions` in `tests/test_version.py` | |
| § 7 | Readers encountering an unknown major SPEC version SHOULD warn. | `warn_if_incompatible` in `src/cortex/compat.py` | `test_unsupported_spec_version_warns`, `test_missing_spec_version_warns` in `tests/test_manifest.py`; `test_spec_version_guard_warns_on_unsupported`, `test_spec_version_guard_warns_on_missing` in `tests/test_grep.py` | |
| § 7 | Writers encountering an unknown major SPEC version SHOULD refuse. | `require_compatible` in `src/cortex/compat.py` | `test_writer_refuses_missing_spec_version`, `test_writer_refuses_unsupported_spec_version` in `tests/test_journal_draft.py`; `test_spawn_refuses_missing_spec_version`, `test_spawn_refuses_unsupported_spec_version` in `tests/test_plan_spawn.py` | |

### § 8 Relationship to the Cortex Protocol

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 8 | `cortex doctor` validates BOTH SPEC.md compliance AND Protocol compliance (Tier 1 trigger fires produced entries). | Plain checks via `run_plain_checks` + `run_all_checks` in `src/cortex/doctor_checks.py` / `src/cortex/validation.py`; Protocol-fire checks via `audit` in `src/cortex/audit.py` (called from `--audit` flag in `src/cortex/commands/doctor.py`) | `test_fresh_scaffold_is_clean` and the rest of `tests/test_doctor.py`; `test_audit_warns_when_no_matching_journal`, `test_journal_entry_satisfies_at_most_one_fire`, `test_human_authored_entry_without_trigger_still_matches`, `test_cli_audit_flag_runs_without_error`, `test_cli_audit_warns_not_crashes_on_non_git_project` in `tests/test_audit.py` | |
| § 8 (Protocol T1.1) | Diff touches `.cortex/doctrine/`, `.cortex/plans/`, `principles/`, or `SPEC.md` MUST produce a `decision` Journal entry. | `Trigger.T1_1` + `classify` in `src/cortex/audit.py` (uses `T1_1_PATH_PREFIXES`, `T1_1_EXACT_PATHS`) | `test_t1_1_fires_on_doctrine_touch`, `test_audit_matches_when_journal_has_matching_type` in `tests/test_audit.py` | |
| § 8 (Protocol T1.5) | Dependency manifest changes (`pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `Gemfile`) MUST produce a `decision` Journal entry. | `Trigger.T1_5` + `classify` in `src/cortex/audit.py` | `test_t1_5_fires_on_dep_manifest_change` in `tests/test_audit.py` | |
| § 8 (Protocol T1.8) | Commit-message patterns (`fix: … regression`, `refactor: … (removes|introduces)`, `feat: … (breaking|replaces)`) MUST produce a `decision` Journal entry. | `Trigger.T1_8` + `classify` in `src/cortex/audit.py` | `test_t1_8_fires_on_regression_fix` in `tests/test_audit.py` | |
| § 8 (Protocol T1.9) | PR merged to default branch MUST produce a `pr-merged` Journal entry. | `Trigger.T1_9` + first-parent walk in `src/cortex/audit.py` | `test_t1_9_does_not_fire_on_feature_branch_commits`, `test_merge_commit_fan_out_uses_first_parent` in `tests/test_audit.py` | |
| § 8 (Protocol T1.10) | Tagged release MUST produce a `release` Journal entry within 72h whose `Tag:` field equals the tag name. | `Trigger.T1_10` + `load_tags` + `_best_matching_entry` in `src/cortex/audit.py` (uses `DEFAULT_TAG_PATTERN`, `JOURNAL_MATCH_WINDOW_HOURS`) | `test_t1_10_load_tags_filters_by_pattern`, `test_t1_10_fires_per_release_tag`, `test_t1_10_matches_release_journal_entry_within_window`, `test_t1_10_unmatched_when_no_release_journal`, `test_t1_10_journal_must_name_the_tag`, `test_t1_10_release_entry_without_tag_field_does_not_match`, `test_t1_10_decision_journal_does_not_satisfy_release_fire` in `tests/test_audit.py` | |
| § 8 (Protocol T1.4) | File deletion exceeding N lines (default 100) MUST produce a `decision` Journal entry within 72h. | `check_t1_4_deletions` in `src/cortex/doctor_checks.py` (`DEFAULT_DELETION_LINE_THRESHOLD`, `_protocol_deletion_threshold`) | `test_t1_4_large_deletion_without_journal_warns` in `tests/test_doctor_invariants.py` | |
| § 8 (Protocol T1.2) | Test command failed mid-session MUST produce an `incident` Journal entry. | n/a | n/a | Deferred. See `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)`: "T1.2 / T1.6 / T1.7 audits stay deferred per `journal/2026-04-24-production-release-rerank` #8." |
| § 8 (Protocol T1.6) | Sentinel cycle ended (`.sentinel/runs/<timestamp>.md` written) MUST produce a `sentinel-cycle` Journal entry. | n/a | n/a | Same parking citation as T1.2 above. |
| § 8 (Protocol T1.7) | Touchstone pre-merge on architecturally significant diff MUST produce a `doctrine/candidate.md` draft. | n/a | n/a | Same parking citation as T1.2 above. Triad-mode infra is itself deferred from v1.0 (README.md line 133). |

### § 8 (cont.) `cortex doctor --audit-instructions`

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 8 (trust layer) | External claims in instruction files (Homebrew tap, sibling repos, GitHub releases, URLs) MUST be auditable; staleness surfaces as warnings. | `audit_instructions`, `scan_instruction_files`, `audit_filesystem_siblings`, `audit_homebrew_tap`, `audit_github_releases` in `src/cortex/audit_instructions.py`; `--audit-instructions` flag in `src/cortex/commands/doctor.py`; config schema in `check_config_toml_schema` (`src/cortex/doctor_checks.py`) | `test_clean_project_no_findings_prints_summary`, `test_stale_homebrew_formula_version_reports_source_line`, `test_missing_sibling_reports_reference_line`, `test_discovery_mode_audits_discovered_sibling`, `test_url_404_warns`, `test_strict_exits_1_on_warning`, `test_json_shape_parses`, `test_always_prints_summary_for_zero_claims`, `test_network_timeout_warns_without_crashing`, `test_brew_and_gh_absent_degrade_gracefully` in `tests/test_audit_instructions.py`; `test_config_toml_schema_type_and_unknown_key` in `tests/test_doctor_invariants.py` | Cross-the-fourth-wall claim audit shipped at v0.5.0 per `.cortex/state.md` Tier 3. |

### § 9 What Cortex explicitly does not do

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 9 | "Does not store vectors inside the canonical layer" — derived index (if any) MUST live in gitignored `.cortex/.index/`. | `cortex retrieve` index path computation (`src/cortex/retrieve/`); `.gitignore` shipped by `cortex init`. | `test_cortex_cache_dir_controls_index_path` and the broader corpus in `tests/test_retrieve_bm25.py` | The other § 9 boundaries (does not execute work, does not enforce standards, does not replace git, does not synthesize without permission, does not aggregate cross-project, does not host in cloud) are negative scope statements — proven by absence of code rather than presence of a check. |

## Gaps surfaced

Rows flagged **GAP — no parking entry** above, in priority order:

- **§ 6 blockquote-summary structural validator** — small AST check; proposed ship-fix-in-v1.0 if cheap, else formally-defer-to-v1.x with a brief journal entry capturing the decision.
- **§ 4.7 promotion-queue candidate-state enum, WIP limit, review-complexity split** — three rows; all converge on the deferred per-candidate interactive UX (README.md line 92, `.cortex/plans/cortex-v1.md` `## Follow-ups (deferred)` #6). Proposed disposition: bundle a single new journal entry `.cortex/journal/2026-05-XX-promotion-queue-spec-rules-deferred.md` that names the three sub-rules and re-points to the existing UX deferral. That converts three GAPs to "deferred with citation" without ceremony.
- **§ 5.3 digest depth cap + quarterly ≥5-raw rule** — formally-defer-to-v1.x: no digests in the corpus today; validator without consumers is premature. Same proposed remedy as the § 4.7 rows: one new journal entry pinning the deferral.
- **§ 3.6 Procedures `Doc version:`/`Last change:`/`Spec:` validator** — formally-defer-to-v1.x: no procedures shipped yet on this repo or dogfood targets; same one-journal-entry remedy.
- **§ 3.1 `Load-priority: always` over-pinning warning** — formally-defer-to-v1.x: no project today ships >2 `always` entries. Revisit when a dogfood target hits ≥5.

None of the gaps justify holding v1.0; all are "ship a 1-line journal entry to convert GAP → deferred" before the v1.0 ceremony.
