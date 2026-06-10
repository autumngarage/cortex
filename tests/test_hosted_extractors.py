"""Tests for the deterministic repo-native extractors (cortex#351-#353).

Covers: rule detection in CLAUDE.md/AGENTS.md with exact spans and scope
proposals; near-verbatim ADR import with status-driven lane semantics and
supersede hints; CODEOWNERS rule parsing with malformed-line drops; the
fail-closed source dispatcher; ledger-event identity (stable, collision-free
idempotency keys); and per-file determinism (same content -> identical span
hashes and candidates).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cortex.hosted.degradation import DegradationMode, classify_failure
from cortex.hosted.extractors import (
    DROP_ADR_MISSING_DECISION_SECTION,
    DROP_ADR_MISSING_STATUS,
    DROP_ADR_MISSING_TITLE,
    DROP_BULLET_WITHOUT_CONSTRAINT,
    DROP_CODE_BLOCK,
    DROP_CODEOWNERS_INVALID_OWNER,
    DROP_CODEOWNERS_MISSING_PATTERN,
    DROP_CODEOWNERS_UNOWNED_PATTERN,
    DROP_HEADING_ONLY,
    DROP_LINK_ONLY,
    DROP_PROSE_WITHOUT_CONSTRAINT,
    DROP_TABLE,
    EXTRACTOR_IDS,
    ExtractedCandidate,
    ExtractorError,
    RepoNativeExtractor,
    candidate_events,
    classify_source,
    extract_adr_decision,
    extract_agent_instruction_rules,
    extract_codeowners_rules,
    extract_repo_native,
    proposed_path_scopes,
)
from cortex.hosted.lanes import DEFAULT_LANE_POLICY, DeriveSourceType
from cortex.hosted.ledger_events import LedgerEventType
from cortex.hosted.model_interfaces import DeriveCandidate
from cortex.hosted.provenance import SourceDocument
from cortex.hosted.scopes import ScopeType

TENANT_ID = "11111111-1111-4111-8111-111111111111"
SOURCE_ID = "22222222-2222-4222-8222-222222222222"
T0 = datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)


def _document(
    external_id: str, content: str, *, source_timestamp: datetime = T0
) -> SourceDocument:
    return SourceDocument(
        tenant_id=TENANT_ID,
        source_id=SOURCE_ID,
        document_type="repo-file",
        external_id=external_id,
        permalink=external_id,
        author_ref="cortex-derive",
        source_timestamp=source_timestamp,
        content=content,
    )


ACCEPTED_ADR = (
    "# 1. Use Postgres\n"
    "\n"
    "Status: Accepted\n"
    "\n"
    "## Context\n"
    "\n"
    "We need durable storage for `src/ledger/`.\n"
    "\n"
    "## Decision\n"
    "\n"
    "We will use Postgres for the hosted ledger.\n"
    "\n"
    "## Consequences\n"
    "\n"
    "Backups become mandatory.\n"
)


# ---------------------------------------------------------------------------
# #351 — agent instruction files
# ---------------------------------------------------------------------------


def test_constraint_bullet_becomes_candidate_with_exact_span() -> None:
    content = "# Rules of the road\n\n- Never run migrations by hand in prod.\n"
    rule_text = "Never run migrations by hand in prod."
    outcome = extract_agent_instruction_rules(_document("CLAUDE.md", content))
    assert outcome.source_type is DeriveSourceType.AGENT_INSTRUCTIONS
    assert len(outcome.extracted) == 1
    candidate = outcome.extracted[0].candidate
    span = candidate.spans[0]
    assert candidate.decision_text == rule_text
    assert span.excerpt == rule_text
    assert span.start_offset == content.index(rule_text)
    assert span.end_offset == span.start_offset + len(rule_text)


def test_hard_requirements_section_promotes_keywordless_bullets() -> None:
    content = (
        "## Hard requirements\n"
        "\n"
        "- Keep functions small.\n"
        "\n"
        "## Style\n"
        "\n"
        "- Keep functions small.\n"
    )
    outcome = extract_agent_instruction_rules(_document("AGENTS.md", content))
    assert [item.candidate.decision_text for item in outcome.extracted] == [
        "Keep functions small."
    ]
    assert outcome.extracted[0].metadata["section"] == "Hard requirements"
    reasons = [chatter.reason_code for chatter in outcome.dropped]
    assert reasons.count(DROP_BULLET_WITHOUT_CONSTRAINT) == 1


def test_bold_lead_bullet_is_a_rule() -> None:
    content = "- **One code path.** Share business logic across modes.\n"
    outcome = extract_agent_instruction_rules(_document("AGENTS.md", content))
    assert len(outcome.extracted) == 1
    assert outcome.extracted[0].candidate.decision_text.startswith("**One code path.**")


def test_constraint_sentence_span_covers_exactly_that_sentence() -> None:
    content = (
        "# Doc\n"
        "\n"
        "Alpha explains the background. The cache must never be shared. Beta is fine.\n"
    )
    sentence = "The cache must never be shared."
    outcome = extract_agent_instruction_rules(_document("CLAUDE.md", content))
    texts = [item.candidate.decision_text for item in outcome.extracted]
    assert texts == [sentence]
    span = outcome.extracted[0].candidate.spans[0]
    assert span.start_offset == content.index(sentence)
    assert span.excerpt == sentence
    assert outcome.extracted[0].metadata["rule_kind"] == "sentence"


def test_noise_blocks_drop_with_reason_codes_never_silently() -> None:
    content = (
        "# Title\n"
        "\n"
        "Plain prose with no binding language at all.\n"
        "\n"
        "- [SPEC.md](./SPEC.md)\n"
        "\n"
        "- A bullet that merely describes things.\n"
        "\n"
        "```bash\n"
        "echo hello\n"
        "```\n"
        "\n"
        "| a | b |\n"
        "|---|---|\n"
    )
    outcome = extract_agent_instruction_rules(_document("CLAUDE.md", content))
    assert outcome.extracted == ()
    reasons = sorted(chatter.reason_code for chatter in outcome.dropped)
    assert reasons == sorted(
        [
            DROP_HEADING_ONLY,
            DROP_PROSE_WITHOUT_CONSTRAINT,
            DROP_LINK_ONLY,
            DROP_BULLET_WITHOUT_CONSTRAINT,
            DROP_CODE_BLOCK,
            DROP_TABLE,
        ]
    )
    for chatter in outcome.dropped:
        assert len(chatter.excerpt_hash) == 64


def test_rule_scope_tokens_propose_paths_and_globs_not_conjunctions() -> None:
    text = (
        "Touchstone owns `principles/*.md` and/or `src/db/client.py`; "
        "see docs/adr/0001.md and SPEC.md."
    )
    scopes = {(scope.scope_type, scope.normalized_value) for scope in proposed_path_scopes(text)}
    assert (ScopeType.GLOB, "principles/*.md") in scopes
    assert (ScopeType.PATH, "src/db/client.py") in scopes
    assert (ScopeType.PATH, "docs/adr/0001.md") in scopes
    assert (ScopeType.PATH, "SPEC.md") in scopes
    assert not any(value == "and/or" for _scope_type, value in scopes)


def test_bold_markers_never_become_glob_scopes() -> None:
    scopes = proposed_path_scopes("**Spec before implementation.** Amend SPEC.md first.")
    assert [(scope.scope_type, scope.value) for scope in scopes] == [
        (ScopeType.PATH, "SPEC.md")
    ]


# ---------------------------------------------------------------------------
# #352 — ADRs near-verbatim
# ---------------------------------------------------------------------------


def test_accepted_adr_is_one_near_verbatim_auto_promotable_candidate() -> None:
    outcome = extract_adr_decision(_document("docs/adr/0001-use-postgres.md", ACCEPTED_ADR))
    assert outcome.source_type is DeriveSourceType.ADR
    assert len(outcome.extracted) == 1
    item = outcome.extracted[0]
    candidate = item.candidate
    # Near-verbatim invariant: decision_text is exactly the cited excerpts.
    assert candidate.decision_text == "\n\n".join(span.excerpt for span in candidate.spans)
    assert candidate.decision_text == (
        "1. Use Postgres"
        "\n\n"
        "We will use Postgres for the hosted ledger."
        "\n\n"
        "We need durable storage for `src/ledger/`."
    )
    assert item.lane.auto_promotable is True
    assert item.lane.backfilled is False
    assert item.metadata["adr_status"] == "accepted"
    assert (ScopeType.PATH, "src/ledger") in {
        (scope.scope_type, scope.normalized_value) for scope in candidate.proposed_scopes
    }


def test_superseded_adr_is_advisory_and_carries_supersede_hint() -> None:
    content = (
        "# 2. Drop Redis\n"
        "\n"
        "**Status:** Superseded by 0005\n"
        "\n"
        "## Decision\n"
        "\n"
        "We drop Redis in favor of Postgres LISTEN/NOTIFY.\n"
    )
    outcome = extract_adr_decision(_document("docs/adr/0002-drop-redis.md", content))
    assert len(outcome.extracted) == 1
    item = outcome.extracted[0]
    assert item.lane.auto_promotable is False
    assert item.lane.advisory_only is True
    assert item.lane.backfilled is True
    assert item.metadata["adr_status"] == "superseded"
    # The hint for the future graph writer; no graph writes happen here.
    assert item.metadata["adr_superseded_by_ref"] == "0005"


def test_adr_missing_status_or_decision_drops_with_reasons() -> None:
    no_status = "# 3. Title only\n\n## Decision\n\nDo the thing.\n"
    outcome = extract_adr_decision(_document("docs/adr/0003-x.md", no_status))
    assert outcome.extracted == ()
    assert [chatter.reason_code for chatter in outcome.dropped] == [DROP_ADR_MISSING_STATUS]

    no_decision = "# 4. Title\n\nStatus: Accepted\n\n## Context\n\nStuff.\n"
    outcome = extract_adr_decision(_document("docs/adr/0004-x.md", no_decision))
    assert [chatter.reason_code for chatter in outcome.dropped] == [
        DROP_ADR_MISSING_DECISION_SECTION
    ]

    no_title = "Status: Accepted\n\n## Decision\n\nDo the thing.\n"
    outcome = extract_adr_decision(_document("docs/adr/0005-x.md", no_title))
    assert [chatter.reason_code for chatter in outcome.dropped] == [DROP_ADR_MISSING_TITLE]


def test_status_section_format_is_recognized() -> None:
    content = (
        "# 6. MADR style\n"
        "\n"
        "## Status\n"
        "\n"
        "Accepted\n"
        "\n"
        "## Decision Outcome\n"
        "\n"
        "Use the MADR template.\n"
    )
    outcome = extract_adr_decision(_document("docs/decisions/0006-madr.md", content))
    assert len(outcome.extracted) == 1
    assert outcome.extracted[0].metadata["adr_status"] == "accepted"


# ---------------------------------------------------------------------------
# #353 — CODEOWNERS
# ---------------------------------------------------------------------------


def test_codeowners_rule_lines_become_ownership_candidates() -> None:
    content = (
        "# ownership map\n"
        "\n"
        "*.py @backend-team\n"
        "docs/** @alice @bob\n"
        "src/api/ @org/api-team # inline note\n"
        "infra/ ops@example.com\n"
    )
    document = _document(".github/CODEOWNERS", content)
    outcome = extract_codeowners_rules(document)
    assert outcome.source_type is DeriveSourceType.CODEOWNERS
    texts = [item.candidate.decision_text for item in outcome.extracted]
    assert texts == [
        "@backend-team own *.py",
        "@alice @bob own docs/**",
        "@org/api-team own src/api/",
        "ops@example.com own infra/",
    ]
    assert outcome.dropped == ()

    line = "docs/** @alice @bob"
    span = outcome.extracted[1].candidate.spans[0]
    assert span.excerpt == line
    assert span.start_offset == content.index(line)

    scopes = {
        (scope.scope_type, scope.normalized_value)
        for scope in outcome.extracted[1].candidate.proposed_scopes
    }
    assert scopes == {
        (ScopeType.GLOB, "docs/**"),
        (ScopeType.OWNER, "alice"),
        (ScopeType.OWNER, "bob"),
    }
    for item in outcome.extracted:
        assert item.lane.auto_promotable is True
        assert item.lane.lane.value == "structured"


def test_codeowners_malformed_lines_drop_with_reasons_never_silently() -> None:
    content = (
        "# fine comment\n"
        "\n"
        "@owner-where-pattern-belongs stuff\n"
        "docs/orphan\n"
        "src/x.py not-an-owner-token\n"
        "*.md @docs-team\n"
    )
    outcome = extract_codeowners_rules(_document("CODEOWNERS", content))
    assert [item.candidate.decision_text for item in outcome.extracted] == [
        "@docs-team own *.md"
    ]
    assert [chatter.reason_code for chatter in outcome.dropped] == [
        DROP_CODEOWNERS_MISSING_PATTERN,
        DROP_CODEOWNERS_UNOWNED_PATTERN,
        DROP_CODEOWNERS_INVALID_OWNER,
    ]


# ---------------------------------------------------------------------------
# Dispatch, lane policy wiring, and degradation classification
# ---------------------------------------------------------------------------


def test_classify_source_routes_each_known_shape() -> None:
    assert (
        classify_source(_document("CLAUDE.md", "x")) is DeriveSourceType.AGENT_INSTRUCTIONS
    )
    assert (
        classify_source(_document("nested/AGENTS.md", "x"))
        is DeriveSourceType.AGENT_INSTRUCTIONS
    )
    assert classify_source(_document(".github/CODEOWNERS", "x")) is DeriveSourceType.CODEOWNERS
    assert classify_source(_document("docs/adr/0001-x.md", "x")) is DeriveSourceType.ADR
    assert classify_source(_document("docs/decisions/0002-y.md", "x")) is DeriveSourceType.ADR
    # NNNN-*.md outside an ADR directory needs a Status: header to qualify.
    assert (
        classify_source(_document("0007-loose.md", "# T\n\nStatus: Accepted\n"))
        is DeriveSourceType.ADR
    )
    with pytest.raises(ExtractorError, match=r"0007-loose\.md"):
        classify_source(_document("0007-loose.md", "# T\n\nNo status here.\n"))
    with pytest.raises(ExtractorError, match=r"README\.md"):
        classify_source(_document("README.md", "hello"))


def test_lane_assignments_follow_default_lane_policy_rules() -> None:
    outcome = extract_agent_instruction_rules(
        _document("CLAUDE.md", "- Never do the thing.\n")
    )
    expected = DEFAULT_LANE_POLICY.assign(
        DeriveSourceType.AGENT_INSTRUCTIONS, backfilled=False
    )
    assert outcome.extracted[0].lane == expected


def test_extractor_error_classifies_as_invalid_input_rejected() -> None:
    assert (
        classify_failure(ExtractorError("probe")) is DegradationMode.INVALID_INPUT_REJECTED
    )


def test_extracted_candidate_rejects_reserved_metadata_keys() -> None:
    document = _document("CLAUDE.md", "- Never do the thing.\n")
    outcome = extract_agent_instruction_rules(document)
    item = outcome.extracted[0]
    with pytest.raises(ExtractorError, match="reserved"):
        ExtractedCandidate(
            candidate=item.candidate,
            lane=item.lane,
            metadata={"extractor": "spoofed"},
        )


def test_outcome_rejects_lane_from_wrong_source_type() -> None:
    document = _document("CLAUDE.md", "- Never do the thing.\n")
    outcome = extract_agent_instruction_rules(document)
    wrong_lane = DEFAULT_LANE_POLICY.assign(DeriveSourceType.CODEOWNERS, backfilled=False)
    with pytest.raises(ExtractorError, match="source type"):
        extract_repo_native(document).__class__(
            source_type=DeriveSourceType.AGENT_INSTRUCTIONS,
            extractor_id=outcome.extractor_id,
            extracted=(
                ExtractedCandidate(candidate=outcome.extracted[0].candidate, lane=wrong_lane),
            ),
            dropped=(),
        )


# ---------------------------------------------------------------------------
# Ledger-event identity and determinism
# ---------------------------------------------------------------------------


def test_candidate_events_conform_to_the_derive_envelope() -> None:
    document = _document("docs/adr/0001-use-postgres.md", ACCEPTED_ADR)
    outcome = extract_repo_native(document)
    events = candidate_events(document, outcome)
    assert len(events) == 1
    event = events[0]
    assert event.event_type is LedgerEventType.CANDIDATE_PROPOSED
    assert event.tenant_id == TENANT_ID
    assert event.source_id == SOURCE_ID
    assert event.occurred_at == T0
    assert event.actor.actor_id == EXTRACTOR_IDS[DeriveSourceType.ADR]
    # Candidates carry spans always; the envelope mirrors the payload spans.
    assert event.source_span_hashes
    payload_spans = event.payload["spans"]
    assert event.source_span_hashes == tuple(span["span_hash"] for span in payload_spans)
    assert event.payload["lane_assignment"]["rule_citation"] == (
        outcome.extracted[0].lane.rule_citation
    )
    assert event.metadata["extractor"] == EXTRACTOR_IDS[DeriveSourceType.ADR]
    assert event.metadata["document_hash"] == document.document_hash
    external_ref = event.source_event_external_id
    assert external_ref is not None
    assert external_ref.startswith("docs/adr/0001-use-postgres.md@")


def test_same_content_yields_identical_span_hashes_and_candidates() -> None:
    content = (
        "## Hard requirements\n"
        "\n"
        "- Never bypass `scripts/open-pr.sh` when shipping.\n"
        "\n"
        "Releases must always be tagged.\n"
    )
    first = extract_repo_native(_document("CLAUDE.md", content, source_timestamp=T0))
    second = extract_repo_native(_document("CLAUDE.md", content, source_timestamp=T1))
    assert [item.candidate for item in first.extracted] == [
        item.candidate for item in second.extracted
    ]
    assert [item.candidate.span_hashes for item in first.extracted] == [
        item.candidate.span_hashes for item in second.extracted
    ]
    assert first.dropped == second.dropped


def test_event_identity_is_stable_for_identical_snapshots() -> None:
    document = _document("CODEOWNERS", "*.py @backend-team\n")
    events_a = candidate_events(document, extract_repo_native(document))
    events_b = candidate_events(document, extract_repo_native(document))
    assert [event.idempotency_key for event in events_a] == [
        event.idempotency_key for event in events_b
    ]
    assert [event.event_hash for event in events_a] == [
        event.event_hash for event in events_b
    ]


def test_new_snapshot_timestamp_gets_new_keys_never_a_hash_collision() -> None:
    """Touching a file (new mtime, same content) must append a new snapshot's
    events, not collide with stored ones on the same idempotency key."""

    content = "*.py @backend-team\n"
    doc_t0 = _document("CODEOWNERS", content, source_timestamp=T0)
    doc_t1 = _document("CODEOWNERS", content, source_timestamp=T1)
    events_t0 = candidate_events(doc_t0, extract_repo_native(doc_t0))
    events_t1 = candidate_events(doc_t1, extract_repo_native(doc_t1))
    keys_t0 = {event.idempotency_key for event in events_t0}
    keys_t1 = {event.idempotency_key for event in events_t1}
    assert keys_t0.isdisjoint(keys_t1)


def test_repo_native_extractor_accumulates_dropped_records_per_file() -> None:
    extractor = RepoNativeExtractor()
    events = extractor(_document("CLAUDE.md", "# Heading only\n"))
    assert events == ()
    events = extractor(_document("CODEOWNERS", "docs/orphan\n"))
    assert events == ()
    records = extractor.dropped
    assert [record.external_id for record in records] == ["CLAUDE.md", "CODEOWNERS"]
    assert [record.chatter.reason_code for record in records] == [
        DROP_HEADING_ONLY,
        DROP_CODEOWNERS_UNOWNED_PATTERN,
    ]


def test_uncited_candidates_are_unrepresentable() -> None:
    with pytest.raises(ValueError, match="at least one source span"):
        DeriveCandidate(decision_text="rule", spans=())
