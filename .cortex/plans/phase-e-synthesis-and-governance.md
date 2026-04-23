---
Status: active
Written: 2026-04-23
Author: human
Goal-hash: 87f9ab57
Blocked-by: phase-d-integration
Updated-by:
  - 2026-04-23T15:10 claude-session-2026-04-23 (created as reordered Phase E; absorbs LLM synthesis, promotion writer, doctor expansions, and external dogfood gate from old phase-c-first-synthesis)
Cites: ../../SPEC.md, ../../.cortex/protocol.md, ../../PLAN.md § Phase E, ../doctrine/0003-spec-is-the-artifact, ../doctrine/0005-scope-boundaries-v2, journal/2026-04-23-phase-c-reordered
---

# Phase E — Synthesis and governance

> Layer LLM-enhanced synthesis on top of the deterministic core, wire the promotion-queue writer end-to-end, and give `cortex doctor` teeth on every SPEC § 4 cross-layer rule. This is the phase where spec-compliance becomes enforceable and Cortex produces prose-quality Map/State files alongside the deterministic defaults.

## Why (grounding)

The first three phases built the skeleton (init, inspect, validate — Phase B), the authoring loop (draft, spawn, refresh deterministic — Phase C), and the composition surface (Sentinel/Touchstone hooks — Phase D). Phase E is the capstone: the synthesis commands that call `claude -p` for prose generation (building on `refresh-state`'s deterministic core), the `.cortex/.index.json` writer that backs the promotion queue, the `cortex promote` writer that turns Journal evidence into Doctrine entries, and the `cortex doctor` expansions that enforce the SPEC's harder invariants.

Most work items here were originally scoped into [`phase-c-first-synthesis`](./phase-c-first-synthesis.md) (now cancelled) — they get redelivered here because they are, in aggregate, governance and polish rather than the core value-delivery path ([`doctrine/0001-why-cortex-exists`](../doctrine/0001-why-cortex-exists.md) says the value is the reasoning layer, not the enforcement tooling). The exception is the external dogfood gate on Sentinel's repo, which moves to this phase because it's where prompt design actually matters — the deterministic `refresh-state` from Phase C will already have been exercised on this repo's `.cortex/`, so the Sentinel-clone gate is specifically a test of the LLM synthesis and the Doctrine-promotion flow.

Grounded in [`doctrine/0003-spec-is-the-artifact`](../doctrine/0003-spec-is-the-artifact.md) (every new doctor check translates a SPEC § 4 rule into enforceable behavior) and [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #7 (LLM synthesis is optional enhancement — `--enhance` flag, never default — and never required for Cortex to deliver its core value).

## Success Criteria

This plan is done when the following hold against this repo (dogfood gate) AND against a fresh Sentinel clone (external gate):

1. **`cortex refresh-map`** — reads primary sources (code tree + `pyproject.toml`/`package.json`/`Cargo.toml`/`go.mod` + Doctrine entries), shells out to `claude -p`, and writes `.cortex/map.md` with a complete seven-field header and a structural summary body that references real package/module names. Runs in under 60 s on this repo. Fails gracefully (non-zero exit + stderr message) when `claude` is not on PATH — does NOT silently produce a partial file.
2. **`cortex refresh-state --enhance`** — runs the Phase C deterministic `refresh-state`, then invokes `claude -p` to polish the auto-generated sections into a more narrative form between the hand-authored markers. The `--enhance` output is deterministic enough to survive a `doctor` clean run but is explicitly NOT byte-identical across invocations (the prose varies). Default `cortex refresh-state` remains deterministic; `--enhance` is opt-in.
3. **`.cortex/.index.json` writer** — emits a stable JSON shape with `promotion_queue`, each entry carrying `{id, source, type, first_seen, last_seen, state, related}`. Queue states (`proposed`, `stale-proposed`, `approved`, `needs-more-evidence`, `skip-forever`, `duplicate-of`) populate correctly per SPEC § 4.7. Writer is a pure function of `.cortex/journal/` + `.cortex/doctrine/`; runs automatically as part of `cortex refresh-state` and also as a standalone `cortex refresh-index` command.
4. **`cortex promote <id>` writer** — reads the candidate from `.index.json`, writes a new `doctrine/NNNN-<slug>.md` from the `candidate.md` template with `Promoted-from:` set, updates `.index.json` to mark the candidate `approved`, and emits a `Type: promotion` Journal entry in the same operation (never modifies the source Journal entry — append-only per SPEC § 3.5).
5. **`cortex doctor` orphan-deferral detection** — every Plan `## Follow-ups (deferred)` item must resolve to another Plan or a Journal entry referenced in the same commit (SPEC § 4.2); orphans surface as errors.
6. **`cortex doctor` append-only-violation detection** — flag any Journal file modified after its initial commit (SPEC § 3.5).
7. **`cortex doctor` immutable-Doctrine / Status-mutation detection** — flag Doctrine entries whose content changes while `Status: Accepted` (SPEC § 3.1); mutation requires a new superseding entry.
8. **`cortex doctor` promotion-queue invariants** — WIP limit (default 10 `proposed` candidates → error when exceeded); candidate aging (>14 days → `stale-proposed`).
9. **`cortex doctor` single-authority-rule drift** — scan `AGENTS.md` / `CLAUDE.md` / `.cursor/rules/*` for content that duplicates Cortex Doctrine claims without `grounds-in:` citation; drift surfaces as a warning per file (SPEC § 4.8).
10. **`cortex doctor` CLI-less-fallback warning** — when only `@.cortex/protocol.md` + `@.cortex/state.md` are imported and the corpus exceeds default thresholds (>20 Doctrine entries or >100 Journal entries), warn about recency-by-grep insufficiency.
11. **`cortex doctor --audit` expansion to T1.2 / T1.3 / T1.4 / T1.6 / T1.7** — T1.2 (test failure after success) via session-state input; T1.3 (Plan status transition) via frontmatter diff; T1.4 (file deletion >N lines) via `git diff --stat`; T1.6 (Sentinel cycle) via `.sentinel/runs/` detection; T1.7 (Touchstone pre-merge arch-significant) via the Phase D integration hook.
12. **Full SPEC § 5.4 claim-trace in `cortex doctor --audit-digests`** — replace the first-5-bullets heuristic with a random N-sample per digest, verifying each claim traces to ≥1 source Journal entry.
13. **Interactive per-candidate prompts in bare `cortex`** — `cortex` (no args) loops over `.cortex/.index.json` candidates with y/n/view/defer/skip prompts per the README example, plus a single-keystroke "Generate <month> digest?" when the latest digest is overdue (>45 days).
14. **External dogfood gate.** Running `cortex refresh-map && cortex refresh-state --enhance && cortex doctor --strict` against a freshly-cloned Sentinel repo produces a clean exit and non-trivial Map/State content — this is the first test against a project Cortex didn't author.
15. **v1.0.0 release** — first non-draft release. SPEC.md frozen at whatever version this phase ships against; any amendments discovered during Phase E land before the tag.

## Approach

**Synthesis pattern mirrors Sentinel:** `subprocess.run(["claude", "-p", prompt, "--output-format", "stream-json"])` with `@path` imports for source files. A new `src/cortex/synth.py` module owns the `claude` CLI invocation; no other module imports `anthropic`, and a test greps the source tree to enforce this (SPEC § 3 / Doctrine 0005 #7: no SDK, no provider layer).

**Synthesis is additive, never destructive.** The seven-field metadata block is written before the synthesized body so a partial / interrupted run leaves a file `cortex doctor` can still diagnose. `Incomplete:` is derived from the actual source diff between configured and loaded sources, not hand-waved. `--enhance` on `refresh-state` layers prose on top of the deterministic core, preserving the hand-authored markers — it never rewrites from scratch.

**Doctor expansions share a pattern:** each check is a pure function from repo state to a list of `DoctorFinding(path, severity, message)`. Running `cortex doctor --json` emits the full finding list; `--strict` escalates all warnings to errors. This is the shape the Phase D pre-push hook needs.

**External dogfood is the spec-validation step.** Running the refresh commands against Sentinel's repo forces the spec to survive a project with a different structure (Python package layout, Sentinel run files, different writing style). Any spec amendments discovered here land in this phase's PRs, with a journal entry per [`doctrine/0003-spec-is-the-artifact`](../doctrine/0003-spec-is-the-artifact.md).

## Work items

All items originally scoped into [`phase-c-first-synthesis`](./phase-c-first-synthesis.md) (now cancelled) are absorbed here:

- [ ] **`.cortex/.index.json` writer + `cortex refresh-index`** — absorbs "`.cortex/.index.json` writer" and "`cortex refresh-index`" from cancelled Phase C.
- [ ] **`cortex refresh-map`** — LLM synthesis, seven-field header. Absorbs "`cortex refresh-map`" from cancelled Phase C.
- [ ] **`cortex refresh-state --enhance`** — LLM polish on top of the Phase C deterministic core. Absorbs the LLM half of cancelled Phase C's "`cortex refresh-state`" work item; Phase C ships the deterministic half.
- [ ] **`cortex promote <id>` writer** — full end-to-end promotion. Absorbs "`cortex promote` writer" from cancelled Phase C.
- [ ] **Orphan-deferral detection in `cortex doctor`** — absorbs same-named item from cancelled Phase C.
- [ ] **Append-only-violation detection on Journal in `cortex doctor`** — absorbs same-named item.
- [ ] **Immutable-Doctrine / Status-mutation detection in `cortex doctor`** — absorbs same-named item.
- [ ] **Promotion-queue invariants in `cortex doctor`** — absorbs same-named item.
- [ ] **Single-authority-rule drift detection in `cortex doctor`** — absorbs same-named item.
- [ ] **CLI-less-fallback warning in `cortex doctor`** — absorbs same-named item.
- [ ] **Expand `cortex doctor --audit` Tier-1 coverage to T1.2 / T1.3 / T1.4 / T1.6 / T1.7** — absorbs same-named item. T1.7 depends on Phase D's Touchstone pre-merge integration landing first.
- [ ] **Full SPEC § 5.4 claim-trace in `cortex doctor --audit-digests`** — absorbs same-named item.
- [ ] **Interactive per-candidate prompts in bare `cortex`** — absorbs same-named item from cancelled Phase C.
- [ ] **`cortex doctor --strict`** — completed here by gating on all the above checks (Phase D landed `--strict` against the v0.3.0 check set; this phase extends it).
- [ ] **External dogfood gate on Sentinel repo** — absorbs same-named item; positioned at the end of the phase so every other capability is available for validation.
- [ ] **v1.0.0 release** — first non-draft release; SPEC.md frozen at the shipping version.

## Follow-ups (deferred)

Nothing deferred at plan creation. Items move here only when scope actually shifts during execution, per SPEC § 4.2.
