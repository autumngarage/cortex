"""`cortex init` — scaffold a SPEC-v0.3.1-dev-conformant `.cortex/` directory
and absorb existing project structure (principles/, decisions/, ROADMAP.md,
…) into it via per-file interactive prompts.

Creates:

- `.cortex/SPEC_VERSION`            → current spec version (major.minor.patch[-dev])
- `.cortex/protocol.md`             → copy of the Cortex Protocol shipped with this CLI
- `.cortex/README.md`               → human-facing orientation doc (layer map, edit rules)
- `.cortex/templates/...`           → copy of the template tree shipped with this CLI
- `.cortex/doctrine/`               → empty; seeded with `.gitkeep`
- `.cortex/plans/`                  → empty; seeded with `.gitkeep`
- `.cortex/journal/`                → empty; seeded with `.gitkeep`
- `.cortex/procedures/`             → empty; seeded with `.gitkeep`
- `.cortex/map.md`                  → seven-field stub with `Incomplete: [all sources]`
- `.cortex/state.md`                → seven-field stub; Sources enriched from the scan

Refuses to overwrite an existing `.cortex/SPEC_VERSION` unless `--force` is
passed. With `--force`, the scaffold files (SPEC_VERSION, protocol.md,
templates/, map.md/state.md stubs) are overwritten; existing doctrine, plan,
journal, and procedure content is never deleted (idempotency by
``Imported-from:`` cites — re-running on an absorbed repo never duplicates).

Scan-and-absorb (no flag — driven by the scan + interactive prompts):
on every TTY invocation, init walks the project root for known patterns
(`principles/*.md`, `docs/decisions/*.md`, `ROADMAP.md`, `*PLAN*.md`,
`README.md`, `CHANGELOG.md`, …), prints a one-screen summary grouped by
category (Doctrine / Plan / Map ref / Reference / Unknown) and asks
"Continue?" before doing anything. Then per Doctrine candidate it asks
"Import as Doctrine?" (default Yes), per Plan candidate "Import as Plan?"
(stubbing required Plan sections as `[ ] Hand-author from <source>`
checklists), and per unknown file "[D]octrine / [P]lan / [M]ap ref /
[R]eference / [S]kip?" (default M). Unknown classifications persist to
`.cortex/.discover.toml` so future invocations recognize the pattern
without re-prompting. Source files are never modified — every imported
entry cites the source via `Imported-from:` frontmatter and the source
remains canonical text.

CHANGELOGs and `journal/*.md` are NEVER auto-imported into the Cortex
Journal — Journal is time-anchored and append-only (Protocol § 4.1), so
backfilling synthetic entries from past CHANGELOGs would lie about when
events happened. Such files surface in `state.md` Sources only.

Interactive first-run follow-ups (per Doctrine 0002 — interactive-by-default):
when run on a TTY against a project that already has `CLAUDE.md` / `AGENTS.md`,
`cortex init` offers to append `@.cortex/protocol.md` + `@.cortex/state.md`
imports, and offers to add `.cortex/.index.json` + `.cortex/pending/` entries
to `.gitignore`. Each prompt defaults to Yes. Flags (`--add-imports-claude`,
`--add-imports-agents`, `--gitignore`, and their `--no-*` counterparts) skip
the corresponding prompt. `--yes`/`-y` accepts all defaults without prompting.
Non-TTY invocations without `--yes` skip all three follow-ups silently and
do not absorb scan candidates either (preserves the pre-interactive
scaffolding behavior — scan summary still prints).

`--local-only` inverts the `.gitignore` default. The SPEC treats `.cortex/`
as committed, team-shared memory. Solo / private projects that don't want
journals, plans, doctrine, or state published alongside their code pass
`--local-only` to gitignore the whole `.cortex/` directory instead of just
the transient paths. Conflicts with `--no-gitignore`.
"""

from __future__ import annotations

import re
import shlex
import shutil
import sys
from datetime import UTC, date, datetime
from importlib import resources
from pathlib import Path

import click

from cortex import (
    __version__ as CORTEX_VERSION,  # noqa: N812 — namespaced re-export, deliberate caps.
)
from cortex.init_scan import (
    Category,
    Finding,
    ScanResult,
    append_user_pattern,
    scan_project,
)
from cortex.init_seeders import seed_doctrine, seed_plan, seed_plans
from cortex.shell import git_remediation_cmd, run_git

CURRENT_SPEC_VERSION = "0.4.0-dev"

SCAFFOLD_SUBDIRS = ("doctrine", "plans", "journal", "procedures")


def _package_data_root() -> Path:
    """Resolve the filesystem path to the cortex._data directory.

    Uses `importlib.resources.files(...)` which works for installed wheels
    and for editable installs (uv sync). Callers should treat the returned
    path as read-only.
    """
    root = resources.files("cortex._data")
    # `files()` returns a MultiplexedPath or Traversable; for our shipped
    # data (real filesystem, not inside a zip) `Path(str(root))` is safe.
    return Path(str(root))


def _now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


_STUB_BODIES: dict[str, str] = {
    "state": (
        "> **Hand-authored placeholder.** `cortex init` wrote this as a scaffolded "
        "starting point. Edit it freely — describe the current priorities, open "
        "questions, and load-bearing context you want agents to load at session "
        "start. When the deterministic `cortex refresh-state` command ships in "
        "v0.4.0 (per the 2026-04-24 production-release rerank), it will "
        "regenerate this layer from the journal and plans automatically; until "
        "then, hand-editing is the intended workflow."
    ),
    "map": (
        "> **Hand-authored placeholder.** `cortex init` wrote this as a scaffolded "
        "starting point. Edit it to describe the structural view of your "
        "codebase (key modules, entry points, data flows). The LLM-driven "
        "`cortex refresh-map` is **deferred from the v1.0 path to v1.x** per "
        "the 2026-04-24 production-release rerank — solo authors already know "
        "their map; the synthesis is a contributor-onboarding feature. Until "
        "`refresh-map` ships, hand-editing is the intended workflow."
    ),
}


