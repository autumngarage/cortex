# markdownfs council review — outward positioning + Journal accretion gap

**Date:** 2026-04-28
**Type:** decision
**Trigger:** T1.1 (diff touches `.cortex/plans/cortex-v1.md`)
**Cites:** plans/cortex-v1, journal/2026-04-28-codesight-cross-pollination-and-council-review, doctrine/0005-scope-boundaries-v2

> User pointed at https://github.com/subramanya1997/markdownfs as a recently-discovered adjacent project; asked which ideas (if any) Cortex should absorb. A 3-member council via conductor (Gemini Pro, Kimi, DeepSeek V4 with Gemini+GPT synthesis) pushed back on the initial framing — markdownfs is a *different category* (workspace-as-database / infrastructure) that just shares "markdown" as a surface, not a competitor — and surfaced one genuine blind spot (Journal accretion) plus one outward-facing positioning gap that activates at v0.9.0.

## Context

markdownfs (Rust, in-memory virtual filesystem with disk persistence, content-addressable storage, reimplemented git-style VCS, multi-user permissions, three peer access methods CLI/HTTP/MCP) is the second project in the "agent workspace / agent memory" space surfaced this week, after codesight. Unlike codesight (which inspired three integrations into the v1.0 plan), markdownfs's core architectural bet is the *opposite* of Cortex's: replace the host's git + filesystem + tools with a self-contained store, vs. compose with the project's existing git + grep + gh + IDE.

The initial maintainer read suggested adding a Doctrine entry codifying the storage-philosophy contrast. The council disagreed, sharply.

## What we decided

**Three actions, two non-actions:**

1. **No Doctrine entry on storage philosophy.** The contrast is already implicit in SPEC.md and `.cortex/doctrine/0005-scope-boundaries-v2`; explanations of *why* belong in outward-facing docs, not in machine-readable Doctrine where context-window tokens matter. Council was unanimous on the first two members; member 3 dissented but was outvoted on the maintenance-cost argument.

2. **No spec edit on "Cortex is a substrate for downstream semantic indexes."** Council recommended adding this; on review, `.cortex/protocol.md § 1` already says it: *"Projects that want semantic retrieval wire up their own index over `.cortex/` as a read-side layer; that index is out of scope for the Protocol."* Adding a parallel SPEC.md sentence duplicates without clarifying — fails `principles/documentation-ownership.md` rule 1.

3. **No MCP transport at v0.9.0.** Council confirmed the deferral. markdownfs's MCP surface is just CRUD verbs (`read_file`, `write_file`, `list_directory`, `search_files`, `find_files`, `commit`, `revert`); reinforces that when Cortex un-defers MCP later, it's a thin adapter, not a new design problem. Existing revisit conditions in [`journal/2026-04-28-codesight-cross-pollination-and-council-review`](./2026-04-28-codesight-cross-pollination-and-council-review.md) stand unchanged.

4. **Add v0.9.0 work item — outward-facing positioning paragraph.** The moment Cortex installs on conductor / vesper, discovery starts and "how is this different from markdownfs / Letta / MemGPT / mem0" becomes a live question. v1.0 is too late. Council's draft preserved as the starting point: *"Cortex is a protocol for agent project memory that treats your exact git repo as the memory store. Instead of introducing a new database, daemon, or vector index, it defines a directory of structured Markdown files (`.cortex/`) that agents evolve alongside code. It is grepable, diffable, and auditable with existing tools — augmenting your repo with an agent memory contract rather than replacing your workspace."* Position against the *absence* of conventions, not against named competitors (engaging them accepts their framing).

5. **No new deferral — the council's "missing archival path" claim was a brief-coverage error.** The brief described Journal append-only (§ 4.1) and Doctrine immutable-with-supersede (§ 4.2) but did not include SPEC § 5.1, which specifies tiered Hot (0–30d) → Warm (30–365d) → Cold (>365d, auto-moved to `journal/archive/<year>/`) retention plus Plan auto-archive after 30d in shipped/cancelled. The council reasoned from the partial brief and surfaced what looked like a blind spot ("old raw entries never move; grep cost grows linearly forever"), and the maintainer initially propagated the claim into a new deferred subsection on `plans/cortex-v1.md`. **Codex review on PR #77 caught the drift before merge.** Resolution: the existing **"Retention automation (cleanup, not visibility)"** deferred item already captures the implementation work (visibility ships in v0.6.0; destructive auto-move per SPEC § 5.1 defers post-v1.0). That item gains a "Reaffirmed 2026-04-28" pointer to this entry rather than carrying a duplicate alongside it. **Lesson:** when delegating a council brief, include the canonical-doc § that defines the area in question, or a council member with a partial picture will surface a non-finding the maintainer is likely to accept on authority.

## Consequences / action items

- [x] Add v0.9.0 outward-facing positioning paragraph work item to `plans/cortex-v1.md` (sequenced before install PRs — see plan).
- [x] Drop the duplicative "Journal accretion / archival strategy" deferred subsection from `plans/cortex-v1.md` (Codex catch on PR #77; the existing "Retention automation (cleanup, not visibility)" item already covers it per SPEC § 5.1). Reaffirmation pointer added to that item instead.
- [ ] Use the council positioning draft verbatim or near-verbatim when the v0.9.0 positioning work item ships; do not engage markdownfs / Letta / MemGPT by name.
- [ ] When delegating future council briefs touching retention, archival, digest, or any well-specified protocol concern, include the canonical SPEC § verbatim in the brief so council members reason from the full picture rather than from invariants alone.
