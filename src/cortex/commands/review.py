"""`cortex review` — the local advisory review verb closing the PE-0 loop.

Stage 0 issue #515: diff → ``decisions_for_diff`` → evaluator → cited
advisory finding, end to end, in one CLI verb. This is the single-player
version of the Stage 2 GitHub reviewer and the natural vehicle for #450's
replay over real history. The command composes only shipped modules — it
owns no retrieval, no evaluation, and no model transport of its own:

- **Diff text** comes from ``--diff FILE``, ``--staged`` (``git diff
  --staged``), or ``--against REF`` (``git diff REF...HEAD``); ``--staged``
  is the documented default.
- **Changed surface** via ``diff_surface.extract_changed_surface`` — the one
  extraction path (cortex#363); no second parser here.
- **Candidate pack** via the shipped ``decisions_for_diff`` SQL against the
  hosted Postgres when ``DATABASE_URL`` is set, or via the replay runner's
  documented fixture-local retrieval emulation
  (``replay_runner.build_fixture_candidate_pack``, cortex#336) seeded from a
  ``--decisions-fixture`` EvalFixture when it is not. No third pack builder
  exists.
- **Budgeted context + evaluation** via ``evaluator.evaluate_diff``
  (cortex#370), which owns context assembly, the citation gate, the
  class-evidence registry, and the advisory ladder. ``--token-budget``
  defaults to the manifest session guardrail
  (``cortex.manifest.DEFAULT_BUDGET_TOKENS``).
- **Model access** through the cortex#345 routing boundary:
  ``RecordedResponseAdapter`` when ``CORTEX_MODEL_FIXTURES`` names a
  directory of recordings (fully offline, CI-safe), ``ClaudeCliAdapter``
  otherwise. A missing ``claude`` binary degrades visibly, naming both
  setup paths — it never crashes and never fabricates findings.
- **Finding blocks** via ``cortex.hosted.finding_render`` (cortex#376) —
  the one render path shared with the Stage 2 GitHub comment renderer
  (cortex#390). This module keeps only the CLI accounting lines and the
  replay key line.

Exit-code policy (Stage 0, cortex#375): findings NEVER change the exit
code — blocking is unrepresentable, so the verb exits 0 whether it emitted
ten findings or none. Declared degraded-capability states (no decision
source, no evaluate model) also exit 0 with a visible message naming the
setup paths. Only invalid input or a failed evaluation pipeline (bad diff,
malformed fixture, stale recordings, DB errors) exits non-zero.

Cost note: the local claude CLI transport is subscription-authenticated, so
``REVIEW_PRICE_TABLE`` prices it at $0 under a version string that says so
(``cortex-review-unmetered-v1``); recorded playback is $0 by definition
(cortex#335). The basis is visible in every cost record either way.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import click

from cortex.commands.ask import latest_graph_snapshot_sql
from cortex.commands.derive import default_source_id, default_tenant_id
from cortex.hosted.advisory_ladder import DEFAULT_ADVISORY_LADDER
from cortex.hosted.context_assembly import ContextAssemblyValidationError
from cortex.hosted.cost import (
    CostValidationError,
    ModelPrice,
    ModelPriceTable,
    RunLedger,
)
from cortex.hosted.db import HostedDbError, connect
from cortex.hosted.decisions_for_diff import (
    DecisionsForDiffCandidatePack,
    DecisionsForDiffQuery,
    DecisionsForDiffValidationError,
    build_decisions_for_diff_candidate_pack,
    decisions_for_diff_retrieval_sql,
)
from cortex.hosted.diff_surface import DiffSurfaceValidationError, extract_changed_surface
from cortex.hosted.eval_fixtures import EvalFixture, FixtureValidationError
from cortex.hosted.evaluator import (
    EvaluationOutcome,
    EvaluatorValidationError,
    evaluate_diff,
    evaluate_prompt_guidance,
)
from cortex.hosted.finding_render import (
    FindingRenderError,
    build_span_index,
    render_finding_block_lines,
)
from cortex.hosted.ledger_events import ActorRef
from cortex.hosted.model_interfaces import EvaluateModel, ModelInterfaceValidationError
from cortex.hosted.model_registry import (
    ModelPromptRegistry,
    RegisteredModel,
    RegisteredPrompt,
    RegistryValidationError,
)
from cortex.hosted.replay_runner import ReplayError, build_fixture_candidate_pack
from cortex.hosted.routing import (
    DEFAULT_CLAUDE_BINARY,
    RECORDED_RESPONSES_SCHEMA_VERSION,
    ClaudeCliAdapter,
    ModelRouter,
    ProviderAdapter,
    RecordedResponseAdapter,
    RouteConfig,
    RouteTable,
    RoutingError,
    TaskKind,
)
from cortex.manifest import DEFAULT_BUDGET_TOKENS

MODEL_FIXTURES_ENV_VAR = "CORTEX_MODEL_FIXTURES"
DEFAULT_REVIEW_TOKEN_BUDGET = DEFAULT_BUDGET_TOKENS

RECORDED_ADAPTER_ID = "recorded-responses"
CLAUDE_ADAPTER_ID = "claude-cli"
# Provider-qualified route id for the local claude CLI transport. The CLI
# does not surface an exact upstream model name; the route id names the
# transport so replay keys distinguish it from recorded playback.
REVIEW_CLAUDE_MODEL_ID = "anthropic/claude-cli"

# Server-side evaluate route (#517): when there is no claude CLI (a headless
# worker) but an API key is configured, evaluate over the HTTP transport.
# The model is the judge — the high-stakes role — so it defaults to a
# capable model and is overridable per deployment via CORTEX_REVIEW_MODEL
# (a config edit, no redeploy), per cheapest-model-that-holds-quality.
REVIEW_API_MODEL_ENV = "CORTEX_REVIEW_MODEL"
DEFAULT_REVIEW_API_MODEL = "claude-sonnet-4-6"

# The evaluate prompt contract this verb stamps. The template embeds the
# canonical Stage 0 class guidance (evaluator.evaluate_prompt_guidance), so
# the self-certifying prompt_version drifts the moment the registered class
# vocabulary changes — recordings made under the old contract miss visibly.
# (The cortex#373/#374 shadow classes joining the vocabulary surfaced as
# exactly that hash drift; the per-run registry starts at v1, so the
# content-hash suffix, not the version number, is the contract identifier.)
REVIEW_EVALUATE_PROMPT = RegisteredPrompt(
    prompt_id="review-evaluate",
    version_number=1,
    template_text=(
        "Judge whether the diff conflicts with the decisions in the bounded "
        "candidate pack (cortex review, Stage 0 advisory).\n"
        + evaluate_prompt_guidance()
    ),
    description="cortex review Stage 0 evaluate contract (issue #515).",
)
REVIEW_PROMPT_VERSION = REVIEW_EVALUATE_PROMPT.prompt_version

REVIEW_ACTOR = ActorRef(actor_type="cli", actor_id="cortex-review")

# INTERNAL cost basis (cortex#547). This price table is OPERATOR-INTERNAL: it
# prices OUR real provider dollars (tokens x provider list rate) so we can
# understand cost and price the product to be profitable. It is NOT a customer
# price and never reaches a customer surface — the customer-facing meter is
# credits (docs/HOSTED-PRICING.md), a separate concern.
#
# Two regimes live in this one table because both transports can serve the
# evaluate route:
#   - the local claude CLI transport is subscription-authenticated, so its
#     per-call USD is not metered at this boundary — priced at $0, and the
#     version string still says "internal cost" because that $0 is itself a
#     cost fact about the CLI regime;
#   - the api-http judge model (DEFAULT_REVIEW_API_MODEL, the headless server
#     path) IS metered: real Anthropic tokens x published Sonnet list rates.
# Pricing the CLI but leaving the api model unpriced was the regression
# (cortex#547): ModelPriceTable.price_for raised "unpriced call" on the server
# path, so the live worker could not record what its review actually cost.
# These rates are our internal cost basis, config-driven later (#335/#547) —
# not a customer price.
REVIEW_INTERNAL_SONNET_USD_PER_MILLION_INPUT = 3.0
REVIEW_INTERNAL_SONNET_USD_PER_MILLION_OUTPUT = 15.0
REVIEW_PRICE_TABLE = ModelPriceTable(
    version="cortex-review-internal-cost-v1",
    prices=(
        ModelPrice(
            model_id=REVIEW_CLAUDE_MODEL_ID,
            usd_per_million_input_tokens=0.0,
            usd_per_million_output_tokens=0.0,
        ),
        # Our internal cost basis for the api-http judge model (provider list
        # price). Internal cost, not a customer price — see the comment above.
        ModelPrice(
            model_id=f"anthropic/{DEFAULT_REVIEW_API_MODEL}",
            usd_per_million_input_tokens=REVIEW_INTERNAL_SONNET_USD_PER_MILLION_INPUT,
            usd_per_million_output_tokens=REVIEW_INTERNAL_SONNET_USD_PER_MILLION_OUTPUT,
        ),
    ),
)

REVIEW_NO_DECISION_SOURCE_MESSAGE = (
    "no decision source is configured: set DATABASE_URL to the hosted "
    "Postgres for live decisions-for-diff retrieval, or pass "
    "--decisions-fixture <eval-fixture.json> (with CORTEX_MODEL_FIXTURES "
    "recordings for fully offline replay) "
    "(degradation: degraded_capability — no candidate pack is fabricated, "
    "nothing was evaluated)"
)
REVIEW_NO_EVALUATE_MODEL_MESSAGE = (
    f"no evaluate model is available: install the `{DEFAULT_CLAUDE_BINARY}` "
    "CLI on PATH for live evaluation, or set CORTEX_MODEL_FIXTURES to a "
    "directory of recorded evaluate responses for offline replay "
    "(degradation: degraded_capability — no findings are fabricated, "
    "nothing was evaluated)"
)


class ReviewError(ValueError):
    """Raised when review input or configuration cannot support a run.

    Lives in ``cortex.commands`` (not ``cortex.hosted``), so it is a CLI
    boundary error, deliberately outside the hosted degradation taxonomy.
    """


class ReviewDegradedError(RuntimeError):
    """Raised for declared degraded-capability states (advisory: exit 0).

    Carries the visible message naming every setup path; the command renders
    it and exits 0 — Stage 0 review is advisory, so an absent backend must
    never break a pipeline, only announce itself.
    """


@dataclass(frozen=True)
class EvaluateRoute:
    """One resolved evaluate route: the adapter plus its registry identity."""

    adapter: ProviderAdapter
    adapter_id: str
    model_id: str
    # Adapter-specific route params (cortex#345 RouteConfig.params). Empty for
    # the CLI/recorded routes; the api-http route carries api_model (the bare
    # provider model name) since model_id must be provider-qualified.
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.adapter_id.strip():
            raise ReviewError("adapter_id must not be empty")
        if not self.model_id.strip():
            raise ReviewError("model_id must not be empty")


# ---------------------------------------------------------------------------
# Diff acquisition
# ---------------------------------------------------------------------------


def resolve_diff_text(
    project_root: Path,
    *,
    diff_file: Path | None = None,
    staged: bool = False,
    against: str | None = None,
) -> str:
    """Return the unified diff text from exactly one configured source.

    Callers (the command) enforce mutual exclusivity; this function runs the
    selected source and fails visibly — a git failure names the command and
    its stderr, an unreadable file names the path.
    """

    if diff_file is not None:
        try:
            return diff_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ReviewError(f"cannot read diff file {diff_file}: {exc}") from exc
    if against is not None:
        ref = against.strip()
        if not ref:
            raise ReviewError("--against requires a non-empty git ref")
        if ref.startswith("-"):
            raise ReviewError(f"--against ref {against!r} must not start with '-'")
        return _git_diff(project_root, ["diff", f"{ref}...HEAD"])
    if staged:
        return _git_diff(project_root, ["diff", "--staged"])
    raise ReviewError("no diff source selected (--diff FILE, --staged, or --against REF)")


def _git_diff(project_root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ReviewError(f"cannot run git {' '.join(args)}: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise ReviewError(
            f"git {' '.join(args)} failed (exit {completed.returncode}): {stderr}"
        )
    return completed.stdout


# ---------------------------------------------------------------------------
# Candidate pack: live SQL or fixture-local emulation — never a third builder
# ---------------------------------------------------------------------------


def load_decisions_fixture(path: Path) -> EvalFixture:
    """Load the EvalFixture whose decisions seed the offline emulation."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReviewError(f"cannot read decisions fixture {path}: {exc}") from exc
    try:
        return EvalFixture.from_json(text)
    except FixtureValidationError as exc:
        raise ReviewError(f"decisions fixture {path} is invalid: {exc}") from exc


