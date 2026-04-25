"""Project scanner for `cortex init` — first phase of scan-and-absorb.

`cortex init` runs ``scan_project()`` on every TTY invocation BEFORE prompting
the user about anything. The scan walks the project root looking for files
that match well-known patterns from the broader engineering ecosystem
(``principles/*.md``, ``docs/decisions/*.md``, ``ROADMAP.md``, ``CHANGELOG.md``,
…). Findings are categorized (doctrine / plan / reference / map_ref) so the
import wizard can then ask "do you want to absorb these into ``.cortex/``?"
one prompt at a time.

Design rules (NON-NEGOTIABLE — these are the brief's core principles):

1. **No auto-classification of unknowns.** Files matching no known pattern but
   looking load-bearing are surfaced separately for a per-file prompt; we
   never silently treat an unknown file as Doctrine or as a Plan.
2. **Mirror source shape.** One scan finding per source *file*, never per
   inline section/principle. The seeder later mints exactly one ``.cortex/``
   entry per finding, not one per H2.
3. **Skip transient directories aggressively.** ``node_modules/``, ``.venv/``,
   ``DerivedData/``, ``build/`` and friends never appear in scan results.
   Vesper's literal ``site/node_modules/doctrine/`` JS package is the
   motivating regression — the skip list keeps that out.
4. **Respect the project's ``.gitignore``.** Anything git already ignores is
   not Cortex's business. We shell out to ``git check-ignore`` when a git
   binary is on PATH; absent git we fall back to the always-skip list and
   carry on (degrade gracefully — Doctrine 0002 file-contract pattern).
5. **Cite, never hallucinate.** Every finding records the file's relative
   path so downstream printers and seeders can reference the source. The
   scan never reads a file's body to decide its category beyond the small
   "shipped Plan?" demote heuristic and the load-bearing structure check
   for unknowns.
6. **Forward-only on Sentinel.** ``.sentinel/runs/`` is detected only to
   surface a one-line note. We do NOT backfill synthetic T1.6 entries —
   journal is append-only AND time-anchored (Protocol § 4.1).

Custom-pattern learning lives in ``.cortex/.discover.toml``. When init
encounters an unclassifiable file it offers to teach the scanner so future
runs (in this repo or any other repo carrying the same shape) recognize it
without prompting. The TOML file is a Tier-2 escape hatch: built-in patterns
ship with Cortex; user-taught patterns live next to the project they were
discovered in.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Categories surfaced by the scan.
#
# - ``doctrine`` — candidate for promotion into ``.cortex/doctrine/``. One
#   imported entry per source file (mirror source shape).
# - ``plan`` — candidate for ``.cortex/plans/``. May be demoted to
#   ``reference`` when the source shows shipped/done status markers.
# - ``reference`` — content that the project should know about but that we
#   refuse to absorb (CHANGELOGs are time-anchored, journals can't be
#   backfilled). Surface in ``state.md`` Sources only.
# - ``map_ref`` — README/ARCHITECTURE/SETUP — load-bearing context the State
#   layer should cite, also Sources-only (not extracted).
# - ``unknown`` — load-bearing markdown that didn't match any built-in or
#   user-taught pattern; surfaces for an interactive classify prompt.
# - ``touchstone_managed`` — would have classified as ``doctrine`` but the
#   project has Touchstone integration (.touchstone-config / .touchstone-
#   manifest / .touchstone-version present), meaning the source file is
#   already imported into the agent's context via ``@<path>`` directives in
#   CLAUDE.md / AGENTS.md (Touchstone owns ``principles/``). Importing a
#   stub-pointer Doctrine entry on top of that adds no content and *displaces*
#   real Doctrine in the session-start manifest budget. Surface in scan
#   output as informational; do not seed.
# - ``meta_doc`` — would have classified as ``doctrine`` by a built-in
#   pattern but the filename is README.md (case-insensitive). README files
#   are orientation prose for the directory they live in, not doctrine
#   content. Surface in scan output as "Skipped (meta-doc filename):"; do
#   not seed. User-taught ``.cortex/.discover.toml`` doctrine patterns
#   override this filter (explicit user instruction wins).
Category = Literal[
    "doctrine", "plan", "reference", "map_ref", "unknown", "touchstone_managed", "meta_doc"
]

# Pattern categories include everything in ``Category`` plus ``skip`` — the
# latter is a user-taught directive that suppresses matches without producing
# findings. ``Category`` (no ``skip``) is what flows through ``Finding``.
PatternCategory = Literal[
    "doctrine",
    "plan",
    "reference",
    "map_ref",
    "unknown",
    "touchstone_managed",
    "meta_doc",
    "skip",
]

# Globs whose matches should be reclassified as ``touchstone_managed`` when
# Touchstone integration is detected on the project. These are the
# Touchstone-owned (synced via ``touchstone update``) source paths whose
# content already lands in the agent's context via the existing
# ``@<path>`` import directives in CLAUDE.md / AGENTS.md.
_TOUCHSTONE_MANAGED_DOCTRINE_GLOBS: frozenset[str] = frozenset({
    "principles/*.md",
    "docs/principles/*.md",
})

# Filenames (case-insensitive basename) that should NEVER be auto-imported
# as Doctrine when matched by a built-in pattern. README files are
# orientation prose; doctrine content lives in sibling files. User-taught
# patterns from .cortex/.discover.toml bypass this filter (explicit user
# instruction wins over the conservative default).
_META_DOC_BASENAMES: frozenset[str] = frozenset({"readme.md"})


@dataclass(frozen=True)
class Pattern:
    """One scan rule. Built-in patterns + user-taught (TOML) ones share this shape."""

    glob: str
    category: PatternCategory
    description: str
    source: str  # "built-in" or ".cortex/.discover.toml"


# Built-in pattern table. Order matters: when a file matches more than one
# pattern, the first match wins. ``ROADMAP.md`` for example matches both the
# explicit ``ROADMAP.md`` rule and the broader ``*PLAN*.md`` glob — keeping
# the explicit rule first makes the description more accurate.
BUILT_IN_PATTERNS: tuple[Pattern, ...] = (
    # --- Doctrine sources ----------------------------------------------------
    Pattern("principles/*.md", "doctrine", "principles directory", "built-in"),
    Pattern("docs/principles/*.md", "doctrine", "docs/principles directory", "built-in"),
    Pattern("decisions/*.md", "doctrine", "decisions directory", "built-in"),
    Pattern("adr/*.md", "doctrine", "ADR directory", "built-in"),
    Pattern("docs/decisions/*.md", "doctrine", "docs/decisions directory", "built-in"),
    # --- Plan sources --------------------------------------------------------
    Pattern("plans/*.md", "plan", "plans directory", "built-in"),
    Pattern("ROADMAP.md", "plan", "roadmap doc", "built-in"),
    Pattern("NEXT_PHASE.md", "plan", "next phase doc", "built-in"),
    Pattern("agent/NEXT_PHASE.md", "plan", "agent-cluster next phase", "built-in"),
    Pattern("agent/*PLAN*.md", "plan", "agent-cluster plan docs", "built-in"),
    Pattern("*PLAN*.md", "plan", "plan-named docs", "built-in"),
    # Migration docs are typically transient plans (one-shot work to move
    # between systems). Conservative: we ONLY cover ``*MIGRATION*`` —
    # ``*SETUP*`` / ``*INTEGRATION*`` / ``*API*`` / ``*_THESIS.md`` are
    # too ambiguous and stay user-taught via ``.cortex/.discover.toml``.
    # Shipped/done migrations are demoted to reference by ``_looks_shipped``
    # (sigint's ``agent/COLLECTOR_MIGRATION.md`` carries ``Status: shipped``
    # markers and lands in reference, not plan), so adding the pattern
    # doesn't risk wrongly importing closed work as an active plan.
    Pattern("agent/*MIGRATION*.md", "plan", "agent-cluster migration docs", "built-in"),
    Pattern("*/*MIGRATION*.md", "plan", "subdir migration docs", "built-in"),
    Pattern("*MIGRATION*.md", "plan", "migration plan docs", "built-in"),
    # --- Reference (no auto-import — Cortex Journal is time-anchored) -------
    Pattern("CHANGELOG.md", "reference", "changelog", "built-in"),
    Pattern("journal/*.md", "reference", "journal directory", "built-in"),
    # --- Map references ------------------------------------------------------
    Pattern("README.md", "map_ref", "repo readme", "built-in"),
    Pattern("ARCHITECTURE.md", "map_ref", "architecture doc", "built-in"),
    Pattern("SYSTEM_ARCHITECTURE.md", "map_ref", "system architecture", "built-in"),
    Pattern("agent/SYSTEM_ARCHITECTURE.md", "map_ref", "agent system architecture", "built-in"),
    Pattern("SETUP.md", "map_ref", "setup doc", "built-in"),
    # Bounded-depth sub-dir READMEs (one level under repo root) — catches
    # ``dashboard/README.md``, ``cli/README.md`` etc. without descending into
    # ``vendor/foo/bar/README.md``.
    Pattern("*/README.md", "map_ref", "subsystem readmes", "built-in"),
)

# Directories that are NEVER scanned regardless of `.gitignore`. Most of
# these are language-toolchain caches that contain markdown documentation
# we explicitly do not want to absorb (the JS ``doctrine`` linter package
# under ``node_modules/`` is the canonical regression case).
#
# The second group (``.sentinel/``, ``.cortex/``, ``.claude/``, ``.github/``,
# ``.husky/``, ``.circleci/``, ``.devcontainer/``) is toolchain/agent
# configuration — files like ``.sentinel/lenses.md`` or ``.claude/loop.md``
# are config for OTHER tools, not project content for Cortex to absorb. The
# sigint dogfood regression that motivated this expansion: scan listed
# ``.sentinel/backlog.md``, ``.claude/loop.md``, and
# ``.github/pull_request_template.md`` as unknown candidates.
ALWAYS_SKIP: frozenset[str] = frozenset(
    {
        # --- Build / dependency / cache dirs -------------------------------
        "node_modules", ".build", ".swiftpm", "vendor", "dist", "target",
        "__pycache__", ".venv", "venv", ".tox", ".pytest_cache", ".ruff_cache",
        ".mypy_cache", "build", "out", "coverage", ".next", ".nuxt",
        "DerivedData", "Pods", ".gradle",
        # --- Toolchain / agent / VCS config dirs --------------------------
        # Each of these holds config for some other tool whose ``.md`` files
        # are not project content. ``.cortex`` itself is here so re-running
        # init never feeds its own scaffold back into the next scan.
        ".git", ".github", ".vscode", ".idea", ".husky", ".circleci",
        ".devcontainer", ".sentinel", ".cortex", ".claude",
    }
)

# Maximum depth (in path components, relative to project root) we descend.
# The ``*/README.md`` pattern bounds at depth 2; other patterns are explicit.
# We hard-cap at 4 to stay fast on monorepos and keep findings legible.
_MAX_SCAN_DEPTH = 4

# Patterns examined for the "looks shipped — demote to reference" heuristic.
# Conservative: we want false negatives (a shipped plan still imported, the
# user can fix it) over false positives (a real active plan demoted away).
_SHIPPED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*Status:\s*(?:shipped|done|complete|completed|cancelled)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#+\s*Done\b", re.IGNORECASE | re.MULTILINE),
)
# How many lines of head to read for status detection. 30 is enough to catch
# a frontmatter block + the first heading without ballooning IO.
_SHIPPED_PEEK_LINES = 30
# Threshold for "lots of strikethrough" — we consider 5+ instances of `~~…~~`
# in the head a signal that the doc is largely retrospective.
_STRIKETHROUGH_RE = re.compile(r"~~[^~\n]+~~")
_STRIKETHROUGH_THRESHOLD = 5

# "Looks load-bearing" heuristic for the unknown-file prompt. A markdown
# file qualifies when it's at the project root or one level deep AND it's
# either >=1KB OR has at least one H1 + one H2.
_LOAD_BEARING_MIN_BYTES = 1024
_H1_RE = re.compile(r"^#\s+\S", re.MULTILINE)
_H2_RE = re.compile(r"^##\s+\S", re.MULTILINE)
_GIT_CHECK_TIMEOUT_SECONDS = 5.0

# Files whose top-level instances are handled by other parts of the init
# pipeline — listing them under "unknown" would double-prompt the user.
# ``CLAUDE.md`` / ``AGENTS.md`` already get the Doctrine-0002 import-injection
# prompts (``--add-imports-claude``, ``--add-imports-agents``) handled in
# ``commands/init.py``; the unscoped-constraint heuristic in ``validation.py``
# also reports on their contents. Surfacing them as "unknown" on top of that
# is the sigint/vesper double-prompt regression we're fixing.
SCANNER_HANDLED_ELSEWHERE: frozenset[str] = frozenset({"CLAUDE.md", "AGENTS.md"})


@dataclass(frozen=True)
class Finding:
    """A single scan hit."""

    path: Path  # absolute path on disk
    relative: str  # POSIX-style path relative to project root, for display
    category: Category
    pattern: Pattern  # which rule fired
    demoted_from: Category | None = None  # set when a Plan is demoted to reference

    @property
    def is_demoted_plan(self) -> bool:
        return self.demoted_from == "plan" and self.category == "reference"


@dataclass
class SiblingSignals:
    """Touchstone / Sentinel detection — informational only, never gating."""

    has_codex_review_toml: bool = False
    has_codex_review_hook: bool = False
    touchstone_version: str | None = None
    has_touchstone_config: bool = False
    has_touchstone_manifest: bool = False
    sentinel_dir_present: bool = False
    sentinel_runs_count: int = 0


@dataclass
class ScanResult:
    """All findings from one ``scan_project()`` invocation."""

    project_root: Path
    findings: list[Finding] = field(default_factory=list)
    sibling_signals: SiblingSignals = field(default_factory=SiblingSignals)
    # Count of unscoped CLAUDE.md/AGENTS.md constraints (validation reuses
    # the existing ``check_claude_agents`` heuristic). This is a count for
    # the scan summary; the existing doctor warning surfaces the per-line
    # detail after init finishes.
    unscoped_constraint_count: int = 0
    # First unscoped-constraint location ("path.md:line") for inline display
    # in the scan summary. Lets the printer name the first hit instead of
    # only the count, saving a follow-up `cortex doctor` invocation for the
    # common single-warning case (Fix #6 from
    # plans/init-ux-fixes-from-touchstone). None when count is 0.
    unscoped_constraint_first_location: str | None = None

    def by_category(self, category: Category) -> list[Finding]:
        """Findings filtered by category, preserving discovery order."""
        return [f for f in self.findings if f.category == category]


# --- Pattern loading & glob matching ----------------------------------------


def _load_user_patterns(project_root: Path) -> list[Pattern]:
    """Parse ``.cortex/.discover.toml`` if present; never raise on bad input.

    A malformed file logs nothing (we don't want to gate ``cortex init`` on a
    user-edited TOML) — we silently fall back to built-ins. Schema is::

        [[pattern]]
        glob = "agent/*_THESIS.md"
        category = "doctrine"  # or plan / reference / map_ref / skip
        description = "investment theses"

    ``category = "skip"`` is special — it adds to ``ALWAYS_SKIP`` semantics for
    the next scan (we still let users opt out of a built-in match by teaching
    a skip rule on the same glob). The "skip" category is not surfaced as a
    finding; it just suppresses matches.
    """
    discover_toml = project_root / ".cortex" / ".discover.toml"
    if not discover_toml.exists():
        return []
    try:
        data = tomllib.loads(discover_toml.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        # Malformed or unreadable — surface nothing rather than crashing init.
        # Doctrine 0002: degrade gracefully. The user can re-author the file.
        return []

    raw_patterns = data.get("pattern", [])
    if not isinstance(raw_patterns, list):
        return []

    valid_categories: set[str] = {"doctrine", "plan", "reference", "map_ref", "skip"}
    patterns: list[Pattern] = []
    for entry in raw_patterns:
        if not isinstance(entry, dict):
            continue
        glob = entry.get("glob")
        category = entry.get("category")
        description = entry.get("description", "(user-taught)")
        if not isinstance(glob, str) or not isinstance(category, str):
            continue
        if category not in valid_categories:
            continue
        # Narrow `category` for the type checker — we just validated it above.
        narrowed: PatternCategory = category  # type: ignore[assignment]
        patterns.append(
            Pattern(
                glob=glob,
                category=narrowed,
                description=description if isinstance(description, str) else "(user-taught)",
                source=".cortex/.discover.toml",
            )
        )
    return patterns


def _git_ignored_paths(project_root: Path, candidates: list[Path]) -> set[Path]:
    """Ask git which of ``candidates`` are ignored. Empty when git is absent.

    Calls ``git -C <root> check-ignore --stdin -z`` once with the full list to
    keep this O(1) git invocations regardless of repo size. Anything git
    couldn't classify (not in a repo, errored, missing binary) is treated as
    not-ignored — we'd rather show a finding the user can dismiss than hide
    a real signal.
    """
    if not candidates:
        return set()
    git_path = shutil.which("git")
    if git_path is None:
        return set()
    if not (project_root / ".git").exists():
        return set()
    payload = "\0".join(str(p.relative_to(project_root)) for p in candidates) + "\0"
    try:
        completed = subprocess.run(
            [git_path, "-C", str(project_root), "check-ignore", "--stdin", "-z"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=_GIT_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return set()
    # `check-ignore` exits 0 when at least one path matched, 1 when none did,
    # 128 on usage errors. Treat any non-fatal exit as "use what stdout gave".
    if completed.returncode not in (0, 1):
        return set()
    ignored: set[Path] = set()
    for raw in completed.stdout.split("\0"):
        if not raw:
            continue
        ignored.add((project_root / raw).resolve())
    return ignored


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """Convert a simple glob to a regex anchored to the full relative path.

    We support ``*`` (any chars within one path segment), ``?`` (single char),
    and the literal ``/`` separator. We deliberately do NOT support ``**`` —
    every built-in pattern is bounded depth, and bounded depth keeps scans
    snappy on monorepos. User-taught patterns inherit the same restriction.
    """
    parts: list[str] = []
    for ch in glob:
        if ch == "*":
            parts.append(r"[^/]*")
        elif ch == "?":
            parts.append(r"[^/]")
        elif ch in r".+(){}[]^$|\\":
            parts.append("\\" + ch)
        else:
            parts.append(ch)
    return re.compile("^" + "".join(parts) + "$")


def _matches_pattern(relative: str, pattern: Pattern) -> bool:
    return bool(_glob_to_regex(pattern.glob).match(relative))


# --- Per-finding heuristics --------------------------------------------------


def _looks_shipped(path: Path) -> bool:
    """Heuristic: does this file's first ~30 lines look like a shipped doc?"""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head_lines = []
            for _ in range(_SHIPPED_PEEK_LINES):
                line = fh.readline()
                if not line:
                    break
                head_lines.append(line)
    except OSError:
        return False
    head = "".join(head_lines)
    if any(rx.search(head) for rx in _SHIPPED_PATTERNS):
        return True
    return len(_STRIKETHROUGH_RE.findall(head)) >= _STRIKETHROUGH_THRESHOLD


