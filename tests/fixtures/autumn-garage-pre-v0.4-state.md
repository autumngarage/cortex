---
Generated: 2026-04-18T22:00:00-07:00
Generator: hand-authored (regeneration infrastructure ships in Cortex Phase C)
Sources:
  - doctrine/0001-why-autumn-garage-exists, doctrine/0002-interactive-by-default
  - plans/autumn-mail-dogfood (active), plans/sentinel-cortex-t16-integration (active)
  - journal/2026-04-18-{kickoff, plan-authoring-cortex-gaps, scaffold-friction-findings, stacked-merge-recovery, r1-regression-pass, r2-wizards-shipped, setup-reflection, r5-findings-from-fresh-scaffold, t16-shipped-and-audit-gap, first-cycle-attempt-findings}
  - autumn-garage-plan.md (v3)
  - TODOs.md
  - github.com/autumngarage/{touchstone@1.2.2, cortex@0.2.0, sentinel@0.3.4}
  - github.com/autumngarage/autumn-mail (live, commit 8d771f0 plus unpushed cycle artifacts)
Corpus: 2 Doctrine, 2 active Plans, 10 Journal entries
Omitted: []
Incomplete:
  - autumn-mail MVP features (0 of 14 Success Criteria met — container + tools wired, no user-facing features yet)
  - Sentinel→Cortex T1.7 (touchstone pre-merge → doctrine/candidate) and T1.9 (PR merged → journal/pr-merged) not yet operationalized. T1.6 shipped in sentinel v0.3.0+; T1.7/T1.9 follow the same pattern in future work.
  - Cortex `doctor --audit` T1.6 classification — Cortex Phase B first-slice deferred this; without it, T1.6 entries validate but aren't enforced in the audit layer
Conflicts-preserved: []
Spec: 0.3.1
---

# Project State — Autumn Garage