def build_offline_candidate_pack(
    fixture: EvalFixture, diff_text: str
) -> DecisionsForDiffCandidatePack:
    """Build the pack via the replay runner's fixture-local emulation.

    The fixture supplies the decision corpus; the diff under review replaces
    the fixture's frozen diff (its lexical metadata is cleared — the
    emulation extracts the changed surface from the patch itself), and the
    documented ``build_fixture_candidate_pack`` path does the rest. Expected
    findings and labels are dropped: review evaluates, it does not grade.
    """

    review_diff = replace(
        fixture.diff,
        patch=diff_text,
        changed_paths=(),
        symbols=(),
        config_keys=(),
        issue_refs=(),
    )
    review_fixture = replace(
        fixture, diff=review_diff, expected_findings=(), labels=()
    )
    return build_fixture_candidate_pack(review_fixture).pack


def build_live_candidate_pack(
    *,
    dsn: str,
    diff_text: str,
    tenant_id: str,
    source_id: str,
    schema: str = "cortex_hosted",
) -> DecisionsForDiffCandidatePack:
    """Run the shipped decisions-for-diff SQL against the hosted Postgres."""

    surface = extract_changed_surface(diff_text)
    query = DecisionsForDiffQuery(
        tenant_id=tenant_id,
        changed_surface=surface,
        diff_text=diff_text,
        visible_source_ids=(source_id,),
    )
    connection = connect(dsn)
    import psycopg

    try:
        try:
            snapshot_cursor = connection.execute(
                latest_graph_snapshot_sql(schema), {"tenant_id": tenant_id}
            )
            snapshot_row = snapshot_cursor.fetchone()
            if snapshot_row is None:
                raise ReviewError(
                    f"no graph snapshot registered for tenant {tenant_id}; the "
                    "hosted graph projection has not been built yet, so a "
                    "replayable candidate pack cannot name its snapshot boundary"
                )
            graph_snapshot_hash = str(snapshot_row[0])
            cursor = connection.execute(
                decisions_for_diff_retrieval_sql(schema), query.as_sql_parameters()
            )
            column_names = [description[0] for description in cursor.description or ()]
            rows: list[dict[str, Any]] = [
                dict(zip(column_names, row, strict=True)) for row in cursor.fetchall()
            ]
        except psycopg.Error as exc:
            raise ReviewError(f"hosted decisions-for-diff query failed: {exc}") from exc
    finally:
        connection.close()
    return build_decisions_for_diff_candidate_pack(
        query=query, graph_snapshot_hash=graph_snapshot_hash, rows=rows
    )


