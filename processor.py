from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from html import escape
from typing import Protocol

from .config import parse_mappings
from .documents import LocalDocumentProvider
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


@dataclass
class PreparedNote:
    note: NoteLike
    mapping: FieldMapping
    source_text: str
    search_results: list[SearchResult] = field(default_factory=list)
    search_checked: bool = False
    tool_results: list[SearchResult] = field(default_factory=list)
    tool_checked: bool = False
    explanation: str | None = None


class BatchGenerationError(RuntimeError):
    pass


class ProcessingCancelled(RuntimeError):
    pass


CancelRequested = Callable[[], bool]


def process_notes(
    col: CollectionLike,
    note_ids: list[int],
    config: JsonDict,
    *,
    llm_client: LLMClient | None = None,
    search_providers: list[SearchProvider] | None = None,
    progress: Callable[[int, int], None] | None = None,
    cancel_requested: CancelRequested | None = None,
    append: bool = False,
) -> BatchResult:
    mappings = parse_mappings(config)
    llm = llm_client or LLMClient(config)
    providers = build_search_providers(config) if search_providers is None else search_providers
    document_provider = LocalDocumentProvider.from_config(config)
    max_results = int(config["search"].get("max_results", 5))
    prompt_config = config["prompt"]
    batch_config = config.get("batch", {})
    tool_config = config.get("documents", {})

    if _cancel_requested(cancel_requested):
        return BatchResult(cancelled=True)

    if not append and _batch_enabled(batch_config) and len(note_ids) > 1 and len(set(note_ids)) == len(note_ids):
        return process_notes_batched(
            col,
            note_ids,
            mappings,
            llm,
            providers,
            document_provider,
            max_results=max_results,
            prompt_config=prompt_config,
            tool_config=tool_config,
            batch_config=batch_config,
            progress=progress,
            cancel_requested=cancel_requested,
        )

    return process_notes_individually(
        col,
        note_ids,
        mappings,
        llm,
        providers,
        document_provider,
        max_results=max_results,
        prompt_config=prompt_config,
        tool_config=tool_config,
        progress=progress,
        cancel_requested=cancel_requested,
        append=append,
    )


def process_notes_individually(
    col: CollectionLike,
    note_ids: list[int],
    mappings: dict[str, FieldMapping],
    llm: LLMClient,
    providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    progress: Callable[[int, int], None] | None = None,
    cancel_requested: CancelRequested | None = None,
    append: bool = False,
) -> BatchResult:
    batch = BatchResult()
    total = len(note_ids)
    for index, note_id in enumerate(note_ids, start=1):
        if _cancel_requested(cancel_requested):
            batch.cancelled = True
            break
        try:
            note = col.get_note(note_id)
            result = process_note(
                col,
                note,
                mappings,
                llm,
                providers,
                document_provider,
                max_results=max_results,
                prompt_config=prompt_config,
                tool_config=tool_config,
                cancel_requested=cancel_requested,
                append=append,
            )
        except ProcessingCancelled:
            batch.cancelled = True
            break
        except Exception as exc:
            result = NoteProcessResult(note_id=note_id, status="failed", message=str(exc))
        batch.add(result)
        if progress:
            progress(index, total)
    return batch


