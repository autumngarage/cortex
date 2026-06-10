---
Status: superseded
Written: 2026-05-09
Author: human
Goal-hash: 5372e371
Superseded-by: plans/hosted-decision-reviewer.md
Updated-by:
  - 2026-05-09T20:55 codex (created from context-build-system vision)
  - 2026-05-09T21:27 codex (shipped #244 token-budget instrumentation slice)
  - 2026-06-09T00:00 codex (Status: active → superseded by plans/hosted-decision-reviewer.md; retained as context-integrity history)
Cites: doctrine/0008-context-integrity-build-system, journal/2026-05-09-context-build-system-vision, state.md § Current work
---

# Build production context integrity

> **Superseded 2026-06-09:** the active product sequence moved to
> [`plans/hosted-decision-reviewer.md`](./hosted-decision-reviewer.md), which is
> guided by the Obsidian Cortex master plan. This file remains useful context
> for the CLI/context-integrity substrate, but it is no longer the active master
> plan.

> **Cortex becomes production-ready when it can build, budget, and verify agent context across real projects, and fail visibly when that context is stale, incomplete, uncited, or too expensive.**

## Why (grounding)

Grounded in [Doctrine 0008](../doctrine/0008-context-integrity-build-system.md), which sharpens Cortex from generic "project memory" into a context integrity product. The triggering session is [journal/2026-05-09-context-build-system-vision](../journal/2026-05-09-context-build-system-vision.md): after reviewing Autumn Garage tools and competitive prior art, the product line that matters is not more memory volume, but bounded, cited, verifiable context for agents.

Existing protocol already points this way: [`.cortex/protocol.md`](../protocol.md) says agents start with `cortex manifest --budget <N>`, then use `cortex grep` and `cortex retrieve` for deeper lookup. This plan turns that read contract into production behavior.

## Approach

Ship in narrow slices that each strengthen the context-integrity loop without changing the storage model:

1. **Instrument budgets.** Implement issue [#244](https://github.com/autumngarage/cortex/issues/244): token-budget reporting and guardrails for manifest/retrieve/agent workflows. The output should explain included, omitted, and over-budget material without requiring a live LLM.
2. **Make handoffs narrow and testable.** Implement issue [#243](https://github.com/autumngarage/cortex/issues/243): `cortex journal draft pr-merged --facts-file <path>` or equivalent, with a schema that cheap models and Conductor can fill without getting filesystem authority.
3. **Move journal staging into source PRs.** Implement issue [#207](https://github.com/autumngarage/cortex/issues/207): stage the PR-merged journal entry before merge and finalize it after merge, reducing meta-merge churn while preserving append-only Journal invariants.
4. **Promote Context CI.** Add a production profile for doctor checks that combines structural validation, generated-state freshness, budget warnings, handoff schema checks, and source-PR journal staging checks.
5. **Measure lookup reality.** Implement issue [#235](https://github.com/autumngarage/cortex/issues/235): track grep:retrieve usage ratio from local command metadata so retrieval and manifest decisions are data-backed.
6. **Use data before semantic expansion.** Revisit issue [#234](https://github.com/autumngarage/cortex/issues/234) only after #235 produces enough usage evidence to justify deterministic semantic top-up in session-start manifests.

Artifact policy for this plan:

- **Committed source files:** `.cortex/doctrine/*.md`, `.cortex/plans/*.md`, `.cortex/journal/*.md`, `.cortex/procedures/*.md`, `.cortex/config.toml`.
- **Committed derived files:** `.cortex/state.md`, `.cortex/map.md` when present, and `.cortex/.index.json` once the CLI writes it, because the SPEC treats the index as the authoritative cache for promotion and cross-reference state.
- **Ephemeral derived files:** local usage counters and retrieval indexes unless a future SPEC change declares a committed format.
- **Manual edits to generated files:** rejected by doctor when they remove generated metadata or make source hashes stale; allowed only inside documented hand blocks when a generated file supports them.
- **Machine-readable diagnostics:** JSON output must include stable diagnostic codes, affected paths, severity, exit-code class, and suggested rebuild/repair command.

## Success Criteria

- `cortex manifest --budget 4000`, `--budget 8000`, and `--budget 32000` emit deterministic budget reports with included bytes/tokens, omitted sources, and warning thresholds covered by tests.
- `cortex journal draft pr-merged --facts-file <path>` validates a documented schema, writes no file on invalid facts, and has fixture tests for valid, missing-field, and malformed-input cases.
- Source-PR journal staging reduces PR-merged meta PRs on this repo to zero for five consecutive Cortex PR merges while preserving append-only Journal behavior.
- `cortex doctor --production` or an equivalent production profile passes on `cortex`, `touchstone`, `conductor`, and `sentinel` with zero errors and documented warnings only.
- Editing any Doctrine, Plan, Journal, or Procedure source file marks affected derived artifacts stale; rebuilding updates source hashes deterministically and produces reviewable diffs.
- CI fails with distinct diagnostics for stale-derived, missing-source, unresolved-provenance, budget-exceeded, policy-violation, and manual-edit-to-generated cases.
- Lookup telemetry records local grep/retrieve/manifest usage without prompt contents, secrets, or remote reporting; a journal entry cites at least two weeks or 100 Cortex command events before deciding #234.
- Touchstone, Conductor, Sentinel, and Alchemist can consume Cortex manifests or diagnostics through file/CLI contracts without bespoke hidden state.
- README, `docs/PITCH.md`, `docs/spec-conformance.md`, and fixtures all describe context integrity consistently: source capture, generated artifacts, budget, invalidation, and verification.

## Work items

- [x] #244 — add token-budget instrumentation and guardrails for Cortex agent workflows.
- [x] #243 — add a narrow journal-drafting facts-file handoff for cheap model or Conductor summarization.
- [x] #207 — design decided in doctrine/candidate (see PR: https://github.com/autumngarage/cortex/compare/main...docs/source-pr-staging-adr?expand=1).
- [ ] Define context artifact contracts: source vs derived classes, schema/version fields, generated edit policy, and source-to-derived invalidation rules. *(Parked 2026-06-09 — superseded track; revisit only if the CLI/protocol track resumes. See `journal/2026-06-09-roadmap-refinement-and-issue-hygiene`.)*
- [x] Add the production doctor profile with human-readable and JSON diagnostics, stable diagnostic codes, and nonzero exit codes for stale-derived, missing-source, unresolved-provenance, budget-exceeded, policy-violation, and manual-edit-to-generated cases.
- [x] Wire the production doctor profile into this repo's review/release path.
- [x] #235 — record local grep:retrieve/manifest usage ratio with privacy-preserving metadata.
- [ ] Use #235 telemetry to decide #234 with a journaled product decision. *(Parked 2026-06-09 — lives on in open issue cortex#234 + `doctrine/candidate-manifest-semantic-topup`'s promotion checklist; evidence-gated, no clock.)*
- [ ] Add end-to-end fixtures for fresh repo, stale generated state, budget exceeded, invalid facts file, source-PR journal staging, and optional Autumn Garage integrations. *(Parked 2026-06-09 — superseded track; see `journal/2026-06-09-roadmap-refinement-and-issue-hygiene`.)*
- [ ] Refresh docs/spec-conformance once the production doctor profile exists. *(Parked 2026-06-09 — superseded track; see `journal/2026-06-09-roadmap-refinement-and-issue-hygiene`.)*

## Follow-ups (deferred)

None. Work not listed here remains governed by the cited GitHub issues or by [Doctrine 0008](../doctrine/0008-context-integrity-build-system.md).

## Known limitations at exit

- This plan does not make semantic retrieval mandatory; semantic top-up waits for #235 telemetry and the #234 decision.
- This plan does not add a hosted service, central dashboard, or agent framework surface.
- This plan does not change the `.cortex/` file format. Any SPEC-impacting change discovered during implementation must ship as a separate SPEC update with its required version bump.
