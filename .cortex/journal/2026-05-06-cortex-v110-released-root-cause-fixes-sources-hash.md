# Cortex v1.1.0 released — root-cause fixes (Sources-hash, doctrine intro baseline, --strict, trailer audit)

**Date:** 2026-05-06
**Type:** release
**Trigger:** T1.10
**Tag:** v1.1.0
**Cites:** plans/cortex-v1, journal/2026-05-06-cortex-v100-released-production-freeze-5-target-do, journal/2026-05-06-pre-10-compatibility-audit-v03-and-v05-forward-com, doctrine/0001-why-cortex-exists, doctrine/0003-spec-is-the-artifact

> v1.1.0 is the **first post-1.0 minor release**, shipping four root-cause fixes for issues surfaced during the v1.0 dogfood pass: hash-based source tracking replaces mtime for staleness checks (#171), doctrine append-only baselines at file-introduction (#172), `cortex doctor --audit-pr-trailers` enforces the issue-closing trailer convention (#173), and `cortex doctor --strict` makes warnings CI-blocking (#174). SPEC bumped 1.0.0 → 1.1.0 (additive minor per § 6).

## Artifact

- **Kind:** GitHub Release + Homebrew tap
- **Location:** https://github.com/autumngarage/cortex/releases/tag/v1.1.0; `autumngarage/homebrew-cortex` formula now points at v1.1.0
- **Version:** v1.1.0
- **Tag:** v1.1.0
- **Release notes:** https://github.com/autumngarage/cortex/releases/tag/v1.1.0

## What shipped

Four root-cause fixes plus their SPEC and config-schema knock-on changes. Each fix was filed with explicit root-cause analysis (surface symptom → root cause → fundamental fix → trade-offs → engineering principles applied) before any code was written. The user's framing for this round: "we spot something wrong, think hard about the root issue, fix it fundamentally."

### Fix #171 — Hash-based source tracking (PR #178)

**Surface symptom:** Transient `state.md generated before source changed` warning fired ~30% of the time after `cortex refresh-state` succeeded.

**Root cause:** Doctor used filesystem **mtime** as a proxy for content-equality. mtime is perturbed by editor saves (no content change), git checkouts (touches all files), sub-second races between refresh-state writes and sibling processes.

**Fundamental fix:** state.md frontmatter now embeds a `Sources-hash:` block listing SHA-256 per source. Doctor's staleness check compares hashes; mtime stays as a fallback when `Sources-hash:` is absent (compat with v0.5 scaffolds).

**Engineering principle applied:** *Derive-don't-persist*. mtime is persisted noisy state; content hash is derived from the source of truth.

### Fix #172 — Doctrine append-only file-introduction baseline (PR #176)

**Surface symptom:** 3 persistent warnings about commit `866be5764448` modifying doctrines 0001/0002/0003 — un-resolvable without history rewrite.

**Root cause diagnosis (from PR #176):** `866be57…` was a real M (modification) commit that backfilled `Load-priority: always` on the four doctrines BEFORE the immutable-Doctrine invariant existed. Both layers needed:

- **Layer 1:** Per-doctrine invariant correctly stated as "no modifications between file's introduction commit and HEAD." Mechanically: `git log --diff-filter=A --follow` finds intro; `git log --diff-filter=M intro_sha..HEAD` finds real modifications.
- **Layer 2:** `[doctrine.append-only].grandfather-commits` config field acknowledges the pre-invariant drift explicitly. Adding a SHA here MUST come with a same-commit Journal entry explaining why.

**Engineering principle applied:** *Think in invariants*. The actual invariant is "doctrine content doesn't change after the entry exists." The previous check stated a coarser invariant that fired false positives.

### Fix #173 — Issue-closing trailer enforcement (PR #177)

**Surface symptom:** Multiple PRs this session left referenced issues OPEN despite naming them in title/body, requiring manual issue-close cleanup. (PRs #146, #169 both hit this.)

**Root cause:** The `Closes-issue:` trailer convention was documented in CLAUDE.md but **advisory, not load-bearing**. Agents routinely forgot it; `open-pr.sh` only injected from commit trailers (didn't scan PR title/body); GitHub auto-close needs body keywords specifically.

**Fundamental fix (cortex-side, layers 1 + 3):**

- **Layer 1:** New `cortex doctor --audit-pr-trailers` check warns when a branch references issues without matching `Closes-issue:` trailers.
- **Layer 3:** `cortex install-brief --closes <N,N,N>` flag prefills the trailer convention into generated briefs.
- **Layer 2 (touchstone-side):** filed as touchstone#180 — `open-pr.sh` should also scan PR title/body for references and inject trailers.

**Engineering principles applied:** *No silent failures* (missing trailers fail silently today; the audit makes it visible). *Make irreversible actions recoverable* (catching missing trailers pre-merge is cheap; manually closing issues post-merge is the cost without the check).

### Fix #174 — `cortex doctor --strict` flag (PR #175)

**Surface symptom:** `cortex doctor: 0 errors, N warnings` exits 0 even when warnings include action-required findings (stale state.md, audit findings, append-only violations). CI/merge-gate flows can't reliably differentiate.

**Root cause:** Severity binary (error/warning) collapses two orthogonal questions: "correctness violation?" vs "block CI?" Some warnings are FYI; some are conditional blockers; binary lumps them.

**Fundamental fix (smaller path, smaller PR):** `cortex doctor --strict` exits 1 on any non-info finding. Default exit semantics unchanged — opt-in for CI. Three-tier severity (`info`/`warning`/`error`) deferred per the issue's recommendation; needs a classification audit of every existing check and travels separately.

**Touchstone follow-up filed:** touchstone#179 — `touchstone-run.sh validate` may want to migrate to `cortex doctor --strict` once shipped.

**Engineering principle applied:** *One code path*. `--strict` is the explicit knob; CI and interactive flows share doctor but use different exit semantics via the flag.

## Downstream docs this changes

- `autumngarage/homebrew-cortex` — formula `url` and `sha256` auto-bumped to v1.1.0.
- `.cortex/state.md` — refreshed pre-release; the new `Sources-hash:` block is visible.
- `SPEC.md` — bumped 1.0.0 → 1.1.0 (additive minor per § 6); § 4.3 documents `Sources-hash:`; the doctrine append-only invariant is restated correctly.
- `docs/config-reference.md` — new `[doctrine.append-only]` section documents `grandfather-commits`.
- Five sibling install repos (conductor, touchstone, vesper, sentinel, vanguard) — their next `cortex doctor` runs will see the same warning reductions on the post-v1.0 brew binary.
- Two filed touchstone follow-ups: touchstone#179 (`--strict` integration) and touchstone#180 (`open-pr.sh` PR-body trailer injection).

## Verification post-release

`/opt/homebrew/bin/cortex --version` reports `1.1.0`. `cortex doctor` on this repo: 6 warnings → 3 warnings. The remaining 3 are: optional semantic-retrieval extras absent (info-level), map.md staleness (refresh-map deferred to v1.x), and state.md staleness from the post-release auto-drafted pr-merged entry — all explainable.

## Self-dogfood signal

The root-cause swarm itself ran with `Closes-issue: #N` trailers in the briefs and (mostly) in the resulting commits. PR #178 / #176 / #175 / #177 all auto-closed their issues correctly. **This release is the trailer-convention's first round-trip** — the convention's enforcement (PR #177) shipped in the same release that demonstrated it works (the others). The dogfood loop is tightening.

## Follow-ups (deferred to future work)

- [ ] **Three-tier severity (`info`/`warning`/`error`)** — Resolved to: future v1.x feature work; classification audit of every existing doctor check is the bulk of it. Filed in cortex#174's "deeper fix" framing.
- [ ] **Touchstone-side `open-pr.sh` PR title/body trailer injection (cortex#173 layer 2)** — Resolved to: touchstone#180.
- [ ] **`touchstone-run.sh validate` migrate to `cortex doctor --strict`** — Resolved to: touchstone#179.

(Per SPEC § 4.2, deferred items resolve to another Plan, Journal entry, or Doctrine entry in the same commit. All three resolutions above point to existing durable issue-tracker entries.)
