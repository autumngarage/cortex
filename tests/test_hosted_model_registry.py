"""Tests for the prompt/model version registry (cortex#327)."""

from __future__ import annotations

import hashlib
import json

import pytest

from cortex.hosted.model_registry import (
    PROMPT_HASH_PREFIX_LENGTH,
    REGISTRY_SCHEMA_VERSION,
    ModelPromptRegistry,
    RegisteredModel,
    RegisteredPrompt,
    RegistryValidationError,
    parse_prompt_version,
)


def _model() -> RegisteredModel:
    return RegisteredModel(
        model_id="anthropic/claude-fable-5",
        description="Primary evaluator route for Stage 0.",
    )


def _prompt(version: int = 1, text: str = "Judge whether DIFF contradicts DECISION.") -> RegisteredPrompt:
    return RegisteredPrompt(
        prompt_id="evaluate-contradiction",
        version_number=version,
        template_text=text,
        description="Soft-evaluator contradiction judgment prompt.",
    )


def _registry() -> ModelPromptRegistry:
    return ModelPromptRegistry(models=(_model(),), prompts=(_prompt(),))


def test_prompt_version_string_is_self_certifying() -> None:
    prompt = _prompt()
    expected_prefix = hashlib.sha256(prompt.template_text.encode("utf-8")).hexdigest()[
        :PROMPT_HASH_PREFIX_LENGTH
    ]
    assert prompt.prompt_version == f"evaluate-contradiction/v1+{expected_prefix}"
    parsed = parse_prompt_version(prompt.prompt_version)
    assert parsed.prompt_id == "evaluate-contradiction"
    assert parsed.version_number == 1
    assert parsed.hash_prefix == expected_prefix


def test_noncanonical_version_strings_fail_visibly() -> None:
    for bad in ("v1", "evaluate-contradiction/v0+aaaaaaaaaaaa", "evaluate/v1", "evaluate/v1+zzzz"):
        with pytest.raises(RegistryValidationError, match="not canonical"):
            parse_prompt_version(bad)


def test_reregistering_same_content_is_idempotent() -> None:
    registry = _registry()
    again = registry.register_prompt(_prompt())
    assert again.prompt_version == _prompt().prompt_version
    assert len(registry.prompts) == 1


def test_reregistering_different_content_under_same_version_is_an_error() -> None:
    registry = _registry()
    with pytest.raises(RegistryValidationError, match="immutable"):
        registry.register_prompt(_prompt(text="A different template."))


def test_versions_are_dense_and_append_only() -> None:
    registry = _registry()
    with pytest.raises(RegistryValidationError, match="next version must be v2"):
        registry.register_prompt(_prompt(version=3, text="Skipping ahead."))
    v2 = registry.register_prompt(_prompt(version=2, text="Second revision."))
    assert registry.latest_prompt("evaluate-contradiction") == v2


def test_resolve_detects_prompt_drift() -> None:
    registry = _registry()
    version = _prompt().prompt_version
    drifted_prefix = "0" * PROMPT_HASH_PREFIX_LENGTH
    drifted = version.split("+")[0] + "+" + drifted_prefix
    with pytest.raises(RegistryValidationError, match="drift detected"):
        registry.resolve_prompt_version(drifted)


def test_resolve_unknown_prompt_or_model_fails_visibly() -> None:
    registry = _registry()
    ghost = _prompt(version=1, text="Never registered elsewhere.")
    with pytest.raises(RegistryValidationError, match="not registered"):
        registry.resolve_prompt_version(
            "ghost-prompt/v1+" + ghost.content_hash[:PROMPT_HASH_PREFIX_LENGTH]
        )
    with pytest.raises(RegistryValidationError, match="not registered"):
        registry.resolve_model("openai/gpt-5.4")


def test_stamp_returns_validated_atomic_pair() -> None:
    registry = _registry()
    pair = registry.stamp(
        model_id="anthropic/claude-fable-5",
        prompt_version=_prompt().prompt_version,
    )
    assert pair == ("anthropic/claude-fable-5", _prompt().prompt_version)


def test_model_id_must_be_provider_qualified() -> None:
    with pytest.raises(RegistryValidationError, match="provider-qualified"):
        RegisteredModel(model_id="claude-fable-5", description="bare name")


def test_registry_round_trips_byte_identical() -> None:
    registry = ModelPromptRegistry(
        models=(_model(),),
        prompts=(_prompt(), _prompt(version=2, text="Second revision.")),
    )
    raw = registry.to_canonical_json()
    reloaded = ModelPromptRegistry.from_json(raw)
    assert reloaded.to_canonical_json() == raw


def test_unknown_registry_schema_version_fails_visibly() -> None:
    payload = json.loads(_registry().to_canonical_json())
    payload["registry_schema_version"] = REGISTRY_SCHEMA_VERSION + 1
    with pytest.raises(RegistryValidationError, match="unknown registry_schema_version"):
        ModelPromptRegistry.from_payload(payload)


def test_tampered_content_hash_in_payload_is_rejected() -> None:
    payload = json.loads(_registry().to_canonical_json())
    payload["prompts"][0]["template_text"] = "Tampered template."
    with pytest.raises(RegistryValidationError, match="content_hash mismatch"):
        ModelPromptRegistry.from_payload(payload)
