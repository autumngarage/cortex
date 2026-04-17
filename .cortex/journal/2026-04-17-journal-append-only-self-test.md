# Journal append-only invariant self-test — caught, recorded, fixed

**Date:** 2026-04-17
**Type:** decision
**Trigger:** T2.2 (failed attempt that taught something non-obvious before the retry)
**Cites:** journal/2026-04-17-phase-b-plan-refresh, plans/phase-b-walking-skeleton

> While iterating on the Phase B plan refresh PR (chore/refresh-phase-b-plan), the journal entry `2026-04-17-phase-b-plan-refresh.md` was created in commit `21fd9c7` and then modified in a later commit (`01eccb2`) to remove a pre-merge "merged" claim that Codex review flagged as risking a false-append-only-fact. The modification itself violated SPEC § 3.5 (Journal is append-only once written). This entry records the violation, its correct resolution, and the reinforcement to the plan.

## What happened

- Commit `21fd9c7` created `journal/2026-04-17-phase-b-plan-refresh.md` describing the plan refresh. The context section contained the phrasing "The refresh PR (`chore/refresh-phase-b-plan`, merged 2026-04-17) brings the plan up to v0.3.1-dev's command surface."
- Codex pre-merge review correctly flagged that "merged 2026-04-17" was a pre-merge claim that could become false if the PR didn't merge. Since Journal is append-only, recording an unverified claim is a durable-falsehood risk.
- Commit `01eccb2` modified the existing entry to drop the "merged" language. **This in-place modification is itself the violation** — the correct pattern per SPEC § 3.5 and § 3.1 of the Protocol is to leave the original entry unchanged and write a new entry that supersedes the claim. The quicker fix (editing in place) felt reasonable because the PR hadn't merged yet, but the file was already on a feature branch; per the spec the moment an entry is written, it's append-only.

## What I should have done

Write a new Journal entry that cited `2026-04-17-phase-b-plan-refresh.md` and said "the claim about merge status in that entry is pre-merge; treat the entry as describing PR intent, not shipped state." That's exactly what *this* entry does — except it's late by one commit.

## Correct pattern going forward

- **Feature-branch edits to Journal entries are invariant violations even pre-merge.** Branches are part of git; the entry is part of history the moment it's committed. The spec does not grant a "pre-merge grace period."
- **`cortex doctor`'s append-only Journal check (SPEC § 3.5) must be an error under `--strict`, not a warning.** If it's a warning, `doctor --strict` can pass a contract violation — which is exactly what happened here, except the enforcer was Codex, not doctor. The Phase B plan is updated in this same commit to reflect the stricter severity.
- **Pre-merge hedging goes into new entries, not existing ones.** When a claim in an entry is predicated on a future event (merge, ship, external confirmation), the entry should say so explicitly at write time. Retro-hedging is a code smell.

## Consequences / action items

- [x] `journal/2026-04-17-phase-b-plan-refresh.md` stays as written in `01eccb2` (the entry's content is now correct, even though the path to correctness violated the invariant). Attempting to revert the correction would make the content wrong *again* — an independent violation. The original violation is what this entry is for.
- [x] Phase B plan's append-only Journal check escalated from warning → error under `--strict` (this commit).
- [ ] When `cortex doctor --strict` ships in Phase B, its first run on this repo will flag the 21fd9c7→01eccb2 modification as a historical violation. The response is a warning that cites this entry; the violation is recorded, not papered over.

## What we'd do differently

This is the third invariant-violation lesson in three days — Doctrine 0004 in-place edit (fixed via supersede to 0005), Plan deferrals without resolution targets (fixed via journal entry), and now Journal entry edited in place (recorded here). The pattern: **the invariants are easy to violate by reflex, because reflex is "edit the thing that's wrong."** Cortex's value proposition is that the reflexes are *supposed* to be constrained. Until `cortex doctor --strict` ships, Codex pre-merge review is the only enforcement — and it's doing heroic work catching these in round after round of review on the same PR.

The Phase B cortex doctor --strict exit criterion is not ceremony; it's the load-bearing tool that makes the whole Cortex pitch credible. Ship it.
