# Vision draft — first multi-agent critique round (Codex + Gemini)

**Date:** 2026-04-17
**Type:** decision
**Cites:** plans/vision-sharpening, doctrine/0001-why-cortex-exists, doctrine/0002-compose-by-file-contract-not-code, journal/2026-04-17-vision-session-lost-to-crash

> `vision-draft.md` (repo root, uncommitted) sent to `codex exec -s read-only` and `gemini --approval-mode plan` with a direct pushback prompt. Full critiques at `/tmp/cortex-critique-codex.md` (1,244 lines, ~80k tokens) and `/tmp/cortex-critique-gemini.md` (118 lines of content plus hook noise). Both critics converged on the same fatal flaw and diverged productively on two others. Summary here so the thread survives the next crash; the draft itself is not yet updated.

## What both critics found independently (concur)

**The projection-authority gap.** Codex § 1: *"A fresh `.cortex/state.md` does not matter if Sentinel, Claude Code, or a human workflow still acts on stale `CLAUDE.md`. Not a Phase E integration nuisance. A protocol-level authority problem."* Gemini § 1: *"Claude Code, Cursor, and Aider do not natively know to read `.cortex/doctrine/0001-why-cortex-exists.md`. They are hardcoded to look for `.cursorrules`, `CLAUDE.md`, or `.aider.conf`. Cortex does not replace the arbitrary project prompt; it exiles it to a subdirectory... you haven't created a native file protocol. You have created a secondary payload."*

Both are pointing at the same structural gap: the draft's § 7.6 ("Sentinel can derive from a stale `CLAUDE.md`") is not a minor Phase E item. It is *the* question — **who owns the files agents actually load at session start, and how does Cortex reach them?** Without an answer, Cortex is a well-organized directory adjacent to the real prompt surface, not a memory protocol for the prompt surface.

This reshapes the vision. The spec needs a **projection contract**: either (a) `CLAUDE.md` / `AGENTS.md` imports Cortex-derived files with provenance, or (b) `cortex` emits a generated-context file that agents load, or (c) `cortex doctor` fails when the agent-facing root duplicates Doctrine without linking back. Something. The current spec does not address this.

The composition framing also needs rethinking. Codex: *"Policy / execution / reasoning is better than foundation / loop / memory, but not sharp enough. The sharper division is not concern, but authority: who originates facts, who derives views, who consumes projections, and how drift is detected."*

## Where they diverge (productively)

### Codex: the defensible product is the *validator*, not the layer count

Codex § 3: *"The combination argument is plausible but not yet load-bearing. A skeptic saying 'this is Letta with more rules and a regeneration cron' has a point. Cortex can still defend itself, but the defense should shift from novelty to failure prevention. The product is not six layers. The product is: orphan deferrals are caught; stale derived views are visible; generated layers name incomplete sources; Doctrine cannot be silently rewritten; Journal entries survive crashes; promotions preserve lineage."*

Codex § 4: the MVP should be *three invariants*, not three files. Journal append-only + validated; State with provenance + fail-closed on missing sources; at least one consumer path uses the projection. Otherwise the spec collapses into "write better docs."

Codex § 5: the missing risk is **false freshness** — LLM-regenerated State can be "fresh, plausible, and wrong." A `Generated:` header invites trust. The spec needs unknown-sources disclosure, fail-closed on missing git, provenance linking claims back to Journal/commits, and human review before a generated State is authoritative.

Also: Codex read Letta's current docs and said our comparison overclaims. Letta MemFS already has folder hierarchy, `system/` pinned at top, frontmatter, read-only flags, `/doctor`, reflection subagents, and git sync. Claude Code already has `CLAUDE.md` + imports + `.claude/rules/` + path-scoped rules + managed org policy + auto memory. *"'Layered, not flat' is a weak discriminator. Argue that they lack enforceable project-memory invariants."*

### Gemini: the human-first gates are the bug, not the feature

Gemini § 2: *"Documentation rots precisely because it relies on human discipline and manual gating. Claude Code's native memory/ and Letta's self-editing core work because they bypass human laziness... If an engineer has to remember to write a Journal entry after a late-night debugging session, they won't. Cortex's explicit gates are an anti-pattern masquerading as 'accountability.'"*

This directly contradicts the draft's § 2.3 load-bearing claim. The draft argues write gates are the point; Gemini argues they re-introduce the exact failure mode they claim to solve.

Both Gemini and Codex land on the same resolution shape — **gates plus automatic capture**. Sentinel end-of-cycle should draft Journal entries automatically. Touchstone pre-merge should draft Doctrine candidates automatically. The human reviews and promotes, but the draft happens without remembering. That's the spec's intent at § 3 (*"Sentinel → Cortex: end-of-cycle hook writes a Journal entry"*), but it's unwired. The vision has to be honest that the gates are only tolerable if the drafting is automated — otherwise Gemini is right and this is bureaucracy.

