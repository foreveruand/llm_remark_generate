from __future__ import annotations

import time
from threading import Event
from typing import Any

from .config import ConfigError, merged_config, validate_config
from .models import BatchResult
from .processor import process_notes


ADDON_NAME = "LLM Remark Generator"


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
        from aqt.utils import askUser, showInfo, showWarning
        from aqt.operations import CollectionOp
    except Exception:
        return

    from .config_dialog import show_config_dialog

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

    gui_hooks.browser_menus_did_init.append(on_browser_menus_did_init)


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
