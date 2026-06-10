"""Append-only prompt/model version registry for hosted Cortex (cortex#327).

Every model-backed result in the ledger is stamped with an atomic
``(model_id, prompt_version)`` pair (enforced by ``ledger_events.py`` and the
DB CHECK shipped in PR #477). This module owns what those identifiers *mean*:

- **Model IDs** are provider-qualified (``provider/name``), so route changes
  are visible in replay keys instead of hiding behind bare model names.
- **Prompt versions are self-certifying**: the canonical string is
  ``<prompt-id>/v<N>+<content-hash-prefix>``, so a version string alone can
  detect drift against the registered template content — a replayed verdict
  whose prompt text changed under the same version number fails loudly.
- **Registration is append-only**: re-registering an existing
  ``(prompt_id, version)`` with identical content is idempotent; with
  different content it is an error. New content gets a new version number.

The registry serializes to canonical JSON (sorted, newline-terminated) so a
committed registry file round-trips byte-identically, mirroring the eval
fixture format (cortex#332). Loading an unknown registry schema version
fails visibly — no silent fallback.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

REGISTRY_SCHEMA_VERSION = 1
PROMPT_HASH_PREFIX_LENGTH = 12

_PROMPT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_MODEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-zA-Z0-9][a-zA-Z0-9._:-]*$")
_PROMPT_VERSION_RE = re.compile(
    r"^(?P<prompt_id>[a-z0-9][a-z0-9-]*)/v(?P<number>[1-9][0-9]*)"
    rf"\+(?P<hash_prefix>[a-f0-9]{{{PROMPT_HASH_PREFIX_LENGTH}}})$"
)


class RegistryValidationError(ValueError):
    """Raised when registry material cannot support replayable stamping."""


@dataclass(frozen=True)
class RegisteredModel:
    """A model route that may stamp ledger events."""

    model_id: str
    description: str

    def __post_init__(self) -> None:
        if not _MODEL_ID_RE.match(self.model_id):
            raise RegistryValidationError(
                "model_id must be provider-qualified ('provider/name'); "
                f"got {self.model_id!r}"
            )
        if not self.description.strip():
            raise RegistryValidationError("description must not be empty")

    def as_payload(self) -> dict[str, str]:
        return {"description": self.description, "model_id": self.model_id}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RegisteredModel:
        return cls(
            model_id=_get_str(payload, "model_id"),
            description=_get_str(payload, "description"),
        )


@dataclass(frozen=True)
class RegisteredPrompt:
    """One immutable prompt template version."""

    prompt_id: str
    version_number: int
    template_text: str
    description: str

    def __post_init__(self) -> None:
        if not _PROMPT_ID_RE.match(self.prompt_id):
            raise RegistryValidationError(
                f"prompt_id must be lowercase kebab-case; got {self.prompt_id!r}"
            )
        if self.version_number < 1:
            raise RegistryValidationError("version_number must be >= 1")
        if not self.template_text:
            raise RegistryValidationError("template_text must not be empty")
        if not self.description.strip():
            raise RegistryValidationError("description must not be empty")

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.template_text.encode("utf-8")).hexdigest()

    @property
    def prompt_version(self) -> str:
        """The self-certifying version string stamped into ledger events."""

        prefix = self.content_hash[:PROMPT_HASH_PREFIX_LENGTH]
        return f"{self.prompt_id}/v{self.version_number}+{prefix}"

    def as_payload(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "description": self.description,
            "prompt_id": self.prompt_id,
            "template_text": self.template_text,
            "version_number": self.version_number,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RegisteredPrompt:
        prompt = cls(
            prompt_id=_get_str(payload, "prompt_id"),
            version_number=_get_int(payload, "version_number"),
            template_text=_get_str(payload, "template_text"),
            description=_get_str(payload, "description"),
        )
        recorded = payload.get("content_hash")
        if recorded is not None and recorded != prompt.content_hash:
            raise RegistryValidationError(
                f"content_hash mismatch for {prompt.prompt_version!r}: the stored "
                "template text does not hash to the recorded content_hash"
            )
        return prompt


@dataclass(frozen=True)
class ParsedPromptVersion:
    """Components of a canonical prompt-version string."""

    prompt_id: str
    version_number: int
    hash_prefix: str


def parse_prompt_version(value: str) -> ParsedPromptVersion:
    """Parse a canonical version string, failing visibly on any other shape."""

    match = _PROMPT_VERSION_RE.match(value)
    if match is None:
        raise RegistryValidationError(
            f"prompt_version {value!r} is not canonical "
            "('<prompt-id>/v<N>+<12-hex-hash-prefix>')"
        )
    return ParsedPromptVersion(
        prompt_id=match.group("prompt_id"),
        version_number=int(match.group("number")),
        hash_prefix=match.group("hash_prefix"),
    )


class ModelPromptRegistry:
    """Append-only registry of model routes and prompt template versions."""

    def __init__(
        self,
        *,
        models: Iterable[RegisteredModel] = (),
        prompts: Iterable[RegisteredPrompt] = (),
    ) -> None:
        self._models: dict[str, RegisteredModel] = {}
        self._prompts: dict[tuple[str, int], RegisteredPrompt] = {}
        for model in models:
            self.register_model(model)
        for prompt in prompts:
            self.register_prompt(prompt)

    def register_model(self, model: RegisteredModel) -> RegisteredModel:
        existing = self._models.get(model.model_id)
        if existing is not None:
            if existing == model:
                return existing
            raise RegistryValidationError(
                f"model {model.model_id!r} is already registered with a different "
                "description; registry entries are append-only"
            )
        self._models[model.model_id] = model
        return model

    def register_prompt(self, prompt: RegisteredPrompt) -> RegisteredPrompt:
        key = (prompt.prompt_id, prompt.version_number)
        existing = self._prompts.get(key)
        if existing is not None:
            if existing.content_hash == prompt.content_hash:
                return existing
            raise RegistryValidationError(
                f"prompt {prompt.prompt_id!r} v{prompt.version_number} is already "
                "registered with different template content; prompt versions are "
                "immutable — register the new content as the next version_number"
            )
        latest = self.latest_version_number(prompt.prompt_id)
        if prompt.version_number != latest + 1:
            raise RegistryValidationError(
                f"prompt {prompt.prompt_id!r} next version must be v{latest + 1}; "
                f"got v{prompt.version_number} (versions are dense and append-only)"
            )
        self._prompts[key] = prompt
        return prompt

    def latest_version_number(self, prompt_id: str) -> int:
        return max(
            (number for pid, number in self._prompts if pid == prompt_id),
            default=0,
        )

    def latest_prompt(self, prompt_id: str) -> RegisteredPrompt:
        latest = self.latest_version_number(prompt_id)
        if latest == 0:
            raise RegistryValidationError(f"no versions registered for prompt {prompt_id!r}")
        return self._prompts[(prompt_id, latest)]

    def resolve_model(self, model_id: str) -> RegisteredModel:
        model = self._models.get(model_id)
        if model is None:
            raise RegistryValidationError(
                f"model {model_id!r} is not registered; register the route before "
                "stamping ledger events with it"
            )
        return model

    def resolve_prompt_version(self, prompt_version: str) -> RegisteredPrompt:
        """Resolve a canonical version string, verifying its content-hash prefix."""

        parsed = parse_prompt_version(prompt_version)
        prompt = self._prompts.get((parsed.prompt_id, parsed.version_number))
        if prompt is None:
            raise RegistryValidationError(
                f"prompt_version {prompt_version!r} is not registered"
            )
        if not prompt.content_hash.startswith(parsed.hash_prefix):
            raise RegistryValidationError(
                f"prompt_version {prompt_version!r} hash prefix does not match the "
                "registered template content — prompt drift detected; refuse to "
                "treat the verdicts as comparable"
            )
        return prompt

    def stamp(self, *, model_id: str, prompt_version: str) -> tuple[str, str]:
        """Return the validated atomic pair for ledger stamping (cortex#326).

        Both halves must resolve; the ledger's model_id/prompt_version
        together-or-neither CHECK is mirrored here at the registry boundary.
        """

        model = self.resolve_model(model_id)
        prompt = self.resolve_prompt_version(prompt_version)
        return (model.model_id, prompt.prompt_version)

    @property
    def models(self) -> tuple[RegisteredModel, ...]:
        return tuple(self._models[key] for key in sorted(self._models))

    @property
    def prompts(self) -> tuple[RegisteredPrompt, ...]:
        return tuple(self._prompts[key] for key in sorted(self._prompts))

    def as_payload(self) -> dict[str, Any]:
        return {
            "models": [model.as_payload() for model in self.models],
            "prompts": [prompt.as_payload() for prompt in self.prompts],
            "registry_schema_version": REGISTRY_SCHEMA_VERSION,
        }

    def to_canonical_json(self) -> str:
        """Serialize deterministically; identical registries are identical bytes."""

        return (
            json.dumps(self.as_payload(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n"
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ModelPromptRegistry:
        if not isinstance(payload, Mapping):
            raise RegistryValidationError("registry payload must be a JSON object")
        raw_version = payload.get("registry_schema_version")
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise RegistryValidationError(
                "registry_schema_version must be an integer; refusing to guess"
            )
        if raw_version != REGISTRY_SCHEMA_VERSION:
            raise RegistryValidationError(
                f"unknown registry_schema_version {raw_version!r}; this loader "
                f"supports only {REGISTRY_SCHEMA_VERSION} — no silent fallback"
            )
        models = [
            RegisteredModel.from_payload(item) for item in _get_object_list(payload, "models")
        ]
        prompts = sorted(
            (
                RegisteredPrompt.from_payload(item)
                for item in _get_object_list(payload, "prompts")
            ),
            key=lambda prompt: (prompt.prompt_id, prompt.version_number),
        )
        return cls(models=models, prompts=prompts)

    @classmethod
    def from_json(cls, text: str) -> ModelPromptRegistry:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RegistryValidationError(f"registry is not valid JSON: {exc}") from exc
        return cls.from_payload(payload)


def _get_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise RegistryValidationError(f"{key} must be a string; got {type(value).__name__}")
    return value


def _get_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise RegistryValidationError(f"{key} must be an integer")
    return value


def _get_object_list(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise RegistryValidationError(f"{key} must be a list")
    for item in value:
        if not isinstance(item, Mapping):
            raise RegistryValidationError(f"{key} entries must be JSON objects")
    return value