def _derived_stub(
    title: str,
    layer: str,
    generator: str,
    *,
    sources: list[str] | None = None,
) -> str:
    """Render a seven-field derived-layer stub (map or state).

    The seven-field frontmatter is load-bearing — `cortex doctor` validates it
    (SPEC § 4.5). Only the prose body is user-facing guidance, and it's phrased
    for the hand-editing workflow that's expected until `cortex refresh-{layer}`
    ships. Per the 2026-04-24 production-release rerank: deterministic
    `cortex refresh-state` ships in **v0.4.0**; LLM `cortex refresh-map` is
    **deferred from the v1.0 path to v1.x**.

    ``sources`` is a list of relative paths the scan classified as ``map_ref``
    or ``reference`` and that the State layer should cite. When non-empty,
    ``Corpus:`` reflects the source count so the placeholder doesn't lie
    about how much context exists. Empty/None falls back to today's
    "no synthesis yet" wording.
    """
    now = _now_iso()
    body = _STUB_BODIES[layer]
    # Per the 2026-04-24 production-release rerank: deterministic
    # `cortex refresh-state` ships in v0.4.0; LLM `cortex refresh-map`
    # is deferred from v1.0 to v1.x. Each stub names its own arrival
    # framing so users reading the scaffolded file don't get stale
    # guidance from a cortex version that was generated before the
    # rerank landed.
    refresh_phase = "v0.4.0" if layer == "state" else "v1.x (deferred from v1.0 path)"
    if sources:
        sources_yaml = "\n".join(f"  - {src}" for src in sources)
        corpus_line = f"Corpus: {len(sources)} files (scan-discovered, not synthesized)"
        incomplete_line = (
            f"Incomplete:\n"
            f"  - All sources — listed by `cortex init` from a project scan; "
            f"`cortex refresh-{layer}` will synthesize them in {refresh_phase}."
        )
    else:
        sources_yaml = "  - (none — scaffolded placeholder, no synthesis yet)"
        corpus_line = "Corpus: 0 files (no synthesis yet)"
        incomplete_line = (
            f"Incomplete:\n"
            f"  - All sources — scaffolded at project init; "
            f"`cortex refresh-{layer}` will regenerate from primary sources in {refresh_phase}."
        )
    return f"""---
Generated: {now}
Generator: {generator} (scaffolded by `cortex init`; hand-editable until `cortex refresh-{layer}` ships in {refresh_phase})
Sources:
{sources_yaml}
{corpus_line}
Omitted: []
{incomplete_line}
Conflicts-preserved: []
Spec: {CURRENT_SPEC_VERSION.split("-")[0]}
---

# {title}

{body}
"""


def _copy_tree(src: Path, dst: Path, *, overwrite: bool) -> list[Path]:
    """Copy every file under `src` into `dst`, preserving relative structure.

    Skips `__init__.py` files that are part of the _data package machinery.
    Returns the list of destination paths written.
    """
    written: list[Path] = []
    for entry in src.rglob("*"):
        if entry.is_dir():
            continue
        if entry.name == "__init__.py":
            continue
        rel = entry.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not overwrite:
            # Caller is responsible for checking SPEC_VERSION *before* calling.
            # If a file exists here despite SPEC_VERSION absence, treat it as
            # user content and leave it alone.
            continue
        shutil.copyfile(entry, target)
        written.append(target)
    return written


def _ensure_subdir(path: Path) -> None:
    """Create `path` if missing; drop a `.gitkeep` so git tracks empty dirs."""
    path.mkdir(parents=True, exist_ok=True)
    gitkeep = path / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()


# Import block the interactive wizard appends to CLAUDE.md / AGENTS.md. The
# exact literal strings below are also how the idempotency check works:
# if `@.cortex/protocol.md` already appears in the target file we skip the
# append entirely and print a note. This mirrors how doctrine-0002 defines
# the user contract ("running the wizard twice must be a no-op").
_CORTEX_IMPORT_BLOCK = """\
## Current state (read this first)

@.cortex/state.md

## Cortex Protocol

@.cortex/protocol.md
"""

_PROTOCOL_IMPORT_MARKER = "@.cortex/protocol.md"
_STATE_IMPORT_MARKER = "@.cortex/state.md"

_GITIGNORE_ENTRIES: tuple[str, ...] = (
    # Auto-maintained Cortex index; transient per SPEC § 2 — never committed.
    ".cortex/.index.json",
    # Placeholder for the Phase E write path; present pre-emptively so the
    # directory never leaks into commits once consumers start dropping files.
    ".cortex/pending/",
)

# `--local-only` inverts the default. Instead of committing `.cortex/` as
# team-shared memory (the SPEC default), the whole directory stays on this
# machine. Used by solo developers who don't want journals/plans/state
# published alongside their code when they share the project.
_GITIGNORE_LOCAL_ONLY_ENTRY: str = ".cortex/"


