from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .http_client import HttpClientError, request_json
from .models import JsonDict


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config: JsonDict) -> None:
        llm = config["llm"]
        self.base_url = _normalize_base_url(str(llm["base_url"]))
        self.api_key = str(llm["api_key"])
        self.model = str(llm["model"])
        self.temperature = float(llm.get("temperature", 0.2))
        self.timeout = int(llm.get("timeout_seconds", 60))

    def chat(self, messages: list[dict[str, str]], *, response_format: JsonDict | None = None) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        try:
            response = self._post_chat(payload)
        except HttpClientError as exc:
            if response_format and _is_response_format_rejected(exc):
                fallback_payload = dict(payload)
                fallback_payload.pop("response_format", None)
                try:
                    response = self._post_chat(fallback_payload)
                except HttpClientError as fallback_exc:
                    raise LLMError(str(fallback_exc)) from fallback_exc
            else:
                raise LLMError(str(exc)) from exc

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("LLM response did not contain choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMError("LLM choice was not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMError("LLM choice did not contain a message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM response content was empty")
        return content.strip()

    def _post_chat(self, payload: dict[str, Any]) -> JsonDict:
        return request_json(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            payload=payload,
            timeout=self.timeout,
        )


def _normalize_base_url(base_url: str) -> str:
    stripped = base_url.strip().rstrip("/")
    parts = urlsplit(stripped)
    if parts.scheme and parts.netloc and parts.path in ("", "/"):
        return urlunsplit((parts.scheme, parts.netloc, "/v1", "", ""))
    return stripped


def _is_response_format_rejected(exc: HttpClientError) -> bool:
    message = str(exc).lower()
    return "http 400" in message and "response_format" in message