def _looks_load_bearing(path: Path, project_root: Path) -> bool:
    """Filter for the unknown-file prompt: top-level or one-deep markdown,
    >= 1KB or showing H1+H2 structure."""
    if path.suffix != ".md":
        return False
    rel = path.relative_to(project_root)
    if len(rel.parts) > 2:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size >= _LOAD_BEARING_MIN_BYTES:
        return True
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(_H1_RE.search(text) and _H2_RE.search(text))


# --- Sibling / Sentinel detection (informational) ----------------------------


def _detect_sibling_signals(project_root: Path) -> SiblingSignals:
    """Surface Touchstone / Sentinel presence — never gates anything."""
    sig = SiblingSignals()

    # Touchstone surface: configs at the project root.
    sig.has_codex_review_toml = (project_root / ".codex-review.toml").is_file()
    sig.has_touchstone_config = (project_root / ".touchstone-config").is_file()
    sig.has_touchstone_manifest = (project_root / ".touchstone-manifest").is_file()
    version_file = project_root / ".touchstone-version"
    if version_file.is_file():
        try:
            sig.touchstone_version = version_file.read_text().strip() or None
        except OSError:
            sig.touchstone_version = None

    # `.pre-commit-config.yaml` mentioning `codex-review` is a strong signal
    # the project already has the Touchstone hook wired up (we don't parse
    # YAML — a substring match is enough for an informational note).
    pcc = project_root / ".pre-commit-config.yaml"
    if pcc.is_file():
        try:
            sig.has_codex_review_hook = "codex-review" in pcc.read_text()
        except OSError:
            sig.has_codex_review_hook = False

    sentinel_dir = project_root / ".sentinel"
    if sentinel_dir.is_dir():
        sig.sentinel_dir_present = True
        runs = sentinel_dir / "runs"
        if runs.is_dir():
            try:
                sig.sentinel_runs_count = sum(
                    1 for entry in runs.iterdir() if entry.is_file() and entry.suffix == ".md"
                )
            except OSError:
                sig.sentinel_runs_count = 0

    return sig