# ---------------------------------------------------------------------------
# Model routing: recorded playback or the claude CLI — never a silent middle
# ---------------------------------------------------------------------------


def claude_cli_available() -> bool:
    """True when the ``claude`` binary the live adapter shells out to exists."""

    return shutil.which(DEFAULT_CLAUDE_BINARY) is not None


def load_recorded_response_adapter(
    fixtures_dir: Path,
) -> tuple[RecordedResponseAdapter, str]:
    """Load and merge every recording file in the fixtures directory.

    Returns the merged adapter plus the single evaluate model id the
    recordings stamp — the route must name exactly that model, so recordings
    spanning multiple model ids are refused rather than silently split.
    """

    if not fixtures_dir.is_dir():
        raise ReviewError(
            f"{MODEL_FIXTURES_ENV_VAR} names {fixtures_dir}, which is not a "
            "directory of recorded-response *.json files"
        )
    paths = sorted(fixtures_dir.glob("*.json"))
    if not paths:
        raise ReviewError(
            f"{MODEL_FIXTURES_ENV_VAR} directory {fixtures_dir} contains no "
            "*.json recordings; replay mode cannot run without recordings and "
            "never falls back to a live call"
        )
    derive_payloads: list[dict[str, Any]] = []
    evaluate_payloads: list[dict[str, Any]] = []
    for path in paths:
        try:
            adapter = RecordedResponseAdapter.from_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, RoutingError) as exc:
            raise ReviewError(f"recorded responses file {path} is unusable: {exc}") from exc
        payload = adapter.as_payload()
        derive_payloads.extend(payload["derive"])
        evaluate_payloads.extend(payload["evaluate"])
    try:
        merged = RecordedResponseAdapter.from_payload(
            {
                "derive": derive_payloads,
                "evaluate": evaluate_payloads,
                "recorded_responses_schema_version": RECORDED_RESPONSES_SCHEMA_VERSION,
            }
        )
    except RoutingError as exc:
        raise ReviewError(
            f"recordings in {fixtures_dir} cannot merge into one adapter: {exc}"
        ) from exc
    model_ids = sorted({str(entry["model_id"]) for entry in evaluate_payloads})
    if not model_ids:
        raise ReviewError(
            f"recordings in {fixtures_dir} contain no evaluate responses; "
            "cortex review replays evaluate results only"
        )
    if len(model_ids) > 1:
        raise ReviewError(
            f"recordings in {fixtures_dir} stamp multiple evaluate model ids "
            f"{model_ids}; one review run routes to exactly one model"
        )
    return merged, model_ids[0]


