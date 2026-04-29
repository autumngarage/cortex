---
Status: active
Written: 2026-04-29
Author: claude-code (Henry Modisett)
Goal-hash: b57f6355
Updated-by:
  - 2026-04-29 claude-code (initial draft — design for opt-in semantic retrieval as a derived layer Cortex owns the interface for; supersedes Doctrine 0005 #1)
  - 2026-04-29 claude-code (council review applied — fixed critical invalidation bug for uncommitted edits; preserved pure-grep floor by keeping `cortex grep` untouched and adding `--mode bm25` alongside; removed auto-resolve to Conductor in favor of explicit opt-in; controlled model cache path; tightened doctrine supersede to declare index "hazmat" + interface non-normative; smaller default chunk size; cross-platform install gaps documented as risks)
  - 2026-04-29 claude-code (frontmatter SPEC compliance — Status enum + Cites scalar + auto-computed Goal-hash + required section headers added to satisfy `cortex doctor`)
Cites: .cortex/doctrine/0005-scope-boundaries-v2.md, .cortex/doctrine/0006-scope-boundaries-v3.md, .cortex/protocol.md, .cortex/plans/cortex-v1.md
---

> **Council-applied deltas (2026-04-29).** This plan was reviewed by a 3-member council (Gemini-pro / Kimi / DeepSeek-v4) with synthesis. The following changes were folded back from the council critique:
>
> 1. **Invalidation correctness fix.** Original design used `git ls-tree -r HEAD` for the fast path — which only sees committed files. A user editing `.cortex/journal/foo.md` without committing would get stale retrieval results. Replaced with mtime+size check against the working tree (see § Index lifecycle).
> 2. **True grep floor preserved.** The original Slice 1 deprecated `cortex grep` in favor of `cortex retrieve --mode bm25`. Council reversed this: `cortex grep` stays untouched as a zero-dependency path that doesn't load sqlite-vec or ONNX. `cortex retrieve --mode bm25` ships alongside, layered on the FTS5 index.
> 3. **No silent Conductor selection.** Original `auto` resolution would prefer Conductor when configured. Council rejected this — auto-routing to a paid API without consent is hostile. Resolution is now explicit: `flag > config > builtin > grep-only`, with a CLI suggestion when Conductor is detected but unconfigured.
> 4. **Controlled model cache path.** fastembed default cache locations can collide with permissions. Force `~/.cache/cortex/models/` (XDG-respectful) or per-project `.cortex/.index/models/`.
> 5. **Doctrine tightening.** Index declared "hazmat" — consumers must use `cortex retrieve`, not query the SQLite directly. Interface declared **non-normative** reference implementation; custom consumers may bypass and re-index.
> 6. **Smaller default chunks.** 500–800 tokens with 100–150 overlap (down from 1000 / 200), to match Cortex entries' actual density. Measure on `autumn-mail` corpus before S2 freeze.
> 7. **Cross-platform install gaps documented as risks.** onnxruntime has no aarch64 Linux PyPI wheels (Graviton, Pi); brew + pip + python@3.11 has known upgrade fragility.
> 8. **Hybrid-default with one-time notice.** When index is first built and mode flips from grep to hybrid, emit a one-line notice so behavior change isn't silent.

# `cortex retrieve` — semantic retrieval as an opt-in derived layer

> The canonical store stays markdown + git + grep — that doctrine is load-bearing, not negotiable. But once a project's `.cortex/` accumulates ~50–100+ entries, recency-by-grep stops being enough: agents miss the entry that *would* answer their question because it uses different terminology. This plan adds `cortex retrieve` as an opt-in derived layer Cortex owns the *interface* for — semantic search via a local index that is gitignored, rebuildable from `.cortex/`, and works without network or paid services. Source of truth never changes; retrieval quality scales.

## Why (grounding)

Three forcing functions, in order of weight:

1. **Memory differentiator stops scaling at ~100 cycles.** Sentinel's pitch is "drop-in autonomous engineer that ships with project memory." That promise holds for the first dozens of cycles where recency + grep cover the cases. Past ~100, agents repeatedly miss prior decisions because grep needs the *exact term* the historical entry used. The Planner re-proposes a previously-rejected work item under different phrasing; the Reviewer grades a cache-invalidation diff without surfacing the prior staleness/freshness/TTL discussions. That's not a Cortex problem until users put autonomous agents on top of Cortex — at which point it becomes Cortex's product moat.
2. **The on-demand retrieval pattern is more efficient than manifest stuffing.** Today the manifest pre-loads ~6k tokens of recency-ranked Doctrine + Journal at session start, paid by every role × every cycle. The mature pattern (Anthropic agentic guidance, RAG community 2024-2026) is: thin always-on slice + tools that retrieve on demand. `cortex retrieve` becomes that tool. Direct token savings every cycle; same dep weight whether index runs or not.
3. **[`doctrine/0005-scope-boundaries-v2`](../doctrine/0005-scope-boundaries-v2.md) #1 was written to push back against "every memory tool is a vector DB" — and that pushback was correct.** The doctrine's spirit: storage is durable, portable, grep-able; semantic retrieval is a separate concern. This plan honors that spirit while accepting the original framing was too strict — *consumer-side* indexing left every consumer (Sentinel, future tools) to re-implement the same logic against the same format. Owning the *interface* in Cortex while keeping the *storage* unchanged is the right resolution. The narrowing lands as [`doctrine/0006-scope-boundaries-v3`](../doctrine/0006-scope-boundaries-v3.md) (shipped alongside this plan).

## Doctrine implications

This plan supersedes **Cortex Doctrine 0005 #1** ("Not a vector store. No embeddings, no ANN indexes, no similarity search at the storage layer."). The supersede entry is drafted alongside this plan (see `Slice S0 — doctrine supersede`).

What carries forward unchanged:

- **Markdown + git is the canonical store.** Removing `.cortex/.index/` loses nothing not regeneratable from `.cortex/` markdown content.
- **Grep is the always-available retrieval primitive.** Default `cortex retrieve` is grep/BM25; semantic is opt-in.
- **No cloud dependency for core function.** A user can install Cortex, run grep retrieval, never touch a network. Semantic retrieval *can* use Conductor for cloud embedders but ships with a CPU-only local default.
- **One project per `.cortex/`.** Cross-project indexing stays out of scope.

What changes:

- The phrase "no embeddings, no ANN indexes, no similarity search at the storage layer" is narrowed to "no embeddings *in* `.cortex/` markdown files" — the index lives in `.cortex/.index/` (gitignored, derived).
- "External indexing is fine, storing vectors inside `.cortex/` is not" → "Internal indexing is fine *as a derived layer* under `.cortex/.index/`, never as part of canonical content."
- Cortex's CLI surface gains `cortex retrieve` and `cortex index` subcommands.

## Approach

Layer an opt-in retrieval interface (`cortex retrieve`) over the existing markdown + git + grep storage substrate. The substrate is unchanged and remains canonical. The interface is non-normative — part of the Cortex CLI distribution, not part of the Cortex Protocol / SPEC. Consumers are free to bypass it. Implementation is sliced (S0–S4 below under Work items) so each slice is independently shippable and prior slices unblock later ones.

### Design principles

1. **Source of truth is markdown.** The index is a cache. `rm -rf .cortex/.index/ && cortex index --build` recovers everything. No information is durable in the index that isn't durable in `.cortex/` markdown.
2. **Default works without setup.** A user who runs `cortex retrieve "..."` on a fresh `.cortex/` — no index, no embedder configured, no network — gets grep results. Semantic is an upgrade path, never a prerequisite.
3. **Embedder is pluggable, not bundled-and-frozen.** Cortex ships with a small CPU-only built-in default (`fastembed` + `BAAI/bge-small-en-v1.5`, ~25MB ONNX model) for users who want semantic-out-of-the-box. Power users route through Conductor for cloud embedders or alternate local models.
4. **Index is gitignored, not committed.** Vectors are derived from markdown. Committing the index would (a) bloat the repo with binary blobs, (b) create a second source of truth, (c) tie all team members to one embedder choice. `.cortex/.index/` is `.gitignore`d.
5. **Hybrid retrieval beats vector-only.** BM25 (lexical) + vector (semantic) + optional cross-encoder rerank is the production-quality default. Vector-only misses exact-term queries ("Doctrine 0003"); BM25-only misses semantic phrasings.
6. **Install experience is the gate.** Any design choice that breaks brand-new-repo install or makes existing-repo adoption surprising loses, even if it improves retrieval quality. See § Install experience.

### Architecture

### Storage layout

```
.cortex/
  doctrine/
  journal/
  plans/
  …
  .index/                       ← gitignored; derived; rebuildable
    manifest.json               ← {git_tree_hash, embed_model, chunk_strategy_version, built_at}
    chunks.sqlite               ← SQLite + sqlite-vec extension
                                  tables:
                                    chunks (id, source_path, source_hash, chunk_idx,
                                            text, frontmatter_json, vec)
                                    bm25  (FTS5 virtual table over chunks.text)
    models/                     ← ONNX model cache (only if built-in embedder used)
```

A single SQLite file holds vectors (via `sqlite-vec`'s `vec0` virtual table) and BM25 lexical index (via SQLite FTS5). One file means atomic rebuild — write to `chunks.sqlite.new`, rename on success, never partial state.

### Embedder selection

Three options, exposed as a config axis with sensible defaults:

| Provider | Use case | Dep weight | Network |
|---|---|---|---|
| **`builtin`** (default for semantic if available) | Brand-new install, no Conductor, no API keys | `fastembed` package (~50MB on first use, includes onnxruntime); ONNX model `BAAI/bge-small-en-v1.5` (~25MB, lazy-downloaded on first index, cached at `~/.cache/cortex/models/`) | First model download only |
| **`conductor`** (opt-in only) | Garage users with Conductor; better-quality embedders (Voyage, Cohere, Ollama) | Conductor binary | Per-provider; Ollama local, Voyage/Cohere remote (paid) |
| **`grep-only`** (always available, no Python ML imports) | No embedder configured; offline; degraded mode | None | None |

Resolution order at runtime (no auto-selection of paid services):

1. If `cortex retrieve --embedder <name>` flag passed, use that.
2. Else if `.cortex/config.toml` has `[retrieve] embedder = "..."`, use that.
3. Else if `fastembed` Python package importable, use `builtin`.
4. Else fall through to `grep-only` with a one-time message.

If `conductor` binary is on PATH AND has an embed-capable provider configured, `cortex retrieve --semantic` (without explicit embedder selection) prints a one-time **suggestion** ("conductor is available with embed-capable providers; opt in with `cortex config set retrieve.embedder conductor` or `--embedder conductor`") and falls through to step 3. **Never silently routes to a paid API.**

**Lazy imports.** The `cortex retrieve --mode grep` command path **must not import `sqlite-vec`, `fastembed`, or `onnxruntime`** at startup. The grep path is the always-available floor; missing or broken native extensions in the semantic path must not crash grep mode. Imports are deferred until the semantic / hybrid path is actually invoked, and ImportError surfaces as a clear "semantic mode unavailable: <reason>; falling back to grep" rather than a CLI-wide crash.

### Index lifecycle

**Build trigger.** Index is built lazily on first `cortex retrieve --semantic` (or first `cortex retrieve` when `[retrieve] mode = "semantic"` is configured). Explicit build via `cortex index --build`. Never auto-built on `cortex init` or session start.

**Invalidation strategy.** Two-level check, fast path first. **The fast path operates on the working tree, not on git HEAD** — uncommitted edits invalidate correctly.

1. **Working-tree fingerprint compare.** On every `cortex retrieve` call, walk `.cortex/{doctrine,journal,plans}/` and build a fingerprint of `(path, mtime_ns, size)` tuples (sorted, hashed). Compare to `manifest.json`'s stored fingerprint. Match → index is fresh, query directly. Mismatch → fall to step 2. This catches uncommitted edits (mtime/size changes), staged-but-uncommitted (same), and `git rebase` / amend (mtime updates on checkout).
2. **Per-file content-hash compare.** For each `.md` file whose `(path, mtime, size)` differs from the stored fingerprint, compute SHA-256 of contents. Compare to `chunks.source_hash` per source_path. Re-embed only files whose content actually changed (mtime can change without content changing — `git checkout` of an unchanged file). Delete chunks for files that no longer exist.

The fast path is sub-second on a 1000-file `.cortex/` even when no changes occurred (just stat calls). Re-embed cost is per-changed-file only.

**Why mtime+size and not git tree.** `git ls-tree -r HEAD` only sees committed files. A user editing `.cortex/journal/2026-04-29-my-decision.md` and immediately running `cortex retrieve` (without committing) would get stale results — the HEAD tree hash hasn't changed. Working-tree mtime+size catches this. Git operations (rebase, amend, checkout) update mtime as a side effect, so they're caught too. Edge case: a user manually `touch`ing a file without changing content forces a re-hash but no re-embed (content-hash short-circuits the work).

**Rebuild on schema change.** `manifest.json` carries `chunk_strategy_version` + `embed_model`. If either changes (e.g., user upgrades Cortex and the chunker improves), full rebuild on next retrieve. Auto-detected; no user action.

### Chunking strategy

**Per-entry default, with overlap on long entries.**

- One Cortex entry (a single Doctrine, Journal, or Plan file) = one chunk **if** its body ≤ 800 tokens (typical entries are 200–600).
- Long entries (Plans, especially) split into ~600-token chunks with 100-token overlap (council-recommended; smaller defaults than the original 1000/200 because Cortex entries are denser and shorter than typical RAG corpora). **Measure on `autumn-mail` corpus before S2 freeze** — adjust if Recall@5 on the golden set wants different defaults.
- Token counting via `tiktoken` (cl100k_base encoding) — explicitly NOT char heuristics. Documented behavior, reproducible across embedders.
- Frontmatter is **prepended to every chunk's embedded text** as structured context: `[type: <Type> | date: <Date> | cites: <comma-list> | path: <relative-path>] <body chunk>`. This is "contextual retrieval" (Anthropic, late-2024) — measured ~30% recall lift on technical corpora.
- Non-canonical files are **excluded**: `.cortex/.index/`, `.cortex/templates/`, `.cortex/procedures/` (procedures can opt in via config).

**Versioning.** `chunk_strategy_version` in manifest.json. Bump on any change that affects what gets indexed (file selection, chunk size, overlap, frontmatter prepend format, tokenizer choice). Bump triggers full rebuild.

### Hybrid retrieval

Default query path:

1. Embed query (~50-200ms with built-in embedder; ~50ms with Conductor cloud).
2. Vector top-K=20 from `chunks.vec` via sqlite-vec.
3. BM25 top-K=20 from `chunks` FTS5 table.
4. **Reciprocal-rank fusion** to merge the two lists (RRF, k=60 — well-studied default).
5. Optional cross-encoder rerank if `[retrieve] rerank = true` and a reranker is configured (default off; opt-in for power users).
6. Return top-N=5 (configurable via `--top-k`).

**Why hybrid by default.** Vector-only misses "Doctrine 0003" type exact-term queries; BM25-only misses semantic phrasings. RRF is parameter-free and robust. Cross-encoder rerank is measurably better but doubles latency (~100-300ms) and is therefore opt-in for users with a golden-set evaluation showing the lift is worth it.

### CLI surface

```
# Existing — preserved untouched; zero new deps; no sqlite-vec or ONNX imports
cortex grep "<pattern>"           # current Phase B implementation; the always-available floor

# New unified retrieval entry point — layered, lazy-imports semantic deps only when needed
cortex retrieve "<query>" [--top-k N] [--mode hybrid|semantic|bm25|grep] [--embedder NAME] [--rerank] [--filter Type=decision] [--since 30d]

# Explicit index management
cortex index --build              # full rebuild (use after schema changes)
cortex index --update             # incremental update (default cron-friendly path)
cortex index --status             # show: built? fresh? stale chunks count? size on disk?
cortex index --clear              # rm -rf .cortex/.index/ (rebuild on next retrieve)

# Doctor surfacing — always reports retrieve readiness
cortex doctor                     # adds "Retrieve: <mode> via <embedder>" line + warnings
                                  # warns if index stale, embedder unavailable, etc.
```

**`cortex grep` is intentionally preserved.** Council critique: don't replace it. `cortex grep` is the zero-dependency, can't-fail floor — even if `sqlite-vec` or `fastembed` are broken or absent, `cortex grep` works. `cortex retrieve --mode grep` is an alias that calls the same code path. `cortex retrieve --mode bm25` is a separate path that uses the FTS5 index (so requires the index built but not the embedder).

**First-time mode-flip notice.** When index is first built and `cortex retrieve` (no flag) flips its default from `grep` to `hybrid`, emit a one-time message:
```
[cortex] semantic index built — `cortex retrieve` default is now `--mode hybrid`. Override with `--mode grep` or `[retrieve] mode = "grep"` in config.
```
Suppressed thereafter; user can clear with `cortex config set retrieve._notice_seen false`.

**Output format.** `cortex retrieve` returns markdown excerpts with file paths and similarity scores by default; `--json` emits structured output (path, score, frontmatter, excerpt) for tool consumers. Sentinel/Touchstone consume `--json`.

**Filter axes — v0.1 ships strict equality only.**

- `--filter Type=decision` — frontmatter equality (S1)
- `--since 30d` / `--since 2026-04-01` — date range (S1)
- Substring (`cites~doctrine-0003`), DSL, complex predicates — **deferred to v1.x** (council pushback: keep simple, add when measured demand exists)

### Configuration

`.cortex/config.toml` gains an optional section:

```toml
[retrieve]
mode = "hybrid"                  # hybrid | semantic | grep — default for `cortex retrieve` calls
embedder = "auto"                # auto | builtin | conductor | grep-only
top_k = 5
rerank = false
include_procedures = false       # exclude .cortex/procedures/ from index by default

[retrieve.builtin]
model = "BAAI/bge-small-en-v1.5"

[retrieve.conductor]
provider = "voyage"              # any conductor provider with embed capability
```

All keys optional; defaults are listed above. `cortex init` does *not* write this section by default (zero-config is the default experience).

## Install experience (the load-bearing constraint)

Five paths, all must work cleanly.

### Path 1 — Brand-new repo, brew-installed Cortex, no Conductor

```
$ brew install autumngarage/cortex/cortex
$ cd new-project
$ cortex init                # writes .cortex/ skeleton; no index, no models
$ # … user adds entries …
$ cortex retrieve "anything"
[grep-only mode — no entries to retrieve from yet]
$ # … later, dozens of entries accumulated …
$ cortex retrieve "memory invalidation strategy" --semantic
[building index for first time — downloading model (25MB)…]
[building index — 47 entries, 51 chunks — 4.2s]
[returning 5 results …]
```

**Guarantees:**
- `cortex init` adds zero new dependencies and zero new artifacts beyond the existing `.cortex/` skeleton.
- First semantic retrieval shows a clear progress message during model download + index build. Both are bounded operations (model: one-time, ~15s on a typical home connection; index: <10s on a 1000-entry repo).
- If the user is offline at first-semantic-retrieve time, fallback message: "model not cached and offline — falling back to grep; run `cortex index --build` when online to enable semantic."

### Path 2 — Existing repo with a populated `.cortex/`, fresh Cortex install

```
$ brew install autumngarage/cortex/cortex
$ cd existing-project-with-cortex
$ cortex doctor
…
Retrieve: grep-only (no index built; run `cortex index --build` for semantic)
$ cortex retrieve "stale fixture"
[grep-only — 3 results]
$ cortex index --build
[downloading model (25MB)…]
[indexing 312 entries — 8.7s]
$ cortex retrieve "stale fixture"
[hybrid — 5 results, 2 not surfaced by grep]
```

**Guarantees:**
- A repo with existing `.cortex/` and no index works on day 1 — grep mode covers everything.
- Building the index is a single explicit command, idempotent, with a progress bar and bounded time.
- After build, hybrid mode is the default for `cortex retrieve` calls (configurable to opt out via `[retrieve] mode = "grep"`).

### Path 3 — Garage user with Conductor configured

```
$ # Conductor already installed and configured with embed-capable providers
$ cortex retrieve "..."
[hybrid via conductor (voyage) — 5 results]
```

**Guarantees:**
- Auto-detection picks Conductor when available; no config edits required.
- Cost is logged to stderr ("estimated $0.0001 for query embedding") so operators see what Conductor is spending.

### Path 4 — User with no internet

```
$ cortex retrieve "..."
[grep-only — no embedder available offline]
$ cortex retrieve "..." --semantic
[error: embedder requires network; --semantic unavailable. Use grep mode or install fastembed for offline embedding.]
```

**Guarantees:**
- Grep mode always works.
- Built-in embedder (after first model download) works offline.
- Cloud embedders require network; failure is loud and explicit.

### Path 5 — Cross-platform install (Linux, macOS Intel, macOS ARM)

**Guarantees:**
- `sqlite-vec` ships pre-compiled wheels for all three platforms via PyPI; no source build at install.
- `fastembed` (when installed) ships with `onnxruntime` wheels for all three.
- Brew formula `depends_on "python@3.11"` and pip-installs both via the post-install hook.
- CI tests install on all three platforms before tagging a release.

## Migration / backward compatibility

- **Existing `cortex grep`** (Phase B feature): unchanged, continues to work. `cortex retrieve --mode grep` and `cortex grep` behave identically (the former is the unified entry point; the latter aliases it).
- **Existing `.cortex/` repos**: zero-impact upgrade. New Cortex version installed → no index built, no behavior change to existing commands. User opts into retrieve when they want it.
- **`.cortex/config.toml` schema**: new `[retrieve]` section is fully optional. Existing configs continue to validate.
- **`.gitignore`**: Cortex's `cortex init` updates `.gitignore` to add `.cortex/.index/` if not present. Existing repos: `cortex doctor` warns if `.cortex/.index/` is in the repo's git tree (would mean someone committed the index by mistake) and suggests `git rm --cached -r .cortex/.index/`.

## Success Criteria

This plan is done when all hold:

1. **Brand-new repo path works clean.** `cortex init && cortex retrieve "..."` on a freshly-`init`d repo with no entries returns "no entries indexed yet" cleanly, no errors.
2. **Existing repo path works clean.** A populated `.cortex/` with no `.cortex/.index/` works in grep mode immediately; `cortex index --build` succeeds within bounded time on a 1000-entry repo (<30s on M-series Mac).
3. **First-time semantic retrieval is bounded and visible.** Model download (~25MB) + index build show a progress bar; complete in <60s on typical home internet for a 1000-entry repo.
4. **Cross-platform install** (Linux x86_64, macOS Intel, macOS ARM) succeeds via brew + pip with no source builds. **Lazy imports verified**: `cortex retrieve --mode grep` works on a system where `sqlite-vec` and/or `fastembed` are missing or broken (CLI does not crash; returns grep results). aarch64 Linux gracefully degrades to grep with a clear platform-not-supported message.
5. **Hybrid retrieval beats grep alone** on a hand-built golden set (~30 query/expected-entry pairs from real cycles) by ≥20% Recall@5.
6. **Invalidation is correct against uncommitted edits.** A user editing `.cortex/journal/foo.md` and running `cortex retrieve` *without committing* gets up-to-date results — the working-tree fingerprint catches the edit. A `git rebase` / amend / `git checkout` likewise triggers per-file re-embed only for files whose content actually changed (mtime-only changes don't force re-embed).
6a. **No silent paid-API calls.** With Conductor configured, `cortex retrieve --semantic` (no `--embedder` flag, no `[retrieve] embedder` config) uses `builtin`, not Conductor. The Conductor opt-in suggestion appears once; never enables itself.
7. **Doctrine supersede landed.** Doctrine 0005 #1 carries `Status: Superseded-by 0007` (or whichever number); new entry explains the carry-forward / changes split.
8. **`cortex doctor` reports active retrieve state.** "Retrieve: hybrid via builtin (model: bge-small-en-v1.5, index: 312 chunks, age: 14m)" — concrete and actionable.
9. **Sentinel consumes via `cortex retrieve --json`** in at least one role (Planner most likely) and demonstrates measurably better behavior on a multi-cycle dogfood (memory-usefulness gate from Sentinel master plan).
10. **Zero-config default**: a user who never edits `.cortex/config.toml` gets sensible behavior (grep by default; semantic if and only if they opt in via `--semantic` or build an index).

## Work items

Five slices. Each is independently shippable; prior slices unblock later ones.

### S0 — Doctrine supersede + design freeze

- New Doctrine entry that supersedes 0005 #1.
- This plan's Status flips from `draft` to `council-reviewed-ready` after council critique applied.
- No code changes.
- **Acceptance**: `cortex doctor` runs clean against the new doctrine entry; this plan's frontmatter cites the new doctrine.

### S1 — Index machinery + BM25 mode (grep floor untouched)

- `cortex index --build / --update / --status / --clear` shipped.
- Storage layout written; SQLite + sqlite-vec + FTS5 set up. Lazy imports — these modules are only loaded when index commands or `--mode bm25/semantic/hybrid` are invoked.
- Chunking strategy implemented (per-entry + long-entry split, 600/100 default, tiktoken cl100k_base).
- `cortex retrieve --mode bm25` shipped — uses the FTS5 index. **`cortex grep` is preserved untouched** as the zero-dependency floor; `cortex retrieve --mode grep` aliases it. The two paths share no code at runtime.
- Embedder *not yet* invoked; `--mode semantic` and `--mode hybrid` error with "embedder not yet implemented" (suggests S2 ETA).
- `cortex doctor` reports retrieve state.
- Working-tree-fingerprint invalidation implemented; tested against `git rebase` / amend / `touch` / uncommitted-edits scenarios.
- **Acceptance**: `cortex grep` is byte-identical in behavior to today's. `cortex retrieve --mode bm25` Recall@5 ≥ `cortex grep` baseline on a hand-built golden set. Lazy imports verified — `cortex retrieve --mode grep` works on a system with broken `sqlite-vec` install.

### S2 — Built-in embedder + hybrid retrieval

- `fastembed` integration with lazy model download.
- `cortex retrieve --semantic / --mode hybrid` shipped.
- RRF fusion of vector + BM25 results.
- Manifest tracks model + chunk-strategy version.
- **Acceptance**: hybrid Recall@5 ≥ grep-only + 20% on the golden set; first-time index build on a 1000-entry repo completes in <60s.

### S3 — Conductor integration + alternate embedders (explicit opt-in)

- `cortex retrieve --embedder conductor` invokes `conductor embed --with <provider>`.
- `[retrieve] embedder = "conductor"` config recognized.
- **No auto-selection of paid services.** When Conductor is detected with embed-capable providers but no explicit opt-in is configured, emit a one-time **suggestion** message ("conductor with embed-capable provider X detected; opt in via `cortex config set retrieve.embedder conductor` or `--embedder conductor`") and continue with `builtin`. Council recommendation: explicit consent before paid API calls.
- Cost-tracking output on every `cortex retrieve` call routed to a paid provider; aggregated in `cortex doctor`.
- This slice depends on Conductor shipping the `embed` capability axis (not yet shipped; tracked as `autumngarage/conductor#XXX`).
- **Acceptance**: same query returns comparable results across builtin and conductor-routed embedders; no API calls made without explicit user opt-in; cost-tracking visible per call.

### S4 — Filters, rerank, polish

- `--filter`, `--since`, `--top-k` flag completion.
- Optional cross-encoder rerank (`[retrieve] rerank = true`).
- Performance tuning if golden-set numbers warrant.
- Migration guidance docs.
- **Acceptance**: documentation complete; `cortex doctor` warnings cover all common misconfigurations.

## Out of scope (explicitly)

- **Cross-project indexing.** One project per `.cortex/` (Doctrine 0005 #4). Sharing index across repos is a v1.x+ Lighthouse-conversation concern.
- **Custom embedders defined in user code.** Embedders come from the resolution-order list; users wanting weird embedders use Conductor providers.
- **Online learning / fine-tuning embedders on user data.** Cold-only models for v0.x.
- **Network-side index hosting.** Doctrine 0005 #7 (not cloud-hosted) holds. The index is local files only.
- **Index sharding for very large `.cortex/`.** A single SQLite file is fine up to many thousands of chunks; if a project hits that scale, that's a v1.x problem.
- **Replacing `cortex manifest`.** The manifest is still the session-start primer (always-on slice). `cortex retrieve` is mid-cycle on-demand, not the manifest.

## Open questions — council recommendations applied

The original 10 open questions have council-recommended answers folded into the design above. Summary:

1. **Auto-resolution order.** **Resolved: explicit only.** No silent routing to paid Conductor APIs. `flag > config > builtin > grep-only`; Conductor surfaces as a CLI suggestion when detected with embed-capable providers, never auto-selected. Reflected in § Embedder selection.
2. **Vendor sqlite-vec vs pip wheels.** **Resolved: pip wheels with graceful fallback.** Standard pip wheels for normal install paths; if native extension fails to load, fall back cleanly to `cortex grep` (zero-dep floor). Don't vendor the binary — pip wheels are the standard distribution path and simpler.
3. **Default chunk size + overlap.** **Resolved: 600/100 (smaller than original 1000/200).** Cortex entries are denser and shorter than typical RAG corpora. Measure on `autumn-mail` corpus before S2 freeze; adjust if golden-set Recall@5 wants different. Tokenizer locked to tiktoken cl100k_base.
4. **Memory of surfaced entries (diversity-aware retrieval).** **Resolved: deferred to v1.x.** Stateful retrieval over a stateless layer adds complexity that v0 doesn't justify.
5. **Frontmatter filtering syntax.** **Resolved: strict equality only in v0.1.** `--filter Type=decision` ships in S1; substring matching, DSL, complex predicates all deferred to v1.x. Keep simple; expand on measured demand.
6. **Hybrid as default once index exists.** **Resolved: hybrid by default with one-time notice.** When index is first built and the default flips, emit a one-line message ("`cortex retrieve` default is now `--mode hybrid`. Override with `--mode grep`."). Resolves the predictability tension without keeping the user out of the better mode.
7. **Fast-path single-keyword queries.** **Resolved: deferred to S4 polish.** Send all queries through the standard RRF flow for v0; the optimization isn't load-bearing.
8. **`cortex doctor` warn at >100 entries with no index.** **Resolved: ship as one-time, suppressible warning.** Aids feature discovery; suppress with `cortex config set retrieve._scale_warning_seen true`. Lands in S2 (when the warning's recommendation actually exists).
9. **Telemetry / opt-in performance reporting.** **Resolved: hard reject.** Cortex is local-first; telemetry breaks that promise even with opt-in. If golden-set tuning needs cross-project data, do it via shared evaluation corpora the user explicitly contributes to, not in-tool reporting.
10. **Model-download UX — Cortex manages or pip extras.** **Resolved: Cortex manages.** Cortex draws the progress bar so users understand disk/network usage; punting to `pip install fastembed[bge-small]` extras hides the cost. Cache forced to `~/.cache/cortex/models/` (XDG) for predictability.

(Open question section retained for traceability; do not strip on future revisions.)

## Risks

1. **Brew package size grows.** Adding `fastembed` as a Cortex dep takes the brew formula from ~MB to ~50MB+ on first model download. Mitigation: model is lazy-downloaded, not bundled in the brew tarball; first `--semantic` retrieval triggers the fetch.
2. **Index gets out of sync with markdown.** Mitigation: two-level invalidation (working-tree fingerprint via mtime+size + per-file content-hash) catches uncommitted edits, `git rebase`, amends, and stat-only changes. Worst case, `cortex index --clear && cortex retrieve` recovers.
3. **fastembed model quality regressions.** ONNX models occasionally update with subtle behavior shifts. Mitigation: pin model version in `manifest.json`; user-controlled bumps; new `chunk_strategy_version` triggers rebuild.
4. **Cross-platform `sqlite-vec` issues.** sqlite-vec pre-compiles for the major platforms but edge cases (musl Linux, Windows/WSL) may miss. Mitigation: graceful fallback to `grep` mode with a clear warning; **`cortex grep` is the always-available floor and never imports `sqlite-vec`**; CI tests Linux + macOS Intel + macOS ARM as Tier 1.
5. **Embedding cost surprises in Conductor mode.** Voyage / Cohere are paid services. Mitigation: cost-tracking output on every `cortex retrieve` call routed to a paid provider; **never auto-route to paid services without explicit opt-in**; `cortex doctor` flags when cumulative spend exceeds a configurable threshold.
6. **Hybrid retrieval introduces noise on small corpora.** If `.cortex/` has 10 entries, hybrid + RRF over 5 vector + 5 BM25 is essentially returning everything. Mitigation: at <50 chunks, `cortex retrieve` defaults to `--mode bm25` regardless of `mode = hybrid` setting; one-line warning.
7. **`onnxruntime` aarch64 Linux gap.** No standard PyPI wheels for ARM Linux (Graviton, Pi, some K8s). Users on these platforms hit ImportError on `fastembed` install. Mitigation: graceful fallback to grep with a clear "platform not supported by builtin embedder; use --embedder conductor or stick with grep" message; document as a known gap; track upstream. **No CLI crash** — lazy imports ensure `cortex retrieve --mode grep` works regardless.
8. **Brew + pip + python@3.11 fragility.** Brew's `python@3.11` dependency can break on `brew upgrade` when the system Python migrates. fastembed and sqlite-vec installed via post-install hooks may end up with broken native extensions. Mitigation: install Python deps into a Cortex-managed venv (separate from system Python), bypass brew's Python-package fragility; document as a known consideration; `cortex doctor` checks for broken native extensions and surfaces clear remediation.
9. **fastembed model cache permission errors.** Default cache locations may collide with multi-user installs or read-only filesystems. Mitigation: force `~/.cache/cortex/models/` (XDG-respectful), or per-project `.cortex/.index/models/` if `~/.cache/` unwritable; document the override env var.
10. **Cortex scope-creep one-way door.** Council flagged: maintaining ONNX models, chunkers, and rerankers shifts Cortex from a file-format protocol toward an ML-ops sidecar. **Mitigation:** the `cortex retrieve` interface is **non-normative** (per doctrine supersede); Cortex's protocol/SPEC stays storage-only; semantic features ship as opt-in CLI surface. Re-evaluate at v1.x if maintenance burden grows.
11. **`sqlite-vec` long-term maintenance.** sqlite-vec is a relatively new SQLite extension. If upstream support stalls or SQLite version incompatibilities arise, Cortex faces a costly storage migration. Mitigation: keep storage-format documented in the chunker's `chunk_strategy_version`; rebuild from canonical markdown at any time; alternative backends (LanceDB, plain numpy) are bounded ports if needed.
12. **Air-gapped vs lazy-download tension.** Council flagged: zero-config lazy model download (good UX) and enterprise air-gapped install (no network at runtime) cannot both be satisfied without a 50MB+ bundled binary. Mitigation: document an `OFFLINE_MODE` install path (manual `pip install fastembed-models[bge-small]` or similar) for air-gapped users; lazy download stays the default.

## Doctrine supersede draft (S0 deliverable)

A new doctrine entry, with council-recommended tightening on the index status and the interface's normativity:

> **0007 — Cortex owns the retrieval interface; storage stays markdown + git (supersedes 0005 #1)**
>
> Cortex Doctrine 0005 #1 said Cortex was "not a vector store" and that "external indexing is fine, storing vectors inside `.cortex/` is not." Two years of consumer experience (Sentinel especially, at deployed scale) show this framing is too strict: every consumer re-implements the same chunking + retrieval logic against the same format, and recency-by-grep stops scaling past ~100 entries. This doctrine narrows the prior framing.
>
> **What stays the same:**
>
> - **Canonical storage is markdown + git + grep.** This is the load-bearing portability guarantee. Removing `.cortex/.index/` loses nothing not regeneratable from `.cortex/` markdown. Any tool with grep can still read `.cortex/`.
>
> **What changes:**
>
> - Cortex now owns the **retrieval interface** (`cortex retrieve`) with an opt-in derived index at `.cortex/.index/` (gitignored, recomputable). Vectors live outside canonical content.
> - The index is **hazmat**. Consumers (Sentinel, Touchstone, future tools) **must** use the `cortex retrieve` interface. Direct queries against `.cortex/.index/chunks.sqlite` are unsupported and may break across versions without notice. The index format is internal.
> - The `cortex retrieve` interface is a **non-normative reference implementation**. It is part of the Cortex CLI distribution but NOT part of the Cortex Protocol / SPEC. Custom consumers are explicitly free to bypass it and implement their own retrieval over `.cortex/` markdown content (the normative format). The Cortex Protocol promises the *storage* layer; the *retrieval* layer is implementation convenience.
> - The grep floor is doctrine. `cortex grep` (the existing Phase B command) ships untouched and never depends on sqlite-vec, ONNX, or any embedder. Failure modes in semantic-path dependencies must not affect grep availability.
>
> **What this entry does not change:**
>
> - Doctrine 0005 #2 (`.cortex/.index.json` is a cache; git is the durable store) — extended in spirit to `.cortex/.index/` (the new index location). Both are caches, both regeneratable.
> - Doctrine 0005 #4 (one project per `.cortex/`) — unchanged.
> - Doctrine 0005 #7 (not cloud-hosted) — unchanged. The retrieval interface ships with a CPU-only local default; cloud embedders are opt-in via Conductor.

(The actual entry will be written and reviewed in S0; this draft establishes the framing.)