def _append_imports(target_file: Path) -> bool:
    """Append the Cortex import block at the end of `target_file`.

    Returns True when the file was modified, False when the imports were
    already present (idempotent path). The append is **always at end-of-file**
    with exactly one blank line separating prior content from the new block.
    Existing content is never rewritten or reordered.

    Why end-of-file rather than after the last ``@<path>`` import: the
    "cluster with existing imports" heuristic broke document outlines on
    real projects (touchstone dogfood 2026-04-24 — `### Never commit on
    main` was reparented under `## Cortex Protocol` because the inserted
    `## ` heading landed immediately after `@principles/git-workflow.md`
    and before the existing `### ` sub-heading). End-of-file append never
    reparents existing sub-headings; the cost is that imports may sit
    physically apart from existing `@<path>` imports — acceptable since
    Markdown loads the whole file regardless of order.
    """
    text = target_file.read_text()
    if _PROTOCOL_IMPORT_MARKER in text and _STATE_IMPORT_MARKER in text:
        # Both imports already present — no-op. Do not duplicate.
        return False

    # Always append at end of file with a blank-line guard (exactly one
    # blank line separating prior content from the block).
    if text.endswith("\n\n"):
        new_text = text + _CORTEX_IMPORT_BLOCK
    elif text.endswith("\n"):
        new_text = text + "\n" + _CORTEX_IMPORT_BLOCK
    elif text == "":
        new_text = _CORTEX_IMPORT_BLOCK
    else:
        new_text = text + "\n\n" + _CORTEX_IMPORT_BLOCK

    target_file.write_text(new_text)
    return True


def _has_existing_cortex_imports(path: Path) -> bool:
    """Return True iff `path` already imports `@.cortex/protocol.md` or
    `@.cortex/state.md`.

    Used by `--local-only` to catch the case where a project previously ran
    `cortex init` in team-shared mode (committing the imports) and is now
    converting to local-only. Leaving those imports in CLAUDE.md / AGENTS.md
    while `.cortex/` is gitignored produces dangling references for
    downstream clones.
    """
    if not path.exists():
        return False
    text = path.read_text()
    return _PROTOCOL_IMPORT_MARKER in text or _STATE_IMPORT_MARKER in text


def _tracked_cortex_files(project_root: Path) -> list[str] | None:
    """Return the list of `.cortex/` files already tracked by git.

    Thin adapter over :func:`cortex.shell.run_git` that maps git's
    tri-state result to the per-file list the ``--local-only`` caller
    wants:

      * `[]`  — check ran cleanly and nothing is tracked OR the project is
                not a git repository (no tracked state is possible).
      * `[...]` — one entry per tracked `.cortex/` path.
      * `None` — the check could not be completed (git missing, subprocess
                error, unexpected nonzero exit for any reason *other than*
                "not a repo"). Callers MUST treat None as "unknown, warn
                the user" — silently treating a failed check as success
                would give false "not published" assurance.

    The tri-state comes from :class:`cortex.shell.GitRun`; this function
    exists only to shape it into the list form the caller already expects.
    """
    result = run_git("-C", str(project_root), "ls-files", ".cortex")
    if result.ok:
        return [line for line in result.stdout.splitlines() if line.strip()]
    if result.not_a_repo:
        return []
    return None


def _append_gitignore_entries(gitignore: Path, *, local_only: bool = False) -> bool:
    """Append Cortex-specific entries to `.gitignore` if missing.

    Idempotent: each entry is only appended when not already present as its
    own line. Returns True when at least one entry was appended.

    When `local_only` is True, appends `.cortex/` (the whole directory) so
    the project's Cortex memory stays on this machine. Otherwise appends the
    transient-paths set only, keeping `.cortex/` as committed team-shared
    memory per the SPEC default.
    """
    existing_lines: set[str] = set()
    prior_text = ""
    if gitignore.exists():
        prior_text = gitignore.read_text()
        existing_lines = {line.strip() for line in prior_text.splitlines()}

    entries = (_GITIGNORE_LOCAL_ONLY_ENTRY,) if local_only else _GITIGNORE_ENTRIES
    to_add = [entry for entry in entries if entry not in existing_lines]
    if not to_add:
        return False

    # Ensure a trailing newline before the appended block so entries land on
    # their own lines even if the user's .gitignore didn't end with one.
    if prior_text and not prior_text.endswith("\n"):
        prior_text = prior_text + "\n"

    new_text = prior_text + "\n".join(to_add) + "\n"
    gitignore.write_text(new_text)
    return True


def _absorb_doctrine(
    project_root: Path,
    scan: ScanResult,
    *,
    will_prompt: bool,
    assume_yes: bool,
) -> list[Path]:
    """Walk Doctrine candidates with per-file Y/n prompts; mint accepted entries.

    The selection step is interactive (one prompt per source) so the user
    can opt into absorbing the principles they want to track in Cortex's
    promotion queue without taking the ones that aren't load-bearing for
    agents (e.g. a ``principles/README.md`` that's just orientation prose).

    On non-TTY without ``--yes`` no candidates are imported (preserves
    today's silent-scaffold behavior). With ``--yes``, every candidate is
    accepted — same default as the prompts (default Yes).
    """
    candidates = scan.by_category("doctrine")
    if not candidates:
        return []
    accepted: list[Path] = []
    if assume_yes:
        accepted = [c.path for c in candidates]
    elif will_prompt:
        for finding in candidates:
            if click.confirm(f"  Import {finding.relative} as Doctrine?", default=True):
                accepted.append(finding.path)
    else:
        # Non-TTY without --yes: silent skip per Doctrine 0002.
        return []

    if not accepted:
        return []
    written = seed_doctrine(project_root, accepted)
    for path in written:
        click.echo(f"  Imported Doctrine: {path.relative_to(project_root).as_posix()}")
    return written


_UNKNOWN_CHOICES: dict[str, Category | None] = {
    "d": "doctrine",
    "p": "plan",
    "m": "map_ref",
    "r": "reference",
    "s": None,  # skip — surface choice but mint nothing
}
_UNKNOWN_CHOICE_LABELS = {
    "d": "Doctrine",
    "p": "Plan",
    "m": "Map reference",
    "r": "Reference",
    "s": "Skip",
}


