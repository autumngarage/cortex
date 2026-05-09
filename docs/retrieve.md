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

1. Run `cortex manifest --budget N` for session-start hot context.
2. Run `cortex grep` for exact strings, frontmatter, audit, and every-hit checks.
3. Run `cortex retrieve --mode bm25 --for-agent` for ranked lookup.
4. Use `semantic` or `hybrid` only when embeddings already exist or you
   explicitly intend to pay the semantic-index cost.
5. Open only the cited file or line range that the compact result justifies.

## Budgets

`--for-agent` defaults to 600 excerpt characters per result. Tune it for the
handoff:

```bash
cortex retrieve "journal staging" --for-agent --excerpt-chars 240 --top-k 5
```

The top blockquote summary is not part of the excerpt cap. Entries that need to
be useful in agent lookup should keep that first blockquote concise and factual.
