---
Status: active
Written: 2026-04-23
Author: human
Goal-hash: 1c66ba43
Blocked-by: phase-c-authoring-and-state
Updated-by:
  - 2026-04-23T15:05 claude-session-2026-04-23 (created as reordered Phase D; promotes integration work from old PLAN.md Phase E because integration is where Cortex's value compounds and synthesis is not a prerequisite)
Cites: ../../SPEC.md, ../../.cortex/protocol.md, ../../PLAN.md § Phase D, ../doctrine/0001-why-cortex-exists, ../doctrine/0002-compose-by-file-contract-not-code, journal/2026-04-23-phase-c-reordered
---

# Phase D — Composition integrations

> Sentinel and Touchstone write to `.cortex/` via Phase C's `cortex journal draft` so the Journal fills itself from real work events (cycle endings, PR merges) instead of depending on the author remembering to write entries. This is where the composition story (Touchstone = standards, Sentinel = loop, Cortex = memory) actually starts compounding.

## Why (grounding)

Cortex's value multiplies when it receives writes from the other two autumngarage tools — each tool covers a different writer/trigger cadence, so the Journal accumulates decisions, migrations, and outcomes without anyone having to remember to record them ([`doctrine/0001-why-cortex-exists`](../doctrine/0001-why-cortex-exists.md), [`doctrine/0002-compose-by-file-contract-not-code`](../doctrine/0002-compose-by-file-contract-not-code.md)). The Protocol already defines the Tier 1 triggers this phase implements: T1.6 (Sentinel cycle ended), T1.7 (Touchstone pre-merge on architecturally-significant diff), T1.9 (PR merged to default branch). Without the integrations, those triggers are aspirations in [`protocol.md`](../protocol.md); with them, the Journal becomes an ambient byproduct of normal work.

Phase C delivered the authoring primitive (`cortex journal draft`). Phase D wires it into the two existing write-side tools so that every Sentinel cycle that shipped a PR, and every architecturally-significant Touchstone pre-merge, drafts an entry that just needs human review. The writing cost per event drops from "author a full entry" to "eyeball a pre-filled draft and save."

Grounded in [`doctrine/0002-compose-by-file-contract-not-code`](../doctrine/0002-compose-by-file-contract-not-code.md): Cortex does not import Sentinel or Touchstone; the integrations are hooks owned by those tools that invoke `cortex` as a subprocess. Cortex degrades gracefully when either absent.

## Success Criteria

This plan is done when the following hold end-to-end against Sentinel, Touchstone, and this repo:

1. **Sentinel end-of-cycle (T1.6).** Sentinel's `post-cycle` hook (owned by the Sentinel repo; added there, not here) invokes `cortex journal draft --type sentinel-cycle --run <path-to-run.md> --no-edit` when a cycle ended with a shipped PR or a significant lens finding. The drafted entry cites the Sentinel run file and summarizes the PR/finding. The hook is opt-in per-project via Sentinel's existing config; absent that opt-in, no Cortex calls happen.
2. **Touchstone post-merge (T1.9).** Touchstone's post-merge hook (in `autumngarage/touchstone`) invokes `cortex journal draft --type pr-merged --pr <number> --no-edit` on merge to the default branch. The drafted entry cites the PR (title, body, merge SHA) and lists the Journal / Plan / Doctrine files that were touched. Opt-in per-project via `.touchstone-config`.
3. **Touchstone pre-merge on architecturally-significant diff (T1.7).** When a PR's diff matches the architecturally-significant patterns configured in `.touchstone-config` (default patterns: `principles/**`, `.cortex/doctrine/**`, `SPEC.md`, `.cortex/protocol.md`), Touchstone's pre-merge hook reads `.cortex/templates/doctrine/candidate.md` (the T1.7 template the Protocol names) and posts a PR comment with the template pre-filled from PR title + body + touched-files summary. The author reads the comment and hand-authors a Doctrine candidate by creating a file in `.cortex/doctrine/` if the decision warrants it. No new storage layer introduced — SPEC.md currently has no `pending/` layer, and adding one is scope that belongs with Phase E's `cortex promote` writer + the SPEC amendment to define the promotion-staging surface. Phase D stays inside the Protocol contract without expanding the storage shape.
4. **Touchstone pre-push invariant gate.** Touchstone's pre-push hook is the one integration point that *intentionally* blocks the host operation: when opted in via `.touchstone-config`, the hook runs `cortex doctor --strict` on the default-branch push and blocks the push on any error. This is Touchstone's policy choice (a normal pre-push gate pattern), not a Cortex-forced behavior. At Phase D exit the `--strict` check-set is whatever Phase C's `cortex doctor` already covers (scaffold, seven-field metadata, Doctrine / Plan / Journal frontmatter, Goal-hash, deterministic `refresh-state` output) escalated to errors; the larger invariant checks (promotion-queue WIP, single-authority-rule drift, append-only violation, immutable-Doctrine mutation) are Phase E work and arrive in `--strict` automatically once they ship.
5. **Graceful degradation of informational hooks.** Every *informational* Cortex invocation from a Sentinel or Touchstone hook (end-of-cycle T1.6, post-merge T1.9, pre-merge T1.7 template comment) is non-blocking for the host tool — a non-zero exit from Cortex logs a warning but does NOT fail the Sentinel cycle or the Touchstone merge. A missing `cortex` on PATH produces one informational log line per invocation, never repeated, and never fails the host. The pre-push gate in (4) is the deliberate exception: Touchstone chooses to block because `cortex doctor --strict` failure is a spec-invariant violation the user asked Touchstone to enforce.
6. **Dogfood gate.** A week of work on this repo after v0.4.0 release produces ≥5 PR-merged journal entries auto-drafted by Touchstone. At least one Sentinel cycle on this repo produces an auto-drafted cycle entry. Both measurements recorded at the Phase D exit commit.

## Approach

**Writing code in two other repos.** Sentinel and Touchstone are the hook owners; Cortex provides the subprocess interface (`cortex journal draft`, `cortex doctor --strict`). Pull requests to Sentinel and Touchstone ship in the Phase D window alongside any Cortex-side refinements discovered during integration. This is the first multi-repo work item in the project — plan for coordinated PRs with ref-linking between them.

**No shared code.** Each hook shells out to `cortex`. Exit-code contracts are the integration surface: 0 = drafted, non-zero = not drafted (with a human-readable reason on stderr). No Python imports of Cortex modules from Sentinel or Touchstone; no `anthropic` import from any of them.

**Opt-in everywhere, off by default.** Both hooks require explicit config in the host tool's per-project config to fire. Users who install Cortex as a standalone journaling tool should see zero change in Sentinel/Touchstone behavior on projects that haven't opted in.

## Work items

- [ ] **Sentinel end-of-cycle integration** — implement the `post-cycle` hook in the `autumngarage/sentinel` repo that calls `cortex journal draft --type sentinel-cycle`. PR to that repo; coordinated with a Cortex-side PR that adds any missing `--run` / `--no-edit` plumbing surfaced during integration.
- [ ] **Touchstone post-merge hook** — implement in `autumngarage/touchstone` calling `cortex journal draft --type pr-merged`. Opt-in via `.touchstone-config`.
- [ ] **Touchstone pre-merge hook (architecturally-significant)** — pattern-match diff against `.touchstone-config` patterns, read the `doctrine/candidate.md` template, post a PR comment with template pre-filled from PR + diff context. Author hand-creates the Doctrine entry if warranted. Matches Protocol § 2 T1.7 template reference without expanding the SPEC storage layer.
- [ ] **`cortex doctor --strict`** — CLI flag that escalates every active warning emitted by `cortex doctor` to an error. At Phase D exit the active warning set is whatever Phase C's doctor ships (scaffold, seven-field metadata, Doctrine / Plan / Journal frontmatter, Goal-hash, deterministic `refresh-state` output, `--audit` Tier-1 misses for T1.1/T1.5/T1.8/T1.9, `--audit-digests` claim-sampling). The larger invariant checks (promotion-queue WIP, single-authority drift, append-only violation, immutable-Doctrine mutation, T1.2/T1.3/T1.4/T1.6/T1.7 audit, full § 5.4 claim-trace) ship in Phase E and are automatically covered by `--strict` once they land — no Phase D work needed to pick them up.
- [ ] **Touchstone pre-push hook** — invoke `cortex doctor --strict`, fail loudly on error.
- [ ] **Graceful-degradation tests** — each hook is exercised with (a) Cortex installed + opted in, (b) Cortex installed + not opted in, (c) Cortex not on PATH. All three paths must leave Sentinel / Touchstone behavior unchanged for the host tool.
- [ ] **v0.4.0 release** — tag + GitHub Release + Homebrew formula SHA update.

## Follow-ups (deferred)

Nothing deferred at plan creation. Items move here only when scope actually shifts during execution, per SPEC § 4.2.