def _describe_unknown(finding: Finding) -> str:
    """Return a short single-line size/structure description for an unknown."""
    try:
        size = finding.path.stat().st_size
    except OSError:
        size = 0
    size_kb = size / 1024.0
    extra = ""
    try:
        text = finding.path.read_text(encoding="utf-8", errors="replace")
        h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        h2_count = len(re.findall(r"^##\s+\S", text, re.MULTILINE))
        if h1 is not None:
            title_excerpt = h1.group(1).strip()[:60]
            extra = f', H1 "{title_excerpt}"'
            if h2_count:
                extra += f", {h2_count} H2 sections"
    except OSError:
        pass
    return f"This file is {size_kb:.1f}KB{extra}."


def _classify_unknown(finding: Finding) -> str | None:
    """Prompt the user to classify one unknown finding. Return the choice key (or None to abort)."""
    click.echo("")
    click.echo(f"Found {finding.relative} — doesn't match a known pattern.")
    click.echo("")
    click.echo(f"  {_describe_unknown(finding)}")
    click.echo("  Treat as: [D]octrine / [P]lan / [M]ap reference / [R]eference / [S]kip?")
    click.echo("  Default: M (map reference)")
    raw = click.prompt("  Choice", default="m", show_default=False)
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()[:1] or "m"
    if key not in _UNKNOWN_CHOICES:
        click.echo("  (unrecognized — defaulting to map reference)")
        key = "m"
    return key


def _absorb_unknowns(
    project_root: Path,
    scan: ScanResult,
    *,
    will_prompt: bool,
    assume_yes: bool,
) -> None:
    """Walk unknown candidates, prompting the user to classify each.

    On TTY, each unknown is classified into one of the five categories
    (Doctrine / Plan / Map ref / Reference / Skip) and the choice is
    persisted to ``.cortex/.discover.toml`` so future invocations
    recognize the pattern without re-prompting. With ``--yes`` the
    default classification is ``map_ref`` (the safest choice — surfaces
    the file in state.md Sources without seeding into Doctrine/Plans).
    Non-TTY without ``--yes`` skips entirely.
    """
    candidates = scan.by_category("unknown")
    if not candidates:
        return
    if not will_prompt and not assume_yes:
        return
    for finding in candidates:
        choice_key = "m" if assume_yes else (_classify_unknown(finding) or "s")
        category = _UNKNOWN_CHOICES[choice_key]
        # Persist the teaching for future runs.
        if category is not None:
            append_user_pattern(
                project_root,
                glob=finding.relative,
                category=category,
                description=f"(taught by user during cortex init on {date.today().isoformat()})",
            )
        else:
            append_user_pattern(
                project_root,
                glob=finding.relative,
                category="skip",
                description=f"(skipped by user during cortex init on {date.today().isoformat()})",
            )
        # Mint immediately if the user chose Doctrine or Plan.
        if category == "doctrine":
            written = seed_doctrine(project_root, [finding.path])
            for path in written:
                click.echo(f"  Imported Doctrine: {path.relative_to(project_root).as_posix()}")
        elif category == "plan":
            result = seed_plan(project_root, finding.path)
            if result is not None:
                click.echo(f"  Imported Plan: {result.relative_to(project_root).as_posix()}")
        elif category == "map_ref":
            click.echo(f"  Noted as map reference: {finding.relative}")
        elif category == "reference":
            click.echo(f"  Noted as reference-only: {finding.relative}")
        else:
            click.echo(f"  Skipped: {finding.relative}")


def _absorb_plans(
    project_root: Path,
    scan: ScanResult,
    *,
    will_prompt: bool,
    assume_yes: bool,
) -> list[Path]:
    """Walk Plan candidates with per-file Y/n prompts; mint accepted entries.

    Each accepted source becomes ``.cortex/plans/<slug>.md`` with required
    sections stubbed as ``[ ] Hand-author from <source>`` checklists. The
    Goal-hash is computed from the source's H1 (or filename when there's
    no H1) so ``cortex doctor``'s recompute check passes immediately.
    """
    candidates = scan.by_category("plan")
    if not candidates:
        return []
    accepted: list[Path] = []
    if assume_yes:
        accepted = [c.path for c in candidates]
    elif will_prompt:
        for finding in candidates:
            if click.confirm(f"  Import {finding.relative} as Plan?", default=True):
                accepted.append(finding.path)
    else:
        return []

    if not accepted:
        return []
    written = seed_plans(project_root, accepted)
    for path in written:
        click.echo(f"  Imported Plan: {path.relative_to(project_root).as_posix()}")
    return written


