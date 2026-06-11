"""Stateless GitHub PR review — the default tier (cortex#537).

The webhook-driven version of ``cortex review``: on a ``github.pull_request``
delivery, Cortex fetches the repo's decision sources and the PR diff *from
GitHub* (never from local disk), derives the decision pack **in memory**,
evaluates the diff against it, renders the advisory comment, and — by default
— **dry-runs** instead of posting. It stores **nothing** about the customer's
decisions or code. The repo is the store; this is the projection doctrine
taken to its conclusion (``docs/security.md`` §"Target default posture").

The local proof this mirrors is ``docs/walkthrough-pe0.md`` §7: the same
``decisions_for_diff`` → evaluator → cited finding loop, with the decision
source fetched over the installation token instead of read off disk.

Pipeline (every heavy stage is reused, never reimplemented):

1. Parse the ``ClaimedJob.payload`` webhook body into a
   :class:`PullRequestEvent` — installation id, owner, repo, PR number, base
   and head SHA. A malformed or missing field is a fail-closed
   :class:`StatelessReviewError` carrying a registered remediation.
2. Fetch the PR diff: ``client.get_pull_request_diff(owner, repo, pr)``.
3. Fetch decision sources at the **base** SHA: ``CLAUDE.md``, ``AGENTS.md``,
   and every ``.cortex/doctrine/*.md`` entry (enumerated via
   ``client.list_directory``), plus ``.cortex/plans/*.md`` when cheap. A
   missing ``.cortex/`` degrades to a visible "no recorded decisions" comment,
   never a crash.
4. Derive decisions in memory from the fetched content — CLAUDE/AGENTS rules
   via ``extractors.extract_agent_instruction_rules``, doctrine ADRs via
   ``extractors.extract_adr_decision`` — and bound the pack to the diff's
   changed surface with the shipped fixture-local structural retrieval
   (``replay_runner.build_fixture_candidate_pack``). The fetched rules and
   doctrine are CONFIRMED-by-commit for evaluation. **No database** is touched.
5. Evaluate via ``commands.review.evaluate_review`` (the one evaluator seam).
   The model is injected: ``RecordedResponseAdapter`` (offline/CI) when
   ``CORTEX_MODEL_FIXTURES`` is set, else the route resolves the API or claude
   CLI transport — reusing ``review.resolve_evaluate_route`` /
   ``review.build_review_router`` so there is one route-resolution path.
6. Render the advisory comment with ``github_comment.render_pr_comment`` (the
   stable hidden marker + cited findings + disclosure).
7. Post — dry-run gated. ``ReviewHandlerConfig.dry_run`` defaults **True**:
   the rendered body is returned in the handler result and **nothing** is
   posted. With ``dry_run=False`` the prior Cortex comment is found by marker
   and updated, else created — idempotent: one comment per (PR, head SHA).
   A finding never fails the job; the review is always advisory.

Importable dogfood entry: :func:`run_stateless_review` runs the whole pipeline
against an injected client + model + config, so an orchestrator can call it
directly in a local script against a real Cortex PR (real
``GithubInstallationClient`` + ``ClaudeCliAdapter``) and print the comment it
*would* post — no worker, no deploy, no database required.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from cortex.hosted.cost import RunLedger
from cortex.hosted.decisions_for_diff import DecisionsForDiffCandidatePack
from cortex.hosted.eval_fixtures import (
    DecisionStatus,
    EvalFixture,
    FixtureDecision,
    FixtureDiff,
    FixtureScope,
    FixtureSourceSpan,
)
from cortex.hosted.evaluator import EvaluationOutcome
from cortex.hosted.extractors import (
    ExtractedCandidate,
    extract_adr_decision,
    extract_agent_instruction_rules,
    has_adr_status_header,
)
from cortex.hosted.finding_render import build_span_index
from cortex.hosted.github_app_auth import DirectoryEntry
from cortex.hosted.github_comment import ReviewAccounting, extract_marker, render_pr_comment
from cortex.hosted.jobs import ClaimedJob
from cortex.hosted.model_interfaces import EvaluateModel
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.worker import HandlerRegistry, JobHandler
from cortex.manifest import DEFAULT_BUDGET_TOKENS


class GithubReviewClient(Protocol):
    """The narrow GitHub surface the stateless reviewer reads and writes.

    The concrete ``cortex.hosted.github_app_auth.GithubInstallationClient``
    satisfies this structurally; typing the reviewer against the protocol
    keeps the dependency narrow (the reviewer needs six methods, not the whole
    client) and lets tests inject a fake without subclassing the auth machinery.
    """

    def get_pull_request_diff(self, owner: str, repo: str, pr_number: int) -> str: ...

    def get_file_contents(
        self, owner: str, repo: str, path: str, ref: str
    ) -> bytes | None: ...

    def list_directory(
        self, owner: str, repo: str, path: str, ref: str
    ) -> tuple[DirectoryEntry, ...]: ...

    def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[Mapping[str, Any], ...]: ...

    def post_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> Mapping[str, Any]: ...

    def update_issue_comment(
        self, owner: str, repo: str, comment_id: int, body: str
    ) -> Mapping[str, Any]: ...


# ``cortex review`` (cortex#515) owns the evaluate seam, the route resolution,
# the unmetered-CLI price table, and the manifest-derived token budget. This
# module imports those lazily inside the functions that use them: ``cortex
# review`` pulls in ``cortex.commands.ask`` -> ``cortex.commands.confirm`` ->
# ``cortex.hosted.degradation``, and ``degradation`` registers this module's
# error type — a module-level import here would close that cycle. The
# token-budget default comes straight from the manifest guardrail (the same
# value ``review.DEFAULT_REVIEW_TOKEN_BUDGET`` aliases), so the config default
# carries no import-time dependency on the command layer.
DEFAULT_REVIEW_TOKEN_BUDGET = DEFAULT_BUDGET_TOKENS

# The env var naming the recorded-response fixtures directory. The literal is
# duplicated from ``cortex.commands.review.MODEL_FIXTURES_ENV_VAR`` to keep the
# module-level import graph free of the command layer; a test asserts the two
# stay equal so the duplication can never drift.
MODEL_FIXTURES_ENV_VAR = "CORTEX_MODEL_FIXTURES"

# Namespace for the deterministic per-repo tenant/source UUIDs. Stateless mode
# stores nothing keyed on these — they exist only so the evaluator's ledger
# drafts and replay key carry a stable, repo-scoped identity (the drafts are
# computed in memory and discarded; nothing is written to the graph tables).
_REPO_UUID_NAMESPACE = uuid5(
    NAMESPACE_URL, "https://github.com/autumngarage/cortex#stateless-review"
)

# Decision sources fetched at the PR base. CLAUDE.md / AGENTS.md carry agent
# instruction rules; .cortex/doctrine/ carries ratified decisions; plans are
# fetched when cheap. The repo is the store: every path is read on demand and
# nothing is retained (docs/security.md).
DEFAULT_AGENT_INSTRUCTION_PATHS: tuple[str, ...] = ("CLAUDE.md", "AGENTS.md")
DEFAULT_DOCTRINE_DIR = ".cortex/doctrine"
DEFAULT_PLANS_DIR = ".cortex/plans"

# The evaluate model id stamped on recorded-response fixtures for the offline
# proof; mirrors the cortex review playback id so one recording format serves
# both surfaces.
STATELESS_REVIEW_RUN_PREFIX = "cortex-stateless-review"

STATELESS_REVIEW_PAYLOAD_REMEDIATION = (
    "the github.pull_request webhook body must carry installation.id, "
    "repository.owner.login, repository.name, and pull_request.{number, "
    "base.sha, head.sha}; a delivery missing any of these is refused before "
    "any fetch — verify the webhook event and the App's pull_request "
    "subscription (see docs/setup/github-app.md)"
)
"""The one actionable next step for ``stateless_review_payload_malformed``.

