from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin import (
    REVIEWER_APPEND_COMMAND,
    _append_reviewer_button_html,
    _current_reviewer_note_id,
    _format_batch_result,
    _is_reviewer_append_command,
    _run_collection_op,
)
from ankiplugin.models import BatchResult, NoteProcessResult


class CollectionOpWithProgress:
    def __init__(self) -> None:
        self.with_progress_called = False
        self.run_in_background_called = False

    def with_progress(self):
        self.with_progress_called = True
        return self

    def run_in_background(self) -> None:
        self.run_in_background_called = True


class CollectionOpWithoutProgress:
    def __init__(self) -> None:
        self.run_in_background_called = False

    def run_in_background(self) -> None:
        self.run_in_background_called = True


class FakeCard:
    def __init__(self, note_id: int) -> None:
        self.nid = note_id


class FakeReviewer:
    def __init__(self, note_id: int | None) -> None:
        self.card = FakeCard(note_id) if note_id is not None else None


class AnkiOpsTest(unittest.TestCase):
    def test_runs_collection_op_with_legacy_progress_method(self) -> None:
        op = CollectionOpWithProgress()

        _run_collection_op(op)

        self.assertTrue(op.with_progress_called)
        self.assertTrue(op.run_in_background_called)

    def test_runs_collection_op_without_progress_method(self) -> None:
        op = CollectionOpWithoutProgress()

        _run_collection_op(op)

        self.assertTrue(op.run_in_background_called)

    def test_runs_collection_op_without_default_progress_when_requested(self) -> None:
        op = CollectionOpWithProgress()

        _run_collection_op(op, with_progress=False)

        self.assertFalse(op.with_progress_called)
        self.assertTrue(op.run_in_background_called)

    def test_batch_result_exposes_collection_op_changes(self) -> None:
        result = BatchResult()
        result.add(NoteProcessResult(note_id=1, status="failed", message="HTTP 400"))

        self.assertFalse(result.changes.note)
        self.assertEqual(0, result.count)

        result.add(NoteProcessResult(note_id=2, status="written"))

        self.assertTrue(result.changes.note)
        self.assertEqual(1, result.count)

    def test_format_batch_result_marks_cancelled_run_as_stopped(self) -> None:
        result = BatchResult(cancelled=True)

        message = _format_batch_result(result)

        self.assertIn("LLM Remark Generator stopped.", message)
        self.assertIn("Stopped before all selected notes were processed.", message)

    def test_reviewer_append_button_html_is_added_once(self) -> None:
        html = _append_reviewer_button_html("<div>bottom</div>")

        self.assertIn(REVIEWER_APPEND_COMMAND, html)
        self.assertEqual(html, _append_reviewer_button_html(html))

    def test_reviewer_append_command_and_note_id_helpers(self) -> None:
        self.assertTrue(_is_reviewer_append_command(REVIEWER_APPEND_COMMAND))
        self.assertFalse(_is_reviewer_append_command("other"))
        self.assertEqual(123, _current_reviewer_note_id(FakeReviewer(123)))
        self.assertIsNone(_current_reviewer_note_id(FakeReviewer(None)))


if __name__ == "__main__":
    unittest.main()
