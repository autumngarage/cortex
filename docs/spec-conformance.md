# SPEC-to-test traceability matrix

> Per [Doctrine 0003](../.cortex/doctrine/0003-spec-is-the-artifact.md),
> SPEC is the primary deliverable. This matrix is the proof that the
> reference CLI implements the spec — every normative § maps to a test,
> a doctor check, or a documented deferral.
>
> Last updated: 2026-05-03. Regenerated whenever SPEC.md changes;
> drift surfaces in `cortex doctor --audit-spec` (planned for v1.x).

## How to read this matrix

| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § N.M | one-line restatement of the requirement | `check_<name>` in `src/cortex/<file>:<line>` OR n/a | `test_<name>` in `tests/<file>` OR n/a | citation if deferred |

A row with both Enforcement = n/a AND Test = n/a is a gap — must have a
"deferred to vN.x" note in Notes.

## Matrix

### § 2 Directory layout
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 2 | `SPEC_VERSION` must be present. | `structural_validation` in `src/cortex/validation.py` | `test_init_spec_version.py` | |
| § 2 | `protocol.md` must be present. | `structural_validation` in `src/cortex/validation.py` | n/a | Assumed present by `AGENTS.md` import. |
| § 2 | `templates/` directory structure. | `structural_validation` in `src/cortex/validation.py` | n/a | Checked by convention, not enforced by `doctor`. |
| § 2 | `.index.json` absence is not an error. | `check_cli_less_fallback` in `src/cortex/doctor_checks.py` | n/a | `doctor` warns if corpus size exceeds threshold without an index. |
| § 2 | Hand-editing `.index.json` is a violation. | `check_promotion_queue` in `src/cortex/doctor_checks.py` | `test_refresh_index.py` | `doctor` warns if it can't parse it or finds inconsistencies. |

### § 3 Layer contracts
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 3.1 | Doctrine: Immutable-with-supersede. | `check_immutable_doctrine` in `src/cortex/doctor_checks.py` | n/a | Enforced by git history; doctor check warns on modification. |
| § 3.1 | Doctrine: Status must be one of Proposed, Accepted, Superseded-by. | `structural_validation` in `src/cortex/validation.py` | `test_doctor_invariants.py` | |
| § 3.1 | Doctrine: `Load-priority: always` over-pinning is flagged. | `structural_validation` in `src/cortex/validation.py` | n/a | |
| § 3.2 | Map: Is derived and regenerated. | n/a | n/a | Deferred to v1.x per `plans/cortex-v1.md`. See `journal/2026-04-24-production-release-rerank.md`. |
| § 3.2 | Map: Header must contain seven metadata fields. | `check_generated_layers` in `src/cortex/doctor_checks.py` | `test_doctor_invariants.py` | Applies to Map and State. |
| § 3.3 | State: Is derived and regenerated. | n/a | `test_refresh_state.py` | A property of the `refresh-state` command. |
| § 3.3 | State: Header must contain seven metadata fields. | `check_generated_layers` in `src/cortex/doctor_checks.py` | `test_doctor_invariants.py` | |
| § 3.3 | State: Staleness rule. | `check_generated_layers` in `src/cortex/doctor_checks.py` | `test_doctor_invariants.py` | |
| § 3.4 | Plans: `Status` frontmatter is required. | `structural_validation` in `src/cortex/validation.py` | `test_plan_status.py` | |
| § 3.4 | Plans: `Goal-hash` frontmatter is required and verified. | `structural_validation` in `src/cortex/validation.py` | `test_goal_hash.py` | |
| § 3.5 | Journal: Is append-only. | `check_append_only_journal` in `src/cortex/doctor_checks.py` | n/a | Enforced by git; doctor check warns on modification. |
| § 3.5 | Journal: Filename format is `YYYY-MM-DD-<slug>.md`. | `structural_validation` in `src/cortex/validation.py` | `test_journal_draft.py` | |
| § 3.6 | Procedures: Are mutable. | n/a | n/a | By convention. |

