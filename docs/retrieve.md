# `cortex retrieve`

`cortex retrieve` searches the gitignored derived index for `.cortex/`
Doctrine, Journal, Plans, digests, `state.md`, and `map.md`. Use it after the
bounded manifest and `cortex grep` lookup path when ranked results are useful.

## Agent Loop

Use the compact agent shape before opening full source files:

```bash
cortex retrieve "why did we choose lookup-first reads" --mode bm25 --for-agent
```

The output is JSON. Each result starts with citation fields and compact context:

- `path` and `citation` for stable source reads
- `line_range` for targeted opening
- `layer`, `type`, `status`, and `frontmatter`
- `summary`, populated from the entry's first top-level blockquote when present
- `excerpt`, capped by `--excerpt-chars`
- `excerpt_omitted` and `omission` when the excerpt was truncated
- `next_step`, usually to open the cited file or search narrower

Recommended flow:

1. Run `cortex manifest --budget 8000` for normal session-start hot context.
   Use `--show-budget` when tuning a workflow and `--json` when a wrapper needs
   machine-readable token/omission metadata.
2. Run `cortex grep` for exact strings, frontmatter, audit, and every-hit checks.
3. Run `cortex retrieve --mode bm25 --for-agent` for ranked lookup.
4. Use `semantic` or `hybrid` only when embeddings already exist or you
   explicitly intend to pay the semantic-index cost.
5. Open only the cited file or line range that the compact result justifies.

Semantic backfill is opt-in. A lookup such as `cortex retrieve "query" --mode
hybrid` falls back to BM25 when embeddings are missing and tells you how to
build them. To pay the cost deliberately, run one of:

```bash
cortex refresh-index --retrieve --semantic
cortex retrieve "query" --mode hybrid --build-embeddings
```

When embedding backfill runs, Cortex reports the indexed chunk count, embedded
chunk count, model name, index path, and model-cache path.

## Budgets

`--for-agent` defaults to 600 excerpt characters per result. Tune it for the
handoff:

```bash
cortex retrieve "journal staging" --for-agent --excerpt-chars 240 --top-k 5
```

The top blockquote summary is not part of the excerpt cap. Entries that need to
be useful in agent lookup should keep that first blockquote concise and factual.

Normal coding startup should fit in the default 8k-token manifest. Agent-to-agent
delegation should start from `cortex manifest --profile delegation` (4k tokens by
default). Generated Journal entries should stay under ~1200 estimated tokens
unless the writer passes `cortex journal draft <type> --allow-large` and
accepts the review cost explicitly.
