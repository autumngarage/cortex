---
Status: cancelled
Written: 2026-04-18
Cancelled: 2026-04-23
Author: human
Goal-hash: eec16ea4
Updated-by:
  - 2026-04-18T11:45 claude-session-2026-04-18 (created as Phase C P0 on Phase B exit; absorbs deferred items from phase-b-walking-skeleton)
  - 2026-04-23T15:15 claude-session-2026-04-23 (cancelled; reordered into three new plans that decompose the work along risk lines)
Promoted-to: plans/phase-c-authoring-and-state, plans/phase-d-integration, plans/phase-e-synthesis-and-governance, journal/2026-04-23-phase-c-reordered
Cites: ../../SPEC.md, ../../.cortex/protocol.md, ../../PLAN.md § Phase C, ../doctrine/0003-spec-is-the-artifact, ../doctrine/0005-scope-boundaries-v2, journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew
---

# Phase C — First Synthesis

> **Cancelled 2026-04-23.** This plan bundled three distinct risk classes (LLM synthesis, deterministic index + promotion writer, doctor expansions) into one phase. Decomposed into [`phase-c-authoring-and-state`](./phase-c-authoring-and-state.md) (P0), [`phase-d-integration`](./phase-d-integration.md) (P1), and [`phase-e-synthesis-and-governance`](./phase-e-synthesis-and-governance.md) (P2) so the reordering delivers "session pickup works" (the stated value) before LLM polish and governance. Every work item below is absorbed into one of the successor plans — see the Work items list in each for the mapping. Full rationale in [`journal/2026-04-23-phase-c-reordered.md`](../journal/2026-04-23-phase-c-reordered.md). The H1 and Goal-hash remain unchanged because SPEC § 4.9 uses them to detect drift; status transitions live in frontmatter, not the title.
>
> *Original scope follows, preserved unchanged for historical reference.*

---

> Wire `cortex refresh-map` and `cortex refresh-state` to the `claude -p` CLI so Map and State are generated from primary sources with seven-field provenance (SPEC § 4.5), and populate `.cortex/.index.json` so the promotion queue, doctor's `--audit` invariants, and `cortex promote` graduate from stubs to working writers.

## Why (grounding)

Phase B shipped v0.1.0 on Homebrew ([`journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md`](../journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md)) but deferred every check or command that needs either LLM synthesis or a populated `.cortex/.index.json`. Those deferrals land here — Phase C is the first phase that calls out to `claude -p` and writes the index cache that the rest of the CLI can then trust. Grounded in [`doctrine/0003-spec-is-the-artifact`](../doctrine/0003-spec-is-the-artifact.md) (spec is the contract — regeneration must match § 4.5) and [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) (no SDK, no provider abstraction — shell out to the `claude` CLI the same way Sentinel does).

## Success Criteria

This plan is done when the following hold against this repo (dogfood gate) and against a fresh Sentinel clone (external gate):

1. `cortex refresh-map` reads primary sources (code tree + `pyproject.toml` / `package.json` / `Cargo.toml` if present + Doctrine entries), shells out to `claude -p`, and writes `.cortex/map.md` with a complete seven-field header (`Generated`, `Generator`, `Sources`, `Corpus`, `Omitted`, `Incomplete: []`, `Conflicts-preserved`) and a structural summary body that references real package/module names from the repo.
2. `cortex refresh-state` reads `.cortex/journal/`, `.cortex/plans/`, `.sentinel/runs/` if present, and writes `.cortex/state.md` with a seven-field header and a `## P0/P1/P2` priority ranking plus a "Shipped recently" block; staleness warning surfaces if `Generated:` is older than 24 h (SPEC § 3.3).
3. Both refresh commands emit `.cortex/.index.json` with a non-empty `promotion_queue` drawn from post-Protocol Journal entries matching the `Type:` signals for promotion candidacy (SPEC § 4.7). Queue states (`proposed`, `stale-proposed`, `approved`, `needs-more-evidence`, `skip-forever`, `duplicate-of`) populate correctly.
4. `cortex promote <id>` upgrades from its v0.1.0 stub to a working writer: reads the candidate from `.index.json`, writes a new `doctrine/NNNN-*.md` from the `doctrine/candidate.md` template, sets `Promoted-from:`, updates `.index.json` to mark the candidate `approved`, and emits a `Type: promotion` Journal entry — never modifies the source Journal entry (append-only).
5. `cortex doctor` gains orphan-deferral detection per SPEC § 4.2: every Plan's `## Follow-ups (deferred)` item must resolve to another Plan or a Journal entry referenced in the same commit; orphans surface as errors.
6. `cortex doctor` gains promotion-queue invariant checks per SPEC § 4.7: WIP limit (default 10 `proposed` candidates), candidate aging (>14 days → `stale-proposed`), and WIP-exceeded → error.
7. `cortex doctor` gains single-authority-rule drift detection per SPEC § 4.8: scans `AGENTS.md`, `CLAUDE.md`, and `.cursor/rules/*` for content that duplicates Cortex Doctrine claims without a `grounds-in:` citation; drift surfaces as a warning per file.
8. `cortex doctor` warns when the CLI-less fallback manifest (Protocol § 1) is in use against a corpus exceeding default thresholds (>20 Doctrine entries or >100 Journal entries).
9. Interactive `cortex` (no args) now loops over real `.cortex/.index.json` candidates with y/n/view/defer/skip prompts per the README example, and offers a single-keystroke "Generate <month> digest?" when the latest digest is overdue (>45 days).
10. External dogfood gate: running `cortex refresh-map && cortex refresh-state && cortex doctor` against a freshly-cloned Sentinel repo (the second composition partner) produces a clean exit with non-trivial Map/State content and zero orphan deferrals.

