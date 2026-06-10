"""Promotion-lane policy contract over the shipped decision lifecycle states.

This module is the versioned policy artifact for cortex#315. It defines the
candidate lanes (structured / provisional / dropped), the source-class entry
condition for each lane, the closed set of legal ``decision_nodes.status``
transitions per lane, the auto-promotion boundary, and — for every legal
transition — the append-only ledger event that records it. Transitions not
listed here are illegal by default; cortex#360 tests that closed set
adversarially. Derive-time lane assignment (cortex#358) and backfill
enforcement (cortex#362) consume this contract; they do not restate it.

The Obsidian master-plan non-negotiable is encoded as policy, not prose:
backfilled nodes default advisory-only and are never auto-promotable.
``LaneAssignment`` makes the violating state unrepresentable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from cortex.hosted.ledger_events import LedgerEventType

LANE_POLICY_VERSION = 1


class LanePolicyValidationError(ValueError):
    """Raised when a lane assignment or status transition violates the policy."""


class Lane(StrEnum):
    """Candidate lanes from the master plan: trustworthy, advisory, discarded."""

    STRUCTURED = "structured"
    PROVISIONAL = "provisional"
    DROPPED = "dropped"


class DecisionStatus(StrEnum):
    """Mirror of the shipped ``decision_nodes.status`` CHECK in ``schema.py``.

    Canonical owner: ``create_schema_sql()`` (HOSTED_SCHEMA_VERSION 6). This
    enum introduces no new lifecycle states; a drift test asserts these values
    match the DDL CHECK verbatim.
    """

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"


class DeriveSourceType(StrEnum):
    """Source classes the Stage 0 repo-native derive path extracts from."""

    AGENT_INSTRUCTIONS = "agent_instructions"
    ADR = "adr"
    CODEOWNERS = "codeowners"
    COMMIT_MESSAGE = "commit_message"
    PR_DESCRIPTION = "pr_description"
    PR_REVIEW_COMMENT = "pr_review_comment"
    UNATTRIBUTED_CHATTER = "unattributed_chatter"


@dataclass(frozen=True)
class StatusTransition:
    """One legal lifecycle transition and the ledger event that records it."""

    from_status: DecisionStatus
    to_status: DecisionStatus
    ledger_event: LedgerEventType

    def __post_init__(self) -> None:
        if self.from_status is self.to_status:
            raise LanePolicyValidationError("a status transition must change the status")


# Entry into the graph is recorded by candidate.proposed; it is not a
# status-to-status transition because the node does not exist before it.
LANE_ENTRY_EVENT = LedgerEventType.CANDIDATE_PROPOSED

# Statuses with no outgoing transitions. Corrections to a rejected or
# superseded node append a new node; the old one never resurrects.
TERMINAL_STATUSES = frozenset({DecisionStatus.REJECTED, DecisionStatus.SUPERSEDED})

_GRAPH_STATUS_TRANSITIONS: tuple[StatusTransition, ...] = (
    StatusTransition(
        DecisionStatus.CANDIDATE, DecisionStatus.CONFIRMED, LedgerEventType.DECISION_CONFIRMED
    ),
    StatusTransition(
        DecisionStatus.CANDIDATE, DecisionStatus.REJECTED, LedgerEventType.DECISION_REJECTED
    ),
    StatusTransition(
        DecisionStatus.CANDIDATE, DecisionStatus.SUPERSEDED, LedgerEventType.DECISION_SUPERSEDED
    ),
    StatusTransition(
        DecisionStatus.CANDIDATE, DecisionStatus.STALE, LedgerEventType.STALE_MARKED
    ),
    StatusTransition(
        DecisionStatus.CONFIRMED, DecisionStatus.SUPERSEDED, LedgerEventType.DECISION_SUPERSEDED
    ),
    StatusTransition(
        DecisionStatus.CONFIRMED, DecisionStatus.STALE, LedgerEventType.STALE_MARKED
    ),
    StatusTransition(
        DecisionStatus.STALE, DecisionStatus.CONFIRMED, LedgerEventType.DECISION_CONFIRMED
    ),
    StatusTransition(
        DecisionStatus.STALE, DecisionStatus.SUPERSEDED, LedgerEventType.DECISION_SUPERSEDED
    ),
)

# Structured and provisional lanes share one transition table — the lanes
# differ at the auto-promotion boundary (actor), not in reachable states.
# The dropped lane has no entry statuses and no transitions: dropped chatter
# is logged with a reason code and never becomes graph state.
LANE_STATUS_TRANSITIONS: Mapping[Lane, tuple[StatusTransition, ...]] = MappingProxyType(
    {
        Lane.STRUCTURED: _GRAPH_STATUS_TRANSITIONS,
        Lane.PROVISIONAL: _GRAPH_STATUS_TRANSITIONS,
        Lane.DROPPED: (),
    }
)

LANE_ENTRY_STATUSES: Mapping[Lane, tuple[DecisionStatus, ...]] = MappingProxyType(
    {
        Lane.STRUCTURED: (DecisionStatus.CANDIDATE,),
        Lane.PROVISIONAL: (DecisionStatus.CANDIDATE,),
        Lane.DROPPED: (),
    }
)

# The auto-promotion boundary is exactly one transition. Everything else
# that reaches `confirmed` requires a human confirmation event.
AUTO_PROMOTION_FROM_STATUS = DecisionStatus.CANDIDATE
AUTO_PROMOTION_TO_STATUS = DecisionStatus.CONFIRMED


@dataclass(frozen=True)
class SourceTypeRule:
    """Per-source-type lane entry condition and auto-promotion permission."""

    source_type: DeriveSourceType
    lane: Lane
    auto_promote: bool = False

    def __post_init__(self) -> None:
        if self.auto_promote and self.lane is not Lane.STRUCTURED:
            raise LanePolicyValidationError(
                f"auto_promote is only legal in the structured lane; "
                f"{self.source_type.value} is assigned to {self.lane.value}"
            )


@dataclass(frozen=True)
class LaneAssignment:
    """One candidate's lane assignment with its policy-rule citation.

    The advisory-only-backfill non-negotiable lives in ``__post_init__``:
    a backfilled or non-structured assignment that claims auto-promotability
    cannot be constructed.
    """

    policy_version: int
    source_type: DeriveSourceType
    lane: Lane
    backfilled: bool
    advisory_only: bool
    auto_promotable: bool
    enters_graph: bool
    rule_citation: str

    def __post_init__(self) -> None:
        if self.policy_version < 1:
            raise LanePolicyValidationError("policy_version must be >= 1")
        if not self.rule_citation.strip():
            raise LanePolicyValidationError("rule_citation must not be empty")
        if self.enters_graph != (self.lane is not Lane.DROPPED):
            raise LanePolicyValidationError(
                "enters_graph must be true exactly when the lane is not dropped"
            )
        if self.advisory_only != (self.backfilled or self.lane is not Lane.STRUCTURED):
            raise LanePolicyValidationError(
                "advisory_only must hold for every backfilled or non-structured assignment"
            )
        if self.auto_promotable and self.backfilled:
            raise LanePolicyValidationError(
                "backfilled nodes default advisory-only and are never auto-promotable"
            )
        if self.auto_promotable and self.lane is not Lane.STRUCTURED:
            raise LanePolicyValidationError(
                "only structured-lane assignments may be auto-promotable"
            )

    def as_payload(self) -> dict[str, Any]:
        """Return the JSON-safe payload recorded alongside ledger events."""

        return {
            "advisory_only": self.advisory_only,
            "auto_promotable": self.auto_promotable,
            "backfilled": self.backfilled,
            "enters_graph": self.enters_graph,
            "lane": self.lane.value,
            "policy_version": self.policy_version,
            "rule_citation": self.rule_citation,
            "source_type": self.source_type.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> LaneAssignment:
        """Rebuild an assignment from a payload, failing closed on bad fields."""

        try:
            source_type = DeriveSourceType(_payload_field(payload, "source_type", str))
            lane = Lane(_payload_field(payload, "lane", str))
        except ValueError as exc:
            if isinstance(exc, LanePolicyValidationError):
                raise
            raise LanePolicyValidationError(
                f"lane assignment payload is invalid: {exc}"
            ) from exc
        return cls(
            policy_version=_payload_field(payload, "policy_version", int),
            source_type=source_type,
            lane=lane,
            backfilled=_payload_field(payload, "backfilled", bool),
            advisory_only=_payload_field(payload, "advisory_only", bool),
            auto_promotable=_payload_field(payload, "auto_promotable", bool),
            enters_graph=_payload_field(payload, "enters_graph", bool),
            rule_citation=_payload_field(payload, "rule_citation", str),
        )


def _payload_field(payload: Mapping[str, Any], key: str, expected: type) -> Any:
    """Return a payload field of the exact expected type, failing closed."""

    if key not in payload:
        raise LanePolicyValidationError(
            f"lane assignment payload is invalid: missing {key!r}"
        )
    value = payload[key]
    # bool subclasses int; an int field carrying True/False is a tampered payload.
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise LanePolicyValidationError(
            f"lane assignment payload is invalid: {key!r} must be {expected.__name__}"
        )
    return value


@dataclass(frozen=True)
class LanePolicy:
    """Versioned promotion-lane policy: one rule per derive source type."""

    policy_version: int
    rules: Mapping[DeriveSourceType, SourceTypeRule]

    def __post_init__(self) -> None:
        if self.policy_version < 1:
            raise LanePolicyValidationError("policy_version must be >= 1")
        missing = set(DeriveSourceType) - set(self.rules)
        if missing:
            missing_names = ", ".join(sorted(source.value for source in missing))
            raise LanePolicyValidationError(
                f"lane policy must be total over derive source types; missing: {missing_names}"
            )
        for source_type, rule in self.rules.items():
            if rule.source_type is not source_type:
                raise LanePolicyValidationError(
                    f"rule for {source_type.value} names {rule.source_type.value}"
                )
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))

    def rule_for(self, source_type: DeriveSourceType) -> SourceTypeRule:
        # Totality is validated at construction, so this lookup cannot miss.
        return self.rules[source_type]

    def assign(self, source_type: DeriveSourceType, *, backfilled: bool) -> LaneAssignment:
        """Assign exactly one lane and return the citable assignment record."""

        rule = self.rule_for(source_type)
        return LaneAssignment(
            policy_version=self.policy_version,
            source_type=source_type,
            lane=rule.lane,
            backfilled=backfilled,
            advisory_only=backfilled or rule.lane is not Lane.STRUCTURED,
            auto_promotable=rule.auto_promote
            and rule.lane is Lane.STRUCTURED
            and not backfilled,
            enters_graph=rule.lane is not Lane.DROPPED,
            rule_citation=(
                f"cortex.hosted.lanes/v{self.policy_version}/{source_type.value}"
                f"/{rule.lane.value}"
            ),
        )


# Default lane policy. Structured auto-promoting sources are the human-ratified
# near-verbatim repo documents from the Tier-1 derive brief (CLAUDE.md/AGENTS.md,
# accepted ADRs, CODEOWNERS). Commit and PR prose is provisional: it enters the
# graph as advisory candidates and never auto-promotes — human confirmation is
# required. Unattributed chatter is dropped: logged with a reason code by
# cortex#358, never written as graph state.
DEFAULT_LANE_POLICY = LanePolicy(
    policy_version=LANE_POLICY_VERSION,
    rules={
        DeriveSourceType.AGENT_INSTRUCTIONS: SourceTypeRule(
            DeriveSourceType.AGENT_INSTRUCTIONS, Lane.STRUCTURED, auto_promote=True
        ),
        DeriveSourceType.ADR: SourceTypeRule(
            DeriveSourceType.ADR, Lane.STRUCTURED, auto_promote=True
        ),
        DeriveSourceType.CODEOWNERS: SourceTypeRule(
            DeriveSourceType.CODEOWNERS, Lane.STRUCTURED, auto_promote=True
        ),
        DeriveSourceType.COMMIT_MESSAGE: SourceTypeRule(
            DeriveSourceType.COMMIT_MESSAGE, Lane.PROVISIONAL
        ),
        DeriveSourceType.PR_DESCRIPTION: SourceTypeRule(
            DeriveSourceType.PR_DESCRIPTION, Lane.PROVISIONAL
        ),
        DeriveSourceType.PR_REVIEW_COMMENT: SourceTypeRule(
            DeriveSourceType.PR_REVIEW_COMMENT, Lane.PROVISIONAL
        ),
        DeriveSourceType.UNATTRIBUTED_CHATTER: SourceTypeRule(
            DeriveSourceType.UNATTRIBUTED_CHATTER, Lane.DROPPED
        ),
    },
)


def allowed_entry_statuses(lane: Lane) -> tuple[DecisionStatus, ...]:
    """Return the statuses a node may carry when it enters the graph."""

    return LANE_ENTRY_STATUSES[lane]


def validate_entry_status(lane: Lane, status: DecisionStatus) -> LedgerEventType:
    """Validate graph entry for a lane and return the recording ledger event."""

    if lane is Lane.DROPPED:
        raise LanePolicyValidationError(
            "dropped lane never enters the decision graph; log it with a reason code"
        )
    if status not in LANE_ENTRY_STATUSES[lane]:
        raise LanePolicyValidationError(
            f"{lane.value} lane cannot enter the graph as {status.value!r}; "
            f"allowed entry statuses: "
            f"{', '.join(entry.value for entry in LANE_ENTRY_STATUSES[lane])}"
        )
    return LANE_ENTRY_EVENT


def validate_status_transition(
    lane: Lane,
    from_status: DecisionStatus,
    to_status: DecisionStatus,
) -> LedgerEventType:
    """Validate a lifecycle transition and return the recording ledger event.

    Transitions not listed in ``LANE_STATUS_TRANSITIONS`` are illegal by
    default; there is no permissive fallback.
    """

    if lane is Lane.DROPPED:
        raise LanePolicyValidationError(
            "dropped lane has no lifecycle transitions; it never becomes graph state"
        )
    for transition in LANE_STATUS_TRANSITIONS[lane]:
        if transition.from_status is from_status and transition.to_status is to_status:
            return transition.ledger_event
    raise LanePolicyValidationError(
        f"transition {from_status.value!r} -> {to_status.value!r} is not listed for the "
        f"{lane.value} lane; unlisted transitions are illegal by default"
    )


def validate_auto_promotion(assignment: LaneAssignment) -> LedgerEventType:
    """Validate unattended candidate -> confirmed promotion for an assignment.

    Returns the recording ledger event when the structured-source rule permits
    auto-promotion. Every refusal names the policy reason.
    """

    if not assignment.enters_graph:
        raise LanePolicyValidationError(
            "dropped lane never enters the decision graph, so nothing exists to promote"
        )
    if assignment.backfilled:
        raise LanePolicyValidationError(
            "backfilled nodes default advisory-only and are never auto-promotable; "
            "promotion requires a human confirmation event"
        )
    if assignment.lane is Lane.PROVISIONAL:
        raise LanePolicyValidationError(
            "provisional lane never auto-promotes; human confirmation is required"
        )
    if not assignment.auto_promotable:
        raise LanePolicyValidationError(
            f"source type {assignment.source_type.value!r} does not permit auto-promotion "
            f"under lane policy v{assignment.policy_version}"
        )
    return validate_status_transition(
        assignment.lane, AUTO_PROMOTION_FROM_STATUS, AUTO_PROMOTION_TO_STATUS
    )