def _print_scan_summary(scan: ScanResult) -> None:
    """Render the one-screen scan summary before any prompts fire.

    The block is grouped by category in a fixed order so users developing
    a mental model of "what cortex finds" see consistent layout regardless
    of the project shape. Each section is suppressed when empty so projects
    with no Doctrine candidates don't render a meaningless heading.
    """
    click.echo("")
    click.echo(f"Scanning {scan.project_root}…")
    click.echo("")
    click.echo("Found existing structure:")
    click.echo("")

    doctrine = scan.by_category("doctrine")
    if doctrine:
        click.echo("  Doctrine candidates (Y/n on each):")
        for f in doctrine:
            click.echo(f"    {f.relative}")
        click.echo("")

    touchstone_managed = scan.by_category("touchstone_managed")
    if touchstone_managed:
        click.echo(
            "  Detected Touchstone-managed; skipped from Doctrine import "
            "(already imported via @path in CLAUDE.md/AGENTS.md):"
        )
        for f in touchstone_managed:
            click.echo(f"    {f.relative}")
        click.echo("")

    meta_docs = scan.by_category("meta_doc")
    if meta_docs:
        click.echo(
            "  Skipped (meta-doc filename — README files are orientation "
            "prose, not doctrine; override via .cortex/.discover.toml):"
        )
        for f in meta_docs:
            click.echo(f"    {f.relative}")
        click.echo("")

    plans = scan.by_category("plan")
    if plans:
        click.echo("  Plan candidates (Y/n on each, Success-Criteria stubbed as TODO):")
        for f in plans:
            click.echo(f"    {f.relative}")
        click.echo("")

    map_refs = scan.by_category("map_ref")
    if map_refs:
        click.echo("  Map references (added to state.md Sources, not imported):")
        for f in map_refs:
            click.echo(f"    {f.relative}")
        click.echo("")

    references = scan.by_category("reference")
    if references:
        click.echo("  Reference-only (noted in state.md, NOT imported into Journal):")
        for f in references:
            note = "  (looks shipped — demoted from Plan)" if f.is_demoted_plan else ""
            click.echo(f"    {f.relative}{note}")
        click.echo("")

    unknown = scan.by_category("unknown")
    if unknown:
        click.echo("  Unknown pattern (will prompt for classification):")
        for f in unknown:
            click.echo(f"    {f.relative}")
        click.echo("")

    sig = scan.sibling_signals
    touchstone_bits: list[str] = []
    if sig.has_codex_review_toml:
        touchstone_bits.append("✓ .codex-review.toml")
    if sig.has_codex_review_hook:
        touchstone_bits.append("✓ codex-review pre-commit hook")
    if sig.has_touchstone_config:
        touchstone_bits.append("✓ .touchstone-config")
    if sig.has_touchstone_manifest:
        touchstone_bits.append("✓ .touchstone-manifest")
    if sig.touchstone_version:
        touchstone_bits.append(f"✓ .touchstone-version {sig.touchstone_version}")
    if touchstone_bits:
        click.echo("  Touchstone signals: " + " ".join(touchstone_bits))

    if sig.sentinel_dir_present:
        if sig.sentinel_runs_count > 0:
            click.echo(
                f"  Sentinel signal: ✓ .sentinel/ exists with {sig.sentinel_runs_count} runs, "
                "no T1.6 entries (forward-only — past runs not backfilled)"
            )
        else:
            click.echo("  Sentinel signal: ✓ .sentinel/ exists, no runs detected")

    if scan.unscoped_constraint_count:
        location = scan.unscoped_constraint_first_location
        if location:
            click.echo(
                f"  CLAUDE.md/AGENTS.md unscoped constraints: {scan.unscoped_constraint_count} "
                f"({location}{' — first of multiple' if scan.unscoped_constraint_count > 1 else ''}; "
                "run `cortex doctor` for full detail)"
            )
        else:  # pragma: no cover — defensive; count > 0 implies a location
            click.echo(
                f"  CLAUDE.md/AGENTS.md unscoped constraints: {scan.unscoped_constraint_count} "
                "(run `cortex doctor` for per-line detail)"
            )

    click.echo("")


def _should_prompt(yes: bool) -> bool:
    """Return True iff we should run interactive prompts.

    The rule (doctrine 0002): prompt only on a TTY. Non-TTY invocations
    without `--yes` skip all interactive follow-ups entirely — the pre-
    interactive scaffolding behavior is preserved for CI, hooks, and piped
    stdin. `--yes` means "accept defaults without prompting" regardless of
    TTY state, and is the only way to opt into the follow-ups non-interactively.
    """
    if yes:
        return False
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):  # pragma: no cover - defensive
        # sys.stdin closed or unavailable — treat as non-TTY.
        return False


def _resolve_flag(
    *,
    flag_value: bool | None,
    yes: bool,
    prompt_text: str,
    target_exists: bool,
) -> bool:
    """Resolve whether an interactive step should execute.

    Precedence (doctrine 0002 § 3):
      1. Explicit flag (`--foo` or `--no-foo`) always wins.
      2. `--yes` accepts the default (True).
      3. TTY → prompt with default=True.
      4. Non-TTY without `--yes` → skip silently (return False).

    `target_exists` lets the caller gate a step on a file existing first — if
    the target doesn't exist, we never prompt about it.
    """
    if not target_exists:
        return False
    if flag_value is not None:
        return flag_value
    if yes:
        return True
    if _should_prompt(yes=yes):
        return click.confirm(prompt_text, default=True)
    # Non-TTY, no flag, no --yes → preserve silent-scaffold behavior.
    return False


def _format_equivalent_command(
    *,
    did_claude: bool,
    did_agents: bool,
    did_gitignore: bool,
    did_local_only: bool,
    force: bool,
    path_arg: str | None,
) -> str:
    """Return the single-line flag-form command that reproduces this run.

    Teach-by-doing (doctrine 0002 § 5): after every successful wizard, print
    the command that would produce the same result non-interactively.
    """
    parts = ["cortex init"]
    if path_arg is not None:
        # Shell-quote the path so rerun commands for targets containing
        # spaces or metacharacters (e.g. `/tmp/My Project`) survive
        # copy-paste into a terminal without re-tokenization.
        parts.append(f"--path {shlex.quote(path_arg)}")
    parts.append("--add-imports-claude" if did_claude else "--no-add-imports-claude")
    parts.append("--add-imports-agents" if did_agents else "--no-add-imports-agents")
    if did_local_only:
        # `--local-only` implies the gitignore step; omit `--gitignore` to keep
        # the reproduction command minimal.
        parts.append("--local-only")
    else:
        parts.append("--gitignore" if did_gitignore else "--no-gitignore")
    parts.append("--yes")
    if force:
        parts.append("--force")
    return " ".join(parts)


