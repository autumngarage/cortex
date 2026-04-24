---
Status: active
Written: 2026-04-24
Author: claude-session-2026-04-24
Goal-hash: 9e961737
Updated-by:
  - 2026-04-24T12:30 claude-session-2026-04-24 (consolidated from plans/phase-c-authoring-and-state + phase-d-integration + phase-e-synthesis-and-governance; absorbed the five case-study-driven follow-ups from journal/2026-04-24-case-study-driven-roadmap)
Cites: ../../SPEC.md, ../../.cortex/protocol.md, ../doctrine/0001-why-cortex-exists, ../doctrine/0005-scope-boundaries-v2, ../doctrine/0003-spec-is-the-artifact, ../doctrine/0002-compose-by-file-contract-not-code, journal/2026-04-23-phase-c-reordered, journal/2026-04-24-case-study-driven-roadmap, ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md
---

# Ship Cortex v1.0

> The one plan from here to v1.0. Three phases — authoring & deterministic state (v0.3.0), composition integrations (v0.4.0), synthesis & governance (v1.0.0) — tracked as sub-sections instead of separate plan files so scope lives in one place. Absorbs the phase plans cancelled 2026-04-24 and the five case-study-driven follow-ups from the same day's roadmap synthesis.

## Why (grounding)

The session-pickup gap is what Cortex exists to close ([`doctrine/0001-why-cortex-exists`](../doctrine/0001-why-cortex-exists.md)). The 2026-04-23 reorder split that work into three phase plans with per-phase `Blocked-by:` chains — structurally correct per SPEC § 3.4, but organizationally it forced every scope decision to ping between four files (PLAN.md + three phase plans) before landing anywhere. One plan file restores single-file focus without giving up the phased ordering; phases become work-item sub-sections with deterministic-first / LLM-additive posture intact per [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #7.

The five case-study items folded in under the `## Work items` phase sub-sections trace back to the conductor-repo incident ([`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`](../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md)) via [`journal/2026-04-24-case-study-driven-roadmap`](../journal/2026-04-24-case-study-driven-roadmap.md) — a stale `CLAUDE.md` ("tap planned for v0.1.0; not yet wired") steered an agent to recommend the wrong install path, when the tap had shipped eight releases earlier. The case study's structural lessons — no release-event trigger, no across-the-fourth-wall audit, no manifest freshness signal — set the shape of Phase E's work items.

## Success Criteria

This plan is done when Cortex v1.0 ships. Measurable outcomes per phase:

1. **Phase C shipped (v0.3.0).** For the week after v0.3.0 release, ≥ 80 % of new journal entries on this repo are authored via `cortex journal draft` rather than hand-written. `cortex refresh-state` is byte-identical on unchanged inputs, preserves hand-authored regions between `<!-- cortex:hand -->` markers, and runs clean under `cortex doctor`.
2. **Phase D shipped (v0.4.0).** A week of PRs on this repo produces ≥ 5 auto-drafted `pr-merged` journal entries; ≥ 1 Sentinel cycle on this repo produces an auto-drafted cycle entry. Touchstone pre-push `cortex doctor --strict` is opted in on this repo.
3. **Phase E shipped (v1.0.0).** `cortex refresh-map && cortex refresh-state --enhance && cortex doctor --strict` on a freshly-cloned Sentinel repo produces non-trivial Map/State content with clean exit; SPEC.md frozen (no `-dev` suffix); case-study items #3 (`cortex doctor --audit-instructions`) and #5 (manifest provenance) shipped; T1.10 release-event trigger wired + audited; `cortex next` deterministic MVP usable.

## Approach

**Phase ordering** from the 2026-04-23 reorder ([`journal/2026-04-23-phase-c-reordered`](../journal/2026-04-23-phase-c-reordered.md)): deterministic authoring first (write side), then integrations (so the journal fills itself from work events), then LLM synthesis + governance last (polish + spec freeze). Within each phase, ordering is implementation-level — no explicit Blocked-by between work items in the same phase.

**Dogfood gate per phase.** Exit criteria target *this* repo for Phases C and D, *a freshly-cloned Sentinel repo* for Phase E. Don't ship a phase until its dogfood gate passes.

**No LLM calls in Phase C.** Every Phase C command is deterministic — templates + git/gh introspection + frontmatter parsing. LLM synthesis is opt-in `--enhance` flags landing in Phase E, never a hard dependency ([`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #7).

**Case-study items are slotted by phase home, not by insertion date.** Items 1 (release journal type) and 2 (T1.10 trigger) land with Phase C because they're small template/protocol additions that pair naturally with `cortex journal draft`. Items 3, 4, and 5 (`--audit-instructions`, `cortex next --enhance`, manifest provenance) land in Phase E; `cortex next`'s deterministic MVP fits Phase C scope *or* stands alone as a small follow-up depending on appetite.

## Work items

### Phase C — Authoring and deterministic state (v0.3.0 target)

- [ ] **`cortex journal draft <type>`** — writes a journal entry from the matching template under `.cortex/templates/journal/`, pre-filled from `git log` + `gh pr view` context. Opens `$EDITOR` by default; `--no-edit` writes and exits with the draft path on stdout.
- [ ] **`cortex plan spawn <slug>`** — scaffolds a Plan file with seven-field frontmatter (Status, Written, Author, Goal-hash, Updated-by seeded, Cites) and all required sections per SPEC § 3.4. Title prompt computes Goal-hash per § 4.9.
- [ ] **`cortex plan status`** — per-plan completion % (checkboxes parsed) + staleness flag (active plans with last Updated-by older than 14 days and open checkboxes). `--json` emits machine-readable output.
- [ ] **`cortex refresh-state` (deterministic)** — seven-field header; auto-generated Active-Plans / Shipped-recently / Stale-now sections; hand-authored regions between `<!-- cortex:hand -->` markers survive verbatim; byte-identical output on unchanged inputs.
- [ ] **Case-study item #1** — `release` journal type + `.cortex/templates/journal/release.md` template (fields: artifact kind, location, version, release-notes link, "install-path this changes" downstream docs list). Added 2026-04-24 from the conductor case study.
- [ ] **Case-study item #2 (may land in its own PR before Phase C exit)** — T1.10 Protocol amendment ("release / distribution artifact shipped") + corresponding SPEC minor bump + `cortex doctor --audit` expansion to check release events against `journal/release.md` entries within 72 h.
- [ ] Tests — real filesystem, real git (no mocked subprocess), idempotency test on `refresh-state`, `cortex doctor` clean on this repo after each refresh.
- [ ] v0.3.0 release — tag + GitHub Release + Homebrew formula SHA update.

### Phase D — Composition integrations (v0.4.0 target, blocked on Phase C)

- [ ] **Sentinel end-of-cycle hook** (in `autumngarage/sentinel` repo) — shells out to `cortex journal draft --type sentinel-cycle` with the cycle summary piped in; graceful-degrades to a no-op when Cortex is missing.
- [ ] **Touchstone post-merge hook** (in `autumngarage/touchstone`) — shells out to `cortex journal draft --type pr-merged` on default-branch merges; opt-in per project via `.touchstone-config`.
- [ ] **Touchstone pre-push hook** — shells out to `cortex doctor --strict`; fail-loud gate on the default-branch push path; opt-in per project.
- [ ] T1.7 (Touchstone pre-merge on architecturally-significant diff) **stays deferred to Phase E** — the Tier-1 "auditable" contract requires a durable write to a staging layer (`.cortex/pending/`) that SPEC.md does not yet define; half-wiring it in Phase D would silently downgrade T1.7 to Tier-2-in-practice while leaving it Tier-1-on-paper (contract-drift pattern the 2026-04-23 reorder is explicitly avoiding).
- [ ] Graceful-degradation tests — Cortex missing, Cortex present but not opted in, Cortex present + opted in — for each hook.
- [ ] v0.4.0 release.

### Phase E — Synthesis and governance (v1.0.0 target, blocked on Phase D)

- [ ] **`cortex refresh-map`** — LLM synthesis via the `claude` CLI, with full seven-field header (Generated / Generator / Sources / Corpus / Omitted / Incomplete / Conflicts-preserved).
- [ ] **`cortex refresh-state --enhance`** — LLM prose polish layered over the Phase C deterministic core; never touches `<!-- cortex:hand -->` regions.
- [ ] **`.cortex/.index.json` writer** + **`cortex refresh-index`** — populates promotion-queue state per SPEC § 2 / § 4.7; both a standalone command and a call embedded in `refresh-map` / `refresh-state`.
- [ ] **`cortex promote <id>`** — end-to-end writer: writes Doctrine entry, updates `.index.json`, emits `Type: promotion` Journal entry.
- [ ] **`.cortex/pending/` SPEC amendment + `cortex doctrine draft` + Touchstone pre-merge hook (T1.7)** — ship together as a single unit so the Tier-1 contract is never half-satisfied.
- [ ] **`cortex doctor` expansions** — orphan-deferral (§ 4.2), append-only violation (§ 3.5), immutable-Doctrine mutation (§ 3.1), promotion-queue invariants (§ 4.7), single-authority-rule drift (§ 4.8), CLI-less-fallback warning (Protocol § 1), Tier-1 audit expansion to T1.2 / T1.3 / T1.4 / T1.6 / T1.7, full § 5.4 claim-trace in `--audit-digests`.
- [ ] **Interactive per-candidate prompts** in bare `cortex` — depends on `.index.json` writer so candidates are real.
- [ ] **Case-study item #3** — `cortex doctor --audit-instructions` — scan `CLAUDE.md` / `AGENTS.md` / `README.md` prose for claims about external artifacts (brew taps, PyPI, sibling repos, release versions, URL liveness) and verify each against reality. Needs a config primitive naming source-of-truth artifact per claim type (likely in `.cortex/config.toml` once config lands, or a frontmatter block in the audited file). Warnings by default; errors under `--strict`.
- [ ] **Case-study item #4** — `cortex next` — deterministic MVP first (walk state.md P0/P1/P2 + `## Open questions` + active-plan open checkboxes + recent `docs/case-studies/*.md`; produce ranked list with citations). `cortex next --enhance` adds LLM synthesis over the deterministic signals as a follow-up. Deterministic MVP could land in Phase C if appetite permits; stays here by default.
- [ ] **Case-study item #5** — Manifest provenance — SPEC § 4.3 extension so derived facts inside `state.md` / `doctrine/*.md` can carry a `Verified: <date>` tag per bullet. `cortex manifest` surfaces stale `Verified:` inline so agents see the freshness signal next to the fact.
- [ ] **External dogfood gate** — `cortex refresh-map && cortex refresh-state --enhance && cortex doctor --strict` on a freshly-cloned Sentinel repo; the first real test of prompts and of every SPEC § 4 rule against a repo Cortex did not author.
- [ ] v1.0.0 release — SPEC.md frozen at the shipping version; Homebrew formula + tap + GitHub Release.

## Follow-ups (deferred)

Items deliberately out of v1.0 scope, with phase-exit or version targets where applicable:

- **Cross-repo journal import** — opt-in sibling-repo release-event mirroring (`homebrew-<project>` release → journal entry in `<project>`). Depends on T1.10 landing first. Expected v1.1+ or a late-Phase-E add if the sibling-repo watcher shape falls out naturally from `--audit-instructions`.
- **Promotion enforcement automation** — v1.0 has manual promotion (human decides); automated Journal-to-Doctrine graduation gate is v1.x. Consistent with PLAN.md's retired "Known Limitations" framing.
- **Embedding / semantic retrieval** — wait until grep stops working. Deferred per [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #1.
- **Portfolio view (Lighthouse)** — `cortex across` for multi-project aggregation. Out of scope per [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md).
- **Cortex-as-protocol separation** — if a second implementation appears (e.g., a JS reader), extract SPEC.md to its own `autumngarage/cortex-spec` repo. Not needed at one implementation.
- **Single-writer assumption** — two humans or two agents writing `.cortex/` concurrently will conflict. Append-only Journal helps; a full CRDT-ish merge story is v1.x+ if it's ever needed.
- **Retrofit historical T1.9 journal entries** — `cortex doctor --audit` flagged ~14 unmatched T1.9 fires on this repo (the `pr-merged` template shipped after those merges). Backfill or mark "pre-template" is a follow-up; not on the v1.0 path.
