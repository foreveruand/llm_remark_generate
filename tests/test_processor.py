from __future__ import annotations

import unittest
import sys
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin.config import merged_config
from ankiplugin.models import SearchResult
from ankiplugin.processor import process_notes


class FakeNote:
    def __init__(self, note_id: int, note_type_name: str, fields: dict[str, str]) -> None:
        self.id = note_id
        self._note_type_name = note_type_name
        self.fields = dict(fields)

    def note_type(self) -> dict[str, str]:
        return {"name": self._note_type_name}

    def keys(self) -> list[str]:
        return list(self.fields.keys())

    def __getitem__(self, field: str) -> str:
        return self.fields[field]

    def __setitem__(self, field: str, value: str) -> None:
        self.fields[field] = value


class FakeCollection:
    def __init__(self, notes: list[FakeNote]) -> None:
        self.notes = {note.id: note for note in notes}
        self.updated_note_ids: list[int] = []

    def get_note(self, note_id: int) -> FakeNote:
        return self.notes[note_id]

    def update_note(self, note: FakeNote) -> None:
        self.updated_note_ids.append(note.id)


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages: list[dict[str, str]], *, response_format: dict[str, Any] | None = None) -> str:
        self.calls.append({"messages": messages, "response_format": response_format})
        return self.responses.pop(0)


class FakeSearchProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, max_results: int) -> list[SearchResult]:
        self.calls.append((query, max_results))
        return [
            SearchResult(
                title="France facts",
                url="https://example.test/france",
                content="Paris is the capital of France.",
                provider=self.name,
            )
        ]


def test_config() -> dict[str, Any]:
    return merged_config(
        {
            "llm": {"api_key": "llm-key"},
            "search": {"enabled": False, "providers": [], "max_results": 2},
            "mappings": {
                "Choice": {
                    "source_fields": ["Question", "Options", "Answer"],
                    "target_field": "Remark",
                }
            },
        }
    )


class ProcessorTest(unittest.TestCase):
    def test_skips_existing_target_before_llm_or_search_calls(self) -> None:
        note = FakeNote(
            1,
            "Choice",
            {
                "Question": "What is the capital of France?",
                "Options": "A. Paris\nB. Rome",
                "Answer": "A",
                "Remark": "Already explained",
            },
        )
        col = FakeCollection([note])
        llm = FakeLLM([])
        search = FakeSearchProvider()

        result = process_notes(col, [1], test_config(), llm_client=llm, search_providers=[search])

        self.assertEqual(1, result.skipped_existing)
        self.assertEqual(0, result.written)
        self.assertEqual([], llm.calls)
        self.assertEqual([], search.calls)
        self.assertEqual([], col.updated_note_ids)

    def test_generates_explanation_with_llm_selected_search(self) -> None:
        note = FakeNote(
            2,
            "Choice",
            {
                "Question": "What is the capital of France?",
                "Options": "A. Paris\nB. Rome",
                "Answer": "A",
                "Remark": "",
            },
        )
        col = FakeCollection([note])
        llm = FakeLLM(
            [
                '{"need_search": true, "queries": ["capital of France"], "reason": "verify fact"}',
                "<p>Paris is correct because it is the capital of France.</p>",
            ]
        )
        search = FakeSearchProvider()

        result = process_notes(col, [2], test_config(), llm_client=llm, search_providers=[search])

        self.assertEqual(1, result.written)
        self.assertEqual([2], col.updated_note_ids)
        self.assertEqual("<p>Paris is correct because it is the capital of France.</p>", note["Remark"])
        self.assertEqual([("capital of France", 2)], search.calls)
        self.assertEqual(2, len(llm.calls))
        self.assertEqual({"type": "json_object"}, llm.calls[0]["response_format"])
        final_prompt = llm.calls[1]["messages"][1]["content"]
        self.assertIn("Question:\nWhat is the capital of France?", final_prompt)
        self.assertIn("Options:\nA. Paris\nB. Rome", final_prompt)
        self.assertIn("Answer:\nA", final_prompt)
        self.assertIn("France facts", final_prompt)

    def test_invalid_search_decision_falls_back_to_no_search(self) -> None:
        note = FakeNote(
            3,
            "Choice",
            {
                "Question": "2 + 2 = ?",
                "Options": "A. 3\nB. 4",
                "Answer": "B",
                "Remark": "",
            },
        )
        col = FakeCollection([note])
        llm = FakeLLM(["not json", "<p>Four is correct.</p>"])
        search = FakeSearchProvider()

        result = process_notes(col, [3], test_config(), llm_client=llm, search_providers=[search])

        self.assertEqual(1, result.written)
        self.assertEqual("<p>Four is correct.</p>", note["Remark"])
        self.assertEqual([], search.calls)


if __name__ == "__main__":
    unittest.main()
