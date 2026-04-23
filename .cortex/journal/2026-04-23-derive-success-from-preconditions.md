# Derive success claims from preconditions, don't assert them

**Date:** 2026-04-23
**Type:** decision
**Trigger:** T2.5
**Cites:** https://github.com/autumngarage/cortex/pull/27, principles/engineering-principles.md, .cortex/doctrine/0003-cortex-is-the-reflective-layer.md

> A CLI command that claims an outcome must derive that claim from the full precondition set it just checked, never from the last step that happened to succeed. "We wrote to .gitignore" is not evidence that ".cortex/ will not be published"; only a verified list of preconditions is. Any drift between claim and check is a false-assurance bug.

**inferred-invariant:** true

## Context

PR #27 added `cortex init --local-only`. The Codex review loop ran eight rounds before it cleared — an unusually high count for a small flag. Three of those rounds (#2, #3, #6) were variations of the same structural bug:

- Round #2: the success message said `.cortex/ will not be published` immediately after writing `.cortex/` to `.gitignore`. But `.gitignore` does not untrack files already in git's index, so repos converting to local-only kept publishing the previously-committed content. The claim was true for one precondition (`.gitignore` updated) and load-bearing against an unchecked one (nothing in the index).
- Round #3: the same success message was still printed when `CLAUDE.md` / `AGENTS.md` already imported `@.cortex/protocol.md` from a prior shared-mode init — dangling imports after `.cortex/` became gitignored. A second unchecked precondition.
- Round #6: `_tracked_cortex_files` collapsed two outcomes into one empty list: "git said 0 files" and "git failed to run." Callers that branched on `tracked == []` could print the success message after a failed check. A third unchecked precondition — the check's own validity.

The common shape: the code asserted a conclusion, then bolted on precondition checks later when each one was individually pointed out. Each round added one more condition the claim depended on. The structural fix is to derive the claim from "all checks clean" rather than assert it next to any single one.

## What we decided

**For any user-facing success message, the text is a function of the full precondition set, not a consequence of reaching a particular line of code.**

Concretely, this changes how we write CLI output:

- Enumerate the preconditions a claim depends on before writing the claim. "`.cortex/ will not be published`" depends on: (a) `.gitignore` includes `.cortex/`; (b) no `.cortex/` files are currently tracked; (c) no files committed outside `.cortex/` reference it as a path. Drop a condition and the claim is a lie.
- Run each check and compute a local `ok` per precondition. Print the success claim only when every `ok` is true. If any check is unknown (subprocess failed, file unreadable), print an uncertainty warning — never the success claim.
- Wrap subprocess queries in tri-state returns (`result | None`), so callers can distinguish "conclusively no" from "could not determine." Silent collapse of those two states is the single biggest source of false assurance across the review loop.
- For user-executable advice (remediation hints, copy-paste commands), treat the string as executable code: anchor it to the target (not cwd) and shell-quote every interpolated token. A remediation that breaks on a path with spaces is worse than no remediation — the user believes they ran the fix.

This is the engineering-principles.md "derive, don't persist" rule applied to output UX: the success claim is derived state, not persisted state. Persisting it (by writing it right after one precondition) creates the same silent-staleness bug as persisting any derived state anywhere else.

## How to apply

- `cortex init`: the `--local-only` post-check now gates the "will not be published" echo on `tracked == [] AND not dangling_import_files` (SPEC-derived claim from the checks that just ran).
- `cortex doctor`: already leans this way (exit 0 ≡ no violations). Extend the rule to the human-readable summary — if any check was skipped or unknown, say so rather than imply cleanliness.
- `cortex status`: when `.cortex/.index.json` is absent, the promotion-queue line already says "not yet initialised" instead of "0 candidates." Keep that shape for any new derived field.
- Phase C `cortex refresh-map` / `cortex refresh-state`: every `Generated:` header's `Incomplete:` field is the machine-readable version of this same rule. A non-empty `Incomplete:` is the layer's own admission that it could not derive its claim from a complete input set.
- Phase E integrations: any Touchstone or Sentinel hook that reports "Cortex invariants hold" must route through `cortex doctor --strict` (an exit code derived from all checks), not a lightweight subset.

## Consequences / action items

- [x] Extract shared subprocess + remediation helpers into `src/cortex/shell.py` (`run_git` returning `GitRun`, `git_remediation_cmd` builder). Single authority for the tri-state + shell-quoting invariants.
- [x] Migrate `_tracked_cortex_files` and the `--local-only` remediation strings in `cortex init` to use the helpers — no more inline subprocess or f-strings with interpolated paths.
- [ ] Retrofit `cortex doctor`'s summary output to emit an explicit uncertainty line (instead of a clean message) when any check was skipped for reasons other than "not applicable." Track in a follow-up plan; this entry is the motivation for that work.
- [ ] When SPEC.md next rev-bumps, add § cross-references to this entry from the Map/State layer contract (derived layers are the SPEC-level instance of this principle).