def resolve_evaluate_route(fixtures_dir: Path | None) -> EvaluateRoute:
    """Pick the evaluate route: recorded playback wins, claude CLI otherwise.

    Raises :class:`ReviewDegradedError` (the advisory exit-0 path) when no
    backend exists, naming both setup paths. Raises :class:`ReviewError` for
    unusable recordings — a promised replay that cannot load is an input
    error, not a degradation.
    """

    if fixtures_dir is not None:
        adapter, model_id = load_recorded_response_adapter(fixtures_dir)
        return EvaluateRoute(
            adapter=adapter, adapter_id=RECORDED_ADAPTER_ID, model_id=model_id
        )
    if claude_cli_available():
        return EvaluateRoute(
            adapter=ClaudeCliAdapter(),
            adapter_id=CLAUDE_ADAPTER_ID,
            model_id=REVIEW_CLAUDE_MODEL_ID,
        )
    # Headless server (no claude CLI): evaluate over the HTTP transport when
    # an API key is configured. The adapter defaults api_key_env to
    # ANTHROPIC_API_KEY and api_model to the route model_id (#517).
    from cortex.hosted.api_transport import (
        API_HTTP_ADAPTER_ID,
        DEFAULT_API_KEY_ENV,
        ApiHttpAdapter,
    )

    if os.environ.get(DEFAULT_API_KEY_ENV, "").strip():
        bare_model = os.environ.get(REVIEW_API_MODEL_ENV, "").strip() or DEFAULT_REVIEW_API_MODEL
        # The registry requires a provider-qualified model_id; the Anthropic
        # API expects the bare model name, carried as the api_model param.
        return EvaluateRoute(
            adapter=ApiHttpAdapter(),
            adapter_id=API_HTTP_ADAPTER_ID,
            model_id=f"anthropic/{bare_model}",
            params={"api_model": bare_model},
        )
    raise ReviewDegradedError(REVIEW_NO_EVALUATE_MODEL_MESSAGE)


