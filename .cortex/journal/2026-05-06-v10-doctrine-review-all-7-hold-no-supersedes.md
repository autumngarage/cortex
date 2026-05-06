# v1.0 doctrine review — all 7 hold, no supersedes

**Date:** 2026-05-06
**Type:** decision
**Trigger:** T1.1
**Cites:** plans/cortex-v1, doctrine/0001-why-cortex-exists, doctrine/0002-compose-by-file-contract-not-code, doctrine/0003-spec-is-the-artifact, doctrine/0004-scope-boundaries, doctrine/0005-scope-boundaries-v2, doctrine/0006-scope-boundaries-v3, doctrine/0007-canonical-ownership-of-state-and-plans, journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared, journal/2026-05-06-v090-dogfood-retrieval-validation-across-three-tar

> The v1.0 doctrine review walked all 7 entries in the doctrine corpus against the v0.9.0 dogfood gate evidence (3 install PRs, 9 surfaced bugs, fresh-clone + bare-repo CI fixtures, retrieval validation, behavioral exit-bar review). All 7 entries hold; no supersedes are warranted.

## Context

The `plans/cortex-v1.md` v1.0 ceremony checklist includes: "Walk all 5 active Doctrine entries; if dogfood evidence supports promoting any of them to a stronger formulation, write the supersede entry. Only supersede if dogfood produced real conflicting evidence." The plan text predates 0006 and 0007, so the actual corpus is 7 entries (0001–0007), of which 5 are `Status: Accepted` and 2 (`0004`, `0005`) are already `Status: Superseded-by`.

The evidence base for this review is:
- Three install PRs: conductor (`autumngarage/conductor#178`), touchstone (`autumngarage/touchstone#151`), vesper (`henrymodisett/vesper#167`)
- 9 dogfood-surfaced bugs filed and closed in 4 swarm PRs (cortex#145–#148)
- Fresh-clone acceptance fixture (PR #150) and bare-repo degradation fixture (PR #151)
- Retrieval validation journal: `journal/2026-05-06-v090-dogfood-retrieval-validation-across-three-tar`
- Behavioral exit-bar review: `journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared`

## Per-doctrine assessment

- **0004 — Scope boundaries (original).** `Status: Superseded-by 0005`. Already superseded; no dogfood evidence changes this. **No action.**

- **0005 — Scope boundaries v2.** `Status: Superseded-by 0006`. Already superseded; no dogfood evidence changes this. **No action.**

- **0001 — Why Cortex exists.** The conductor case study (`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`) is the design test case the doctrine names. The v0.9.0 vesper install surfaced the same failure mode in the wild: `cortex doctor --audit-instructions` caught the unreplaced `YOUR_USERNAME/vesper.git` template URL in vesper's README — exactly the class of stale external-artifact claim the doctrine says Cortex exists to prevent. The doctrine's premise ("session-pickup gap is what Cortex exists to close; stale inputs confidently steer agents wrong") is corroborated, not contradicted. **No supersede.**

- **0002 — Compose by file contract, not code.** All three install PRs executed without any code-level coupling between Cortex and the sibling tools. The bare-repo degradation fixture (PR #151) makes the contract testable in CI: every command exits 0, produces visible output, no traceback when optional siblings (Sentinel runs, Touchstone config) are absent. The doctrine's "install independently, useful alone, compose through the filesystem" promise is confirmed. **No supersede.**

- **0003 — The spec is the primary artifact.** The SPEC-to-test traceability matrix (PR #92, `docs/spec-conformance.md`) maps every normative SPEC.md § to a `cortex doctor` check, test assertion, or documented deferral — exactly the enforcement the doctrine requires. SPEC.md is being frozen to `v1.0.0` in the parallel slice 1 PR. No spec-versus-implementation drift surfaced during the gate. **No supersede.**

- **0006 — Scope boundaries v3 (storage vs. retrieval).** Retrieval validation across three targets (`journal/2026-05-06-v090-dogfood-retrieval-validation-across-three-tar`) confirmed: BM25 floor delivers ranked results on conductor (9/10) and touchstone (8/10); hybrid-mode unavailability surfaces a visible warning before falling back, satisfying the "silent fallback is a gate failure" criterion; `--json` output matches the SPEC contract; cold-rebuild latency is sub-second on all three targets. The doctrine's hazmat boundary held — consumers used `cortex retrieve`, not direct SQLite queries. The non-normative framing is correct: the two mature targets benefited from the retrieval interface; the fresh vesper install (3/10) correctly reflects corpus scarcity, not a doctrine failure. **No supersede.**

- **0007 — Canonical ownership of state and plans.** The `cortex doctor` canonical-ownership invariant shipped in v0.6.0 and ran cleanly on all three dogfood targets — none of them had ROADMAP.md / STATUS.md anti-pattern files at repo root post-install. The override path (`.cortex/config.toml [doctrine.0007]`) is documented in `docs/config-reference.md`. The enforcement path (warn on detected anti-pattern, cite doctrine, override per-project) is exactly what the doctrine specifies. **No supersede.**

## What we decided

All 7 doctrine entries hold against v0.9.0 dogfood evidence. The corpus enters v1.0 unchanged. No supersede pairs will be written for this review block.

The plan's "5 active" count was correct: 0001, 0002, 0003, 0006, 0007 are `Status: Accepted`. The 2 already-superseded entries (0004, 0005) were walked for completeness and require no action.

## Consequences / action items

- [x] Tick the "Doctrine review" checkbox in `plans/cortex-v1.md` v1.0 ceremony section.