def process_notes_batched(
    col: CollectionLike,
    note_ids: list[int],
    mappings: dict[str, FieldMapping],
    llm: LLMClient,
    providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    batch_config: JsonDict,
    progress: Callable[[int, int], None] | None = None,
    cancel_requested: CancelRequested | None = None,
) -> BatchResult:
    result_by_note_id: dict[int, NoteProcessResult] = {}
    prepared_notes: list[PreparedNote] = []

    for note_id in note_ids:
        if _cancel_requested(cancel_requested):
            return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
        try:
            note = col.get_note(note_id)
            prepared_or_result = prepare_note(note, mappings)
        except Exception as exc:
            result_by_note_id[note_id] = NoteProcessResult(note_id=note_id, status="failed", message=str(exc))
            continue

        if isinstance(prepared_or_result, NoteProcessResult):
            result_by_note_id[note_id] = prepared_or_result
        else:
            prepared_notes.append(prepared_or_result)

    if len(prepared_notes) <= 1:
        try:
            _process_prepared_notes_individually(
                col,
                prepared_notes,
                result_by_note_id,
                llm,
                providers,
                document_provider,
                max_results=max_results,
                prompt_config=prompt_config,
                tool_config=tool_config,
                cancel_requested=cancel_requested,
            )
        except ProcessingCancelled:
            return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
        return _build_batch_result(note_ids, result_by_note_id, progress)

    for prepared in prepared_notes:
        if _cancel_requested(cancel_requested):
            return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
        try:
            ensure_search_results(
                prepared,
                llm,
                providers,
                document_provider,
                max_results=max_results,
                prompt_config=prompt_config,
                tool_config=tool_config,
                cancel_requested=cancel_requested,
            )
        except ProcessingCancelled:
            return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
        except Exception as exc:
            result_by_note_id[prepared.note.id] = NoteProcessResult(
                note_id=prepared.note.id,
                status="failed",
                message=str(exc),
            )

    batch_candidates = [prepared for prepared in prepared_notes if prepared.note.id not in result_by_note_id]
    if len(batch_candidates) <= 1:
        try:
            _process_prepared_notes_individually(
                col,
                batch_candidates,
                result_by_note_id,
                llm,
                providers,
                document_provider,
                max_results=max_results,
                prompt_config=prompt_config,
                tool_config=tool_config,
                cancel_requested=cancel_requested,
            )
        except ProcessingCancelled:
            return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
        return _build_batch_result(note_ids, result_by_note_id, progress)

    fallback = bool(batch_config.get("fallback_to_single_on_error", True))
    chunks = split_prepared_notes(
        batch_candidates,
        max_notes=int(batch_config.get("max_notes_per_request", 10)),
        max_chars=int(batch_config.get("max_chars_per_request", 30000)),
    )
    for chunk in chunks:
        if _cancel_requested(cancel_requested):
            return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
        if len(chunk) == 1:
            try:
                _process_prepared_notes_individually(
                    col,
                    chunk,
                    result_by_note_id,
                    llm,
                    providers,
                    document_provider,
                    max_results=max_results,
                    prompt_config=prompt_config,
                    tool_config=tool_config,
                    cancel_requested=cancel_requested,
                )
            except ProcessingCancelled:
                return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
            continue

        try:
            _raise_if_cancelled(cancel_requested)
            explanations = generate_batch_explanations(llm, chunk, prompt_config)
            _raise_if_cancelled(cancel_requested)
        except ProcessingCancelled:
            return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
        except Exception as exc:
            if fallback:
                try:
                    _process_prepared_notes_individually(
                        col,
                        chunk,
                        result_by_note_id,
                        llm,
                        providers,
                        document_provider,
                        max_results=max_results,
                        prompt_config=prompt_config,
                        tool_config=tool_config,
                        cancel_requested=cancel_requested,
                    )
                except ProcessingCancelled:
                    return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
            else:
                message = str(exc)
                for prepared in chunk:
                    result_by_note_id[prepared.note.id] = NoteProcessResult(
                        note_id=prepared.note.id,
                        status="failed",
                        message=message,
                    )
            continue

        for prepared in chunk:
            if _cancel_requested(cancel_requested):
                return _build_batch_result(note_ids, result_by_note_id, progress, cancelled=True)
            note_id = prepared.note.id
            try:
                prepared.note[prepared.mapping.target_field] = explanations[note_id]
                col.update_note(prepared.note)
                result = NoteProcessResult(note_id=note_id, status="written")
            except Exception as exc:
                result = NoteProcessResult(note_id=note_id, status="failed", message=str(exc))
            result_by_note_id[note_id] = result

    return _build_batch_result(note_ids, result_by_note_id, progress)


def process_note(
    col: CollectionLike,
    note: NoteLike,
    mappings: dict[str, FieldMapping],
    llm: LLMClient,
    search_providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    cancel_requested: CancelRequested | None = None,
    append: bool = False,
) -> NoteProcessResult:
    _raise_if_cancelled(cancel_requested)
    prepared_or_result = prepare_note(note, mappings, skip_existing=not append)
    if isinstance(prepared_or_result, NoteProcessResult):
        return prepared_or_result
    return process_prepared_note(
        col,
        prepared_or_result,
        llm,
        search_providers,
        document_provider,
        max_results=max_results,
        prompt_config=prompt_config,
        tool_config=tool_config,
        cancel_requested=cancel_requested,
        append=append,
    )


def prepare_note(
    note: NoteLike,
    mappings: dict[str, FieldMapping],
    *,
    skip_existing: bool = True,
) -> PreparedNote | NoteProcessResult:
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

    if skip_existing and note[mapping.target_field].strip():
        return NoteProcessResult(note_id=note.id, status="skipped_existing", message="target field already has content")

    source_text = format_note_fields(note, mapping)
    return PreparedNote(note=note, mapping=mapping, source_text=source_text)


