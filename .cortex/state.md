---
Generated: 2026-04-17T22:45:00-07:00
Generator: hand-authored (regeneration infrastructure ships in Phase C)
Sources:
  - HEAD of branch feat/cortex-grep (targeting main at 5a7826a)
  - .cortex/doctrine/ (5 entries: 0001–0003 + 0005 active with Load-priority: always; 0004 Superseded-by 0005)
  - .cortex/plans/ (1 active: phase-b-walking-skeleton; vision-sharpening shipped)
  - .cortex/journal/ (12 entries, all for 2026-04-17)
  - .cortex/templates/ (8 files)
  - .cortex/map.md (stub, pending Phase C)
  - .cortex/procedures/ (empty; .gitkeep only)
  - SPEC.md v0.3.1-dev
  - pyproject.toml, src/cortex/ (scaffold + version + init + doctor + manifest + grep commands)
  - PLAN.md phase-A-complete, phase-B-started
Corpus: 5 Doctrine entries, 1 active Plan, 12 Journal entries, 8 Templates, 1 Python package (cortex 0.1.0.dev0 with `version` + `init` + `doctor` + `manifest` + `grep` commands)
Omitted:
  - .cortex/.index.json — not present pre-CLI; per SPEC § 2 the file is auto-maintained by the Cortex CLI and its absence is the expected state before Phase B ships.
Incomplete:
  - Map regeneration (Phase C); map.md is a stub with Incomplete: [all sources]
  - Automated metric aggregation (Phase C); State is hand-authored
  - Sentinel run journals (no integration yet; Phase E)
Conflicts-preserved: []
Spec: 0.3.1-dev
---

# Project State

> Vision v3 promoted this afternoon; protocol sharpened and dogfood templates shipped this evening. Phase B (walking-skeleton CLI) is the single open priority. The spec is now internally consistent and implementable without the CLI — the distribution-race floor is durable.

## P0 — Phase B: walking-skeleton CLI + Protocol implementation

Build the CLI structure and non-synthesizing commands so there's something to `brew install`, so later phases have something to extend, and so the Protocol's Tier 1 triggers have a `cortex doctor --audit` to enforce them.

Full plan: [`plans/phase-b-walking-skeleton.md`](./plans/phase-b-walking-skeleton.md) — refreshed 2026-04-17 to cover the v0.3.1-dev scope (manifest, grep, expanded doctor checks, T1.9 audit, Goal-hash verification, Load-priority, interactive flow).

**Success signal:** `brew tap autumngarage/cortex && brew install cortex && cortex init` works in a fresh repo and produces a SPEC-v0.3.1-conformant `.cortex/` scaffold including `.cortex/protocol.md` and `.cortex/templates/`, validated by `cortex doctor`.

- [x] Python package scaffold (`pyproject.toml`, `src/cortex/`, `uv`-managed) — shipped with `cortex version` as first command
- [ ] `cortex` (interactive entry point) — status + promotion queue + digest prompts (per README example)
- [x] `cortex init` — scaffolds `.cortex/` per SPEC.md v0.3.1, copying bundled `protocol.md` + `templates/` into the target project; idempotent; `--force` preserves user content
- [x] `cortex manifest --budget <N>` — token-budgeted session-start slice per Protocol § 1; `Load-priority: always` Doctrine pinned first, then recency; degrades to state-only below 2000 tokens; widens Journal to 7 days at 15000+
- [x] `cortex grep <pattern>` — frontmatter-aware wrapper over ripgrep; shells out to `rg`, groups matches per file, prepends a metadata summary line (Status/Type/Date/Load-priority) extracted from YAML frontmatter or bold-inline fields per SPEC § 6. `--layer` restricts to one subdirectory; extra flags pass through to `rg`.
- [ ] `cortex --status-only` — equivalent of status summary, for scripting
- [x] `cortex doctor` (first slice) — scaffold structure, seven-field metadata on derived layers, Doctrine frontmatter (Status/Date/Load-priority), Plan frontmatter + sections + Goal-hash recomputation (SPEC § 4.9), Journal filename pattern. Promotion-queue invariants and single-authority-rule drift defer to the `.index.json`-enabled slice.
- [ ] `cortex doctor --audit` — verifies Tier 1 Protocol triggers (T1.1–T1.9) produced entries during the git session window
- [ ] `cortex doctor --audit-digests` — random-sample claim verification on digests
- [ ] `cortex doctor` warning when the CLI-less fallback manifest (per Protocol § 1) is used against a corpus exceeding default thresholds
- [ ] `cortex --promote <id>` — flag-style promotion (interactive flow is the default)
- [x] `cortex version` — prints CLI version + supported spec + protocol versions + install method
- [ ] Tests for each command (temp-dir fixtures, no mocked filesystem)
- [ ] `autumngarage/homebrew-cortex` tap repo created
- [ ] v0.1.0 release via Homebrew formula pointing at the source tarball (first CLI release per PLAN.md Phase B; ships targeting spec v0.3.1-dev)