def build_review_router(route: EvaluateRoute, *, run_ledger: RunLedger) -> ModelRouter:
    """Wire the resolved route through the cortex#345 routing boundary."""

    registry = ModelPromptRegistry(
        models=(
            RegisteredModel(
                model_id=route.model_id,
                description=f"cortex review evaluate route via {route.adapter_id}",
            ),
        ),
        prompts=(REVIEW_EVALUATE_PROMPT,),
    )
    table = RouteTable(
        routes=(
            RouteConfig(
                task_kind=TaskKind.EVALUATE,
                model_id=route.model_id,
                adapter_id=route.adapter_id,
                params=route.params,
            ),
        )
    )
    return ModelRouter(
        route_table=table,
        adapters={route.adapter_id: route.adapter},
        registry=registry,
        ledger=run_ledger,
    )


# ---------------------------------------------------------------------------
# Evaluation + rendering
# ---------------------------------------------------------------------------


def review_run_id(pack: DecisionsForDiffCandidatePack) -> str:
    """Deterministic run id derived from the retrieval query identity."""

    return f"cortex-review-{pack.query_hash[:12]}"


def evaluate_review(
    *,
    pack: DecisionsForDiffCandidatePack,
    diff_text: str,
    model: EvaluateModel,
    token_budget: int,
    tenant_id: str,
    source_id: str,
    run_ledger: RunLedger | None = None,
    occurred_at: datetime | None = None,
) -> EvaluationOutcome:
    """Run the soft evaluator with the review verb's fixed Stage 0 policy.

    One code path: the CLI, the recording generator in the tests, and any
    future replay harness all build the evaluator call (and therefore the
    ``EvaluateRequest`` whose ``input_hash`` keys recordings) through here.
    """

    ledger = (
        run_ledger
        if run_ledger is not None
        else RunLedger(run_id=review_run_id(pack), price_table=REVIEW_PRICE_TABLE)
    )
    return evaluate_diff(
        pack,
        diff_text,
        model,
        token_budget=token_budget,
        ladder=DEFAULT_ADVISORY_LADDER,
        run_ledger=ledger,
        prompt_version=REVIEW_PROMPT_VERSION,
        tenant_id=tenant_id,
        source_id=source_id,
        actor=REVIEW_ACTOR,
        occurred_at=occurred_at if occurred_at is not None else datetime.now(UTC),
    )


