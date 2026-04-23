# Plan phase-c-first-synthesis — active → cancelled; reordered into three successor plans

**Date:** 2026-04-23
**Type:** plan-transition
**Trigger:** T1.3
**Cites:** plans/phase-c-first-synthesis, plans/phase-c-authoring-and-state, plans/phase-d-integration, plans/phase-e-synthesis-and-governance, doctrine/0001-why-cortex-exists, doctrine/0005-scope-boundaries-v2, journal/2026-04-23-derive-success-from-preconditions

> Old `phase-c-first-synthesis` cancelled 2026-04-23 after a roadmap reflection revealed it bundled three risk classes (LLM synthesis, deterministic index + promotion writer, doctor expansions) and led with LLM synthesis before the features that actually close the session-pickup gap Cortex exists to solve. Replaced with three successor plans in priority order: `phase-c-authoring-and-state` (P0, deterministic), `phase-d-integration` (P1, Sentinel + Touchstone hooks), `phase-e-synthesis-and-governance` (P2, LLM + governance).

## Transition

- **From:** active
- **To:** cancelled
- **Reason:** Scope audit triggered by the user asking "are we on the right path." The original plan was not wrong about *what* needed to ship for v1.0 — every work item has a home in one of the successor plans. It was wrong about *ordering* and *phase granularity*. Specifically: (a) the user's stated value (rolling idea of project status, searchable history, session survivability after disconnection) is delivered by deterministic write-side primitives (`journal draft`, `plan spawn`, deterministic `refresh-state`) that don't require LLM synthesis; (b) those primitives are prerequisites for the Sentinel and Touchstone integrations that make Cortex *useful to anyone other than the author*; (c) LLM synthesis is polish that layers on top of working integrations, not the base layer. The original plan inverted this.

## What changed

**Old plan (cancelled):** one phase with 15 work items spanning LLM synthesis (`refresh-map`, `refresh-state` via `claude -p`), `.cortex/.index.json` writer, `cortex promote` writer, seven `cortex doctor` expansions, an external dogfood gate on Sentinel, and a v0.2.0 release target. The "New for Phase C" items all depended on prompt design; the "Deferred from Phase B" items mostly depended on `.index.json` being populated. Nothing in the plan shipped without either LLM or the index.

**New ordering (active):**

1. **Phase C — authoring and deterministic state** ([`phase-c-authoring-and-state`](../plans/phase-c-authoring-and-state.md), targets v0.3.0). Ship `cortex journal draft`, `cortex plan spawn`, `cortex plan status`, and a deterministic `cortex refresh-state` that walks plans + journal with a marker convention (`<!-- cortex:hand -->`) for preserving hand-authored priority sections. No LLM dependency. Byte-identical output on unchanged inputs. Exit bar: ≥ 80 % of journal entries on this repo authored via `cortex journal draft` for a week.

2. **Phase D — composition integrations** ([`phase-d-integration`](../plans/phase-d-integration.md), targets v0.4.0, blocked on Phase C). Sentinel and Touchstone hooks consume `cortex journal draft` to auto-draft entries on cycle end (T1.6) and PR merge (T1.9). For architecturally-significant pre-merge (T1.7), Touchstone renders the `doctrine/candidate.md` template pre-filled from PR context as a PR comment — no new storage layer introduced, matching the Protocol template reference without requiring a SPEC amendment to define a `pending/` layer (that amendment rides with Phase E alongside `cortex promote`). Touchstone pre-push runs `cortex doctor --strict`. Exit bar: a week of PRs produces ≥ 5 auto-drafted `pr-merged` entries and ≥ 1 auto-drafted Sentinel-cycle entry.

3. **Phase E — synthesis and governance** ([`phase-e-synthesis-and-governance`](../plans/phase-e-synthesis-and-governance.md), targets v1.0.0, blocked on Phase D). Absorbs every cancelled-Phase-C work item: LLM `refresh-map`, `refresh-state --enhance`, `.cortex/.index.json` writer, `cortex promote` writer, the seven `cortex doctor` expansion checks, Tier-1 audit expansion to T1.2-T1.7, full SPEC § 5.4 claim-trace, interactive per-candidate prompts, and the external Sentinel-clone dogfood gate. SPEC.md freezes at this phase's exit.

## Why this sequence serves the stated goal

The user's working definition of Cortex's value (given in plain words on 2026-04-23): *"a rolling idea of what the project is for, where things are at now and what is happening next; easy to search back on the past; a super extension of context tappable at any time so if you lose connection you can pick back up."*

Against that definition, the old Phase C's 15 work items sort as:

