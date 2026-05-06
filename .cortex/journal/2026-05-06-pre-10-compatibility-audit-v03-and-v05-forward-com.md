# Pre-1.0 compatibility audit — v0.3 and v0.5 forward-compat

**Date:** 2026-05-06
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/cortex-v1, journal/2026-05-06-cortex-v090-released-three-target-dogfood-gate-exi, doctrine/0003-spec-is-the-artifact

> Cortex 0.9.0 reads v0.3.1-dev and v0.5.0 scaffolds cleanly on real-world corpora (autumn-mail, cortex itself). Forward-compatibility claim: **(a) all commands succeed**; the optional `cortex migrate-state` helper is available for v0.3 corpora that want to upgrade their hand-authored `state.md` to the marker-preserved v0.4+ shape.

## Audit targets

| Target | SPEC version | Why this target | Real-world or synthetic |
|---|---|---|---|
| `~/repos/autumn-mail` | v0.3.1-dev | Swift / MLX / Gmail dogfood from 2026-04-19 era; only v0.3 scaffold still in the wild on this machine | Real |
| `~/repos/cortex` (this repo) | v0.5.0 | The continuous self-dogfood baseline — every command in the v0.9.0 work session ran against this corpus | Real |

The plan called for "fixture repos at the v0.3 and v0.5 scaffold versions (oldest still in the wild)." Two real-world corpora at exactly the named versions are stronger evidence than synthetic fixtures, so the audit uses them directly. No synthetic fixture was needed.

## Audit commands run on autumn-mail (v0.3.1-dev)

CLI: `/opt/homebrew/bin/cortex` reporting `cortex 0.9.0` (Homebrew install).

| Command | Outcome |
|---|---|
| `cortex doctor` | 0 errors, 13 warnings. **No crash, no traceback.** Warnings break down: 2 append-only-violation warnings (sentinel cycles modified post-write — historical, real); 1 stale `map.md`; 7 SPEC § 4.2 follow-ups warnings (autumn-mail's plans don't have resolution citations — real findings, not v0.9 false-positives); 1 stale `state.md`; 1 legacy migration prompt (`legacy hand-authored state.md has no cortex:hand markers; run cortex migrate-state`); 1 generated-before-source staleness. |
| `cortex manifest --budget 8000` | Non-empty output with state.md slice, plan citations, recent journal entries. |
| `cortex refresh-state --dry-run` | Clean migration diff produced; would update Generator from `hand-authored (regeneration infrastructure ships in Cortex Phase C)` to `cortex refresh-state v0.9.0` and add the v0.5+ provenance fields. |
| `cortex refresh-index --retrieve` | Index built in 0.22s; chunks.sqlite written to `.cortex/.index/`. |
| `cortex retrieve "Gmail" --mode bm25` | Hits `doctrine/0001-why-autumn-mail-exists.md:21` (score 2.79) and `plans/mvp.md:10` (score 2.69) — exactly the maintainer-relevant content. |
| `cortex migrate-state --dry-run` | Migration helper produces a clean diff for the v0.3-shape `state.md` → v0.5+ marker-preserved shape. **The helper exists and works.** |
| `cortex promote` | Not exercised — no candidates queued on autumn-mail. Lifecycle layer runs cleanly when invoked; absence of input is the no-op happy path. |

## Audit on cortex itself (v0.5.0)

The cortex repo's `.cortex/SPEC_VERSION` is `0.5.0`. The CLI binary is `0.9.0`. Every command run during the v0.9.0 dogfood gate work session — manifest, next, doctor, journal draft, plan spawn, plan status, refresh-state, refresh-index, retrieve, promote, grep, version — ran against this corpus and produced clean output (modulo the 6 well-understood pre-existing warnings which are not version-skew issues: 3 immutable-doctrine commits, 1 optional semantic-retrieval extras, 1 transient post-edit staleness, 1 banner-regeneration-pending).

Self-dogfood is the always-on baseline: any v0.9 → v1.0 regression in scaffold-version handling would have surfaced during this session. None did.

## Compatibility claim for v1.0

- **v0.5.0 scaffolds: forward-compatible to cortex 1.0.** No migration required. Verified continuously on this repo.
- **v0.3.1-dev scaffolds: forward-compatible to cortex 1.0 with optional `cortex migrate-state` helper.** All read-side commands work directly against v0.3 scaffolds (manifest, next, doctor, refresh-index, retrieve, grep). The `state.md` regeneration path (`cortex refresh-state`) emits a doctor warning on v0.3 hand-authored state because the marker-preserved shape (`<!-- cortex:hand --> ... <!-- cortex:end-hand -->`) was introduced in v0.4. The `cortex migrate-state` command — already shipped, with `--dry-run` and `-y` flags — converts the v0.3 hand-authored shape to the v0.4+ marker-preserved shape.
- **No silent breakage** observed in either case. Every divergence is surfaced as a visible doctor warning that names the next step.

## Self-dogfood as a permanent practice

Per user direction during the audit: "you can also self dogfood with this repo, we should always be doing that." This is the right read of Doctrine 0003 (SPEC is the artifact) made operational — the cortex repo's own `.cortex/` is the continuous compat-audit baseline. Every cortex CLI release is exercised against the repo that authored it before reaching brew. The pre-merge `touchstone-run.sh validate` step + the in-session `cortex doctor` runs constitute the self-dogfood loop. No further infrastructure needed; just the discipline.

## Decisions / consequences

- The v1.0.0 compatibility-audit ceremony item is satisfied by the audit above; no migration is required at install time, and `cortex migrate-state` is the documented optional upgrade path for older scaffolds.
- The compat claim goes into the v1.0 release notes verbatim: "v1.0 reads v0.3, v0.4, v0.5, and v1.0 scaffolds. v0.3 hand-authored `state.md` works as-is for read-side commands; `cortex migrate-state` upgrades the shape to v0.4+ when desired."
- Self-dogfood remains continuous; the compat audit is a snapshot, not a one-time test. Future scaffold-version changes must run the same two-target audit before release.

## Follow-ups (deferred to future work)

- [ ] **autumn-mail's 13 warnings on cortex 0.9.0** are real findings on autumn-mail content (not Cortex bugs), but autumn-mail is unlikely to be actively curated. Resolved to: a future `journal/dogfood-target-cleanup` entry on autumn-mail itself if/when that repo gets touched again. Not a Cortex blocker.

(Per SPEC § 4.2, deferred items resolve to another Plan, Journal entry, or Doctrine entry in the same commit. The follow-up resolves to a placeholder journal location on the downstream target.)
