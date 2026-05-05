# `.cortex/config.toml` reference

> The optional per-project configuration file. All sections and keys are
> optional; an absent file uses defaults throughout. Cortex commands
> (`cortex doctor`, `cortex manifest`, `cortex refresh-index`,
> `cortex doctor --audit-instructions`, …) work without a config — defaults
> cover the no-config case.

## File location

`<project-root>/.cortex/config.toml`. Loaded by the in-tree readers in
`src/cortex/config.py` (`load_audit_instructions_config`,
`load_refresh_index_config`) and validated by
`check_config_toml_schema` in `src/cortex/doctor_checks.py`.

## Compatibility

Cortex SPEC v0.5.x and the v0.6.0+ CLI accept this format. Future
sections will be additive per [SPEC § 7](../SPEC.md). Validation
behaviour:

- **Unknown keys** inside a known section surface as a `cortex doctor`
  warning (`[<section>] unknown key \`<name>\``).
- **Type mismatches** for known keys surface as a `cortex doctor` error
  (`[<section>] \`<name>\` must be <type>`).
- **Unknown top-level sections** are silently ignored — only the sections
  named below are validated. (Notable consequence today: the
  `[refresh-index]` section described below is consumed by
  `cortex refresh-index` but is not part of the schema check, so it
  won't trigger unknown-key warnings.)
- **Missing file**, **unreadable file**, and **un-parseable TOML** all
  degrade gracefully — readers fall back to defaults; doctor surfaces a
  parse error.

## Sections

### `[audit-instructions]`

Configuration for `cortex doctor --audit-instructions` (the
across-the-fourth-wall claim audit; SPEC § 4.3.1). Parsed by
`load_audit_instructions_config` in `src/cortex/config.py`. Validated by
`check_config_toml_schema` in `src/cortex/doctor_checks.py`.

When the section is absent, the auditor runs in **discovery mode** —
findings come from content scanned in the repo, not from explicit
declarations.

| Key | Type | Default | Description |
|---|---|---|---|
| `homebrew_tap` | string \| null | `null` | Homebrew tap to audit for stale claims (e.g. `"autumngarage/cortex"`). Empty strings are normalized to `null`. |
| `siblings` | list of strings \| null | `[]` | Local filesystem paths for sibling repos (e.g. `"~/repos/touchstone"`) to cross-check for drift. |
| `pypi_package` | string \| null | `null` | PyPI package name to audit (e.g. `"cortex"`). Empty strings are normalized to `null`. |
| `github_repos` | list of strings \| null | `[]` | GitHub `owner/repo` references to check for release / tap state. |
| `urls` | list of strings \| null | `[]` | Free-form URLs to check for liveness or version drift. |
| `scan_files` | list of strings \| null | `["CLAUDE.md", "AGENTS.md", "README.md"]` (`DEFAULT_AUDIT_SCAN_FILES`) | Repo-root files to scan for external claims. Setting this overrides the default — pass the full list, not a delta. |

Source pointers:
- Dataclass: `AuditInstructionsConfig` in `src/cortex/config.py`.
- Parser: `load_audit_instructions_config` in `src/cortex/config.py`.
- Schema check: `check_config_toml_schema` (`audit_schema` table) in
  `src/cortex/doctor_checks.py`.

### `[doctrine.0007]`

Per-project overrides for the canonical-ownership warning ([Doctrine
0007](../.cortex/doctrine/0007-canonical-ownership-of-state-and-plans.md)).
By default, `cortex doctor` warns when repo-root files like `ROADMAP.md`,
`STATUS.md`, `PLAN.md`, `NEXT.md`, or `TODO.md` exist alongside
`.cortex/state.md` + an active plan, because they duplicate the
canonical answers to "where are we" / "what's next."

| Key | Type | Default | Description |
|---|---|---|---|
| `allowed_root_files` | list of strings | `[]` | Repo-root filenames that suppress the canonical-ownership warning. Per-file, case-sensitive against the actual filename (e.g. `["ROADMAP.md"]` to keep a documented root-level roadmap). |

Source pointers:
- Reader: `_doctrine_0007_allowed_root_files` in
  `src/cortex/doctor_checks.py`.
- Schema check: `check_config_toml_schema` (`doctrine.0007` branch) in
  `src/cortex/doctor_checks.py`.
- Warning emitter: `check_canonical_ownership` in
  `src/cortex/doctor_checks.py`.

### `[refresh-index]`

Configuration for `cortex refresh-index` (the promotion-queue index
writer). Parsed by `load_refresh_index_config` in `src/cortex/config.py`.

> **Schema-validation gap.** This section is consumed by the CLI but is
> **not** included in `check_config_toml_schema`'s known-section list as
> of v0.7.0, so its keys do not produce schema warnings/errors.
> Documented here for completeness; declare with care.

| Key | Type | Default | Description |
|---|---|---|---|
| `candidate_patterns` | list of strings | `[]` | Case-insensitive **substring matches against the body** of Journal entries with `Type: decision`. A Journal entry whose body contains any listed substring is added to `.cortex/.index.json` as a Doctrine-promotion candidate. (Entries already tagged `candidate-doctrine` are picked up regardless of this list.) These are not file globs and not regex. |

Source pointers:
- Dataclass: `RefreshIndexConfig` in `src/cortex/config.py`.
- Parser: `load_refresh_index_config` in `src/cortex/config.py`.
- Matcher: `_is_candidate` in `src/cortex/index.py`.

### `[doctor.stale-checkbox]`

Configuration for stale high-authority guidance checks:
`check_stale_plan_checkboxes`, `check_stale_pickup_pointers`, and
`check_stale_state_current_work` (cortex#100 plus the 2026-05-04 dogfood
extension). Read by `_stale_checkbox_window_days` in
`src/cortex/doctor_checks.py`. Schema-validated by
`check_config_toml_schema`.

| Key | Type | Default | Description |
|---|---|---|---|
| `window_days` | positive integer | `14` | How many days back to scan `Type: release` and `Type: pr-merged` Journal entries when computing overlap with active-plan `- [ ]` checkboxes, active-plan `## Pickup pointer` prose, and `.cortex/state.md ## Current work` bullets. Larger values catch older drift; smaller values reduce false positives from long-tail mentions. |

**Bypass marker.** A checkbox, pickup-pointer section, or current-work
bullet annotated `<!-- cortex:no-stale-check -->` is exempt from the
overlap check. Use this for legitimately aspirational items whose prose
overlaps with release/PR-merge journal mentions without being shipped
(e.g., "sustained-work period across all three targets" — release entries
naturally name those targets).

**State source freshness.** Separately, `check_generated_layers` warns when
`.cortex/state.md` was generated before newer primary Cortex sources
(`plans/`, `journal/`, `doctrine/`, templates, case studies, protocol/spec,
or the project manifest). This check has no config knob; rerun
`cortex refresh-state` or update the preserved hand-authored section.

Source pointers:
- Checks: `check_stale_plan_checkboxes`, `check_stale_pickup_pointers`,
  `check_stale_state_current_work`, and `check_generated_layers` in
  `src/cortex/doctor_checks.py`.
- Window reader: `_stale_checkbox_window_days` (same file).
- Schema: `check_config_toml_schema` (same file).

## Worked example

A complete realistic config for a project that has a Homebrew tap,
sibling-repo cross-checks, and a documented reason to keep a root-level
`ROADMAP.md`:

```toml
[audit-instructions]
homebrew_tap = "autumngarage/example"
siblings = ["~/repos/example-helper"]
pypi_package = "example"
urls = ["https://example.com/install"]
scan_files = ["CLAUDE.md", "AGENTS.md", "README.md"]
github_repos = ["autumngarage/example"]

[doctrine.0007]
allowed_root_files = ["ROADMAP.md"]
```

Every key in this example is one the schema validator (`audit-instructions` +
`doctrine.0007`) accepts at its current type. Run `cortex doctor` after
editing `.cortex/config.toml` to confirm: no `unknown key` warnings on
the `[audit-instructions]` or `[doctrine.0007]` sections, and no type
errors.

## Validation

`cortex doctor` validates this file via `check_config_toml_schema` (see
`src/cortex/doctor_checks.py`). Run `cortex doctor` to surface unknown
keys (warning) or type mismatches (error). A missing `.cortex/config.toml`
is not an error — every consumer falls back to defaults.
