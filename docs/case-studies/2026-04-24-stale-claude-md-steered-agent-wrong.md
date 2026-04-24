---
date: 2026-04-24
project: conductor
incident-type: stale-memory
severity: medium
cortex-relevance: high
---

# Case study: stale CLAUDE.md steered an agent to recommend the wrong install path

A short, concrete incident that maps onto several Cortex design concerns:
Documentation Ownership, the "Derive, don't persist" principle, Tier 1
triggers on release-affecting events, and the problem of trusting
load-at-session-start imports as ground truth.

## The incident

A user asked their agent (Claude Code, working on the `conductor` repo):

> why do we use pip instead of homebrew

The agent answered with confidence:

> Homebrew isn't wired up yet. The tap was planned for v0.1.0 but
> deferred. `pip install` is the reliable current option.

The user pushed back:

> we should use homebrew right? this is more in line with how all the
> other tools are setup

On the next turn the agent actually looked at the filesystem instead
of relying on memory, and found:

- A sibling repo `~/Repos/homebrew-conductor` checked out locally.
- Formula: `Formula/conductor.rb`, fully authored, with a correct
  build recipe using `uv venv` + `uv pip install`.
- Six release commits spanning `v0.2.1` through `v0.3.3`.
- Matches the distribution pattern used by every other Autumn Garage
  peer (`autumngarage/homebrew-touchstone`, `…/sentinel`, `…/cortex`).

The agent's claim was wrong. The tap was not "planned and deferred" —
it had shipped eight releases ago.

## Where the false belief came from

Two load-bearing files inside the `conductor` repo contradicted reality:

**`conductor/CLAUDE.md` line 65:**

> Homebrew formula via `autumngarage/homebrew-conductor` tap (planned
> for v0.1.0; not yet wired). [...] Pip install also supported
> (`pip install conductor`).

**`conductor/README.md`, "Deferred" scope list:**

> - Brew tap for `brew install autumngarage/conductor/conductor`.

Both files are imported at every session start — CLAUDE.md directly,
`.cortex/state.md` via the Cortex protocol's session manifest — and
both contain a claim that stopped being true somewhere between v0.2.0
and v0.2.1 when `homebrew-conductor` was first published.

No entry in `conductor/.cortex/journal/` records the tap going live. No
doctrine was added pointing at the tap as the canonical install path.
The `state.md` in this project is still the scaffolded placeholder
written by `cortex init`.

## Why the agent didn't catch it

