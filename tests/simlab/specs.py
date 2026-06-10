"""The simlab spec format: declarative, versioned, canonical JSON (cortex#520).

Two spec kinds, each a frozen dataclass with a fail-closed loader and a
byte-stable ``to_canonical_json()`` (sorted keys, two-space indent, trailing
newline — the same canonical-JSON discipline as ``EvalFixture`` and the
recorded-response store):

- **Archetype** (``kind: "archetype"``): one synthetic project — fixed
  tenant/source identity, a commit-by-commit git history with deterministic
  authors and timestamps, and committed ``gh``-shaped PR fixture JSON for
  the PR gatherers. One archetype spec materializes to one repo; the same
  spec twice materializes to the identical derive ``event_hash`` set.
- **Scenario** (``kind: "scenario"``): one (repo-state, diff, expected
  outcome) triple — which archetype to materialize, which derived decisions
  a human confirmed/superseded, an optional post-derive working-tree edit
  (the span-drift case), the diff under review, the findings a correct
  evaluator must emit, and the replay-report numbers the pipeline must
  produce.

Determinism contract (the reason ``now()`` is unrepresentable here): every
timestamp is a literal in the spec, validated timezone-aware. Commit shas,
file mtimes, derive event hashes, fixture hashes, and recording input
hashes all flow from spec literals and file bytes — never from the clock.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from cortex.hosted.eval_fixtures import FindingClass
from cortex.hosted.replay_runner import OmissionStage
from cortex.manifest import DEFAULT_BUDGET_TOKENS

SIMLAB_SPEC_SCHEMA_VERSION = 1
ARCHETYPE_KIND = "archetype"
SCENARIO_KIND = "scenario"

# The three founder-named archetypes (#520). The loader accepts any archetype
# id of the right shape; the generator tests pin that these three ship.
CLEAN_SHOP = "clean-shop"
CHATTY_STARTUP = "chatty-startup"
LEGACY_MIGRATION = "legacy-migration"
SHIPPED_ARCHETYPE_IDS = (CHATTY_STARTUP, CLEAN_SHOP, LEGACY_MIGRATION)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_GIT_SHA_RE = re.compile(r"^[a-f0-9]{7,40}$")

SIMLAB_DIR = Path(__file__).parent
ARCHETYPES_DIR = SIMLAB_DIR / "archetypes"
SCENARIOS_DIR = SIMLAB_DIR / "scenarios"


class SimlabSpecError(ValueError):
    """Raised when a simlab spec cannot support deterministic materialization.

    Test tooling lives outside the ``cortex.hosted`` degradation taxonomy on
    purpose; the posture is the same — fail closed, name the offending spec
    field, never guess.
    """


def _require_non_empty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SimlabSpecError(f"{name} must be a non-empty string")
    return value


def _require_id(name: str, value: Any) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise SimlabSpecError(
            f"{name} must be a lowercase kebab-case identifier; got {value!r}"
        )
    return value


def _require_uuid(name: str, value: Any) -> str:
    text = _require_non_empty(name, value)
    try:
        UUID(text)
    except ValueError as exc:
        raise SimlabSpecError(f"{name} must be a UUID; got {value!r}") from exc
    return text


def _require_timestamp(name: str, value: Any) -> str:
    text = _require_non_empty(name, value)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SimlabSpecError(f"{name} must be an ISO-8601 timestamp; got {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SimlabSpecError(f"{name} must be timezone-aware; got {value!r}")
    return text


def _require_relative_path(name: str, value: Any) -> str:
    text = _require_non_empty(name, value)
    if text.startswith(("/", "\\")) or text.endswith("/"):
        raise SimlabSpecError(f"{name} must be a relative file path; got {value!r}")
    parts = text.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SimlabSpecError(
            f"{name} must not contain empty, '.', or '..' segments; got {value!r}"
        )
    return text


def _get_str(payload: Mapping[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise SimlabSpecError(
            f"{label}: {key} must be a string; got {type(value).__name__}"
        )
    return value


def _get_optional_str(payload: Mapping[str, Any], key: str, *, label: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SimlabSpecError(f"{label}: {key} must be a string when present")
    return value


def _get_int(payload: Mapping[str, Any], key: str, *, label: str, default: int | None = None) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SimlabSpecError(f"{label}: {key} must be an integer")
    return value


def _get_bool(payload: Mapping[str, Any], key: str, *, label: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise SimlabSpecError(f"{label}: {key} must be a boolean")
    return value


def _get_str_map(payload: Mapping[str, Any], key: str, *, label: str) -> dict[str, str]:
    value = payload.get(key, {})
    if not isinstance(value, Mapping):
        raise SimlabSpecError(f"{label}: {key} must be a JSON object")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not isinstance(raw_value, str):
            raise SimlabSpecError(f"{label}: {key} entries must map strings to strings")
        result[raw_key] = raw_value
    return result


def _get_str_list(payload: Mapping[str, Any], key: str, *, label: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SimlabSpecError(f"{label}: {key} must be a list of strings")
    return tuple(value)


def _get_object_list(
    payload: Mapping[str, Any], key: str, *, label: str
) -> list[Mapping[str, Any]]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise SimlabSpecError(f"{label}: {key} must be a list")
    for item in value:
        if not isinstance(item, Mapping):
            raise SimlabSpecError(f"{label}: {key} entries must be JSON objects")
    return value


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _check_header(payload: Mapping[str, Any], *, kind: str, label: str) -> None:
    if not isinstance(payload, Mapping):
        raise SimlabSpecError(f"{label}: spec payload must be a JSON object")
    raw_version = payload.get("simlab_spec_schema_version")
    if not isinstance(raw_version, int) or isinstance(raw_version, bool):
        raise SimlabSpecError(
            f"{label}: simlab_spec_schema_version must be an integer; refusing to guess"
        )
    if raw_version != SIMLAB_SPEC_SCHEMA_VERSION:
        raise SimlabSpecError(
            f"{label}: unknown simlab_spec_schema_version {raw_version!r}; this loader "
            f"supports only {SIMLAB_SPEC_SCHEMA_VERSION} — no silent fallback for "
            "unrecognized spec versions"
        )
    raw_kind = payload.get("kind")
    if raw_kind != kind:
        raise SimlabSpecError(
            f"{label}: spec kind must be {kind!r}; got {raw_kind!r}"
        )


# ---------------------------------------------------------------------------
# Archetype specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecCommit:
    """One deterministic commit: fixed author, fixed timestamp, literal files.

    ``authored_at`` doubles as the committer date, so the resulting commit
    sha is a pure function of the spec and the parent chain. Files map
    relative paths to full file content; ``deleted`` removes paths
    introduced by earlier commits.
    """

    message: str
    author_name: str
    author_email: str
    authored_at: str
    files: Mapping[str, str] = field(default_factory=dict)
    deleted: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("commit message", self.message)
        _require_non_empty("author_name", self.author_name)
        _require_non_empty("author_email", self.author_email)
        _require_timestamp("authored_at", self.authored_at)
        if not self.files and not self.deleted:
            raise SimlabSpecError(
                f"commit {self.message.splitlines()[0]!r} changes nothing; empty "
                "commits cannot anchor deterministic history"
            )
        for path, content in self.files.items():
            _require_relative_path("commit file path", path)
            if not content:
                raise SimlabSpecError(f"commit file {path!r} must not be empty")
        for path in self.deleted:
            _require_relative_path("commit deleted path", path)
        object.__setattr__(self, "files", dict(self.files))

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "author_email": self.author_email,
            "author_name": self.author_name,
            "authored_at": self.authored_at,
            "files": dict(sorted(self.files.items())),
            "message": self.message,
        }
        if self.deleted:
            payload["deleted"] = list(self.deleted)
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, label: str) -> SpecCommit:
        return cls(
            message=_get_str(payload, "message", label=label),
            author_name=_get_str(payload, "author_name", label=label),
            author_email=_get_str(payload, "author_email", label=label),
            authored_at=_get_str(payload, "authored_at", label=label),
            files=_get_str_map(payload, "files", label=label),
            deleted=_get_str_list(payload, "deleted", label=label),
        )


@dataclass(frozen=True)
class PrFixture:
    """Committed ``gh``-shaped JSON for the PR gatherers (cortex#355/#356).

    ``view`` is the ``gh pr view --json number,title,body,author,createdAt,
    url`` object; ``comments`` is the REST pulls-comments array. The shapes
    are validated where they are consumed (``pr_description_documents`` /
    ``pr_review_comment_documents`` fail closed on malformed payloads) —
    this dataclass only pins JSON-object structure and the PR number.
    """

    view: Mapping[str, Any]
    comments: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.view, Mapping):
            raise SimlabSpecError("pr fixture view must be a JSON object")
        number = self.view.get("number")
        if not isinstance(number, int) or isinstance(number, bool):
            raise SimlabSpecError("pr fixture view.number must be an integer")
        object.__setattr__(self, "view", dict(self.view))
        object.__setattr__(self, "comments", tuple(dict(item) for item in self.comments))

    @property
    def pr_number(self) -> int:
        number = self.view["number"]
        assert isinstance(number, int)  # validated in __post_init__
        return number

    def as_payload(self) -> dict[str, Any]:
        return {
            "comments": [dict(item) for item in self.comments],
            "view": dict(self.view),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, label: str) -> PrFixture:
        view = payload.get("view")
        if not isinstance(view, Mapping):
            raise SimlabSpecError(f"{label}: pr fixture view must be a JSON object")
        return cls(
            view=view,
            comments=tuple(_get_object_list(payload, "comments", label=label)),
        )


@dataclass(frozen=True)
class ArchetypeSpec:
    """One synthetic project: identity, history, and gathered-source fixtures."""

    archetype_id: str
    description: str
    tenant_id: str
    source_id: str
    commits: tuple[SpecCommit, ...]
    pr_fixtures: tuple[PrFixture, ...] = ()

    def __post_init__(self) -> None:
        _require_id("archetype_id", self.archetype_id)
        _require_non_empty("description", self.description)
        _require_uuid("tenant_id", self.tenant_id)
        _require_uuid("source_id", self.source_id)
        if not self.commits:
            raise SimlabSpecError("archetypes require at least one commit")
        pr_numbers = [fixture.pr_number for fixture in self.pr_fixtures]
        if len(set(pr_numbers)) != len(pr_numbers):
            raise SimlabSpecError("pr fixture numbers must be unique")

    @property
    def commit_gather_limit(self) -> int:
        """Gather every spec commit — the spec is the whole history."""

        return len(self.commits)

    def as_payload(self) -> dict[str, Any]:
        return {
            "archetype_id": self.archetype_id,
            "commits": [commit.as_payload() for commit in self.commits],
            "description": self.description,
            "kind": ARCHETYPE_KIND,
            "pr_fixtures": [fixture.as_payload() for fixture in self.pr_fixtures],
            "simlab_spec_schema_version": SIMLAB_SPEC_SCHEMA_VERSION,
            "source_id": self.source_id,
            "tenant_id": self.tenant_id,
        }

    def to_canonical_json(self) -> str:
        return _canonical_json(self.as_payload())

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ArchetypeSpec:
        label = "archetype spec"
        _check_header(payload, kind=ARCHETYPE_KIND, label=label)
        archetype_id = _get_str(payload, "archetype_id", label=label)
        label = f"archetype {archetype_id!r}"
        return cls(
            archetype_id=archetype_id,
            description=_get_str(payload, "description", label=label),
            tenant_id=_get_str(payload, "tenant_id", label=label),
            source_id=_get_str(payload, "source_id", label=label),
            commits=tuple(
                SpecCommit.from_payload(item, label=f"{label} commit [{index}]")
                for index, item in enumerate(_get_object_list(payload, "commits", label=label))
            ),
            pr_fixtures=tuple(
                PrFixture.from_payload(item, label=f"{label} pr fixture [{index}]")
                for index, item in enumerate(
                    _get_object_list(payload, "pr_fixtures", label=label)
                )
            ),
        )

    @classmethod
    def from_json(cls, text: str) -> ArchetypeSpec:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SimlabSpecError(f"archetype spec is not valid JSON: {exc}") from exc
        return cls.from_payload(payload)


# ---------------------------------------------------------------------------
# Scenario specs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupersedeSpec:
    """Mark one derived decision superseded by another (selector-resolved)."""

    decision: str
    by: str

    def __post_init__(self) -> None:
        _require_non_empty("supersede.decision", self.decision)
        _require_non_empty("supersede.by", self.by)
        if self.decision == self.by:
            raise SimlabSpecError("a decision cannot supersede itself")

    def as_payload(self) -> dict[str, str]:
        return {"by": self.by, "decision": self.decision}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, label: str) -> SupersedeSpec:
        return cls(
            decision=_get_str(payload, "decision", label=label),
            by=_get_str(payload, "by", label=label),
        )


@dataclass(frozen=True)
class ExpectedFindingSpec:
    """One finding a correct evaluator must emit — or provably cannot.

    ``decision`` is a unique-substring selector over derived decision text.
    ``impossible_at`` names the omission stage that keeps the decision out
    of the evaluator's sight; such findings are expected to grade as
    ``ImpossibleExpectedFinding`` in the replay report, never as matches.
    """

    finding_id: str
    finding_class: FindingClass
    decision: str
    summary: str
    suggested_repair: str | None = None
    impossible_at: OmissionStage | None = None

    def __post_init__(self) -> None:
        _require_id("finding_id", self.finding_id)
        if not isinstance(self.finding_class, FindingClass):
            raise SimlabSpecError(f"unknown finding_class: {self.finding_class!r}")
        _require_non_empty("decision selector", self.decision)
        _require_non_empty("summary", self.summary)
        if self.suggested_repair is not None:
            _require_non_empty("suggested_repair", self.suggested_repair)
        if self.impossible_at is not None and not isinstance(self.impossible_at, OmissionStage):
            raise SimlabSpecError(f"unknown omission stage: {self.impossible_at!r}")

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "decision": self.decision,
            "finding_class": self.finding_class.value,
            "finding_id": self.finding_id,
            "summary": self.summary,
        }
        if self.suggested_repair is not None:
            payload["suggested_repair"] = self.suggested_repair
        if self.impossible_at is not None:
            payload["impossible_at"] = self.impossible_at.value
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, label: str) -> ExpectedFindingSpec:
        raw_class = _get_str(payload, "finding_class", label=label)
        try:
            finding_class = FindingClass(raw_class)
        except ValueError as exc:
            raise SimlabSpecError(f"{label}: unknown finding_class {raw_class!r}") from exc
        raw_stage = _get_optional_str(payload, "impossible_at", label=label)
        stage: OmissionStage | None = None
        if raw_stage is not None:
            try:
                stage = OmissionStage(raw_stage)
            except ValueError as exc:
                raise SimlabSpecError(f"{label}: unknown impossible_at stage {raw_stage!r}") from exc
        return cls(
            finding_id=_get_str(payload, "finding_id", label=label),
            finding_class=finding_class,
            decision=_get_str(payload, "decision", label=label),
            summary=_get_str(payload, "summary", label=label),
            suggested_repair=_get_optional_str(payload, "suggested_repair", label=label),
            impossible_at=stage,
        )


@dataclass(frozen=True)
class ExpectedOutcome:
    """The replay-report numbers the scenario pipeline must produce.

    Every count is asserted exactly — a scenario that cannot say what its
    pipeline does is not a regression test. ``pack_omitted`` carries all
    three pack stages even when zero (the cortex#331 visibility posture).
    """

    matched: int
    missed: int
    unexpected: int
    needs_manual_review: bool
    pack_omitted: Mapping[str, int]
    over_budget: int = 0
    evaluator_omitted_decisions: int = 0
    degraded_reasons_contain: tuple[str, ...] = ()
    span_drift_skips: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("matched", self.matched),
            ("missed", self.missed),
            ("unexpected", self.unexpected),
            ("over_budget", self.over_budget),
            ("evaluator_omitted_decisions", self.evaluator_omitted_decisions),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise SimlabSpecError(f"expected.{name} must be a non-negative integer")
        pack = dict(self.pack_omitted)
        for stage in (
            OmissionStage.STATUS_FILTERED,
            OmissionStage.SUPPRESSED_BELOW_FLOOR,
            OmissionStage.OVER_LIMIT,
        ):
            if stage.value not in pack:
                raise SimlabSpecError(
                    f"expected.pack_omitted must carry {stage.value!r} even when zero"
                )
        for key, value in pack.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise SimlabSpecError(
                    f"expected.pack_omitted[{key!r}] must be a non-negative integer"
                )
        for needle in self.degraded_reasons_contain:
            _require_non_empty("expected.degraded_reasons_contain", needle)
        for selector in self.span_drift_skips:
            _require_non_empty("expected.span_drift_skips", selector)
        object.__setattr__(self, "pack_omitted", dict(pack))

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "matched": self.matched,
            "missed": self.missed,
            "needs_manual_review": self.needs_manual_review,
            "over_budget": self.over_budget,
            "pack_omitted": dict(sorted(dict(self.pack_omitted).items())),
            "unexpected": self.unexpected,
        }
        if self.evaluator_omitted_decisions:
            payload["evaluator_omitted_decisions"] = self.evaluator_omitted_decisions
        if self.degraded_reasons_contain:
            payload["degraded_reasons_contain"] = list(self.degraded_reasons_contain)
        if self.span_drift_skips:
            payload["span_drift_skips"] = list(self.span_drift_skips)
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, label: str) -> ExpectedOutcome:
        pack_raw = payload.get("pack_omitted")
        if not isinstance(pack_raw, Mapping):
            raise SimlabSpecError(f"{label}: expected.pack_omitted must be a JSON object")
        pack: dict[str, int] = {}
        for key, value in pack_raw.items():
            if not isinstance(key, str) or not isinstance(value, int) or isinstance(value, bool):
                raise SimlabSpecError(
                    f"{label}: expected.pack_omitted entries must map strings to integers"
                )
            pack[key] = value
        return cls(
            matched=_get_int(payload, "matched", label=label),
            missed=_get_int(payload, "missed", label=label),
            unexpected=_get_int(payload, "unexpected", label=label),
            needs_manual_review=_get_bool(
                payload, "needs_manual_review", label=label, default=False
            ),
            pack_omitted=pack,
            over_budget=_get_int(payload, "over_budget", label=label, default=0),
            evaluator_omitted_decisions=_get_int(
                payload, "evaluator_omitted_decisions", label=label, default=0
            ),
            degraded_reasons_contain=_get_str_list(
                payload, "degraded_reasons_contain", label=label
            ),
            span_drift_skips=_get_str_list(payload, "span_drift_skips", label=label),
        )


@dataclass(frozen=True)
class ScenarioSpec:
    """One scripted review scenario: repo state, diff, expected outcome."""

    scenario_id: str
    archetype_id: str
    title: str
    demo_notes: str
    diff_base_sha: str
    diff_head_sha: str
    patch: str
    confirm: tuple[str, ...] = ()
    supersede: tuple[SupersedeSpec, ...] = ()
    post_derive_edits: Mapping[str, str] = field(default_factory=dict)
    token_budget: int = DEFAULT_BUDGET_TOKENS
    expected_findings: tuple[ExpectedFindingSpec, ...] = ()
    scripted_degraded_reasons: tuple[str, ...] = ()
    scripted_omitted_decision_count: int = 0
    expected: ExpectedOutcome = field(
        default_factory=lambda: ExpectedOutcome(
            matched=0,
            missed=0,
            unexpected=0,
            needs_manual_review=False,
            pack_omitted={
                OmissionStage.STATUS_FILTERED.value: 0,
                OmissionStage.SUPPRESSED_BELOW_FLOOR.value: 0,
                OmissionStage.OVER_LIMIT.value: 0,
            },
        )
    )

    def __post_init__(self) -> None:
        _require_id("scenario_id", self.scenario_id)
        _require_id("archetype_id", self.archetype_id)
        _require_non_empty("title", self.title)
        _require_non_empty("demo_notes", self.demo_notes)
        if not _GIT_SHA_RE.match(self.diff_base_sha):
            raise SimlabSpecError("diff_base_sha must be an abbreviated or full git sha")
        if not _GIT_SHA_RE.match(self.diff_head_sha):
            raise SimlabSpecError("diff_head_sha must be an abbreviated or full git sha")
        _require_non_empty("patch", self.patch)
        for selector in self.confirm:
            _require_non_empty("confirm selector", selector)
        for path in self.post_derive_edits:
            _require_relative_path("post_derive_edits path", path)
        if self.token_budget < 1:
            raise SimlabSpecError("token_budget must be >= 1")
        finding_ids = [finding.finding_id for finding in self.expected_findings]
        if len(set(finding_ids)) != len(finding_ids):
            raise SimlabSpecError("expected finding ids must be unique")
        for reason in self.scripted_degraded_reasons:
            _require_non_empty("scripted_degraded_reasons", reason)
        if self.scripted_omitted_decision_count < 0:
            raise SimlabSpecError("scripted_omitted_decision_count must be >= 0")
        impossible = sum(
            1 for finding in self.expected_findings if finding.impossible_at is not None
        )
        if self.expected.missed < impossible:
            raise SimlabSpecError(
                "expected.missed must cover every finding marked impossible_at; "
                f"{impossible} impossible finding(s) but expected.missed="
                f"{self.expected.missed}"
            )
        object.__setattr__(self, "post_derive_edits", dict(self.post_derive_edits))

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "archetype_id": self.archetype_id,
            "demo_notes": self.demo_notes,
            "diff_base_sha": self.diff_base_sha,
            "diff_head_sha": self.diff_head_sha,
            "expected": self.expected.as_payload(),
            "expected_findings": [
                finding.as_payload() for finding in self.expected_findings
            ],
            "kind": SCENARIO_KIND,
            "patch": self.patch,
            "scenario_id": self.scenario_id,
            "simlab_spec_schema_version": SIMLAB_SPEC_SCHEMA_VERSION,
            "title": self.title,
            "token_budget": self.token_budget,
        }
        if self.confirm:
            payload["confirm"] = list(self.confirm)
        if self.supersede:
            payload["supersede"] = [item.as_payload() for item in self.supersede]
        if self.post_derive_edits:
            payload["post_derive_edits"] = dict(sorted(dict(self.post_derive_edits).items()))
        if self.scripted_degraded_reasons:
            payload["scripted_degraded_reasons"] = list(self.scripted_degraded_reasons)
        if self.scripted_omitted_decision_count:
            payload["scripted_omitted_decision_count"] = self.scripted_omitted_decision_count
        return payload

    def to_canonical_json(self) -> str:
        return _canonical_json(self.as_payload())

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ScenarioSpec:
        label = "scenario spec"
        _check_header(payload, kind=SCENARIO_KIND, label=label)
        scenario_id = _get_str(payload, "scenario_id", label=label)
        label = f"scenario {scenario_id!r}"
        expected_raw = payload.get("expected")
        if not isinstance(expected_raw, Mapping):
            raise SimlabSpecError(f"{label}: expected must be a JSON object")
        return cls(
            scenario_id=scenario_id,
            archetype_id=_get_str(payload, "archetype_id", label=label),
            title=_get_str(payload, "title", label=label),
            demo_notes=_get_str(payload, "demo_notes", label=label),
            diff_base_sha=_get_str(payload, "diff_base_sha", label=label),
            diff_head_sha=_get_str(payload, "diff_head_sha", label=label),
            patch=_get_str(payload, "patch", label=label),
            confirm=_get_str_list(payload, "confirm", label=label),
            supersede=tuple(
                SupersedeSpec.from_payload(item, label=f"{label} supersede [{index}]")
                for index, item in enumerate(
                    _get_object_list(payload, "supersede", label=label)
                )
            ),
            post_derive_edits=_get_str_map(payload, "post_derive_edits", label=label),
            token_budget=_get_int(
                payload, "token_budget", label=label, default=DEFAULT_BUDGET_TOKENS
            ),
            expected_findings=tuple(
                ExpectedFindingSpec.from_payload(
                    item, label=f"{label} expected finding [{index}]"
                )
                for index, item in enumerate(
                    _get_object_list(payload, "expected_findings", label=label)
                )
            ),
            scripted_degraded_reasons=_get_str_list(
                payload, "scripted_degraded_reasons", label=label
            ),
            scripted_omitted_decision_count=_get_int(
                payload, "scripted_omitted_decision_count", label=label, default=0
            ),
            expected=ExpectedOutcome.from_payload(expected_raw, label=label),
        )

    @classmethod
    def from_json(cls, text: str) -> ScenarioSpec:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SimlabSpecError(f"scenario spec is not valid JSON: {exc}") from exc
        return cls.from_payload(payload)


# ---------------------------------------------------------------------------
# Directory loaders (fail-closed: an empty corpus is never a passing one)
# ---------------------------------------------------------------------------


def load_archetype_specs(directory: Path = ARCHETYPES_DIR) -> dict[str, ArchetypeSpec]:
    """Load every committed archetype spec, keyed by archetype id."""

    specs: dict[str, ArchetypeSpec] = {}
    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise SimlabSpecError(f"no archetype specs found in {directory}")
    for path in paths:
        spec = ArchetypeSpec.from_json(path.read_text(encoding="utf-8"))
        if path.stem != spec.archetype_id:
            raise SimlabSpecError(
                f"archetype file {path.name} carries archetype_id "
                f"{spec.archetype_id!r}; filename and id must agree"
            )
        if spec.archetype_id in specs:
            raise SimlabSpecError(f"duplicate archetype id {spec.archetype_id!r}")
        specs[spec.archetype_id] = spec
    return specs


def load_scenario_specs(directory: Path = SCENARIOS_DIR) -> tuple[ScenarioSpec, ...]:
    """Load every committed scenario spec in sorted filename order."""

    paths = sorted(directory.glob("*.json"))
    if not paths:
        raise SimlabSpecError(f"no scenario specs found in {directory}")
    scenarios: list[ScenarioSpec] = []
    seen: set[str] = set()
    for path in paths:
        spec = ScenarioSpec.from_json(path.read_text(encoding="utf-8"))
        if path.stem != spec.scenario_id:
            raise SimlabSpecError(
                f"scenario file {path.name} carries scenario_id "
                f"{spec.scenario_id!r}; filename and id must agree"
            )
        if spec.scenario_id in seen:
            raise SimlabSpecError(f"duplicate scenario id {spec.scenario_id!r}")
        seen.add(spec.scenario_id)
        scenarios.append(spec)
    return tuple(scenarios)
