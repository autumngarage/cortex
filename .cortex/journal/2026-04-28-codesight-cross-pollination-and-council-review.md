# Codesight cross-pollination and 4-reviewer council review of cortex-v1 plan

**Date:** 2026-04-28
**Type:** decision
**Trigger:** T1.1 (diff touches `.cortex/plans/cortex-v1.md`)
**Cites:** plans/cortex-v1, journal/2026-04-28-cortex-v050-released-tier-2-tier-3-partial-tier-4, journal/2026-04-28-cortex-v051-autumn-mail-dogfood-polish-patch, journal/2026-04-24-production-release-rerank, doctrine/0005-scope-boundaries-v2, doctrine/0003-spec-is-the-artifact, doctrine/0002-compose-by-file-contract-not-code

> User pointed at https://github.com/Houseofmvps/codesight as a reference; asked which ideas to borrow into Cortex's v1.0 plan, then asked for council review via conductor. Five codesight ideas evaluated, three integrated into v1.0 path, two deferred with explicit revisit conditions, three rejected. The integration was then sharpened by a 4-reviewer council (codex, gemini, kimi, deepseek-reasoner via conductor) which produced ~10 substantive changes — most aggressively, Kimi cut the ADR-import-readiness gate item and demoted the token-cost measurement from a launch-story commitment to opportunistic evidence; Codex added five validation/audit work items v1.0 was previously silent on (compatibility audit, fresh-clone test, bare-repo degradation, generated-layer contract, retention visibility); Kimi added the SPEC-to-test traceability matrix as the single sharpest contribution. v1.0 grows from a ceremony milestone into a compatibility + invariant-validation milestone.

## What changed in cortex-v1.md

Concrete deltas applied:

