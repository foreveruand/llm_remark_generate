from __future__ import annotations

import unittest
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin.config import merged_config
from ankiplugin.http_client import HttpClientError
from ankiplugin.llm_client import LLMClient
from ankiplugin.search import brave as brave_module
from ankiplugin.search import tavily as tavily_module
from ankiplugin.search.brave import BraveSearchProvider
from ankiplugin.search.tavily import TavilySearchProvider


class LLMClientTest(unittest.TestCase):
    def test_chat_posts_openai_compatible_request(self) -> None:
        captured: dict[str, Any] = {}

        def fake_request_json(
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            payload: dict[str, Any] | None = None,
            timeout: int | float = 30,
        ) -> dict[str, Any]:
            captured.update(
                {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                }
            )
            return {"choices": [{"message": {"content": " <p>ok</p> "}}]}

        import ankiplugin.llm_client as llm_module

        original_request_json = llm_module.request_json
        llm_module.request_json = fake_request_json
        try:
            config = merged_config(
                {
                    "llm": {
                        "base_url": "https://llm.example/v1/",
                        "api_key": "secret",
                        "model": "model-a",
                        "temperature": 0.1,
                        "timeout_seconds": 12,
                    }
                }
            )
            content = LLMClient(config).chat(
                [{"role": "user", "content": "hello"}],
                response_format={"type": "json_object"},
            )
        finally:
            llm_module.request_json = original_request_json

        self.assertEqual("<p>ok</p>", content)
        self.assertEqual("POST", captured["method"])
        self.assertEqual("https://llm.example/v1/chat/completions", captured["url"])
        self.assertEqual("Bearer secret", captured["headers"]["Authorization"])
        self.assertEqual(
            {
                "model": "model-a",
                "messages": [{"role": "user", "content": "hello"}],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            captured["payload"],
        )
        self.assertEqual(12, captured["timeout"])

    def test_chat_normalizes_root_base_url_to_openai_v1(self) -> None:
        captured: dict[str, Any] = {}

        def fake_request_json(
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            payload: dict[str, Any] | None = None,
            timeout: int | float = 30,
        ) -> dict[str, Any]:
            captured["url"] = url
            return {"choices": [{"message": {"content": "ok"}}]}

        import ankiplugin.llm_client as llm_module

        original_request_json = llm_module.request_json
        llm_module.request_json = fake_request_json
        try:
            config = merged_config(
                {
                    "llm": {
                        "base_url": "http://llm.example:3000/",
                        "api_key": "secret",
                        "model": "model-a",
                    }
                }
            )
            LLMClient(config).chat([{"role": "user", "content": "hello"}])
        finally:
            llm_module.request_json = original_request_json

        self.assertEqual("http://llm.example:3000/v1/chat/completions", captured["url"])

    def test_chat_retries_without_response_format_when_endpoint_rejects_it(self) -> None:
        captured_payloads: list[dict[str, Any]] = []

        def fake_request_json(
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            payload: dict[str, Any] | None = None,
            timeout: int | float = 30,
        ) -> dict[str, Any]:
            captured_payloads.append(dict(payload or {}))
            if len(captured_payloads) == 1:
                raise HttpClientError(
                    "HTTP 400 for http://llm.example/v1/chat/completions: response_format invalid"
                )
            return {"choices": [{"message": {"content": "{\"need_search\": false}"}}]}

        import ankiplugin.llm_client as llm_module

        original_request_json = llm_module.request_json
        llm_module.request_json = fake_request_json
        try:
            config = merged_config({"llm": {"api_key": "secret", "model": "model-a"}})
            content = LLMClient(config).chat(
                [{"role": "user", "content": "Return JSON only: {}"}],
                response_format={"type": "json_object"},
            )
        finally:
            llm_module.request_json = original_request_json

        self.assertEqual("{\"need_search\": false}", content)
        self.assertEqual({"type": "json_object"}, captured_payloads[0]["response_format"])
        self.assertNotIn("response_format", captured_payloads[1])

    def test_chat_response_api_posts_responses_request(self) -> None:
        captured: dict[str, Any] = {}

        def fake_request_json(
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            payload: dict[str, Any] | None = None,
            timeout: int | float = 30,
        ) -> dict[str, Any]:
            captured.update(
                {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                }
            )
            return {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": " first "},
                            {"type": "refusal", "refusal": "ignored"},
                            {"type": "output_text", "text": ""},
                        ]
                    },
                    {"content": [{"type": "output_text", "text": "second"}]},
                ]
            }

        import ankiplugin.llm_client as llm_module

        original_request_json = llm_module.request_json
        llm_module.request_json = fake_request_json
        try:
            config = merged_config(
                {
                    "llm": {
                        "base_url": "https://llm.example/v1/",
                        "api_key": "secret",
                        "api_type": "response",
                        "model": "model-a",
                        "temperature": 0.1,
                        "timeout_seconds": 12,
                    }
                }
            )
            content = LLMClient(config).chat(
                [{"role": "user", "content": "hello"}],
                response_format={"type": "json_object"},
            )
        finally:
            llm_module.request_json = original_request_json

        self.assertEqual("first\nsecond", content)
        self.assertEqual("POST", captured["method"])
        self.assertEqual("https://llm.example/v1/responses", captured["url"])
        self.assertEqual("Bearer secret", captured["headers"]["Authorization"])
        self.assertEqual(
            {
                "model": "model-a",
                "input": [{"role": "user", "content": "hello"}],
                "temperature": 0.1,
                "store": False,
                "text": {"format": {"type": "json_object"}},
            },
            captured["payload"],
        )
        self.assertEqual(12, captured["timeout"])

    def test_chat_response_api_retries_without_text_format_when_endpoint_rejects_it(self) -> None:
        captured_payloads: list[dict[str, Any]] = []

        def fake_request_json(
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            payload: dict[str, Any] | None = None,
            timeout: int | float = 30,
        ) -> dict[str, Any]:
            captured_payloads.append(dict(payload or {}))
            if len(captured_payloads) == 1:
                raise HttpClientError(
                    "HTTP 400 for http://llm.example/v1/responses: text.format json_object invalid"
                )
            return {
                "output": [
                    {"content": [{"type": "output_text", "text": "{\"need_search\": false}"}]}
                ]
            }

        import ankiplugin.llm_client as llm_module

        original_request_json = llm_module.request_json
        llm_module.request_json = fake_request_json
        try:
            config = merged_config(
                {
                    "llm": {
                        "api_key": "secret",
                        "api_type": "response",
                        "model": "model-a",
                    }
                }
            )
            content = LLMClient(config).chat(
                [{"role": "user", "content": "Return JSON only: {}"}],
                response_format={"type": "json_object"},
            )
        finally:
            llm_module.request_json = original_request_json

        self.assertEqual("{\"need_search\": false}", content)
        self.assertEqual({"format": {"type": "json_object"}}, captured_payloads[0]["text"])
        self.assertNotIn("text", captured_payloads[1])


