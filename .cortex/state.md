# Project State

**Generated:** 2026-04-17T22:00:00-04:00 (hand-authored at bootstrap; regeneration infrastructure ships in Phase C)
**As-of data:** HEAD of main, SPEC.md v0.1.0 (draft)
**Spec:** 0.1.0

> Phase A (foundation + spec) shipped today. Phase B (walking-skeleton CLI) is next and is the single open priority.

## P0 — Phase B: walking-skeleton CLI

Build the CLI structure and non-synthesizing commands so there's something to `brew install` and so later phases have something to extend. No LLM calls in Phase B.

Full plan: [`plans/phase-b-walking-skeleton.md`](./plans/phase-b-walking-skeleton.md).

**Success signal:** `brew tap autumngarage/cortex && brew install cortex && cortex init` works in a fresh repo and produces a SPEC-v0.1.0-conformant `.cortex/` scaffold, validated by `cortex doctor`.

- [ ] Python package scaffold (`pyproject.toml`, `src/cortex/`, `uv`-managed)
- [ ] `cortex version` — prints CLI version + supported spec versions
- [ ] `cortex init` — scaffolds `.cortex/` per SPEC.md §2
- [ ] `cortex status` — reads `.cortex/`, reports freshness and spec violations
- [ ] `cortex doctor` — validates `.cortex/` structure against spec
- [ ] Tests for each command (temp-dir fixtures, no mocked filesystem)
- [ ] `autumngarage/homebrew-cortex` tap repo created
- [ ] v0.1.0 release via Homebrew formula pointing at the source tarball

## P1 — Phase C: first synthesis (`cortex refresh-map`)

Gated on P0. Not started. Will use the `claude` CLI directly (no SDK, no provider layer).

## P2 — Integration with Sentinel and Touchstone (Phase E)

Gated on P0–D. Out of scope until the single-project loop is proven on Sentinel's own repo as dogfood.

---

## Shipped recently

- **2026-04-17** — Phase A complete. Repo bootstrapped, SPEC.md v0.1.0 drafted, PLAN.md + README.md + PRIOR_ART.md + CLAUDE.md + AGENTS.md written, dogfood `.cortex/` populated with three Doctrine entries and one Journal entry. See [`journal/2026-04-17-spec-v0.1.0-drafted.md`](./journal/2026-04-17-spec-v0.1.0-drafted.md).

## Open questions

- **Python project structure:** src-layout vs. flat? Lean toward `src/cortex/` (matches Sentinel). Confirm on Phase B kickoff.
- **Testing framework:** pytest (matches Sentinel). Agreed — but decide whether tests use `typer.testing.CliRunner` or shell-out to the built entrypoint.
- **Brew formula placement:** `autumngarage/homebrew-cortex` tap will need to be created before v0.1.0 release. Pattern-match Sentinel's tap for formula shape.

## Known stale-now / handle-later

- **Spec freshness:** SPEC.md v0.1.0 is draft and has not yet been validated against a real external project. Expect at least one amendment (minor bump) during Phase C–D dogfood on Sentinel's repo.
- **No Map layer in this repo's own `.cortex/` yet.** Map requires regeneration, which Phase C provides. Hand-authoring a Map at this stage would violate the "derived, not authored" contract.
