# Task: `cortex grep --frontmatter` filter syntax for Sentinel queries

You are an autonomous engineering agent dispatched by Cortex to address
[issue #60](https://github.com/autumngarage/cortex/issues/60). You operate
inside a fresh git worktree branched off `origin/main`. You will own the
implementation end-to-end and ship it via `scripts/open-pr.sh --auto-merge`.

## Goal

Sentinel is moving to read-side Cortex consumption (per `autumn-garage/.cortex/plans/sentinel-autonomous-engineer.md`).
Mid-cycle retrieval needs frontmatter-aware filtering — queries like
`rejected:true`, `status:active type:plan`, `superseded-by:*`, and
`Load-priority:always` must produce useful, narrow result sets.

The current `cortex grep` (`src/cortex/commands/grep.py`) annotates each
matched file with frontmatter, but does not **filter by** frontmatter
fields. This task adds that filter syntax so Sentinel's planned queries
work.

## Required behavior

`cortex grep [PATTERN] --frontmatter <key>:<value>` filters search
results to files whose frontmatter (or bold-inline `**Key:** value`
fallback per SPEC § 6) matches.

1. **Conjunction by repetition.** `--frontmatter status:active --frontmatter type:plan`
   is AND. Both must match.

2. **Wildcard value.** `--frontmatter superseded-by:*` matches when the
   key is present with any non-empty value (presence check).

3. **Case-insensitive keys.** `Status:` and `status:` are equivalent.
   Values match exactly (case-sensitive) by default; document this in
   `--help`.

4. **Empty PATTERN allowed when filtering.** If the user passes only
   `--frontmatter` flags with no PATTERN, list every file whose
   frontmatter matches — no body grep. Detect via positional argument
   absence and skip the ripgrep call entirely; print the same
   `<file> [<metadata>]` summary the existing command emits.

5. **`--layer`, `--path`, and forwarded `rg` args continue to work**
   unchanged. Filter applies after layer scoping.

6. **Negation.** `--frontmatter !key:value` excludes files where the
   key matches. Document in `--help`. (Sentinel's `rejected:true`
   query and a future `!Status:Superseded` are both motivating.)

7. **List-valued frontmatter.** If a frontmatter value is a YAML list
   (e.g., `Tags: [a, b, c]`), the filter matches when **any** list
   element equals the requested value. Document.

## Implementation outline

- Extend `grep_command` in `src/cortex/commands/grep.py` with
  `--frontmatter` (multiple, `multiple=True`) accepting `key:value`
  strings.
- New helper module `src/cortex/grep_filter.py` (pure, side-effect-
  free): parses `key:value` (and `!key:value`, `key:*`) into a
  `FrontmatterFilter` dataclass; exposes `matches(frontmatter, bold_fields) -> bool`.
- When `PATTERN` is empty AND filters are non-empty, walk
  `search_root` directly (no `rg`) and apply the filter to each file's
  parsed frontmatter; output uses the same `_summarize_file`
  formatter.
- When `PATTERN` is non-empty, run `rg` first; then drop files whose
  frontmatter doesn't match; only print surviving file groups.
- `_summarize_file` already extracts bold-inline `**Key:** value`
  pairs — reuse that path so the filter sees both YAML frontmatter
  and bold-inline metadata.
- Update `cortex grep --help` to document filter syntax with the four
  Sentinel motivating examples.
- Add `docs/grep.md` (new file) with a tutorial-style walkthrough:
  filter syntax, the four Sentinel queries, list-value semantics,
  layer-scoped filtering. Link from `cortex grep --help` to
  `docs/grep.md` and from `README.md` if there's a "commands" or
  "docs" section that references other commands.

## Tests required

`tests/test_grep.py` already exists — extend it. Use `tmp_path`
fixtures with real frontmatter (no mocks).

1. **Single filter** — `cortex grep "" --frontmatter status:active`
   matches only files whose `Status: active` (or
   `**Status:** active`).
2. **Conjunction** — `--frontmatter status:active --frontmatter type:plan`
   matches only intersection.
3. **Wildcard** — `--frontmatter superseded-by:*` matches presence;
   missing key excluded.
4. **Negation** — `--frontmatter !type:incident` excludes incidents.
5. **List value** — frontmatter `Tags: [a, b, c]` matches
   `--frontmatter tags:b`.
6. **Empty PATTERN, filter only** — produces file list without
   running `rg` (assert via no body lines in output).
7. **`PATTERN` + filter** — body match must occur AND filter must
   match.
8. **Case-insensitive key** — `Status:` and `status:` equivalent.
9. **Layer scoping** — `--layer doctrine --frontmatter Load-priority:always`
   limits both grep and filter to doctrine.
10. **Sentinel queries** — exercise the four issue queries against a
    fixture corpus (`rejected:true`, `status:active type:plan`,
    `superseded-by:*`, `Load-priority:always`) and assert each
    returns the expected files.

## Acceptance criteria

- All four Sentinel motivating queries work and are documented.
- `cortex grep --help` covers the new flag with one example per
  syntax form.
- `docs/grep.md` exists with the four motivating queries as worked
  examples.
- Existing `cortex grep` tests still pass.
- `bash scripts/touchstone-run.sh validate` is green.

## Out of scope

- OR / disjunction across `--frontmatter` flags. Conjunction only;
  if needed later, file a follow-up issue.
- Range or regex matching on values. Exact match (with the wildcard
  presence-check). Note this in `docs/grep.md` so future feature
  requests cite a known boundary.
- Performance optimization for huge corpora. Walk files
  straightforwardly; SPEC's expected scale is hundreds of entries,
  not millions.

## SPEC + design references

- `SPEC.md` § 6 (frontmatter conventions, bold-inline scalars).
- `.cortex/protocol.md` § 1 (mid-session retrieval is grep — Doctrine
  0005 #1).
- `.cortex/doctrine/0005-scope-boundaries-v2.md` #1.

## How to ship

```sh
bash scripts/touchstone-run.sh validate
git add src/cortex/commands/grep.py src/cortex/grep_filter.py \
        tests/test_grep.py docs/grep.md
git commit -m "feat: cortex grep --frontmatter filter syntax (closes #60)"
bash scripts/open-pr.sh --auto-merge
```

The commit message MUST include `closes #60` so the issue auto-closes
on merge.
