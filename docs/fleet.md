# `cortex fleet`

`cortex fleet` answers the question "are all my Cortex-enabled repos current?" across many repositories at once, instead of running `cortex doctor` and `cortex update --check` repo-by-repo.

It has two subcommands:

```sh
cortex fleet check [--json] [--path P ...] [--paths-file F] [--root R ...] [--audit-instructions]
cortex fleet update [--dry-run] [--pr] [--path P ...] [--paths-file F] [--root R ...]
```

`fleet check` is read-only. `fleet update` writes only refreshed generated layers, and only to repos that are stale **and** structurally valid.

## Discovery

Repos are discovered in this precedence order; the first source that yields any candidate wins (so naming explicit paths never also triggers an unrelated sibling scan):

1. **Explicit** — `--path P` (repeatable) and/or `--paths-file F`.
2. **`~/.touchstone-projects`** — if present. Read as either a JSON list of path strings or a newline-delimited file (`#` comments allowed). Optional; absence is normal.
3. **Sibling scan** — immediate child directories of each `--root` (default `~/repos`) that contain a `.cortex/` directory. One level deep only.
4. **Current directory** — as a final fallback.

`--paths-file` accepts the same JSON-list or newline-delimited format as `~/.touchstone-projects`. A malformed paths file is reported on stderr and skipped, never silently dropped.

## `cortex fleet check`

For each discovered repo, `check`:

- Classifies the **install shape** (see table below).
- Runs the `cortex update --check` freshness logic **in-process** (reusing `cortex.commands.sync._state_update_needed` / `_index_update_needed` — no `cortex` subprocess is spawned).
- Runs the structural `cortex doctor` checks **in-process** (`cortex.validation.run_all_checks` + `cortex.doctor_checks.run_plain_checks`).
- Optionally runs `--audit-instructions` (`cortex.audit_instructions.audit_instructions`).
- Rolls those signals into a traffic-light classification with one actionable next command.

Exit code is `0` when no repo is red, `1` when any repo is red (structural doctor errors, an unsupported/partial install, or a repo that could not be classified).

### Install shapes

| `install_shape` | Meaning |
|---|---|
| `full` | Supported, current `SPEC_VERSION`, `protocol.md` and core subdirs present. |
| `legacy` | Supported but an older supported `major.minor` than this CLI's literal. Still readable. |
| `partial` | Supported `SPEC_VERSION` but an incomplete scaffold (missing `protocol.md` or a core subdir). |
| `missing_spec_version` | `.cortex/` exists but no `SPEC_VERSION` file. |
| `unsupported_spec` | `SPEC_VERSION` present but `major.minor` not in this CLI's `SUPPORTED_SPEC_VERSIONS`. |
| `missing` | No `.cortex/` directory — not a Cortex repo. |

### Traffic-light classification

| `classification` | Meaning |
|---|---|
| `green` | Current, no structural errors. |
| `yellow` | Structurally clean but stale generated layers (or advisory doctor / audit warnings). |
| `red` | Structural doctor errors, or an unsupported/partial/missing-spec install, or unclassifiable. |
| `skipped` | No `.cortex/` store (`missing`). |

### Stable JSON contract (`--json`)

`--json` emits a top-level object `{"repos": [ ... ]}`. Each per-repo record is a **stable public contract** — fields are added deliberately, never by dumping internal structures:

| Field | Type | Meaning |
|---|---|---|
| `path` | string | Absolute path to the repo root. |
| `repo` | string | Basename of the repo root (display name). |
| `spec_version` | string \| null | Declared `.cortex/SPEC_VERSION`, or null. |
| `install_shape` | string | One of the install shapes above. |
| `update_status` | string | `current` / `stale` / `unknown`. |
| `update_reasons` | string[] | Human-readable stale reasons (may be empty). |
| `doctor_errors` | int | Count of structural doctor ERROR issues. |
| `doctor_warnings` | int | Count of structural doctor WARNING issues. |
| `audit_warnings` | int \| null | `--audit-instructions` warning count, or null when not requested. |
| `classification` | string | `green` / `yellow` / `red` / `skipped`. |
| `next_command` | string | The single actionable command for this repo. |
| `error` | string \| null | Non-null when the repo could not be classified; the repo still appears (no silent drops). |

## `cortex fleet update`

`update` refreshes stale generated layers across discovered repos, using the same `run_sync` path each project's own `cortex update` uses. A repo is **eligible** only when its layers are stale **and** it has no structural doctor errors and a valid install shape. Ineligible repos are reported as `skipped` with the blocking reason (structural errors, already current, etc.) — never silently dropped.

- **`--dry-run`** lists exactly what would be rewritten and writes nothing (no files, no index, no commits).
- **`--pr`** creates a per-repo scoped branch `cortex/fleet-update`, commits the refreshed layers, pushes, and opens a PR with `gh`. It **never commits to `main`/`master`**: it switches to the scoped branch first and refuses to proceed if it cannot leave the default branch. On any failure it leaves the branch in place for inspection and force-pushes nothing.
- **Default (no `--pr`, no `--dry-run`)** refreshes in place on the repo's current branch, and refuses if the worktree is dirty (use `--pr` or commit/stash first).

`--dry-run` and `--pr` cannot be combined.
