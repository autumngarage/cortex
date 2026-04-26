# AGENTS.md — AI Reviewer Guide for Cortex

You are reviewing pull requests for **Cortex**, a file-format protocol for project memory. Optimize your review for catching the things that bite this repo, not generic style polish.

This file is the source of truth for how AI reviewers (Codex, Claude, etc.) should think about a PR. The companion file `CLAUDE.md` is for the *author* writing the code; this file is for the *reviewer*.

## Cortex Protocol (context for spec-aware review)

@.cortex/protocol.md

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
