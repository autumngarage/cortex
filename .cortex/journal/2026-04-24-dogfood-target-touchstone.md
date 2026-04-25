# Dogfood target for v0.9.0: touchstone

**Date:** 2026-04-24
**Type:** decision
**Trigger:** T2.1 (user phrased a decision: "let's dogfood on touchstone") + T1.1 (diff touches .cortex/plans/)
**Cites:** plans/cortex-v1, journal/2026-04-24-production-release-rerank, ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md, doctrine/0001-why-cortex-exists, doctrine/0002-compose-by-file-contract-not-code

> The v0.9.0 external dogfood gate target is **touchstone** (`autumngarage/touchstone`, currently v2.1.1 installed locally). The earlier production-release-rerank journal recommended conductor (case-study subject) with sigint (oldest CLAUDE.md dogfood reference) as alternative; the user picked a third option not on either list. This entry records the choice, the reasoning, and what changes about the v0.9.0 exit criteria as a result.

## Context

[`journal/2026-04-24-production-release-rerank`](./2026-04-24-production-release-rerank.md) explicitly named the dogfood-target decision as confirmable before v0.3.0 ships, with two recommendations: conductor (the case study is grounded there, has known-stale `CLAUDE.md` / `README.md` claims that exercise `--audit-instructions` on day one) and sigint (oldest CLAUDE.md dogfood reference, but predates the case-study insight). Touchstone was not on the recommendation list.

The user's response: *"let's dogfood on touchstone."*

Touchstone wasn't recommended because the rerank framed external dogfood as testing Cortex against a *downstream consumer* repo where stale-CLAUDE.md-class problems are known to live. The conductor case study is exactly that shape. Touchstone is a different shape — it's a *sibling tool* in the autumngarage trio (Touchstone = standards/policy, Sentinel = loop, Cortex = memory), not a downstream consumer. The user's choice reframes the v0.9.0 test from "does Cortex catch the conductor-class bug" to "does Cortex compose with its sibling tool when both are operating on the same codebase." Both are valid v1.0 gates; the latter is arguably stronger.

## What we decided

