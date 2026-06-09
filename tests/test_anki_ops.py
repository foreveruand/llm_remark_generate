from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin import _run_collection_op
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

    def test_batch_result_exposes_collection_op_changes(self) -> None:
        result = BatchResult()
        result.add(NoteProcessResult(note_id=1, status="failed", message="HTTP 400"))

        self.assertFalse(result.changes.note)
        self.assertEqual(0, result.count)

        result.add(NoteProcessResult(note_id=2, status="written"))

        self.assertTrue(result.changes.note)
        self.assertEqual(1, result.count)


if __name__ == "__main__":
    unittest.main()
