from __future__ import annotations

import time
from threading import Event
from typing import Any

from .config import ConfigError, merged_config, parse_mappings, validate_config
from .models import BatchResult, NoteProcessResult
from .processor import append_field_content, generate_remark_html, prepare_note, process_notes


ADDON_NAME = "LLM Remark Generator"
REVIEWER_APPEND_COMMAND = "llm_remark_generator_append_current"
REVIEWER_APPEND_BUTTON_HTML = (
    '<span style="padding-left: 8px;">'
    f'<button onclick="pycmd(\'{REVIEWER_APPEND_COMMAND}\')" '
    'title="Append a new LLM result to the configured target field">'
    "Append LLM Remark"
    "</button></span>"
)


class _CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


def _register() -> None:
    try:
        from aqt import gui_hooks, mw
        from aqt.qt import (
            QAction,
            QDialog,
            QHBoxLayout,
            QLabel,
            QProgressBar,
            QPushButton,
            QVBoxLayout,
            qconnect,
        )
        from aqt.utils import askUser, showInfo, showWarning, tooltip
        from aqt.operations import CollectionOp, QueryOp
    except Exception:
        return

    from .config_dialog import show_config_dialog

    try:
        from aqt.reviewer import Reviewer
    except Exception:
        Reviewer = None

    class BatchProgressDialog(QDialog):
        def __init__(self, parent: Any, total: int, cancel_token: _CancellationToken) -> None:
            super().__init__(parent)
            self._total = total
            self._cancel_token = cancel_token
            self._finished = False

            self.setWindowTitle(ADDON_NAME)
            self.setMinimumWidth(420)

            root = QVBoxLayout(self)
            self.label = QLabel(self._label(0), self)
            self.progress_bar = QProgressBar(self)
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(0)
            root.addWidget(self.label)
            root.addWidget(self.progress_bar)

            button_row = QHBoxLayout()
            button_row.addStretch(1)
            self.stop_button = QPushButton("Stop", self)
            qconnect(self.stop_button.clicked, lambda *_args: self.request_stop())
            button_row.addWidget(self.stop_button)
            root.addLayout(button_row)

        def request_stop(self) -> None:
            self._cancel_token.cancel()
            self.stop_button.setEnabled(False)
            self.stop_button.setText("Stopping...")
            self.label.setText("Stopping after the current request returns...")

        def update_progress(self, current: int, total: int) -> None:
            if self._finished:
                return
            self._total = total
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            if not self._cancel_token.is_cancelled():
                self.label.setText(self._label(current))

        def finish(self) -> None:
            self._finished = True
            self.close()
            self.deleteLater()

        def closeEvent(self, event: Any) -> None:
            if self._finished:
                super().closeEvent(event)
                return
            self.request_stop()
            event.ignore()

        def _label(self, current: int) -> str:
            return f"Generating LLM explanations... {current}/{self._total}"

    set_config_action = getattr(mw.addonManager, "setConfigAction", None)
    if callable(set_config_action):
        set_config_action(__name__, lambda *_args: show_config_dialog(mw, __name__))

    def on_browser_menus_did_init(browser: Any) -> None:
        action = QAction("Generate LLM Remark", browser)
        qconnect(action.triggered, lambda: _run_from_browser(browser))

        menu = getattr(browser.form, "menuNotes", None) or getattr(browser.form, "menuEdit", None)
        if menu is not None:
            menu.addSeparator()
            menu.addAction(action)

    def _run_from_browser(browser: Any) -> None:
        note_ids = _selected_note_ids(browser)
        if not note_ids:
            showInfo("No notes selected.")
            return

        config = merged_config(mw.addonManager.getConfig(__name__))
        try:
            validate_config(config)
        except ConfigError as exc:
            showWarning(f"{ADDON_NAME} configuration error:\n\n{exc}")
            return

        if not askUser(
            f"Generate explanations for {len(note_ids)} selected note(s)?\n\n"
            "Notes whose target field already has content will be skipped."
        ):
            return

        cancel_token = _CancellationToken()
        progress_dialog = BatchProgressDialog(browser, len(note_ids), cancel_token)
        progress = _progress_callback(len(note_ids), progress_dialog)
        op = CollectionOp(
            parent=browser,
            op=lambda col: process_notes(
                col,
                note_ids,
                config,
                progress=progress,
                cancel_requested=cancel_token.is_cancelled,
            ),
        )
        op.success(lambda result: _finish_success(progress_dialog, result))
        op.failure(lambda exc: _finish_failure(progress_dialog, exc))
        progress_dialog.show()
        _run_collection_op(op, with_progress=False)

    reviewer_append_in_flight: set[int] = set()

    def _run_from_reviewer(reviewer: Any) -> None:
        note_id = _current_reviewer_note_id(reviewer)
        if note_id is None:
            showInfo("No current review card.")
            return

        config = merged_config(mw.addonManager.getConfig(__name__))
        try:
            validate_config(config)
        except ConfigError as exc:
            showWarning(f"{ADDON_NAME} configuration error:\n\n{exc}")
            return

        parent = getattr(reviewer, "mw", mw)
        taskman = getattr(mw, "taskman", None)
        run_in_background = getattr(taskman, "run_in_background", None)
        if not callable(run_in_background):
            showWarning("Anki background task manager is not available.")
            return
        if not _mark_note_in_flight(reviewer_append_in_flight, note_id):
            tooltip("LLM remark generation is already running for this note.")
            return

        try:
            note = mw.col.get_note(note_id)
            prepared_or_result = prepare_note(note, parse_mappings(config), skip_existing=False)
        except Exception as exc:
            _clear_note_in_flight(reviewer_append_in_flight, note_id)
            tooltip(f"{ADDON_NAME} did not append: {exc}")
            return

        if isinstance(prepared_or_result, NoteProcessResult):
            _clear_note_in_flight(reviewer_append_in_flight, note_id)
            message = prepared_or_result.message or "note is not configured for LLM remarks"
            tooltip(f"{ADDON_NAME} did not append: {message}")
            return

        source_text = prepared_or_result.source_text
        target_field = prepared_or_result.mapping.target_field
        tooltip("Generating LLM remark in background...")

        def generate() -> str:
            return generate_remark_html(source_text, config)

        def generated(future: Any) -> None:
            try:
                html = future.result()
            except Exception as exc:
                _clear_note_in_flight(reviewer_append_in_flight, note_id)
                tooltip(f"{ADDON_NAME} failed: {exc}")
                return

            def write_succeeded(result: BatchResult) -> None:
                try:
                    _finish_reviewer_append_success(
                        reviewer_append_in_flight, note_id, reviewer, result
                    )
                finally:
                    _notify_operation_did_execute(mw, gui_hooks, result)

            try:
                _run_query_op_without_progress(
                    QueryOp,
                    parent=parent,
                    op=lambda col: _append_llm_result_to_note(col, note_id, target_field, html),
                    success=write_succeeded,
                    failure=lambda exc: _finish_reviewer_append_failure(
                        reviewer_append_in_flight, note_id, exc
                    ),
                )
            except Exception as exc:
                _clear_note_in_flight(reviewer_append_in_flight, note_id)
                tooltip(f"{ADDON_NAME} failed: {exc}")

        try:
            _run_background_without_collection(taskman, generate, generated)
        except Exception as exc:
            _clear_note_in_flight(reviewer_append_in_flight, note_id)
            tooltip(f"{ADDON_NAME} failed: {exc}")

    def _progress_callback(total: int, progress_dialog: Any):
        last_update = 0.0

        def progress(current: int, _total: int) -> None:
            nonlocal last_update
            now = time.monotonic()
            if now - last_update < 0.2 and current != total:
                return
            last_update = now
            mw.taskman.run_on_main(
                lambda current=current: progress_dialog.update_progress(current, total)
            )

        return progress

    def _finish_success(progress_dialog: Any, result: BatchResult) -> None:
        progress_dialog.finish()
        showInfo(_format_batch_result(result))

    def _finish_failure(progress_dialog: Any, exc: Exception) -> None:
        progress_dialog.finish()
        showWarning(f"{ADDON_NAME} failed:\n\n{exc}")

    def _finish_reviewer_append_success(
        in_flight_note_ids: set[int],
        note_id: int,
        reviewer: Any,
        result: BatchResult,
    ) -> None:
        _clear_note_in_flight(in_flight_note_ids, note_id)
        if result.written:
            _refresh_reviewer_card_if_current(reviewer, note_id)
            tooltip("LLM result appended to target field.")
            return

        tooltip(f"{ADDON_NAME} did not append: {_reviewer_append_result_message(result)}")

    def _finish_reviewer_append_failure(
        in_flight_note_ids: set[int],
        note_id: int,
        exc: Exception,
    ) -> None:
        _clear_note_in_flight(in_flight_note_ids, note_id)
        tooltip(f"{ADDON_NAME} failed: {exc}")

    def _register_reviewer_append_button() -> None:
        if Reviewer is None or getattr(Reviewer, "_llm_remark_append_button_patched", False):
            return

        original_bottom_html = getattr(Reviewer, "_bottomHTML", None)
        original_link_handler = getattr(Reviewer, "_linkHandler", None)
        if not callable(original_bottom_html) or not callable(original_link_handler):
            return

        def bottom_html(self: Any) -> str:
            return _append_reviewer_button_html(original_bottom_html(self))

        def link_handler(self: Any, command: str) -> Any:
            if _is_reviewer_append_command(command):
                _run_from_reviewer(self)
                return None
            return original_link_handler(self, command)

        Reviewer._bottomHTML = bottom_html
        Reviewer._linkHandler = link_handler
        Reviewer._llm_remark_append_button_patched = True

    gui_hooks.browser_menus_did_init.append(on_browser_menus_did_init)
    _register_reviewer_append_button()


