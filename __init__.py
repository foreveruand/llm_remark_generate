from __future__ import annotations

import time
from typing import Any

from .config import ConfigError, merged_config, validate_config
from .models import BatchResult
from .processor import process_notes


ADDON_NAME = "LLM Remark Generator"


def _register() -> None:
    try:
        from aqt import gui_hooks, mw
        from aqt.qt import QAction, qconnect
        from aqt.utils import askUser, showInfo, showWarning
        from aqt.operations import CollectionOp
    except Exception:
        return

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

        progress = _progress_callback(len(note_ids))
        op = CollectionOp(
            parent=browser,
            op=lambda col: process_notes(col, note_ids, config, progress=progress),
        )
        op.success(lambda result: showInfo(_format_batch_result(result)))
        op.failure(lambda exc: showWarning(f"{ADDON_NAME} failed:\n\n{exc}"))
        _run_collection_op(op)

    def _progress_callback(total: int):
        last_update = 0.0

        def progress(current: int, _total: int) -> None:
            nonlocal last_update
            now = time.monotonic()
            if now - last_update < 0.2 and current != total:
                return
            last_update = now
            mw.taskman.run_on_main(
                lambda current=current: mw.progress.update(
                    label=f"Generating LLM explanations... {current}/{total}",
                    value=current,
                    max=total,
                )
            )

        return progress

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


def _run_collection_op(op: Any) -> None:
    with_progress = getattr(op, "with_progress", None)
    if callable(with_progress):
        op = with_progress()
    op.run_in_background()


def _format_batch_result(result: BatchResult) -> str:
    lines = [
        f"{ADDON_NAME} finished.",
        "",
        f"Processed: {result.processed}",
        f"Written: {result.written}",
        f"Skipped existing target field: {result.skipped_existing}",
        f"Skipped unmapped note type: {result.skipped_unmapped}",
        f"Failed: {result.failed}",
    ]

    failures = [item for item in result.details if item.status == "failed"]
    if failures:
        lines.extend(["", "First failures:"])
        for item in failures[:5]:
            lines.append(f"- Note {item.note_id}: {item.message}")
    return "\n".join(lines)


_register()