The agent's session-start manifest loaded `CLAUDE.md`, treated its
claims as background context for the whole session, and generated an
answer consistent with that context. The contradictory signal — a
sibling repo named `homebrew-conductor` on the same filesystem — was
never surfaced because nothing prompted the agent to look for it.
The engineering-principles "Memory Hygiene" section explicitly warns
about this pattern ("Treat Claude Code memory as cached guidance, not
canonical truth. Before relying on a remembered command, flag, path,
version, or workflow, verify it against this repo"), but it's guidance
to the agent, not an automated check.

The mistake was self-correctable the moment a human pushed back —
one `ls ~/Repos/homebrew-conductor` was all it took. But the user had
to provide the skepticism. An agent working alone, or an agent
answering a question the user couldn't verify, would have left the
wrong belief in place.

## Why this is a Cortex problem, not just a documentation problem

A one-off "fix CLAUDE.md" response would patch the symptom. The
structural issues this incident exposes are all inside Cortex's
current scope:

1. **Documentation Ownership violation.** The fact "where does
   conductor install from?" had no single canonical owner. Two files
   (CLAUDE.md, README.md Deferred list) both made claims, the
   `homebrew-conductor` repo was the real source of truth, and no
   mechanism reconciled the three. This is precisely the anti-pattern
   `principles/documentation-ownership.md` targets ("one canonical
   owner per fact; duplicated volatile facts drift and create
   contradictions that erode trust in all the docs"). In practice,
   nothing enforces the principle when prose duplicates itself.

2. **"Derive, don't persist" violation.** The install-path claim in
   CLAUDE.md is a derived fact — it's "true if the tap repo is
   published and has at least one release." It shouldn't live as
   prose in a hand-maintained file. It should be computed from the
   `homebrew-conductor` repo's release state, or at minimum point at
   that repo as the source of truth.

3. **Missing Tier 1 trigger.** The Cortex protocol's Tier 1 triggers
   include "Dependency manifest changed" and "Commit message matches
   patterns: fix: … regression, refactor: … (removes|introduces),
   feat: … (breaking|replaces)" — but not "a release / distribution
   artifact was created or shipped." When `homebrew-conductor` was
   published, there was no structural reason for a journal entry to
   land in conductor's `.cortex/journal/`. So the journal contains no
   trace that the tap shipped, which means:
   - No digest would mention it.
   - No doctrine candidate would emerge from it.
   - No `state.md` refresh would pick it up.
   - The stale CLAUDE.md claim persists indefinitely.

4. **No audit across the fourth wall.** Cortex checks internal
   consistency (is journal append-only, are doctrine entries
   properly superseded). It does not check whether claims inside
   the repo match the state of related external artifacts — sibling
   repos, published tags, PyPI presence, brew taps. Every project in
   the Autumn Garage has sibling repos it depends on or cross-links
   to; this is a structural audit surface Cortex could own.

5. **Session manifest trusts its inputs.** The default manifest
   (`cortex manifest --budget N`) loads state.md and pins of doctrine
   as ground truth. When those inputs contain stale claims, the
   agent inherits them uncritically. There's no field in the
   manifest output that says "this fact was last verified at X"
   or "this doctrine was written before release Y, re-verify."

## What solving this with Cortex could look like

Not a design proposal — just the natural affordances this incident
argues for, so the real design can respond to them.

- **A trigger for release-adjacent events.** T1.N: "release workflow
  published (new tag pushed, brew formula updated, PyPI package
  released, Docker image tagged)." The Cortex protocol today
  catches `.cortex/plans/` status changes and commit-message
  patterns; it does not catch "we just made this thing installable
  via a new channel." A new trigger (plus template) would leave a
  journal entry on every release event, creating the audit trail
  this incident lacks.

- **A staleness check specifically for CLAUDE.md / AGENTS.md prose.**
  `cortex doctor --audit-instructions` (or whatever it gets named)
  scans the project's agent-instruction files for claims about
  external artifacts (paths, URLs, versions, "planned," "deferred,"
  "not yet wired"), and surfaces any where the corresponding
  external artifact's state contradicts the prose. Implementation
  would need project-specific configuration — "the
  `homebrew-<project>` repo is the source of truth for install
  path" — but the plumbing is reusable across every garage tool.

- **Cross-repo journal import.** When `homebrew-conductor` lands a
  release bump, the conductor project should see a journal entry
  saying so. Cortex could offer an opt-in "watch these sibling
  repos and mirror their release events into our journal" hook, so
  the consumer project's memory automatically absorbs the publisher's
  state changes. This is the design principle several sibling repos
  already share — "Touchstone and Sentinel shell out to Conductor,
  they don't import it" — extended to the memory layer.

- **Manifest provenance.** Every fact the session manifest loads
  could carry a "verified-against" timestamp or a "derived-from"
  pointer. An agent that reads "Homebrew tap not yet wired
  (verified-against 2025-11-17)" is much more likely to double-check
  than an agent that reads the same prose with no provenance.

## What this case study is NOT

- Not an argument that Cortex should police every line of prose.
  CLAUDE.md legitimately holds narrative and principles; not
  everything in it is a derivable fact. The audit needs to be
  targeted at claims about external artifacts specifically.
- Not a plea for automation on every release. Release events in
  small projects are infrequent enough that a simple journal entry
  per release is plenty.
- Not a bug report on the conductor repo. The conductor repo will
  update its docs as a follow-up; the structural lessons are for
  Cortex.

## Recommended follow-up

- Drop this file as-is into `docs/case-studies/` as a durable
  reference.
- When the corresponding Cortex feature is designed (release-event
  trigger, instruction-file audit, cross-repo journal imports —
  whatever the team decides), cite this case study in the plan doc
  so the motivating incident is traceable from the design decision.
- In parallel, the conductor repo fixes its own CLAUDE.md and README
  so the specific false belief stops propagating — but the structural
  fix for "how does this class of mistake stop happening across every
  Autumn Garage tool" is the Cortex work.

## Appendix: the exact false statements

For posterity, the agent's exact wrong claims during the session:

> Short answer: we don't — Homebrew isn't wired up yet. I used
> `pip install conductor` in the summary because that's the install
> path that actually works today.

and

> The work to bring it forward, if you want:
>   1. Create the `autumngarage/homebrew-conductor` tap repo (external
>      side effect — needs your say-so).
>   2. Write a Formula…
>   3. GitHub Actions on tag push…
>   4. Update the README to lead with `brew install ...`…

Items 1, 2, and the mechanism behind 3 were already done months
earlier. The agent proposed them as new work to be scoped.

## Appendix: contradicting ground truth at incident time

- `~/Repos/homebrew-conductor/Formula/conductor.rb` — fully authored
  Formula pinned at v0.3.3.
- `~/Repos/homebrew-conductor` git log — six release commits.
- Sibling taps (`homebrew-touchstone`, `homebrew-sentinel`,
  `homebrew-cortex`) all present and wired identically.
- `conductor/CLAUDE.md` line 65 — "planned for v0.1.0; not yet
  wired."
- `conductor/README.md` Deferred list — "Brew tap for `brew install
  autumngarage/conductor/conductor`."