## P1 — Phase C: first synthesis (`cortex refresh-map`, `cortex refresh-state`)

Gated on P0. Not started. Will use the `claude` CLI directly (no SDK, no provider layer). Must emit the seven-field metadata contract per SPEC.md § 4.5.

## P2 — Integration with Sentinel and Touchstone (Phase E)

Gated on P0–D. Critical integrations: Sentinel end-of-cycle → Journal entry (Trigger T1.6); Touchstone pre-merge → Doctrine candidate draft (Trigger T1.7); Touchstone post-merge → T1.9 `journal/pr-merged.md` entry; Touchstone pre-push → `cortex doctor --strict` (the invariant-enforcement story from SPEC.md § 9 and README). Without these, Cortex is useful but not *enforced*.

---

## Shipped recently

- **2026-04-17 (late evening)** — **Phase B fifth slice: `cortex grep`.** Frontmatter-aware ripgrep wrapper — the primary mid-session retrieval path per Protocol § 1 (Doctrine 0005 #1 rules out vector retrieval at the storage layer). Shells out to `rg --json` and parses the NDJSON stream so match vs. context records are unambiguous (`-C`/`-A`/`-B` context now renders with the ripgrep `-` separator instead of being mangled by a `:` splitter). Groups matches per file and prepends a one-line metadata summary pulled from YAML frontmatter or bold-inline fields (SPEC § 6) covering Status/Type/Date/Written/Load-priority. `--layer {doctrine,plans,journal,procedures,templates}` restricts the search root; extra flags pass through to `rg`; patterns are terminated with `--` so leading-dash patterns like `- [ ]` work. Exits 2 when `.cortex/` is missing, 3 when `rg` is not on PATH, 2 on an `rg` error (bad pattern). Reader-contract warnings on missing or unsupported `.cortex/SPEC_VERSION` go through a shared `cortex.compat.warn_if_incompatible` helper also wired into `cortex manifest`. Malformed JSON records surface a stderr warning instead of masquerading as "no matches". 11 new grep tests; 79 total (monkeypatch `subprocess.run`, so tests don't require ripgrep on PATH).
- **2026-04-17 (late evening)** — **Phase B fourth slice: `cortex manifest --budget`.** Assembles the session-start manifest per Protocol § 1: full `state.md` always loaded, Doctrine ordered by `Load-priority: always` first then `Date:` recency, only `Status: active` Plans, Journal entries from the last 72 h plus the latest digest, and a promotion-queue summary from `.cortex/.index.json` (or an explicit "unavailable" line when the index does not exist). Graceful degradation: `--budget < 2000` → state-only, `--budget >= 15000` → Journal window widens from 72 h to 7 d. Token estimates use a conservative ~4 chars/token ratio; the exact tokenizer belongs with whichever agent consumes the manifest. 10 new tests (63 total).
- **2026-04-17 (late evening)** — **Phase B third slice: `cortex doctor` (basic).** Validates scaffold structure, seven-field metadata on derived layers (SPEC § 4.5), Doctrine entry frontmatter (Status/Date enum + Load-priority for non-superseded; SPEC §§ 3.1, 6 accepts either bold-inline or YAML frontmatter), Plan frontmatter + Goal-hash recomputation (§ 4.9) + required sections with fence-aware parsing + grounding citation (§§ 3.4, 4.1, 4.3) + measurable-signal check on Success Criteria, and Journal filename pattern. Ships a minimal in-repo frontmatter parser (no YAML dependency) and a goal-hash normalizer matching the SPEC § 4.9 worked example (`Sharpen Cortex's Vision` → `1cc12b25`). Dogfood-validated on this repo: doctor surfaced a legacy content gap (`plans/vision-sharpening.md` used `## Success criteria` and was missing the canonical `## Why (grounding)`, `## Approach`, `## Work items` sections) which is fixed in the same PR. Doctrine 0004-scope-boundaries is `Superseded-by 0005` and left untouched per the immutable-with-supersede invariant — the validator exempts superseded entries from the post-hoc `Load-priority:` requirement rather than retrofitting them. `--audit` and `--audit-digests` ship in a separate follow-up slice.
- **2026-04-17 (late afternoon)** — **Phase B second slice: `cortex init`.** Scaffolds `.cortex/` per SPEC v0.3.1-dev: SPEC_VERSION, protocol.md, full templates/ tree, doctrine/plans/journal/procedures/ subdirs with .gitkeep, seven-field map.md + state.md stubs. Idempotent with `--force` escape hatch that preserves user content. Bundles `.cortex/protocol.md` + `templates/` into `src/cortex/_data/` via hatchling force-include; `tests/test_data_sync.py` enforces the _data/ copies stay in sync with canonical `.cortex/`. 17 tests green.
- **2026-04-17 (late afternoon)** — **Phase B kicked off: Python scaffold + `cortex version`.** `pyproject.toml` with click/pytest/ruff/mypy; `src/cortex/` package with `__version__` + supported-version constants; click CLI with `version` subcommand; 5 passing tests; ruff + mypy clean. Entry point wired (`uv run cortex version` works).
- **2026-04-17 (evening)** — **Protocol sharpened, templates shipped, drafts archived.** Three Protocol/SPEC amendments resolved live contradictions and hand-waves: Protocol § 1 rewritten to use `Load-priority: always` + recency (removing the "semantic relevance" contradiction with Doctrine 0004); Protocol § 1 fallback specified for CLI-less projects; T1.9 (PR merged) added to Tier 1; SPEC § 4.9 Goal-hash normalization concretized (lowercase title → sha256[:8]); SPEC § 3.1 Doctrine gains `Load-priority:` field; all four existing Doctrine entries backfilled. Eight templates shipped under `.cortex/templates/` (five journal, one doctrine, two digest). Vision drafts v1/v2/v3 moved to `drafts/` with supersede banners. Strategic content preserved in [`journal/2026-04-17-competitive-positioning-and-claude-code-risk.md`](./journal/2026-04-17-competitive-positioning-and-claude-code-risk.md). Full details in [`journal/2026-04-17-protocol-sharpened-and-drafts-archived.md`](./journal/2026-04-17-protocol-sharpened-and-drafts-archived.md).
- **2026-04-17 (afternoon)** — **Vision v3 promoted.** Cortex Protocol shipped as `.cortex/protocol.md` (two-tier triggers, three invariants, template references). SPEC.md bumped to v0.2.0-dev with seven-field metadata contract, promotion queue operational rules, single authority rule for reads, multi-writer Plan visibility, retention and consolidation section. Doctrine 0004 (scope boundaries) landed. README rewritten. Full provenance in [`journal/2026-04-17-vision-v3-promoted.md`](./journal/2026-04-17-vision-v3-promoted.md).
- **2026-04-17 (morning)** — Phase A complete. Repo bootstrapped, SPEC.md v0.1.0 drafted, PLAN.md + README.md + PRIOR_ART.md + CLAUDE.md + AGENTS.md written, dogfood `.cortex/` populated with three Doctrine entries and one Journal entry. See [`journal/2026-04-17-spec-v0.1.0-drafted.md`](./journal/2026-04-17-spec-v0.1.0-drafted.md).

## Open questions (Phase B kickoff)

- **Python project structure:** src-layout vs. flat? Lean toward `src/cortex/` (matches Sentinel). Confirm.
- **Testing framework:** pytest (matches Sentinel). Agreed; decide `typer.testing.CliRunner` vs. shell-out.
- **Brew formula placement:** `autumngarage/homebrew-cortex` tap needs creating before v0.1.0 release.
- **`cortex doctor` cadence:** CI-only? Pre-commit? Periodic? Decide in Phase B.
- **Interactive-flow UX:** terminal rendering of the prompt-per-candidate flow; pager interaction; keybindings. Sketch in Phase B.
- **Click vs prompt_toolkit for the interactive flow:** the refreshed Phase B plan lists both as candidates. Decide during scaffold.

## Known stale-now / handle-later

- **Spec freshness:** SPEC.md v0.3.1-dev is draft and has not yet been validated against a real external project. Expect at least one amendment (minor bump) during Phase C–D dogfood on Sentinel's repo.
- **Gemini round-2 critique is missing.** Google capacity was exhausted during v2 → v3 iteration; v3 went to promotion on Codex critique + user direction alone. Re-running Gemini when capacity returns is optional; v3 is defensible without it.
- **Map layer is a stub.** `.cortex/map.md` exists with a seven-field header and `Incomplete: [all sources]`; real synthesis ships in Phase C via `cortex refresh-map`.
- **Competitive landscape re-assessment due ~2026-07-17** (quarterly cadence set in [`journal/2026-04-17-competitive-positioning-and-claude-code-risk.md`](./journal/2026-04-17-competitive-positioning-and-claude-code-risk.md)). Watch-items: Letta trigger-discipline features, Anthropic memory-roadmap signals.
