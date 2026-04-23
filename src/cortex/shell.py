"""Shell subprocess helpers shared across Cortex commands.

Cortex shells out to external tools (today ``git``; Phase C adds the
``claude`` CLI; Phase E may add Sentinel / Touchstone pokes) whenever the
answer lives outside Python. Two patterns kept recurring during the PR #27
``--local-only`` review loop and each one was a load-bearing fix:

**Tri-state returns for subprocess queries.** "Did git say zero files?"
and "did git fail to run at all?" are NOT the same answer. Collapsing
both into an empty-list fallback gave false assurance downstream:
callers that say "no tracked files → print 'will not be published'"
happily print the success claim when the check never ran. :func:`run_git`
returns a :class:`GitRun` with an explicit ``ok`` flag and a dedicated
``not_a_repo`` branch so callers can branch on three outcomes instead
of two.

**Path-aware, shell-quoted remediation strings.** User-facing advice
like "run ``git rm --cached -r .cortex/``" is not a string, it is
executable code the user will paste into a shell. Two invariants apply:
the command must anchor to the project Cortex acted on (not the user's
cwd) and it must survive paths with metacharacters.
:func:`git_remediation_cmd` emits the plain form when cwd matches the
target and the ``git -C <shlex.quote(target)>`` form otherwise.

This module is the single authority for both rules; new commands that
shell out should import from here rather than reinvent either pattern.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitRun:
    """Structured result of a :func:`run_git` invocation.

    Three states matter to callers:

    * ``ok`` is True — the command ran cleanly; ``stdout`` is the result.
    * ``ok`` is False and ``not_a_repo`` is True — git reported that the
      given path is not a git repository. A known-safe state for queries
      like ``ls-files`` where "not a repo" is equivalent to "nothing
      tracked here".
    * ``ok`` is False and ``not_a_repo`` is False — something genuinely
      went wrong (git not installed, subprocess error, corrupted repo,
      permission issue). Callers MUST NOT collapse this with the
      known-safe branch; prefer "unknown, warn the user" over silent
      success.
    """

    stdout: str
    stderr: str
    returncode: int
    reason: str | None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def not_a_repo(self) -> bool:
        return (
            self.returncode != 0
            and "not a git repository" in (self.stderr or "").lower()
        )


def run_git(
    *args: str,
    cwd: Path | None = None,
    timeout: float = 10.0,
) -> GitRun:
    """Run ``git`` with the given args and return a :class:`GitRun`.

    Never raises. Failures at the subprocess level (``git`` not on PATH,
    :class:`OSError`, timeout) surface as ``ok=False, not_a_repo=False``
    with a populated ``reason`` string, so every caller can use the same
    tri-state branch structure regardless of failure mode.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return GitRun(stdout="", stderr="", returncode=-1, reason="git not installed")
    except subprocess.TimeoutExpired:
        return GitRun(stdout="", stderr="", returncode=-1, reason="git timed out")
    except OSError as exc:
        return GitRun(
            stdout="",
            stderr="",
            returncode=-1,
            reason=f"subprocess error: {exc}",
        )
    return GitRun(
        stdout=result.stdout or "",
        stderr=result.stderr or "",
        returncode=result.returncode,
        reason=None,
    )


def git_remediation_cmd(
    *git_args: str,
    target: Path,
    anchor_to_target: bool,
) -> str:
    """Build a copy-paste-safe ``git`` command string for user-facing advice.

    When ``anchor_to_target`` is True, the command is prefixed with
    ``git -C <shlex.quote(target)>`` so a user running the command from
    any cwd will affect the project Cortex acted on. When False, the
    plain ``git <args>`` form is emitted for readability in the common
    case (cwd == target).

    Every positional ``git_args`` entry is passed through
    :func:`shlex.quote`, which is a no-op for safe tokens like
    ``--cached`` or ``-r`` and a proper quoting pass for tokens that
    contain whitespace or shell metacharacters (e.g. a commit message
    like ``chore: untrack .cortex/ (local-only)``).
    """
    quoted_body = " ".join(shlex.quote(a) for a in git_args)
    if anchor_to_target:
        return f"git -C {shlex.quote(str(target))} {quoted_body}"
    return f"git {quoted_body}"
