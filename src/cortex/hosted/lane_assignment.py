"""Derive-time lane assignment over the shipped promotion-lane policy.

This module is the cortex#358 implementation artifact. The policy itself
lives in ``cortex.hosted.lanes`` (the cortex#315 contract) and is consumed,
never restated: ``assign_lane`` routes every derive candidate through an
injected ``LanePolicy`` (default ``DEFAULT_LANE_POLICY``), re-validates the
returned assignment through ``LaneAssignment.from_payload`` so a policy
subclass cannot smuggle weakened state past the ``lanes.py`` invariants, and
refuses any assignment that does not echo the request.

The Obsidian master-plan non-negotiable, enforced at assignment time
(cortex#362) and exported for the cortex#375 evaluator confidence ladder as
``BACKFILL_ADVISORY_ONLY_RULE``:
backfilled nodes default advisory-only and are never auto-promotable.
The advisory-only marking consumes
``cortex.hosted.confidence.ADVISORY_ONLY_TIER_CAP`` regardless of source
type; lifecycle status and confidence tier stay separate vocabularies.

Dropped chatter is logged, never persisted (the cortex#361 invariant,
absorbed by cortex#358): ``dropped_chatter_record`` produces the
machine-readable reason-code log entry citing the policy rule that dropped
the material, and ``candidate_proposed_event`` raises for any dropped
assignment — no persistable ledger event, and therefore no
``decision_nodes`` / ``decision_versions`` / ``decision_edges`` row, can
exist for dropped material.

Integration seam (documented, deliberately not yet wired):
``cortex.commands.derive`` exposes the ``CandidateExtractor`` boundary
(``SourceDocument -> Sequence[LedgerEvent]``); the scaffold's default
extractor emits zero candidates. Real extractors (cortex#351-#357, in
flight on other branches) compose with this module per candidate:
``assign_lane(...)`` first, then either ``candidate_proposed_event(...)``
for the structured / provisional lanes or ``dropped_chatter_record(...)``
for the dropped lane.
TODO(cortex#358 seam): wire this stamping at the ``CandidateExtractor``
boundary in ``cortex.commands.derive`` when the first real extractor lands;
the scaffold is left untouched here so in-flight extractor branches keep a
stable base.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from cortex.hosted.confidence import (
    ADVISORY_ONLY_TIER_CAP,
    DEFAULT_CONFIDENCE_TIER,
    ConfidenceState,
    tier_rank,
)
from cortex.hosted.lanes import (
    DEFAULT_LANE_POLICY,
    DeriveSourceType,
    Lane,
    LaneAssignment,
    LanePolicy,
    LanePolicyValidationError,
    allowed_entry_statuses,
    validate_auto_promotion,
    validate_entry_status,
)
from cortex.hosted.ledger_events import (
    ActorRef,
    LedgerEvent,
    LedgerEventType,
    derive_idempotency_key,
)
from cortex.hosted.model_interfaces import DeriveCandidate, DroppedChatter
from cortex.hosted.provenance import content_hash

# Verbatim Obsidian master-plan non-negotiable, exported as a named policy
# constant so the cortex#375 evaluator confidence ladder cites this rule
# instead of re-deriving it.
BACKFILL_ADVISORY_ONLY_RULE = (
    "backfilled nodes default advisory-only and are never auto-promotable"
)

# Reserved payload keys stamped onto every CANDIDATE_PROPOSED event built
# here. Consumers rebuild the typed records with LaneAssignment.from_payload
# and ConfidenceState.from_payload, both fail-closed.
LANE_ASSIGNMENT_PAYLOAD_KEY = "lane_assignment"
CONFIDENCE_PAYLOAD_KEY = "confidence"

# Machine-readable reason-code prefix for the dropped-chatter log. The full
# reason code is "<prefix>:<rule_citation>" so every drop cites the policy
# rule that selected the dropped lane.
DROPPED_CHATTER_REASON_PREFIX = "lane-dropped"


class LaneAssignmentError(ValueError):
    """Raised when derive material cannot be lane-assigned or persisted safely."""


def enforce_backfill_advisory_only(assignment: LaneAssignment) -> LaneAssignment:
    """Assignment-time enforcement of the cortex#362 non-negotiable.

    backfilled nodes default advisory-only and are never auto-promotable.
    That sentence is the Obsidian master-plan rule verbatim
    (``BACKFILL_ADVISORY_ONLY_RULE``); the cortex#375 evaluator confidence
    ladder consumes the exported constant rather than re-deriving the rule.
    ``lanes.LaneAssignment`` already makes the violating state
    unrepresentable at construction; this boundary re-checks it because
    assignments can arrive from injected policies whose subclasses may
    bypass ``__post_init__``.
    """

    if assignment.backfilled and assignment.auto_promotable:
        raise LaneAssignmentError(
            f"{BACKFILL_ADVISORY_ONLY_RULE}; refusing auto-promotable backfilled "
            f"material (rule {assignment.rule_citation})"
        )
    if assignment.backfilled and not assignment.advisory_only:
        raise LaneAssignmentError(
            f"{BACKFILL_ADVISORY_ONLY_RULE}; a backfilled assignment without the "
            f"advisory-only marking is invalid (rule {assignment.rule_citation})"
        )
    return assignment


def assign_lane(
    source_type: DeriveSourceType,
    *,
    backfilled: bool,
    policy: LanePolicy = DEFAULT_LANE_POLICY,
) -> LaneAssignment:
    """Assign exactly one lane to derive-candidate material.

    The policy decides; this boundary fails closed. The returned assignment
    is re-validated through ``LaneAssignment.from_payload`` (running the
    full ``lanes.py`` invariant set, so a policy subclass that bypasses
    ``__post_init__`` cannot smuggle weakened state through) and must echo
    the request — a policy that drops the ``backfilled`` flag or swaps the
    source type is laundering candidate material and is refused.
    """

    if not isinstance(source_type, DeriveSourceType):
        raise LaneAssignmentError(
            f"source_type must be a DeriveSourceType, got {type(source_type).__name__}"
        )
    if not isinstance(backfilled, bool):
        raise LaneAssignmentError(
            f"backfilled must be a bool, got {type(backfilled).__name__}"
        )
    if not isinstance(policy, LanePolicy):
        raise LaneAssignmentError(
            f"policy must be a cortex.hosted.lanes.LanePolicy, got {type(policy).__name__}"
        )
    proposed = policy.assign(source_type, backfilled=backfilled)
    if not isinstance(proposed, LaneAssignment):
        raise LaneAssignmentError(
            f"policy returned {type(proposed).__name__}, not a LaneAssignment"
        )
    try:
        assignment = LaneAssignment.from_payload(proposed.as_payload())
    except LanePolicyValidationError as exc:
        raise LaneAssignmentError(
            f"policy v{policy.policy_version} returned an assignment that violates "
            f"the lane policy contract: {exc}"
        ) from exc
    if (
        assignment.source_type is not source_type
        or assignment.backfilled != backfilled
        or assignment.policy_version != policy.policy_version
    ):
        raise LaneAssignmentError(
            f"policy v{policy.policy_version} returned an assignment that does not "
            f"echo the request (source_type={source_type.value!r}, "
            f"backfilled={backfilled}); refusing to launder candidate material "
            "across the policy boundary"
        )
    return enforce_backfill_advisory_only(assignment)


def validate_assignment_against_policy(
    assignment: LaneAssignment,
    *,
    policy: LanePolicy = DEFAULT_LANE_POLICY,
) -> LaneAssignment:
    """Recompute the assignment from its policy and refuse drift or forgery.

    The auto-promotion boundary may trust ``LaneAssignment.auto_promotable``
    only when the assignment matches what the cited policy derives for its
    ``(source_type, backfilled)`` pair. A forged lane claim — chatter
    stamped ``structured``, or a hand-built assignment that skipped
    ``policy.assign`` — fails here even when it is internally consistent.
    """

    if not isinstance(assignment, LaneAssignment):
        raise LaneAssignmentError(
            f"assignment must be a LaneAssignment, got {type(assignment).__name__}"
        )
    if not isinstance(policy, LanePolicy):
        raise LaneAssignmentError(
            f"policy must be a cortex.hosted.lanes.LanePolicy, got {type(policy).__name__}"
        )
    if assignment.policy_version != policy.policy_version:
        raise LaneAssignmentError(
            f"assignment cites lane policy v{assignment.policy_version} but "
            f"validation ran against policy v{policy.policy_version}; replay the "
            "citing policy version instead of blending policy regimes"
        )
    expected = assign_lane(
        assignment.source_type, backfilled=assignment.backfilled, policy=policy
    )
    try:
        normalized = LaneAssignment.from_payload(assignment.as_payload())
    except LanePolicyValidationError as exc:
        raise LaneAssignmentError(
            f"assignment violates the lane policy contract: {exc}"
        ) from exc
    if normalized != expected:
        raise LaneAssignmentError(
            f"assignment does not match lane policy v{policy.policy_version} for "
            f"source type {assignment.source_type.value!r} "
            f"(backfilled={assignment.backfilled}); expected rule "
            f"{expected.rule_citation}, got {assignment.rule_citation}"
        )
    return expected


def validate_policy_auto_promotion(
    assignment: LaneAssignment,
    *,
    policy: LanePolicy = DEFAULT_LANE_POLICY,
) -> LedgerEventType:
    """Validate unattended promotion across the full cortex#315 boundary.

    Composes policy revalidation (refusing forged or drifted assignments)
    with ``lanes.validate_auto_promotion`` (the candidate -> confirmed
    gate), so exactly the policy table's allowed set passes. cortex#360
    sweeps the product adversarially.
    """

    return validate_auto_promotion(
        validate_assignment_against_policy(assignment, policy=policy)
    )


def initial_confidence_state(
    assignment: LaneAssignment, *, citation_count: int
) -> ConfidenceState:
    """Creation-time confidence marking for a graph-entering assignment.

    backfilled nodes default advisory-only and are never auto-promotable —
    the advisory-only marking here is the cortex#362 enforcement of that
    rule at node creation, capped by ``confidence.ADVISORY_ONLY_TIER_CAP``
    regardless of source type (cortex#375 consumes the marking). Dropped
    material has no confidence state because it never becomes graph state.
    """

    if not isinstance(assignment, LaneAssignment):
        raise LaneAssignmentError(
            f"assignment must be a LaneAssignment, got {type(assignment).__name__}"
        )
    if not assignment.enters_graph:
        raise LaneAssignmentError(
            "dropped-lane material never becomes graph state, so it has no "
            "confidence state; log it with dropped_chatter_record "
            f"(rule {assignment.rule_citation})"
        )
    enforce_backfill_advisory_only(assignment)
    if assignment.advisory_only:
        # Consume the cortex#316 cap: an advisory-only node starts at the
        # lower of the advisory default and ADVISORY_ONLY_TIER_CAP, so a
        # future raise of the default tier can never outrun the cap.
        tier = min(DEFAULT_CONFIDENCE_TIER, ADVISORY_ONLY_TIER_CAP, key=tier_rank)
    else:
        tier = DEFAULT_CONFIDENCE_TIER
    return ConfidenceState(
        tier=tier,
        confirmation_count=0,
        citation_count=citation_count,
        override_count_in_window=0,
        last_transition_reason=f"lane-assignment:{assignment.rule_citation}",
        advisory_only=assignment.advisory_only,
    )


def dropped_chatter_record(assignment: LaneAssignment, *, excerpt: str) -> DroppedChatter:
    """Build the log record for dropped-lane material: reason code + hash.

    Dropped chatter is logged with a machine-readable reason code and never
    becomes graph state; the reason code cites the policy rule that dropped
    the material so replay can verify every drop. Graph-eligible material is
    refused — routing a structured or provisional assignment into the drop
    log would be a silent drop.
    """

    if not isinstance(assignment, LaneAssignment):
        raise LaneAssignmentError(
            f"assignment must be a LaneAssignment, got {type(assignment).__name__}"
        )
    if assignment.lane is not Lane.DROPPED:
        raise LaneAssignmentError(
            f"{assignment.lane.value!r} lane material enters the decision graph; "
            "logging it as dropped chatter would silently drop graph-eligible "
            f"material (rule {assignment.rule_citation})"
        )
    if not excerpt.strip():
        raise LaneAssignmentError(
            "dropped chatter requires the dropped excerpt text; an empty excerpt "
            "is unauditable"
        )
    return DroppedChatter(
        reason_code=f"{DROPPED_CHATTER_REASON_PREFIX}:{assignment.rule_citation}",
        excerpt_hash=content_hash(excerpt),
    )


def candidate_proposed_event(
    assignment: LaneAssignment,
    candidate: DeriveCandidate,
    *,
    tenant_id: str,
    source_id: str,
    actor: ActorRef,
    occurred_at: datetime,
    source_event_external_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> LedgerEvent:
    """Build the persistable graph-entry event for a lane-assigned candidate.

    Stamps the lane assignment (lane, auto_promotable, backfilled, rule
    citation) and the creation-time confidence marking onto the
    ``candidate.proposed`` payload, so every downstream consumer sees which
    policy rule admitted the node and at what trust level it entered. The
    entry status is derived from the lane's policy table, never chosen here.

    Raises for any dropped assignment: a dropped assignment cannot produce
    a persistable event, so dropped material can never become a
    ``decision_nodes`` / ``decision_versions`` / ``decision_edges`` row.

    Replay rule: ``occurred_at`` must be source-derived (commit timestamp,
    file mtime), never wall-clock. The idempotency key is derived from the
    stamped payload plus the cited span hashes, so identical inputs replay
    to the identical event and changed citations produce a new event rather
    than colliding with the old one.
    """

    if not isinstance(assignment, LaneAssignment):
        raise LaneAssignmentError(
            f"assignment must be a LaneAssignment, got {type(assignment).__name__}"
        )
    if not isinstance(candidate, DeriveCandidate):
        raise LaneAssignmentError(
            f"candidate must be a DeriveCandidate, got {type(candidate).__name__}"
        )
    if not assignment.enters_graph:
        raise LaneAssignmentError(
            "dropped-lane material never becomes graph state; no persistable "
            "event can exist for it — log it with dropped_chatter_record "
            f"(rule {assignment.rule_citation})"
        )
    enforce_backfill_advisory_only(assignment)
    entry_statuses = allowed_entry_statuses(assignment.lane)
    if len(entry_statuses) != 1:
        raise LaneAssignmentError(
            f"lane {assignment.lane.value!r} permits {len(entry_statuses)} entry "
            "statuses; graph entry requires exactly one so the stamped status "
            "cannot be chosen silently"
        )
    entry_status = entry_statuses[0]
    event_type = validate_entry_status(assignment.lane, entry_status)
    confidence = initial_confidence_state(
        assignment, citation_count=len(candidate.span_hashes)
    )
    payload: dict[str, Any] = {
        "decision_text": candidate.decision_text,
        "entry_status": entry_status.value,
        "proposed_scopes": [scope.as_payload() for scope in candidate.proposed_scopes],
        LANE_ASSIGNMENT_PAYLOAD_KEY: assignment.as_payload(),
        CONFIDENCE_PAYLOAD_KEY: confidence.as_payload(),
    }
    idempotency_key = derive_idempotency_key(
        source_id=source_id,
        event_type=event_type,
        source_event_external_id=source_event_external_id,
        payload={**payload, "source_span_hashes": list(candidate.span_hashes)},
    )
    return LedgerEvent(
        tenant_id=tenant_id,
        source_id=source_id,
        event_type=event_type,
        actor=actor,
        occurred_at=occurred_at,
        idempotency_key=idempotency_key,
        payload=payload,
        source_span_hashes=candidate.span_hashes,
        source_event_external_id=source_event_external_id,
        metadata=metadata if metadata is not None else {},
    )
