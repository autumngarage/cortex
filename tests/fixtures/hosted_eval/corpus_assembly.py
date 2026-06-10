"""Assemble the committed Stage 0 eval corpus from real cortex history (cortex#339).

Run from the repository root (requires ``git`` history and the ``gh`` CLI):

    uv run python tests/fixtures/hosted_eval/corpus_assembly.py

The script freezes real merged-PR diffs and real commit-range diffs into
``tests/fixtures/hosted_eval/corpus/`` via ``cortex.hosted.corpus_builder``.
Decision context cites real repository documents pinned at the fixture's
base SHA, with offsets computed against the pinned content. Rebuilding with
the same inputs is byte-identical, so re-running this script is the
reconciliation check for the committed corpus.

All fixtures are written ungraded (``labels`` empty); grading happens through
the cortex#333 hand-labeling workflow, never by treating model output as
ground truth.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cortex.hosted.corpus_builder import (
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


def _pinned_span(pin_sha: str, path: str, excerpt: str) -> tuple[FixtureSourceSpan, str]:
    """Freeze an excerpt of a repo document at a pinned commit.

    Returns the span plus the document's last-change timestamp at that pin,
    used as the decision's ``source_timestamp``.
    """

    content = run_command(["git", "show", f"{pin_sha}:{path}"])
    span = build_document_span(
        document_content=content,
        excerpt=excerpt,
        permalink=f"https://github.com/{REPO}/blob/{pin_sha}/{path}",
    )
    timestamp = run_command(
        ["git", "log", "-1", "--format=%cI", pin_sha, "--", path]
    ).strip()
    if not timestamp:
        raise RuntimeError(f"no last-change timestamp for {path} at {pin_sha}")
    return span, timestamp


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


def main() -> None:
    builders: tuple[Callable[[], EvalFixture], ...] = (
        build_standalone_boundary_respected,
        build_spec_version_drift,
        build_consolidated_journal,
        build_journal_entry_deletion,
        build_touchstone_managed_principles,
        build_standalone_boundary_synthetic,
    )
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for builder in builders:
        fixture = builder()
        path = write_fixture(fixture, CORPUS_DIR)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