def _selected_note_ids(browser: Any) -> list[int]:
    if hasattr(browser, "selected_notes"):
        return [int(note_id) for note_id in browser.selected_notes()]

    if hasattr(browser, "selectedCards"):
        note_ids: list[int] = []
        seen: set[int] = set()
        for card_id in browser.selectedCards():
            note_id = int(browser.mw.col.get_card(card_id).nid)
            if note_id not in seen:
                seen.add(note_id)
                note_ids.append(note_id)
        return note_ids

    return []


def _run_collection_op(op: Any, *, with_progress: bool = True) -> None:
    op_with_progress = getattr(op, "with_progress", None)
    if with_progress and callable(op_with_progress):
        op = op_with_progress()
    op.run_in_background()


def _run_query_op_without_progress(
    query_op_type: Any,
    *,
    parent: Any,
    op: Any,
    success: Any,
    failure: Any,
) -> None:
    query_op = query_op_type(parent=parent, op=op, success=success)
    query_op.failure(failure)
    query_op.run_in_background()


def _run_background_without_collection(taskman: Any, task: Any, on_done: Any) -> None:
    run_in_background = taskman.run_in_background
    try:
        run_in_background(task, on_done, uses_collection=False)
    except TypeError:
        run_in_background(task, on_done)


def _notify_operation_did_execute(mw_obj: Any, gui_hooks_obj: Any, result: BatchResult) -> None:
    update_undo_actions = getattr(mw_obj, "update_undo_actions", None)
    if callable(update_undo_actions):
        update_undo_actions()

    operation_did_execute = getattr(gui_hooks_obj, "operation_did_execute", None)
    if callable(operation_did_execute):
        operation_did_execute(result.changes, None)

    col = getattr(mw_obj, "col", None)
    op_made_changes = getattr(col, "op_made_changes", None)
    state_did_reset = getattr(gui_hooks_obj, "state_did_reset", None)
    if callable(op_made_changes) and callable(state_did_reset) and op_made_changes(result.changes):
        state_did_reset()