@click.command("init")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite the scaffold files even if `.cortex/SPEC_VERSION` already exists. "
    "Doctrine, Plan, Journal, and Procedure contents are never deleted.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root where `.cortex/` will be created.",
)
@click.option(
    "--add-imports-claude/--no-add-imports-claude",
    "add_imports_claude",
    default=None,
    help="Append `@.cortex/protocol.md` + `@.cortex/state.md` imports to CLAUDE.md. "
    "On a TTY without this flag, `cortex init` prompts (default Yes). "
    "Non-TTY without `--yes` skips. Idempotent when imports are already present.",
)
@click.option(
    "--add-imports-agents/--no-add-imports-agents",
    "add_imports_agents",
    default=None,
    help="Same as --add-imports-claude, but for AGENTS.md.",
)
@click.option(
    "--gitignore/--no-gitignore",
    "add_gitignore",
    default=None,
    help="Append `.cortex/.index.json` and `.cortex/pending/` to the project `.gitignore`. "
    "Idempotent; existing entries are never duplicated. On a TTY without this flag, "
    "`cortex init` prompts (default Yes).",
)
@click.option(
    "--local-only",
    "local_only",
    is_flag=True,
    default=False,
    help="Keep `.cortex/` on this machine — append `.cortex/` (the whole directory) "
    "to `.gitignore` so journals, plans, doctrine, and state stay unpublished when "
    "the project is shared. The SPEC default treats `.cortex/` as committed "
    "team-shared memory; this flag inverts that for solo / private use. "
    "Conflicts with `--no-gitignore`.",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    default=False,
    help="Accept all interactive defaults without prompting (doctrine 0002 § 4). "
    "Equivalent to running the wizard and pressing Enter at every step.",
)
def init_command(
    *,
    force: bool,
    target_path: Path,
    add_imports_claude: bool | None,
    add_imports_agents: bool | None,
    add_gitignore: bool | None,
    local_only: bool,
    assume_yes: bool,
) -> None:
    """Scaffold a SPEC-v0.3.1-dev-conformant `.cortex/` directory in the target project."""
    # `--local-only` and `--no-gitignore` are mutually exclusive: the former
    # says "gitignore all of .cortex/", the latter says "don't touch
    # .gitignore at all". Silently preferring one over the other would
    # produce a confusing outcome; fail loudly instead.
    if local_only and add_gitignore is False:
        click.echo(
            "error: --local-only and --no-gitignore conflict. "
            "--local-only requires writing `.cortex/` to .gitignore.",
            err=True,
        )
        sys.exit(2)

    # Capture whether the target differs from cwd so the printed equivalent
    # command at the end reproduces the invocation faithfully.
    path_differs_from_cwd = Path(target_path).resolve() != Path.cwd().resolve()
    target_path = Path(target_path).resolve()
    if not target_path.exists():
        click.echo(f"error: target path does not exist: {target_path}", err=True)
        sys.exit(2)

    cortex_dir = target_path / ".cortex"
    spec_version_file = cortex_dir / "SPEC_VERSION"

    cortex_has_any_content = cortex_dir.exists() and any(cortex_dir.iterdir())

    if spec_version_file.exists() and not force:
        existing = spec_version_file.read_text().strip()
        click.echo(
            f"error: `.cortex/SPEC_VERSION` already exists ({existing}) at {cortex_dir}. "
            "Use `--force` to rewrite the scaffold; existing doctrine/plan/journal/"
            "procedure content is preserved either way.",
            err=True,
        )
        sys.exit(1)

    if cortex_has_any_content and not spec_version_file.exists() and not force:
        # `.cortex/` exists with files but no SPEC_VERSION — ambiguous state.
        # Writing a fresh scaffold on top would leave a mix of shipped files and
        # pre-existing content under a "conformant" SPEC_VERSION marker. Refuse.
        click.echo(
            f"error: {cortex_dir} already contains content but has no `SPEC_VERSION` marker. "
            "This looks like an incomplete or hand-authored Cortex directory. "
            "Use `--force` to write the scaffold over any scaffold-level files "
            "(SPEC_VERSION, protocol.md, templates/, map.md, state.md); "
            "doctrine/plan/journal/procedure content is preserved either way.",
            err=True,
        )
        sys.exit(1)

    # --- Scan-first: walk the project for existing structure before any prompts.
    # Per the scan-and-absorb design, the scan summary is the first thing the
    # user sees so they understand what cortex found before deciding to continue.
    # On non-TTY without `--yes` we still print the summary (it's information,
    # not interaction) but skip the "Continue?" prompt and downstream imports.
    scan = scan_project(target_path)
    _print_scan_summary(scan)
    will_prompt = _should_prompt(yes=assume_yes)
    if will_prompt and not click.confirm("Continue?", default=True):
        click.echo("Aborted by user — no changes made.")
        sys.exit(0)

    data_root = _package_data_root()

    cortex_dir.mkdir(exist_ok=True)

    # When we get here, either the directory is fresh/empty, or --force is set.
    # In both cases the scaffold-level files are written/overwritten so the
    # advertised "spec v{CURRENT_SPEC_VERSION} conformant" marker is truthful.
    # Non-scaffold files (doctrine/, plans/, journal/, procedures/ contents) are
    # never touched because we never write into those subdirs except .gitkeep.

    # 1. SPEC_VERSION
    spec_version_file.write_text(CURRENT_SPEC_VERSION + "\n")

    # 2. protocol.md
    protocol_src = data_root / "protocol.md"
    protocol_dst = cortex_dir / "protocol.md"
    shutil.copyfile(protocol_src, protocol_dst)

    # 3. templates/ tree (overwrite scaffold template files; we're past the guard)
    templates_src = data_root / "templates"
    templates_dst = cortex_dir / "templates"
    _copy_tree(templates_src, templates_dst, overwrite=True)

    # 3b. Human-facing README at the top of `.cortex/`. The canonical source
    # lives at `templates/README.md` in the bundled tree so the sync test keeps
    # downstream projects in lockstep with this repo's own orientation doc.
    readme_src = data_root / "templates" / "README.md"
    readme_dst = cortex_dir / "README.md"
    shutil.copyfile(readme_src, readme_dst)

    # 4. subdirectories with .gitkeep (.gitkeep is scaffold; empty dirs stay empty)
    for sub in SCAFFOLD_SUBDIRS:
        _ensure_subdir(cortex_dir / sub)

    # 5. map.md and state.md stubs (scaffold files; overwrite). Generator
    # string is derived from `cortex.__version__` so the stubs' seven-field
    # metadata stays truthful across releases (no hand-bump to remember).
    init_generator = f"cortex init v{CORTEX_VERSION}"
    # State layer's Sources field is enriched with anything the scan
    # classified as map_ref (READMEs/ARCHITECTURE/SETUP), reference
    # (CHANGELOGs, journal/*), or doctrine (principles/*.md, decisions/*.md
    # — the files about to be absorbed as Doctrine entries) so a fresh
    # state.md cites the full set of documentation that informed any layer.
    # Fix #5 from plans/init-ux-fixes-from-touchstone — v0.2.3 omitted
    # the doctrine sources, leaving Sources internally inconsistent with
    # the .cortex/doctrine/ entries init created in the same invocation.
    # touchstone_managed and meta_doc are intentionally excluded — those
    # categories represent files Cortex saw but deliberately did NOT cite
    # (Touchstone owns them via @path / they're orientation prose).
    # Map layer stays scaffold-empty; structural inputs are code+git, not
    # markdown sources, so the scan can't enrich it usefully — that's
    # `cortex refresh-map`'s job in v1.x.
    state_sources = [
        f.relative
        for f in scan.findings
        if f.category in ("map_ref", "reference", "doctrine")
    ]
    for layer, title in (
        ("map", "Project Map"),
        ("state", "Project State"),
    ):
        layer_sources = state_sources if layer == "state" else None
        (cortex_dir / f"{layer}.md").write_text(
            _derived_stub(title, layer, init_generator, sources=layer_sources)
        )

    click.echo(f"Scaffolded {cortex_dir} (spec v{CURRENT_SPEC_VERSION}).")

    # Absorb existing structure surfaced by the scan into ``.cortex/``.
    # Each candidate gets a per-file Y/n prompt on TTY; --yes accepts all;
    # non-TTY without --yes skips imports entirely (silent-scaffold preserved).
    _absorb_doctrine(target_path, scan, will_prompt=will_prompt, assume_yes=assume_yes)
    _absorb_plans(target_path, scan, will_prompt=will_prompt, assume_yes=assume_yes)
    _absorb_unknowns(target_path, scan, will_prompt=will_prompt, assume_yes=assume_yes)

    # Interactive follow-ups (doctrine 0002). Each step is gated on the
    # relevant target file existing — if CLAUDE.md / AGENTS.md / .gitignore
    # doesn't exist we don't prompt about it. Flags override prompts; `--yes`
    # accepts defaults; non-TTY without `--yes` skips silently.
    claude_md = target_path / "CLAUDE.md"
    agents_md = target_path / "AGENTS.md"
    gitignore_path = target_path / ".gitignore"

    # Resolve what each step will do. We record the effective decision for
    # each step so the printed equivalent-command reflects reality even
    # when a step was a no-op because imports were already present.
    # `--local-only` inverts the import default. Reason: if `.cortex/` is
    # gitignored, committing `@.cortex/protocol.md` imports into CLAUDE.md /
    # AGENTS.md leaves dangling references for anyone who clones the
    # published repo — the imports resolve locally for the author but not
    # for downstream consumers. Explicit `--add-imports-claude` /
    # `--add-imports-agents` still wins (the user is opting in knowingly),
    # but we print a warning so the tradeoff is visible.
    if local_only and add_imports_claude is None:
        add_imports_claude = False
    if local_only and add_imports_agents is None:
        add_imports_agents = False
    if local_only and (add_imports_claude is True or add_imports_agents is True):
        click.echo(
            "  warning: --local-only with explicit --add-imports-* leaves "
            "@.cortex/... imports in CLAUDE.md / AGENTS.md while `.cortex/` "
            "is gitignored. Downstream clones will see dangling imports.",
            err=True,
        )

    want_claude = _resolve_flag(
        flag_value=add_imports_claude,
        yes=assume_yes,
        prompt_text=f"Add @.cortex/protocol.md and @.cortex/state.md imports to {claude_md.name}?",
        target_exists=claude_md.exists(),
    )
    want_agents = _resolve_flag(
        flag_value=add_imports_agents,
        yes=assume_yes,
        prompt_text=f"Add @.cortex/protocol.md and @.cortex/state.md imports to {agents_md.name}?",
        target_exists=agents_md.exists(),
    )
    # .gitignore gets prompted even if the file doesn't exist yet — we'll
    # create it. Most projects already have one; pass True unconditionally.
    # `--local-only` short-circuits the prompt: the flag itself is the
    # affirmative answer to a more specific question.
    if local_only:
        want_gitignore = True
    else:
        want_gitignore = _resolve_flag(
            flag_value=add_gitignore,
            yes=assume_yes,
            prompt_text="Add .cortex/ entries to project .gitignore?",
            target_exists=True,
        )

    if want_claude and claude_md.exists():
        if _append_imports(claude_md):
            click.echo(f"  Appended Cortex imports to {claude_md.name}.")
        else:
            click.echo(f"  {claude_md.name} already imports Cortex protocol.")
    if want_agents and agents_md.exists():
        if _append_imports(agents_md):
            click.echo(f"  Appended Cortex imports to {agents_md.name}.")
        else:
            click.echo(f"  {agents_md.name} already imports Cortex protocol.")
    if want_gitignore:
        if _append_gitignore_entries(gitignore_path, local_only=local_only):
            if local_only:
                click.echo(
                    f"  Updated {gitignore_path.name}: `.cortex/` is now gitignored "
                    "(local-only mode)."
                )
            else:
                click.echo(f"  Updated {gitignore_path.name} with Cortex entries.")
        else:
            if local_only:
                click.echo(
                    f"  {gitignore_path.name} already ignores `.cortex/` "
                    "(local-only mode)."
                )
            else:
                click.echo(f"  {gitignore_path.name} already ignores Cortex transient paths.")

    # Local-only post-check covers three ways the "not published" promise
    # can fail silently:
    #   1. `.cortex/` is already tracked by git — .gitignore does not untrack
    #      existing files, so the user needs `git rm --cached -r .cortex/`.
    #   2. CLAUDE.md / AGENTS.md already import `@.cortex/...` from a prior
    #      team-shared init — those imports get committed and will dangle for
    #      downstream clones once `.cortex/` is gitignored or untracked.
    # Each case warns with the specific remediation. The "will not be
    # published" success message is only printed when both checks are clean.
    if local_only:
        tracked = _tracked_cortex_files(target_path)
        dangling_import_files = [
            p for p in (claude_md, agents_md) if _has_existing_cortex_imports(p)
        ]
        # Both `tracked is None` and `tracked != []` branches emit git
        # commands for the user to paste into a shell. Every emission goes
        # through `cortex.shell.git_remediation_cmd` so the two invariants
        # the PR #27 review loop nailed down — anchor to the target project
        # (not cwd), shell-quote every interpolated token — are enforced in
        # one place rather than re-implemented at each call site.
        if tracked is None:
            # We couldn't determine whether `.cortex/` is tracked. Warn
            # explicitly rather than falling through to the "not published"
            # success message — false assurance is worse than no answer.
            lsfiles_cmd = git_remediation_cmd(
                "ls-files", ".cortex",
                target=target_path,
                anchor_to_target=path_differs_from_cwd,
            )
            untrack_cmd = git_remediation_cmd(
                "rm", "--cached", "-r", ".cortex/",
                target=target_path,
                anchor_to_target=path_differs_from_cwd,
            )
            click.echo(
                "  warning: could not verify whether `.cortex/` is already "
                "tracked by git (git check failed). If this project has a "
                f"prior Cortex history, run `{lsfiles_cmd}` manually "
                f"and follow up with `{untrack_cmd}` if files "
                "are listed. Otherwise downstream clones may still see the "
                "previously-committed `.cortex/` content.",
                err=True,
            )
        elif tracked:
            untrack_cmd = git_remediation_cmd(
                "rm", "--cached", "-r", ".cortex/",
                target=target_path,
                anchor_to_target=path_differs_from_cwd,
            )
            commit_cmd = git_remediation_cmd(
                "commit", "-m", "chore: untrack .cortex/ (local-only)",
                target=target_path,
                anchor_to_target=path_differs_from_cwd,
            )
            click.echo(
                f"  warning: {len(tracked)} `.cortex/` file(s) are already tracked by git. "
                ".gitignore does not untrack existing files. Run:\n"
                f"      {untrack_cmd}\n"
                f"      {commit_cmd}\n"
                "  to complete the local-only transition; otherwise the existing tracked "
                "files will continue to be published.",
                err=True,
            )
        if dangling_import_files:
            names = ", ".join(p.name for p in dangling_import_files)
            click.echo(
                f"  warning: {names} already import(s) `@.cortex/protocol.md` "
                "or `@.cortex/state.md`. Under --local-only these imports will "
                "resolve for you but dangle for downstream clones. Remove the "
                f"`@.cortex/...` lines from {names} to complete the local-only "
                "transition.",
                err=True,
            )
        # "Will not be published" is only truthful when the check succeeded
        # and returned no files AND no dangling imports remain.
        if tracked == [] and not dangling_import_files:
            click.echo(
                "  Doctrine, plans, journals, and state will not be published "
                "with this project."
            )

    # Build the Next-steps list contiguously (Fix #7 from
    # plans/init-ux-fixes-from-touchstone — v0.2.3 emitted "1." then "3."
    # with step 2 conditionally suppressed but the numbering not
    # renumbered, leaving a gap that confused dogfood users).
    next_steps: list[str] = [
        "Author doctrine/0001-why-<project>-exists.md (see templates/doctrine/candidate.md for shape).",
    ]
    if not want_claude and not want_agents and not local_only:
        # In local-only mode, committing `@.cortex/...` imports would leave
        # dangling references for downstream clones — don't recommend it.
        next_steps.append(
            "Import `@.cortex/protocol.md` and `@.cortex/state.md` into your AGENTS.md or CLAUDE.md."
        )
    next_steps.append("Run `cortex doctor` to validate the scaffold against SPEC.md.")

    click.echo("Next steps:")
    for idx, step in enumerate(next_steps, start=1):
        click.echo(f"  {idx}. {step}")

    # Teach-by-doing (doctrine 0002 § 5): print the exact flag-form command
    # that reproduces this invocation non-interactively. Scripters learn the
    # flags by seeing them after a hand-run wizard.
    equivalent = _format_equivalent_command(
        did_claude=want_claude,
        did_agents=want_agents,
        did_gitignore=want_gitignore,
        did_local_only=local_only,
        force=force,
        path_arg=str(target_path) if path_differs_from_cwd else None,
    )
    click.echo("")
    click.echo("==> Equivalent to rerun:")
    click.echo(f"    {equivalent}")
