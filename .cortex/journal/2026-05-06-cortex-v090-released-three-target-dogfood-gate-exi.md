# Cortex v0.9.0 released — three-target dogfood gate exit

**Date:** 2026-05-06
**Type:** release
**Trigger:** T1.10
**Tag:** v0.9.0
**Cites:** plans/cortex-v1, journal/2026-05-06-cortex-v083-released-installable-baseline-for-vesp, journal/2026-05-06-v090-dogfood-retrieval-validation-across-three-tar, journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared

> v0.9.0 is the engineering release-gate ship: Cortex installed on three real projects (conductor, touchstone, vesper), 9 dogfood-surfaced bugs filed and closed, fresh-clone + bare-repo fixtures permanent in CI, retrieval validated, exit-bar review declared the gate exit. Homebrew tap auto-bumped; `/opt/homebrew/bin/cortex` reports 0.9.0.

## Artifact

- **Kind:** GitHub Release + Homebrew tap
- **Location:** https://github.com/autumngarage/cortex/releases/tag/v0.9.0; `autumngarage/homebrew-cortex` formula now points at v0.9.0
- **Version:** v0.9.0
- **Tag:** v0.9.0
- **Release notes:** https://github.com/autumngarage/cortex/releases/tag/v0.9.0

## What shipped

The full post-v0.8.3 commit range: 14 commits, 9 PRs.

**Bug-fix swarm (4 PRs, 9 issues closed in ~25 min):**
- `fix(plan-status,retrieve): clear empty-corpus messages instead of silent exits` (#145) — closes #135, #136, #137.
- `feat(audit-instructions): skip template URLs and add github_releases freshness check` (#146) — closes #138, #140.
- `fix(init): inject CLI version into state.md, stub config.toml, drop false 'will prompt' under --yes` (#147) — closes #139, #142, #143.
- `fix(doctor): tighten constraint detector to skip noun-in-prose matches` (#148) — closes #141.

**Gate-closure docs and CI:**
- `docs(plan): tick v0.9.0 vesper install checkbox` (#144).
- `docs(journal): record v0.9.0 retrieval validation findings` (#149) — per-target latency, hybrid fallback, JSON contract.
- `test(acceptance): fresh-clone session-start fixture covers manifest/next/doctor` (#150) — 5 tests; Codex P1 review caught and corrected a hardcoded date.
- `test(degradation): bare-repo fixture asserts visible degradation when siblings + optional tools are absent` (#151) — 7 tests; Doctrine 0002 made testable.
- `docs(plan): close v0.9.0 dogfood gate with behavioral exit-bar review` (#152) — 7-criterion review, gate exit declared.

**Three-target install record** (kicked off the gate):
- conductor — `autumngarage/conductor#178` (12 audited claims verified after surfacing 3 upstream bugs).
- touchstone — `autumngarage/touchstone#151` (visible audit findings, 3 upstream bugs).
- vesper — `henrymodisett/vesper#167` (10 audited claims, 1 genuine stale claim caught — the conductor-case-study failure mode in the wild).

## Downstream docs this changes

- `autumngarage/homebrew-cortex` — formula `url` and `sha256` now point at v0.9.0 (auto-bumped via `release.yml` → `bump-tap` workflow).
- `.cortex/state.md` — regenerated to make v0.9.0 the current installed/released package reality.
- `.cortex/plans/cortex-v1.md` — v0.9.0 dogfood-gate items closed; remaining work is the v1.0.0 ceremony block (compatibility audit, schema reference docs already shipped, README/PITCH refresh, brew-install smoke test, doctrine review, v1.0.0 tag).
- `CLAUDE.md`, `AGENTS.md`, `README.md` — no version-string edits required; the brew-tap install path stays canonical and is not version-pinned.
- Sibling install PRs across `~/repos/conductor`, `~/repos/touchstone`, `henrymodisett/vesper` — `cortex doctor --audit-instructions` from this version benefits from cortex#138 (template URL skip) and cortex#141 (constraint detector tightening); their next dogfood pass will see fewer false positives.

## Follow-ups (deferred to future work)

- [ ] **Sustained-merge accumulation on conductor / touchstone / vesper** — each must hit ≥ 5 auto-drafted `pr-merged` entries naturally. Resolved to: `journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared` `## Known limitations`. Not a v0.9.0 blocker; documented as a carried-forward gap whose threshold attaches as merge activity accrues post-install.
- [ ] **Hybrid-mode retrieval validation against a target with `cortex[semantic]` installed** — Resolved to: `journal/2026-05-06-v090-dogfood-retrieval-validation-across-three-tar` `## Follow-ups`.
- [ ] **Brew formula `caveats` SPEC/Protocol version drift** (cortex#134, lives in `autumngarage/homebrew-cortex` tap repo, not in cortex source) — Resolved to: `homebrew-cortex#1` (the same fix in the canonical owner repo). Not a cortex-source v0.9.0 blocker; fix is a one-line edit in the formula's caveats block.

(Per SPEC § 4.2, deferred items resolve to another Plan, Journal entry, or Doctrine entry in the same commit. All three resolutions above point to existing durable layers.)
