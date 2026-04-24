# Reranked plans/cortex-v1 for production release on a real project

**Date:** 2026-04-24
**Type:** decision
**Trigger:** T2.1 (user phrased a goal: "our goal is to aim for production release. we want to prepare to work on a real project") + T1.1 (diff touches .cortex/plans/)
**Cites:** plans/cortex-v1, journal/2026-04-24-single-plan-consolidation, journal/2026-04-24-case-study-driven-roadmap, journal/2026-04-24-v1-followups-parked, ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md, doctrine/0001-why-cortex-exists, doctrine/0005-scope-boundaries-v2, ../../SPEC.md § 4.2

> Reordered the v1.0 work-item structure from three abstract phases (C/D/E) to six release-driven sub-sections (v0.3.0 → v1.0.0) under a single forcing function: install Cortex on a real project, work for a week, no surprises. The reorder drops LLM-additive features and triad-mode infrastructure off the v1.0 path entirely (parked here for v1.x); pulls the highest-leverage deterministic items (`cortex next`, `--audit-instructions`, orphan-deferral check) earlier; and explicitly names the dogfood gate at v0.9.0.

## Context

This morning's consolidation ([`journal/2026-04-24-single-plan-consolidation`](./2026-04-24-single-plan-consolidation.md)) collapsed three phase plans into one. The phase structure inside that single plan (Phase C / Phase D / Phase E sub-sections) was preserved verbatim from the cancelled originals. The phase shape encoded an implementation-ordering principle (deterministic write → integrate → synthesize) that's still correct, but it was written without a forcing function — phases ended on internal exit bars (≥ 80 % of journal entries authored via `cortex journal draft`, etc.) that test "is this thing being used on the dogfood repo," not "is this thing production-ready for a real project."

This afternoon the user reframed the goal: *"okay our goal is to aim for production release. we want to prepare to work on a real project."* That's a different lens. ROI-on-paper rewards every high-leverage feature; production-on-real-project rewards only what's on the critical path to "install Cortex on conductor (or sigint, or whichever target), work for a week, the tool earns its keep instead of getting in the way." Most LLM features and most triad-mode features fall *off* that path, not just down it.

Three pieces of evidence aligned on the same answer:

1. **The conductor case study is the production-readiness test case.** [`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`](../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md) describes an incident where a stale `CLAUDE.md` claim ("tap planned for v0.1.0; not yet wired") confidently steered an agent to recommend the wrong install path on conductor. The fix v1.0 must ship is `cortex doctor --audit-instructions` (case-study item #3) backed by `Manifest Verified:` (item #5) — both currently in Phase E. Producing them later than the v0.5.0 trust+automation layer makes the v1.0 gate weaker.

2. **The LLM-additive features may be net-negative for production trust.** Same case study: prose polish hides staleness. `cortex refresh-state --enhance` (LLM polish over deterministic core) would re-create the conductor failure mode *inside Cortex itself* — synthesized prose over a stale base reads more authoritative than hand-edited prose, and that authority is exactly what made the agent over-trust the conductor `CLAUDE.md`. `cortex refresh-map` is less risky but solo-dev value is near-zero (the author already knows the map). Both should defer to v1.x and only ship when an external forcing function (a contributor lands; a fresh agent on a clone gets confused) demands them.

3. **Triad-mode infrastructure is a different audience.** `.cortex/pending/` + `cortex doctrine draft` + T1.7 Touchstone pre-merge hook is a SPEC change for the architecturally-significant-pre-merge workflow, which only matters when Touchstone is doing the merging. That's a narrow audience until triad mode is being actively dogfooded on the target real project. Park to v1.x.

## What we decided

[`plans/cortex-v1`](../plans/cortex-v1.md) reorganizes from three Phase C/D/E sub-sections to **six release-driven sub-sections** under `## Work items`, each one a small focused PR series:

| Release | Theme | Scope |
|---|---|---|
| **v0.3.0** | Write-side foundation | `cortex journal draft <type>` + `release` template + T1.10 amendment/audit + `cortex plan spawn` + `cortex doctor` orphan-deferral check |
| **v0.4.0** | Read-side foundation | Deterministic `cortex refresh-state` + `cortex next` MVP + `cortex plan status` |
| **v0.5.0** | Trust + automation layer | Touchstone post-merge hook + `cortex doctor --audit-instructions` + Manifest `Verified:` per-fact |
| **v0.6.0** | Lifecycle layer | `.cortex/.index.json` writer + `cortex promote` real writer + remaining `cortex doctor` invariant expansions |
| **v0.9.0** | External dogfood gate | Install on chosen real project; one week of real work; fix what surfaces |
| **v1.0.0** | Production release | SPEC freeze + documentation refresh + Homebrew formula update + GitHub Release |

**Recommended dogfood target: conductor.** Reasoning: (a) the case study is grounded there, so dogfooding directly tests whether v1.0 closes the gap that motivated this design pass; (b) conductor has known-stale `CLAUDE.md` / `README.md` claims that `cortex doctor --audit-instructions` would catch on day one — immediate evidence; (c) the user already has it checked out and is actively working on it. **Alternative: sigint** (oldest dogfood target referenced in `CLAUDE.md`); reasoning against — predates the case-study insight, so testing on it would not directly validate the conductor-class fix. Confirmable before v0.3.0 ships; the choice shapes which fields to populate in `--audit-instructions` config.

### Items moved off the v1.0 path (deferred to v1.x)

Each gets a resolution target in this entry per SPEC § 4.2 (no orphan deferrals). Numbered list because the new `## Follow-ups (deferred)` in `plans/cortex-v1` cites these positions:

1. **`cortex refresh-map` (LLM synthesis)** — was Phase E capstone. Solo author already knows the map; real value is contributor onboarding. **Revisit when:** a contributor lands on the dogfood target's repo, OR a fresh agent on a clone gets visibly confused by the map stub.

2. **`cortex refresh-state --enhance` (LLM polish over deterministic core)** — was Phase E. Conductor case-study evidence (polished prose hides staleness) makes this risky-without-clear-value. **Revisit when:** external dogfood explicitly surfaces demand AND the trust model (`--audit-instructions`, `Verified:`) has shipped, so the polish layer has guardrails.

3. **`.cortex/pending/` SPEC amendment + `cortex doctrine draft` + T1.7 Touchstone pre-merge hook (one unit)** — was Phase E. Big SPEC change for narrow triad-mode audience. **Revisit when:** triad mode is being actively dogfooded on the target project (i.e., Touchstone is doing the merging there).

4. **Sentinel end-of-cycle hook (T1.6 / journal-cycle template)** — was Phase D. Only matters when Sentinel is running on the target project. **Revisit when:** the dogfood target starts using Sentinel cycles. Hook itself is one-line shell-out; the work lives in the `autumngarage/sentinel` repo.

5. **Touchstone pre-push `cortex doctor --strict` hook** — was Phase D. Opt-in gate; nice-to-have, not blocking. **Revisit when:** the v1.x quality-of-life pass bundles Touchstone hooks for repos that want stricter gating; or earlier if the dogfood gate exposes a class of contract violations that --strict would have caught.

6. **Interactive per-candidate prompts in bare `cortex`** — was Phase E. UX polish on the promotion flow. **Revisit when:** `.index.json` has months of real promotion candidates so the UX is informed by actual data instead of synthetic examples.

7. **`cortex next --enhance` (LLM layer over deterministic MVP)** — was Phase E. The deterministic MVP ships in v0.4.0; the LLM enhancement is the same risk class as #2 (polish hides reasoning gaps). **Revisit when:** external dogfood produces a corpus rich enough that the deterministic ranking misses important signals AND the trust-layer (#2's revisit conditions) has graduated.

8. **Doctor audits for runtime-state triggers (T1.2 test failure, T1.6 Sentinel cycle, T1.7 Touchstone pre-merge)** — was Phase E (in the `cortex doctor` expansions bundle). T1.2 needs a session-state mechanism Cortex doesn't yet have; T1.6 depends on #4 above; T1.7 depends on #3. **Revisit when:** the corresponding runtime / hook infrastructure ships. Other doctor expansions (orphan-deferral, append-only, immutable-Doctrine, promotion-queue, single-authority drift, T1.4 file-deletion, claim-trace) ship across v0.3.0 and v0.6.0 on the v1.0 path.

## Consequences / action items

- [x] [`plans/cortex-v1`](../plans/cortex-v1.md) `## Work items` rewritten: 3 Phase sub-sections → 6 release sub-sections (v0.3.0, v0.4.0, v0.5.0, v0.6.0, v0.9.0, v1.0.0). `## Approach` and `## Success Criteria` rewritten to match. `## Follow-ups (deferred)` extended with the 8 newly-parked items above, each citing this entry as its resolution target.
- [x] `## Updated-by` line added to plan frontmatter recording this rerank.
- [x] `.cortex/state.md` `## Current work` rewritten: 3-bullet phase summary → 6-bullet release-roadmap summary; `Sources` + `Corpus` refreshed; `Generated:` bumped.
- [x] `CLAUDE.md` Architecture commands list updated to use version targets (v0.3.0/v0.4.0/v0.5.0/v0.6.0) instead of phase names; LLM commands annotated as deferred-from-v1.0.
- [x] `README.md` `## Status and plan` paragraph rewritten to describe the production-release sequence and the v0.9.0 dogfood gate.
- [x] `.cortex/map.md` body updated to reflect that `refresh-map` synthesis is parked, not "pending Phase E."
- [ ] Confirm dogfood target (recommend: conductor) before v0.3.0 ships. The choice surfaces in `--audit-instructions` config design and in the v0.9.0 exit-bar criteria.
- [ ] First v0.3.0 PR: smallest opening move is `release` journal template + T1.10 amendment (case-study items #1 + #2; small, splittable, closes the conductor-class trail). Keystone PR after that is `cortex journal draft <type>` since Touchstone post-merge hook (v0.5.0) shells out to it.

## What this forecloses

**The Phase C / D / E framing is over.** Future internal communication about the v1.0 path uses release versions (v0.3.0 etc.), not phase names. The phase concept was load-bearing during the 2026-04-23 reorder when it documented dependency direction (Phase D blocks on C, etc.); the release-driven shape encodes the same dependencies via sequencing without the abstraction layer. If a future plan needs phase-style abstraction (e.g., a multi-phase v2.0 effort), reintroduce it then; don't preserve it now for hypothetical reuse.

**LLM features are no longer "the v1.0 capstone."** They were positioned that way in the original Phase E framing because the project's research arc (PRIOR_ART.md, vision drafts, Doctrine 0005) treats LLM synthesis as the polish layer that turns a deterministic store into an agent-friendly store. The case-study evidence (polished prose hides staleness) makes that framing more nuanced: synthesis without trust guardrails (`--audit-instructions`, `Verified:`) re-creates the failure mode the trust guardrails exist to prevent. Sequencing trust-before-synthesis means LLM features get the guardrails they need, but it also means LLM features are no longer load-bearing for the v1.0 narrative — they are a quality-of-life enhancement for v1.x. That's a real shift in how the project tells its story; expect README/PITCH updates at v0.9.0/v1.0.0 to reflect this.

**The v0.9.0 external dogfood gate is the real release-gate, not v1.0.0.** v1.0.0 is the ceremonial freeze + documentation refresh; the *engineering* gate is v0.9.0 (does it survive contact with a real project?). If v0.9.0 surfaces structural bugs, v1.0.0 ships *after* fixing them — v0.9.0 may produce additional point releases (v0.9.1, v0.9.2) before v1.0.0 declares freeze. This matches industry convention (1.0 = stable + frozen, not 1.0 = first attempt) and makes the production-readiness story honest.
