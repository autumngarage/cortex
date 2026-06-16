"""Frozen eval-fixture format for the Stage 0 evaluator harness.

This module owns three contracts (cortex#332):

1. **The fixture schema** — diffs, decision context, labels, and expected
   findings, serialized as canonical JSON so fixtures round-trip
   byte-identically.
2. **The label taxonomy** — the classes hand-graders assign to emitted
   findings. Downstream consumers (#333 labeling workflow, #342 FP-vs-tone
   metrics, #378 hand-grade gate, #380 Stage 2 override classification)
   consume this taxonomy; none may redefine it.
3. **The version gate** — every fixture carries
   ``fixture_schema_version``; loading an unknown version fails visibly.

Field names mirror the shipped hosted substrate (``schema.py``,
``provenance.py``, ``scopes.py``, ``decisions_for_diff.py``) where they
overlap. Fixtures are file-based artifacts, not DB rows: the substrate is
non-executing until cortex#472 lands the first Postgres execution path.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cortex.hosted.scopes import ScopeType, normalize_scope_value

EVAL_FIXTURE_SCHEMA_VERSION = 1

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_GIT_SHA_RE = re.compile(r"^[a-f0-9]{7,40}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class FixtureValidationError(ValueError):
    """Raised when fixture material cannot support replayable evaluation."""


class LabelClass(StrEnum):
    """The hand-grading label taxonomy owned by cortex#332.

    Two independent axes: correctness (is the finding factually right?) and
    usefulness (would a reviewer act on it?). The split keeps the precision
    metric (#342) from being polluted by tone/preference feedback — training
    on conflated labels drives the system toward silence.

    Stage 2 (#380) extends this taxonomy with override-context classes.
    Those classes record why a human overrode an advisory finding without
    treating the override as proof that the evaluator was imprecise or noisy.
    """

    CORRECT_USEFUL = "correct_useful"
    """Finding is factually right and a reviewer would act on it."""

    CORRECT_NOT_USEFUL = "correct_not_useful"
    """Factually right but noise here (tone/preference/severity objection);
    counts against usefulness, never against precision."""

    INCORRECT_PRECISION = "incorrect_precision"
    """The factual claim is wrong (false contradiction, stale citation);
    the account-lethal class — counts against precision."""

    MISSED_EXPECTED = "missed_expected"
    """An expected finding the evaluator failed to emit (false negative,
    assessed during replay against ``expected_findings``)."""

    OVERRIDE_CHANGED_DECISION = "override_changed_decision"
    """A human override because the cited decision changed or went stale after
    the review context was produced; visible context, not a quality-gate label."""

    OVERRIDE_EMERGENCY_EXCEPTION = "override_emergency_exception"
    """A human override because an explicitly accepted emergency/one-off
    exception applies; visible context, not a quality-gate label."""


class FindingClass(StrEnum):
    """Evaluator finding classes a fixture can expect (Stage 0 vocabulary)."""

    CONTRADICTS_PRIOR_DECISION = "contradicts-prior-decision"
    REVERSES_SUPERSEDED_PATTERN = "reverses-superseded-pattern"
    CITES_MISSING_PATH = "cites-missing-path"
    OMITTED_LOAD_BEARING_CONSTRAINT = "omitted-load-bearing-constraint"


class DecisionStatus(StrEnum):
    """Mirrors the ``decision_nodes.status`` enum in hosted ``schema.py``."""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    STALE = "stale"


@dataclass(frozen=True)
class FixtureSourceSpan:
    """Citable excerpt mirroring hosted ``provenance.SourceSpan`` fields.

    ``span_hash`` is computed over the same material as the hosted span hash
    (document hash, offsets, excerpt hash) so fixture citations are directly
    comparable with ledger citations.
    """

    source_document_hash: str
    start_offset: int
    end_offset: int
    excerpt: str
    permalink: str

    def __post_init__(self) -> None:
        _require_hash("source_document_hash", self.source_document_hash)
        if self.start_offset < 0:
            raise FixtureValidationError("start_offset must be >= 0")
        if self.end_offset <= self.start_offset:
            raise FixtureValidationError("end_offset must be greater than start_offset")
        _require_non_empty("excerpt", self.excerpt)
        if len(self.excerpt) != self.end_offset - self.start_offset:
            raise FixtureValidationError("excerpt length must match offsets")
        _require_non_empty("permalink", self.permalink)

    @property
    def span_hash(self) -> str:
        return _hash_mapping(
            {
                "end_offset": self.end_offset,
                "excerpt_hash": hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest(),
                "source_document_hash": self.source_document_hash,
                "start_offset": self.start_offset,
            }
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "end_offset": self.end_offset,
            "excerpt": self.excerpt,
            "permalink": self.permalink,
            "source_document_hash": self.source_document_hash,
            "span_hash": self.span_hash,
            "start_offset": self.start_offset,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> FixtureSourceSpan:
        span = cls(
            source_document_hash=_get_str(payload, "source_document_hash"),
            start_offset=_get_int(payload, "start_offset"),
            end_offset=_get_int(payload, "end_offset"),
            excerpt=_get_str(payload, "excerpt"),
            permalink=_get_str(payload, "permalink"),
        )
        recorded = payload.get("span_hash")
        if recorded is not None and recorded != span.span_hash:
            raise FixtureValidationError(
                "span_hash does not match span material; fixture citations must be recomputable"
            )
        return span


@dataclass(frozen=True)
class FixtureScope:
    """One structural scope claim, reusing the hosted 9-type vocabulary."""

    scope_type: ScopeType
    value: str

    def __post_init__(self) -> None:
        _require_non_empty("value", self.value)

    @property
    def normalized_value(self) -> str:
        return normalize_scope_value(self.scope_type, self.value)

    def as_payload(self) -> dict[str, str]:
        return {
            "normalized_value": self.normalized_value,
            "scope_type": self.scope_type.value,
            "value": self.value,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> FixtureScope:
        try:
            scope_type = ScopeType(_get_str(payload, "scope_type"))
        except ValueError as exc:
            raise FixtureValidationError(f"unknown scope_type: {payload.get('scope_type')!r}") from exc
        return cls(scope_type=scope_type, value=_get_str(payload, "value"))


@dataclass(frozen=True)
class FixtureDecision:
    """Decision-context entry mirroring decision_versions + decision_scopes."""

    decision_id: str
    decision_text: str
    status: DecisionStatus
    source_timestamp: str
    spans: tuple[FixtureSourceSpan, ...]
    scopes: tuple[FixtureScope, ...] = ()
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        _require_id("decision_id", self.decision_id)
        _require_non_empty("decision_text", self.decision_text)
        _require_non_empty("source_timestamp", self.source_timestamp)
        if not self.spans:
            raise FixtureValidationError(
                "decisions require at least one provenance span "
                "(mirrors decision_versions cardinality(source_span_hashes) > 0)"
            )
        if self.superseded_by is not None:
            _require_id("superseded_by", self.superseded_by)
            if self.status is not DecisionStatus.SUPERSEDED:
                raise FixtureValidationError(
                    "superseded_by requires status 'superseded'"
                )

    @property
    def span_hashes(self) -> tuple[str, ...]:
        return tuple(span.span_hash for span in self.spans)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision_id": self.decision_id,
            "decision_text": self.decision_text,
            "scopes": [scope.as_payload() for scope in self.scopes],
            "source_timestamp": self.source_timestamp,
            "spans": [span.as_payload() for span in self.spans],
            "status": self.status.value,
        }
        if self.superseded_by is not None:
            payload["superseded_by"] = self.superseded_by
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> FixtureDecision:
        try:
            status = DecisionStatus(_get_str(payload, "status"))
        except ValueError as exc:
            raise FixtureValidationError(f"unknown decision status: {payload.get('status')!r}") from exc
        return cls(
            decision_id=_get_str(payload, "decision_id"),
            decision_text=_get_str(payload, "decision_text"),
            status=status,
            source_timestamp=_get_str(payload, "source_timestamp"),
            spans=tuple(
                FixtureSourceSpan.from_payload(item) for item in _get_list(payload, "spans")
            ),
            scopes=tuple(
                FixtureScope.from_payload(item) for item in _get_list(payload, "scopes", default=())
            ),
            superseded_by=_get_optional_str(payload, "superseded_by"),
        )


@dataclass(frozen=True)
class FixtureDiff:
    """The change under evaluation; fields align with
    ``DecisionsForDiffQuery.from_diff_metadata`` inputs so #363's extractor
    output can be frozen alongside the raw patch."""

    repo_owner: str
    repo_name: str
    base_sha: str
    head_sha: str
    patch: str
    changed_paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    config_keys: tuple[str, ...] = ()
    issue_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("repo_owner", self.repo_owner)
        _require_non_empty("repo_name", self.repo_name)
        _require_git_sha("base_sha", self.base_sha)
        _require_git_sha("head_sha", self.head_sha)
        _require_non_empty("patch", self.patch)

    def as_payload(self) -> dict[str, Any]:
        return {
            "base_sha": self.base_sha,
            "changed_paths": list(self.changed_paths),
            "config_keys": list(self.config_keys),
            "head_sha": self.head_sha,
            "issue_refs": list(self.issue_refs),
            "patch": self.patch,
            "repo_name": self.repo_name,
            "repo_owner": self.repo_owner,
            "symbols": list(self.symbols),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> FixtureDiff:
        return cls(
            repo_owner=_get_str(payload, "repo_owner"),
            repo_name=_get_str(payload, "repo_name"),
            base_sha=_get_str(payload, "base_sha"),
            head_sha=_get_str(payload, "head_sha"),
            patch=_get_str(payload, "patch"),
            changed_paths=_get_str_tuple(payload, "changed_paths"),
            symbols=_get_str_tuple(payload, "symbols"),
            config_keys=_get_str_tuple(payload, "config_keys"),
            issue_refs=_get_str_tuple(payload, "issue_refs"),
        )


@dataclass(frozen=True)
class ExpectedFinding:
    """A finding the evaluator should emit for this fixture's diff."""

    finding_id: str
    finding_class: FindingClass
    decision_id: str
    cited_span_hashes: tuple[str, ...]
    summary: str
    suggested_repair: str | None = None

    def __post_init__(self) -> None:
        _require_id("finding_id", self.finding_id)
        _require_id("decision_id", self.decision_id)
        _require_non_empty("summary", self.summary)
        if not self.cited_span_hashes:
            raise FixtureValidationError(
                "expected findings require at least one cited span hash (cited, never a vibe)"
            )
        for value in self.cited_span_hashes:
            _require_hash("cited_span_hashes", value)
        if self.suggested_repair is not None:
            _require_non_empty("suggested_repair", self.suggested_repair)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cited_span_hashes": list(self.cited_span_hashes),
            "decision_id": self.decision_id,
            "finding_class": self.finding_class.value,
            "finding_id": self.finding_id,
            "summary": self.summary,
        }
        if self.suggested_repair is not None:
            payload["suggested_repair"] = self.suggested_repair
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ExpectedFinding:
        try:
            finding_class = FindingClass(_get_str(payload, "finding_class"))
        except ValueError as exc:
            raise FixtureValidationError(
                f"unknown finding_class: {payload.get('finding_class')!r}"
            ) from exc
        return cls(
            finding_id=_get_str(payload, "finding_id"),
            finding_class=finding_class,
            decision_id=_get_str(payload, "decision_id"),
            cited_span_hashes=_get_str_tuple(payload, "cited_span_hashes"),
            summary=_get_str(payload, "summary"),
            suggested_repair=_get_optional_str(payload, "suggested_repair"),
        )


@dataclass(frozen=True)
class FixtureLabel:
    """One hand-grading judgment attached to an expected or emitted finding."""

    finding_id: str
    label: LabelClass
    grader: str
    graded_at: str
    note: str | None = None

    def __post_init__(self) -> None:
        _require_id("finding_id", self.finding_id)
        _require_non_empty("grader", self.grader)
        _require_non_empty("graded_at", self.graded_at)
        if self.note is not None:
            _require_non_empty("note", self.note)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "finding_id": self.finding_id,
            "graded_at": self.graded_at,
            "grader": self.grader,
            "label": self.label.value,
        }
        if self.note is not None:
            payload["note"] = self.note
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> FixtureLabel:
        try:
            label = LabelClass(_get_str(payload, "label"))
        except ValueError as exc:
            raise FixtureValidationError(f"unknown label class: {payload.get('label')!r}") from exc
        return cls(
            finding_id=_get_str(payload, "finding_id"),
            label=label,
            grader=_get_str(payload, "grader"),
            graded_at=_get_str(payload, "graded_at"),
            note=_get_optional_str(payload, "note"),
        )