def process_prepared_note(
    col: CollectionLike,
    prepared: PreparedNote,
    llm: LLMClient,
    search_providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    cancel_requested: CancelRequested | None = None,
    append: bool = False,
) -> NoteProcessResult:
    _raise_if_cancelled(cancel_requested)
    source_text = prepared.source_text
    search_results = ensure_search_results(
        prepared,
        llm,
        search_providers,
        document_provider,
        max_results=max_results,
        prompt_config=prompt_config,
        tool_config=tool_config,
        cancel_requested=cancel_requested,
    )
    _raise_if_cancelled(cancel_requested)
    explanation = prepared.explanation or generate_explanation(llm, source_text, search_results, prompt_config)
    _raise_if_cancelled(cancel_requested)
    target_field = prepared.mapping.target_field
    if append:
        prepared.note[target_field] = append_field_content(prepared.note[target_field], explanation)
    else:
        prepared.note[target_field] = explanation
    col.update_note(prepared.note)
    return NoteProcessResult(note_id=prepared.note.id, status="written")


def generate_remark_html(
    source_text: str,
    config: JsonDict,
    *,
    llm_client: LLMClient | None = None,
    search_providers: list[SearchProvider] | None = None,
    cancel_requested: CancelRequested | None = None,
) -> str:
    llm = llm_client or LLMClient(config)
    providers = build_search_providers(config) if search_providers is None else search_providers
    document_provider = LocalDocumentProvider.from_config(config)
    max_results = int(config["search"].get("max_results", 5))
    prompt_config = config["prompt"]
    tool_config = config.get("documents", {})

    search_results, explanation = collect_tool_results(
        llm,
        source_text,
        providers,
        document_provider,
        max_results=max_results,
        prompt_config=prompt_config,
        tool_config=tool_config,
        cancel_requested=cancel_requested,
    )
    _raise_if_cancelled(cancel_requested)
    return explanation or generate_explanation(llm, source_text, search_results, prompt_config)


def ensure_search_results(
    prepared: PreparedNote,
    llm: LLMClient,
    search_providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    cancel_requested: CancelRequested | None = None,
) -> list[SearchResult]:
    if prepared.tool_checked:
        return prepared.search_results or prepared.tool_results

    search_results, explanation = collect_tool_results(
        llm,
        prepared.source_text,
        search_providers,
        document_provider,
        max_results=max_results,
        prompt_config=prompt_config,
        tool_config=tool_config,
        cancel_requested=cancel_requested,
    )
    prepared.search_results = search_results
    prepared.tool_results = search_results
    prepared.tool_checked = True
    prepared.explanation = explanation
    return search_results


def collect_tool_results(
    llm: LLMClient,
    source_text: str,
    search_providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    cancel_requested: CancelRequested | None = None,
) -> tuple[list[SearchResult], str | None]:
    _raise_if_cancelled(cancel_requested)
    return run_tool_loop(
        llm,
        source_text,
        search_providers,
        document_provider,
        max_results=max_results,
        prompt_config=prompt_config,
        tool_config=tool_config,
        cancel_requested=cancel_requested,
    )


def run_tool_loop(
    llm: LLMClient,
    source_text: str,
    search_providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    cancel_requested: CancelRequested | None = None,
) -> tuple[list[SearchResult], str | None]:
    tool_results: list[SearchResult] = []
    tool_history: list[dict[str, object]] = []
    current_input = source_text
    for _round in range(int(tool_config.get("max_tool_rounds", 3))):
        _raise_if_cancelled(cancel_requested)
        content = llm.chat(
            [
                {"role": "system", "content": str(prompt_config["system"])},
                {
                    "role": "user",
                    "content": build_tool_prompt(prompt_config, current_input, tool_history, tool_config),
                },
            ],
            response_format={"type": "json_object"},
        )
        tool_request = _parse_tool_request(content)
        if tool_request is None:
            return tool_results, _content_to_final_html(content)
        tool_type = str(tool_request.get("tool", "")).strip()
        action = str(tool_request.get("action", "")).strip() or "search"
        query = str(tool_request.get("query", "")).strip()
        document = str(tool_request.get("document", "")).strip()
        if not tool_type:
            return tool_results, None
        if not query and not (tool_type == "local_documents" and action == "list_documents"):
            return tool_results, None

        if tool_type == "local_documents" and document_provider is not None:
            if action == "list_documents":
                hits = document_provider.list_documents(query, max_results=max_results)
            else:
                hits = document_provider.search(query, document=document or None, max_results=max_results)
                tool_results = merge_tool_results(tool_results, hits)
            history_item: dict[str, object] = {
                "tool": tool_type,
                "action": action,
                "query": query,
                "results": format_search_results(hits),
            }
            if document:
                history_item["document"] = document
            tool_history.append(history_item)
            current_input = source_text
            continue

        if tool_type in {"web_search", "search"} and search_providers:
            hits = run_searches(
                search_providers,
                [query],
                max_results=max_results,
                cancel_requested=cancel_requested,
            )
            tool_results = merge_tool_results(tool_results, hits)
            tool_history.append({"tool": tool_type, "query": query, "results": format_search_results(hits)})
            current_input = source_text
            continue

        return tool_results, None

    return tool_results, None


