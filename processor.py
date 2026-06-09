from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from html import escape
from typing import Protocol

from .config import parse_mappings
from .llm_client import LLMClient
from .models import BatchResult, FieldMapping, JsonDict, NoteProcessResult, SearchResult
from .search import SearchProvider, build_search_providers, dedupe_results


class NoteLike(Protocol):
    id: int

    def note_type(self) -> JsonDict:
        ...

    def keys(self) -> Iterable[str]:
        ...

    def __getitem__(self, field: str) -> str:
        ...

    def __setitem__(self, field: str, value: str) -> None:
        ...


class CollectionLike(Protocol):
    def get_note(self, note_id: int) -> NoteLike:
        ...

    def update_note(self, note: NoteLike) -> None:
        ...


def process_notes(
    col: CollectionLike,
    note_ids: list[int],
    config: JsonDict,
    *,
    llm_client: LLMClient | None = None,
    search_providers: list[SearchProvider] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> BatchResult:
    mappings = parse_mappings(config)
    llm = llm_client or LLMClient(config)
    providers = build_search_providers(config) if search_providers is None else search_providers
    max_results = int(config["search"].get("max_results", 5))
    prompt_config = config["prompt"]

    batch = BatchResult()
    total = len(note_ids)
    for index, note_id in enumerate(note_ids, start=1):
        try:
            note = col.get_note(note_id)
            result = process_note(
                col,
                note,
                mappings,
                llm,
                providers,
                max_results=max_results,
                prompt_config=prompt_config,
            )
        except Exception as exc:
            result = NoteProcessResult(note_id=note_id, status="failed", message=str(exc))
        batch.add(result)
        if progress:
            progress(index, total)
    return batch


def process_note(
    col: CollectionLike,
    note: NoteLike,
    mappings: dict[str, FieldMapping],
    llm: LLMClient,
    search_providers: list[SearchProvider],
    *,
    max_results: int,
    prompt_config: JsonDict,
) -> NoteProcessResult:
    note_type = note.note_type()
    note_type_name = note_type.get("name") if isinstance(note_type, dict) else None
    if not isinstance(note_type_name, str):
        return NoteProcessResult(note_id=note.id, status="skipped_unmapped", message="missing note type name")

    mapping = mappings.get(note_type_name)
    if mapping is None:
        return NoteProcessResult(note_id=note.id, status="skipped_unmapped", message=f"unmapped note type: {note_type_name}")

    fields = set(note.keys())
    missing = [field for field in [*mapping.source_fields, mapping.target_field] if field not in fields]
    if missing:
        return NoteProcessResult(note_id=note.id, status="failed", message=f"missing fields: {', '.join(missing)}")

    if note[mapping.target_field].strip():
        return NoteProcessResult(note_id=note.id, status="skipped_existing", message="target field already has content")

    source_text = format_note_fields(note, mapping)
    search_decision = decide_search(llm, source_text, prompt_config)
    search_results: list[SearchResult] = []
    if search_decision.get("need_search") and search_providers:
        queries = [query for query in search_decision.get("queries", []) if isinstance(query, str) and query.strip()]
        search_results = run_searches(search_providers, queries[:3], max_results=max_results)

    explanation = generate_explanation(llm, source_text, search_results, prompt_config)
    note[mapping.target_field] = explanation
    col.update_note(note)
    return NoteProcessResult(note_id=note.id, status="written")


def decide_search(llm: LLMClient, source_text: str, prompt_config: JsonDict) -> JsonDict:
    content = llm.chat(
        [
            {"role": "system", "content": str(prompt_config["system"])},
            {"role": "user", "content": f"{prompt_config['analysis_instruction']}\n\n{source_text}"},
        ],
        response_format={"type": "json_object"},
    )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"need_search": False, "queries": [], "reason": "invalid search decision JSON"}
    if not isinstance(parsed, dict):
        return {"need_search": False, "queries": [], "reason": "search decision was not an object"}
    return parsed


def generate_explanation(
    llm: LLMClient,
    source_text: str,
    search_results: list[SearchResult],
    prompt_config: JsonDict,
) -> str:
    search_context = format_search_results(search_results)
    user_content = "\n\n".join(
        [
            str(prompt_config["final_instruction"]),
            "Note fields:",
            source_text,
            "Search results:",
            search_context or "No search results were used.",
        ]
    )
    return llm.chat(
        [
            {"role": "system", "content": str(prompt_config["system"])},
            {"role": "user", "content": user_content},
        ]
    )


def run_searches(
    providers: list[SearchProvider],
    queries: list[str],
    *,
    max_results: int,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for query in queries:
        for provider in providers:
            results.extend(provider.search(query, max_results=max_results))
    return dedupe_results(results, limit=max_results)


def format_note_fields(note: NoteLike, mapping: FieldMapping) -> str:
    parts = []
    for field in mapping.source_fields:
        parts.append(f"{field}:\n{note[field]}")
    return "\n\n".join(parts)


def format_search_results(results: list[SearchResult]) -> str:
    lines = []
    for index, result in enumerate(results, start=1):
        content = result.content.strip()
        lines.append(
            "\n".join(
                [
                    f"[{index}] {escape(result.title)}",
                    f"Provider: {escape(result.provider)}",
                    f"URL: {escape(result.url)}",
                    f"Content: {escape(content)}",
                ]
            )
        )
    return "\n\n".join(lines)
