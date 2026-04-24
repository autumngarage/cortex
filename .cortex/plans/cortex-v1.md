---
Status: active
Written: 2026-04-24
Author: claude-session-2026-04-24
Goal-hash: 9e961737
Updated-by:
  - 2026-04-24T12:30 claude-session-2026-04-24 (consolidated from plans/phase-c-authoring-and-state + phase-d-integration + phase-e-synthesis-and-governance; absorbed the five case-study-driven follow-ups from journal/2026-04-24-case-study-driven-roadmap)
  - 2026-04-24T15:35 claude-session-2026-04-24 (added journal/2026-04-24-v1-followups-parked as resolution target for v1.x+ deferrals per Codex review on PR #30 + SPEC § 4.2)
  - 2026-04-24T16:30 claude-session-2026-04-24 (reranked work items from 3 Phase C/D/E sub-sections into 6 release-driven sub-sections (v0.3.0 → v1.0.0) under production-on-real-project framing; LLM features and triad-mode infrastructure deferred to v1.x per journal/2026-04-24-production-release-rerank)
Cites: ../../SPEC.md, ../../.cortex/protocol.md, ../doctrine/0001-why-cortex-exists, ../doctrine/0005-scope-boundaries-v2, ../doctrine/0003-spec-is-the-artifact, ../doctrine/0002-compose-by-file-contract-not-code, journal/2026-04-23-phase-c-reordered, journal/2026-04-24-case-study-driven-roadmap, journal/2026-04-24-single-plan-consolidation, journal/2026-04-24-v1-followups-parked, journal/2026-04-24-production-release-rerank, ../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md
---

# Ship Cortex v1.0

> The one plan from here to v1.0. Sequenced as **six release-driven sub-sections** (v0.3.0 → v1.0.0) under a single forcing function: install Cortex on a real project, work for a week, no surprises. Reranked 2026-04-24 from the original Phase C/D/E framing — LLM-additive features and triad-mode infrastructure now defer to v1.x, deterministic trust-and-audit features pull forward, and the v0.9.0 external dogfood gate is named explicitly as the real engineering release-gate.

## Why (grounding)

The session-pickup gap is what Cortex exists to close ([`doctrine/0001-why-cortex-exists`](../doctrine/0001-why-cortex-exists.md)). The 2026-04-23 reorder split that work into deterministic-first phases ([`journal/2026-04-23-phase-c-reordered`](../journal/2026-04-23-phase-c-reordered.md)). The 2026-04-24 consolidation collapsed three plan files into one ([`journal/2026-04-24-single-plan-consolidation`](../journal/2026-04-24-single-plan-consolidation.md)). Today's rerank ([`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md)) sequences the surviving work into release-sized slices oriented toward production use on a real project, drops LLM polish and triad-mode infra off the v1.0 path, and pulls the highest-leverage trust/audit features earlier.

The conductor case study ([`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`](../../docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md)) is the production-readiness test case: a stale `CLAUDE.md` claim ("tap planned for v0.1.0; not yet wired") confidently steered an agent to recommend the wrong install path on conductor. v1.0 is done when Cortex on conductor (or the chosen alternative) makes that incident structurally impossible — the trust layer (`--audit-instructions`, `Verified:`) catches the stale claim, the write layer (`cortex journal draft`, T1.10) records when reality changed, and the lifecycle layer (`promote`, `index`) graduates load-bearing observations to durable doctrine.

## Success Criteria

This plan is done when Cortex v1.0 ships into real-project use. Measurable per release:

1. **v0.3.0 — write-side foundation.** ≥ 80 % of new journal entries on this repo authored via `cortex journal draft` for the week after release; `cortex doctor` (with the orphan-deferral check shipped this release) passes on this repo with no waivers; T1.10 trigger fires on the next Cortex tag and the corresponding `release` journal entry is auto-detected by `cortex doctor --audit`.
2. **v0.4.0 — read-side foundation.** `cortex refresh-state` byte-identical on unchanged inputs (idempotency test); `cortex next` produces a non-empty ranked list on this repo with stable citations; `cortex plan status` flags any active plan staler than 14 days.
3. **v0.5.0 — trust + automation layer.** A week of PRs on this repo produces ≥ 5 auto-drafted `pr-merged` entries via the Touchstone post-merge hook; `cortex doctor --audit-instructions` on this repo produces clean exit, on conductor (or chosen target) produces non-trivial findings; manifest output surfaces stale `Verified:` timestamps inline.
4. **v0.6.0 — lifecycle layer.** `.cortex/.index.json` writer produces a populated index on this repo; end-to-end promotion (journal observation → doctrine entry via `cortex promote <id>`) succeeds with `Type: promotion` Journal entry written; remaining doctor invariant checks (append-only, immutable-Doctrine, promotion-queue, single-authority drift, T1.4, claim-trace) pass on this repo.
5. **v0.9.0 — external dogfood gate.** Cortex installed on chosen real project; one week of real work using `cortex journal draft` for new entries; `cortex doctor --audit-instructions` non-trivially exercised; ≥ 1 case-study-style journal entry captured for surfaced friction; zero crashes; user assessment "I'd rather use this than hand-write."
6. **v1.0.0 — production release.** `SPEC.md` frozen (no `-dev` suffix; bumped to 1.0.0); README / PITCH refreshed to tell the production-ready story; Homebrew formula updated; GitHub Release covering the full v0.3.0 → v1.0.0 arc.

## Approach

**Critical-path framing.** Every v1.0 work item answers yes to: *does this make Cortex usable on a real project today?* Items that don't (LLM polish, triad-mode infra, low-frequency UX) move to `## Follow-ups (deferred)` with explicit revisit conditions per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md). The bar is not "would this be valuable" — almost everything would; the bar is "does production-on-a-real-project depend on it."

**Deterministic only on the v1.0 path.** Per [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #7, LLM features were already opt-in `--enhance` flags. The 2026-04-24 rerank takes this further: LLM commands (`refresh-map`, `refresh-state --enhance`, `cortex next --enhance`) defer to v1.x because the conductor case study evidence is that prose polish *hides* staleness — synthesis over a stale base would re-create the conductor incident inside Cortex itself. Trust-layer features (`--audit-instructions`, `Verified:`) ship first; LLM polish gets reconsidered only after the trust layer has dogfooded.

**Real-project target chosen now, dogfooded at v0.9.0.** Recommended target: **conductor** — the case study is grounded there, conductor has known-stale `CLAUDE.md` / `README.md` claims that exercise `--audit-instructions` on day one, and the user already has it checked out and is actively working on it. Alternative: sigint (oldest dogfood target referenced in `CLAUDE.md`, but predates the case-study insight). Confirmable before v0.3.0 ships; the choice shapes which fields to populate in `--audit-instructions` config and which doctor invariants get exercised under load.

**v0.9.0 is the engineering release-gate, not v1.0.0.** v1.0.0 is the ceremonial freeze + documentation refresh; v0.9.0 is "does it survive contact with a real project." If v0.9.0 surfaces structural bugs, point releases (v0.9.1, v0.9.2) ship before v1.0.0 declares freeze. This matches industry convention (1.0 = stable + frozen, not 1.0 = first attempt) and keeps the production-readiness story honest.

**Release granularity.** Five minor releases between v0.3.0 and v0.9.0 plus v1.0.0 — each release is a small, focused PR series (typically 3-5 work items). Within a release, work items are sequenceable in any order as long as the exit bar passes. Cross-release ordering matters: v0.5.0's Touchstone post-merge hook depends on v0.3.0's `cortex journal draft`; v0.6.0's `cortex promote` real writer depends on the same release's `.index.json` writer; v0.9.0 depends on everything before it.

## Work items

### v0.3.0 — Write-side foundation

Goal: the user can write journal entries and spawn plans cheaply, and the contract enforcement that would have caught the orphan-deferral bug on PR #30 ships with the release.

- [ ] **`cortex journal draft <type>`** — writes a journal entry from the matching template under `.cortex/templates/journal/`, pre-filled from `git log` + `gh pr view` context. Opens `$EDITOR` by default; `--no-edit` writes and exits with the draft path on stdout. Handles `gh` not installed / not authenticated: degrade to `git log`-only pre-fill with a one-line warning, never block.
- [ ] **`release` journal type + `.cortex/templates/journal/release.md` template** (case-study item #1) — fields: artifact kind (tap / PyPI / Docker / tag), artifact location, release version, release-notes link, "install-path this changes" downstream-docs list. Template-only; pairs with the T1.10 audit shipped in the same release.
- [ ] **T1.10 Protocol amendment + SPEC.md minor bump + `cortex doctor --audit` expansion** (case-study item #2) — Add `T1.10: Release / distribution artifact shipped` to `.cortex/protocol.md` § 2 with `journal/release.md` as its template. SPEC bumps to v0.3.2-dev so accepted Protocol versions include 0.2.1. Audit walks `git tag --list --sort=-creatordate` for the window and matches each tag against a `Type: release` journal entry within 72 h. May land in its own small PR before the keystone `cortex journal draft` PR.
- [ ] **`cortex plan spawn <slug>`** — scaffolds a Plan file with seven-field frontmatter (Status, Written, Author, Goal-hash, Updated-by seeded, Cites) and all required sections per SPEC § 3.4. Title prompt computes Goal-hash per § 4.9 (reuses existing `cortex.goal_hash.normalize_goal_hash`).
- [ ] **`cortex doctor` orphan-deferral check** — scans every active Plan's `## Follow-ups (deferred)` section; warns when any item lacks a citation to another Plan or a Journal entry per SPEC § 4.2. Errors under `--strict`. Would have caught the round-1 finding on PR #30.
- [ ] Tests — real filesystem, real git (no mocked subprocess), template-presence check, `cortex doctor` orphan-check unit tests against synthetic plan files, T1.10 audit test against a real `git init`'d repo with tags.
- [ ] v0.3.0 release — version bump, tag, GitHub Release, Homebrew formula SHA update.

### v0.4.0 — Read-side foundation

Goal: state.md stays current automatically and the user gets a deterministic "what to work on" command. No LLM dependency.

- [ ] **Deterministic `cortex refresh-state`** — seven-field header regenerated; auto-generated `## Active plans`, `## Shipped recently`, `## Stale-now / handle-later` sections walked from plans + journal; hand-authored regions between `<!-- cortex:hand -->` / `<!-- cortex:end-hand -->` markers survive verbatim; byte-identical output on unchanged inputs (idempotency test). Marker convention decision (per-section pairs vs. one outer pair) finalized at first implementation; lean per-section.
- [ ] **`cortex next` deterministic MVP** (case-study item #4) — walks `.cortex/state.md` `## Current work` section + `## Open questions` + active-plan open checkboxes (reuses `cortex plan status` parser) + `docs/case-studies/*.md` newer than N days (default 30); produces a ranked list with stable citations to each source. `--json` for scripting. The LLM-enhanced layer (`--enhance`) is parked at item #7 in [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) and ships post-v1.0.
- [ ] **`cortex plan status`** — per-plan completion % (checkboxes parsed) + staleness flag (active plans with last `Updated-by` older than 14 days and open checkboxes). `--json` emits machine-readable output. Used directly by `cortex next` and by `cortex doctor`.
- [ ] Tests — `refresh-state` idempotency test, marker-preservation test, `cortex next` ranked-output test against fixture state.md, `cortex plan status` against synthetic plan files.
- [ ] v0.4.0 release.

### v0.5.0 — Trust + automation layer

Goal: external claims stay honest; the journal fills itself from real merge events; the trust model the conductor case study demands is in place before any LLM polish gets considered.

- [ ] **Touchstone post-merge hook** (in `autumngarage/touchstone`) — shells out to `cortex journal draft --type pr-merged --no-edit` on default-branch merges; opt-in per project via `.touchstone-config`. Cortex side is graceful degradation: if `cortex` not on PATH, hook is a no-op with a one-line stderr note. Touchstone PR is a separate ship; Cortex side is the `--no-edit` flag and any glue needed for piped-context input.
- [ ] **`cortex doctor --audit-instructions`** (case-study item #3) — scans `CLAUDE.md` / `AGENTS.md` / `README.md` for claims about external artifacts (filesystem siblings via `~/Repos/<name>` checks, Homebrew taps via `brew tap-info`, PyPI packages via `pip show` or HTTPS HEAD on the index, `gh release list` for tap/main repos, URL liveness HEAD checks); verifies each against reality. Configurable via `.cortex/config.toml` section `[audit-instructions]` naming source-of-truth artifacts per project (e.g., `homebrew_tap = "autumngarage/cortex"`). Warnings by default; errors under `--strict` so Touchstone pre-push (deferred — v1.x) can use it later. Two non-goals carried from the case study: does not police narrative/principles prose; does not require release-per-commit hygiene.
- [ ] **Manifest provenance — per-fact `Verified:` per SPEC § 4.3 extension** (case-study item #5) — SPEC minor bump so derived facts inside `state.md` / `doctrine/*.md` can carry a `Verified: <date>` tag on individual bullets (not just the whole file's `Generated:` timestamp). `cortex manifest` surfaces stale `Verified:` warnings inline so agents see "this fact was last verified 180 days ago" next to the fact itself. Pairs with `--audit-instructions` (active check) as the passive freshness signal.
- [ ] Tests — `--audit-instructions` against a fixture with known-stale claims, `Verified:` parser unit tests, end-to-end test that `cortex manifest` warns inline. Touchstone-side hook tested in the touchstone repo.
- [ ] v0.5.0 release (Cortex side); separate Touchstone release.

### v0.6.0 — Lifecycle layer

Goal: journal observations can graduate to doctrine via a real promotion flow; the contract-enforcement story is complete.

- [ ] **`.cortex/.index.json` writer + `cortex refresh-index`** — populates promotion-queue state per SPEC § 2 / § 4.7; both a standalone command and an embedded call in `cortex journal draft` (when a draft hits configurable signal patterns) and `cortex refresh-state`. JSON shape per SPEC § 2: candidate id, source path, last-touched, age, tags.
- [ ] **`cortex promote <id>` (real writer)** — end-to-end promotion: reads candidate from `.index.json`, writes Doctrine entry from template with `Promoted-from:` set, updates index to mark candidate promoted, emits `Type: promotion` Journal entry. Replaces today's stub which only validates `.index.json` presence and exits 3.
- [ ] **Remaining `cortex doctor` invariant expansions** — append-only Journal violation (§ 3.5), immutable-Doctrine mutation (§ 3.1), promotion-queue invariants (§ 4.7 — depends on `.index.json` writer being available), single-authority-rule drift (§ 4.8), CLI-less-fallback warning (Protocol § 1), T1.4 file-deletion audit (was missed by the orphan-deferral-only check shipped in v0.3.0), full § 5.4 claim-trace audit in `--audit-digests` (currently first-slice only). T1.2 / T1.6 / T1.7 audits stay deferred (parked at item #8 in [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) — they need runtime session state or hooks that ship post-v1.0).
- [ ] Tests — promotion round-trip on real `.index.json`, doctor invariant checks against synthetic violations, claim-trace test on a fixture digest.
- [ ] v0.6.0 release.

### v0.9.0 — External dogfood gate

Goal: install Cortex on a real project, do a week of real work, fix what surfaces. This is the engineering release-gate. v1.0.0 is ceremony; v0.9.0 is the truth.

- [ ] **Confirm dogfood target** (recommend: conductor; alternative: sigint). Decision posted as a Journal entry of `Type: decision` before installation.
- [ ] **Install Cortex on the target.** `cortex init` with the target's existing `CLAUDE.md` / `AGENTS.md` / `README.md` scanned-and-absorbed (uses v0.2.2's scan-and-absorb). Configure `.cortex/config.toml` `[audit-instructions]` section with the target's source-of-truth artifacts. First-pass `cortex doctor --audit-instructions` run; capture findings as a journal entry.
- [ ] **One week of real work on the target using Cortex.** Every decision / incident / merge / release goes through `cortex journal draft <type>`. `cortex next` consulted at every session start. `cortex doctor` run before every push.
- [ ] **Surface bugs and friction in real-time.** Each significant friction point (≥ 5 minutes of extra work, or any crash) gets a journal entry of `Type: incident` or `Type: decision` on the dogfood target's `.cortex/`, mirrored back to this repo's `docs/case-studies/` if it reveals a structural issue. Bug fixes ship as v0.9.x point releases.
- [ ] **Exit-bar review.** At end of week: zero crashes; non-trivial output from each command on the target's data; user assessment "I'd rather use this than hand-write." If any criterion fails, scope a fix-and-retry pass before declaring v0.9.0 exit.
- [ ] v0.9.0 release.

### v1.0.0 — Production release

Goal: ceremonial freeze. SPEC.md stops moving; documentation tells the v0.3.0 → v1.0.0 production story.

- [ ] **SPEC.md freeze.** Drop `-dev` suffix; bump version to 1.0.0; final review pass to ensure no internal `TODO` / `FIXME` references; tag corresponding `cortex` CLI release as the first version that supports SPEC v1.0 (in `SUPPORTED_SPEC_VERSIONS` per `src/cortex/__init__.py`).
- [ ] **Documentation refresh.** README.md `## Status and plan` rewritten from "in development" framing to "production-ready" framing. PITCH.md updated with the dogfood-validated story. New `docs/CASE-STUDIES.md` index (or expanded existing case-studies dir) covering the conductor incident + the dogfood-gate findings.
- [ ] **Doctrine review.** Walk all 5 active Doctrine entries; if v0.9.0 evidence supports promoting any of them to a stronger formulation, write the supersede entry. If new doctrine emerged from dogfood, draft + promote it.
- [ ] **Homebrew formula update + GitHub Release.** Tag v1.0.0; update `autumngarage/homebrew-cortex` formula `url` + `sha256`; GitHub Release notes covering the full v0.3.0 → v1.0.0 arc with grounding in the case study.
- [ ] **Announce.** Wherever the autumngarage trio gets discussed (Sentinel + Touchstone + Cortex composition story).

## Follow-ups (deferred)

Items deferred from the v1.0 path. Each resolves to a Doctrine entry (for items already scoped out) or to one of two journal entries: [`journal/2026-04-24-v1-followups-parked`](../journal/2026-04-24-v1-followups-parked.md) (consolidation-era parks) or [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) (production-rerank parks). Per SPEC § 4.2, every deferral has an in-tree resolution target — no orphans.

### Newly deferred from the v1.0 path (2026-04-24 rerank)

- **`cortex refresh-map` (LLM synthesis)** — was Phase E capstone. Solo author already knows the map; real value is contributor onboarding. Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #1; revisit when a contributor lands on the dogfood target's repo or a fresh agent on a clone gets visibly confused by the map stub.
- **`cortex refresh-state --enhance` (LLM polish over deterministic core)** — was Phase E. Conductor case-study evidence (polished prose hides staleness) makes this risky-without-clear-value. Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #2; revisit when external dogfood explicitly surfaces demand AND the trust layer has shipped.
- **`.cortex/pending/` SPEC amendment + `cortex doctrine draft` + T1.7 Touchstone pre-merge hook (one unit)** — was Phase E. Big SPEC change for narrow triad-mode audience. Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #3; revisit when triad mode is being actively dogfooded on the target.
- **Sentinel end-of-cycle hook (T1.6)** — was Phase D. Only relevant when Sentinel is running on the target. Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #4; revisit when the dogfood target starts using Sentinel cycles. Hook itself is one-line shell-out; the work lives in the `autumngarage/sentinel` repo.
- **Touchstone pre-push `cortex doctor --strict` hook** — was Phase D. Opt-in gate; nice-to-have but not blocking. Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #5; bundle with v1.x Touchstone hooks pass, or earlier if the dogfood gate exposes a class of contract violations that `--strict` would have caught.
- **Interactive per-candidate prompts in bare `cortex`** — was Phase E. UX polish on promotion flow. Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #6; revisit once `.index.json` has months of real promotion candidates so the UX is informed by actual data.
- **`cortex next --enhance` (LLM layer over deterministic MVP)** — was Phase E. Same risk class as `refresh-state --enhance`. Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #7; revisit when external dogfood produces a corpus rich enough that deterministic ranking misses important signals AND the trust layer has graduated.
- **Doctor audits for runtime-state triggers (T1.2 test failure, T1.6 Sentinel cycle, T1.7 Touchstone pre-merge)** — was Phase E (in the doctor expansions bundle). Parked per [`journal/2026-04-24-production-release-rerank`](../journal/2026-04-24-production-release-rerank.md) #8; revisit when the corresponding runtime / hook infrastructure ships.

### Previously parked (v1.x or later)

- **Cross-repo journal import** — opt-in sibling-repo release-event mirroring (`homebrew-<project>` release → journal entry in `<project>`). Depends on T1.10 landing first. Parked per [`journal/2026-04-24-v1-followups-parked`](../journal/2026-04-24-v1-followups-parked.md) #1; revisit when `--audit-instructions` is dogfooded.
- **Promotion enforcement automation** — v1.0 has manual promotion (human decides via `cortex promote <id>`); automated Journal-to-Doctrine graduation gate is v1.x. Parked per [`journal/2026-04-24-v1-followups-parked`](../journal/2026-04-24-v1-followups-parked.md) #2; revisit once `.index.json` writer has produced ≥ 30 days of real promotion-queue data.
- **Embedding / semantic retrieval** — wait until grep stops working. Resolved by [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #1 (out of scope at the storage layer).
- **Portfolio view (Lighthouse)** — `cortex across` for multi-project aggregation. Resolved by [`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) (out of scope).
- **Cortex-as-protocol separation** — if a second implementation appears (e.g., a JS reader), extract SPEC.md to its own `autumngarage/cortex-spec` repo. Parked per [`journal/2026-04-24-v1-followups-parked`](../journal/2026-04-24-v1-followups-parked.md) #3; revisit when a second implementation is proposed.
- **Single-writer assumption** — two humans or two agents writing `.cortex/` concurrently will conflict on the same file. Parked per [`journal/2026-04-24-v1-followups-parked`](../journal/2026-04-24-v1-followups-parked.md) #4; revisit on first concurrent-write conflict in practice.
- **Retrofit historical T1.9 journal entries** — `cortex doctor --audit` flagged ~14 unmatched T1.9 fires on this repo (the `pr-merged` template shipped after most of those merges). Parked per [`journal/2026-04-24-v1-followups-parked`](../journal/2026-04-24-v1-followups-parked.md) #5; revisit if/when historical entries become load-bearing for any synthesis.
