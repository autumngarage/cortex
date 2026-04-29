# Dependencies + Rust council review — Rust never; deps mostly NO; one v0.9.0 watch added

**Date:** 2026-04-28
**Type:** decision
**Trigger:** T1.1 (diff touches `.cortex/plans/cortex-v1.md`)
**Cites:** plans/cortex-v1, journal/2026-04-28-markdownfs-council-review, journal/2026-04-28-codesight-cross-pollination-and-council-review, doctrine/0005-scope-boundaries-v2

> User asked whether Cortex would benefit from runtime dependencies (markdown / YAML / git / schema-validation / observability libraries) and whether it should be rewritten in Rust before v1.0. A 3-member council via conductor (Gemini Pro, Kimi, DeepSeek V4 with Gemini+GPT synthesis) returned a unanimous "no Rust rewrite — ever — by the solo maintainer" plus mostly-NO on dependencies, with two corrections to the maintainer's brief. Final plan state: zero new v1.0 work items; one new v0.9.0 watch (frontmatter-parser dogfood watch + fork-decision); two new deferreds (Python-native single-binary distribution via `zipapp`/`shiv`/`pex` as the answer to distribution pain instead of Rust; a lightweight markdown AST library deferred against regex-breakage trigger); two closeds (Rust rewrite as a maintainer task; Pydantic for config-schema validation). The council also flagged a `Spec:` field on generated-artifact provenance headers as a "missing" v1.0 item; ground truth (caught across three Codex review iterations on PR #77) is that SPEC § 3.2 + § 3.3 example headers already include `Spec:`, the implementation already emits it, and only § 4.5's seven-field enumeration silently omits it — a one-word cleanup that rides on the existing v1.0 SPEC.md freeze item.

## Context

Cortex today: 8,753 lines of Python across 38 files, one runtime dependency (`click`), frontmatter / YAML / validation / goal-hash / manifest assembly all hand-rolled. The maintainer asked the question seriously after looking at markdownfs (Rust, single-binary distribution, in-memory VFS).

Brief named six dependency candidates (frontmatter, markdown AST, git, schema validation, indexing, observability) and the Rust question. Maintainer's initial lean: hand-rolled stays for v1.0; Rust deferred to "second implementer appears" trigger.

## What we decided

**Dependencies — five rejections, two corrections, one new watch trigger:**

1. **Pydantic — rejected, brief was wrong.** Member 1 caught the maintainer error: pydantic-core requires Rust compilation, which adds install footprint *and* CLI startup latency. For validating one `config.toml` file at v1.0 it is wildly over-budget. **Decision: stdlib `tomllib` + hand-rolled `ValueError` messages.** This reverses the brief's "lean toward yes" on Pydantic. Captured in the v1.0 `.cortex/config.toml` schema reference doc work item — the published schema does not require a Pydantic-derived implementation.

2. **Frontmatter parser — keep hand-rolled with explicit v0.9.0 watch.** Member 3's catch: `cortex init` ingests third-party `CLAUDE.md` / `AGENTS.md` / `principles/*.md` files that may contain nested YAML, lists, or anchors the hand-rolled parser surfaces as fatal errors. **Decision: keep hand-rolled, but add a named v0.9.0 dogfood watch.** If any of the three install targets crash on first contact, fork to either (i) tighten the spec to forbid the syntax (Member 2) or (ii) absorb a YAML library — `ruamel.yaml` is council's pick over PyYAML for safer extraction (Member 3). The fork-decision is itself a v0.9.0 work item.

3. **Markdown AST library — no for v1.0.** Regex-over-headings for section detection is fine. If dogfooding surfaces breakage on code blocks, `mistletoe` is the default revisit choice. Deferred with concrete trigger.

4. **`pygit2` / `dulwich` — no.** `subprocess.run(["git", ...])` is the right tool. `pygit2` is `libgit2` — a C dep — and the install footprint cost outweighs the API niceness for the few git operations Cortex does.

5. **`structlog` / observability — no.** Stderr + exit codes is the CLI contract. Adding structured logs is solving a problem Cortex doesn't have at v1.0 install scale.