def build_tool_prompt(
    prompt_config: JsonDict,
    source_text: str,
    tool_history: list[dict[str, object]],
    tool_config: JsonDict,
) -> str:
    sections = [
        str(prompt_config["analysis_instruction"]),
        (
            "You may request one tool at a time by returning JSON: "
            "{\"tool\":\"local_documents|search\",\"action\":\"search|list_documents\","
            "\"query\":\"...\",\"document\":\"optional document filename or unique fragment\","
            "\"reason\":\"...\"}."
        ),
        (
            "For local_documents, use action=list_documents with a short keyword or empty query "
            "to find candidate filenames, then use action=search with query and document when a "
            "filename or unique filename fragment is known."
        ),
        "Use search for web search. Prefer explicit local document names instead of blind global document searches.",
        "Keep queries short and specific. Prefer reusing earlier query wording when possible.",
    ]
    if tool_history:
        sections.append("Tool history:")
        sections.append(json.dumps(tool_history, ensure_ascii=False))
    sections.append("Source text:")
    sections.append(source_text)
    return "\n\n".join(sections)


def _parse_tool_request(content: str) -> JsonDict | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if "tool" in parsed and ("query" in parsed or parsed.get("action") == "list_documents"):
        return parsed
    if "need_search" in parsed and parsed.get("need_search"):
        queries = parsed.get("queries", [])
        if isinstance(queries, list) and queries:
            first = next((query for query in queries if isinstance(query, str) and query.strip()), "")
            if first:
                return {"tool": "search", "query": first, "reason": parsed.get("reason", "")}
    return None


def _content_to_final_html(content: str) -> str | None:
    stripped = content.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(parsed, dict):
        html = parsed.get("html") or parsed.get("final") or parsed.get("answer")
        if isinstance(html, str) and html.strip():
            return html.strip()
        return None
    return stripped


def merge_tool_results(existing: list[SearchResult], new_results: list[SearchResult]) -> list[SearchResult]:
    return dedupe_results([*existing, *new_results], limit=50)


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


