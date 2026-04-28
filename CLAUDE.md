# Cortex — Claude Code Instructions

## Who You Are on This Project

You are designing a file-format protocol (`.cortex/`) for project memory and a reference CLI that reads, writes, and validates it. Cortex is the reflective layer of the autumngarage composition — **Touchstone** is the foundation (standards, hooks), **Sentinel** is the loop (autonomous agent cycles), **Cortex** is the memory. The three tools install independently and compose through file contracts, never code dependencies.

At this stage the repo is **spec-first**: the protocol in [SPEC.md](./SPEC.md) is the primary artifact. The CLI is designed to implement the spec, not the other way around. Changes to the spec require explicit version bumps per the rules in SPEC.md §6.

"Good" looks like: a spec that survives real projects (sigint, sentinel, touchstone) as dogfood targets; a CLI that's small and legible, synthesizing via the `claude` CLI the same way Sentinel does; layer contracts that hold when consumed by both humans and agents; staleness that's visible instead of tolerated.

## Current state (read this first)

@.cortex/state.md

## Cortex Protocol (how agents write to .cortex/)

@.cortex/protocol.md

## Engineering Principles

@principles/engineering-principles.md
@principles/pre-implementation-checklist.md
@principles/audit-weak-points.md
@principles/documentation-ownership.md

## Git Workflow

@principles/git-workflow.md

### The lifecycle (drive this automatically, do not ask the user for permission at each step)

1. **Pull.** `git pull --rebase` on main before starting work.
2. **Branch.** `git checkout -b <type>/<short-description>` where `<type>` is one of `feat`, `fix`, `chore`, `refactor`, `docs`.
3. **Change + commit.** Stage explicit file paths, commit with a concise message.
4. **Ship.** `bash scripts/open-pr.sh --auto-merge` — pushes, creates the PR, runs Codex review, squash-merges, syncs main.
5. **Clean up.** `git branch -D <feature-branch>` if it still exists locally.

### Housekeeping

- Concise commit messages. One concern per commit where practical.
- Run `/compact` at ~50% context. Start fresh sessions for unrelated work.

### Memory Hygiene

- Treat Claude Code memory as cached guidance, not canonical truth. Verify against this repo before acting on a remembered command, flag, path, or version.
- Do not write memory for facts cheap to derive from `README.md`, `SPEC.md`, `.cortex/plans/cortex-v1.md`, or the code itself.
- If you write memory mentioning a command, flag, file path, version, or workflow, include the date (YYYY-MM-DD) and the canonical source checked.

## Cortex-Specific Principles

- **Spec before implementation.** The protocol in SPEC.md is the primary artifact. Don't code ahead of the spec; amend the spec first, then implement. Spec version bumps follow SPEC.md §6.
- **Dogfood as the readiness bar.** v0.9.0 is the engineering release-gate — install Cortex on **touchstone** (`autumngarage/touchstone`, locked 2026-04-24 per [`.cortex/journal/2026-04-24-dogfood-target-touchstone.md`](./.cortex/journal/2026-04-24-dogfood-target-touchstone.md)), do one week of real work, fix what surfaces. v1.0.0 is ceremonial freeze on top of v0.9.0. Earlier minor releases (v0.3.0 → v0.6.0) exit on this repo's own dogfood metrics named in `.cortex/plans/cortex-v1.md` `## Success Criteria`. If the output needs more than one editing pass at any release boundary, the release isn't done.
- **Compose by file contract, not code.** Cortex does not import Sentinel, Touchstone, or anything they own. Integration is: "if `.sentinel/runs/` exists, read it; if `.touchstone-config` exists, respect it; otherwise degrade gracefully." Match the pattern Sentinel already uses for Touchstone detection.
- **Synthesis via `claude` CLI, no SDK.** Same convergent-CLI pattern Sentinel uses. No stored keys inside Cortex. No provider abstraction layer duplicating Sentinel's — shell out to `claude -p` directly for the synthesis commands that need it. (applies to: toolchain — Cortex itself is a toolchain CLI, no app-runtime distinction.)
- **Regeneration is visible.** Every derived layer (Map, State) carries a `Generated:` timestamp and a source list at the top. Stale-beyond-threshold regeneration surfaces as a warning. Silent staleness is a bug.
- **Append-only for Journal; immutable-with-supersede for Doctrine.** These are load-bearing invariants. Any code path that would overwrite a Journal entry or delete a Doctrine entry is a spec violation.