6. **Indexing beyond `.cortex/.index.json` — no.** Grep is the right tool. The protocol explicitly punts semantic retrieval to downstream consumers (Doctrine 0005 #1, protocol § 1).

**Rust — never, by the solo maintainer.** The brief framed the question as "should the maintainer rewrite Cortex in Rust, and when?" Council unanimously rejected the framing. The "second implementer appears" trigger is for *spec extraction* (graduate SPEC.md to `autumngarage/cortex-spec`), not for the maintainer to rewrite. A 6–8 week port of 8.7k lines of text-munging plus subprocess orchestration is what Member 2 called "a resource-allocation death trap" for a solo author. Member 3 offered a softer trigger ("after v1.0, if 3 distinct support queries in 30 days are about Python install issues") but even Member 3 immediately offered a better path: Python-native single-binary distribution via stdlib `zipapp` (or `shiv` / `pex`). **Decision: Rust rewrite is closed as a maintainer task, not deferred.** If a second implementer wants Rust, it's their burden; the spec/CLI separation lands when that happens.

**Two genuinely new items (council found, brief missed):**

7. **No new v1.0 work item for spec-version on generated artifacts — council reasoned from a partial brief; ground truth is much closer to "already shipped."** Council framed this as "missing entirely"; on verification: SPEC § 3.2 (Map) and § 3.3 (State) example headers in the canonical spec **already include `Spec:`**; the implementation already emits `Spec: 0.5.0` in `state.md` provenance (`src/cortex/state_render.py`) and `.index.json` carries a `spec` field; the `.cortex/SPEC_VERSION` store-level marker drives read/write compatibility gating via `compat.py`. The only residual gap is that SPEC § 4.5 (the seven-field required-fields enumeration) silently omits `Spec:` from the list even though the example headers in § 3.2 and § 3.3 show it. That's a one-word consistency cleanup that rides on the existing v1.0 "SPEC.md freeze" work item's final review pass — no separate work item, no doctor changes. (An earlier draft of this journal entry and the plan added a full v1.0 work item for this; Codex review on PR #77 caught the over-scope on the third iteration. Per `principles/audit-weak-points.md` the lesson is captured below in the Reframe section.)

8. **Python-native single-binary distribution via `zipapp` / `shiv` / `pex` — deferred with concrete trigger.** Replaces "Rust for distribution win" entirely. If Homebrew tap maintenance produces real pain (Member 3 flagged this as a recurring solo-developer tax) or install-failure user reports accumulate, package Cortex as a single executable archive. **Decision: deferred follow-up; revisit on tap-maintenance pain or ≥3 user install-failure reports in 30 days.**

**Reframe (operative principle going forward):** the dep-aversion rule is not about "borrowed complexity" — Member 1's reframe is sharper. Every import blocks the CLI's first instruction; for an interactive tool the maintainer types dozens of times per session, **startup latency is the constraint that minimizes the import graph**, more than maintenance burden does. Same conclusion, more useful frame. Captured here as the operative principle; not promoted to a Doctrine entry per the markdownfs council's "Doctrine costs context-window tokens" precedent.

## Consequences / action items

- [x] Append a one-sentence note to the existing v1.0 "SPEC.md freeze" work item in `plans/cortex-v1.md` covering the trivial § 4.5 enumeration cleanup (add `Spec:` to the seven-field list so the contract matches the existing example headers and writer behavior). No separate work item; no doctor change.
- [x] Add frontmatter-parser dogfood watch + fork-decision work item to v0.9.0.
- [x] Add new `## Follow-ups (deferred)` subsection for 2026-04-28 deps+rust council with two items: single-binary distribution via `zipapp`/`shiv`/`pex`; lightweight markdown AST (`mistletoe`).
- [x] Add closed items to `## Resolved / closed`: Rust rewrite as maintainer task; Pydantic for config-schema validation.
- [ ] When the v0.9.0 frontmatter watch triggers (or doesn't), write a follow-up journal entry resolving the fork: spec-tighten vs. `ruamel.yaml` adoption.
- [ ] If Homebrew tap maintenance becomes painful, consider `zipapp` packaging *before* any cross-language reimplementation discussion reopens.
- [ ] **Process lesson — verify council findings against canonical spec sections before plan additions.** Both councils (markdownfs and deps+rust) made factually-grounded-but-wrong claims that the maintainer accepted on authority and propagated into plan adds. Codex review caught both, twice each. Future council briefs touching well-specified protocol concerns must include the canonical SPEC § verbatim in the brief, AND the maintainer must verify any "blind spot" claim against the named spec section before adding plan items. The two false-positive cycles cost ~20 minutes plus three commits of correction churn each.
