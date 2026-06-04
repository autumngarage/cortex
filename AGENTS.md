# AGENTS.md — AI Reviewer Guide for Cortex

<!-- touchstone:steering:start -->

<!-- This block is generated from TOUCHSTONE.md. `touchstone update` refreshes it.
     Edit content OUTSIDE the markers; touchstone will not touch project-owned content. -->

## Touchstone — Shared Agent Steering

You are an AI agent (Claude Code, Codex, or another driving CLI) working in a Touchstone-bootstrapped project. This block is the universal contract: rules that apply on every turn, plus a routing table to deeper docs you should consult when specific triggers fire. Project-specific guidance lives outside this block in your driver's steering doc (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`).

## Agent Roles And Fallbacks

- **Driving CLI** — Claude Code, Codex, or Gemini CLI. Owns file edits, git state, tests, commits, PR creation, Conductor review invocation, and merge. Drivers are interchangeable; driver fallback is shared-contract fallback — if one is unavailable, another reads the same files and continues.
- **Conductor worker/reviewer router** — the model router used by the driving CLI for review and bounded model work. Conductor can route to Claude, Codex, Gemini, Kimi, Ollama, or other providers, and provider fallback runs across configured backends, but Conductor does not replace the driver's responsibility for the branch → PR → merge-gate review → automerge workflow.

## Engineering principles (always in mind)

Non-negotiable. Every code change is reviewed against them. Full rationale lives in `principles/engineering-principles.md`.

- **No band-aids** — fix the root cause; if patching a symptom, say so explicitly and name the root cause.
- **Keep interfaces narrow** — expose the smallest stable contract; don't leak storage shape, vendor SDKs, or workflow sequencing.
- **Derive limits from domain** — thresholds and sizes come from input/config/named constants; test at small, typical, and large scales.
- **Derive, don't persist** — compute from the source of truth; persist derived state only with documented invalidation + rebuild path.
- **No silent failures** — every exception is re-raised or logged with debug context. No `except: pass`, no swallowed errors.
- **Every fix gets a test** — bug fix includes a regression test that runs in CI and fails on the old code.
- **Think in invariants** — name and assert at least one invariant for nontrivial logic.
- **One code path** — share business logic across modes; confine mode-specific differences to adapters, config, or the I/O boundary.
- **Version your data boundaries** — when a model/algorithm/source change affects decisions, version the boundary; don't aggregate across.
- **Separate behavior changes from tidying** — never mix functional changes with broad renames, formatting sweeps, or unrelated refactors.
- **Make irreversible actions recoverable** — destructive operations need dry-run, backup, idempotency, rollback, or forward-fix plan before they run.
- **Preserve compatibility at boundaries** — public API/config/schema/CLI/hook/template changes need a compatibility or migration plan.
- **Audit weak-point classes** — find a structural bug → audit the class + add a guardrail. Use the `touchstone-audit-weak-points` skill (Claude) or read `principles/audit-weak-points.md` (other drivers).
- **Isolate file-writing subagents** — parallel workers use dedicated worktrees, slice manifests, and disjoint file ownership by default.
- **File issues for bugs** — open a GitHub issue when you find a bug, in this project or in an autumngarage tool. Don't silently work around it.
- **Escalate delivery friction upstream** — if Conductor or Touchstone causes workflow drag (excessive token burn, weak parallelization, unclear delegation ergonomics, brittle merge-gate behavior, or other agent-delivery inefficiency), file an actionable upstream issue with repro steps and impact instead of normalizing the pain.

## Never commit on the default branch

Before the first edit of a tracked file in a session, run `git branch --show-current`. If it reports the default branch (`main` or `master`), branch first with `git checkout -b <type>/<slug>` where `<type>` is `feat | fix | chore | refactor | docs`. Your unstaged changes carry over — there's no cost to switching now and a real cost to discovering at commit time. Recovery steps when it happens anyway live in `principles/git-workflow.md`.

## Required Delivery Workflow

Drive this lifecycle automatically; do not ask the user for permission at each step.

1. **Pull.** `git pull --rebase` on the default branch.
2. **Branch.** Before any edit that might become a commit.
3. **Claim issues before implementation.** If the work starts from a GitHub issue, claim it before editing or dispatching an agent: `bash scripts/claim-issue.sh <n>`. Claim every issue in a multi-issue bundle so two agents do not ship competing fixes.
4. **Change + commit.** Stage explicit file paths. Concise message. One concern per commit.
5. **Reconcile issues.** Before opening the PR, list every GitHub issue found, claimed, fixed, partially fixed, or made stale by the work. Fully fixed issues get closing trailers (`Closes-issue: #123` or `Closes #123`) so merge auto-closes them; partial/stale issues get a comment explaining the evidence or remaining gap. Do not leave fixed issues open silently.
6. **Open PR + ship through the merge gate.** `bash scripts/open-pr.sh --auto-merge` pushes, opens the PR, runs the merge-gate pipeline, squash-merges, and syncs the default branch. The required expensive gates happen at merge time: deterministic checks, Conductor LLM review/fix loop, then deterministic checks again only if Conductor changed the PR head.
7. **Clean up.** Delete the local branch if it persists.

