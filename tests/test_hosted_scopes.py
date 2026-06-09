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
    normalize_scope_value,
    query_scope_parameters,
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