> Coordination repo for the Touchstone/Cortex/Sentinel/**Conductor** quartet + autumn-mail dogfood project + **vanguard/outrider full-separation effort** (active, finish-line in flight). **2026-04-26 evening:** the all-nighter delivered the decoupling foundation (44 PRs through codex review) and tonight's wave drives the remaining cleanup. Separation finish-line plan published at `.cortex/plans/separation-finish-line.md` — SF-1/2/3 (code-only allow-list shrink in vanguard) shipping in parallel, SF-6/7 NEXT_STEPS docs ditto. SF-4 (B.2 vanguard owns its Postgres) is daylight work waiting on Railway DB provision, detailed in `.cortex/plans/vanguard-db-ownership.md`. SF-5 (drop the outrider git+https dep) is mechanical once SF-4 lands. Outrider naming batches 2 + 3 also shipped tonight (`outrider/conductor/` → `outrider/scheduler/`, `ARES_*` → `AUTOLAB_*`, `_platform/` → `platform/` + glossary). All deferred excellence workstreams (WS4 streaming, WS5 DX, WS6 SLA, WS11 invariants, WS12/13 customer auth, legal Tier 1) explicitly blocked on SF-5 via task graph — stay parked until separation done. **2026-04-24 early:** local LLM path dogfooded end-to-end and three ergonomics fixes shipped as conductor v0.3.3 ([#12](https://github.com/autumngarage/conductor/pull/12), [release](https://github.com/autumngarage/conductor/releases/tag/v0.3.3)). Default model bumped qwen2.5-coder:14b → qwen3.6:35b-a3b (MoE handles structured tool_calls correctly; qwen-coder was silently bypassing the tool executor). Timeout 180s → 600s with `CONDUCTOR_OLLAMA_TIMEOUT_SEC` env override. New silent-fail guard warns when a model returns a tool-call-shaped JSON block in message.content instead of populating tool_calls. 368 tests, ruff clean. Full dogfood writeup in `journal/2026-04-24-local-llm-dogfood.md`. **2026-04-23 evening (late):** Stage 3 shipped — HTTP tool-use end-to-end on kimi + ollama, strict sandbox, context budget, ASCII branding parity, all three slices merged in one session.
> - **Conductor v0.3.0** ([#7](https://github.com/autumngarage/conductor/pull/7), [release](https://github.com/autumngarage/conductor/releases/tag/v0.3.0)) — `conductor.tools` registry (ReadTool/GrepTool/GlobTool + ToolExecutor + strict path validation); kimi.exec drives a full multi-turn tool-use loop against Cloudflare Workers AI with a 10-iteration cap and graceful cap-hit; router unblocked for read-only kimi tool requests.
> - **Conductor v0.3.1** ([#8](https://github.com/autumngarage/conductor/pull/8), [release](https://github.com/autumngarage/conductor/releases/tag/v0.3.1)) — EditTool, WriteTool, BashTool + workspace-write sandbox semantics; ollama gets the same tool-use loop against `/api/chat`; kimi + ollama both declare the full six-tool set.
> - **Conductor v0.3.2** ([#10](https://github.com/autumngarage/conductor/pull/10), [release](https://github.com/autumngarage/conductor/releases/tag/v0.3.2)) — `max_context_tokens` on every provider; HTTP loops halt before exceeding model context; per-iteration cost log in `usage["iterations"]`; new `--sandbox strict` adds POSIX rlimits + tighter timeouts on BashTool. ASCII hero banner ([#9](https://github.com/autumngarage/conductor/pull/9)) landed alongside.
> - **Brew tap bumped**: `brew upgrade autumngarage/conductor/conductor` picks up v0.3.2. 282 tests (was 203 pre-Stage-3), ruff clean throughout.
> - **Sentinel migration (Stage 5) fully unblocked at provider-capability level.** Sentinel's coder can now use any conductor provider for multi-turn write tasks under workspace-write. The migration PR on sentinel's side is its own session.
> - **Quartet versions**: Touchstone 2.1 · Cortex 0.2.3 · Sentinel 0.3.4 · **Conductor 0.3.3**. All LLM access in the garage now flows through Conductor; the trio→quartet→collapse transformation is done.
>
> Historical: 2026-04-22/23 was the UX hardening + release session: 6 dogfood-driven UX gaps closed (config shape, route-log, cold-onboard, local-reviewer translate, cache key, dry-run preview), `touchstone migrate-review-config` shipped, latent `set -u` cache-key bug surfaced and fixed, codex JSON + session-id research-driven follow-ups landed. 2026-04-21: Conductor v0.2 + Touchstone v2.0 PRs opened. 2026-04-20/21 morning: Conductor v0.1.0 (4 PRs). 2026-04-18: 7 tool releases + R1–R4 + T1.6 + R5 + V1/V2.

## P0 — Conductor v0.1.0 shipped ✅ (2026-04-21)

- **Repo:** [autumngarage/conductor](https://github.com/autumngarage/conductor) · **Release:** [v0.1.0](https://github.com/autumngarage/conductor/releases/tag/v0.1.0)
- Commands: `conductor call --with <id> | --auto [--tags a,b,c]`, `list`, `smoke`, `doctor`, `init`.
- Providers: `kimi` (Cloudflare Workers AI HTTP, default `@cf/moonshotai/kimi-k2.6`), `claude` / `codex` / `gemini` (CLI shell-out), `ollama` (localhost HTTP).
- Credentials resolver: env var → macOS Keychain (service `conductor`) → None. `conductor init` wizard offers Keychain / direnv / print-only storage.
- Org-level GitHub secrets `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` on `autumngarage`, `visibility: all`. CI validate workflow runs mocked tests every push/PR; live-smoke via `workflow_dispatch`.
- See `journal/2026-04-21-conductor-v0.1-shipped.md` for the full story.

**Quartet, post-collapse:** **Touchstone 2.0.0** · Cortex 0.2.3 · Sentinel 0.3.4 · **Conductor 0.2.1**.

**Next Conductor workstreams (deferred to future plans):** Sentinel migration (replace `src/sentinel/providers/*.py` with `conductor call` shell-outs), Touchstone migration (reviewer cascade gains `auto` entry), Cortex Phase C synthesis backends, brew tap.

## P0 — Cortex install flow into existing repos: READY (verified)

Cortex v0.2.3 shipped (cortex#26, codex 0 findings, clean merge). Re-tested on /tmp/cortex-test-sigint: all three v0.2.2 polish bugs gone. Remaining unknowns (6 in sigint: INVESTMENT_THESIS, API_INTEGRATION, PAID_DATA_SOURCES, RESEARCH_API, SCHWAB_SETUP, CODEX_AUTOFIX_DISABLED) are genuine project-specific docs that hit the user-taught `.cortex/.discover.toml` flow by design.

**Verdict:** install flow into existing repos is ready. Next session can install cortex into actual `/Users/henry.modisett/Repos/sigint/` (real production repo) with confidence. Estimated 10 min interactive (6 Doctrine + 7 Plan + 6 unknown classification prompts).

The user's original ask ("the first major goal is can we get codex [...meaning cortex per follow-up...] to a point where it can be installed into our other repos") is met for cortex. The original codex-installability question (the AI reviewer hook) is unaddressed — separate workstream if pursued.

## P0.5 — Autumn-mail first real feature SHIPPED ✅ (2026-04-19)

`GWSWrapper.swift` + `GWSWrapperTests.swift` + AutumnMailApp wiring landed on autumn-mail main as PR #5 (squash 205e7e7). Salvage path worked end-to-end on the third try:
1. F11 (touchstone v1.2.3 `.swiftlint.yml` template) shipped
2. `touchstone update` on autumn-mail pulled the template
3. Branch from `salvage/cycle-5-gws-wrapper-reviewer-approved` tag, rebase on main (clean)
4. swiftlint violations dropped 24 → 1 (the .build excludes did most of the work; identifier_name allowlist absorbed the `to` violation)
5. Manual fix on the remaining violation (split a 178-char JSON fixture line into multiline)
6. `swift test`: 15/15 passing. `swiftlint --strict`: 0 violations.
7. PR via `scripts/open-pr.sh --auto-merge` — codex review allowed, merged

Now P1 unblocked: MLX Swift wiring → SwiftUI views → end-to-end flow.

## P0.6 — Resume autumn-mail dogfood on improved sentinel + cortex

Tools at:
- **Sentinel** with PR #81 merged: approved-jumps-queue, refinement file-existence check (pre+post), lens `scope:` field, planner dedupe.
- **Cortex** with PR #22 merged: `cortex doctor` warns on unscoped LLM/API constraints in CLAUDE.md/AGENTS.md.

Two paths to pick:
- **Retry:** rerun `sentinel work --auto --budget 5 --coder-timeout 1200` against autumn-mail. Expect: (a) the approved gws-wrapper proposal gets picked first (Fix 1); (b) any "harden GmailClient.swift" refinement is rejected on HEAD (Fix 2); (c) lens-scope fix avoids the privacy-compliance false-positive on `.sentinel/config.toml`. **Best dogfood signal** — verifies the bundle actually fixes the failure modes that surfaced it.
- **Salvage:** check out salvage tag `salvage/cycle-3-gws-wrapper-pre-retry` (or the cycle-4 worktree's `GmailClient.swift`), apply pipe-buffering drain + wire-into-AutumnMailApp, open PR manually. Faster path to P1 unblock; doesn't dogfood the new sentinel.

**Recommended:** retry. Salvage is always available afterward if the cycle still doesn't ship.

## P1 — Autumn-mail MVP remaining features (unchanged)

See `plans/autumn-mail-dogfood.md`. 14 SC, 0 met. After gws-wrapper lands, MLX Swift wiring → SwiftUI views → end-to-end flow.

## P1 — Autumn-mail MVP remaining features

See `plans/autumn-mail-dogfood.md` for full plan. 14 Success Criteria. 0 met. After gws-wrapper lands:
- MLX Swift local-LLM wiring (model download + inference)
- SwiftUI views: Inbox (triage), Message (read + Draft reply), Composer (editable + Send)
- End-to-end flow: triage → read → draft-via-local-LLM → send
- OAuth setup docs for `gws auth setup`

Each can be a separate `sentinel work` cycle or combined into a larger one.

## P2 — Cortex Phase C gap blocks T1.6 full enforcement

Cortex v0.2.0's `doctor --audit` classifies T1.1/T1.5/T1.8/T1.9 but NOT T1.6. This means autumn-mail accumulates valid T1.6 journal entries but `cortex doctor --audit` doesn't yet confirm they match fires. Filed as a cortex TODO in `TODOs.md` under the Cortex section. Can ship independently as a Cortex Phase C extension.

## P3 — Tool-side improvements surfaced today, not yet addressed

Findings from 2026-04-18 cycles that became new TODOs:
- **Sentinel:** role-timeout generalization (Monitor/Researcher/Planner/Reviewer still use legacy `scan.provider_timeout_sec`; only Coder got per-role in C5). Parallel-agent coordination hazard (agents on same repo step on each other). C6 lens determinism drift.
- **Touchstone:** `scripts/open-pr.sh --base <branch>` for stacked PRs (found during R2/R3/R4 coordination).
- **Coordination playbook:** prefer bundled rounds over stacked PRs from the start.

All in `TODOs.md`.

---

## Installed tool versions (2026-04-23 end-of-day)

- **Touchstone 2.0.0** — [conductor adapter + migrate-review-config + review --dry-run + commit-frequency principle + 2.0-shape config writer + route-log in transcript + cold-onboard messages + cache key includes conductor knobs]
- **Conductor 0.2.1** — [exec subcommand + capability declarations + prefer/effort/tools/sandbox axes + route preview + concierge init + graceful fallback + session_id capture + --resume + ollama model-pulled doctor warning]
- **Cortex 0.2.3** — [plans template + init interactive + sibling detection + scan-and-absorb + unscoped-LLM-constraint warning]
- **Sentinel 0.3.4** — [reviewer=codex default + sibling detection + T1.6 Cortex journal writes + coder timeout config + built-in registry + rejection memory + graceful missing-tool verifier + verifications.jsonl audit]

## Shipped releases today (2026-04-23)

- **touchstone v2.0.0** ([release](https://github.com/autumngarage/touchstone/releases/tag/v2.0.0), brew formula updated)
- **conductor v0.2.1** ([release](https://github.com/autumngarage/conductor/releases/tag/v0.2.1), brew tap pending creation)

## Earlier release history

touchstone: 1.1.0 → 1.2.0 → 1.2.1 → 1.2.2 → 1.2.3 → 2.0.0 · conductor: 0.1.0 → 0.2.1 · cortex: 0.1.0 → 0.2.0 → 0.2.2 → 0.2.3 · sentinel: 0.2.0 → 0.3.0 → 0.3.1 → 0.3.2 → 0.3.3 → 0.3.4.

## Open decisions

D1 Principles/Doctrine boundary (deferred) · D2 Write contract (settled: CLI-primary via Phase D) · D3 Umbrella branding (settled) · D4 Tap structure (settled) · D5 Compat surfacing (settled: `<tool> version --verbose` prints CIC range) · D6 Review-hook → journal opt-in default (deferred; R4 registry visibility partially addresses) · D7 Nested `.cortex/` in monorepos (deferred) · D8 Deferred-write longevity (settled).