``cortex.hosted.degradation`` registers this string in
``REMEDIATION_BY_REASON`` (the import runs that way around so this module
never imports the taxonomy, mirroring ``github_app_auth``)."""


class StatelessReviewError(ValueError):
    """Raised when a stateless review cannot start from the delivery payload.

    The fail-closed boundary for the webhook → review handoff: a malformed or
    missing installation/repo/PR field is refused before any GitHub fetch or
    model call, carrying the ``stateless_review_payload_malformed`` remediation.
    """


@dataclass(frozen=True)
class PullRequestEvent:
    """The narrow slice of a ``github.pull_request`` body the reviewer needs.

    Every field is required: the diff fetch, the base-SHA decision fetch, and
    the head-SHA comment marker each depend on one of them, so a delivery
    missing any field cannot produce a replayable review and is refused.
    """

    installation_id: str
    owner: str
    repo: str
    pr_number: int
    base_sha: str
    head_sha: str

    def __post_init__(self) -> None:
        for name, value in (
            ("installation_id", self.installation_id),
            ("owner", self.owner),
            ("repo", self.repo),
            ("base_sha", self.base_sha),
            ("head_sha", self.head_sha),
        ):
            if not isinstance(value, str) or not value.strip():
                raise StatelessReviewError(
                    f"github.pull_request payload field {name!r} is missing or "
                    f"blank; {STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
                )
        if isinstance(self.pr_number, bool) or not isinstance(self.pr_number, int):
            raise StatelessReviewError(
                "github.pull_request payload pull_request.number must be an "
                f"integer; {STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
            )
        if self.pr_number <= 0:
            raise StatelessReviewError(
                "github.pull_request payload pull_request.number must be > 0; "
                f"{STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
            )

    @property
    def tenant_id(self) -> str:
        """Deterministic, repo-scoped tenant UUID (never a stored key)."""

        return str(uuid5(_REPO_UUID_NAMESPACE, f"tenant:{self.owner}/{self.repo}"))

    @property
    def source_id(self) -> str:
        """Deterministic, repo-scoped source UUID (never a stored key)."""

        return str(uuid5(_REPO_UUID_NAMESPACE, f"source:{self.owner}/{self.repo}"))


@dataclass(frozen=True)
class ReviewHandlerConfig:
    """Per-installation stateless-review configuration (default: dry-run).

    ``dry_run`` defaults **True**: the handler returns the rendered comment
    body and posts nothing, so the local dogfood proof prints the comment it
    *would* post without touching the PR. Setting it False opts into posting
    (idempotent: one comment per (PR, head SHA), found and updated by marker).
    Every path/dir is overridable so a project with a non-default decision
    layout still reviews.
    """

    dry_run: bool = True
    agent_instruction_paths: tuple[str, ...] = DEFAULT_AGENT_INSTRUCTION_PATHS
    doctrine_dir: str = DEFAULT_DOCTRINE_DIR
    plans_dir: str = DEFAULT_PLANS_DIR
    include_plans: bool = True
    token_budget: int = DEFAULT_REVIEW_TOKEN_BUDGET

    def __post_init__(self) -> None:
        if not self.agent_instruction_paths:
            raise StatelessReviewError(
                "agent_instruction_paths must name at least one decision source "
                "(default CLAUDE.md / AGENTS.md)"
            )
        if not self.doctrine_dir.strip():
            raise StatelessReviewError("doctrine_dir must not be blank")
        if isinstance(self.token_budget, bool) or not isinstance(self.token_budget, int):
            raise StatelessReviewError("token_budget must be an int")
        if self.token_budget < 1:
            raise StatelessReviewError("token_budget must be >= 1")


@dataclass(frozen=True)
class FetchedDecisionSource:
    """One decision document fetched from GitHub, with its repo path + permalink."""

    path: str
    permalink: str
    content: str


@dataclass(frozen=True)
class ReviewCost:
    """Operator-INTERNAL cost of one review's model evaluation (cortex#547).

    This is OUR provider dollars (tokens x provider list rate, from the
    versioned price table), captured so we can understand our costs and price
    the product to be profitable. It is **never** the customer-facing figure:
    the customer meter is credits (docs/HOSTED-PRICING.md). It is not in the
    rendered PR comment and not customer-visible; it surfaces only in the
    worker's structured log and the internal cost ledger.

    ``usd`` is the known cost when the transport reported tokens. On the
    recorded-adapter path (offline replay) the live cost is exactly 0 by
    definition (``CostBasis.RECORDED_PLAYBACK``); a transport that could not
    report tokens leaves ``usd`` 0 with the token counts also 0 — the shape
    is honest about what was (not) metered.
    """

    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    call_count: int

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise StatelessReviewError("ReviewCost.model must not be empty")
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("call_count", self.call_count),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise StatelessReviewError(f"ReviewCost.{name} must be a non-negative integer")
        if (
            isinstance(self.usd, bool)
            or not isinstance(self.usd, int | float)
            or self.usd < 0
        ):
            raise StatelessReviewError("ReviewCost.usd must be a non-negative number")

    def as_payload(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "usd": self.usd,
            "call_count": self.call_count,
        }


@dataclass(frozen=True)
class StatelessReviewResult:
    """The advisory outcome of one stateless review — content-free by tier.

    ``comment_body`` is the rendered markdown either posted or (dry-run)
    returned for printing. ``finding_count`` and ``decision_count`` make the
    review's shape visible; ``posted`` / ``comment_id`` record whether and
    where a comment landed. ``cost`` is the operator-internal model cost of
    this review (cortex#547) — provider dollars, never customer credits, and
    never part of the rendered comment. Nothing about the customer's decision
    *text* is persisted by the stateless path — this object is the in-memory
    return, not a stored row.
    """

    comment_body: str
    finding_count: int
    decision_count: int
    dry_run: bool
    posted: bool
    no_decisions: bool
    comment_id: int | None = None
    comment_url: str | None = None
    cost: ReviewCost | None = None

    def as_result_mapping(self) -> dict[str, Any]:
        """The JSON-serializable handler result (the worker stores this).

        The comment *body* is included so the local proof can print it; in a
        deployed worker the result row is operational bookkeeping (job result),
        which docs/security.md names as the only durable stateless write. The
        body is the rendered advisory comment — derived from the customer's
        public decision files, never their private decision graph. The ``cost``
        key carries the operator-internal model cost (cortex#547) — provider
        dollars for our pricing analysis, not a customer-visible figure.
        """

        return {
            "handled": True,
            "review_mode": "stateless",
            "dry_run": self.dry_run,
            "posted": self.posted,
            "finding_count": self.finding_count,
            "decision_count": self.decision_count,
            "no_decisions": self.no_decisions,
            "comment_id": self.comment_id,
            "comment_url": self.comment_url,
            "comment_body": self.comment_body,
            "cost": None if self.cost is None else self.cost.as_payload(),
        }


# ---------------------------------------------------------------------------
# Payload parsing (fail-closed)
# ---------------------------------------------------------------------------


def parse_pull_request_payload(payload: Mapping[str, Any]) -> PullRequestEvent:
    """Parse a ``github.pull_request`` webhook body, fail-closed.

    The worker's ``ClaimedJob.payload`` carries the webhook envelope; the
    GitHub PR event body lives either at the top level or under ``body`` (the
    shape the Stage 1 ``ArrivalRecorder`` already reads). This reads whichever
    is present and refuses anything missing the installation/repo/PR fields it
    needs to fetch and cite a review.
    """

    if not isinstance(payload, Mapping):
        raise StatelessReviewError(
            "github.pull_request payload must be a JSON object; "
            f"{STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
        )
    body = payload.get("body")
    event_body: Mapping[str, Any] = body if isinstance(body, Mapping) else payload

    installation = _require_mapping(event_body, "installation")
    repository = _require_mapping(event_body, "repository")
    pull_request = _require_mapping(event_body, "pull_request")
    owner = _require_mapping(repository, "owner")
    base = _require_mapping(pull_request, "base")
    head = _require_mapping(pull_request, "head")

    return PullRequestEvent(
        installation_id=_require_scalar_str(installation, "id"),
        owner=_require_str(owner, "login"),
        repo=_require_str(repository, "name"),
        pr_number=_require_int(pull_request, "number"),
        base_sha=_require_str(base, "sha"),
        head_sha=_require_str(head, "sha"),
    )


# ---------------------------------------------------------------------------
# Decision source fetch (the repo is the store)
# ---------------------------------------------------------------------------


def fetch_decision_sources(
    client: GithubReviewClient,
    event: PullRequestEvent,
    config: ReviewHandlerConfig,
) -> tuple[FetchedDecisionSource, ...]:
    """Fetch the repo's decision files at the PR base, in repo order.

    Agent-instruction files (CLAUDE.md / AGENTS.md) and every doctrine entry
    under ``.cortex/doctrine/`` (enumerated via ``list_directory``) are fetched
    at the **base** SHA — the baseline the diff is judged against. A file or
    directory absent at the ref is simply skipped (``get_file_contents`` and
    ``list_directory`` return ``None`` / ``()`` for a 404), so a repo with no
    ``.cortex/`` yields an empty tuple rather than a crash. Bytes are decoded
    UTF-8 (replacing undecodable bytes) and empty files are dropped — an empty
    decision document carries no rule.
    """

    sources: list[FetchedDecisionSource] = []
    seen_paths: set[str] = set()

    for path in config.agent_instruction_paths:
        fetched = _fetch_file(client, event, path)
        if fetched is not None and path not in seen_paths:
            sources.append(fetched)
            seen_paths.add(path)

    for path in _list_markdown_files(client, event, config.doctrine_dir):
        fetched = _fetch_file(client, event, path)
        if fetched is not None and path not in seen_paths:
            sources.append(fetched)
            seen_paths.add(path)

    if config.include_plans:
        for path in _list_markdown_files(client, event, config.plans_dir):
            fetched = _fetch_file(client, event, path)
            if fetched is not None and path not in seen_paths:
                sources.append(fetched)
                seen_paths.add(path)

    return tuple(sources)


def _fetch_file(
    client: GithubReviewClient, event: PullRequestEvent, path: str
) -> FetchedDecisionSource | None:
    raw = client.get_file_contents(event.owner, event.repo, path, event.base_sha)
    if raw is None:
        return None
    content = raw.decode("utf-8", errors="replace")
    if not content.strip():
        return None
    return FetchedDecisionSource(
        path=path,
        permalink=_blob_permalink(event, path),
        content=content,
    )


def _list_markdown_files(
    client: GithubReviewClient, event: PullRequestEvent, directory: str
) -> tuple[str, ...]:
    entries = client.list_directory(event.owner, event.repo, directory, event.base_sha)
    return tuple(
        sorted(
            entry.path
            for entry in entries
            if entry.type == "file" and entry.path.endswith(".md")
        )
    )


def _blob_permalink(event: PullRequestEvent, path: str) -> str:
    """Stable GitHub blob permalink at the reviewed base SHA.

    Pinned to the base commit (not a branch), so a citation always resolves to
    the exact decision text the review judged the diff against, even after the
    branch moves on.
    """

    return f"https://github.com/{event.owner}/{event.repo}/blob/{event.base_sha}/{path}"


# ---------------------------------------------------------------------------
# In-memory decision derivation (no database)
# ---------------------------------------------------------------------------


def derive_fixture_decisions(
    sources: tuple[FetchedDecisionSource, ...],
    event: PullRequestEvent,
    *,
    source_timestamp: datetime,
) -> tuple[FixtureDecision, ...]:
    """Derive CONFIRMED decisions from the fetched files, entirely in memory.

    Reuses the shipped extractors — ``extract_agent_instruction_rules`` for
    CLAUDE/AGENTS rules and ``extract_adr_decision`` for doctrine entries that
    carry an ADR ``Status:`` header — then bridges each extracted
    ``SourceSpan`` to a ``FixtureSourceSpan`` (identical hash material) so the
    decisions feed ``build_fixture_candidate_pack`` unchanged. Decisions are
    marked CONFIRMED because a rule committed to ``main`` is a ratified
    decision for review purposes (the same status the local walkthrough
    confirms verbatim CLAUDE.md rules into). Decision ids are deterministic
    (``path#index``) so a re-run over the same base produces the same ids.
    """

    decisions: list[FixtureDecision] = []
    timestamp = source_timestamp.astimezone(UTC).isoformat()
    for source in sources:
        document = _source_document(source, event, source_timestamp=source_timestamp)
        outcome = _extract_for(source, document)
        for index, extracted in enumerate(outcome):
            decision = _fixture_decision_from(
                source, extracted, index=index, source_timestamp=timestamp
            )
            if decision is not None:
                decisions.append(decision)
    return tuple(decisions)


def _extract_for(
    source: FetchedDecisionSource, document: SourceDocument
) -> tuple[ExtractedCandidate, ...]:
    """Route one fetched file to the matching deterministic extractor.

    Doctrine entries carrying an ADR ``Status:`` header import near-verbatim as
    one decision; everything else (CLAUDE.md, AGENTS.md, plans) extracts
    constraint-shaped instruction rules. The routing is by content shape, not
    filename guesswork, so a doctrine file without a status header degrades to
    the instruction-rule extractor rather than dropping silently.
    """

    if has_adr_status_header(source.content):
        return extract_adr_decision(document).extracted
    return extract_agent_instruction_rules(document).extracted


def _fixture_decision_from(
    source: FetchedDecisionSource,
    extracted: ExtractedCandidate,
    *,
    index: int,
    source_timestamp: str,
) -> FixtureDecision | None:
    candidate = extracted.candidate
    spans = tuple(
        FixtureSourceSpan(
            source_document_hash=span.source_document_hash,
            start_offset=span.start_offset,
            end_offset=span.end_offset,
            excerpt=span.excerpt,
            permalink=span.permalink,
        )
        for span in candidate.spans
    )
    if not spans:
        return None
    return FixtureDecision(
        decision_id=_slug_decision_id(source.path, index),
        decision_text=candidate.decision_text,
        status=DecisionStatus.CONFIRMED,
        source_timestamp=source_timestamp,
        spans=spans,
        scopes=_dedupe_scopes(candidate.proposed_scopes),
    )


def _slug_decision_id(path: str, index: int) -> str:
    """A stable, valid kebab-case decision id from a repo path + ordinal.

    Fixture decision ids must match ``^[a-z0-9][a-z0-9-]*$``; a repo path like
    ``.cortex/doctrine/0007-ownership.md`` is not, so it is lowercased and its
    non-alphanumeric runs collapse to ``-``. The trailing ``-<index>`` keeps
    two rules from the same file distinct, and the determinism (same path +
    ordinal -> same id) means a re-review over the same base produces the same
    ids.
    """

    lowered = "".join(ch if ch.isalnum() else "-" for ch in path.lower())
    collapsed = "-".join(part for part in lowered.split("-") if part)
    stem = collapsed or "decision"
    return f"{stem}-{index}"


def _dedupe_scopes(scopes: tuple[FixtureScope, ...]) -> tuple[FixtureScope, ...]:
    seen: set[tuple[str, str]] = set()
    deduped: list[FixtureScope] = []
    for scope in scopes:
        key = (scope.scope_type.value, scope.normalized_value)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(scope)
    return tuple(deduped)


def _source_document(
    source: FetchedDecisionSource,
    event: PullRequestEvent,
    *,
    source_timestamp: datetime,
) -> SourceDocument:
    return SourceDocument(
        tenant_id=event.tenant_id,
        source_id=event.source_id,
        document_type="repo_file",
        external_id=f"{event.owner}/{event.repo}@{event.base_sha}:{source.path}",
        permalink=source.permalink,
        author_ref=f"{event.owner}/{event.repo}",
        source_timestamp=source_timestamp,
        content=source.content,
    )


# ---------------------------------------------------------------------------
# Pack assembly (diff-scoped, in memory)
# ---------------------------------------------------------------------------


def build_review_pack(
    decisions: tuple[FixtureDecision, ...],
    event: PullRequestEvent,
    diff_text: str,
) -> DecisionsForDiffCandidatePack:
    """Bound the in-memory decisions to the diff's changed surface.

    Builds a transient :class:`EvalFixture` from the derived decisions and the
    PR diff, then runs the shipped fixture-local structural retrieval
    (``replay_runner.build_fixture_candidate_pack``) — the same diff-scoping
    the local ``cortex review`` offline path uses — to keep only the decisions
    whose scopes match the changed surface. Nothing here queries a database;
    the pack is built entirely from fetched files.
    """

    fixture = EvalFixture(
        fixture_id=f"stateless-{event.owner}-{event.repo}-{event.pr_number}",
        diff=FixtureDiff(
            repo_owner=event.owner,
            repo_name=event.repo,
            base_sha=event.base_sha,
            head_sha=event.head_sha,
            patch=diff_text,
        ),
        decisions=decisions,
    )
    from cortex.hosted.replay_runner import build_fixture_candidate_pack

    return build_fixture_candidate_pack(fixture).pack


# ---------------------------------------------------------------------------
# The importable entry — the dogfood seam
# ---------------------------------------------------------------------------


def run_stateless_review(
    payload: Mapping[str, Any],
    *,
    client: GithubReviewClient,
    model: EvaluateModel,
    config: ReviewHandlerConfig | None = None,
    now: Callable[[], datetime] | None = None,
) -> StatelessReviewResult:
    """Run the whole stateless review against injected dependencies.

    The thin, importable entry the orchestrator calls directly (no worker, no
    deploy, no database): pass the webhook ``payload``, a built
    ``GithubInstallationClient``, an ``EvaluateModel`` (a ``ModelRouter`` over
    the recorded adapter offline, or over the claude CLI / API transport live),
    and a config. It parses, fetches the diff and decision sources from GitHub,
    derives the pack in memory, evaluates, renders the advisory comment, and
    either returns it (dry-run, the default) or posts it idempotently.

    Stateless invariant: nothing about the customer's decisions or code is
    written anywhere by this function. The only durable effect — and only when
    ``config.dry_run`` is False — is one advisory PR comment, which is derived
    from the repo's own public decision files.
    """

    config = config or ReviewHandlerConfig()
    clock = now or (lambda: datetime.now(UTC))
    event = parse_pull_request_payload(payload)

    diff_text = client.get_pull_request_diff(event.owner, event.repo, event.pr_number)
    sources = fetch_decision_sources(client, event, config)
    decisions = derive_fixture_decisions(sources, event, source_timestamp=clock())

    if not decisions:
        return _no_decisions_result(client, event, config)

    from cortex.commands.review import evaluate_review

    pack = build_review_pack(decisions, event, diff_text)
    ledger = RunLedger(
        run_id=f"{STATELESS_REVIEW_RUN_PREFIX}-{pack.query_hash[:12]}",
        price_table=_review_price_table(),
    )
    outcome = evaluate_review(
        pack=pack,
        diff_text=diff_text,
        model=model,
        token_budget=config.token_budget,
        tenant_id=event.tenant_id,
        source_id=event.source_id,
        run_ledger=ledger,
        occurred_at=clock(),
    )
    # Capture the operator-internal model cost from the router's own private
    # ledger (cortex#547) — provider dollars, never customer credits. The
    # `ledger` above is the evaluator's draft ledger; the cost of the actual
    # routed call is recorded on the model router, read here via cost_summary.
    cost = _capture_review_cost(model)
    body = _render_comment(outcome, pack, event)
    return _post_or_return(client, event, config, body, outcome, cost)


def _render_comment(
    outcome: EvaluationOutcome,
    pack: DecisionsForDiffCandidatePack,
    event: PullRequestEvent,
) -> str:
    return render_pr_comment(
        outcome.emitted,
        accounting=ReviewAccounting.from_outcome(outcome),
        replay_key=outcome.replay,
        pr_number=event.pr_number,
        head_sha=event.head_sha,
        span_by_hash=build_span_index(pack),
    )


def _capture_review_cost(model: EvaluateModel) -> ReviewCost | None:
    """Read the operator-internal review cost from the model router (cortex#547).

    The model the handler injects is a ``ModelRouter`` whose private ledger
    accumulated the routed call's cost; ``cost_summary`` is its read-only
    surface. A model without that property (a hand-rolled test stub) yields no
    cost rather than a fabricated zero — the absence is visible, not silently
    metered. The single review route folds the summary's (one) model id into a
    single ``ReviewCost``; multiple models join with ``+`` so the row is still
    attributable.
    """

    summary = getattr(model, "cost_summary", None)
    if summary is None:
        return None
    model_label = "+".join(summary.model_ids) if summary.model_ids else "unknown"
    return ReviewCost(
        model=model_label,
        input_tokens=summary.reported_input_tokens,
        output_tokens=summary.reported_output_tokens,
        usd=summary.known_usd_total,
        call_count=summary.call_count,
    )


def _post_or_return(
    client: GithubReviewClient,
    event: PullRequestEvent,
    config: ReviewHandlerConfig,
    body: str,
    outcome: EvaluationOutcome,
    cost: ReviewCost | None,
) -> StatelessReviewResult:
    finding_count = len(outcome.emitted)
    decision_count = len({emitted.decision_node_id for emitted in outcome.emitted})
    if config.dry_run:
        return StatelessReviewResult(
            comment_body=body,
            finding_count=finding_count,
            decision_count=decision_count,
            dry_run=True,
            posted=False,
            no_decisions=False,
            cost=cost,
        )
    identity = _upsert_comment(client, event, body)
    return StatelessReviewResult(
        comment_body=body,
        finding_count=finding_count,
        decision_count=decision_count,
        dry_run=False,
        posted=True,
        no_decisions=False,
        comment_id=identity["id"],
        comment_url=identity["html_url"],
        cost=cost,
    )


def _no_decisions_result(
    client: GithubReviewClient,
    event: PullRequestEvent,
    config: ReviewHandlerConfig,
) -> StatelessReviewResult:
    """The visible no-decisions outcome: a comment, never a crash.

    A repo with no ``.cortex/`` (or no extractable rules) still posts an honest
    "no recorded decisions for this repo" comment so the absence is visible —
    silence is not the same as "we looked and found nothing". Rendered through
    the same ``render_pr_comment`` no-findings path so the body carries the
    stable marker and the advisory framing.
    """

    body = render_pr_comment(
        (),
        accounting=ReviewAccounting(
            degraded_reasons=(
                "no recorded decisions were found for this repo — fetched "
                "CLAUDE.md / AGENTS.md / .cortex/doctrine at the PR base and "
                "extracted no reviewable rules; nothing to check the diff "
                "against",
            ),
        ),
        replay_key=_no_decisions_replay_key(event),
        pr_number=event.pr_number,
        head_sha=event.head_sha,
        span_by_hash={},
    )
    if config.dry_run:
        return StatelessReviewResult(
            comment_body=body,
            finding_count=0,
            decision_count=0,
            dry_run=True,
            posted=False,
            no_decisions=True,
        )
    identity = _upsert_comment(client, event, body)
    return StatelessReviewResult(
        comment_body=body,
        finding_count=0,
        decision_count=0,
        dry_run=False,
        posted=True,
        no_decisions=True,
        comment_id=identity["id"],
        comment_url=identity["html_url"],
    )


def _upsert_comment(
    client: GithubReviewClient, event: PullRequestEvent, body: str
) -> Mapping[str, Any]:
    """Update Cortex's prior comment for this PR if present, else create one.

    Idempotency: one comment per (PR, head SHA). The prior comment is found by
    scanning the PR's comments for the hidden marker — any Cortex comment on
    this PR is updated in place, so a re-delivery on the same head SHA edits
    rather than duplicates, and a new push (different head SHA, different
    marker in the new body) replaces the visible state.
    """

    existing = client.list_issue_comments(event.owner, event.repo, event.pr_number)
    for comment in existing:
        raw_body = comment.get("body")
        if not isinstance(raw_body, str):
            continue
        if extract_marker(raw_body) is None:
            continue
        comment_id = comment.get("id")
        if isinstance(comment_id, int) and not isinstance(comment_id, bool):
            return client.update_issue_comment(
                event.owner, event.repo, comment_id, body
            )
    return client.post_issue_comment(event.owner, event.repo, event.pr_number, body)


# ---------------------------------------------------------------------------
# Worker wiring
# ---------------------------------------------------------------------------


def build_review_handler(
    *,
    client_factory: Callable[[str], GithubReviewClient],
    model_resolver: Callable[[], EvaluateModel],
    config: ReviewHandlerConfig | None = None,
) -> JobHandler:
    """Build the ``github.pull_request`` handler over injected dependencies.

    ``client_factory`` takes the installation id and returns an authenticated
    client; ``model_resolver`` returns the routed evaluate model. Both are
    injected so tests pass fakes and the offline proof runs without network or
    a model provider. The handler is advisory: a finding never fails the job,
    and the result mapping is content-free per the stateless tier.
    """

    review_config = config or ReviewHandlerConfig()

    def handle(job: ClaimedJob) -> Mapping[str, Any]:
        event = parse_pull_request_payload(job.payload)
        client = client_factory(event.installation_id)
        model = model_resolver()
        result = run_stateless_review(
            job.payload, client=client, model=model, config=review_config
        )
        return result.as_result_mapping()

    return handle


def build_review_registry(
    *,
    client_factory: Callable[[str], GithubReviewClient],
    model_resolver: Callable[[], EvaluateModel],
    config: ReviewHandlerConfig | None = None,
    issue_comment_handler: JobHandler | None = None,
) -> HandlerRegistry:
    """The Stage 2 registry: the stateless review handler for pull_request.

    Registered alongside (never replacing) ``build_default_registry``: the
    worker entrypoint chooses this when the GitHub-App credentials and review
    config are present, else the Stage 1 stub registry. ``issue_comment`` stays
    a stub unless a handler is injected — the converse Slack/console loop is a
    separate stage.
    """

    registry = HandlerRegistry()
    registry.register(
        "github.pull_request",
        build_review_handler(
            client_factory=client_factory,
            model_resolver=model_resolver,
            config=config,
        ),
    )
    if issue_comment_handler is not None:
        registry.register("github.issue_comment", issue_comment_handler)
    return registry


# ---------------------------------------------------------------------------
# Model resolution (one route-resolution path, shared with cortex review)
# ---------------------------------------------------------------------------


def default_model_resolver() -> Callable[[], EvaluateModel]:
    """A resolver mirroring ``cortex review``'s route resolution.

    ``RecordedResponseAdapter`` when ``CORTEX_MODEL_FIXTURES`` names a
    recordings directory (offline proof + CI), else the claude CLI / API
    transport route. One route-resolution path: this reuses
    ``review.resolve_evaluate_route`` / ``review.build_review_router`` so the
    server reviewer and the local ``cortex review`` verb can never disagree
    about how a model is chosen.
    """

    def resolve() -> EvaluateModel:
        from cortex.commands.review import build_review_router, resolve_evaluate_route

        fixtures_env = os.environ.get(MODEL_FIXTURES_ENV_VAR, "").strip()
        fixtures_dir = Path(fixtures_env) if fixtures_env else None
        route = resolve_evaluate_route(fixtures_dir)
        ledger = RunLedger(
            run_id=f"{STATELESS_REVIEW_RUN_PREFIX}-route",
            price_table=_review_price_table(),
        )
        return build_review_router(route, run_ledger=ledger)

    return resolve


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _review_price_table() -> Any:
    # The price table lives in cortex review (the unmetered-CLI regime); import
    # it lazily so this module's import cost stays flat and the cost contract
    # has exactly one owner.
    from cortex.commands.review import REVIEW_PRICE_TABLE

    return REVIEW_PRICE_TABLE


def _no_decisions_replay_key(event: PullRequestEvent) -> Any:
    """A minimal replay key for the no-decisions comment footer.

    The no-decisions path runs no evaluation, so there is no real replay key;
    this names the model/prompt as the stateless reviewer with an all-zero
    snapshot so the footer renders honestly (no findings, no decisions, no
    snapshot reached).
    """

    from cortex.commands.review import REVIEW_CLAUDE_MODEL_ID, REVIEW_PROMPT_VERSION
    from cortex.hosted.evaluator import EvaluationReplayKey

    zero = "0" * 64
    return EvaluationReplayKey(
        graph_snapshot_hash=zero,
        retrieval_config_version="stateless-no-decisions",
        query_hash=zero,
        candidate_set_hash=zero,
        context_hash=zero,
        input_hash=zero,
        model_id=REVIEW_CLAUDE_MODEL_ID,
        prompt_version=REVIEW_PROMPT_VERSION,
        run_id=f"{STATELESS_REVIEW_RUN_PREFIX}-{event.pr_number}",
        estimator_version="stateless-no-decisions",
        token_budget=1,
    )


def _require_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise StatelessReviewError(
            f"github.pull_request payload field {key!r} must be a JSON object; "
            f"{STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
        )
    return value


def _require_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StatelessReviewError(
            f"github.pull_request payload field {key!r} must be a non-empty string; "
            f"{STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
        )
    return value


def _require_scalar_str(payload: Mapping[str, Any], key: str) -> str:
    """Read a scalar field as a string (GitHub sends installation.id as an int)."""

    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        raise StatelessReviewError(
            f"github.pull_request payload field {key!r} is missing; "
            f"{STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
        )
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value
    raise StatelessReviewError(
        f"github.pull_request payload field {key!r} must be a string or integer; "
        f"{STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
    )


def _require_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StatelessReviewError(
            f"github.pull_request payload field {key!r} must be an integer; "
            f"{STATELESS_REVIEW_PAYLOAD_REMEDIATION}"
        )
    return value