def generate_batch_explanations(
    llm: LLMClient,
    prepared_notes: list[PreparedNote],
    prompt_config: JsonDict,
) -> dict[int, str]:
    note_payload = [
        {
            "note_id": prepared.note.id,
            "fields": prepared.source_text,
            "search_results": format_search_results(prepared.search_results or prepared.tool_results)
            or "No search results were used.",
        }
        for prepared in prepared_notes
    ]
    user_content = "\n\n".join(
        [
            str(prompt_config["final_instruction"]),
            (
                "Return JSON only in this exact shape: "
                '{"results":[{"note_id":123,"html":"<p>...</p>"}]}. '
                "Return one result for every input note_id, keep note_id unchanged, "
                "do not add unknown note_id values, and make every html value non-empty."
            ),
            "Input notes:",
            json.dumps(note_payload, ensure_ascii=False),
        ]
    )
    content = llm.chat(
        [
            {"role": "system", "content": str(prompt_config["system"])},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
    )
    return parse_batch_explanations(content, [prepared.note.id for prepared in prepared_notes])


def parse_batch_explanations(content: str, expected_note_ids: list[int]) -> dict[int, str]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BatchGenerationError("batch LLM response was not valid JSON") from exc

    if not isinstance(parsed, dict):
        raise BatchGenerationError("batch LLM response must be a JSON object")

    results = parsed.get("results")
    if not isinstance(results, list):
        raise BatchGenerationError("batch LLM response must contain a results list")

    expected = set(expected_note_ids)
    explanations: dict[int, str] = {}
    for item in results:
        if not isinstance(item, dict):
            raise BatchGenerationError("each batch result must be an object")
        note_id = item.get("note_id")
        if not isinstance(note_id, int) or isinstance(note_id, bool):
            raise BatchGenerationError("batch result note_id must be an integer")
        if note_id not in expected:
            raise BatchGenerationError(f"batch response contained unknown note_id: {note_id}")
        if note_id in explanations:
            raise BatchGenerationError(f"batch response contained duplicate note_id: {note_id}")
        html = item.get("html")
        if not isinstance(html, str) or not html.strip():
            raise BatchGenerationError(f"batch response contained empty html for note_id: {note_id}")
        explanations[note_id] = html.strip()

    missing = expected - set(explanations)
    if missing:
        missing_ids = ", ".join(str(note_id) for note_id in sorted(missing))
        raise BatchGenerationError(f"batch response missing note_id: {missing_ids}")

    return explanations


def run_searches(
    providers: list[SearchProvider],
    queries: list[str],
    *,
    max_results: int,
    cancel_requested: CancelRequested | None = None,
) -> list[SearchResult]:
    results: list[SearchResult] = []
    for query in queries:
        for provider in providers:
            _raise_if_cancelled(cancel_requested)
            results.extend(provider.search(query, max_results=max_results))
            _raise_if_cancelled(cancel_requested)
    return dedupe_results(results, limit=max_results)


def _process_prepared_notes_individually(
    col: CollectionLike,
    prepared_notes: list[PreparedNote],
    result_by_note_id: dict[int, NoteProcessResult],
    llm: LLMClient,
    providers: list[SearchProvider],
    document_provider: LocalDocumentProvider | None,
    *,
    max_results: int,
    prompt_config: JsonDict,
    tool_config: JsonDict,
    cancel_requested: CancelRequested | None = None,
) -> None:
    for prepared in prepared_notes:
        _raise_if_cancelled(cancel_requested)
        try:
            result = process_prepared_note(
                col,
                prepared,
                llm,
                providers,
                document_provider,
                max_results=max_results,
                prompt_config=prompt_config,
                tool_config=tool_config,
                cancel_requested=cancel_requested,
            )
        except ProcessingCancelled:
            raise
        except Exception as exc:
            result = NoteProcessResult(note_id=prepared.note.id, status="failed", message=str(exc))
        result_by_note_id[prepared.note.id] = result


def _build_batch_result(
    note_ids: list[int],
    result_by_note_id: dict[int, NoteProcessResult],
    progress: Callable[[int, int], None] | None,
    *,
    cancelled: bool = False,
) -> BatchResult:
    batch = BatchResult(cancelled=cancelled)
    total = len(note_ids)
    progress_count = 0
    for note_id in note_ids:
        result = result_by_note_id.get(note_id)
        if result is None:
            if cancelled:
                continue
            result = NoteProcessResult(note_id=note_id, status="failed", message="note was not processed")
        batch.add(result)
        progress_count += 1
        if progress:
            progress(progress_count, total)
    return batch


def _raise_if_cancelled(cancel_requested: CancelRequested | None) -> None:
    if _cancel_requested(cancel_requested):
        raise ProcessingCancelled("processing stopped by user")


def _cancel_requested(cancel_requested: CancelRequested | None) -> bool:
    return bool(cancel_requested and cancel_requested())


def split_prepared_notes(
    prepared_notes: list[PreparedNote],
    *,
    max_notes: int,
    max_chars: int,
) -> list[list[PreparedNote]]:
    chunks: list[list[PreparedNote]] = []
    current: list[PreparedNote] = []
    current_chars = 0

    for prepared in prepared_notes:
        note_chars = _prepared_note_chars(prepared)
        if current and (len(current) >= max_notes or current_chars + note_chars > max_chars):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(prepared)
        current_chars += note_chars

    if current:
        chunks.append(current)

    return chunks


def _prepared_note_chars(prepared: PreparedNote) -> int:
    return len(str(prepared.note.id)) + len(prepared.source_text) + len(
        format_search_results(prepared.search_results or prepared.tool_results)
    )


def _batch_enabled(batch_config: object) -> bool:
    return isinstance(batch_config, dict) and bool(batch_config.get("enabled", False))


def format_note_fields(note: NoteLike, mapping: FieldMapping) -> str:
    parts = []
    for field in mapping.source_fields:
        parts.append(f"{field}:\n{note[field]}")
    return "\n\n".join(parts)


def append_field_content(existing: str, addition: str) -> str:
    return f"{existing}{addition}"


def format_search_results(results: list[SearchResult]) -> str:
    lines = []
    for index, result in enumerate(results, start=1):
        content = result.content.strip()
        if result.provider == "local_documents":
            result_lines = [
                f"[{index}] {escape(result.title)}",
                f"Provider: {escape(result.provider)}",
                f"Document: {escape(result.title)}",
                f"Path: {escape(result.url)}",
                f"Snippet: {escape(content)}",
            ]
            if result.score is not None:
                result_lines.append(f"Score: {result.score:.2f}")
            lines.append(
                "\n".join(result_lines)
            )
            continue
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