def render_review_report(
    outcome: EvaluationOutcome, pack: DecisionsForDiffCandidatePack
) -> str:
    """Render the advisory terminal report.

    Per-finding blocks (class, tier glyph, summary, citations with
    permalinks, suggested repair) come from the one shared render path,
    ``cortex.hosted.finding_render`` (cortex#376 — the Stage 2 GitHub
    comment renderer, cortex#390, consumes the same blocks); the
    CLI-specific accounting lines and the replay key line live here. Every
    number in the accounting is derived from the outcome — nothing is
    recomputed. Shadow findings (cortex#373/#374) never render as blocks;
    when any were captured, their per-class counts render as one visible
    accounting line.
    """

    span_by_hash = build_span_index(pack)
    lines: list[str] = [
        f"cortex review: {len(outcome.emitted)} advisory finding(s) "
        f"(state: {outcome.state.value}; Stage 0 — blocking unrepresentable, "
        "findings never change the exit code)"
    ]
    for index, emitted in enumerate(outcome.emitted, start=1):
        lines.append("")
        lines.extend(
            render_finding_block_lines(
                emitted,
                index=index,
                total=len(outcome.emitted),
                span_by_hash=span_by_hash,
            )
        )

    lines.append("")
    for suppressed in outcome.suppressed:
        lines.append(
            f"suppressed: {suppressed.finding.finding_class.value} "
            f"[{suppressed.tier.value}] — {suppressed.reason}"
        )
    lines.append(f"suppressed below floor: {outcome.suppressed_below_floor}")
    if outcome.shadow_findings:
        shadow = ", ".join(
            f"{finding_class} x{count}"
            for finding_class, count in outcome.shadow_class_counts.items()
        )
        lines.append(
            "shadow findings (cortex#373/#374 — captured, never rendered as "
            f"advisory): {shadow}"
        )
    rejection_counts = outcome.rejection_counts
    if rejection_counts:
        rejected = ", ".join(f"{code} x{count}" for code, count in rejection_counts.items())
    else:
        rejected = "none"
    lines.append(f"rejected findings: {rejected}")
    omitted = ", ".join(
        f"{stage}={count}" for stage, count in sorted(outcome.total_omitted.items())
    )
    lines.append(f"omitted decisions: {omitted}")
    lines.append(f"model-omitted decisions: {outcome.model_omitted_decision_count}")
    degraded = "; ".join(outcome.degraded_reasons) if outcome.degraded_reasons else "none"
    lines.append(f"degraded reasons: {degraded}")
    replay = outcome.replay
    lines.append(
        "replay-key: "
        f"model={replay.model_id} "
        f"prompt={replay.prompt_version} "
        f"input={replay.input_hash} "
        f"query={replay.query_hash} "
        f"candidates={replay.candidate_set_hash} "
        f"context={replay.context_hash} "
        f"snapshot={replay.graph_snapshot_hash} "
        f"retrieval={replay.retrieval_config_version} "
        f"estimator={replay.estimator_version} "
        f"budget={replay.token_budget} "
        f"run={replay.run_id}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

# Every nameable pipeline failure surfaces as a visible error line and a
# non-zero exit; nothing in this tuple is ever swallowed or downgraded.
_REVIEW_PIPELINE_ERRORS = (
    ReviewError,
    ReplayError,
    FindingRenderError,
    FixtureValidationError,
    DiffSurfaceValidationError,
    DecisionsForDiffValidationError,
    HostedDbError,
    RoutingError,
    EvaluatorValidationError,
    ContextAssemblyValidationError,
    ModelInterfaceValidationError,
    CostValidationError,
    RegistryValidationError,
)


def _validated_uuid_option(value: str | None, *, option_name: str) -> str | None:
    if value is None:
        return None
    try:
        UUID(value)
    except ValueError as exc:
        raise click.BadParameter(f"{value!r} is not a UUID", param_hint=option_name) from exc
    return value


@click.command("review", context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--diff",
    "diff_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read the unified diff to review from FILE.",
)
@click.option(
    "--staged",
    is_flag=True,
    default=False,
    help="Review `git diff --staged` (the default when no source is given).",
)
@click.option(
    "--against",
    "against_ref",
    default=None,
    help="Review `git diff REF...HEAD`.",
)
@click.option(
    "--decisions-fixture",
    "decisions_fixture",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "EvalFixture JSON whose decisions seed the fixture-local retrieval "
        "emulation (the offline pack path used when DATABASE_URL is unset)."
    ),
)
@click.option(
    "--token-budget",
    type=click.IntRange(min=1),
    default=DEFAULT_REVIEW_TOKEN_BUDGET,
    show_default=True,
    help="Token budget for the evaluation context (manifest session guardrail).",
)
@click.option(
    "--tenant-id",
    default=None,
    help=(
        "Tenant UUID for live retrieval and ledger drafts. Default: the same "
        "deterministic UUIDv5 of the resolved project root `cortex derive` uses."
    ),
)
@click.option(
    "--source-id",
    default=None,
    help=(
        "Visible source UUID to authorize for live retrieval. Default: the "
        "same deterministic UUIDv5 of the resolved project root."
    ),
)
@click.option(
    "--path",
    "project_root",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root: git diffs run here and identity defaults derive from it.",
)
def review_command(
    *,
    diff_file: Path | None,
    staged: bool,
    against_ref: str | None,
    decisions_fixture: Path | None,
    token_budget: int,
    tenant_id: str | None,
    source_id: str | None,
    project_root: Path,
) -> None:
    """Review a diff against the decision ledger, advisory-only (Stage 0).

    Extracts the changed surface, retrieves the bounded candidate pack
    (live decisions-for-diff SQL when DATABASE_URL is set; the replay
    runner's fixture-local emulation via --decisions-fixture when not),
    assembles the budgeted context, evaluates through the routed model
    boundary (recorded responses via CORTEX_MODEL_FIXTURES, the claude CLI
    otherwise), and renders cited advisory findings with the suppression/
    omission accounting and the replay key. Findings never change the exit
    code; declared degraded states exit 0 with the setup paths named.
    """

    selected = [
        name
        for present, name in (
            (diff_file is not None, "--diff"),
            (staged, "--staged"),
            (against_ref is not None, "--against"),
        )
        if present
    ]
    if len(selected) > 1:
        raise click.UsageError(
            f"diff sources are mutually exclusive; got {', '.join(selected)}"
        )

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if dsn and decisions_fixture is not None:
        raise click.UsageError(
            "both DATABASE_URL and --decisions-fixture are set; choose one "
            "decision source (live retrieval or the fixture-local emulation)"
        )

    root = Path(project_root).resolve()
    tenant = (
        _validated_uuid_option(tenant_id, option_name="--tenant-id")
        or default_tenant_id(root)
    )
    source = (
        _validated_uuid_option(source_id, option_name="--source-id")
        or default_source_id(root)
    )

    try:
        diff_text = resolve_diff_text(
            root,
            diff_file=diff_file,
            staged=staged or not selected,
            against=against_ref,
        )
    except ReviewError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    if not diff_text.strip():
        click.echo("cortex review: diff is empty; nothing to review")
        return

    fixtures_env = os.environ.get(MODEL_FIXTURES_ENV_VAR, "").strip()
    fixtures_dir = Path(fixtures_env) if fixtures_env else None

    try:
        if dsn:
            pack = build_live_candidate_pack(
                dsn=dsn, diff_text=diff_text, tenant_id=tenant, source_id=source
            )
        elif decisions_fixture is not None:
            fixture = load_decisions_fixture(decisions_fixture)
            pack = build_offline_candidate_pack(fixture, diff_text)
        else:
            raise ReviewDegradedError(REVIEW_NO_DECISION_SOURCE_MESSAGE)

        ledger = RunLedger(run_id=review_run_id(pack), price_table=REVIEW_PRICE_TABLE)
        route = resolve_evaluate_route(fixtures_dir)
        router = build_review_router(route, run_ledger=ledger)
        outcome = evaluate_review(
            pack=pack,
            diff_text=diff_text,
            model=router,
            token_budget=token_budget,
            tenant_id=tenant,
            source_id=source,
            run_ledger=ledger,
        )
        report = render_review_report(outcome, pack)
    except ReviewDegradedError as exc:
        click.echo(f"cortex review: {exc}", err=True)
        return
    except _REVIEW_PIPELINE_ERRORS as exc:
        for line in str(exc).splitlines():
            click.echo(f"error: {line}", err=True)
        sys.exit(1)

    click.echo(report)
