# candidate — Stage T1.9 journal entries on source PRs; keep post-merge as verification only

> Cortex should stage `pr-merged` Journal entries on the source PR before merge, and repurpose the post-merge hook from writer to verifier so T1.9 stays enforceable without creating a second meta-PR for each substantive change.

**Status:** Proposed
**Date:** 2026-05-13
**Promoted-from:** - (direct authoring from issue #207)
**Cites:** [issue #207](https://github.com/autumngarage/cortex/issues/207), [`.cortex/protocol.md` §2 T1.9](../protocol.md), [Doctrine 0006](./0006-scope-boundaries-v3.md), [Doctrine 0007](./0007-canonical-ownership-of-state-and-plans.md), [Doctrine 0008](./0008-context-integrity-build-system.md), [`scripts/open-pr.sh`](../../scripts/open-pr.sh), [`scripts/merge-pr.sh`](../../scripts/merge-pr.sh), [`scripts/cortex-pr-merged-hook.sh`](../../scripts/cortex-pr-merged-hook.sh), [journal/2026-05-12-pr-merged-267](../journal/2026-05-12-pr-merged-267.md), [journal/2026-05-12-pr-merged-268](../journal/2026-05-12-pr-merged-268.md)
**Grounds-in:** [principles/engineering-principles.md#one-code-path](../../principles/engineering-principles.md#one-code-path)
**Load-priority:** default

## Context

### Problem statement

Issue #207 identifies an unhealthy shape: one substantive source PR is often followed by a second PR whose only payload is a `pr-merged` Journal entry. That creates meta churn in trunk history and delays journal authoring until after merge, when author context is colder.

Concrete data point requested from `git log --merges`:

```bash
git log origin/main --merges --since='30 days ago' --format='%s' \
  | awk 'BEGIN{meta=0;total=0} {total++; if ($0 ~ /^docs\(journal\): auto-draft pr-merged entry/) meta++} END{print "total=" total, "meta=" meta, "source=" total-meta}'
```

Result on this branch/worktree snapshot: `total=0 meta=0 source=0` (repo uses squash merges, so merge-commit counts are not informative). To keep the analysis measurable anyway, the equivalent squash-subject proxy shows material meta traffic:

```bash
git log origin/main --since='30 days ago' --format='%s' \
  | awk 'BEGIN{meta=0;total=0} {total++; if ($0 ~ /^docs\(journal\): auto-draft pr-merged entry/) meta++} END{print "total=" total, "meta=" meta, "source=" total-meta}'
```

Current result: `total=226 meta=38 source=188`.

That is lower than the worst-case 2:1 from the issue narrative, but still significant enough to justify design action in a dogfood repo where context-integrity tooling should reduce operator noise, not amplify it.

### Option comparison

#### Option 1 — Status quo (post-merge hook writes the entry)

**How it works now**
- `scripts/merge-pr.sh` merges the source PR and then invokes `scripts/cortex-pr-merged-hook.sh`.
- The hook drafts a `pr-merged` entry, commits it on a feature branch, opens a second PR, and auto-merges it.

**Script contract changes**
- `open-pr.sh`: no change.
- `merge-pr.sh`: no change.
- Post-merge hook: remains primary writer.

**Resulting trunk log shape**
- Source change appears first, then follow-up `docs(journal): auto-draft pr-merged entry ...` commit/PR.

**Failure modes**
- Author context has cooled when entry is generated; entries bias toward “what merged” not “why this was right.”
- Hook is side-effect heavy (branching, commit, push, PR creation), so outages/auth issues create noisy degradations.

**Audit story (T1.9)**
- Strong post-merge presence guarantee when hook succeeds.
- Weak author-time quality guarantee; T1.9 exists, but frequently low-fidelity.

#### Option 2 — Pre-merge stage on source PR (proposed)

**How it works**
- Author tooling stages a `pr-merged` draft before merge (`cortex journal stage --type pr-merged --pr <n>`).
- Entry is committed on the source branch and reviewed in the source PR itself.
- Merge-time hook verifies presence instead of writing a second PR.

**Script contract changes**
- `open-pr.sh`: invokes staging path when T1.9 is expected for this PR.
- `merge-pr.sh`: verifies staged-entry presence before merge (strict backstop).
- Post-merge hook: verifier-only; no branch/commit/push/PR side effects.

**Resulting trunk log shape**
- One merge/squash unit per change: feature/fix plus journal entry together.

**Failure modes**
- If stage invocation is skipped and strict verification is absent, T1.9 can be missed.
- Slightly higher pre-merge author friction.

**Audit story (T1.9)**
- Best fit with deterministic provenance: same reviewed diff contains code + journal rationale.
- Verification still happens (pre-merge and post-merge), but write path is moved to author time.

#### Option 3 — Hybrid (stage when available, otherwise post-merge writer fallback)

**How it works**
- Try staged entry first; if absent, old post-merge hook writes a fallback auto-draft PR.

**Script contract changes**
- `open-pr.sh`: optional stage call.
- `merge-pr.sh`: may warn but can allow fallback path.
- Post-merge hook: keeps writer capability + verifier logic.

**Resulting trunk log shape**
- Mixed: some source PRs include entry; others still produce second meta PR.

**Failure modes**
- Two writer paths drift (template/version behavior diverges).
- Teams stop noticing staging failures because fallback masks them.

**Audit story (T1.9)**
- Entry-presence reliability remains high, but provenance becomes non-deterministic (“which writer path authored this?”).
- Conflicts with one-code-path discipline.

## Decision

Adopt **Option 2 (pre-merge stage on source PR)** as Cortex first-party default.

Why this is the right default against Cortex principles:
- **Deterministic provenance:** the journal artifact is reviewed in the same PR as the code it describes.
- **Author-time context capture:** the entry is editable while the author still has implementation context in working memory.
- **Append-only Journal invariants:** still preserved (new file added, never in-place mutation).
- **Dogfood efficiency:** removes recurring second-PR operational overhead from Cortex’s own workflow.

Option 3 is explicitly rejected as default because it institutionalizes two writer paths for one trigger, violating the engineering principle to keep one code path for business logic.

## Implementation outline (follow-up PR, no implementation in this doctrine PR)

1. **Add CLI stage surface:** `cortex journal stage --type pr-merged --pr <n>` writes draft entry and supports optional `--git-add`/`--amend` convenience.
2. **Wire `open-pr.sh` call site:** before opening PR (or immediately after PR number acquisition), run `cortex check-triggers` and stage when T1.9 applies.
3. **Add merge backstop in `merge-pr.sh`:** fail merge when T1.9-qualified PR lacks staged `Type: pr-merged` entry tied to that PR.
4. **Flip `cortex-pr-merged-hook.sh` to verifier mode:** verify entry presence after merge; stop drafting, committing, pushing, and opening follow-up PRs.
5. **Extend audit/tests:** add fixtures for staged-present, staged-missing, and verifier diagnostics so T1.9 remains machine-auditable.

## Risks and what would invalidate this

- If measured T1.9 miss rate increases after staging (authors skip staging despite script automation), revisit fallback design.
- If review latency rises materially because source PRs become noisier/less readable with staged entries, revisit templating or split strategy.
- If verifier-only post-merge checks prove too weak for teams that bypass `open-pr.sh`/`merge-pr.sh`, revisit enforcement boundary.

Invalidation signal: sustained evidence (e.g., two consecutive weeks) that staged mode produces lower T1.9 completion or lower entry quality than post-merge writer mode.

## Consequences

- **What becomes easier:** one coherent PR per change, better journal quality, lower meta churn.
- **What becomes harder:** `open-pr.sh`/`merge-pr.sh` become stricter workflow gates.
- **What this forecloses:** long-term reliance on post-merge writer as primary authoring path for T1.9.

## Citations

- Issue: [#207](https://github.com/autumngarage/cortex/issues/207)
- Protocol trigger: [`.cortex/protocol.md` §2 T1.9](../protocol.md)
- Doctrine lineage: [0006](./0006-scope-boundaries-v3.md), [0007](./0007-canonical-ownership-of-state-and-plans.md), [0008](./0008-context-integrity-build-system.md)
- Current writer/entry point scripts: [`scripts/cortex-pr-merged-hook.sh`](../../scripts/cortex-pr-merged-hook.sh), [`scripts/open-pr.sh`](../../scripts/open-pr.sh), [`scripts/merge-pr.sh`](../../scripts/merge-pr.sh)
- Journal evidence samples: [2026-05-12-pr-merged-267](../journal/2026-05-12-pr-merged-267.md), [2026-05-12-pr-merged-268](../journal/2026-05-12-pr-merged-268.md)
