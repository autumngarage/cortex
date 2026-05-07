"""`cortex check-triggers` — emit NDJSON for every Tier 1 trigger fired by a diff.

Step 1 of issue #195: turn the Tier 1 trigger table from agent-recall
discipline into a CLI primitive that an agent runtime (Claude Code stop hook,
Aider hook, Cursor) can call after each action and inject the resulting
prompts inline. CI ``--strict`` gating, runtime stop-hook recipes, and
refactoring existing T1.9/T1.10 hooks to call this primitive are explicit
follow-ups, not part of this command.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from cortex.check_triggers import (
    _GitError,
    check_triggers,
    emit_warnings,
)

_HELP_EPILOG = """\
\b
Coverage in this release (deterministic, diff-derivable triggers):
  T1.1  Diff touches `.cortex/doctrine/`, `.cortex/plans/`, `principles/`, or `SPEC.md`.
  T1.4  File deletion exceeds the configured threshold (default 100 lines;
        override `T1.4.line-threshold:` in `.cortex/protocol.md`).
  T1.5  Dependency manifest changed (pyproject.toml, package.json,
        Cargo.toml, go.mod, Gemfile).
  T1.8  Commit subject matches the Protocol §2 regex set
        (`fix:.*regression`, `refactor:.*(removes|introduces)`,
        `feat:.*(breaking|replaces)`).

\b
Out of scope here:
  T1.2  Test-failure event — runtime, not derivable from a diff.
  T1.3  Plan Status: change — cross-cuts plan-spawn semantics; deferred.
  T1.6  Sentinel cycle — runtime event.
  T1.7  Touchstone pre-merge — runtime event.
  T1.9  PR merged — already wired via the post-merge hook.
  T1.10 Release — already wired via the release-substitution path (#192).

\b
Output is NDJSON on stdout, one JSON object per fired trigger. Silence is
success — exit 0 with no output means no Tier 1 triggers fired in the diff.
"""


@click.command(
    "check-triggers",
    epilog=_HELP_EPILOG,
)
@click.option(
    "--since",
    "since",
    metavar="REF",
    default=None,
    help=(
        "Evaluate the diff `<REF>..HEAD`. Mutually exclusive with --staged. "
        "Defaults to `HEAD~1` if neither flag is given."
    ),
)
@click.option(
    "--staged",
    is_flag=True,
    default=False,
    help="Evaluate the staged diff against HEAD (use as a pre-commit hook).",
)
@click.option(
    "--target-path",
    "--path",
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def check_triggers_command(
    *,
    since: str | None,
    staged: bool,
    target_path: Path,
) -> None:
    """Emit NDJSON describing every Tier 1 trigger fired by a diff.

    Designed to be called from agent stop-hooks and similar runtime
    integrations: after a commit (or before, with ``--staged``), the runtime
    invokes this command, parses each NDJSON line, and surfaces the
    accompanying template path so the agent can author the matching Journal
    entry inline — instead of relying on memory and a post-hoc audit.
    """
    if since is not None and staged:
        click.echo(
            "error: --since and --staged are mutually exclusive.",
            err=True,
        )
        sys.exit(2)

    if not staged and since is None:
        since = "HEAD~1"

    target_path = Path(target_path).resolve()

    try:
        result = check_triggers(target_path, since=since, staged=staged)
    except FileNotFoundError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)
    except _GitError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(2)

    emit_warnings(result.warnings)

    for hit in result.hits:
        click.echo(json.dumps(hit.to_dict(), sort_keys=True))
