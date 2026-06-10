from __future__ import annotations

import pytest

from cortex.hosted.scopes import (
    SEMANTIC_MATCH_WEIGHT,
    STRUCTURAL_SCOPE_WEIGHTS,
    ChangedSurface,
    DecisionScope,
    ScopeType,
    ScopeValidationError,
    decision_scope_insert_sql,
    decisions_for_diff_scope_sql,
    glob_like_pattern_sql,
    glob_matches_path,
    normalize_scope_value,
    query_scope_parameters,
    scope_match_reason_sql,
    scope_match_weight_sql,
    scope_structural_match_sql,
)

TENANT_ID = "11111111-1111-4111-8111-111111111111"
REPO_ID = "22222222-2222-4222-8222-222222222222"
DECISION_NODE_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_EVENT_ID = "44444444-4444-4444-8444-444444444444"


def test_normalize_scope_values_by_type() -> None:
    assert normalize_scope_value(ScopeType.PATH, "./src//cortex\\cli.py") == "src/cortex/cli.py"
    assert normalize_scope_value(ScopeType.GLOB, "src/**/") == "src/**"
    assert normalize_scope_value(ScopeType.SYMBOL, "  cortex.hosted.Schema  ") == "cortex.hosted.Schema"
    assert normalize_scope_value(ScopeType.PACKAGE, "My_Package") == "my-package"
    assert normalize_scope_value(ScopeType.CONFIG_KEY, "CORTEX__HOSTED__URL") == "cortex.hosted.url"
    assert normalize_scope_value(ScopeType.OWNER, "@Platform-Team") == "platform-team"
    assert normalize_scope_value(ScopeType.ISSUE_REF, "https://github.com/a/b/issues/463") == "#463"
    assert normalize_scope_value(ScopeType.CHANNEL_REF, "Cortex-Decisions") == "#cortex-decisions"


def test_invalid_issue_ref_fails_closed() -> None:
    with pytest.raises(ScopeValidationError, match="issue ref"):
        normalize_scope_value(ScopeType.ISSUE_REF, "not-an-issue")


def test_changed_surface_deduplicates_and_preserves_reason_codes() -> None:
    scopes = ChangedSurface(
        paths=("./src/cortex/cli.py", "src/cortex/cli.py"),
        symbols=("cortex.cli.cli",),
        config_keys=("CORTEX__HOSTED__URL",),
    ).query_scopes()

    assert [(scope.scope_type, scope.normalized_value) for scope in scopes] == [
        (ScopeType.PATH, "src/cortex/cli.py"),
        (ScopeType.SYMBOL, "cortex.cli.cli"),
        (ScopeType.CONFIG_KEY, "cortex.hosted.url"),
    ]
    assert scopes[0].reason_code == "scope:path:src/cortex/cli.py"
    assert scopes[2].reason_code == "scope:config_key:cortex.hosted.url"


def test_structural_scope_weights_outrank_semantic_candidates() -> None:
    assert all(weight > SEMANTIC_MATCH_WEIGHT for weight in STRUCTURAL_SCOPE_WEIGHTS.values())
    assert STRUCTURAL_SCOPE_WEIGHTS[ScopeType.PATH] > STRUCTURAL_SCOPE_WEIGHTS[ScopeType.SYMBOL]
    assert STRUCTURAL_SCOPE_WEIGHTS[ScopeType.SYMBOL] > STRUCTURAL_SCOPE_WEIGHTS[ScopeType.CONFIG_KEY]


def test_decision_scope_insert_parameters_include_normalized_value_and_reason() -> None:
    scope = DecisionScope(
        tenant_id=TENANT_ID,
        repo_id=REPO_ID,
        decision_node_id=DECISION_NODE_ID,
        scope_type=ScopeType.CONFIG_KEY,
        scope_value="CORTEX__HOSTED__URL",
        source_event_id=SOURCE_EVENT_ID,
    )

    assert scope.normalized_value == "cortex.hosted.url"
    assert scope.reason_code == "scope:config_key:cortex.hosted.url"
    assert scope.structural_weight == STRUCTURAL_SCOPE_WEIGHTS[ScopeType.CONFIG_KEY]
    assert scope.as_insert_parameters() == {
        "tenant_id": TENANT_ID,
        "repo_id": REPO_ID,
        "decision_node_id": DECISION_NODE_ID,
        "scope_type": "config_key",
        "scope_value": "CORTEX__HOSTED__URL",
        "normalized_value": "cortex.hosted.url",
        "source_event_id": SOURCE_EVENT_ID,
    }


def test_query_scope_parameters_preserve_reason_codes_and_weights() -> None:
    scopes = ChangedSurface(
        paths=("src/cortex/hosted/schema.py",),
        symbols=("cortex.hosted.create_schema_sql",),
    ).query_scopes()

    params = query_scope_parameters(scopes)

    assert params["scope_types"] == ["path", "symbol"]
    assert params["normalized_values"] == [
        "src/cortex/hosted/schema.py",
        "cortex.hosted.create_schema_sql",
    ]
    assert params["reason_codes"] == [
        "scope:path:src/cortex/hosted/schema.py",
        "scope:symbol:cortex.hosted.create_schema_sql",
    ]
    assert params["structural_weights"] == [
        STRUCTURAL_SCOPE_WEIGHTS[ScopeType.PATH],
        STRUCTURAL_SCOPE_WEIGHTS[ScopeType.SYMBOL],
    ]