- **Two items (`refresh-map`, `refresh-state`) directly serve the goal.** `refresh-state` answers "where are we, what's next." `refresh-map` answers "what is this project" but mainly for a new contributor, not for you on a project you wrote — map drifts slowly for solo work.
- **Nine items are governance / spec-compliance.** Orphan-deferral detection, append-only-violation detection, immutable-Doctrine mutation, promotion-queue invariants, single-authority-rule drift, CLI-less-fallback warning, T1.2-T1.7 audit expansion, § 5.4 claim-trace, promote writer, `.index.json` writer. These matter when Cortex has adopters enforcing a shared spec — which is months away — and don't directly serve the session-pickup goal today.
- **One item (external Sentinel dogfood) is a validation gate** that depends on everything else in the phase.
- **No items in the old plan delivered the authoring primitives.** `cortex journal draft` and `cortex plan spawn` were in the *next* phase (old Phase D). Yet those are the bottleneck — if journal entries aren't being written, state.md is summarizing nothing, session pickup is loading nothing, search is grepping an empty corpus. The authoring primitives are the feature that makes everything downstream work.

The reorder moves the authoring primitives to P0, moves the integrations that use them to P1, and pushes the governance + LLM layer to P2 where it belongs — on top of a system that's already working for its stated goal, not as a precondition for it.

## Work item redistribution (cancelled plan → successor plans)

Per SPEC § 4.2 (deferrals resolve in the same commit), every work item in the cancelled plan maps to a specific successor plan:

| Cancelled Phase C work item | New home |
|---|---|
| `.cortex/.index.json` writer | Phase E |
| Orphan-deferral detection in doctor | Phase E |
| Append-only-violation detection in doctor | Phase E |
| Immutable-Doctrine / Status-mutation in doctor | Phase E |
| Promotion-queue invariants in doctor | Phase E |
| Single-authority-rule drift in doctor | Phase E |
| CLI-less-fallback warning in doctor | Phase E |
| T1.2–T1.7 audit expansion | Phase E (T1.7 after Phase D Touchstone hook) |
| Full § 5.4 claim-trace in audit-digests | Phase E |
| Interactive per-candidate prompts | Phase E |
| `cortex refresh-map` (LLM) | Phase E |
| `cortex refresh-state` (LLM portion) | Phase E as `--enhance` flag |
| `cortex refresh-state` (deterministic core) | Phase C (new) |
| `cortex refresh-index` | Phase E |
| `cortex promote` writer | Phase E |
| External Sentinel-clone dogfood gate | Phase E |

New work items introduced in Phase C (not from the cancelled plan): `cortex journal draft`, `cortex plan spawn`, `cortex plan status`. These were previously in old PLAN.md Phase D; the reorder pulls them forward because they're the authoring primitives that unblock everything downstream.

New work items introduced in Phase D (not from the cancelled plan): Sentinel end-of-cycle hook, Touchstone post-merge hook, Touchstone pre-merge hook, Touchstone pre-push `cortex doctor --strict` gate. These were previously in old PLAN.md Phase E; the reorder pulls them forward because they're what turns Cortex from "a thing the author uses" into "a thing that fills itself from work events."

## Consequences / action items

- [x] Cancelled-plan Status set to `cancelled` with `Promoted-to:` pointing at the three successor plans + this journal entry.
- [x] PLAN.md Phase C / D / E sections rewritten to match the new ordering.
- [x] `.cortex/state.md` P0 / P1 / P2 sections point at the new plans; frontmatter Corpus counts refreshed to reflect 3 active plans + this new journal entry.
- [x] This journal entry authored.
- [ ] Begin Phase C implementation: first slice is `cortex journal draft <type>` because it's the command whose existence changes the authoring rate and makes everything else in the phase measurable.
- [ ] Watch for scope drift during Phase C: if an implementation discovers that a deterministic `refresh-state` can't express some piece of state without LLM help, surface that as a journal entry and decide whether to (a) accept a hand-authored fallback, (b) pull the specific bit forward from Phase E, or (c) change the `<!-- cortex:hand -->` marker convention to absorb it. Do NOT silently add `claude -p` calls to a plan that promised to be deterministic.
- [ ] Retrospective check at Phase C exit: was the ≥ 80 % auto-drafted-entries bar correct? Too strict? Too loose? Adjust Phase D's dogfood gate if Phase C teaches us that a different measurement better reflects "journaling has become cheap."

## What this forecloses

The reorder makes explicit that **LLM-always is not Cortex's posture for regeneration** — `--enhance` is the opt-in flag for LLM polish, and the deterministic path is the default ([`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #7). This closes off a direction the old plan left ambiguous: "is every refresh an LLM call?" Answer, post-reorder: no. Refresh is deterministic by default; LLM is additive. Any future plan that wants to reverse this needs to supersede Doctrine 0005 explicitly, not drift it away in an implementation.

The reorder also makes explicit that **governance is not the core value-delivery path** — the promotion-queue machinery, doctor's full spec-enforcement, and promote-writer are all v1.0.0 concerns, not v0.3.0 concerns. This is consistent with PLAN.md's "Known Limitations (to be addressed in v1.x)" framing for promotion automation; the reorder brings the phase plans into alignment with that framing instead of trying to ship governance as an early feature.