def _count_unscoped_constraints(project_root: Path) -> tuple[int, str | None]:
    """Return (count, first-location-or-None) from the existing doctor heuristic.

    The location is a short ``"path.md:line"`` string extracted from the
    first issue's message (which already has shape ``"path.md:line: ..."``
    per the doctor's existing rendering convention). When no issues are
    found, returns ``(0, None)``. Imported lazily so the scan module
    stays import-cheap when consumers don't need the count.
    """
    from cortex.validation import check_claude_agents

    issues = check_claude_agents(project_root)
    if not issues:
        return 0, None
    # Issue.message is already "<path>:<line>: <text>"; take the prefix.
    first = issues[0].message
    colon_two = first.find(":", first.find(":") + 1)
    location = first[:colon_two] if colon_two != -1 else issues[0].path
    return len(issues), location


# --- Filesystem walk --------------------------------------------------------


def _iter_candidate_files(project_root: Path) -> list[Path]:
    """Walk the project tree, skipping ``ALWAYS_SKIP`` and respecting depth.

    We collect every regular file (not just ``.md``) so that pattern matching
    can cover non-markdown signals later (e.g. ``CHANGELOG.md`` is markdown
    but ``.touchstone-version`` is plain text and is detected separately
    above). Directory pruning happens during the walk so we never recurse
    into ``node_modules/`` even if a pattern would otherwise match.
    """
    candidates: list[Path] = []
    project_root = project_root.resolve()
    stack: list[Path] = [project_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    # Don't follow symlinks — they're a common way to escape
                    # the project root and recurse into surprising places.
                    continue
                if entry.is_dir():
                    if entry.name in ALWAYS_SKIP:
                        continue
                    rel_depth = len(entry.relative_to(project_root).parts)
                    if rel_depth >= _MAX_SCAN_DEPTH:
                        continue
                    stack.append(entry)
                elif entry.is_file():
                    candidates.append(entry)
            except OSError:
                # File disappeared between iterdir() and is_dir() (race), or
                # we lack permission. Skip silently — the next scan will pick
                # it up if the situation resolves.
                continue
    return candidates


# --- Public API -------------------------------------------------------------


def scan_project(project_root: Path) -> ScanResult:
    """Scan ``project_root`` for Cortex-relevant existing structure.

    Runs synchronously and is safe on large repos because we (a) skip the
    expensive transient directories, (b) bound depth, and (c) only read
    file *bodies* for the small set of Plan candidates needing the shipped
    check and the unknown candidates needing the load-bearing check.
    """
    project_root = project_root.resolve()
    result = ScanResult(project_root=project_root)

    # Gather every candidate file under the depth cap, pruned of ALWAYS_SKIP.
    candidates = _iter_candidate_files(project_root)

    # Ask git which ones are ignored — single shell-out for the whole list.
    ignored = _git_ignored_paths(project_root, candidates)

    # Detect sibling integrations early so the main scan loop can reclassify
    # Touchstone-managed Doctrine candidates (``principles/*.md`` etc.) as
    # ``touchstone_managed`` instead of importing them as stub-pointer
    # Doctrine entries that displace real content in the manifest budget.
    # The result is also stored on `result.sibling_signals` below for the
    # scan summary printer; we just need it earlier here.
    sibling_signals = _detect_sibling_signals(project_root)
    touchstone_present = (
        sibling_signals.has_touchstone_config
        or sibling_signals.has_touchstone_manifest
        or sibling_signals.touchstone_version is not None
    )

    # Merge built-in + user-taught patterns; user-taught come second so the
    # built-in description wins when both match. (User patterns can still
    # *add* matches that built-ins missed, which is the common case.)
    all_patterns: list[Pattern] = list(BUILT_IN_PATTERNS) + _load_user_patterns(project_root)
    skip_patterns = [p for p in all_patterns if p.category == "skip"]
    match_patterns = [p for p in all_patterns if p.category != "skip"]
    # User-taught doctrine patterns — used to override the built-in
    # meta_doc filter (Fix #3). When a built-in doctrine pattern matches
    # a README.md file we'd normally reclassify as meta_doc, but if a
    # user-taught doctrine pattern ALSO matches, the user's explicit
    # instruction wins.
    user_doctrine_patterns = [
        p for p in _load_user_patterns(project_root) if p.category == "doctrine"
    ]

    classified_paths: set[Path] = set()
    for path in candidates:
        if path.resolve() in ignored:
            continue
        rel_posix = path.relative_to(project_root).as_posix()

        # User-taught skip rules win — we never surface a finding the user
        # explicitly asked us to drop.
        if any(_matches_pattern(rel_posix, sp) for sp in skip_patterns):
            classified_paths.add(path)
            continue

        for pattern in match_patterns:
            if not _matches_pattern(rel_posix, pattern):
                continue
            # `match_patterns` filters out the "skip" sentinel above, so any
            # pattern reaching here has one of the six real categories. We
            # narrow explicitly so mypy sees the same invariant.
            assert pattern.category != "skip"
            category: Category = pattern.category
            demoted_from: Category | None = None
            if category == "plan" and _looks_shipped(path):
                demoted_from = "plan"
                category = "reference"
            # Touchstone-managed Doctrine candidates get reclassified so
            # they're displayed informationally but not imported. Importing
            # them as Doctrine creates stub-pointer entries that displace
            # real Doctrine in the session-start manifest (the conductor-
            # case-study failure mode applied to the install path).
            if (
                category == "doctrine"
                and touchstone_present
                and pattern.glob in _TOUCHSTONE_MANAGED_DOCTRINE_GLOBS
            ):
                category = "touchstone_managed"
            # README files matched by built-in doctrine patterns get
            # reclassified to meta_doc (orientation prose, not doctrine
            # content). User-taught patterns from .discover.toml bypass
            # this filter — if the user has an explicit doctrine pattern
            # that matches the same path, their instruction wins.
            elif (
                category == "doctrine"
                and pattern.source == "built-in"
                and path.name.lower() in _META_DOC_BASENAMES
                and not any(_matches_pattern(rel_posix, p) for p in user_doctrine_patterns)
            ):
                category = "meta_doc"
            result.findings.append(
                Finding(
                    path=path,
                    relative=rel_posix,
                    category=category,
                    pattern=pattern,
                    demoted_from=demoted_from,
                )
            )
            classified_paths.add(path)
            break

    # Surface unknown markdown files that look load-bearing — these are the
    # "do you want to teach me?" candidates. We don't classify them; we just
    # note them so the wizard can ask.
    for path in candidates:
        if path in classified_paths:
            continue
        if path.resolve() in ignored:
            continue
        # Top-level CLAUDE.md / AGENTS.md are handled by the
        # import-injection + unscoped-constraint flows. Surfacing them as
        # "unknown" double-prompts the user (sigint/vesper regression).
        rel_for_skip = path.relative_to(project_root)
        if (
            len(rel_for_skip.parts) == 1
            and rel_for_skip.parts[0] in SCANNER_HANDLED_ELSEWHERE
        ):
            continue
        if not _looks_load_bearing(path, project_root):
            continue
        rel_posix = path.relative_to(project_root).as_posix()
        result.findings.append(
            Finding(
                path=path,
                relative=rel_posix,
                category="unknown",
                pattern=Pattern(
                    glob=rel_posix,
                    category="unknown",
                    description="unrecognized markdown",
                    source="(unknown)",
                ),
            )
        )

    # Stable display ordering: by category bucket, then by path. (Categories
    # below match the printer's section order.)
    bucket = {
        "doctrine": 0,
        "touchstone_managed": 1,  # Surfaced right after Doctrine since it's
        # the "what would have been Doctrine if we hadn't detected
        # Touchstone" bucket.
        "meta_doc": 2,  # Same shape — "what would have been Doctrine if it
        # weren't a meta-doc filename".
        "plan": 3,
        "reference": 4,
        "map_ref": 5,
        "unknown": 6,
    }
    result.findings.sort(key=lambda f: (bucket.get(f.category, 99), f.relative))

    # Sibling + Sentinel signals — never gating, always informational.
    # We detected these earlier (above) for the touchstone reclassification;
    # store on the result so the printer can reuse them.
    result.sibling_signals = sibling_signals

    # Constraint count + first-issue location — uses the existing doctor
    # heuristic so the number init prints matches what `cortex doctor` will
    # report after init.
    count, first_loc = _count_unscoped_constraints(project_root)
    result.unscoped_constraint_count = count
    result.unscoped_constraint_first_location = first_loc

    return result


def append_user_pattern(
    project_root: Path,
    *,
    glob: str,
    category: PatternCategory,
    description: str,
) -> None:
    """Append a pattern entry to ``.cortex/.discover.toml`` (creating it if absent).

    We intentionally write a hand-readable TOML block (not parsed-then-rewritten)
    so the user's existing comments and entries are preserved verbatim. The
    file is treated as append-only by Cortex; users may edit it freely between
    runs.
    """
    discover_toml = project_root / ".cortex" / ".discover.toml"
    discover_toml.parent.mkdir(parents=True, exist_ok=True)
    block = (
        "\n[[pattern]]\n"
        f'glob = "{glob}"\n'
        f'category = "{category}"\n'
        f'description = "{description}"\n'
    )
    if discover_toml.exists():
        existing = discover_toml.read_text()
        # Idempotency: if a pattern with the same glob+category already exists
        # we do nothing, so re-running init doesn't duplicate teachings.
        marker = f'glob = "{glob}"'
        if marker in existing:
            # Confirm the matching block is the same category — otherwise we
            # let the user resolve the conflict by hand on next edit.
            return
        if not existing.endswith("\n"):
            existing += "\n"
        discover_toml.write_text(existing + block)
    else:
        header = (
            "# Cortex scan patterns — taught by `cortex init`.\n"
            "# Each `[[pattern]]` entry teaches the scanner one new file shape.\n"
            "# Edit by hand or let init append more during interactive runs.\n"
        )
        discover_toml.write_text(header + block)