@dataclass(frozen=True)
class EvalFixture:
    """One frozen evaluation scenario: diff + decision context + expectations."""

    fixture_id: str
    diff: FixtureDiff
    decisions: tuple[FixtureDecision, ...]
    expected_findings: tuple[ExpectedFinding, ...] = ()
    labels: tuple[FixtureLabel, ...] = ()
    fixture_schema_version: int = EVAL_FIXTURE_SCHEMA_VERSION
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id("fixture_id", self.fixture_id)
        if self.fixture_schema_version != EVAL_FIXTURE_SCHEMA_VERSION:
            raise FixtureValidationError(
                f"unknown fixture_schema_version {self.fixture_schema_version!r}; "
                f"this loader supports only {EVAL_FIXTURE_SCHEMA_VERSION} — "
                "no silent fallback for unrecognized fixture versions"
            )
        if not self.decisions:
            raise FixtureValidationError("fixtures require at least one decision")
        if not isinstance(self.metadata, Mapping):
            raise FixtureValidationError("metadata must be a JSON object")

        decision_ids = [decision.decision_id for decision in self.decisions]
        if len(set(decision_ids)) != len(decision_ids):
            raise FixtureValidationError("decision_id values must be unique")
        known_decisions = set(decision_ids)

        supersede_targets = {
            decision.superseded_by
            for decision in self.decisions
            if decision.superseded_by is not None
        }
        missing_targets = supersede_targets - known_decisions
        if missing_targets:
            raise FixtureValidationError(
                f"superseded_by references unknown decisions: {sorted(missing_targets)}"
            )

        finding_ids = [finding.finding_id for finding in self.expected_findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise FixtureValidationError("finding_id values must be unique")
        known_findings = set(finding_ids)

        known_spans = {
            span_hash for decision in self.decisions for span_hash in decision.span_hashes
        }
        for finding in self.expected_findings:
            if finding.decision_id not in known_decisions:
                raise FixtureValidationError(
                    f"expected finding {finding.finding_id!r} references unknown "
                    f"decision {finding.decision_id!r}"
                )
            uncited = set(finding.cited_span_hashes) - known_spans
            if uncited:
                raise FixtureValidationError(
                    f"expected finding {finding.finding_id!r} cites span hashes absent "
                    f"from the fixture's decision spans: {sorted(uncited)}"
                )

        for label in self.labels:
            if label.finding_id not in known_findings:
                raise FixtureValidationError(
                    f"label references unknown finding {label.finding_id!r}"
                )

        object.__setattr__(self, "metadata", dict(self.metadata))

    def as_payload(self) -> dict[str, Any]:
        return {
            "decisions": [decision.as_payload() for decision in self.decisions],
            "diff": self.diff.as_payload(),
            "expected_findings": [finding.as_payload() for finding in self.expected_findings],
            "fixture_id": self.fixture_id,
            "fixture_schema_version": self.fixture_schema_version,
            "labels": [label.as_payload() for label in self.labels],
            "metadata": dict(self.metadata),
        }

    def to_canonical_json(self) -> str:
        """Serialize deterministically; identical fixtures are identical bytes."""

        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n"
        )

    @property
    def fixture_hash(self) -> str:
        return hashlib.sha256(self.to_canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> EvalFixture:
        if not isinstance(payload, Mapping):
            raise FixtureValidationError("fixture payload must be a JSON object")
        raw_version = payload.get("fixture_schema_version")
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise FixtureValidationError(
                "fixture_schema_version must be an integer; refusing to guess"
            )
        if raw_version != EVAL_FIXTURE_SCHEMA_VERSION:
            raise FixtureValidationError(
                f"unknown fixture_schema_version {raw_version!r}; this loader supports "
                f"only {EVAL_FIXTURE_SCHEMA_VERSION} — no silent fallback for "
                "unrecognized fixture versions"
            )
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise FixtureValidationError("metadata must be a JSON object")
        return cls(
            fixture_id=_get_str(payload, "fixture_id"),
            diff=FixtureDiff.from_payload(_get_mapping(payload, "diff")),
            decisions=tuple(
                FixtureDecision.from_payload(item) for item in _get_list(payload, "decisions")
            ),
            expected_findings=tuple(
                ExpectedFinding.from_payload(item)
                for item in _get_list(payload, "expected_findings", default=())
            ),
            labels=tuple(
                FixtureLabel.from_payload(item)
                for item in _get_list(payload, "labels", default=())
            ),
            fixture_schema_version=raw_version,
            metadata=metadata,
        )

    @classmethod
    def from_json(cls, text: str) -> EvalFixture:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FixtureValidationError(f"fixture is not valid JSON: {exc}") from exc
        return cls.from_payload(payload)


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FixtureValidationError(f"{name} must be a non-empty string")


def _require_id(name: str, value: str) -> None:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise FixtureValidationError(
            f"{name} must be a lowercase kebab-case identifier; got {value!r}"
        )


def _require_hash(name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise FixtureValidationError(f"{name} must be a sha256 hex string")


def _require_git_sha(name: str, value: str) -> None:
    if not isinstance(value, str) or not _GIT_SHA_RE.match(value):
        raise FixtureValidationError(f"{name} must be an abbreviated or full git sha")


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _get_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise FixtureValidationError(f"{key} must be a string; got {type(value).__name__}")
    return value


def _get_optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise FixtureValidationError(f"{key} must be a string when present")
    return value


def _get_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise FixtureValidationError(f"{key} must be an integer")
    return value


def _get_list(
    payload: Mapping[str, Any], key: str, default: tuple[Any, ...] | None = None
) -> list[Any]:
    value = payload.get(key)
    if value is None and default is not None:
        return list(default)
    if not isinstance(value, list):
        raise FixtureValidationError(f"{key} must be a list")
    for item in value:
        if not isinstance(item, Mapping):
            raise FixtureValidationError(f"{key} entries must be JSON objects")
    return value


def _get_str_tuple(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise FixtureValidationError(f"{key} must be a list of strings")
    return tuple(value)


def _get_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise FixtureValidationError(f"{key} must be a JSON object")
    return value