Do not bypass the PR/review/merge path with a direct default-branch push except through the documented emergency path in `principles/git-workflow.md`.

## Memory hygiene

- Treat AI-agent memory as cached guidance, not canonical truth. Verify a remembered command, flag, path, or version against this repo before relying on it.
- Don't write memory for facts that are cheap to derive from `README.md`, the steering files, `VERSION`, `bin/touchstone --help`, or the scripts.
- If memory mentions a command, flag, file path, version, or workflow, include the date (`YYYY-MM-DD`) and the canonical source checked.
- If memory conflicts with the repo, follow the repo and propose updating the stale memory.

## Routing table — read these when the trigger fires

| When you're about to... | Read |
|---|---|
| commit, branch, open a PR, run review, merge, recover from `no-commit-to-branch`, work with stacked PRs, or fan out worktrees | `principles/git-workflow.md` |
| understand the AI-authored change lifecycle, merge-gate review architecture, or where Conductor fits | `principles/ai-delivery-architecture.md` |
| start a non-trivial code change | `principles/pre-implementation-checklist.md` |
| understand the *why* of a daily-reminder rule | `principles/engineering-principles.md` |
| edit, write, or audit documentation | `principles/documentation-ownership.md` |
| coordinate parallel agents (subagents, worktrees, conductor swarm) | `principles/agent-swarms.md` |
| audit a structural bug class after fixing one instance | `principles/audit-weak-points.md` |
| hit a bug in an upstream tool (don't silently work around it) | `principles/file-upstream-bugs.md` |
| write a `.cortex/` artifact or see a Tier-1 trigger fire | `.cortex/protocol.md` |
| delegate to Conductor — pick a provider, write a brief, choose `--kind` / `--effort` | `~/.conductor/delegation-guidance.md` |

Claude Code agents: the Touchstone-bundled user-scoped skills (`touchstone-git-workflow`, `touchstone-pre-impl`, `cortex-protocol`, `conductor-delegation`, `touchstone-audit-weak-points`, `touchstone-agent-swarms`, `memory-audit`) provide the same routing surface as this table, with descriptions in your session header. Trust whichever surface fires first.

## Orientation

If `.cortex/state.md` exists in the project, read it at session start for the current state of in-flight work.

<!-- touchstone:steering:end -->


You are reviewing pull requests for **Cortex**, a file-format protocol for project memory. Optimize your review for catching the things that bite this repo, not generic style polish.

This file is the source of truth for how AI reviewers (Codex, Claude, etc.) should think about a PR. The companion file `CLAUDE.md` is for the *author* writing the code; this file is for the *reviewer*.

## Cortex Protocol (context for spec-aware review)

@.cortex/protocol.md

---

## Unexpected Journal Dirt Recovery

Tracked `.cortex/journal/*.md` files are project memory; do **not** add them to `.gitignore`. When an unexpected Journal file appears in the worktree, classify it before staging anything:

- Expected source-branch entry: keep it with the current PR only when it records that branch's decision, incident, release, or merge context.
- Unexpected file on `main`/`master`: do not commit it to the default branch. Check recent hook output and `git log` to decide whether it belongs on a named recovery branch or duplicates an already-landed entry.
- Stranded auto-draft `pr-merged` entry: preserve it on the hook's `docs/journal-pr-*` recovery branch when possible; remove it only after confirming a replacement or duplicate exists, and record the reason.
- Unclear provenance: stop and surface the path, `git status --short`, current branch, and recent hook output instead of guessing.

This preserves the Journal append-only invariant without folding generated merge metadata into unrelated work.

---

## What to prioritize (in order)

1. **Spec integrity.** Cortex is a spec-first project. Any PR that changes `SPEC.md` must bump the spec version per SPEC.md §6 — major for breaking, minor for additive, patch for clarifications. PRs that change implementation behavior without a matching spec update (when spec applies) must be flagged. Silent drift between spec and implementation is the highest-severity failure mode.

2. **Layer contract violations.** The CLI must never:
   - Overwrite or mutate a Journal entry (append-only invariant)
   - Delete a Doctrine entry (immutable-with-supersede invariant)
   - Write a regenerated layer (Map, State) without a `Generated:` header and source list
   - Leave an orphan deferral (deferred Plan item that doesn't resolve to another Plan, Journal entry, or Doctrine entry within the same commit)
   - Accept a Plan without explicit Success Criteria

3. **Graceful degradation across composition boundaries.** Cortex must run on a bare repo without Sentinel or Touchstone installed. Any code path that hard-requires `.sentinel/` or `.touchstone-config` is a composition bug — it must be an `if-present, read; else, degrade` pattern.

4. **Silent failures.** New `except: pass`, swallowed exceptions, fallbacks that mask broken state. For Cortex specifically: watch for "if refresh fails, keep the stale file" patterns — those are silent-staleness vectors and violate the project's design principles.

5. **Tests for new failure modes.** Bug fixes must add a test that reproduces the original failure. Structural rules in SPEC.md should have validation tests (e.g., orphan-deferral detection).

Style nits, formatting, and theoretical refactors are **out of scope** unless they hide a bug. Do not flag them.

---

## Specific review rules

### High-scrutiny paths

- **`SPEC.md`** — the primary artifact. Any change here must include a version bump in the header and a rationale in the commit message. Breaking changes (major bump) require evidence of migration plan for existing `.cortex/` users (even if currently just this repo's dogfood).
- **`.cortex/plans/*.md`** — active Plans drive scope. One is currently active per `.cortex/state.md` `## Current work`: `cortex-v1.md` (v0.3.0 → v1.0.0 release-driven sub-sections under production-on-real-project framing per [`journal/2026-04-24-production-release-rerank.md`](./.cortex/journal/2026-04-24-production-release-rerank.md)). The v0.2.4 → v0.2.5 patch plan (`init-ux-fixes-from-touchstone.md`) shipped 2026-04-25 — see [`journal/2026-04-25-init-ux-fixes-plan-shipped.md`](./.cortex/journal/2026-04-25-init-ux-fixes-plan-shipped.md). Flag if any release / Success Criterion exit bar is vague or unmeasurable ("it works well" is not a criterion); flag if a deferred item lacks a citation to a Plan, Journal entry, or Doctrine entry per SPEC § 4.2.
- **Code that reads `.cortex/` layer files** — regexes and parsers for status blocks, checkbox syntax, front-matter. Must be tolerant of minor format drift (extra whitespace, trailing newlines) but strict on contract violations.
- **Code that writes `.cortex/` layer files** — every write to a derived layer (Map, State) must include the `Generated:` header. Every write to an immutable layer (Doctrine, Journal) must verify the target file does not already exist.
- **Integration code (Sentinel/Touchstone detection)** — must be `shutil.which` + filesystem checks, never imports or subprocess calls into those tools.

### Silent failures

Flag any of the following:

- New `except: pass`, `except Exception: pass`, or `except: ...` without logging.
- Broad `try / except` that continues without logging the exception.
- "If staleness detection fails, treat as fresh" or any variant that hides drift.
- Fallback behavior that produces an apparently-valid `.cortex/` layer from incomplete inputs without surfacing the gap.
- Default values returned from regeneration paths on partial source inputs. If source material is missing (no `.sentinel/runs/`, no git history), the regenerated layer should say so explicitly, not quietly proceed with less.

The rule: every failure must be visible to someone — the CLI user, the log output, or the generated layer itself.

### Tests

- Bug fixes must include a test reproducing the original failure mode.
- Structural spec rules (checkbox parsing, orphan-deferral detection, header format) should have tests that fail on the violating input.
- Integration tests should use real temp-dir `.cortex/` fixtures, not deep mocks of the filesystem.
- Tests for synthesis commands (`refresh-map`, `refresh-state`, `journal draft`) should use recorded-LLM-response fixtures, not live `claude` CLI calls.

---

## What NOT to flag

- Formatting, whitespace, import order — pre-commit hooks handle these.
- Type annotations on existing untyped code.
- "You could refactor this for clarity" — only if the unclarity hides a bug.
- Missing docstrings on small private functions.
- Speculative future-proofing — don't suggest abstractions for hypothetical future requirements.
- Naming preferences absent a clear convention violation.
- Cross-project concerns (portfolio / Lighthouse-shaped work) — explicitly out of scope.

If you find yourself writing "consider" or "you might want to" without a concrete bug or risk attached, delete the comment.

---

## Output format

1. **Summary** — one paragraph: what this PR does and your overall verdict (approve / request changes / comment). If the PR touches `SPEC.md`, state explicitly which spec version bump applies.
2. **Blocking issues** — bugs or risks that must be fixed before merge. Each item: file:line, what's wrong, why it matters, suggested fix.
3. **Non-blocking observations** — things worth noting but not blocking. Keep this section short.
4. **Spec/implementation alignment** — if the PR touches either the spec or the CLI behavior, confirm the other side is consistent. If not, name the drift.
5. **Tests** — does this PR add tests for the changed behavior? If structural, does it add validation tests? If not, is that OK?

If there are zero blocking issues, the review is just: "LGTM."

<!-- conductor:begin v0.10.32 -->
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