**Dogfood target: `autumngarage/touchstone` (v2.1.1, source repo at the user's local touchstone clone).**

Reasoning:

1. **Composition validation is a stronger v1.0 gate than case-study verification.** The autumngarage trio's value proposition is composition (per [`doctrine/0002-compose-by-file-contract-not-code`](../doctrine/0002-compose-by-file-contract-not-code.md)). Dogfooding Cortex on Touchstone directly tests whether the trio actually composes — not just whether each tool works in isolation. If Cortex earns its keep on the project that *enforces* the engineering rigor for the rest of the trio, that's stronger evidence than verifying a known incident on a downstream consumer.

2. **Touchstone is the foundation layer.** Touchstone is the most stable layer in the trio (the standards/policy layer that the other two depend on). Validating that Cortex doesn't conflict with Touchstone-managed files (`principles/`, `scripts/`, `.codex-review.toml`, `.pre-commit-config.yaml`, etc.) is more important than validating against any single downstream consumer. If the trio composes at the foundation, it composes everywhere.

3. **Touchstone is mature (v2.1.1).** Eight+ minor releases of accumulated invariants, decisions, principles, and design-rationale prose. More opportunity for `cortex doctor --audit-instructions` to find drift, more journal-worthy events to retroactively map, more existing prose for `cortex init`'s scan-and-absorb to test against. Conductor and sigint are both younger and less prose-rich.

4. **Touchstone has its own engineering-rigor culture.** It IS the rigor tool. If Cortex earns its keep on a project that already has its own discipline mechanisms (Codex review, pre-push hooks, principle files), that's a stronger signal than earning its keep on a project that lacks them. The bar is "does Cortex add value where rigor already exists," not "does Cortex add value where rigor is missing."

5. **The user works on touchstone constantly.** Touchstone is installed in this very repo (per `cortex doctor` Autumn Garage siblings output: `✓ touchstone 2.1.1 (installed) — .touchstone-config present`). High frequency of use → more dogfood data points → faster feedback during the v0.9.0 week.

6. **Conductor case-study fit is real but not lost.** The case study still informs trust-layer feature design (`--audit-instructions`, `Verified:`); we just won't verify the specific conductor incident in v0.9.0. We'll find different friction. If `--audit-instructions` catches drift in Touchstone's `CLAUDE.md` / `README.md` / `principles/*.md` that we *didn't* already know about, that's stronger evidence than catching something already documented as broken.

### Tradeoffs acknowledged

- **`--audit-instructions` won't have a known-stale claim to catch on day one.** The conductor incident gives `--audit-instructions` an immediate validation target; touchstone gives it a discovery target. We'll learn more from discovery (does the audit find anything?) but with a riskier exit criterion (no findings could mean either "the audit works and touchstone is clean" or "the audit doesn't catch anything"). Mitigation: design `--audit-instructions` so it produces clear "checked X claims, all verified" output even on clean projects, so a no-findings result is informative not ambiguous.

- **Touchstone-managed files create write boundaries.** Touchstone owns `principles/*.md` and `scripts/*.sh` in this repo (synced via `touchstone update`). If `cortex` writes anywhere those overlap, Touchstone may overwrite. v0.9.0 needs to verify Cortex stays out of Touchstone-owned paths during normal operation (it should — Cortex writes only to `.cortex/`).

- **Touchstone is a meta-project.** Its primary product is `touchstone update` (sync-from-upstream-package) and the principles/scripts it distributes, not a user-facing application. The "what should I work on next" use case for `cortex next` is different on a meta-project than on an app. This is fine for v0.9.0 — different shape of usage is more evidence, not less.

## Consequences / action items

- [x] [`plans/cortex-v1`](../plans/cortex-v1.md) `### v0.9.0` updated: dogfood target named as touchstone (no longer "to be confirmed"); the "Confirm dogfood target" line removed since it's now decided.
- [x] [`plans/cortex-v1`](../plans/cortex-v1.md) `## Approach` updated: "Recommended target: conductor" rewritten to name touchstone as the chosen target with the composition-validation rationale.
- [x] `## Updated-by` line added to plan frontmatter recording this decision.
- [x] `.cortex/state.md` `## Current work` updated to name touchstone (replaces "recommended target: conductor; alternative: sigint — confirmable before v0.3.0 ships").
- [x] `README.md` `## Status and plan` updated to name touchstone.
- [x] `CLAUDE.md` `## Cortex-Specific Principles` "Dogfood as the readiness bar" updated to name touchstone.
- [ ] At v0.9.0 kickoff: install Cortex on touchstone source repo via `cortex init` (use the v0.2.2 scan-and-absorb to map touchstone's existing `principles/`, `scripts/`, `CLAUDE.md`, `AGENTS.md`, etc. into Cortex's layers).
- [ ] At v0.9.0 kickoff: configure `.cortex/config.toml` `[audit-instructions]` section on touchstone naming the source-of-truth artifacts that touchstone's docs claim about (e.g., the `homebrew-touchstone` tap, sibling-repo references, the upstream package source).
- [ ] During v0.9.0 week: every significant friction point gets a journal entry on touchstone's `.cortex/`, mirrored back here (in `docs/case-studies/`) if it reveals a structural Cortex issue rather than a touchstone-specific quirk.

## What this forecloses

**The "external" in "external dogfood gate" was misleading.** The rerank journal framed v0.9.0 as testing on an *external* project — meaning "not Cortex itself." Touchstone *is* a sibling autumngarage tool; it's "external to Cortex's source tree" but it's not external to the autumngarage ecosystem. That framing distinction matters for v1.0's marketing story: dogfooding on a sibling tool validates *the trio's composition story*, not "Cortex works on arbitrary repos." Conductor or sigint would have validated the latter; touchstone validates the former. Both are real evidence but they support different production-readiness claims. Expect README/PITCH at v0.9.0/v1.0.0 to lean into the composition story rather than the "works on any repo" story.

**Future external-validation on a non-trio project becomes a v1.x concern.** If/when v0.9.0 on touchstone surfaces zero structural Cortex issues, the next natural test is on a non-trio project (e.g., the still-recommended conductor or sigint, OR an entirely external project the user picks up). That's a v1.x gate, not a v1.0 gate. v1.0 ships when touchstone-dogfood passes.
