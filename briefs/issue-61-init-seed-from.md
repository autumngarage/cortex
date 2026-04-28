# Task: `cortex init --seed-from <dir>` for external Doctrine packs

You are an autonomous engineering agent dispatched by Cortex to address
[issue #61](https://github.com/autumngarage/cortex/issues/61). You operate
inside a fresh git worktree branched off `origin/main`. You will own the
implementation end-to-end and ship it via `scripts/open-pr.sh --auto-merge`.

## Goal

External tools (Sentinel first, others later) need to ship default
Doctrine packs that get copied into a project's `.cortex/doctrine/`
on init. Cortex stays format-only and neutral; the *content* opinions
live in the consuming tool. This is the ESLint-shareable-config /
tsconfig-base pattern applied to Doctrine.

`cortex init --seed-from <dir>` copies markdown files from `<dir>`
into the new project's `.cortex/doctrine/`, preserving frontmatter,
with smart numbering and a clear conflict policy.

## Required behavior

`cortex init [--path <project>] --seed-from <dir> [--merge skip-existing]`:

1. **Resolve `<dir>`.** Path may be absolute or relative to cwd.
   Tilde-expand. If not a directory, exit 2 with a clear error.

2. **Discover entries.** Glob `*.md` directly under `<dir>` (one
   level — do not recurse). Skip files starting with `_` (e.g.,
   `_template.md`). Sort lexicographically for deterministic
   numbering.

3. **Numbering policy.**
   - If a source filename matches `^(\d{4})-.*\.md$`, treat that
     number as the requested doctrine number.
   - Otherwise assign the next available number starting from one
     greater than the highest existing entry in
     `.cortex/doctrine/`, lower-bounded at `0100` (the `0001-0099`
     range is reserved per the v0.2.5 floor convention; carry it
     forward).
   - For un-numbered source files, derive a slug from the file's
     `# H1` heading (lowercased, hyphenated, ASCII-only). Fall back
     to the filename stem if no H1.

4. **Frontmatter preservation.** Copy bytes verbatim. Do not rewrite
   or normalize — the consuming tool's frontmatter (e.g.,
   `Sentinel-baseline: true`, `Load-priority: always`,
   `Promoted-from:`) must reach the destination unchanged.

5. **Conflict policy (default = abort).** If any destination filename
   already exists in `.cortex/doctrine/`, exit 4 with a list of
   conflicting files. Do not write any file — atomic-or-nothing.

6. **`--merge skip-existing` opt-in.** With this flag, conflicting
   destination files are skipped (logged to stderr); non-conflicting
   files are copied. Idempotent: re-running with the same `<dir>` and
   `--merge skip-existing` is a no-op.

7. **Help text.** `cortex init --help` documents `--seed-from`,
   `--merge`, the numbering policy, and the conflict behavior.
   Include one motivating example referencing Sentinel's planned
   pack.

8. **Combinable with existing init flags.** `--seed-from` runs
   *after* the standard `cortex init` scaffolding. If the project
   already has a non-empty `.cortex/doctrine/` (e.g., the v0.2.2
   scan-and-absorb has populated it), the conflict-policy applies as
   above.

## Implementation outline

- Extend `src/cortex/commands/init.py`:
  - New Click options `--seed-from` (path) and `--merge`
    (`click.Choice(["skip-existing"])`, default `None`).
  - After existing scaffolding, call a new helper
    `seed_doctrine_from(source_dir, project_root, *, merge_mode)` in
    `src/cortex/seed.py` (new module).
- New module `src/cortex/seed.py`:
  - Pure function (file-system-touching but not interactive).
  - Returns a dataclass `SeedResult(copied: list[Path], skipped: list[Path], conflicts: list[Path])`.
- Reuse the existing doctrine numbering helpers if present; if
  numbering logic only lives inline in the v0.6.0 `cortex promote`
  rewrite plan, extract it now into `src/cortex/doctrine.py` so
  both seed and promote share one numbering source. (Pre-implementation
  checklist #1: don't hand-roll a second copy.)

## Tests required

New `tests/test_init_seed.py`. Real `tmp_path` fixtures, no mocks.

1. **Fresh seed** — empty project, `<dir>` has three numbered files;
   destination has all three with bytes preserved (assert
   byte-equality, including frontmatter).
2. **Numbering: sources un-numbered** — three un-numbered source
   files; destination assigned `0100`, `0101`, `0102` lexicographically.
3. **Numbering: floor honored** — existing project has `0099`
   (somehow); seed assigns `0100`+ regardless. (Edge case for
   migration scenarios; defend the invariant.)
4. **Numbering: collision avoided** — source file `0102-foo.md`,
   destination already has `0102-bar.md`; default mode aborts.
5. **Conflict abort** — destination has `0100-x.md`, source has
   `0100-x.md` with different bytes; exit 4, no files written.
6. **`--merge skip-existing`** — same conflict, but with the flag:
   exit 0; conflicting destination unchanged; non-conflicting source
   files copied. Idempotent on re-run.
7. **Skip `_` prefix** — `_template.md` in source is not copied.
8. **`<dir>` doesn't exist** — exit 2 with clear error.
9. **`<dir>` has no `*.md`** — exit 0, log "no doctrine entries
   found", make no changes.
10. **Help text** — `cortex init --help` mentions `--seed-from` and
    `--merge`.

## Acceptance criteria

- All ten test cases pass.
- `cortex init --help` documents the new flags.
- `bash scripts/touchstone-run.sh validate` is green.
- The commit message includes `closes #61`.
- A short note added to `README.md` (or `docs/init.md` if it exists)
  pointing at the seed-from pattern. One paragraph.

## Out of scope

- Multi-source seeding (`--seed-from a --seed-from b`). One source
  per init call. File a follow-up if Sentinel needs multi-source.
- Network-fetch seeds (`--seed-from https://...`). Local paths only.
- `--merge overwrite-existing`. Hold off until a real consumer asks
  — destructive overwrite without explicit confirmation is the path
  to silent doctrine corruption.
- A separate `cortex doctrine seed` command. Keep it as an `init`
  flag; if the operation needs to run post-init, file a follow-up.

## SPEC + design references

- `SPEC.md` § 3.1 (Doctrine: immutable-with-supersede; numbering
  conventions).
- `.cortex/doctrine/0005-scope-boundaries-v2.md` #4 (entry size).
- v0.2.5 numbering floor: see
  `.cortex/journal/2026-04-25-v0.2.4-and-v0.2.5-released.md` for the
  `0001-0099` reserved-range origin.

## How to ship

```sh
bash scripts/touchstone-run.sh validate
git add src/cortex/commands/init.py src/cortex/seed.py \
        src/cortex/doctrine.py tests/test_init_seed.py \
        README.md
git commit -m "feat: cortex init --seed-from for external Doctrine packs (closes #61)"
bash scripts/open-pr.sh --auto-merge
```

The commit message MUST include `closes #61` so the issue auto-closes
on merge.
