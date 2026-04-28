# `cortex grep`

`cortex grep` searches `.cortex/` and prints each matched file with a one-line metadata summary. It understands both YAML frontmatter and the SPEC § 6 bold-inline scalar fallback used by entries such as Doctrine:

```markdown
**Status:** Accepted
**Load-priority:** always
```

## Frontmatter Filters

Use `--frontmatter KEY:VALUE` to keep only files whose metadata matches:

```sh
cortex grep "retry backoff" --frontmatter Status:Accepted
```

Keys are case-insensitive, so `Status:Accepted` and `status:Accepted` are equivalent. Values are exact and case-sensitive by default.

Repeat `--frontmatter` to require every filter:

```sh
cortex grep --frontmatter status:active --frontmatter type:plan
```

When no pattern is provided, `cortex grep` skips ripgrep and lists every file whose metadata matches the filters:

```sh
cortex grep --frontmatter Load-priority:always
```

## Sentinel Queries

Sentinel's read-side Cortex retrieval can use these narrow queries:

```sh
cortex grep --frontmatter rejected:true
cortex grep --frontmatter status:active --frontmatter type:plan
cortex grep --frontmatter superseded-by:*
cortex grep --frontmatter Load-priority:always
```

The same filters can be combined with a body search:

```sh
cortex grep "autonomous engineer" --frontmatter status:active --frontmatter type:plan
```

That command requires both a ripgrep body match and matching metadata.

## Wildcard Presence

Use `*` as the value to match any non-empty value:

```sh
cortex grep --frontmatter superseded-by:*
```

This matches a file with `Superseded-by: doctrine/0005-new.md` or `**Superseded-by:** doctrine/0005-new.md`, and excludes files where the key is missing or empty.

## Negation

Prefix a filter with `!` to exclude files where the key matches:

```sh
cortex grep --frontmatter '!Status:Superseded'
```

Negation only changes that one predicate. Repeated filters are still conjunctions:

```sh
cortex grep --frontmatter type:plan --frontmatter '!status:shipped'
```

## List Values

If YAML frontmatter uses a list, the filter matches when any element equals the requested value:

```yaml
Tags: [read-side, sentinel, retrieval]
```

```sh
cortex grep --frontmatter tags:sentinel
```

Block lists use the same semantics:

```yaml
Tags:
  - read-side
  - sentinel
```

## Layer Scoping

`--layer` limits both the body search and the metadata filter to one `.cortex/` layer:

```sh
cortex grep --layer doctrine --frontmatter Load-priority:always
```

`--path` still selects the project root, and arguments after `--` are still forwarded to ripgrep when a pattern is present:

```sh
cortex grep "retry" --frontmatter Status:Accepted -- -i -C 2
```

## Boundaries

Filters are conjunction-only. There is no OR syntax.

Values are exact matches, except for the `*` presence check. There is no range, glob, or regex matching on frontmatter values.