## Testing

```bash
# Reinstall dependencies without rerunning the full machine setup
bash setup.sh --deps-only

# Before any push — profile-aware via .touchstone-config
bash scripts/touchstone-run.sh validate
```

Fix failing tests before pushing. Phase A has no code to test; test infrastructure starts in Phase B.

## Release & Distribution

v0.1.0 shipped. Distribution: the Homebrew tap `autumngarage/homebrew-cortex` hosts the formula (`brew tap autumngarage/cortex && brew install autumngarage/cortex/cortex` — fully qualified to side-step the unrelated Prometheus `cortex` in homebrew-core); `uv tool install git+...` works for source installs. Release flow: version bump in `__init__.py` + `pyproject.toml`, tag, push tag, `gh release create v0.X.Y --generate-notes`. The release-published event triggers `.github/workflows/release.yml`, which calls the shared `homebrew-bump.yml` reusable workflow in `autumngarage/autumn-garage` (pinned `@v1`) to rewrite the tap formula's `url` + `sha256` and commit directly to the tap's `main` — no hand-editing. Manual escape hatch: `gh workflow run release.yml -f tag_name=v0.X.Y` re-bumps for an existing tag. Required repo secret: `HOMEBREW_TAP_PAT` (classic PAT with `repo` scope on the tap, or fine-grained with `contents:write` on `autumngarage/homebrew-cortex`).

Each release must also declare which spec version it supports (in `SUPPORTED_SPEC_VERSIONS` in `src/cortex/__init__.py`). A minor CLI release cannot change the spec's major version; those travel together.

## Architecture

Python CLI (click + uv-managed venv) organized around layer commands. v0.2.3 ships the non-synthesizing surface; the production-release roadmap ([`.cortex/journal/2026-04-24-production-release-rerank.md`](./.cortex/journal/2026-04-24-production-release-rerank.md), supersedes the 2026-04-23 phase reorder for sequencing decisions) sequences the remaining work as six release-driven sub-sections (v0.3.0 → v1.0.0) under a single forcing function: install Cortex on a real project, work for a week, no surprises.

- `cortex init` — scaffold `.cortex/` per SPEC.md
- `cortex status` / `cortex doctor` — validate and report (orphan-deferral check ships v0.3.0; remaining invariant expansions v0.6.0)
- `cortex journal draft <type>` / `cortex plan spawn <slug>` — v0.3.0 authoring helpers (deterministic; `journal draft` pre-fills from `git log` + `gh pr view` context). Also v0.3.0: `release` journal type + T1.10 release-event trigger.
- `cortex plan status` / `cortex refresh-state` (deterministic, marker-preserved) / `cortex next` (deterministic MVP) — v0.4.0 read-side helpers
- `cortex doctor --audit-instructions` (across-the-fourth-wall claim audit) / Manifest `Verified:` per-fact / Touchstone post-merge hook — v0.5.0 trust + automation layer
- `cortex refresh-index` / `cortex promote <id>` (real writer) — v0.6.0 lifecycle layer
- `cortex refresh-map` / `cortex refresh-state --enhance` / `cortex next --enhance` — **deferred from v1.0** to v1.x; LLM polish is parked because the conductor case study evidence is that polished prose hides staleness. See [`plans/cortex-v1.md`](./.cortex/plans/cortex-v1.md) `## Follow-ups (deferred)`.
- No background daemon; all writes are explicit CLI invocations.

## Key Files

