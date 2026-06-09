from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import JsonDict, SearchResult


class SearchProvider(ABC):
    name: str

    @abstractmethod
    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        raise NotImplementedError


def build_search_providers(config: JsonDict) -> list[SearchProvider]:
    search = config["search"]
    if not search.get("enabled", True):
        return []

    providers: list[SearchProvider] = []
    for provider_name in search.get("providers", []):
        if provider_name == "brave":
            from .brave import BraveSearchProvider

            providers.append(
                BraveSearchProvider(
                    api_key=search["brave_api_key"],
                    timeout=int(search.get("timeout_seconds", 20)),
                )
            )
        elif provider_name == "tavily":
            from .tavily import TavilySearchProvider

            providers.append(
                TavilySearchProvider(
                    api_key=search["tavily_api_key"],
                    timeout=int(search.get("timeout_seconds", 20)),
                )
            )
    return providers


def dedupe_results(results: list[SearchResult], *, limit: int) -> list[SearchResult]:
    deduped: list[SearchResult] = []
    seen_urls: set[str] = set()
    for result in results:
        normalized_url = result.url.strip().rstrip("/")
        if not normalized_url or normalized_url in seen_urls:
            continue
        seen_urls.add(normalized_url)
        deduped.append(result)
        if len(deduped) >= limit:
            break
    return deduped