def test_decision_scope_insert_sql_is_idempotent() -> None:
    sql = decision_scope_insert_sql()

    assert "INSERT INTO cortex_hosted.decision_scopes" in sql
    assert "repo_id" in sql
    assert "ON CONFLICT (tenant_id, decision_node_id, scope_type, normalized_value) DO NOTHING" in sql
    assert "RETURNING decision_scope_id, scope_type, normalized_value" in sql


def test_decisions_for_diff_scope_sql_returns_structural_reason_codes() -> None:
    sql = decisions_for_diff_scope_sql()

    assert "%(scope_types)s::text[]" in sql
    assert "%(normalized_values)s::text[]" in sql
    assert "%(reason_codes)s::text[]" in sql
    assert "%(structural_weights)s::integer[]" in sql
    assert "JOIN cortex_hosted.decision_scopes AS scope" in sql
    assert "scope.repo_id IS NULL OR scope.repo_id = %(repo_id)s::uuid" in sql
    assert "node.repo_id IS NULL OR node.repo_id = %(repo_id)s::uuid" in sql
    assert "node.status IN ('candidate', 'confirmed')" in sql
    assert "ORDER BY structural_weight DESC" in sql
    assert "reason_code" in sql


def test_scope_sql_rejects_unsafe_schema_identifier() -> None:
    with pytest.raises(ScopeValidationError, match="invalid SQL identifier"):
        decisions_for_diff_scope_sql("bad;drop")


# ---------------------------------------------------------------------------
# Glob/directory-granularity matching (cortex#484)
# ---------------------------------------------------------------------------


def test_glob_matches_path_at_directory_granularity() -> None:
    # The cortex#484 acceptance shape: a decision scoped 'src/api/**'
    # matches a diff touching 'src/api/handlers/foo.py'.
    assert glob_matches_path("src/api/**", "src/api/handlers/foo.py")
    assert glob_matches_path("src/api/**", "src/api/foo.py")
    assert glob_matches_path(".cortex/journal/**", ".cortex/journal/2026-06-09-x.md")


def test_glob_match_is_anchored_and_never_prefix_bleeds() -> None:
    # LIKE is anchored at both ends: no sibling-directory or mid-path bleed.
    assert not glob_matches_path("src/api/**", "src/apiX/handlers/foo.py")
    assert not glob_matches_path("src/api/**", "lib/src/api/foo.py")
    # '<dir>/**' covers paths strictly under the directory, not the bare dir.
    assert not glob_matches_path("src/api/**", "src/api")


def test_glob_match_treats_non_doublestar_characters_literally() -> None:
    # Only '**' is a wildcard: underscores (LIKE '_') and lone '*' match
    # themselves — the documented v1 mechanism, mirrored by the SQL escaping.
    assert glob_matches_path("src/a_b/**", "src/a_b/c.py")
    assert not glob_matches_path("src/a_b/**", "src/axb/c.py")
    assert not glob_matches_path("src/*.py", "src/x.py")
    assert glob_matches_path("src/*.py", "src/*.py")
    # Exact value with no wildcard degrades to literal equality.
    assert glob_matches_path("src/api/foo.py", "src/api/foo.py")
    assert not glob_matches_path("src/api/foo.py", "src/api/foo_py")


def test_glob_like_pattern_sql_escapes_like_metacharacters() -> None:
    pattern = glob_like_pattern_sql("scope.normalized_value")
    # Escape order: backslash, percent, underscore — then '**' -> '%'.
    assert pattern == (
        "replace(replace(replace(replace(scope.normalized_value, "
        "'\\', '\\\\'), '%', '\\%'), '_', '\\_'), '**', '%')"
    )


def test_scope_match_fragments_encode_glob_branch_and_precedence() -> None:
    condition = scope_structural_match_sql()
    assert "scope.scope_type = q.scope_type" in condition
    assert "scope.scope_type = 'glob' AND q.scope_type = 'path'" in condition
    assert "q.normalized_value LIKE" in condition

    weight = scope_match_weight_sql()
    glob_weight = STRUCTURAL_SCOPE_WEIGHTS[ScopeType.GLOB]
    assert f"ELSE {glob_weight} END" in weight
    assert "THEN q.structural_weight" in weight
    # Precedence: exact path keeps the PATH weight, which outranks GLOB.
    assert STRUCTURAL_SCOPE_WEIGHTS[ScopeType.PATH] > glob_weight

    reason = scope_match_reason_sql()
    assert "'scope:glob:' || scope.normalized_value" in reason
    assert "THEN q.reason_code" in reason


def test_both_structural_sql_surfaces_embed_the_same_match_fragments() -> None:
    # Guardrail: the standalone matcher and the scope_candidates CTE in the
    # hybrid retrieval SQL must carry the SAME glob-matching fragments, so
    # the two surfaces (and the Python mirror) cannot drift apart silently.
    from cortex.hosted.decisions_for_diff import decisions_for_diff_retrieval_sql

    standalone = decisions_for_diff_scope_sql()
    retrieval = decisions_for_diff_retrieval_sql()
    for fragment in (
        scope_structural_match_sql(),
        scope_match_weight_sql(),
        scope_match_reason_sql(),
    ):
        assert fragment in standalone
        assert fragment in retrieval