| File | Purpose |
|------|---------|
| `SPEC.md` | The `.cortex/` file-format protocol, versioned (currently v0.3.1-dev draft) |
| `.cortex/plans/*.md` | Active plans. Currently one: `cortex-v1.md` (v0.3.0 → v1.0.0 release sequence). The v0.2.4 → v0.2.5 patch plan (`init-ux-fixes-from-touchstone.md`) shipped 2026-04-25. See `.cortex/state.md` `## Current work` for the canonical active-plan list. |
| `README.md` | The story and composition narrative |
| `docs/PRIOR_ART.md` | Research synthesis behind the spec's design rules (ADRs, Diataxis, WAL, Zettelkasten, MemGPT, Voyager) |
| `.cortex/` | This repo's own Cortex dogfood — Doctrine + Journal entries about Cortex itself |
| `principles/*.md` | Touchstone-managed engineering principles (synced via `touchstone update`) |
| `scripts/*.sh` | Touchstone-managed helpers (open-pr, merge-pr, cleanup-branches) |

## State & Config

- Project-owned files include `CLAUDE.md`, `AGENTS.md`, `.codex-review.toml`, `.pre-commit-config.yaml`, `setup.sh`, and the Cortex-native files in this repo (`SPEC.md`, `README.md`, `docs/`, `.cortex/` — see `.cortex/state.md` `## Current work` for the active-plan list).
- Touchstone-managed files live in `principles/` and `scripts/` and are synced via `touchstone update`.
- No runtime config yet. When the CLI ships, per-project config (if any) will live in `.cortex/config.toml` or be derived entirely from `.cortex/` contents.

## Hard-Won Lessons

Too new for project-specific lessons. The spec's design rules are themselves distilled lessons from sigint's manual practice and from the wider literature (see `docs/PRIOR_ART.md` §1, and the "Pain points" section). Notable transfers:

- **Premature completion declarations** (sigint's `COLLECTOR_MIGRATION.md`, 2026-04-05) → SPEC.md requires measurable Success Criteria on every Plan.
- **Silent staleness in aggregators** (sigint's resolution-pipeline death, 2026-04-05 through 04-09) → SPEC.md requires `Generated:` headers with source lists on every derived layer; stale-beyond-threshold surfaces as warning.
- **Scattered deferrals with no consolidation** (sigint's multiple plans) → SPEC.md §4.2 requires deferred items to resolve to another Plan, Journal entry, or Doctrine entry in the same commit.

<!-- conductor:begin v0.8.1 -->
## Conductor delegation

This project has [conductor](https://github.com/autumngarage/conductor)
available for delegating tasks to other LLMs from inside an agent loop.
You can shell out to it instead of trying to do everything yourself.

Quick reference:

- Quick factual/background ask:
  `conductor ask --kind research --effort minimal --brief-file /tmp/brief.md`.
- Deeper synthesis/research:
  `conductor ask --kind research --effort medium --brief-file /tmp/brief.md`.
- Code explanation or small coding judgment:
  `conductor ask --kind code --effort low --brief-file /tmp/brief.md`.
- Repo-changing implementation/debugging:
  `conductor ask --kind code --effort high --brief-file /tmp/brief.md`.
- Merge/PR/diff review:
  `conductor ask --kind review --base <ref> --brief-file /tmp/review.md`.
- Architecture/product judgment needing multiple views:
  `conductor ask --kind council --effort medium --brief-file /tmp/brief.md`.
- `conductor list` — show configured providers and their tags.

Conductor does not inherit your conversation context. For delegation,
write a complete brief with goal, context, scope, constraints, expected
output, and validation; use `--brief-file` for nontrivial `exec` tasks.
Default to `conductor ask`; use provider-specific `call` / `exec` only
when the user explicitly asks for a provider or the semantic API does not
fit.

Providers commonly worth delegating to:

- `kimi` — long-context summarization, cheap second opinions.
- `gemini` — web search, multimodal.
- `claude` / `codex` — strongest reasoning / coding agent loops.
- `ollama` — local, offline, privacy-sensitive.
- `council` kind — OpenRouter-only multi-model deliberation and synthesis.

Full delegation guidance (when to delegate, when not to, error handling):

    ~/.conductor/delegation-guidance.md
<!-- conductor:end -->
