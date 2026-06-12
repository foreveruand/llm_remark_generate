from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import FieldMapping, JsonDict


DEFAULT_CONFIG: JsonDict = {
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "api_type": "completion",
        "model": "gpt-4.1-mini",
        "temperature": 0.2,
        "timeout_seconds": 60,
    },
    "search": {
        "enabled": True,
        "providers": ["tavily", "brave"],
        "brave_api_key": "",
        "tavily_api_key": "",
        "max_results": 5,
        "timeout_seconds": 20,
    },
    "batch": {
        "enabled": False,
        "max_notes_per_request": 10,
        "max_chars_per_request": 30000,
        "fallback_to_single_on_error": True,
    },
    "mappings": {},
    "prompt": {
        "system": (
            "You are an expert tutor. Generate a concise, accurate explanation "
            "for the Anki note. If the answer depends on current or external "
            "facts, request web search before writing the final explanation."
        ),
        "analysis_instruction": (
            "Return JSON only: {\"need_search\": boolean, \"queries\": [string], "
            "\"reason\": string}. Use at most 3 targeted queries."
        ),
        "final_instruction": (
            "Write the explanation in clear HTML suitable for an Anki field. "
            "Explain why the answer is correct and address likely distractors. "
            "When search results are provided, cite source titles or URLs briefly."
        ),
    },
}


class ConfigError(ValueError):
    pass


def merged_config(raw: JsonDict | None) -> JsonDict:
    config = deepcopy(DEFAULT_CONFIG)
    if not raw:
        return config
    _deep_update(config, raw)
    return config


def parse_mappings(config: JsonDict) -> dict[str, FieldMapping]:
    mappings = config.get("mappings")
    if not isinstance(mappings, dict):
        raise ConfigError("mappings must be an object keyed by note type name")

    parsed: dict[str, FieldMapping] = {}
    for note_type, value in mappings.items():
        if not isinstance(note_type, str) or not note_type.strip():
            raise ConfigError("mapping note type names must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigError(f"mapping for {note_type!r} must be an object")
        source_fields = value.get("source_fields")
        target_field = value.get("target_field")
        if not isinstance(source_fields, list) or not source_fields:
            raise ConfigError(f"mapping for {note_type!r} must define source_fields")
        if not all(isinstance(field, str) and field.strip() for field in source_fields):
            raise ConfigError(f"source_fields for {note_type!r} must be non-empty strings")
        if not isinstance(target_field, str) or not target_field.strip():
            raise ConfigError(f"mapping for {note_type!r} must define target_field")
        parsed[note_type] = FieldMapping(
            note_type=note_type,
            source_fields=list(source_fields),
            target_field=target_field,
        )
    return parsed


def validate_config(config: JsonDict) -> None:
    llm = _require_object(config, "llm")
    if not _non_empty_string(llm.get("base_url")):
        raise ConfigError("llm.base_url is required")
    if not _non_empty_string(llm.get("api_key")):
        raise ConfigError("llm.api_key is required")
    if llm.get("api_type", "completion") not in {"completion", "response"}:
        raise ConfigError('llm.api_type must be "completion" or "response"')
    if not _non_empty_string(llm.get("model")):
        raise ConfigError("llm.model is required")

    search = _require_object(config, "search")
    providers = search.get("providers", [])
    if search.get("enabled", True):
        if not isinstance(providers, list) or not providers:
            raise ConfigError("search.providers must contain at least one provider")
        unknown = set(providers) - {"brave", "tavily"}
        if unknown:
            raise ConfigError(f"unsupported search providers: {', '.join(sorted(unknown))}")
        if "brave" in providers and not _non_empty_string(search.get("brave_api_key")):
            raise ConfigError("search.brave_api_key is required when Brave is enabled")
        if "tavily" in providers and not _non_empty_string(search.get("tavily_api_key")):
            raise ConfigError("search.tavily_api_key is required when Tavily is enabled")

    batch = _require_object(config, "batch")
    if not _positive_int(batch.get("max_notes_per_request")):
        raise ConfigError("batch.max_notes_per_request must be a positive integer")
    if not _positive_int(batch.get("max_chars_per_request")):
        raise ConfigError("batch.max_chars_per_request must be a positive integer")
    if not isinstance(batch.get("enabled", False), bool):
        raise ConfigError("batch.enabled must be a boolean")
    if not isinstance(batch.get("fallback_to_single_on_error", True), bool):
        raise ConfigError("batch.fallback_to_single_on_error must be a boolean")

    parse_mappings(config)


def _deep_update(target: JsonDict, source: JsonDict) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _require_object(config: JsonDict, key: str) -> JsonDict:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be an object")
    return value


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
