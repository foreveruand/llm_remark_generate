from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin import (
    REVIEWER_APPEND_COMMAND,
    _append_llm_result_to_note,
    _append_reviewer_button_html,
    _clear_note_in_flight,
    _current_reviewer_note_id,
    _format_batch_result,
    _is_reviewer_append_command,
    _mark_note_in_flight,
    _notify_operation_did_execute,
    _refresh_reviewer_card_if_current,
    _run_background_without_collection,
    _run_collection_op,
    _run_query_op_without_progress,
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


class FakeQueryOp:
    last_instance = None

    def __init__(self, *, parent, op, success) -> None:
        self.parent = parent
        self.op = op
        self.success = success
        self.failure_callback = None
        self.with_progress_called = False
        self.run_in_background_called = False
        FakeQueryOp.last_instance = self

    def failure(self, callback):
        self.failure_callback = callback
        return self

    def with_progress(self):
        self.with_progress_called = True
        return self

    def run_in_background(self) -> None:
        self.run_in_background_called = True


class FakeTaskman:
    def __init__(self) -> None:
        self.calls = []

    def run_in_background(self, task, on_done, *, uses_collection=True) -> None:
        self.calls.append((task, on_done, uses_collection))


class LegacyFakeTaskman:
    def __init__(self) -> None:
        self.calls = []

    def run_in_background(self, task, on_done) -> None:
        self.calls.append((task, on_done))


class FakeCard:
    def __init__(self, note_id: int) -> None:
        self.nid = note_id


class FakeReviewer:
    def __init__(self, note_id: int | None) -> None:
        self.card = FakeCard(note_id) if note_id is not None else None
        self.redraw_count = 0

    def _redraw_current_card(self) -> None:
        self.redraw_count += 1


class FakeNote:
    def __init__(self, note_id: int, fields: dict[str, str]) -> None:
        self.id = note_id
        self.fields = dict(fields)

    def keys(self):
        return self.fields.keys()

    def __getitem__(self, field: str) -> str:
        return self.fields[field]

    def __setitem__(self, field: str, value: str) -> None:
        self.fields[field] = value


class FakeCollection:
    def __init__(self, notes: list[FakeNote]) -> None:
        self.notes = {note.id: note for note in notes}
        self.updated_note_ids: list[int] = []
        self.op_made_changes_calls = []

    def get_note(self, note_id: int) -> FakeNote:
        return self.notes[note_id]

    def update_note(self, note: FakeNote) -> None:
        self.updated_note_ids.append(note.id)

    def op_made_changes(self, changes) -> bool:
        self.op_made_changes_calls.append(changes)
        return bool(getattr(changes, "note", False))


class FakeMainWindow:
    def __init__(self, col: FakeCollection) -> None:
        self.col = col
        self.undo_updates = 0

    def update_undo_actions(self) -> None:
        self.undo_updates += 1


class FakeGuiHooks:
    def __init__(self) -> None:
        self.operation_did_execute_calls = []
        self.state_did_reset_calls = 0

    def operation_did_execute(self, changes, handler) -> None:
        self.operation_did_execute_calls.append((changes, handler))

    def state_did_reset(self) -> None:
        self.state_did_reset_calls += 1


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

    def test_runs_query_op_without_progress(self) -> None:
        parent = object()

        _run_query_op_without_progress(
            FakeQueryOp,
            parent=parent,
            op=lambda col: col,
            success=lambda result: result,
            failure=lambda exc: exc,
        )

        op = FakeQueryOp.last_instance
        self.assertIs(parent, op.parent)
        self.assertFalse(op.with_progress_called)
        self.assertTrue(op.run_in_background_called)
        self.assertIsNotNone(op.failure_callback)

    def test_runs_background_task_without_collection_when_supported(self) -> None:
        taskman = FakeTaskman()

        _run_background_without_collection(taskman, lambda: "ok", lambda future: future)

        self.assertEqual(1, len(taskman.calls))
        self.assertFalse(taskman.calls[0][2])

    def test_runs_background_task_on_legacy_taskman(self) -> None:
        taskman = LegacyFakeTaskman()

        _run_background_without_collection(taskman, lambda: "ok", lambda future: future)

        self.assertEqual(1, len(taskman.calls))

    def test_batch_result_exposes_collection_op_changes(self) -> None:
        result = BatchResult()
        result.add(NoteProcessResult(note_id=1, status="failed", message="HTTP 400"))

        self.assertFalse(result.changes.note)
        self.assertEqual(0, result.count)

        result.add(NoteProcessResult(note_id=2, status="written"))

        self.assertTrue(result.changes.note)
        self.assertEqual(1, result.count)

    def test_notify_operation_did_execute_updates_undo_and_hooks(self) -> None:
        result = BatchResult()
        result.add(NoteProcessResult(note_id=14, status="written"))
        col = FakeCollection([])
        mw = FakeMainWindow(col)
        gui_hooks = FakeGuiHooks()

        _notify_operation_did_execute(mw, gui_hooks, result)

        self.assertEqual(1, mw.undo_updates)
        self.assertEqual(1, len(gui_hooks.operation_did_execute_calls))
        self.assertTrue(gui_hooks.operation_did_execute_calls[0][0].note)
        self.assertIsNone(gui_hooks.operation_did_execute_calls[0][1])
        self.assertEqual(1, len(col.op_made_changes_calls))
        self.assertEqual(1, gui_hooks.state_did_reset_calls)

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

    def test_append_llm_result_to_note_appends_to_latest_target_field(self) -> None:
        note = FakeNote(14, {"Remark": "<p>Existing.</p>"})
        col = FakeCollection([note])

        result = _append_llm_result_to_note(col, 14, "Remark", "<p>New.</p>")

        self.assertEqual(1, result.written)
        self.assertTrue(result.changes.note)
        self.assertEqual([14], col.updated_note_ids)
        self.assertEqual("<p>Existing.</p><p>New.</p>", note["Remark"])

    def test_append_llm_result_to_note_reports_missing_target_field(self) -> None:
        note = FakeNote(14, {"Other": ""})
        col = FakeCollection([note])

        result = _append_llm_result_to_note(col, 14, "Remark", "<p>New.</p>")

        self.assertEqual(0, result.written)
        self.assertEqual(1, result.failed)
        self.assertFalse(result.changes.note)
        self.assertEqual([], col.updated_note_ids)
        self.assertIn("missing field: Remark", result.details[0].message)

    def test_refresh_reviewer_card_only_when_same_note_is_current(self) -> None:
        reviewer = FakeReviewer(14)

        _refresh_reviewer_card_if_current(reviewer, 14)
        reviewer.card = FakeCard(15)
        _refresh_reviewer_card_if_current(reviewer, 14)

        self.assertEqual(1, reviewer.redraw_count)

    def test_note_in_flight_helpers_reject_duplicate_note(self) -> None:
        in_flight: set[int] = set()

        self.assertTrue(_mark_note_in_flight(in_flight, 14))
        self.assertFalse(_mark_note_in_flight(in_flight, 14))
        _clear_note_in_flight(in_flight, 14)

        self.assertTrue(_mark_note_in_flight(in_flight, 14))


if __name__ == "__main__":
    unittest.main()
