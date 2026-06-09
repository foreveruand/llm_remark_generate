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


def batch_test_config(*, fallback: bool = True) -> dict[str, Any]:
    config = test_config()
    config["batch"].update(
        {
            "enabled": True,
            "max_notes_per_request": 10,
            "max_chars_per_request": 30000,
            "fallback_to_single_on_error": fallback,
        }
    )
    return config


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

    def test_individual_processing_stops_after_current_note_when_cancelled(self) -> None:
        first = FakeNote(
            10,
            "Choice",
            {
                "Question": "2 + 2 = ?",
                "Options": "A. 3\nB. 4",
                "Answer": "B",
                "Remark": "",
            },
        )
        second = FakeNote(
            11,
            "Choice",
            {
                "Question": "3 + 3 = ?",
                "Options": "A. 6\nB. 7",
                "Answer": "A",
                "Remark": "",
            },
        )
        col = FakeCollection([first, second])
        llm = FakeLLM(
            [
                '{"need_search": false, "queries": [], "reason": "simple math"}',
                "<p>Four is correct.</p>",
            ]
        )
        stopped = False

        def progress(current: int, _total: int) -> None:
            nonlocal stopped
            if current == 1:
                stopped = True

        result = process_notes(
            col,
            [10, 11],
            test_config(),
            llm_client=llm,
            search_providers=[],
            progress=progress,
            cancel_requested=lambda: stopped,
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(1, result.processed)
        self.assertEqual(1, result.written)
        self.assertEqual([10], col.updated_note_ids)
        self.assertEqual("<p>Four is correct.</p>", first["Remark"])
        self.assertEqual("", second["Remark"])
        self.assertEqual(2, len(llm.calls))

    def test_batch_combines_final_generation_for_multiple_notes(self) -> None:
        first = FakeNote(
            4,
            "Choice",
            {
                "Question": "2 + 2 = ?",
                "Options": "A. 3\nB. 4",
                "Answer": "B",
                "Remark": "",
            },
        )
        second = FakeNote(
            5,
            "Choice",
            {
                "Question": "3 + 3 = ?",
                "Options": "A. 6\nB. 7",
                "Answer": "A",
                "Remark": "",
            },
        )
        col = FakeCollection([first, second])
        llm = FakeLLM(
            [
                '{"need_search": false, "queries": [], "reason": "simple math"}',
                '{"need_search": false, "queries": [], "reason": "simple math"}',
                (
                    '{"results":['
                    '{"note_id":4,"html":"<p>Four is correct.</p>"},'
                    '{"note_id":5,"html":"<p>Six is correct.</p>"}'
                    "]}"
                ),
            ]
        )
        search = FakeSearchProvider()

        result = process_notes(col, [4, 5], batch_test_config(), llm_client=llm, search_providers=[search])

        self.assertEqual(2, result.written)
        self.assertEqual([4, 5], col.updated_note_ids)
        self.assertEqual("<p>Four is correct.</p>", first["Remark"])
        self.assertEqual("<p>Six is correct.</p>", second["Remark"])
        self.assertEqual([], search.calls)
        self.assertEqual(3, len(llm.calls))
        self.assertEqual({"type": "json_object"}, llm.calls[2]["response_format"])
        batch_prompt = llm.calls[2]["messages"][1]["content"]
        self.assertIn('"note_id": 4', batch_prompt)
        self.assertIn('"note_id": 5', batch_prompt)

    def test_batch_processing_can_stop_before_final_generation(self) -> None:
        first = FakeNote(
            12,
            "Choice",
            {
                "Question": "2 + 2 = ?",
                "Options": "A. 3\nB. 4",
                "Answer": "B",
                "Remark": "",
            },
        )
        second = FakeNote(
            13,
            "Choice",
            {
                "Question": "3 + 3 = ?",
                "Options": "A. 6\nB. 7",
                "Answer": "A",
                "Remark": "",
            },
        )
        col = FakeCollection([first, second])
        llm = FakeLLM(['{"need_search": false, "queries": [], "reason": "simple math"}'])

        result = process_notes(
            col,
            [12, 13],
            batch_test_config(),
            llm_client=llm,
            search_providers=[],
            cancel_requested=lambda: len(llm.calls) >= 1,
        )

        self.assertTrue(result.cancelled)
        self.assertEqual(0, result.processed)
        self.assertEqual(0, result.written)
        self.assertEqual([], col.updated_note_ids)
        self.assertEqual("", first["Remark"])
        self.assertEqual("", second["Remark"])
        self.assertEqual(1, len(llm.calls))

    def test_batch_invalid_response_without_fallback_does_not_write_notes(self) -> None:
        first = FakeNote(
            6,
            "Choice",
            {
                "Question": "2 + 2 = ?",
                "Options": "A. 3\nB. 4",
                "Answer": "B",
                "Remark": "",
            },
        )
        second = FakeNote(
            7,
            "Choice",
            {
                "Question": "3 + 3 = ?",
                "Options": "A. 6\nB. 7",
                "Answer": "A",
                "Remark": "",
            },
        )
        col = FakeCollection([first, second])
        llm = FakeLLM(
            [
                '{"need_search": false, "queries": [], "reason": "simple math"}',
                '{"need_search": false, "queries": [], "reason": "simple math"}',
                '{"results":[{"note_id":6,"html":"<p>Four is correct.</p>"}]}',
            ]
        )

        result = process_notes(col, [6, 7], batch_test_config(fallback=False), llm_client=llm, search_providers=[])

        self.assertEqual(0, result.written)
        self.assertEqual(2, result.failed)
        self.assertEqual([], col.updated_note_ids)
        self.assertEqual("", first["Remark"])
        self.assertEqual("", second["Remark"])

    def test_batch_invalid_response_can_fallback_to_single_generation(self) -> None:
        first = FakeNote(
            8,
            "Choice",
            {
                "Question": "2 + 2 = ?",
                "Options": "A. 3\nB. 4",
                "Answer": "B",
                "Remark": "",
            },
        )
        second = FakeNote(
            9,
            "Choice",
            {
                "Question": "3 + 3 = ?",
                "Options": "A. 6\nB. 7",
                "Answer": "A",
                "Remark": "",
            },
        )
        col = FakeCollection([first, second])
        llm = FakeLLM(
            [
                '{"need_search": false, "queries": [], "reason": "simple math"}',
                '{"need_search": false, "queries": [], "reason": "simple math"}',
                "not json",
                "<p>Four is correct.</p>",
                "<p>Six is correct.</p>",
            ]
        )

        result = process_notes(col, [8, 9], batch_test_config(), llm_client=llm, search_providers=[])

        self.assertEqual(2, result.written)
        self.assertEqual([8, 9], col.updated_note_ids)
        self.assertEqual("<p>Four is correct.</p>", first["Remark"])
        self.assertEqual("<p>Six is correct.</p>", second["Remark"])
        self.assertEqual(5, len(llm.calls))
        self.assertIsNone(llm.calls[3]["response_format"])
        self.assertIsNone(llm.calls[4]["response_format"])


if __name__ == "__main__":
    unittest.main()
