"""`cortex fleet` — fleet-wide health and update across Cortex-enabled repos.

Answers the operator question "Are all my Cortex-enabled repos current?"
without N manual `cd repo && cortex doctor && cortex update --check` loops.

The command group has two subcommands:

- ``cortex fleet check`` — read-only. For every discovered repo that
  contains a ``.cortex/`` store, classify the install shape, run the
  ``cortex update --check`` freshness logic, the structural ``cortex
  doctor`` checks, and (optionally) ``--audit-instructions`` — all
  **in-process** by calling the same functions those commands call. No
  ``cortex`` subprocess is ever spawned (One code path: the freshness and
  doctor logic lives in :mod:`cortex.commands.sync` and
  :mod:`cortex.doctor_checks`/:mod:`cortex.validation`; fleet reuses it).

- ``cortex fleet update`` — write side. For repos with stale generated
  layers but **no structural doctor errors**, run the same
  :func:`cortex.commands.sync.run_sync` path each project's own ``cortex
  update`` runs. ``--dry-run`` writes nothing. ``--pr`` creates a
  per-repo scoped branch + commit + PR and never commits to
  ``main``/``master``. Structurally-invalid stores are skipped with their
  blocking doctor errors reported (No silent failures).

Discovery order (first non-empty wins, then deduplicated):

1. Explicit ``--path P`` (repeatable) and ``--paths-file F``.
2. ``~/.touchstone-projects`` if present (newline- or JSON-list of paths).
3. Sibling scan under configurable roots (default ``~/repos``).
4. The current working directory as a final fallback.

Stable JSON contract (``--json``) is a public boundary: the per-repo
record schema is defined deliberately in :func:`_repo_record_to_json` and
documented in this module's docstring and ``docs/fleet.md`` — it does not
dump internal dataclasses. Fields, per repo:

- ``path`` — absolute path to the repo root.
- ``repo`` — basename of the repo root (best-effort display name).
- ``spec_version`` — declared ``.cortex/SPEC_VERSION`` string, or null.
- ``install_shape`` — one of ``full``/``legacy``/``partial``/
  ``missing_spec_version``/``unsupported_spec``/``missing``.
- ``update_status`` — one of ``current``/``stale``/``unknown``.
- ``update_reasons`` — list of human-readable stale reasons (may be empty).
- ``doctor_errors`` — count of structural doctor ERROR issues.
- ``doctor_warnings`` — count of structural doctor WARNING issues.
- ``audit_warnings`` — count of ``--audit-instructions`` warnings, or null
  when the audit was not requested.
- ``classification`` — overall traffic-light: ``green``/``yellow``/
  ``red``/``skipped``.
- ``next_command`` — the single actionable command to run for this repo.
- ``error`` — non-null when the repo could not be classified at all; the
  repo still appears in output with the reason (No silent failures).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import click

from cortex import SUPPORTED_SPEC_VERSIONS
from cortex.validation import Severity

# Default roots scanned for sibling repos when no explicit paths and no
# ~/.touchstone-projects manifest are available. Derived from $HOME so the
# behavior is the same across machines; override with --root.
_DEFAULT_SCAN_ROOTS = ("~/repos",)

# Touchstone's project registry, if the user maintains one. Read-only and
# optional — absence is normal and never an error.
_TOUCHSTONE_PROJECTS_FILE = "~/.touchstone-projects"


# ---------------------------------------------------------------------------
# Per-repo classification record (internal). The JSON contract is rendered
# from this by _repo_record_to_json — callers never see the dataclass shape.
# ---------------------------------------------------------------------------


@dataclass
class RepoRecord:
    path: Path
    repo: str
    spec_version: str | None = None
    install_shape: str = "missing"
    update_status: str = "unknown"
    update_reasons: list[str] = field(default_factory=list)
    doctor_errors: int = 0
    doctor_warnings: int = 0
    audit_warnings: int | None = None
    classification: str = "red"
    next_command: str = ""
    error: str | None = None


# Install-shape constants — the deliberate vocabulary for the public JSON
# contract. Keeping them named (rather than bare strings scattered around)
# makes the boundary auditable.
SHAPE_FULL = "full"
SHAPE_LEGACY = "legacy"
SHAPE_PARTIAL = "partial"
SHAPE_MISSING_SPEC = "missing_spec_version"
SHAPE_UNSUPPORTED = "unsupported_spec"
SHAPE_MISSING = "missing"

CLASS_GREEN = "green"
CLASS_YELLOW = "yellow"
CLASS_RED = "red"
CLASS_SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _read_paths_file(paths_file: Path) -> list[Path]:
    """Read a paths file as either a JSON list of strings or newline-delimited paths.

    Degrades gracefully: a malformed file yields an empty list with a
    visible stderr warning rather than aborting the whole fleet run
    (No silent failures — the skip is reported).
    """

    try:
        text = paths_file.read_text()
    except OSError as exc:
        click.echo(f"warning: could not read paths file {paths_file}: {exc}", err=True)
        return []
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            click.echo(
                f"warning: {paths_file} looks like JSON but did not parse ({exc}); "
                "skipping it.",
                err=True,
            )
            return []
        if not isinstance(data, list):
            click.echo(
                f"warning: {paths_file} JSON is not a list; skipping it.",
                err=True,
            )
            return []
        return [Path(str(item)).expanduser() for item in data if str(item).strip()]
    return [
        Path(line.strip()).expanduser()
        for line in stripped.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _scan_root_for_cortex(root: Path) -> list[Path]:
    """Return immediate child directories of ``root`` that contain ``.cortex/``.

    Only one level deep — a sibling scan, not a recursive walk, so a large
    monorepo or a deep tree never explodes the scan cost.
    """

    if not root.is_dir():
        return []
    found: list[Path] = []
    try:
        children = sorted(root.iterdir())
    except OSError as exc:
        click.echo(f"warning: could not scan root {root}: {exc}", err=True)
        return []
    for child in children:
        if child.is_dir() and (child / ".cortex").is_dir():
            found.append(child)
    return found


def discover_repos(
    *,
    explicit_paths: tuple[Path, ...],
    paths_file: Path | None,
    scan_roots: tuple[str, ...],
    cwd: Path,
    touchstone_projects_file: Path | None = None,
) -> list[Path]:
    """Resolve the set of repos to inspect, in the documented precedence order.

    Returns a deduplicated, order-preserving list of resolved repo roots.
    The first source that yields any candidate short-circuits the cheaper
    fallbacks (explicit > touchstone-projects > sibling scan > cwd) so an
    operator who named paths never also picks up an unrelated ~/repos scan.
    """

    # Source 1: explicit paths and paths-file (combined; both are explicit).
    explicit: list[Path] = [p.expanduser() for p in explicit_paths]
    if paths_file is not None:
        explicit.extend(_read_paths_file(paths_file))
    if explicit:
        return _dedupe_resolved(explicit)

    # Source 2: ~/.touchstone-projects, if present.
    ts_file = touchstone_projects_file
    if ts_file is None:
        ts_file = Path(_TOUCHSTONE_PROJECTS_FILE).expanduser()
    if ts_file.is_file():
        from_ts = _read_paths_file(ts_file)
        if from_ts:
            return _dedupe_resolved(from_ts)

    # Source 3: sibling scan under configured roots.
    scanned: list[Path] = []
    for raw_root in scan_roots:
        scanned.extend(_scan_root_for_cortex(Path(raw_root).expanduser()))
    if scanned:
        return _dedupe_resolved(scanned)

    # Source 4: current repo as fallback.
    return _dedupe_resolved([cwd])


def _dedupe_resolved(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for p in paths:
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _read_spec_version(cortex_dir: Path) -> str | None:
    spec_file = cortex_dir / "SPEC_VERSION"
    if not spec_file.is_file():
        return None
    try:
        return spec_file.read_text().strip() or None
    except OSError:
        return None


def _major_minor(spec_version: str) -> str:
    return ".".join(spec_version.split("-", 1)[0].split(".")[:2])


def classify_install_shape(repo_root: Path) -> tuple[str, str | None]:
    """Return ``(install_shape, spec_version)`` for a repo root.

    - ``missing`` — no ``.cortex/`` directory at all.
    - ``missing_spec_version`` — ``.cortex/`` exists but no SPEC_VERSION.
    - ``unsupported_spec`` — SPEC_VERSION present but major.minor not in
      :data:`cortex.SUPPORTED_SPEC_VERSIONS`.
    - ``partial`` — supported SPEC_VERSION but missing protocol.md or a
      core subdir (an incomplete scaffold).
    - ``legacy`` — supported SPEC_VERSION + protocol.md, but the spec is an
      older supported major.minor than this CLI's literal (still readable).
    - ``full`` — supported, current, with protocol.md and core subdirs.
    """

    cortex_dir = repo_root / ".cortex"
    if not cortex_dir.is_dir():
        return SHAPE_MISSING, None

    spec_version = _read_spec_version(cortex_dir)
    if spec_version is None:
        return SHAPE_MISSING_SPEC, None

    major_minor = _major_minor(spec_version)
    if major_minor not in SUPPORTED_SPEC_VERSIONS:
        return SHAPE_UNSUPPORTED, spec_version

    protocol_present = (cortex_dir / "protocol.md").is_file()
    core_subdirs = ("doctrine", "plans", "journal")
    subdirs_present = all((cortex_dir / d).is_dir() for d in core_subdirs)
    if not protocol_present or not subdirs_present:
        return SHAPE_PARTIAL, spec_version

    # "legacy" = readable but not on the newest supported major.minor.
    newest_supported = SUPPORTED_SPEC_VERSIONS[-1]
    if major_minor != newest_supported:
        return SHAPE_LEGACY, spec_version
    return SHAPE_FULL, spec_version


def _run_doctor_in_process(repo_root: Path) -> tuple[int, int]:
    """Run the same structural checks `cortex doctor` runs, in-process.

    Reuses :func:`cortex.validation.run_all_checks` +
    :func:`cortex.doctor_checks.run_plain_checks` — the exact pair
    :func:`cortex.commands.sync._do_doctor` runs. No subprocess (One code
    path). Returns ``(errors, warnings)``.
    """

    from cortex.doctor_checks import run_plain_checks
    from cortex.validation import run_all_checks

    issues = run_all_checks(repo_root)
    issues.extend(run_plain_checks(repo_root))
    errors = sum(1 for i in issues if i.severity is Severity.ERROR)
    warnings = sum(1 for i in issues if i.severity is Severity.WARNING)
    return errors, warnings


def _freshness_in_process(repo_root: Path) -> tuple[str, list[str]]:
    """Run the `cortex update --check` freshness logic in-process.

    Reuses :func:`cortex.commands.sync._state_update_needed` and
    :func:`~cortex.commands.sync._index_update_needed` (the same functions
    the parent's #261 stale-input auto-sync uses). Returns
    ``(update_status, reasons)`` where status is ``current``/``stale``.
    """

    from cortex.commands.sync import _index_update_needed, _state_update_needed

    needs_state, state_reasons = _state_update_needed(repo_root)
    needs_index, index_reasons = _index_update_needed(repo_root)
    reasons = [*state_reasons, *index_reasons]
    status = "stale" if (needs_state or needs_index) else "current"
    return status, reasons


def _audit_warnings_in_process(repo_root: Path) -> int:
    """Count `--audit-instructions` warnings in-process. Reuses cortex.audit_instructions."""

    from cortex.audit_instructions import audit_instructions

    report = audit_instructions(repo_root)
    return len(report.warnings)


def classify_repo(repo_root: Path, *, audit: bool) -> RepoRecord:
    """Build the full per-repo classification record (read-only).

    Any repo that cannot be classified appears in the result with a
    non-null ``error`` and a ``red`` classification — never silently
    dropped (No silent failures).
    """

    record = RepoRecord(path=repo_root, repo=repo_root.name or str(repo_root))
    try:
        shape, spec_version = classify_install_shape(repo_root)
        record.install_shape = shape
        record.spec_version = spec_version

        if shape == SHAPE_MISSING:
            record.classification = CLASS_SKIPPED
            record.next_command = f"cortex init --path {repo_root}"
            return record

        if shape in (SHAPE_MISSING_SPEC, SHAPE_UNSUPPORTED):
            # Structurally not safe to refresh; surface doctor for detail.
            record.doctor_errors, record.doctor_warnings = _run_doctor_in_process(repo_root)
            record.classification = CLASS_RED
            record.next_command = f"cortex doctor --path {repo_root}"
            return record

        record.doctor_errors, record.doctor_warnings = _run_doctor_in_process(repo_root)
        record.update_status, record.update_reasons = _freshness_in_process(repo_root)
        if audit:
            record.audit_warnings = _audit_warnings_in_process(repo_root)

        record.classification, record.next_command = _classify_overall(record)
    except Exception as exc:  # noqa: BLE001 — surface, never drop the repo
        record.error = f"{type(exc).__name__}: {exc}"
        record.classification = CLASS_RED
        record.next_command = f"cortex doctor --path {repo_root}"
    return record


def _classify_overall(record: RepoRecord) -> tuple[str, str]:
    """Roll the per-axis signals into a traffic light + next command.

    red    — structural doctor errors or an unsupported/partial shape.
    yellow — clean structure but stale generated layers (or audit warnings).
    green  — current, no errors.
    """

    if record.doctor_errors > 0 or record.install_shape in (
        SHAPE_PARTIAL,
        SHAPE_UNSUPPORTED,
        SHAPE_MISSING_SPEC,
    ):
        return CLASS_RED, f"cortex doctor --path {record.path}"
    if record.update_status == "stale":
        return CLASS_YELLOW, f"cortex update --path {record.path}"
    if record.audit_warnings:
        return CLASS_YELLOW, f"cortex doctor --audit-instructions --path {record.path}"
    if record.doctor_warnings > 0:
        return CLASS_YELLOW, f"cortex doctor --path {record.path}"
    return CLASS_GREEN, "(current)"


# ---------------------------------------------------------------------------
# JSON contract rendering (public boundary)
# ---------------------------------------------------------------------------


def _repo_record_to_json(record: RepoRecord) -> dict[str, object]:
    """Render a RepoRecord into the stable public JSON shape.

    This is the single place the public contract is defined. Internal
    dataclass fields are never dumped directly — adding a field to
    RepoRecord does not change the JSON unless this function is updated.
    """

    return {
        "path": str(record.path),
        "repo": record.repo,
        "spec_version": record.spec_version,
        "install_shape": record.install_shape,
        "update_status": record.update_status,
        "update_reasons": list(record.update_reasons),
        "doctor_errors": record.doctor_errors,
        "doctor_warnings": record.doctor_warnings,
        "audit_warnings": record.audit_warnings,
        "classification": record.classification,
        "next_command": record.next_command,
        "error": record.error,
    }


# ---------------------------------------------------------------------------
# `cortex fleet check`
# ---------------------------------------------------------------------------


def _render_human_check(records: list[RepoRecord]) -> None:
    """Print the human report, grouping repos by classification."""

    buckets: dict[str, list[RepoRecord]] = {
        CLASS_GREEN: [],
        CLASS_YELLOW: [],
        CLASS_RED: [],
        CLASS_SKIPPED: [],
    }
    for r in records:
        buckets.setdefault(r.classification, []).append(r)

    headings = [
        (CLASS_GREEN, "Current (green)"),
        (CLASS_YELLOW, "Stale / advisory (yellow)"),
        (CLASS_RED, "Structurally invalid / partial (red)"),
        (CLASS_SKIPPED, "No .cortex/ store (skipped)"),
    ]
    for key, title in headings:
        rows = buckets.get(key, [])
        if not rows:
            continue
        click.echo(f"\n{title}:")
        for r in rows:
            detail_bits = [f"shape={r.install_shape}"]
            if r.spec_version:
                detail_bits.append(f"spec={r.spec_version}")
            if key != CLASS_SKIPPED:
                detail_bits.append(f"update={r.update_status}")
                detail_bits.append(f"doctor={r.doctor_errors}E/{r.doctor_warnings}W")
            if r.audit_warnings is not None:
                detail_bits.append(f"audit={r.audit_warnings}W")
            if r.error:
                detail_bits.append(f"error={r.error}")
            click.echo(f"  {r.repo} ({r.path})")
            click.echo(f"    {', '.join(detail_bits)}")
            if r.error:
                click.echo(f"    reason: {r.error}")
            for reason in r.update_reasons:
                click.echo(f"    - {reason}")
            if r.next_command and r.next_command != "(current)":
                click.echo(f"    -> {r.next_command}")

    green = len(buckets.get(CLASS_GREEN, []))
    yellow = len(buckets.get(CLASS_YELLOW, []))
    red = len(buckets.get(CLASS_RED, []))
    skipped = len(buckets.get(CLASS_SKIPPED, []))
    click.echo(
        f"\nFleet: {len(records)} repos — "
        f"{green} green, {yellow} yellow, {red} red, {skipped} skipped."
    )


@click.command("check")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the stable machine-readable per-repo JSON contract.")
@click.option(
    "--path",
    "explicit_paths",
    multiple=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Repo root to inspect (repeatable). Overrides discovery.",
)
@click.option(
    "--paths-file",
    "paths_file",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="File of repo roots (JSON list or newline-delimited). Overrides discovery.",
)
@click.option(
    "--root",
    "scan_roots",
    multiple=True,
    help="Root directory to sibling-scan for .cortex/ repos (repeatable). "
    "Default: ~/repos. Only used when no --path/--paths-file/~/.touchstone-projects.",
)
@click.option(
    "--audit-instructions",
    "audit",
    is_flag=True,
    default=False,
    help="Also run the across-the-fourth-wall claim audit per repo (may make network calls).",
)
def check_command(
    *,
    as_json: bool,
    explicit_paths: tuple[Path, ...],
    paths_file: Path | None,
    scan_roots: tuple[str, ...],
    audit: bool,
) -> None:
    """Report Cortex install shape + freshness + doctor health across discovered repos.

    Read-only. Reuses the in-process freshness check (cortex update
    --check), structural doctor checks, and optional --audit-instructions
    logic — no `cortex` subprocess is spawned.

    Exit code: 0 when no repo is red; 1 when any repo is red (structural
    doctor errors, unsupported/partial install, or unclassifiable).
    """

    roots = scan_roots or _DEFAULT_SCAN_ROOTS
    repos = discover_repos(
        explicit_paths=explicit_paths,
        paths_file=paths_file,
        scan_roots=roots,
        cwd=Path.cwd(),
    )
    records = [classify_repo(r, audit=audit) for r in repos]

    if as_json:
        payload = {"repos": [_repo_record_to_json(r) for r in records]}
        click.echo(json.dumps(payload, indent=2))
    else:
        _render_human_check(records)

    if any(r.classification == CLASS_RED for r in records):
        sys.exit(1)


# ---------------------------------------------------------------------------
# `cortex fleet update`
# ---------------------------------------------------------------------------


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _current_branch(repo_root: Path) -> str | None:
    res = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def _worktree_dirty(repo_root: Path) -> bool:
    res = _git(repo_root, "status", "--porcelain")
    return bool(res.stdout.strip())


@dataclass
class UpdateOutcome:
    path: Path
    repo: str
    action: str  # "would-update" | "updated" | "pr" | "skipped"
    detail: str


def _eligible_for_update(record: RepoRecord) -> tuple[bool, str]:
    """A repo is eligible for `fleet update` iff stale layers + no structural blockers."""

    if record.error is not None:
        return False, f"unclassifiable: {record.error}"
    if record.install_shape in (SHAPE_MISSING, SHAPE_MISSING_SPEC, SHAPE_UNSUPPORTED, SHAPE_PARTIAL):
        return False, f"structurally invalid (shape={record.install_shape}); run cortex doctor"
    if record.doctor_errors > 0:
        return False, f"{record.doctor_errors} structural doctor error(s); run cortex doctor"
    if record.update_status != "stale":
        return False, "already current"
    return True, "stale generated layers"


def _do_pr_update(repo_root: Path, repo_name: str) -> UpdateOutcome:
    """Create a scoped per-repo branch, run run_sync, commit, push, open PR.

    NEVER commits to main/master: switches to a fresh `cortex/fleet-update`
    branch first and refuses if it cannot leave the default branch. The
    operation is recoverable — on any failure the branch is left in place
    for inspection and nothing is force-pushed.
    """

    from cortex.commands.sync import run_sync

    branch = "cortex/fleet-update"
    starting = _current_branch(repo_root)
    if starting is None:
        return UpdateOutcome(repo_root, repo_name, "skipped", "not a git repo or detached HEAD")

    # Create/switch to the scoped branch. `checkout -B` resets the branch to
    # current HEAD — safe because we only ever commit fleet-generated layers.
    co = _git(repo_root, "checkout", "-B", branch)
    if co.returncode != 0:
        return UpdateOutcome(
            repo_root, repo_name, "skipped", f"could not create branch {branch}: {co.stderr.strip()}"
        )

    # Hard invariant: we must NOT be on main/master before writing.
    on = _current_branch(repo_root)
    if on in ("main", "master") or on is None:
        # Restore and bail rather than risk a main-branch commit.
        _git(repo_root, "checkout", starting)
        return UpdateOutcome(
            repo_root, repo_name, "skipped",
            f"refusing to update: still on {on!r} after branch switch",
        )

    result = run_sync(repo_root, run_doctor=False)
    if not result.ok:
        return UpdateOutcome(repo_root, repo_name, "skipped", "run_sync reported failure; left on branch for inspection")

    # Stage only the generated layers fleet update touches.
    _git(repo_root, "add", ".cortex/state.md", ".cortex/.index.json")
    diff = _git(repo_root, "diff", "--cached", "--quiet")
    if diff.returncode == 0:
        # Nothing changed — restore original branch, report no-op.
        _git(repo_root, "checkout", starting)
        return UpdateOutcome(repo_root, repo_name, "skipped", "no changes after refresh")

    commit = _git(repo_root, "commit", "-m", "chore(cortex): refresh generated layers via fleet update")
    if commit.returncode != 0:
        return UpdateOutcome(repo_root, repo_name, "skipped", f"commit failed: {commit.stderr.strip()}")

    push = _git(repo_root, "push", "-u", "origin", branch)
    if push.returncode != 0:
        return UpdateOutcome(
            repo_root, repo_name, "skipped",
            f"committed on {branch} but push failed: {push.stderr.strip()}",
        )

    pr = _git(repo_root, "log", "-1", "--format=%H")  # placeholder; gh below
    gh = subprocess.run(
        ["gh", "pr", "create", "--fill", "--head", branch],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if gh.returncode != 0:
        return UpdateOutcome(
            repo_root, repo_name, "pr",
            f"pushed {branch} but `gh pr create` failed ({gh.stderr.strip()}); open the PR manually",
        )
    _ = pr
    return UpdateOutcome(repo_root, repo_name, "pr", gh.stdout.strip())


@click.command("update")
@click.option("--dry-run", "dry_run", is_flag=True, default=False, help="List what would be rewritten; write NOTHING.")
@click.option("--pr", "open_pr", is_flag=True, default=False, help="Create a scoped per-repo branch + commit + PR (never commits to main/master).")
@click.option(
    "--path",
    "explicit_paths",
    multiple=True,
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    help="Repo root to update (repeatable). Overrides discovery.",
)
@click.option(
    "--paths-file",
    "paths_file",
    type=click.Path(dir_okay=False, exists=True, path_type=Path),
    default=None,
    help="File of repo roots (JSON list or newline-delimited). Overrides discovery.",
)
@click.option(
    "--root",
    "scan_roots",
    multiple=True,
    help="Root directory to sibling-scan (repeatable). Default: ~/repos.",
)
def update_command(
    *,
    dry_run: bool,
    open_pr: bool,
    explicit_paths: tuple[Path, ...],
    paths_file: Path | None,
    scan_roots: tuple[str, ...],
) -> None:
    """Refresh stale generated layers across discovered Cortex repos.

    Only touches repos with stale layers and NO structural doctor errors;
    structurally-invalid stores are skipped with their blocking errors
    reported (never silently dropped). Runs the same `cortex update`
    (run_sync) path each project uses — one code path, in-process.

    --dry-run lists exactly what would be rewritten and writes nothing.
    --pr creates a per-repo `cortex/fleet-update` branch, commits the
    refreshed layers, pushes, and opens a PR — it NEVER commits to
    main/master. Without --pr or --dry-run, the refresh is written in
    place on the repo's current branch.
    """

    if dry_run and open_pr:
        raise click.UsageError("`--dry-run` and `--pr` cannot be combined.")

    roots = scan_roots or _DEFAULT_SCAN_ROOTS
    repos = discover_repos(
        explicit_paths=explicit_paths,
        paths_file=paths_file,
        scan_roots=roots,
        cwd=Path.cwd(),
    )

    outcomes: list[UpdateOutcome] = []
    for repo_root in repos:
        record = classify_repo(repo_root, audit=False)
        eligible, reason = _eligible_for_update(record)
        if not eligible:
            outcomes.append(UpdateOutcome(repo_root, record.repo, "skipped", reason))
            continue

        if dry_run:
            detail = "; ".join(record.update_reasons) or "stale generated layers"
            outcomes.append(UpdateOutcome(repo_root, record.repo, "would-update", detail))
            continue

        if open_pr:
            outcomes.append(_do_pr_update(repo_root, record.repo))
            continue

        # In-place update on the current branch (no PR, no dry-run).
        if _worktree_dirty(repo_root):
            outcomes.append(
                UpdateOutcome(
                    repo_root, record.repo, "skipped",
                    "worktree is dirty; refusing in-place update (use --pr or commit/stash first)",
                )
            )
            continue
        from cortex.commands.sync import run_sync

        result = run_sync(repo_root, run_doctor=False)
        if result.ok:
            outcomes.append(UpdateOutcome(repo_root, record.repo, "updated", "generated layers refreshed"))
        else:
            outcomes.append(UpdateOutcome(repo_root, record.repo, "skipped", "run_sync reported failure"))

    _render_update_outcomes(outcomes, dry_run=dry_run)


def _render_update_outcomes(outcomes: list[UpdateOutcome], *, dry_run: bool) -> None:
    label = "[dry-run] " if dry_run else ""
    for o in outcomes:
        click.echo(f"{label}{o.action}: {o.repo} ({o.path}) — {o.detail}")
    updated = sum(1 for o in outcomes if o.action in ("updated", "pr"))
    would = sum(1 for o in outcomes if o.action == "would-update")
    skipped = sum(1 for o in outcomes if o.action == "skipped")
    click.echo(
        f"\nFleet update: {len(outcomes)} repos — "
        f"{would} would-update, {updated} updated/PR'd, {skipped} skipped."
    )


@click.group("fleet")
def fleet_group() -> None:
    """Fleet-wide Cortex health and update across multiple repos.

    ``cortex fleet check`` reports install shape, freshness, and doctor
    health for every discovered Cortex repo. ``cortex fleet update``
    refreshes stale generated layers (optionally as per-repo PRs).
    """


fleet_group.add_command(check_command)
fleet_group.add_command(update_command)