class SearchProviderTest(unittest.TestCase):
    def test_brave_maps_web_results_and_extra_snippets(self) -> None:
        captured: dict[str, Any] = {}

        def fake_request_json(
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            payload: dict[str, Any] | None = None,
            timeout: int | float = 30,
        ) -> dict[str, Any]:
            captured.update({"method": method, "url": url, "headers": headers, "timeout": timeout})
            return {
                "web": {
                    "results": [
                        {
                            "title": "Result",
                            "url": "https://example.test",
                            "description": "Summary",
                            "extra_snippets": ["Detail"],
                        },
                        {"title": "Missing URL"},
                    ]
                }
            }

        original_request_json = brave_module.request_json
        brave_module.request_json = fake_request_json
        try:
            results = BraveSearchProvider(api_key="brave-key", timeout=7).search("hello world", max_results=3)
        finally:
            brave_module.request_json = original_request_json

        self.assertEqual("GET", captured["method"])
        self.assertIn("q=hello+world", captured["url"])
        self.assertIn("count=3", captured["url"])
        self.assertEqual("brave-key", captured["headers"]["X-Subscription-Token"])
        self.assertEqual(7, captured["timeout"])
        self.assertEqual(1, len(results))
        self.assertEqual("Result", results[0].title)
        self.assertEqual("Summary Detail", results[0].content)
        self.assertEqual("brave", results[0].provider)

    def test_tavily_maps_answer_and_results(self) -> None:
        captured: dict[str, Any] = {}

        def fake_request_json(
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            payload: dict[str, Any] | None = None,
            timeout: int | float = 30,
        ) -> dict[str, Any]:
            captured.update(
                {
                    "method": method,
                    "url": url,
                    "headers": headers,
                    "payload": payload,
                    "timeout": timeout,
                }
            )
            return {
                "answer": "Direct answer",
                "results": [
                    {
                        "title": "Source",
                        "url": "https://source.test",
                        "content": "Source content",
                        "score": 0.9,
                    }
                ],
            }

        original_request_json = tavily_module.request_json
        tavily_module.request_json = fake_request_json
        try:
            results = TavilySearchProvider(api_key="tavily-key", timeout=8).search("query", max_results=4)
        finally:
            tavily_module.request_json = original_request_json

        self.assertEqual("POST", captured["method"])
        self.assertEqual("https://api.tavily.com/search", captured["url"])
        self.assertEqual("Bearer tavily-key", captured["headers"]["Authorization"])
        self.assertEqual("query", captured["payload"]["query"])
        self.assertEqual(4, captured["payload"]["max_results"])
        self.assertEqual(8, captured["timeout"])
        self.assertEqual(2, len(results))
        self.assertEqual("Tavily answer", results[0].title)
        self.assertEqual("Source", results[1].title)
        self.assertEqual(0.9, results[1].score)


if __name__ == "__main__":
    unittest.main()
