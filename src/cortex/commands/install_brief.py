"""`cortex install-brief <target-path>` — generate a self-contained agent brief
for installing Cortex on a target repository.

Detects per-target specifics from the filesystem (ecosystem, distribution
shape, Touchstone-managed paths, sibling repos) so the bulk of the ~200-line
brief is produced without hand-authoring. The output is ready to hand to an
agent via `conductor exec --brief-file ...` or pasted directly into a session.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click

from cortex.shell import run_git

# ── Ecosystem detection ────────────────────────────────────────────────────

_ECOSYSTEM_MANIFESTS: list[tuple[str, str]] = [
    ("pyproject.toml", "Python"),
    ("Package.swift", "Swift"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("package.json", "Node/JavaScript"),
    ("Gemfile", "Ruby"),
    ("pom.xml", "Java/Kotlin (Maven)"),
    ("build.gradle", "Java/Kotlin (Gradle)"),
    ("build.gradle.kts", "Kotlin (Gradle KTS)"),
]

_TOUCHSTONE_MARKERS = [
    "principles/",
    "scripts/",
    ".codex-review.toml",
    ".pre-commit-config.yaml",
]

# PaaS presence heuristics — triggers before the homebrew-tap check.
_PAAS_MARKERS = [
    "nixpacks.toml",
    "Procfile",
    "railway.toml",
    "fly.toml",
    "render.yaml",
    ".heroku",
]

# The 5 canonical install PR references from the v0.9.0 / v1.0 dogfood pool.
_REFERENCE_PRS = [
    ("autumngarage/conductor#178", "https://github.com/autumngarage/conductor/pull/178",
     "conductor — Python + Homebrew"),
    ("autumngarage/touchstone#151", "https://github.com/autumngarage/touchstone/pull/151",
     "touchstone — cleanest PR body; Conductor review caught a real bug"),
    ("henrymodisett/vesper#167", "https://github.com/henrymodisett/vesper/pull/167",
     "vesper — Swift + Homebrew"),
    ("autumngarage/sentinel#112", "https://github.com/autumngarage/sentinel/pull/112",
     "sentinel — Python, pre-existing .cortex/ plans preserved"),
    ("outriderintel/vanguard#190", "https://github.com/outriderintel/vanguard/pull/190",
     "vanguard — Rust"),
]


def _detect_ecosystem(target: Path) -> tuple[str, str]:
    """Return (language_label, manifest_filename) for the dominant ecosystem.

    Returns ("Unknown", "") when no manifest matches.
    """
    for manifest, label in _ECOSYSTEM_MANIFESTS:
        if (target / manifest).exists():
            return label, manifest
    return "Unknown", ""


def _detect_paas(target: Path) -> str | None:
    """Return the matched PaaS marker filename, or None."""
    for marker in _PAAS_MARKERS:
        if (target / marker).exists():
            return marker
    return None


def _detect_touchstone_paths(target: Path) -> list[str]:
    """Return the subset of Touchstone-managed marker paths that exist in target."""
    present = []
    for marker in _TOUCHSTONE_MARKERS:
        if marker.endswith("/"):
            if (target / marker.rstrip("/")).is_dir():
                present.append(marker)
        else:
            if (target / marker).exists():
                present.append(marker)
    return present


def _detect_homebrew_tap(target: Path) -> str | None:
    """Return a brew tap slug (owner/name) if a sibling homebrew-<name> repo exists."""
    name = target.name
    sibling = target.parent / f"homebrew-{name}"
    if sibling.is_dir():
        # Try to infer owner from git remote of target.
        owner = _parse_github_owner(_git_remote_url(target) or "")
        if owner:
            return f"{owner}/{name}"
        return f"<owner>/{name}"
    return None


def _git_remote_url(target: Path) -> str | None:
    """Return the origin remote URL string, or None."""
    result = run_git("-C", str(target), "remote", "get-url", "origin")
    if result.ok and result.stdout.strip():
        return result.stdout.strip()
    return None


def _parse_github_remote(url: str | None) -> tuple[str | None, str | None]:
    """Parse (owner, repo) from a GitHub remote URL.

    Handles SSH (git@github.com:owner/repo.git) and HTTPS forms.
    Returns (None, None) when the URL is absent or unparseable.
    """
    if not url:
        return None, None
    # SSH: git@github.com:owner/repo.git
    ssh_match = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)
    return None, None


def _parse_github_owner(url: str) -> str | None:
    owner, _ = _parse_github_remote(url)
    return owner


def _enumerate_cortex_siblings(target: Path) -> list[str]:
    """List ~/repos/*/ directories (other than target) that have .cortex/SPEC_VERSION.

    Deduplicates by GitHub slug so worktrees of the same repo don't appear
    multiple times (e.g. cortex, cortex-install-brief-and-merge-docs, etc.).
    """
    repos_dir = Path.home() / "repos"
    siblings: list[str] = []
    seen: set[str] = set()
    if not repos_dir.is_dir():
        return siblings
    target_resolved = target.resolve()

    # Resolve the target's own git remote so we can exclude self-references
    # regardless of which worktree directory the caller is running from.
    target_remote = _git_remote_url(target)
    target_owner, target_repo = _parse_github_remote(target_remote)
    target_slug = f"{target_owner}/{target_repo}" if target_owner and target_repo else None

    for candidate in sorted(repos_dir.iterdir()):
        if not candidate.is_dir():
            continue
        if candidate.resolve() == target_resolved:
            continue
        if not (candidate / ".cortex" / "SPEC_VERSION").exists():
            continue
        remote_url = _git_remote_url(candidate)
        owner, repo = _parse_github_remote(remote_url)
        slug = f"{owner}/{repo}" if owner and repo else candidate.name
        # Skip self (target repo under a different worktree name) and dupes.
        if slug == target_slug:
            continue
        if slug in seen:
            continue
        seen.add(slug)
        siblings.append(slug)
    return siblings


def _source_exclude_hint(language: str) -> str:
    """Return a brief 'exclude from scope' note for the detected ecosystem."""
    if language.startswith("Python"):
        return "src/, tests/ (Python source — exclude from diff scope)"
    if language.startswith("Swift"):
        return "Sources/, Tests/ (Swift source — exclude from diff scope)"
    if language.startswith("Rust"):
        return "src/ (Rust source — exclude from diff scope)"
    if language.startswith("Go"):
        return "*.go files (Go source — exclude from diff scope)"
    if language.startswith("Node") or language.startswith("JavaScript"):
        return "src/, lib/ (JS source — exclude from diff scope)"
    if language.startswith("Ruby"):
        return "lib/, spec/ (Ruby source — exclude from diff scope)"
    if language.startswith("Java") or language.startswith("Kotlin"):
        return "src/ (JVM source — exclude from diff scope)"
    return "<source directories> — exclude from diff scope"


def _build_brief(
    *,
    target: Path,
    owner: str | None,
    repo: str | None,
    language: str,
    manifest: str,
    paas_marker: str | None,
    homebrew_tap: str | None,
    touchstone_paths: list[str],
    siblings: list[str],
    include_references: bool,
) -> str:
    name = target.name
    github_slug = f"{owner}/{repo}" if owner and repo else "<owner>/<repo>"
    title = f"# Brief — Install Cortex on {name} ({github_slug})"

    # Distribution shape
    if paas_marker is not None:
        distrib_lines = [
            "# TODO: swap to paas_repos when cortex#161 lands",
            f"# PaaS signal detected ({paas_marker}); using github_repos fallback for now.",
            f'github_repos = ["{github_slug}"]',
        ]
        distrib_block = "\n".join(distrib_lines)
        distrib_label = f"github_repos (PaaS/{paas_marker} detected — paas_repos pending cortex#161)"
    elif homebrew_tap is not None:
        distrib_lines = [f'homebrew_tap = "{homebrew_tap}"']
        distrib_block = "\n".join(distrib_lines)
        distrib_label = f"homebrew_tap = {homebrew_tap!r}"
    else:
        distrib_lines = [f'github_repos = ["{github_slug}"]']
        distrib_block = "\n".join(distrib_lines)
        distrib_label = f'github_repos = ["{github_slug}"]'

    # Touchstone section
    if touchstone_paths:
        touchstone_section = (
            "\n## Scope — DO NOT touch (Touchstone-managed)\n\n"
            + "\n".join(f"- `{p}`" for p in touchstone_paths)
            + "\n\nThese are synced by Touchstone (`touchstone update`). "
            "Modifying them directly breaks the sync contract."
        )
        scope_do_not_touch = "- Touchstone-managed paths listed above\n"
    else:
        touchstone_section = ""
        scope_do_not_touch = ""

    # Sibling list
    if siblings:
        sibling_lines = "\n".join(f"- {s}" for s in siblings)
        siblings_block = sibling_lines
        siblings_toml = ", ".join(f'"{s}"' for s in siblings)
    else:
        siblings_block = "(none detected in ~/repos/)"
        siblings_toml = ""

    # Reference PRs
    if include_references:
        ref_lines = "\n".join(
            f"- [{ref}]({url}) — {desc}" for ref, url, desc in _REFERENCE_PRS
        )
        references_section = f"\n## Prior install references\n\n{ref_lines}\n"
    else:
        references_section = ""

    # Source exclude hint
    source_exclude = _source_exclude_hint(language)

    # Config skeleton
    scan_files_toml = '["CLAUDE.md", "AGENTS.md", "README.md"]'
    siblings_toml_line = f'\nsibling_repos = [{siblings_toml}]' if siblings_toml else ""
    config_skeleton = f"""\
[audit-instructions]
scan_files = {scan_files_toml}{siblings_toml_line}
{distrib_block}"""

    # Build the brief
    brief = f"""{title}

## Target

- **Path:** {target}
- **GitHub:** {github_slug}
- **Language:** {language}{f" ({manifest})" if manifest else ""}
- **Distribution:** {distrib_label}

## Scope — what to touch

- `.cortex/` — scaffold via `cortex init`
- `.cortex/config.toml` — configure `[audit-instructions]`
- `CLAUDE.md` and/or `AGENTS.md` — append `@.cortex/protocol.md` + `@.cortex/state.md` imports
- `.gitignore` — add Cortex transient-path entries (`.cortex/.index.json`, `.cortex/.index/`, `.cortex/pending/`)
{scope_do_not_touch}{touchstone_section}

## Sibling repos with Cortex installed

{siblings_block}
{references_section}
## [audit-instructions] config skeleton

Paste into `.cortex/config.toml` and fill in the blanks:

```toml
{config_skeleton}
```

## Phase-by-phase plan

### Phase 1 — Pre-flight

- [ ] `cd {target} && git pull --rebase`
- [ ] `cortex --version` — confirm cortex 1.0.0+ is on PATH
- [ ] `cortex doctor` — note pre-install error/warning counts for comparison

### Phase 2 — Install

- [ ] `cortex init --yes` (or `cortex init --force --yes` if `.cortex/` already has content worth preserving)
- [ ] Verify scaffold: `cortex doctor` — no new errors introduced by init

### Phase 3 — Configure

- [ ] Edit `.cortex/config.toml` — paste in the `[audit-instructions]` skeleton above; fill in blanks
- [ ] `cortex doctor --audit-instructions` — all claims verified ✅

### Phase 4 — Verify

- [ ] `cortex manifest --budget 8000`
- [ ] `cortex next`
- [ ] `cortex doctor`
- [ ] Scope check: `git diff --stat main` touches ONLY `.cortex/`, `.gitignore`, `CLAUDE.md`, `AGENTS.md`
- [ ] No diff in: {source_exclude}{"" if not touchstone_paths else (chr(10) + "- [ ] No diff in: " + ", ".join(f"`{p}`" for p in touchstone_paths))}

### Phase 5 — Baseline journal entry

- [ ] `cortex journal draft decision --title "cortex-install-baseline"` — capture install findings
- [ ] Record any pre-existing `cortex doctor` warnings as known debt (do not silently patch)
- [ ] File any issues surfaced against Cortex upstream (autumngarage/cortex)

### Phase 6 — Ship

- [ ] `git checkout -b chore/install-cortex`
- [ ] `git add .cortex/ .gitignore CLAUDE.md AGENTS.md` (stage explicit paths — no `git add .`)
- [ ] `git commit -m "chore: install Cortex"`
- [ ] Open PR on {github_slug} — use the shared PR body from `docs/install-pr-templates.md`
- [ ] **Merge path:** `cd {target} && bash scripts/merge-pr.sh <pr-number>` (preferred — runs Conductor review). Fast path: `gh pr merge <n> --repo {github_slug} --squash --delete-branch` (skips review — use only when target has no `scripts/merge-pr.sh`).

## Output format

When done, report:

```
PR: <URL on {github_slug}>
cortex doctor: <N> errors / <N> warnings (before) → <N> errors / <N> warnings (after)
cortex doctor --audit-instructions: <N> claims checked, all verified / <N> failures
Issues filed: <list or "none">
Scope check: diff touches only .cortex/, .gitignore, CLAUDE.md, AGENTS.md ✅
```
"""
    return brief  # noqa: RET504


@click.command("install-brief")
@click.argument(
    "target_path",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(file_okay=True, dir_okay=False, path_type=Path),
    default=None,
    help="Write the brief to a file instead of stdout.",
)
@click.option(
    "--no-references",
    "omit_references",
    is_flag=True,
    default=False,
    help="Omit prior-install PR references.",
)
def install_brief_command(
    target_path: Path,
    output_path: Path | None,
    omit_references: bool,
) -> None:
    """Generate a self-contained Cortex install brief for delegating to an agent.

    TARGET_PATH is the root of the repository to install Cortex on.
    The brief is written to stdout (or --output PATH) and is ready to hand
    to an agent via `conductor exec --brief-file ...`.
    """
    target = Path(target_path).expanduser().resolve()

    if not target.is_dir():
        click.echo(
            f"error: target path is not a directory: {target}\n"
            "Provide the root of an existing repository.",
            err=True,
        )
        sys.exit(1)

    # Git repo check
    git_check = run_git("-C", str(target), "rev-parse", "--git-dir")
    if not git_check.ok:
        click.echo(
            f"error: {target} is not a git repository.\n"
            "cortex install-brief requires a git repo so it can read the GitHub remote\n"
            "and enumerate the branch history. Run `git init` if this is a new project.",
            err=True,
        )
        sys.exit(1)

    # GitHub remote
    remote_url = _git_remote_url(target)
    if remote_url is None:
        click.echo(
            "error: no 'origin' remote found.\n"
            "Add one with: git remote add origin https://github.com/<owner>/<repo>.git",
            err=True,
        )
        sys.exit(1)

    owner, repo = _parse_github_remote(remote_url)
    if owner is None or repo is None:
        # Non-fatal: emit the brief with placeholders.
        click.echo(
            f"warning: could not parse a GitHub remote from {remote_url!r}. "
            "Placeholders left in the brief.",
            err=True,
        )

    language, manifest = _detect_ecosystem(target)
    paas_marker = _detect_paas(target)
    homebrew_tap = _detect_homebrew_tap(target) if paas_marker is None else None
    touchstone_paths = _detect_touchstone_paths(target)
    siblings = _enumerate_cortex_siblings(target)

    brief = _build_brief(
        target=target,
        owner=owner,
        repo=repo,
        language=language,
        manifest=manifest,
        paas_marker=paas_marker,
        homebrew_tap=homebrew_tap,
        touchstone_paths=touchstone_paths,
        siblings=siblings,
        include_references=not omit_references,
    )

    if output_path is not None:
        output_path.expanduser().resolve().write_text(brief)
        click.echo(f"Brief written to {output_path}", err=True)
    else:
        click.echo(brief, nl=False)