### § 4 Cross-layer rules
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 4.1 | Plans must cite grounding. | `structural_validation` in `src/cortex/validation.py` | `test_doctor_invariants.py` | |
| § 4.2 | Deferrals must be tracked. | `structural_validation` in `src/cortex/validation.py` | `test_doctor_orphan_deferrals.py` | |
| § 4.3 | Success criteria must be measurable. | `structural_validation` in `src/cortex/validation.py` | `test_doctor_invariants.py` | |
| § 4.4 | Promotion links (`Promoted-from`) are canonical and traversable. | `structural_validation` in `src/cortex/validation.py` | `test_promote.py` | |
| § 4.4 | `Promoted-to` only on mutable layers. | `structural_validation` in `src/cortex/validation.py` | `test_doctor_invariants.py` | |
| § 4.5 | Generated layers must have 7 metadata fields. | `check_generated_layers` in `src/cortex/doctor_checks.py` | `test_doctor_invariants.py` | Same as § 3.2, 3.3. |
| § 4.5.1 | `Verified:` tags are parsed and checked for staleness. | `audit_verified_tags` in `src/cortex/audit_instructions.py` | `test_verified.py` | |
| § 4.6 | Typed links are used over free links. | `structural_validation` in `src/cortex/validation.py` | n/a | |
| § 4.6 | `superseded-by` (not `supersded-by`) is enforced. | `structural_validation` in `src/cortex/validation.py` | `test_doctor_invariants.py` | |
| § 4.7 | Promotion queue WIP limit is enforced. | n/a | n/a | Enforced by `cortex` CLI logic, not a `doctor` check. |
| § 4.7 | Promotion queue candidates can be stale. | `check_promotion_queue` in `src/cortex/doctor_checks.py` | `test_refresh_index.py` | |
| § 4.8 | Single authority rule for reads (no drift from `.cortex/`). | n/a | n/a | **GAP** — Deferred to v1.x per `plans/cortex-v1.md`. See `journal/2026-04-28-codesight-cross-pollination-and-council-review.md`. |
| § 4.9 | Multi-writer Plan collisions are visible via `Goal-hash`. | `structural_validation` in `src/cortex/validation.py` | `test_goal_hash.py` | |

### § 5 Retention and consolidation
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 5.1 | Journal entries are archived after 365 days. | `check_retention_visibility` in `src/cortex/doctor_checks.py` | n/a | Doctor warns, but CLI does not yet auto-archive. |
| § 5.1 | Shipped/cancelled Plans are archived after 30 days. | `check_retention_visibility` in `src/cortex/doctor_checks.py` | n/a | Doctor warns, but CLI does not yet auto-archive. |
| § 5.2 | Monthly digests are proposed. | n/a | n/a | Handled by `cortex` CLI logic, not a static check. Deferred to v1.x. |
| § 5.3 | Digest depth cap is one level. | n/a | n/a | **GAP** — No check exists yet. Proposed disposition: ship-fix-in-v1.0. |
| § 5.4 | Digest audit sampling can be run. | `audit_digests` in `src/cortex/audit.py` | `test_audit.py` | Implemented as `cortex doctor --audit-digests`. |

### § 6 File format conventions
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 6 | Blockquote summary at top. | `structural_validation` in `src/cortex/validation.py` | n/a | |
| § 6 | ISO8601 dates. | Parsers for date fields enforce this. | `test_frontmatter.py` | |
| § 6 | Markdown, single H1, H2 sections. | By convention, parsers might fail otherwise. | n/a | |
| § 6 | Checkbox syntax for work items. | Parsed by `cortex plan --status`. | `test_plan_status.py` | |
| § 6 | YAML frontmatter or bold-inline scalars are accepted. | `frontmatter.py` | `test_frontmatter.py` | |

### § 7 Versioning
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 7 | Tools must check `SPEC_VERSION`. | `structural_validation` in `src/cortex/validation.py` | `test_init_spec_version.py` | |
| § 7 | Readers warn on unknown major version. | `cortex.compat` module | n/a | `warn_on_unsupported_spec_version` |

### § 8 Relationship to the Cortex Protocol
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 8 | `doctor` validates both SPEC and Protocol. | `cortex doctor` command | `test_doctor.py` | This is the purpose of the `doctor` command. |

### § 9 What Cortex explicitly does not do
| SPEC § | Requirement | Enforcement | Test | Notes |
|---|---|---|---|---|
| § 9 | Cortex does not execute work, enforce standards, replace git, etc. | n/a | n/a | These are design principles, not checkable invariants. |

## Gaps surfaced

- **§ 4.8 Single authority rule for reads:** No `doctor` check exists to detect drift between root agent files (`AGENTS.md`, etc.) and Cortex content. This is deferred to v1.x per `plans/cortex-v1.md`.
- **§ 5.3 Digest depth cap:** No `doctor` check exists to enforce the one-level-deep rule for digests. Proposed disposition: ship-fix-in-v1.0. This is a straightforward graph traversal on the index and can be implemented before v1.0.