def _append_llm_result_to_note(
    col: Any,
    note_id: int,
    target_field: str,
    html: str,
) -> BatchResult:
    result = BatchResult()
    try:
        note = col.get_note(note_id)
        if target_field not in set(note.keys()):
            result.add(
                NoteProcessResult(
                    note_id=note_id,
                    status="failed",
                    message=f"missing field: {target_field}",
                )
            )
            return result

        note[target_field] = append_field_content(note[target_field], html)
        col.update_note(note)
    except Exception as exc:
        result.add(NoteProcessResult(note_id=note_id, status="failed", message=str(exc)))
    else:
        result.add(NoteProcessResult(note_id=note_id, status="written"))
    return result


def _mark_note_in_flight(in_flight_note_ids: set[int], note_id: int) -> bool:
    if note_id in in_flight_note_ids:
        return False
    in_flight_note_ids.add(note_id)
    return True


def _clear_note_in_flight(in_flight_note_ids: set[int], note_id: int) -> None:
    in_flight_note_ids.discard(note_id)


def _reviewer_append_result_message(result: BatchResult) -> str:
    detail = result.details[0] if result.details else None
    if detail and detail.message:
        return detail.message
    if result.cancelled:
        return "generation stopped before writing"
    return "no result was written"


def _append_reviewer_button_html(html: str) -> str:
    if REVIEWER_APPEND_COMMAND in html:
        return html
    return f"{html}{REVIEWER_APPEND_BUTTON_HTML}"


def _is_reviewer_append_command(command: str) -> bool:
    return command == REVIEWER_APPEND_COMMAND


def _current_reviewer_note_id(reviewer: Any) -> int | None:
    card = getattr(reviewer, "card", None)
    note_id = getattr(card, "nid", None)
    if note_id is None:
        return None
    try:
        return int(note_id)
    except (TypeError, ValueError):
        return None


def _refresh_reviewer_card(reviewer: Any) -> None:
    redraw = getattr(reviewer, "_redraw_current_card", None)
    if callable(redraw):
        redraw()


def _refresh_reviewer_card_if_current(reviewer: Any, note_id: int) -> None:
    if _current_reviewer_note_id(reviewer) == note_id:
        _refresh_reviewer_card(reviewer)


def _format_batch_result(result: BatchResult) -> str:
    lines = [
        f"{ADDON_NAME} {'stopped' if result.cancelled else 'finished'}.",
        "",
        f"Processed: {result.processed}",
        f"Written: {result.written}",
        f"Skipped existing target field: {result.skipped_existing}",
        f"Skipped unmapped note type: {result.skipped_unmapped}",
        f"Failed: {result.failed}",
    ]
    if result.cancelled:
        lines.extend(["", "Stopped before all selected notes were processed."])

    failures = [item for item in result.details if item.status == "failed"]
    if failures:
        lines.extend(["", "First failures:"])
        for item in failures[:5]:
            lines.append(f"- Note {item.note_id}: {item.message}")
    return "\n".join(lines)


_register()