## Approach

Synthesis pattern mirrors Sentinel: `subprocess.run(["claude", "-p", prompt, "--output-format", "stream-json"])` with `@path` imports for source files. No SDK, no provider abstraction — a new `cortex/synth.py` module owns the Claude CLI invocation and nothing imports `anthropic` directly (enforced by a new test that greps the source tree).

The seven-field metadata block is written *before* the synthesized body so a partial / interrupted run leaves a file that `cortex doctor` can still diagnose. The `Incomplete:` list is derived from the actual source diff between the configured sources and those successfully loaded, not hand-waved.

`.cortex/.index.json` writer is a small pure function that walks `.cortex/journal/` and `.cortex/doctrine/` and projects the promotion queue — deterministic, independent of any LLM call, so `cortex promote` can be tested without network. Refresh commands invoke the writer after synthesis completes.

## Work items

Deferred from Phase B (each resolves a specific deferral in [`journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md`](../journal/2026-04-18-phase-b-shipped-v0.1.0-on-homebrew.md) "Deferred items"):

- [ ] **`.cortex/.index.json` writer** — populates `promotion_queue` from Journal; updated every `cortex refresh-*` run. Unblocks the entries below.
- [ ] **Orphan-deferral detection in `cortex doctor`** — resolves the "orphan-deferral detection" deferred item.
- [ ] **Append-only-violation detection on Journal in `cortex doctor`** — flag any Journal file modified after its initial commit (per SPEC § 3.5). Resolves the "append-only Journal detection" deferred item.
- [ ] **Immutable-Doctrine / Status-mutation detection in `cortex doctor`** — flag Doctrine entries whose content changes while `Status: Accepted` (SPEC § 3.1); mutation requires a new superseding entry. Resolves the "Doctrine immutability / Status-mutation detection" deferred item.
- [ ] **Promotion-queue invariants in `cortex doctor`** — WIP limit, candidate aging (per SPEC § 4.7). Resolves the "promotion-queue invariants" deferred item.
- [ ] **Single-authority-rule drift detection in `cortex doctor`** — scan `AGENTS.md` / `CLAUDE.md` / `.cursor/rules/*` for Doctrine-duplicating content without a `grounds-in:` citation (SPEC § 4.8). Resolves the "single-authority-rule drift" deferred item.
- [ ] **CLI-less-fallback warning in `cortex doctor`** — warn when the Protocol § 1 fallback configuration is used against a corpus exceeding default thresholds (>20 Doctrine entries or >100 Journal entries). Resolves the "CLI-less-fallback warning" deferred item.
- [ ] **Expand `cortex doctor --audit` Tier-1 coverage to T1.2 / T1.3 / T1.4 / T1.6 / T1.7** — T1.2 (test failure after success) needs session-state input; T1.3 (Plan status transition) needs frontmatter diff across commits; T1.4 (file deletion >N lines) needs `git diff --stat` parsing; T1.6 (Sentinel cycle) needs `.sentinel/runs/<ts>.md` detection; T1.7 (Touchstone pre-merge on arch-significant diff) needs the existing Touchstone integration hook. Resolves the "Expanded T1.2/T1.3/T1.4/T1.6/T1.7 audit coverage" deferred item.
- [ ] **Full SPEC § 5.4 claim-trace in `cortex doctor --audit-digests`** — replace the first-5-bullets heuristic with a random N-sample per digest, verifying each claim traces to at least one source Journal entry. Resolves the "Full SPEC § 5.4 audit-digest claim tracing" deferred item.
- [ ] **Interactive per-candidate prompts in bare `cortex`** — resolves the "interactive prompts" deferred item; depends on the `.index.json` writer landing first.

New for Phase C:

- [ ] **`cortex refresh-map`** — shells out to `claude -p`, writes `.cortex/map.md` with seven-field header.
- [ ] **`cortex refresh-state`** — shells out to `claude -p`, writes `.cortex/state.md` with seven-field header.
- [ ] **`cortex refresh-index`** (or bundled into refresh-map/state) — decision deferred to first slice.
- [ ] **`cortex promote` writer** — graduates from stub to end-to-end promotion writing a new Doctrine entry, updating `.index.json`, and emitting a `Type: promotion` Journal entry.
- [ ] **Dogfood gate on Sentinel** — run the refresh commands against a freshly-cloned Sentinel repo to validate the external case.

## Follow-ups (deferred)

Nothing deferred at plan creation — items move here only when scope actually shifts during execution, per SPEC § 4.2.
