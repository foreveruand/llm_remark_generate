from __future__ import annotations

from ..http_client import request_json
from ..models import SearchResult
from .base import SearchProvider


class TavilySearchProvider(SearchProvider):
    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, *, api_key: str, timeout: int = 20) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        response = request_json(
            "POST",
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            payload={
                "query": query,
                "max_results": max_results,
                "include_answer": True,
                "include_raw_content": False,
            },
            timeout=self.timeout,
        )
        results: list[SearchResult] = []
        answer = response.get("answer")
        if isinstance(answer, str) and answer.strip():
            results.append(
                SearchResult(
                    title="Tavily answer",
                    url="https://api.tavily.com/search",
                    content=answer.strip(),
                    provider=self.name,
                )
            )

        raw_results = response.get("results", [])
        if not isinstance(raw_results, list):
            return results

        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = _as_text(item.get("title"))
            url = _as_text(item.get("url"))
            content = _as_text(item.get("content")) or _as_text(item.get("raw_content"))
            score = item.get("score")
            if title and url:
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        content=content,
                        provider=self.name,
                        score=score if isinstance(score, (int, float)) else None,
                    )
                )
        return results


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