### Gemini: Map regeneration is a self-inflicted wound

Gemini § 3: *"A structural map should be ephemeral. Aider builds a tree-sitter map at runtime precisely so it is never stale. Persisting a generated map to the filesystem, only to invent a complex schedule of cron jobs and Generated: headers to detect when it inevitably becomes stale, is not a load-bearing move — it is a self-inflicted wound... For structural data (Map), runtime parsing is strictly superior to persisted generation."*

This is the sharpest individual point across both critiques. It may be right. If Map is computable at runtime from tree-sitter, the argument for persisting it collapses. Counter-argument: runtime repo-maps lose the cross-layer provenance (Map cites Doctrine entries that shape which parts of code are load-bearing — something tree-sitter can't synthesize alone). But the spec has not demonstrated this. **Open question for the spec: should Map be derived-on-read rather than derived-on-schedule?** This would resolve both Gemini's point and Codex's false-freshness risk for Map specifically.

### Gemini: the read-economics question

Gemini § 5: *"You have prioritized human readability at the direct expense of agent context limits and Time To First Token. Cortex forces a brute-force filesystem read of potentially thousands of words of Markdown before the agent can even begin reasoning... A vendor like Cursor would reject Cortex not because it's conceptually wrong, but because it would make their product feel unacceptably slow."*

The draft discusses write costs (§ 7.2) but not read costs. The spec does not address which layers get loaded by default vs. on-demand, nor how large a `.cortex/` can grow before it breaks agent context. MemGPT's virtual-memory framing (cited in `docs/PRIOR_ART.md` § 4) is the obvious answer but not spelled out in the spec.

### Gemini: the MVP CLI contradiction

Gemini § 4: *"If state.md is regenerated, the MVP requires the CLI + LLM API key. So 'the spec is the artifact, CLI is secondary' is a false claim. The CLI is the artifact."*

This is a direct hit on doctrine/0003. Either (a) hand-authorable State is valid and the spec should say so, or (b) State requires the CLI and the "spec-first" doctrine is marketing. The honest answer is probably (a) with a caveat — hand-authored State is fine with a `Generated: hand-authored` marker, and automated regeneration is the improvement when the CLI is present. The draft didn't work this out.

## What to do with this

The critiques point to a set of *structural decisions* the vision hasn't made, not just prose to tighten. Before drafting v2, the user and I should decide on these four:

1. **The projection contract.** Does Cortex own a mechanism by which Cortex-derived content reaches the agent-facing entry point (`CLAUDE.md` / `AGENTS.md` / `.cursor/rules/` / etc.)? If yes, what shape? A generated file Cortex writes? An import convention? A doctor check?

2. **The gates vs. drafting axis.** Is the vision "human writes, agent assists" or "agent drafts, human reviews"? The draft leans the first; Gemini argues the second is the only one that survives contact with real workflows. The spec's § 3 integration hooks suggest the user's actual intent is the second, but the draft obscures it.

3. **Map's derived-on-read vs. derived-on-schedule.** If Map can be computed from tree-sitter at runtime, the argument for persisted Map collapses. If Map needs cross-layer synthesis that runtime parsing can't do, say what that synthesis is and why it's worth the false-freshness risk.

4. **Read economics / context budget.** What does an agent load by default? Is there a manifest? Can Cortex surface only active Plans + fresh State + top-N Doctrine? The spec is silent here.

After those four are decided, the v2 draft writes itself. Without them, v2 would paper over the same structural gap.

## What's not lost

Both critics validated:
- The **incident framing** (§ 0) as grounding — Gemini didn't attack it; Codex didn't either.
- **Cortex as a *spec***, not a product — Codex § 3's framing of "failure prevention" aligns with this.
- The **composition with Touchstone and Sentinel** at the conceptual level — neither critic rejected the three-tool decomposition; both rejected the *language* we used for the seams.
- The **explicit-not list** (§ 5) — reading the critiques, the things we said Cortex is *not* held up.

## Next

- [ ] User decides on the four structural questions above, or asks for a sub-round of research on any of them (esp. #1 — the projection contract is an area where `docs/PRIOR_ART.md` is thin).
- [ ] v2 draft folds the decisions back in. Vision shifts from *novelty-as-combination* to *failure-prevention-as-product*. The MVP section becomes three invariants, not three files. A new section on the projection contract lands between §§ 3 and 4. § 7.6 (Sentinel reads stale CLAUDE.md) is removed as a risk because the projection contract resolves it in the body.
- [ ] v2 goes to Codex and Gemini again for a second round. If both agree the structural gaps are closed, promote to `README.md` rewrite + Doctrine amendments. If not, another round.
