"""No-silent-failure degradation taxonomy for the hosted reviewer (cortex#329).

The substrate already fails closed in many places; this module unifies that
vocabulary so every reviewer failure surfaces as one of a small set of
*behaviors* instead of a per-module exception name. The modes are derived
from what the shipped code already does:

- ``ask_ledger`` flips to ``AnswerState.NO_ANSWER`` / ``no_cited_support``
  instead of answering without citations (refusal, boundary held).
- ``visibility`` and ``storage`` refuse operations that would cross a
  declared authorization or storage boundary (refusal, boundary held).
- ``ledger_events``, ``provenance``, ``scopes``, ``ask_ledger``,
  ``decisions_for_diff``, ``diff_surface``, ``eval_fixtures``,
  ``embeddings``, and ``model_interfaces`` reject invalid material before
  any model call (rejection, nothing partial was produced).
- ``model_registry`` raises when stamped identity material does not match
  the registered content ("prompt drift detected"), and at review time a
  registry failure means a verdict's (model, prompt) identity cannot be
  trusted as stated (drift).
- ``decisions_for_diff`` and ``ask_ledger`` answer with bounded candidate
  sets and visible ``omitted_counts`` (bounded omission, counts visible).

Consumers: cortex#377 applies this taxonomy inside the soft evaluator
(Wave 5), and the Stage 2 GitHub reviewer surfaces these modes in advisory
comments. The prose contract lives in ``docs/degradation-modes.md``.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from cortex.hosted.advisory_ladder import AdvisoryLadderError
from cortex.hosted.api.config import ServiceConfigError
from cortex.hosted.api.webhooks import WebhookValidationError
from cortex.hosted.api_transport import (
    API_KEY_REMEDIATION,
    ApiHttpOutputError,
    ApiKeyMissingError,
)
from cortex.hosted.ask_ledger import AnswerState, AskLedgerValidationError
from cortex.hosted.ask_surface import AskSurfaceValidationError, BrowseIndexRefusedError
from cortex.hosted.banking import BankingValidationError
from cortex.hosted.candidate_dedup import CandidateDedupError
from cortex.hosted.candidate_metrics import CandidateMetricsValidationError
from cortex.hosted.cascade import CascadeValidationError
from cortex.hosted.citation_check import CitationCheckError
from cortex.hosted.confidence import ConfidenceValidationError
from cortex.hosted.context_assembly import ContextAssemblyValidationError
from cortex.hosted.corpus_builder import CorpusBuilderError
from cortex.hosted.cost import BudgetExceededError, CostValidationError
from cortex.hosted.db import HostedDbError
from cortex.hosted.decisions_for_diff import DecisionsForDiffValidationError
from cortex.hosted.derive_store import DeriveStoreError
from cortex.hosted.diff_surface import DiffSurfaceValidationError
from cortex.hosted.embeddings import HostedEmbeddingValidationError
from cortex.hosted.eval_fixtures import FixtureValidationError
from cortex.hosted.evaluator import EvaluatorValidationError, UncitedFindingError
from cortex.hosted.event_ordering import EventOrderingError
from cortex.hosted.extractors import ExtractorError
from cortex.hosted.finding_render import FindingRenderError
from cortex.hosted.github_app_auth import (
    GITHUB_API_REMEDIATION,
    GITHUB_APP_CREDENTIALS_REMEDIATION,
    GithubApiError,
    GithubAppAuthError,
    GithubAuthConfigError,
)
from cortex.hosted.github_comment import GitHubCommentRenderError
from cortex.hosted.graph_rebuild import GraphRebuildError
from cortex.hosted.graph_snapshot import GraphSnapshotValidationError
from cortex.hosted.graph_writes import GraphWriteValidationError
from cortex.hosted.jobs import HostedJobError
from cortex.hosted.labeling import LabelingError
from cortex.hosted.lane_assignment import LaneAssignmentError
from cortex.hosted.lanes import LanePolicyValidationError
from cortex.hosted.ledger_events import LedgerEventValidationError
from cortex.hosted.migrations import HostedMigrationError
from cortex.hosted.model_registry import RegistryValidationError
from cortex.hosted.provenance import ProvenanceValidationError
from cortex.hosted.push import HostedPushError
from cortex.hosted.quality_series import QualitySeriesValidationError
from cortex.hosted.question_normalization import QuestionNormalizationError
from cortex.hosted.recorded_responses import RecordedResponseError
from cortex.hosted.replay_runner import ReplayError
from cortex.hosted.review_cost import ReviewCostError
from cortex.hosted.review_feedback import ReviewFeedbackError
from cortex.hosted.route_comparison import RouteComparisonValidationError
from cortex.hosted.routing import (
    ClaudeCliOutputError,
    ClaudeCliUnavailableError,
    RecordedResponseMissingError,
    RoutingError,
)
from cortex.hosted.scopes import ScopeValidationError
from cortex.hosted.stateless_review import (
    STATELESS_REVIEW_PAYLOAD_REMEDIATION,
    StatelessReviewError,
)
from cortex.hosted.storage import StoreBoundaryError
from cortex.hosted.visibility import VisibilityBoundaryValidationError


class DegradationTaxonomyError(ValueError):
    """Raised when a failure cannot be classified or a report is malformed."""


class DegradationMode(StrEnum):
    """Visible reviewer degradation behaviors.

    Modes classify what the system *did* when something went wrong, not
    which module raised. Every mode is documented in
    ``docs/degradation-modes.md`` with its trigger, a shipped-code example,
    what the user sees, and what is never allowed.
    """

    FAIL_CLOSED_REFUSAL = "fail_closed_refusal"
    BOUNDED_OMISSION = "bounded_omission"
    INVALID_INPUT_REJECTED = "invalid_input_rejected"
    DRIFT_DETECTED = "drift_detected"
    DEGRADED_CAPABILITY = "degraded_capability"


# Exact-type dispatch table. Classification is per concrete type, never
# inherited: a future subclass refines behavior, and inheriting the parent's
# mode would silently mislabel that refinement. Unknown types therefore
# raise in classify_failure instead of falling back to anything.
_FAILURE_MODE_BY_TYPE: dict[type[BaseException], DegradationMode] = {
    # An unknown confidence label or ladder-vocabulary violation is rejected
    # before any finding can be placed on the ladder (cortex#375).
    AdvisoryLadderError: DegradationMode.INVALID_INPUT_REJECTED,
    # The api-http server transport mirrors the claude-CLI classifications
    # (cortex#517): an unset API-key env var is a named reduced capability
    # (the refusal names the variable and carries the model_api_key_missing
    # remediation), and transport/contract violations are refused outright,
    # never fabricated.
    ApiHttpOutputError: DegradationMode.FAIL_CLOSED_REFUSAL,
    ApiKeyMissingError: DegradationMode.DEGRADED_CAPABILITY,
    BudgetExceededError: DegradationMode.FAIL_CLOSED_REFUSAL,
    CandidateMetricsValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    CitationCheckError: DegradationMode.INVALID_INPUT_REJECTED,
    ClaudeCliOutputError: DegradationMode.FAIL_CLOSED_REFUSAL,
    ClaudeCliUnavailableError: DegradationMode.DEGRADED_CAPABILITY,
    ContextAssemblyValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # Corpus material that cannot be frozen into a replayable fixture (unmerged
    # PR, empty diff, ambiguous citation excerpt, non-canonical bytes) is
    # rejected before anything is written — same family as fixture validation.
    CorpusBuilderError: DegradationMode.INVALID_INPUT_REJECTED,
    CostValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    EventOrderingError: DegradationMode.INVALID_INPUT_REJECTED,
    GraphSnapshotValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    RecordedResponseError: DegradationMode.DRIFT_DETECTED,
    RecordedResponseMissingError: DegradationMode.FAIL_CLOSED_REFUSAL,
    RoutingError: DegradationMode.INVALID_INPUT_REJECTED,
    AskLedgerValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    BankingValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    CascadeValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    QualitySeriesValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    RouteComparisonValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # CandidateDedupError fires before any graph write: malformed identity
    # material or a non-candidate event is refused, nothing partial folds.
    CandidateDedupError: DegradationMode.INVALID_INPUT_REJECTED,
    # Malformed answer material (e.g. an uncited answer line) is refused at
    # construction, before any rendering — nothing partial reaches the user.
    AskSurfaceValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # A browse-shaped question is refused to hold the no-browsable-index
    # boundary (cortex#382): the corpus is never enumerated to make a query
    # succeed, same family as the visibility deny-by-default refusal.
    BrowseIndexRefusedError: DegradationMode.FAIL_CLOSED_REFUSAL,
    ConfidenceValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    DecisionsForDiffValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # DeriveStoreError's marquee failure is the same-idempotency-key /
    # different-event-hash collision — recorded state disagreeing with a
    # re-derivation is drift, not bad input.
    DeriveStoreError: DegradationMode.DRIFT_DETECTED,
    DiffSurfaceValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # Evaluator material that violates the soft-evaluator contract (class
    # evidence, registry shape, outcome arithmetic) is rejected before any
    # emission or ledger draft exists (cortex#370-#372).
    EvaluatorValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # An unrecognized or malformed derive source is rejected before any
    # extraction or write; recognized-but-noisy material is not an error at
    # all (it becomes DroppedChatter with a reason code).
    ExtractorError: DegradationMode.INVALID_INPUT_REJECTED,
    # A finding block whose cited span hash is absent from the span index is
    # refused rendering (cortex#376) — an advisory surface never renders a
    # citation a reader cannot verify, mirroring the evaluator's citation
    # boundary.
    FindingRenderError: DegradationMode.FAIL_CLOSED_REFUSAL,
    # The Stage 2 GitHub comment renderer refuses to post an advisory comment
    # whose cited decision does not resolve to a permalink through the span
    # index (cortex#390) — same citation boundary as FindingRenderError, one
    # surface further out toward the PR.
    GitHubCommentRenderError: DegradationMode.FAIL_CLOSED_REFUSAL,
    FixtureValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # GitHub App auth (cortex#386). A missing/blank/non-PEM credential is
    # rejected before any signing or HTTP call (mirrors ServiceConfigError);
    # a REST call that fails after bounded retries or a refusing 4xx/5xx is a
    # fail-closed refusal carrying the status and a sanitized context — the
    # installation token never appears in either message. The
    # GithubAppAuthError base is never raised directly (concrete subclasses
    # carry the behavior), but it is registered as a conservative refusal so
    # the family is always classifiable and the per-type guardrail holds.
    GithubAppAuthError: DegradationMode.FAIL_CLOSED_REFUSAL,
    GithubApiError: DegradationMode.FAIL_CLOSED_REFUSAL,
    GithubAuthConfigError: DegradationMode.INVALID_INPUT_REJECTED,
    # GraphRebuildError refuses a replay whose log material cannot fold into
    # a valid projection (missing contract keys, unknown nodes, key/hash
    # drift); no partial graph is ever returned.
    GraphRebuildError: DegradationMode.INVALID_INPUT_REJECTED,
    GraphWriteValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # HostedDbError refuses a connection that cannot satisfy the policy
    # (missing driver, invalid URL, unreachable host, auth failure) before
    # any partial state exists — refusal, boundary held.
    HostedDbError: DegradationMode.FAIL_CLOSED_REFUSAL,
    HostedEmbeddingValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # HostedPushError's marquee failure is drift: a derive-export row whose
    # recomputed event hash, or a working-tree file whose content-keyed
    # document hash, disagrees with what the export recorded — the push
    # refuses to replay content that no longer matches its identity.
    HostedPushError: DegradationMode.DRIFT_DETECTED,
    # HostedMigrationError blocks a migration that cannot be verified
    # (missing extension, unrecorded schema_migrations version) and rolls
    # back — refusal, boundary held.
    HostedMigrationError: DegradationMode.FAIL_CLOSED_REFUSAL,
    # A job that would violate the queue contract (empty type/key, non-JSON
    # payload, malformed claim row) is rejected before any row is written or
    # any handler runs (cortex#471).
    HostedJobError: DegradationMode.INVALID_INPUT_REJECTED,
    # A malformed service environment (non-integer PORT, non-UUID tenant id,
    # blank-but-set secret) refuses startup before any request is served —
    # a half-understood environment never serves traffic (cortex#470).
    ServiceConfigError: DegradationMode.INVALID_INPUT_REJECTED,
    # A structurally malformed webhook delivery (bad event-name header,
    # oversized delivery GUID, non-object JSON body) is rejected before any
    # job row exists; signature mismatches are answered 401 without raising
    # (cortex#470).
    WebhookValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    LabelingError: DegradationMode.INVALID_INPUT_REJECTED,
    # LaneAssignmentError fires before any model call or write — dropped
    # material attempting graph entry, laundered backfill flags, forged lane
    # claims — so the operation never starts, same family as the lane policy.
    LaneAssignmentError: DegradationMode.INVALID_INPUT_REJECTED,
    LanePolicyValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    LedgerEventValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    ProvenanceValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # An empty question is rejected before any normalization or retrieval —
    # nothing partial reaches the FTS leg (cortex#512).
    QuestionNormalizationError: DegradationMode.INVALID_INPUT_REJECTED,
    RegistryValidationError: DegradationMode.DRIFT_DETECTED,
    # ReplayError's marquee failure is a missing recorded response: the replay
    # runner refuses to fall back to a live model call (cortex#336).
    ReplayError: DegradationMode.FAIL_CLOSED_REFUSAL,
    # A malformed operator-internal review cost record (bad tenant UUID,
    # negative tokens, blank model id) is rejected before any row is written to
    # the internal cost ledger (cortex#547) — nothing partial is persisted, the
    # same before-any-write rejection family as the other validation errors.
    ReviewCostError: DegradationMode.INVALID_INPUT_REJECTED,
    # A malformed human-feedback event (bad tenant UUID, a reaction carrying a
    # reply excerpt, a reply pre-labeled with a sentiment, an over-bound
    # excerpt) is rejected before any row enters the ground-truth corpus
    # (cortex#394) — the same before-any-write rejection family as the other
    # validation errors. This keeps the absence-is-never-approval and
    # human-ground-truth-only invariants enforceable at construction.
    ReviewFeedbackError: DegradationMode.INVALID_INPUT_REJECTED,
    ScopeValidationError: DegradationMode.INVALID_INPUT_REJECTED,
    # A github.pull_request webhook body missing the installation/repo/PR
    # fields the stateless reviewer needs to fetch and cite a review is
    # refused before any GitHub fetch or model call (cortex#537) — the same
    # before-any-side-effect rejection family as WebhookValidationError.
    StatelessReviewError: DegradationMode.INVALID_INPUT_REJECTED,
    StoreBoundaryError: DegradationMode.FAIL_CLOSED_REFUSAL,
    # A finding whose provenance is absent from the candidate pack is refused
    # emission outright — the citation boundary holds (cortex#377), mirroring
    # ask_ledger's no_cited_support refusal.
    UncitedFindingError: DegradationMode.FAIL_CLOSED_REFUSAL,
    VisibilityBoundaryValidationError: DegradationMode.FAIL_CLOSED_REFUSAL,
}

# Substrate error types that ship in a separate, not-yet-merged module
# (cortex#344 lands cortex.hosted.model_interfaces). Registering them only
# when importable cannot misclassify anything: an exception class that does
# not exist cannot be raised. The skip is visible, never silent — it is
# reported by unregistered_optional_failure_sources(), asserted in tests,
# and documented in docs/degradation-modes.md.
OPTIONAL_FAILURE_SOURCES: tuple[tuple[str, str, DegradationMode], ...] = (
    (
        "cortex.hosted.model_interfaces",
        "ModelInterfaceValidationError",
        DegradationMode.INVALID_INPUT_REJECTED,
    ),
)

_UNREGISTERED_OPTIONAL_SOURCES: list[tuple[str, str]] = []


def _register_optional_failure_types() -> None:
    for module_name, class_name, mode in OPTIONAL_FAILURE_SOURCES:
        if importlib.util.find_spec(module_name) is None:
            _UNREGISTERED_OPTIONAL_SOURCES.append((module_name, class_name))
            continue
        module = importlib.import_module(module_name)
        candidate = getattr(module, class_name, None)
        if candidate is None:
            raise DegradationTaxonomyError(
                f"{module_name} is importable but does not define {class_name}; "
                "the degradation taxonomy is out of sync with the substrate"
            )
        if not (isinstance(candidate, type) and issubclass(candidate, BaseException)):
            raise DegradationTaxonomyError(
                f"{module_name}.{class_name} is not an exception type; "
                "the degradation taxonomy is out of sync with the substrate"
            )
        _FAILURE_MODE_BY_TYPE[candidate] = mode


_register_optional_failure_types()


def unregistered_optional_failure_sources() -> tuple[tuple[str, str], ...]:
    """Optional substrate error types that were not importable at load time.

    Non-empty output is a declared reduced capability, not a silent gap:
    a class that does not exist cannot be raised, so nothing raisable is
    left unclassified.
    """

    return tuple(_UNREGISTERED_OPTIONAL_SOURCES)


def classified_failure_types() -> tuple[type[BaseException], ...]:
    """Every exception type the taxonomy classifies, sorted by name."""

    return tuple(sorted(_FAILURE_MODE_BY_TYPE, key=lambda exc_type: exc_type.__qualname__))


def classify_failure(failure: BaseException | AnswerState) -> DegradationMode:
    """Classify a reviewer failure into its visible degradation behavior.

    Accepts a raised substrate exception instance or an ``AnswerState``.
    Unknown exception types raise ``DegradationTaxonomyError`` — an
    unclassified failure must never be treated as benign, so the taxonomy
    forces an explicit mapping before the failure can be handled.
    """

    if isinstance(failure, AnswerState):
        if failure is AnswerState.NO_ANSWER:
            return DegradationMode.FAIL_CLOSED_REFUSAL
        raise DegradationTaxonomyError(
            f"answer state {failure.value!r} is not a failure; "
            "refusing to classify success as degradation"
        )
    mode = _FAILURE_MODE_BY_TYPE.get(type(failure))
    if mode is None:
        raise DegradationTaxonomyError(
            f"unclassified failure type {type(failure).__qualname__}; every reviewer "
            "failure must map to an explicit DegradationMode before it can be "
            "handled (an unknown failure is never classified as benign)"
        ) from failure
    return mode


# One module-level remediation table (cortex#516). Errors are the onboarding
# surface of a fail-closed product: every user-facing refusal names exactly
# one actionable next command, and both the CLI refusal surfaces
# (`cortex ask` / `cortex derive` / `cortex candidates`) and
# DegradationReport.remediation draw their hint from this table — never from
# scattered per-call-site strings. Keys are reason codes as they appear in
# refusal messages and DegradationReport.reason_code values.
REMEDIATION_BY_REASON: Mapping[str, str] = MappingProxyType(
    {
        # `cortex ask` against a tenant whose graph projection was never
        # built: no snapshot means no citable boundary.
        "snapshot_missing": (
            "run `cortex push` to project confirmed decisions and register a graph snapshot"
        ),
        # The honest no-answer: nothing cited qualifies yet. Confirmation is
        # the act that makes candidates answerable (ask answers only from
        # confirmed decisions, by design).
        "no_cited_support": (
            "run `cortex candidates triage` to review and confirm pending candidates"
        ),
        # DATABASE_URL is set but the optional Postgres driver is absent —
        # mirrors the cortex.hosted.db install hint (the established pattern).
        "hosted_driver_missing": (
            "install the hosted Postgres driver: `pip install 'cortex[hosted]'` "
            "(or `uv sync --extra hosted`)"
        ),
        # No DATABASE_URL in the environment: name the env var and the
        # compass/Railway setup doc section.
        "database_url_missing": (
            "set DATABASE_URL to the hosted (compass) Postgres DSN — see "
            'docs/hosted-ledger.md § "Executable path: driver, migrations, '
            'integration tests"'
        ),
        # `cortex candidates ...` before any derive run produced a store.
        "derive_store_missing": "run `cortex derive` to propose candidates first",
        # `cortex derive` invoked outside a Cortex project.
        "cortex_dir_missing": "run `cortex init` to scaffold `.cortex/` first",
        # `cortex derive` found none of its default sources.
        "derive_no_sources": (
            "pass `--source FILE` to point cortex derive at decision sources"
        ),
        # The api-http transport's configured API-key env var is unset or
        # blank in the service environment (cortex#517). The canonical hint
        # string lives next to the adapter so the refusal message and this
        # table can never drift apart.
        "model_api_key_missing": API_KEY_REMEDIATION,
        # The GitHub App credential env vars are unset/blank/non-PEM
        # (cortex#386). The canonical hint string lives next to
        # github_app_auth so the refusal message and this table cannot drift.
        "github_app_credentials_missing": GITHUB_APP_CREDENTIALS_REMEDIATION,
        # A GitHub REST call from the installation client was refused or
        # exhausted bounded retries (cortex#386).
        "github_api_request_failed": GITHUB_API_REMEDIATION,
        # The stateless reviewer's pull_request webhook body was malformed or
        # missing the installation/repo/PR fields it needs (cortex#537). The
        # canonical hint lives next to stateless_review so the refusal message
        # and this table cannot drift.
        "stateless_review_payload_malformed": STATELESS_REVIEW_PAYLOAD_REMEDIATION,
    }
)


def remediation_for(reason_code: str) -> str:
    """Return the one actionable next command for a refusal reason code.

    Lookup is fail-closed: an unknown reason code raises
    ``DegradationTaxonomyError`` instead of returning a generic or empty
    hint — a refusal surface that wants a remediation must register it in
    ``REMEDIATION_BY_REASON`` first.
    """

    hint = REMEDIATION_BY_REASON.get(reason_code)
    if hint is None:
        raise DegradationTaxonomyError(
            f"no remediation registered for reason code {reason_code!r}; add it "
            "to REMEDIATION_BY_REASON before naming it on a user-facing refusal"
        )
    return hint


@dataclass(frozen=True)
class DegradationReport:
    """One visible degradation event, shaped by the no-silent-failure rule.

    The engineering-principles fallback rule: continuing after a failure is
    allowed only when the system reports what failed (``source``), why
    (``reason_code``), and what safety boundary still holds
    (``safety_boundary_held``). A report with ``safety_boundary_held=False``
    is structurally invalid: if no boundary held, the failure is an incident
    that must propagate, not a degradation that may continue.

    ``remediation`` is the optional one actionable next command for the
    user-facing surfaces (cortex#516); when present it must be non-empty —
    a blank hint is a dead end dressed up as help, so it fails validation.
    """

    mode: DegradationMode
    reason_code: str
    source: str
    safety_boundary_held: bool
    remediation: str | None = None

    def __post_init__(self) -> None:
        try:
            mode = DegradationMode(self.mode)
        except ValueError as exc:
            raise DegradationTaxonomyError(
                f"unknown degradation mode: {self.mode!r}"
            ) from exc
        object.__setattr__(self, "mode", mode)
        _require_non_empty("reason_code", self.reason_code)
        _require_non_empty("source", self.source)
        object.__setattr__(self, "reason_code", self.reason_code.strip())
        object.__setattr__(self, "source", self.source.strip())
        if self.remediation is not None:
            _require_non_empty("remediation", self.remediation)
            object.__setattr__(self, "remediation", self.remediation.strip())
        if self.safety_boundary_held is not True:
            raise DegradationTaxonomyError(
                "a degradation report must attest the safety boundary that still "
                "holds; if no boundary held, raise the failure instead of reporting "
                "a degradation"
            )

    def as_payload(self) -> dict[str, object]:
        """JSON-ready payload for logs, traces, and advisory comments.

        ``remediation`` appears only when set — consumers distinguish "no
        hint registered" from a hint by key presence, never by a null.
        """

        payload: dict[str, object] = {
            "mode": self.mode.value,
            "reason_code": self.reason_code,
            "safety_boundary_held": self.safety_boundary_held,
            "source": self.source,
        }
        if self.remediation is not None:
            payload["remediation"] = self.remediation
        return payload


def _require_non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise DegradationTaxonomyError(f"{name} must be a non-empty string")
