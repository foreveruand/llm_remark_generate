from __future__ import annotations

from urllib.parse import urlencode

from ..http_client import request_json
from ..models import SearchResult
from .base import SearchProvider


class BraveSearchProvider(SearchProvider):
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        params = urlencode({"q": query, "count": max_results})
        response = request_json(
            "GET",
            f"{self.endpoint}?{params}",
            headers={
                "X-Subscription-Token": self.api_key,
                "Accept": "application/json",
            },
            timeout=self.timeout,
        )
        web = response.get("web", {})
        raw_results = web.get("results", []) if isinstance(web, dict) else []
        if not isinstance(raw_results, list):
            return []

        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = _as_text(item.get("title"))
            url = _as_text(item.get("url"))
            description = _as_text(item.get("description"))
            snippets = item.get("extra_snippets")
            if isinstance(snippets, list):
                description = " ".join([description, *[_as_text(value) for value in snippets]]).strip()
            if title and url:
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        content=description,
                        provider=self.name,
                    )
                )
        return results


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
