"""Assemble the committed Stage 0 eval corpus from real history (cortex#339).

Run from the repository root (requires ``git`` history and the ``gh`` CLI):

    uv run python tests/fixtures/hosted_eval/corpus_assembly.py

The script freezes real merged-PR diffs and real commit-range diffs into
``tests/fixtures/hosted_eval/corpus/`` via ``cortex.hosted.corpus_builder``.
Decision context cites real repository documents pinned at the fixture's
base SHA, with offsets computed against the pinned content. Rebuilding with
the same inputs is byte-identical, so re-running this script is the
reconciliation check for the committed corpus.

Three source classes feed the corpus:

- **cortex history** — merged PRs and commit ranges from this repository.
- **sibling-repo history** — merged PRs and commit ranges from
  ``henrymodisett/vesper``, ``outriderintel/vanguard``, and
  ``outriderintel/outrider``. Rebuilding these requires local checkouts of
  the siblings (``$CORTEX_SIBLINGS_ROOT``, default ``~/repos``) for pinned
  document content, plus ``gh`` access to the repos for the PR-backed diffs.
- **simlab promotions** — deterministic scenario triples from
  ``tests/simlab/scenarios`` converted into corpus fixtures through the
  shipped materialize → derive → ``build_scenario_fixture`` pipeline
  (``metadata.source = "simlab"``). Same spec twice yields identical bytes.

All fixtures are written ungraded (``labels`` empty); grading happens through
the cortex#333 hand-labeling workflow, never by treating model output as
ground truth.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from cortex.hosted.corpus_builder import (
    SIMLAB_SOURCE,
    CommandRunner,
    build_document_span,
    build_fixture_from_commit_range,
    build_fixture_from_pr,
    build_synthetic_fixture,
    run_command,
    write_fixture,
)
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    ExpectedFinding,
    FindingClass,
    FixtureDecision,
    FixtureDiff,
    FixtureScope,
    FixtureSourceSpan,
)
from cortex.hosted.scopes import ScopeType

REPO = "autumngarage/cortex"
CORPUS_DIR = Path(__file__).parent / "corpus"

# Real history pins (full SHAs; immutable once merged).
PR483_BASE = "4599cff3b9aa36d9395e5ea2c138a07c0b1c707f"
PR188_BASE = "fd62709f23e515cc7359fdcf1dc5321277e24715"
PR493_BASE = "17f23e3b55dd2f8eead711c962a905c1422f365a"
PR493_CONSOLIDATED_HEAD = "bf0e32f7e8fce86e020d6e3f0fa454db3eea6d0f"
PR495_MERGE_BASE = "cf5bf9f9cbc29db89576d00e97de358c1d5999dd"
PR495_HEAD_COMMIT = "28d3bbcd3755544008f886e2bdee2fd2574bfaf1"
PR98_BASE = "0f787c7e5d75e0df2c864c6f4205815b55eae7cb"
PR98_MERGE = "c247cfe1727549a2b8b2f592a7f0debe866ac0fb"
MAIN_PIN = "a1f72f9b06e6cf3af43b1048082324041c4c52be"

DOCTRINE_0002_PATH = ".cortex/doctrine/0002-compose-by-file-contract-not-code.md"
SPEC_PATH = "SPEC.md"
PROTOCOL_PATH = ".cortex/protocol.md"
PR_MERGED_TEMPLATE_PATH = ".cortex/templates/journal/pr-merged.md"
CLAUDE_MD_PATH = "CLAUDE.md"

SYNTHETIC_HEAD_SHA = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"


def _pinned_span_for(
    repo: str,
    pin_sha: str,
    path: str,
    excerpt: str,
    *,
    runner: CommandRunner = run_command,
) -> tuple[FixtureSourceSpan, str]:
    """Freeze an excerpt of a repo document at a pinned commit.

    Returns the span plus the document's last-change timestamp at that pin,
    used as the decision's ``source_timestamp``. ``runner`` lets sibling-repo
    builders route ``git`` invocations into their own checkouts.
    """

    content = runner(["git", "show", f"{pin_sha}:{path}"])
    span = build_document_span(
        document_content=content,
        excerpt=excerpt,
        permalink=f"https://github.com/{repo}/blob/{pin_sha}/{path}",
    )
    timestamp = runner(["git", "log", "-1", "--format=%cI", pin_sha, "--", path]).strip()
    if not timestamp:
        raise RuntimeError(f"no last-change timestamp for {path} at {pin_sha}")
    return span, timestamp


def _pinned_span(pin_sha: str, path: str, excerpt: str) -> tuple[FixtureSourceSpan, str]:
    """Freeze an excerpt of a cortex document at a pinned commit."""

    return _pinned_span_for(REPO, pin_sha, path, excerpt)


def _standalone_boundary_decision(pin_sha: str) -> FixtureDecision:
    span, timestamp = _pinned_span(
        pin_sha,
        DOCTRINE_0002_PATH,
        "Cortex does not import Sentinel or Touchstone Python/bash code; "
        "it does not subprocess into their CLIs for functional output.",
    )
    return FixtureDecision(
        decision_id="standalone-boundary-doctrine-0002",
        decision_text=(
            "Cortex composes with Touchstone and Sentinel exclusively through the "
            ".cortex/ filesystem layout; it never imports sibling quartet code and "
            "never subprocesses into their CLIs for functional output (Doctrine 0002)."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(span,),
        scopes=(FixtureScope(scope_type=ScopeType.GLOB, value="src/cortex/**"),),
    )


def build_standalone_boundary_respected() -> EvalFixture:
    """PR #483 added hosted visibility boundaries with stdlib-only code —
    it respected Doctrine 0002, so no finding is expected (negative case)."""

    return build_fixture_from_pr(
        REPO,
        483,
        fixture_id="standalone-boundary-respected-001",
        decisions=(_standalone_boundary_decision(PR483_BASE),),
        expected_findings=(),
        extra_metadata={
            "scenario": "hosted-visibility-pr-respects-standalone-boundary",
            "notes": (
                "Negative case: the diff adds hosted visibility enforcement using "
                "only stdlib imports, consistent with Doctrine 0002."
            ),
        },
    )


def build_spec_version_drift() -> EvalFixture:
    """PR #188 finalized SPEC 1.1.0 but left .cortex/SPEC_VERSION at 0.5.0 —
    the drift stayed on main until PR #493 fixed it (real staleness case)."""

    scaffold_span, _ = _pinned_span(
        PR188_BASE,
        SPEC_PATH,
        "SPEC_VERSION              # the Cortex spec version the project conforms to",
    )
    contract_span, timestamp = _pinned_span(
        PR188_BASE,
        SPEC_PATH,
        "**`SPEC_VERSION`** — a single line, e.g. `0.3.1-dev`. Tools that read "
        "`.cortex/` must check this and bail or warn on unknown major versions.",
    )
    decision = FixtureDecision(
        decision_id="spec-version-declared",
        decision_text=(
            "Generated .cortex/ scaffolds declare the spec version they conform to: "
            ".cortex/SPEC_VERSION is the single-line declaration tools check, so it "
            "must track the released SPEC.md version whenever the spec version changes."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(scaffold_span, contract_span),
        scopes=(
            FixtureScope(scope_type=ScopeType.PATH, value=".cortex/SPEC_VERSION"),
            FixtureScope(scope_type=ScopeType.PATH, value="SPEC.md"),
        ),
    )
    finding = ExpectedFinding(
        finding_id="finding-spec-version-stale",
        finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
        decision_id="spec-version-declared",
        cited_span_hashes=(scaffold_span.span_hash, contract_span.span_hash),
        summary=(
            "The diff finalizes SPEC.md at 1.1.0 and extends SUPPORTED_SPEC_VERSIONS, "
            "but omits the matching .cortex/SPEC_VERSION bump, leaving the repo's own "
            "scaffold declaring 0.5.0 — the drift persisted until PR #493."
        ),
        suggested_repair=(
            "Bump .cortex/SPEC_VERSION to 1.1.0 in the same diff that finalizes the "
            "spec version."
        ),
    )
    return build_fixture_from_pr(
        REPO,
        188,
        fixture_id="spec-version-drift-001",
        decisions=(decision,),
        expected_findings=(finding,),
        extra_metadata={
            "scenario": "spec-finalized-without-scaffold-version-bump",
            "notes": (
                "Real staleness case: .cortex/SPEC_VERSION stayed at 0.5.0 from this "
                "merge (2026-05-07) until PR #493 corrected it to 1.1.0 (2026-06-09)."
            ),
        },
    )


def build_consolidated_journal() -> EvalFixture:
    """PR #493's intermediate head added consolidated multi-merge pr-merged
    entries; the final version split them into one entry per merge."""

    title_span, _ = _pinned_span(
        PR493_BASE,
        PR_MERGED_TEMPLATE_PATH,
        "# PR #{{ nnn }} merged — {{ short title }}",
    )
    merge_commit_span, timestamp = _pinned_span(
        PR493_BASE,
        PR_MERGED_TEMPLATE_PATH,
        "**Merge-commit:** {{ full sha }}",
    )
    decision = FixtureDecision(
        decision_id="pr-merged-entry-per-merge",
        decision_text=(
            "Each default-branch merge gets exactly one pr-merged Journal entry whose "
            "header names that single PR and carries a singular Merge-commit sha; "
            "consolidated entries that batch several merges do not fit the template."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(title_span, merge_commit_span),
        scopes=(FixtureScope(scope_type=ScopeType.GLOB, value=".cortex/journal/**"),),
    )
    finding = ExpectedFinding(
        finding_id="finding-consolidated-pr-merged-entries",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="pr-merged-entry-per-merge",
        cited_span_hashes=(title_span.span_hash, merge_commit_span.span_hash),
        summary=(
            "The diff adds two consolidated pr-merged entries — a May 26-Jun 08 "
            "backfill and a hosted-substrate wave entry — each covering many merges "
            "instead of one entry per merged PR with a singular Merge-commit sha."
        ),
        suggested_repair=(
            "Split the consolidated entries into one pr-merged entry per merge, each "
            "with its own Merge-commit sha (the shape PR #493's final version shipped)."
        ),
    )
    return build_fixture_from_commit_range(
        REPO,
        PR493_BASE,
        PR493_CONSOLIDATED_HEAD,
        fixture_id="consolidated-journal-entries-001",
        decisions=(decision,),
        expected_findings=(finding,),
        paths=(
            ".cortex/journal/2026-06-09-pr-merged-backfill-may26-jun08.md",
            ".cortex/journal/2026-06-09-pr-merged-hosted-substrate-wave.md",
        ),
        pr_number=493,
        extra_metadata={
            "scenario": "consolidated-pr-merged-journal-entries",
            "notes": (
                "Intermediate head of PR #493 before commit 6af6ed5 split the "
                "consolidated T1.9 backfill into per-PR entries with singular "
                "Merge-commit lines."
            ),
        },
    )


def build_journal_entry_deletion() -> EvalFixture:
    """PR #495's stale-base diff deleted a Journal entry that newer main had
    added — the append-only violation the stale-base review flagged."""

    span, timestamp = _pinned_span(
        PR495_MERGE_BASE,
        PROTOCOL_PATH,
        "Never edit an existing Journal entry in place. New entry per event. If new "
        "information changes an old conclusion, write a new entry that cites and "
        "revises the old one; the old one stays unchanged.",
    )
    decision = FixtureDecision(
        decision_id="journal-append-only",
        decision_text=(
            "The Journal is append-only (protocol.md section 4.1): entries are never "
            "edited in place or deleted; corrections happen via new entries that cite "
            "and revise the old ones."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(span,),
        scopes=(FixtureScope(scope_type=ScopeType.GLOB, value=".cortex/journal/**"),),
    )
    finding = ExpectedFinding(
        finding_id="finding-journal-entry-deleted",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="journal-append-only",
        cited_span_hashes=(span.span_hash,),
        summary=(
            "Relative to the merge-time base, the diff deletes "
            ".cortex/journal/2026-06-09-pr-merged-pr493.md — a Journal entry deletion "
            "the append-only invariant forbids. The deletion was a stale-branch "
            "artifact (the branch forked before PR #494 landed the entry) that the "
            "review on PR #495 flagged."
        ),
        suggested_repair=(
            "Merge or rebase onto current main so existing Journal entries are "
            "preserved; never ship a diff that removes a Journal entry."
        ),
    )
    return build_fixture_from_commit_range(
        REPO,
        PR495_MERGE_BASE,
        PR495_HEAD_COMMIT,
        fixture_id="journal-entry-deletion-001",
        decisions=(decision,),
        expected_findings=(finding,),
        paths=(".cortex/journal/",),
        pr_number=495,
        extra_metadata={
            "scenario": "stale-base-journal-entry-deletion",
            "notes": (
                "Real stale-base artifact: PR #495's commit 28d3bbc predates PR #494's "
                "merge (cf5bf9f), so the review diff showed the pr493 journal entry as "
                "deleted. Resolved by merging main (3c846e0) before squash-merge."
            ),
        },
    )


def build_touchstone_managed_principles() -> EvalFixture:
    """PR #98 updated principles/ through a proper touchstone sync — it
    respected the touchstone-managed-files rule (negative case)."""

    span, timestamp = _pinned_span(
        PR98_BASE,
        CLAUDE_MD_PATH,
        "Touchstone-managed files live in `principles/` and `scripts/` and are "
        "synced via `touchstone update`.",
    )
    decision = FixtureDecision(
        decision_id="principles-touchstone-managed",
        decision_text=(
            "Files under principles/ and scripts/ are Touchstone-managed: they change "
            "only via touchstone update sync, never by hand-edits in this repo."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(span,),
        scopes=(FixtureScope(scope_type=ScopeType.GLOB, value="principles/**"),),
    )
    return build_fixture_from_commit_range(
        REPO,
        PR98_BASE,
        PR98_MERGE,
        fixture_id="touchstone-managed-principles-001",
        decisions=(decision,),
        expected_findings=(),
        paths=("principles/",),
        pr_number=98,
        extra_metadata={
            "scenario": "touchstone-sync-updates-principles",
            "notes": (
                "Negative case: the principles/ delta came from the 'chore: update "
                "touchstone to 2.4.0' sync, which is exactly how Touchstone-managed "
                "files are supposed to change."
            ),
        },
    )


def build_standalone_boundary_synthetic() -> EvalFixture:
    """Hypothetical quartet-import diff vs Doctrine 0002 — the one
    clearly-marked synthetic fixture in the corpus."""

    patch = (
        "diff --git a/src/cortex/hosted/derive_worker.py "
        "b/src/cortex/hosted/derive_worker.py\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "--- /dev/null\n"
        "+++ b/src/cortex/hosted/derive_worker.py\n"
        "@@ -0,0 +1,11 @@\n"
        '+"""Derive worker that reuses Sentinel\'s provider abstraction."""\n'
        "+\n"
        "+from sentinel.providers import ProviderRouter\n"
        "+from touchstone.hooks import run_pre_merge\n"
        "+\n"
        "+\n"
        "+def synthesize_decision_summary(prompt: str) -> str:\n"
        '+    router = ProviderRouter.from_env()\n'
        '+    run_pre_merge("cortex-derive")\n'
        "+    return router.complete(prompt)\n"
    )
    diff = FixtureDiff(
        repo_owner="autumngarage",
        repo_name="cortex",
        base_sha=MAIN_PIN,
        head_sha=SYNTHETIC_HEAD_SHA,
        patch=patch,
        changed_paths=("src/cortex/hosted/derive_worker.py",),
        symbols=("ProviderRouter", "run_pre_merge"),
    )
    decision = _standalone_boundary_decision(MAIN_PIN)
    finding = ExpectedFinding(
        finding_id="finding-quartet-import",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="standalone-boundary-doctrine-0002",
        cited_span_hashes=(decision.spans[0].span_hash,),
        summary=(
            "The diff imports sentinel.providers and touchstone.hooks into a Cortex "
            "module, coupling Cortex to sibling quartet code in direct contradiction "
            "of Doctrine 0002's compose-by-file-contract boundary."
        ),
        suggested_repair=(
            "Drop the quartet imports; shell out to `claude -p` directly for "
            "synthesis and integrate with siblings through .cortex/ file contracts."
        ),
    )
    return build_synthetic_fixture(
        fixture_id="standalone-boundary-violation-synthetic-001",
        diff=diff,
        decisions=(decision,),
        expected_findings=(finding,),
        extra_metadata={
            "scenario": "hypothetical-quartet-import",
            "notes": (
                "Synthetic diff (head sha is a placeholder; the patch never existed "
                "in history) because no real cortex PR has violated Doctrine 0002. "
                "The cited decision spans are real Doctrine 0002 content."
            ),
        },
    )


# ---------------------------------------------------------------------------
# Sibling-repo fixtures (cortex#339: items drawn from more than one repo)
# ---------------------------------------------------------------------------

VESPER_REPO = "henrymodisett/vesper"
VANGUARD_REPO = "outriderintel/vanguard"
OUTRIDER_REPO = "outriderintel/outrider"

SIBLINGS_ROOT = Path(os.environ.get("CORTEX_SIBLINGS_ROOT", str(Path.home() / "repos")))

# Sibling history pins (full SHAs; immutable once merged).
VESPER_PR308_BASE = "087a9d2c9a1517e242c6a7fdc76db0e35c193f9b"
VESPER_PR385_BASE = "3937e793918c08bf15c42dc790f74b14834aaf59"
VANGUARD_PR559_BASE = "85133fabf82f218627dbdfde098a52bacff623ee"
OUTRIDER_PR541_BASE = "dcd6c2c98ad3554dd61a086eb65b30f19720298d"
OUTRIDER_PR541_SQUASH = "72c747d3188b08093885faafc456c5c2d68c1d32"

VESPER_CLAUDE_MD = "CLAUDE.md"
OUTRIDER_CLAUDE_MD = "CLAUDE.md"
OUTRIDER_CONTRACT_PATH = "docs/CONTRACT.md"
VANGUARD_DOCTRINE_0002_PATH = ".cortex/doctrine/0002-portfolio-decision-boundary.md"

_VESPER_DESIGN_SYSTEM_INTRO = (
    "All UI must use the existing design system in `Sources/Design/DesignSystem.swift`. "
    "Never hardcode:"
)
_VESPER_ICONS_RULE = (
    "- **Icons**: Use `LucideImage(.iconName, size: ...)` and `LucideLabel(...)` from "
    "`Sources/Design/LucideIcon.swift` — never `Image(systemName:)` or "
    "`Label(_, systemImage:)`. Sizes come from "
    "`Design.Typography.iconBaseSize/iconSmallSize/iconXSmallSize/iconMicroSize`. "
    "Lucide is the only icon vocabulary; SF Symbols are not used."
)
_VESPER_BUTTON_STYLES_RULE = (
    "- **Button styles**: Use `IconButtonStyle`, `GhostButtonStyle`, `SurfaceButtonStyle` "
    "from `ButtonStyles.swift`"
)


def _sibling_runner(repo: str) -> CommandRunner:
    """Route ``git`` calls into the sibling checkout; ``gh`` passes through.

    ``gh`` commands carry ``--repo`` and are cwd-independent; ``git`` commands
    need the sibling's object store, so they run with ``-C <checkout>``. The
    checkout location comes from ``$CORTEX_SIBLINGS_ROOT`` (default
    ``~/repos``); a missing checkout fails closed in ``run_command``.
    """

    repo_dir = SIBLINGS_ROOT / repo.partition("/")[2]

    def runner(argv: Sequence[str]) -> str:
        if argv and argv[0] == "git":
            return run_command(["git", "-C", str(repo_dir), *argv[1:]])
        return run_command(list(argv))

    return runner


def _vesper_design_system_decision(pin_sha: str, *, runner: CommandRunner) -> FixtureDecision:
    intro_span, _ = _pinned_span_for(
        VESPER_REPO, pin_sha, VESPER_CLAUDE_MD, _VESPER_DESIGN_SYSTEM_INTRO, runner=runner
    )
    icons_span, _ = _pinned_span_for(
        VESPER_REPO, pin_sha, VESPER_CLAUDE_MD, _VESPER_ICONS_RULE, runner=runner
    )
    buttons_span, timestamp = _pinned_span_for(
        VESPER_REPO, pin_sha, VESPER_CLAUDE_MD, _VESPER_BUTTON_STYLES_RULE, runner=runner
    )
    return FixtureDecision(
        decision_id="vesper-design-system-tokens",
        decision_text=(
            "All Vesper UI goes through the design system: tokens from "
            "Sources/Design/DesignSystem.swift, icons exclusively via LucideImage/"
            "LucideLabel from Sources/Design/LucideIcon.swift (never Image(systemName:); "
            "Lucide is the only icon vocabulary, SF Symbols are not used), and button "
            "styles from ButtonStyles.swift."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(intro_span, icons_span, buttons_span),
        scopes=(
            FixtureScope(scope_type=ScopeType.PATH, value="Sources/Design/LucideIcon.swift"),
            FixtureScope(scope_type=ScopeType.PATH, value="Sources/Design/ButtonStyles.swift"),
            FixtureScope(scope_type=ScopeType.PATH, value="Sources/Design/DesignSystem.swift"),
            FixtureScope(scope_type=ScopeType.GLOB, value="Sources/**"),
        ),
    )


def build_vesper_lucide_icon_violation() -> EvalFixture:
    """vesper PR #308 moved the AI picker to native controls and introduced
    six ``Image(systemName:)`` call sites — contradicting the still-standing
    Lucide-only icon-vocabulary decision in CLAUDE.md (real positive case)."""

    runner = _sibling_runner(VESPER_REPO)
    decision = _vesper_design_system_decision(VESPER_PR308_BASE, runner=runner)
    icons_span_hash = decision.spans[1].span_hash
    finding = ExpectedFinding(
        finding_id="finding-sf-symbols-vocabulary",
        finding_class=FindingClass.CONTRADICTS_PRIOR_DECISION,
        decision_id="vesper-design-system-tokens",
        cited_span_hashes=(icons_span_hash,),
        summary=(
            "The diff replaces Lucide-based picker controls with native ones and adds "
            "Image(systemName:) call sites in AIInvitePopover.swift and "
            "WindowWatchButton.swift, contradicting the confirmed decision that Lucide "
            "is the only icon vocabulary and SF Symbols are not used."
        ),
        suggested_repair=(
            "Route the new glyphs through LucideImage/LucideLabel, or supersede the "
            "Lucide-only icon-vocabulary decision in CLAUDE.md in the same change."
        ),
    )
    return build_fixture_from_pr(
        VESPER_REPO,
        308,
        fixture_id="vesper-lucide-icon-vocabulary-001",
        decisions=(decision,),
        expected_findings=(finding,),
        extra_metadata={
            "scenario": "native-picker-controls-reintroduce-sf-symbols",
            "notes": (
                "Real sibling-repo positive case: the PR deliberately adopted native "
                "AppKit-style controls, but the Lucide-only decision was never "
                "superseded — the advisory reviewer should cite the contradiction and "
                "let the human decide."
            ),
        },
        runner=runner,
    )


def build_vesper_workspace_sheet_respected() -> EvalFixture:
    """vesper PR #385 added a new sheet built entirely from design-system
    tokens, theme colors, LucideLabel, and vesper button styles — the
    design-system decision respected (negative case)."""

    runner = _sibling_runner(VESPER_REPO)
    decision = _vesper_design_system_decision(VESPER_PR385_BASE, runner=runner)
    return build_fixture_from_pr(
        VESPER_REPO,
        385,
        fixture_id="vesper-workspace-sheet-tokens-respected-001",
        decisions=(decision,),
        expected_findings=(),
        extra_metadata={
            "scenario": "new-sheet-built-from-design-tokens",
            "notes": (
                "Negative case: NewWorkspaceSheet.swift uses Spacing/Typography tokens, "
                "theme colors, LucideLabel, and vesperGhostButtonStyle throughout — "
                "exactly the shape the design-system decision requires."
            ),
        },
        runner=runner,
    )


def build_vanguard_portfolio_boundary_respected() -> EvalFixture:
    """vanguard PR #559 refined dust-floor sizing math inside the vault —
    Doctrine 0002 allows the math to evolve as long as allocation authority
    stays with PortfolioDecision (negative case)."""

    runner = _sibling_runner(VANGUARD_REPO)
    boundary_span, _ = _pinned_span_for(
        VANGUARD_REPO,
        VANGUARD_PR559_BASE,
        VANGUARD_DOCTRINE_0002_PATH,
        "> A versioned `PortfolioDecision` layer is the authoritative source of "
        "trade-or-no-trade and target budget for every proposal. The runner consumes "
        "these decisions; it does not independently size or select against current "
        "portfolio state. The optimizer *math* inside the boundary may evolve; the "
        "*boundary* is doctrine.",
        runner=runner,
    )
    risk_guard_span, timestamp = _pinned_span_for(
        VANGUARD_REPO,
        VANGUARD_PR559_BASE,
        VANGUARD_DOCTRINE_0002_PATH,
        "  - `vault/risk_guard.py` — defense-in-depth hard gate. Survives indefinitely.",
        runner=runner,
    )
    decision = FixtureDecision(
        decision_id="vanguard-portfolio-decision-boundary",
        decision_text=(
            "Vanguard's runner does not independently decide allocation: a versioned "
            "PortfolioDecision is the authoritative source of trade-or-no-trade and "
            "target budget (Doctrine 0002). The sizing math inside the boundary may "
            "evolve; vault/risk_guard.py survives as the defense-in-depth hard gate."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(boundary_span, risk_guard_span),
        scopes=(
            FixtureScope(scope_type=ScopeType.PATH, value="vanguard/runner.py"),
            FixtureScope(scope_type=ScopeType.PATH, value="vanguard/vault/position_sizer.py"),
            FixtureScope(scope_type=ScopeType.PATH, value="vanguard/vault/risk_guard.py"),
        ),
    )
    return build_fixture_from_pr(
        VANGUARD_REPO,
        559,
        fixture_id="vanguard-portfolio-boundary-respected-001",
        decisions=(decision,),
        expected_findings=(),
        extra_metadata={
            "scenario": "dust-floor-exemption-inside-the-boundary",
            "notes": (
                "Negative case: the atomic-minimum dust-floor exemption evolves sizing "
                "math inside the Doctrine 0002 boundary (risk_guard helper mirrored "
                "into the runner pre-check for one code path); allocation authority "
                "stays with PortfolioDecision."
            ),
        },
        runner=runner,
    )


def build_outrider_contract_version_omitted() -> EvalFixture:
    """outrider PR #541 rescoped public track records to the current
    model_version cohort without bumping AGENT_DETAIL_RESPONSE_VERSION or
    updating docs/CONTRACT.md — the drift PR #553 later fixed (real
    omitted-load-bearing-constraint case)."""

    runner = _sibling_runner(OUTRIDER_REPO)
    version_span, _ = _pinned_span_for(
        OUTRIDER_REPO,
        OUTRIDER_PR541_BASE,
        OUTRIDER_CONTRACT_PATH,
        "- `AGENT_DETAIL_RESPONSE_VERSION` (currently `2.2.0`) — for "
        "`/v1/agents/{id}/calibration` and the `/v1/agents/{id}` track-record detail "
        "surface.",
        runner=runner,
    )
    boundaries_span, _ = _pinned_span_for(
        OUTRIDER_REPO,
        OUTRIDER_PR541_BASE,
        OUTRIDER_CLAUDE_MD,
        "- **Version your data boundaries** — `ResearchProposal.research_version` and "
        "`model_version` bump on any schema or model change. Don't aggregate across "
        "model versions in calibration without explicit handling.",
        runner=runner,
    )
    compat_span, timestamp = _pinned_span_for(
        OUTRIDER_REPO,
        OUTRIDER_PR541_BASE,
        OUTRIDER_CLAUDE_MD,
        "- **Preserve compatibility at boundaries** — public API/schema changes need a "
        "compatibility or migration plan that covers vanguard *and* future external "
        "subscribers in the same PR.",
        runner=runner,
    )
    decision = FixtureDecision(
        decision_id="outrider-api-version-boundaries",
        decision_text=(
            "Outrider's public API is a versioned boundary: schema or semantic changes "
            "to a response bump the owning version constant (AGENT_DETAIL_RESPONSE_"
            "VERSION for /v1/agents/{name}) and ship the docs/CONTRACT.md compatibility "
            "or migration plan in the same PR; model_version cohorts are never blended "
            "or rescoped silently."
        ),
        status=DecisionStatus.CONFIRMED,
        source_timestamp=timestamp,
        spans=(version_span, boundaries_span, compat_span),
        scopes=(
            FixtureScope(scope_type=ScopeType.PATH, value="outrider/api/agents.py"),
            FixtureScope(scope_type=ScopeType.PATH, value="docs/CONTRACT.md"),
        ),
    )
    finding = ExpectedFinding(
        finding_id="finding-contract-version-not-bumped",
        finding_class=FindingClass.OMITTED_LOAD_BEARING_CONSTRAINT,
        decision_id="outrider-api-version-boundaries",
        cited_span_hashes=(version_span.span_hash, compat_span.span_hash),
        summary=(
            "The diff rescopes GET /v1/agents/{name} track_record metrics to the "
            "current model_version cohort only — a semantic change to a public "
            "response — but omits the AGENT_DETAIL_RESPONSE_VERSION bump and the "
            "docs/CONTRACT.md methodology update; the contract kept advertising 2.2.0 "
            "all-version semantics until PR #553 bumped it to 3.0.0."
        ),
        suggested_repair=(
            "Bump AGENT_DETAIL_RESPONSE_VERSION and update the docs/CONTRACT.md "
            "methodology and changelog in the same diff that changes track-record "
            "semantics."
        ),
    )
    return build_fixture_from_commit_range(
        OUTRIDER_REPO,
        OUTRIDER_PR541_BASE,
        OUTRIDER_PR541_SQUASH,
        fixture_id="outrider-contract-version-omitted-001",
        decisions=(decision,),
        expected_findings=(finding,),
        paths=("outrider/api/", "outrider/platform/model_version.py"),
        pr_number=541,
        extra_metadata={
            "scenario": "api-semantics-changed-without-contract-version-bump",
            "notes": (
                "Real sibling-repo staleness case, scoped to the API-surface slice of "
                "the 46-file squash commit: PR #541 (merged 2026-06-07) changed "
                "_compute_track_record cohort semantics; the contract drift persisted "
                "until PR #553 (merged 2026-06-09) bumped 2.2.0 to 3.0.0."
            ),
        },
        runner=runner,
    )


# ---------------------------------------------------------------------------
# simlab promotions (cortex#339: scenario triples as corpus fixtures)
# ---------------------------------------------------------------------------

SIMLAB_PROMOTED_SCENARIOS = (
    "chatty-startup-worker-threads",
    "clean-shop-retry-fixed-delay",
    "clean-shop-unrelated-docs",
    "legacy-migration-runbook-anchor",
)


def _repo_root_on_sys_path() -> None:
    """Make ``tests.simlab`` importable when run as a script from repo root."""

    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def build_simlab_promotions() -> tuple[EvalFixture, ...]:
    """Convert the promoted simlab scenario triples into corpus fixtures.

    Each scenario's archetype is materialized and derived through the shipped
    pipeline (``tests.simlab.generator``) and the scenario's EvalFixture is
    built by the shipped ``tests.simlab.runner.build_scenario_fixture`` — the
    same decisions, spans, and expected findings the simlab regression pack
    replays. The fixture is then re-stamped with corpus metadata
    (``source = "simlab"``, stable ``simlab-<scenario>-001`` ids). Both specs
    and pipeline are deterministic, so rebuilds are byte-identical.

    Promotion covers static scenarios only: drift scenarios
    (``post_derive_edits``) exercise working-tree state and stay in the
    simlab pack.
    """

    _repo_root_on_sys_path()
    from tests.simlab.generator import derive_materialized, materialize_archetype
    from tests.simlab.runner import build_scenario_fixture
    from tests.simlab.specs import load_archetype_specs, load_scenario_specs

    archetypes = load_archetype_specs()
    scenarios = {spec.scenario_id: spec for spec in load_scenario_specs()}
    fixtures: list[EvalFixture] = []
    for scenario_id in SIMLAB_PROMOTED_SCENARIOS:
        if scenario_id not in scenarios:
            raise RuntimeError(f"promoted simlab scenario not found: {scenario_id!r}")
        scenario = scenarios[scenario_id]
        if scenario.post_derive_edits:
            raise RuntimeError(
                f"scenario {scenario_id!r} carries post_derive_edits; drift scenarios "
                "stay in the simlab regression pack and are not promoted"
            )
        archetype = archetypes[scenario.archetype_id]
        with tempfile.TemporaryDirectory(prefix="simlab-corpus-") as tmp:
            repo = materialize_archetype(archetype, Path(tmp) / scenario_id)
            derive_materialized(repo)
            scenario_fixture = build_scenario_fixture(scenario, repo)
        if scenario_fixture.drift_skips:
            raise RuntimeError(
                f"scenario {scenario_id!r} produced unexpected span-drift skips during "
                "promotion; refusing to freeze a fixture that lost decisions"
            )
        base = scenario_fixture.fixture
        fixtures.append(
            EvalFixture(
                fixture_id=f"simlab-{scenario.scenario_id}-001",
                diff=base.diff,
                decisions=base.decisions,
                expected_findings=base.expected_findings,
                labels=(),
                metadata={
                    "source": SIMLAB_SOURCE,
                    "archetype_id": scenario.archetype_id,
                    "scenario_id": scenario.scenario_id,
                    "scenario_title": scenario.title,
                    "notes": (
                        "Promoted from the simlab scenario pack (tests/simlab/"
                        "scenarios); decisions and spans come from the deterministic "
                        "materialize/derive pipeline over the synthetic archetype."
                    ),
                },
            )
        )
    return tuple(fixtures)


def main() -> None:
    builders: tuple[Callable[[], EvalFixture], ...] = (
        build_standalone_boundary_respected,
        build_spec_version_drift,
        build_consolidated_journal,
        build_journal_entry_deletion,
        build_touchstone_managed_principles,
        build_standalone_boundary_synthetic,
        build_vesper_lucide_icon_violation,
        build_vesper_workspace_sheet_respected,
        build_vanguard_portfolio_boundary_respected,
        build_outrider_contract_version_omitted,
    )
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    fixtures = [builder() for builder in builders]
    fixtures.extend(build_simlab_promotions())
    for fixture in fixtures:
        path = write_fixture(fixture, CORPUS_DIR)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
