"""`cortex sync` — umbrella command that brings a project up to date with the installed CLI.

Composes the existing refresh primitives in one operator step:

1. `cortex refresh-state` — regenerate `.cortex/state.md`.
2. `cortex refresh-index --retrieve` (when an index already exists or
   retrieval is configured) — rebuild `.cortex/.index.json` and the
   retrieve sqlite index.
3. Schema-validate `.cortex/config.toml` against the current CLI's
   schema — surface unknown keys without rewriting the file.
4. `cortex doctor` — final structural pass; warnings are reported but
   are not fatal.

Sync only invokes idempotent regenerations. Lossy migrations (notably
`cortex migrate-state`) are explicitly excluded — they require operator
consent and have their own command.

This module is also the single code path that auto-sync (Layer 2 in
`commands/__init__.py`) calls, with `run_doctor=False` to keep the
auto-flow lean. There is one sync function; auto-sync is just a
different invocation prefix.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from cortex import __version__
from cortex.compat import require_compatible
from cortex.config import load_refresh_index_config
from cortex.doctor_checks import check_config_toml_schema
from cortex.index import refresh_index
from cortex.state_render import build_state_inputs, render_state
from cortex.validation import Severity, run_all_checks


_GENERATOR_VERSION_RE = re.compile(
    r"Generator:\s*cortex\s+refresh-state\s+v(?P<version>\S+)"
)


@dataclass(frozen=True)
class SyncResult:
    """Outcome of a sync run, used for tests and the auto-sync caller."""

    refresh_state_ok: bool
    refresh_index_ok: bool
    config_unknown_keys: int
    doctor_errors: int
    doctor_warnings: int
    skipped_doctor: bool

    @property
    def ok(self) -> bool:
        # Doctor warnings are not fatal; doctor errors are. Underlying
        # refresh-* failures are fatal too.
        return (
            self.refresh_state_ok
            and self.refresh_index_ok
            and self.doctor_errors == 0
        )


def _read_state_generator_version(cortex_dir: Path) -> str | None:
    """Return the version recorded in state.md's Generator field, or None.

    The seven-field provenance header always declares Generator on a
    refreshed state; legacy hand-authored State files may not. We extract
    the version so the sync banner can show a `(state.md was vX.Y.Z)`
    drift hint when the recorded version disagrees with the installed CLI.
    """

    state_path = cortex_dir / "state.md"
    if not state_path.is_file():
        return None
    try:
        text = state_path.read_text()
    except OSError:
        return None
    match = _GENERATOR_VERSION_RE.search(text)
    if not match:
        return None
    return match.group("version")


def _retrieve_index_present(cortex_dir: Path) -> bool:
    """Return True when the project already maintains a retrieve index.

    The retrieve index lives at `.cortex/.index/`. Sync only rebuilds it
    when a project has opted in (the directory exists) — fresh projects
    that haven't run `cortex retrieve` should not pay the cost of a first
    semantic build during a routine sync.
    """

    return (cortex_dir / ".index").is_dir()


def _do_refresh_state(project_root: Path) -> bool:
    """Regenerate state.md. Returns True on success."""

    cortex_dir = project_root / ".cortex"
    require_compatible(cortex_dir)
    try:
        rendered = render_state(build_state_inputs(project_root))
    except Exception as exc:
        click.echo(f"error: refresh-state failed: {exc}", err=True)
        return False
    try:
        (cortex_dir / "state.md").write_text(rendered)
    except OSError as exc:
        click.echo(f"error: could not write state.md: {exc}", err=True)
        return False
    return True


def _do_refresh_index(project_root: Path, *, include_retrieve: bool) -> bool:
    """Rebuild `.cortex/.index.json` and (optionally) retrieve sqlite. Returns True on success."""

    cortex_dir = project_root / ".cortex"
    require_compatible(cortex_dir)
    config = load_refresh_index_config(project_root)
    try:
        result = refresh_index(project_root, config)
    except Exception as exc:
        click.echo(f"error: refresh-index failed: {exc}", err=True)
        return False
    for warning in result.warnings:
        click.echo(f"warning: {warning}", err=True)
    if include_retrieve:
        try:
            from cortex.retrieve.index import rebuild_index

            rebuild_index(project_root)
        except Exception as exc:
            click.echo(f"error: could not rebuild retrieve index: {exc}", err=True)
            return False
    return True


def _do_validate_config(project_root: Path) -> int:
    """Run the existing config.toml schema validator; return count of unknown keys reported."""

    cortex_dir = project_root / ".cortex"
    if not (cortex_dir / "config.toml").is_file():
        return 0
    issues = check_config_toml_schema(project_root)
    unknown_count = 0
    for issue in issues:
        if "unknown key" in issue.message:
            unknown_count += 1
            click.echo(f"warning: {issue.path}: {issue.message}", err=True)
        else:
            stream = sys.stderr if issue.severity is Severity.ERROR else sys.stdout
            click.echo(f"{issue.severity.value}: {issue.path}: {issue.message}", file=stream)
    return unknown_count


def _do_doctor(project_root: Path) -> tuple[int, int]:
    """Run the same structural-checks pass that `cortex doctor` runs (no audits).

    Returns ``(error_count, warning_count)``. Audits are deferred from sync
    by design: the doctor frontend's `--audit*` flags are opt-in and may
    require network access (e.g. issue-refs needs `gh`). Sync should not
    surprise operators with network calls.
    """

    from cortex.doctor_checks import run_plain_checks

    issues = run_all_checks(project_root)
    issues.extend(run_plain_checks(project_root))
    errors = sum(1 for i in issues if i.severity is Severity.ERROR)
    warnings = sum(1 for i in issues if i.severity is Severity.WARNING)
    for issue in issues:
        if issue.severity is Severity.ERROR:
            click.echo(f"error: {issue.path}: {issue.message}", err=True)
        else:
            click.echo(f"warning: {issue.path}: {issue.message}")
    return errors, warnings


def run_sync(
    project_root: Path,
    *,
    run_doctor: bool = True,
    output_prefix: str = "==>",
) -> SyncResult:
    """Run the full sync sequence; the single code path used by both `cortex sync` and auto-sync.

    Parameters
    ----------
    project_root
        Resolved path to the project root (must contain `.cortex/`).
    run_doctor
        Whether to run the trailing structural-doctor pass. Auto-sync sets
        this to False to keep its critical path lean; the operator-driven
        `cortex sync` runs it by default.
    output_prefix
        Leading token for status lines. `cortex sync` uses ``==>``;
        auto-sync uses ``==> auto-sync:`` so the action is visibly
        distinguishable.
    """

    cortex_dir = project_root / ".cortex"
    state_version = _read_state_generator_version(cortex_dir)
    drift_clause = (
        f" (state.md was v{state_version})"
        if state_version and state_version != __version__
        else ""
    )
    click.echo(f"{output_prefix} Detected cortex {__version__}.{drift_clause} Syncing.")

    # Step 1: refresh-index FIRST so that state.md, which records the
    # promotion-queue file in its provenance, sees the freshly built index.
    # If we ran refresh-state first against a missing .index.json, the
    # render would record `.index.json — absent` in `Omitted:` and the next
    # sync would produce a different (now-clean) state.md — breaking
    # idempotency. The user-facing output still leads with refresh-state
    # in the message text since "regenerate state, rebuild index" is what
    # operators read; the internal ordering is what makes it stable.
    include_retrieve = _retrieve_index_present(cortex_dir)
    refresh_index_ok = _do_refresh_index(project_root, include_retrieve=include_retrieve)

    # Step 2: refresh-state
    refresh_state_ok = _do_refresh_state(project_root)
    click.echo(
        f"{output_prefix} cortex refresh-state ............................. "
        f"{'done' if refresh_state_ok else 'FAILED'}"
    )
    click.echo(
        f"{output_prefix} cortex refresh-index"
        f"{' --retrieve' if include_retrieve else ''} "
        f".................. {'done' if refresh_index_ok else 'FAILED'}"
    )

    # Step 3: config schema validation (warn-only)
    unknown_keys = _do_validate_config(project_root)
    suffix = f"({unknown_keys} unknown key{'s' if unknown_keys != 1 else ''})" if unknown_keys else "(0 unknown keys)"
    click.echo(
        f"{output_prefix} Validating .cortex/config.toml schema  done {suffix}"
    )

    # Step 4: doctor (skippable)
    doctor_errors = 0
    doctor_warnings = 0
    if run_doctor:
        doctor_errors, doctor_warnings = _do_doctor(project_root)
        click.echo(
            f"{output_prefix} cortex doctor .................................... "
            f"{doctor_errors} error{'s' if doctor_errors != 1 else ''}, "
            f"{doctor_warnings} warning{'s' if doctor_warnings != 1 else ''}"
        )

    result = SyncResult(
        refresh_state_ok=refresh_state_ok,
        refresh_index_ok=refresh_index_ok,
        config_unknown_keys=unknown_keys,
        doctor_errors=doctor_errors,
        doctor_warnings=doctor_warnings,
        skipped_doctor=not run_doctor,
    )

    summary_bits: list[str] = []
    if refresh_state_ok:
        summary_bits.append("State.md regenerated")
    if refresh_index_ok:
        summary_bits.append("index rebuilt")
    if unknown_keys == 0:
        summary_bits.append("config validates")
    summary = "; ".join(summary_bits) if summary_bits else "no work performed"
    click.echo(f"\nSync complete. {summary}.")

    return result


@click.command("sync")
@click.option(
    "--no-doctor",
    is_flag=True,
    default=False,
    help="Skip the trailing `cortex doctor` pass. Useful in CI scripts that run doctor separately.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print what would run without invoking refresh-state, refresh-index, config validation, or doctor.",
)
@click.option(
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def sync_command(*, no_doctor: bool, dry_run: bool, target_path: Path) -> None:
    """Bring a project up to date with the installed Cortex CLI in one step.

    Runs the full refresh ritual: regenerate `state.md`, rebuild the
    promotion index (and the retrieve index when present), validate
    `.cortex/config.toml` against the current schema, and run `cortex
    doctor`. Each underlying step is idempotent — running `cortex sync`
    twice on an unchanged project produces no diff. Lossy migrations
    (e.g. `cortex migrate-state`) are explicitly NOT invoked; they
    require operator consent and remain separate commands.

    Exit code: 0 when refresh and doctor pass; 1 if any underlying
    refresh-* command failed or doctor found errors. Doctor warnings do
    not affect the exit code.
    """

    project_root = Path(target_path).resolve()
    cortex_dir = project_root / ".cortex"
    if not cortex_dir.is_dir():
        click.echo(
            f"error: {cortex_dir} does not exist; run `cortex init` first.",
            err=True,
        )
        sys.exit(2)

    if dry_run:
        click.echo(f"==> [dry-run] cortex {__version__} would sync {project_root}")
        click.echo("==> [dry-run] would run: cortex refresh-state")
        include_retrieve = _retrieve_index_present(cortex_dir)
        click.echo(
            f"==> [dry-run] would run: cortex refresh-index"
            f"{' --retrieve' if include_retrieve else ''}"
        )
        click.echo("==> [dry-run] would validate .cortex/config.toml")
        if not no_doctor:
            click.echo("==> [dry-run] would run: cortex doctor")
        return

    result = run_sync(project_root, run_doctor=not no_doctor)
    if not result.ok:
        sys.exit(1)