**Adds (real new work, council-introduced):**
- v0.6.0 / Tier 4: generated-layer contract validation (every Map/State carries `Generated:` + source list per SPEC § 4.3); `.cortex/config.toml` schema validation; SPEC § 5.1 retention visibility (warnings, no destructive cleanup).
- v0.9.0: Touchstone post-merge hook canary-on-Cortex-then-validate-on-three-targets (resolves the Tier-3-vs-v0.9.0 timing question council debated 2-2; chosen sequencing is "land in Touchstone, canary on Cortex first, then exercise on three targets").
- v0.9.0: fresh-clone session-start acceptance test per target (Cortex's core promise made testable).
- v0.9.0: bare-repo degradation fixture (Doctrine 0002 demands no hard dependency on Sentinel/Touchstone/Conductor/`gh`/`brew`).
- v0.9.0: behavioral exit-bar review (replaces "I'd rather use this than hand-write" — zero crashes, no manual repairs to generated layers, no stale claim survives audit, fresh-clone passes, bare-repo passes).
- v1.0.0: pre-1.0 compatibility audit (run all CLI commands against repos initialized at v0.3 and v0.5 scaffolds; document migration if needed). For a file-format protocol, this is a v1.0 blocker.
- v1.0.0: brew-install smoke test on a clean machine (resolves the stale "Homebrew end-to-end verification of v0.3.0" follow-up by making it a per-release ritual).
- v1.0.0: `.cortex/config.toml` schema reference doc (a user-facing config without published schema is an adoption wall).
- v1.0.0: SPEC-to-test traceability matrix (per Doctrine 0003 — every normative § of SPEC.md maps to a doctor check or test assertion; the proof that the CLI implements the spec, not the other way around).
- v1.0.0: CLI help-text + error-message polish pass.

**Subtractions:**
- ADR-import readiness as a v0.9.0 gate item — **cut.** Existing `cortex init` scan-and-absorb covers the small case; the standalone `cortex import-knowledge` command stays deferred with a tightened revisit condition (needs fixtures from at least two real ADR corpora, not just size).
- Token-cost measurement public-ratio language ("≥5–10×", "ratio worth claiming") — **demoted to opportunistic evidence.** Record per target if trivial; no `tiktoken` dependency added; launch story does not depend on the number being large.
- "I'd rather use this than hand-write" subjective exit gate — **replaced** with five behavioral criteria.
- Tier 4 doctor invariants — **trimmed:** keep append-only Journal, immutable-Doctrine, T1.4 file-deletion, promotion-queue, CLI-less-fallback. **Defer to v1.x:** single-authority-rule drift (§ 4.8), full § 5.4 claim-trace audit beyond first slice.
- "Every SPEC invariant has a doctor check" claim — **rephrased** to "every v1.0-applicable file-format invariant" (T1.2/T1.6/T1.7 audits stay deferred, so the broader claim was overreach).
- "Tier 3 = install and walk away" claim — **softened:** the claim activates inside v0.9.0 when the Touchstone hook validates, not at v0.5.0 when Cortex-side surface shipped.
- "Three targets simultaneously" — **rephrased** to "with overlapping active use" (Codex's clarification — diversity is the point, literal-same-day is not).

**Stale follow-ups resolved:**
- Slug normalization → reassigned to v0.6.0 polish.
- Homebrew end-to-end verification of v0.3.0 → closed; replaced by v1.0.0 brew-install smoke test.
- Codex-review pedantry pattern as Doctrine candidate → closed (release-process navel-gazing per Kimi; reopen if pattern recurs in two more releases).
- Retention/cleanup pointer to "Tier 4 lifecycle scope" → resolved (Tier 4 owns visibility; destructive cleanup explicitly deferred with its own resolution target).

**Approach gains one sentence:** *"There is no Tier 5. All deferred items are v1.x candidates and will not be reconsidered until v1.0 ships."*

**Plan also gains a `## Pickup pointer` section** at the top of `cortex-v1.md` so the next session can read in three lines what the very next concrete action is — without grepping through the full plan.

## The five codesight ideas evaluated

Source: https://github.com/Houseofmvps/codesight (~4K npm downloads; AST-based code-shape extractor that compresses 26K–47K tokens of file exploration into 3–5K tokens of pre-compiled markdown; cites Karpathy's LLM-wiki gist as inspiration).

| # | Idea | Verdict | Where it landed |
|---|---|---|---|
| 1 | Token-cost measurement (Codesight publishes 7×–17× per project) | Integrate, then council-demote | v0.9.0 opportunistic, no public-ratio commitment |
| 2 | ADR-import / `--mode knowledge` heuristics for absorbing ADRs/retros/meeting notes | Initially integrate as readiness probe; council-cut | Existing `cortex init` scan-and-absorb covers small case; standalone command deferred |
| 3 | Karpathy + codesight as named related work in PRIOR_ART/PITCH | Integrate | v1.0.0 docs refresh, council-validated |
| 4 | MCP server with typed tools (Codesight ships 13) | Defer with revisit conditions | v1.x; council added "host can't shell out" as fourth revisit condition |
| 5 | `cortex import-knowledge <path>` standalone command | Defer with revisit conditions | v1.x; council tightened condition to "fixtures from ≥2 real ADR corpora available" |

**Three ideas explicitly rejected** (documented but not in plan):
- AST/regex code-shape detection (different layer; would dilute Cortex's history-shape identity).
- Aggressive per-commit regen (Codesight's source of truth is code; Cortex's is the journal — silent regen would clobber agent + human edits).
- "Compilation as deliverable" framing (Codesight compiles code into a cheaper representation; Cortex curates which markdown to load when — different optimization target).

## The 4-reviewer council

Dispatched via `conductor call --with <provider> --brief-file /tmp/cortex-council-brief-final.md` against four providers in parallel: codex (gpt-5.4), gemini (2.5-pro), kimi (k2.6), deepseek-reasoner (r1). Brief was 227 lines: Cortex identity + bounding doctrine + current state + codesight summary + the proposed plan inline + 8 specific questions.

**Per-reviewer character (one paragraph each):**

- **Gemini (2.5-pro)** — constructive; suggested two adds (`--help` polish, version-update nudge), validated all codesight integrations, validated three-target shape, and proposed the only Tier-4 doctor de-scope (cut single-authority-rule drift + full claim-trace expansion to v1.x). Position on Touchstone-hook timing: ship inside v0.9.0.

- **Codex (gpt-5.4)** — most rigor-demanding; "I'd request changes." Five real adds (compatibility/migration gate, generated-layer contract validation, retention visibility, fresh-clone acceptance test, bare-repo degradation), five real corrections (Tier 3 overclaims "install and walk away," Tier 4 invariant claim too broad, dogfood success too subjective, "every decision/incident" sustained-work scope too compliance-heavy, Touchstone hook should land before v0.9.0 starts). Strongest cuts: subjective exit gate, token-ratio public commitment, mandatory doctrine-strengthening.

- **Kimi (k2.6)** — most aggressive cutter; the only reviewer who recommended cutting two whole work items (token-cost measurement entirely, ADR-import readiness entirely). Sharpest single contribution: SPEC-to-test traceability matrix (every normative § maps to a check or test). Other distinct adds: brew-install smoke test on clean machine, `.cortex/config.toml` schema reference doc, "There is no Tier 5" sentence in Approach. Plan-ghost cleanup of stale v0.3.0 follow-ups.

- **DeepSeek-reasoner (r1)** — most willing to dissent on doctrine; proposed shipping `cortex next --enhance` MVP at v0.9.0 gated on `Verified:` <30 days (rejected: violates Doctrine 0005 #7; the freshness-gating mechanic itself is preserved as a deferred-with-interesting-mechanism note). Distinct adds: manifest detection edge-case test fixtures (multi-manifest repos), doctrine-conflict resolution in `cortex promote` (emit `Type: conflict` Journal entries), `.cortex/config.toml` validation as doctor check (independent of Kimi's docs item, ended up combined). Position on Touchstone hook: before v0.9.0.

**Where the four diverged:**

- **Touchstone hook timing.** 2-2 split (Gemini + Kimi: inside v0.9.0; Codex + DeepSeek: before v0.9.0). **Resolved as inside v0.9.0, with canary on Cortex first** — Codex's stability concern is real but addressed by canarying on the Cortex repo before opening the three install PRs; Kimi's "the gate is exactly where to validate it" is the load-bearing argument.
- **ADR-import.** Four different positions. **Resolved as Kimi + Codex blended:** cut the formal sweep + heuristic capture; existing `cortex init` scan-and-absorb handles the small case; standalone command stays deferred.
- **Token-cost measurement.** Four different positions. **Resolved as Codex's middle ground:** keep the measurement, drop the public-ratio commitment.
- **"Simultaneous" three-target rollout.** **Resolved as Codex's clarification:** "with overlapping active use" — not literal-same-day, not staggered-with-1-week-gaps.
- **LLM `--enhance` at v0.9.0.** **Rejected DeepSeek's dissent;** Doctrine 0005 #7 holds. The Verified-freshness-gating mechanism is preserved as a deferred note.

**Where all four converged:**
- Three-target gate is the right shape; don't swap to autumn-mail.
- No Tier 5; deferred items stay deferred.
- Some form of fresh-install / compatibility validation belongs in v1.0 (different framings, same gap).
- The "I'd rather use this than hand-write" exit gate is too subjective.

## Council artifacts

Verbatim outputs were preserved at `/tmp/council-{codex,gemini,kimi,deepseek}.md` during the session but those are ephemeral. The brief at `/tmp/cortex-council-brief-final.md` is reproducible — anyone can re-run the council via `conductor call --with <provider> --brief-file <reconstructed-brief>` against the SAME plan content (now at [`plans/cortex-v1`](../plans/cortex-v1.md) post-2026-04-28T14:30 update). The reviewer-by-reviewer summaries above capture the load-bearing positions; the verbatim files are not needed going forward because the synthesis is the durable artifact.

## Why this matters for v1.0

Before this session, cortex-v1.md was stale on two axes:
1. Tier 2 + Tier 3 had shipped at v0.5.0 but the plan still showed them unchecked.
2. The plan was silent on three classes of work that are real v1.0 risks for a file-format protocol: pre-1.0 compatibility, published config schema, and SPEC-to-test traceability.

The codesight cross-pollination surfaced (1) by forcing a fresh read of the plan against external evidence. The council surfaced (2) by interrogating the plan with adversarial review.

The result is a v1.0 milestone that:
- Reflects what actually shipped (Tier 1, 2, 3 all done; Tier 4 partial).
- Adds compatibility-validation work that turns "v1.0 = ceremony" into "v1.0 = ceremony + the protocol-stability claim."
- Cuts speculative work (ADR-import readiness as a gate item, public token-ratio commitments) that would have absorbed gate budget for unclear value.
- Replaces vibe-check exit gates with behavioral criteria.
- Preserves all the deferral discipline that was already in place.

## Follow-ups (deferred)

- [ ] Run `cortex refresh-state` on this repo to regenerate `state.md` against the updated plan tier statuses (Tier 2 + 3 ✅, Tier 4 🟡 partial). Current `state.md` `## Current work` text predates this update. → resolves to operational task post-this-commit; no plan needed.
- [ ] Edit `briefs/v0.6.0-T3-doctor-invariants.md` to reflect the trimmed scope before dispatch (drop single-authority + full claim-trace; add generated-layer contract + config.toml schema + retention visibility). → resolves inside Tier 4 / v0.6.0 work; the brief edit is the first sub-task of that work item.
- [ ] On v0.9.0 dogfood completion, decide whether the Verified-freshness-gating mechanic for `cortex next --enhance` (DeepSeek's preserved idea) is worth a v1.x scoping pass, or whether external dogfood demand is what continues to gate it. → resolves to a post-v0.9.0 journal entry capturing the call.
