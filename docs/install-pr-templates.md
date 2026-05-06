# Cortex Install PR Templates

Use this copy when opening Cortex install PRs on sibling projects. It positions Cortex against the missing shared convention for project memory, not against named tools or adjacent products.

## Positioning Paragraph

Cortex is a protocol for agent project memory that treats your exact git repo as the memory store. Instead of introducing a new database, daemon, or vector index, it defines a directory of structured Markdown files (`.cortex/`) that agents evolve alongside code. It is grepable, diffable, and auditable with existing tools, adding the missing agent memory convention without replacing your workspace.

## Generating an install brief

Use `cortex install-brief <target-path>` to generate a self-contained brief
for delegating an install to an agent. The command detects the target's
ecosystem (Python / Swift / Rust / Go / Node / Ruby), distribution shape
(Homebrew tap, PaaS, or plain GitHub releases), Touchstone-managed paths, and
sibling repos with Cortex already installed:

```bash
cortex install-brief ~/repos/foo --output /tmp/install-foo.md
# Hand to an agent:
conductor exec --with codex --brief-file /tmp/install-foo.md
```

Flags:
- `--output PATH` — write to a file instead of stdout (default: stdout).
- `--no-references` — omit the five canonical prior-install PR references.
- `--closes N,N,...` — comma-separated issue numbers to track. When provided, the
  brief instructs the delegate to write **two** files instead of one:
  - `.cortex/journal/<date>-cortex-install-baseline.md` — append-only narrative.
    Issue references appear as `Refs:` in frontmatter, not as `[ ]` checkboxes
    (Journal is append-only per Protocol § 4.1 — boxes that can never flip are
    permanent stale claims).
  - `.cortex/plans/cortex-install-followups.md` — mutable plan with `Status: active`
    and `[ ]` checkboxes per tracked issue. Tracking lives here; the journal
    cites it via `Cites:`.

## Dual-artifact convention (installs with follow-up tracking)

When `cortex install-brief --closes <issues>` is used, the brief enforces the
layer contract:

- **Journal** = append-only record. Never evolves after authoring. Issue
  references appear as `Refs: cortex#N` in frontmatter — not as `[ ]` checkboxes.
  Putting `[ ]` in a journal entry creates a permanent stale claim the moment the
  upstream issue closes, because the Journal is append-only (Protocol § 4.1).
- **Plan** = mutable, checkbox-driven. All `[ ]` items live here. The plan
  transitions to `Status: shipped` when all referenced issues are resolved.

The journal entry's `Cites:` field links to the plan so readers find the tracking
from either artifact. The plan's `Cites:` field links back to the journal entry.

## Merging the install PR

Install PRs open on the *target* repository, not on the Cortex repo. The merge
path matters because Conductor review runs inside the target's own
`scripts/merge-pr.sh` gate — it caught a real stale-claim bug on
autumngarage/touchstone#151 (filed upstream as cortex#123).

**Preferred path — full Conductor review:**

From inside the target repo's working tree:

```bash
cd ~/repos/<target>
bash scripts/merge-pr.sh <pr-number>
```

This invokes Touchstone + Conductor review (where configured) and is the
documented merge gate for every Autumn Garage sibling. When the review is
clean the PR is squash-merged and the branch is deleted automatically.

**Fast path — bypasses Conductor review:**

```bash
gh pr merge <pr-number> --repo <owner>/<repo> --squash --delete-branch
```

Use this only when the target has no `scripts/merge-pr.sh` **or** when the PR
is provably metadata-only (zero changes to `src/`, no logic diff — only
`.cortex/`, `.gitignore`, `CLAUDE.md`, `AGENTS.md`).

**Why this matters:** The v0.9.0 install pass demonstrated the asymmetry
clearly. conductor#178 and touchstone#151 merged via `merge-pr.sh` with
Conductor review; sentinel#112 and vanguard#190 merged via the fast path
(direct `gh pr merge --squash`). Conductor review on touchstone caught a
stale-claim drift that the fast-path merges couldn't see. As the dogfood pool
grows past five targets, skipping the reviewed path becomes a systematic
safety-net gap, not an isolated shortcut. This section documents the
trade-off so the human or agent doing the merge knows which path they're on.

## Shared Install PR Body

```markdown
## Summary

This PR installs Cortex on this repository.

Cortex is a protocol for agent project memory that treats your exact git repo as the memory store. Instead of introducing a new database, daemon, or vector index, it defines a directory of structured Markdown files (`.cortex/`) that agents evolve alongside code. It is grepable, diffable, and auditable with existing tools, adding the missing agent memory convention without replacing your workspace.

## Changes

- Run `cortex init` and absorb the repository's existing instructions, plans, decisions, and project docs where applicable.
- Configure `.cortex/config.toml` for this repository's distribution surface and sibling-repo references.
- Capture the first-pass Cortex baseline in `.cortex/journal/`.
- Verify `cortex manifest --budget 8000`, `cortex next`, and `cortex doctor` on the new project memory.

## Testing

- `cortex manifest --budget 8000`
- `cortex next`
- `cortex doctor`
- `cortex doctor --audit-instructions`

## Notes

Cortex does not require Sentinel or Touchstone. If those tools are absent, Cortex should degrade visibly and continue operating from the `.cortex/` file contract.
```
