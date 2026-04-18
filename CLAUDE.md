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
- Do not write memory for facts cheap to derive from `README.md`, `SPEC.md`, `PLAN.md`, or the code itself.
- If you write memory mentioning a command, flag, file path, version, or workflow, include the date (YYYY-MM-DD) and the canonical source checked.

## Cortex-Specific Principles

- **Spec before implementation.** The protocol in SPEC.md is the primary artifact. Don't code ahead of the spec; amend the spec first, then implement. Spec version bumps follow SPEC.md §6.
- **Dogfood as the readiness bar.** Each phase exits by running the CLI against a real repo — Sentinel first, Touchstone second, sigint third. If the output needs more than one editing pass, the phase isn't done.
- **Compose by file contract, not code.** Cortex does not import Sentinel, Touchstone, or anything they own. Integration is: "if `.sentinel/runs/` exists, read it; if `.touchstone-config` exists, respect it; otherwise degrade gracefully." Match the pattern Sentinel already uses for Touchstone detection.
- **Synthesis via `claude` CLI, no SDK.** Same convergent-CLI pattern Sentinel uses. No stored keys inside Cortex. No provider abstraction layer duplicating Sentinel's — shell out to `claude -p` directly for the synthesis commands that need it.
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

v0.1.0 shipped. Distribution: the Homebrew tap `autumngarage/homebrew-cortex` hosts the formula (`brew tap autumngarage/cortex && brew install autumngarage/cortex/cortex` — fully qualified to side-step the unrelated Prometheus `cortex` in homebrew-core); `uv tool install git+...` works for source installs. Release flow mirrors Sentinel: version bump in `__init__.py` + `pyproject.toml`, tag, push, `gh release create`, update the formula's `url` + `sha256` in `autumngarage/homebrew-cortex`.

Each release must also declare which spec version it supports (in `SUPPORTED_SPEC_VERSIONS` in `src/cortex/__init__.py`). A minor CLI release cannot change the spec's major version; those travel together.

## Architecture

Python CLI (click + uv-managed venv) organized around layer commands. v0.1.0 ships the non-synthesizing surface; regeneration is Phase C.

- `cortex init` — scaffold `.cortex/` per SPEC.md
- `cortex status` / `cortex doctor` — validate and report
- `cortex refresh-map` / `cortex refresh-state` — regenerate derived layers via `claude` CLI
- `cortex plan spawn` / `cortex journal draft` — author helpers for Plans / Journal
- No background daemon; all writes are explicit CLI invocations.

## Key Files

| File | Purpose |
|------|---------|
| `SPEC.md` | The `.cortex/` file-format protocol, versioned (currently v0.3.1-dev draft) |
| `PLAN.md` | Build plan, phases A–E with exit criteria and success criteria |
| `README.md` | The story and composition narrative |
| `docs/PRIOR_ART.md` | Research synthesis behind the spec's design rules (ADRs, Diataxis, WAL, Zettelkasten, MemGPT, Voyager) |
| `.cortex/` | This repo's own Cortex dogfood — Doctrine + Journal entries about Cortex itself |
| `principles/*.md` | Touchstone-managed engineering principles (synced via `touchstone update`) |
| `scripts/*.sh` | Touchstone-managed helpers (open-pr, merge-pr, cleanup-branches) |

## State & Config

- Project-owned files include `CLAUDE.md`, `AGENTS.md`, `.codex-review.toml`, `.pre-commit-config.yaml`, `setup.sh`, and the Cortex-native files in this repo (`SPEC.md`, `PLAN.md`, `README.md`, `docs/`, `.cortex/`).
- Touchstone-managed files live in `principles/` and `scripts/` and are synced via `touchstone update`.
- No runtime config yet. When the CLI ships, per-project config (if any) will live in `.cortex/config.toml` or be derived entirely from `.cortex/` contents.

## Hard-Won Lessons

Too new for project-specific lessons. The spec's design rules are themselves distilled lessons from sigint's manual practice and from the wider literature (see `docs/PRIOR_ART.md` §1, and the "Pain points" section). Notable transfers:

- **Premature completion declarations** (sigint's `COLLECTOR_MIGRATION.md`, 2026-04-05) → SPEC.md requires measurable Success Criteria on every Plan.
- **Silent staleness in aggregators** (sigint's resolution-pipeline death, 2026-04-05 through 04-09) → SPEC.md requires `Generated:` headers with source lists on every derived layer; stale-beyond-threshold surfaces as warning.
- **Scattered deferrals with no consolidation** (sigint's multiple plans) → SPEC.md §4.2 requires deferred items to resolve to another plan or journal entry in the same commit.
