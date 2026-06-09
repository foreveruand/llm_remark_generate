from __future__ import annotations

from copy import deepcopy
from typing import Any

from .config import ConfigError, merged_config, validate_config
from .models import JsonDict


def show_config_dialog(mw: Any, addon_module_name: str) -> None:
    from aqt.qt import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QPlainTextEdit,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
    from aqt.utils import showInfo, showWarning

    class ConfigDialog(QDialog):
        def __init__(self, config: JsonDict, parent: Any = None) -> None:
            super().__init__(parent)
            self._base_config = deepcopy(config)
            self.result_config: JsonDict | None = None

            self.setWindowTitle("LLM Remark Generator Configuration")
            self.resize(760, 720)

            root = QVBoxLayout(self)
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            content = QWidget(scroll)
            content_layout = QVBoxLayout(content)
            content_layout.addWidget(self._build_llm_group())
            content_layout.addWidget(self._build_search_group())
            content_layout.addWidget(self._build_batch_group())
            content_layout.addWidget(self._build_mappings_group())
            content_layout.addWidget(self._build_prompt_group())
            content_layout.addStretch(1)
            scroll.setWidget(content)
            root.addWidget(scroll)

            buttons = _standard_button(QDialogButtonBox, "Ok") | _standard_button(QDialogButtonBox, "Cancel")
            self.button_box = QDialogButtonBox(buttons, self)
            self.button_box.accepted.connect(self._on_accept)
            self.button_box.rejected.connect(self.reject)
            root.addWidget(self.button_box)

        def _build_llm_group(self) -> Any:
            llm = self._base_config["llm"]
            group = QGroupBox("LLM", self)
            form = QFormLayout(group)

            self.llm_base_url = QLineEdit(str(llm.get("base_url", "")), group)
            self.llm_api_key = QLineEdit(str(llm.get("api_key", "")), group)
            self.llm_api_key.setEchoMode(_enum_value(QLineEdit, "EchoMode", "Password"))
            self.llm_model = QLineEdit(str(llm.get("model", "")), group)
            self.llm_temperature = QDoubleSpinBox(group)
            self.llm_temperature.setRange(0.0, 2.0)
            self.llm_temperature.setDecimals(2)
            self.llm_temperature.setSingleStep(0.1)
            self.llm_temperature.setValue(float(llm.get("temperature", 0.2)))
            self.llm_timeout = QSpinBox(group)
            self.llm_timeout.setRange(1, 3600)
            self.llm_timeout.setValue(int(llm.get("timeout_seconds", 60)))

            form.addRow("Base URL", self.llm_base_url)
            form.addRow("API key", self.llm_api_key)
            form.addRow("Model", self.llm_model)
            form.addRow("Temperature", self.llm_temperature)
            form.addRow("Timeout seconds", self.llm_timeout)
            return group

        def _build_search_group(self) -> Any:
            search = self._base_config["search"]
            providers = search.get("providers", [])
            group = QGroupBox("Search", self)
            form = QFormLayout(group)

            self.search_enabled = QCheckBox("Enable search", group)
            self.search_enabled.setChecked(bool(search.get("enabled", True)))
            self.search_tavily_provider = QCheckBox("Tavily", group)
            self.search_tavily_provider.setChecked("tavily" in providers)
            self.search_brave_provider = QCheckBox("Brave", group)
            self.search_brave_provider.setChecked("brave" in providers)
            provider_row = QWidget(group)
            provider_layout = QHBoxLayout(provider_row)
            provider_layout.setContentsMargins(0, 0, 0, 0)
            provider_layout.addWidget(self.search_tavily_provider)
            provider_layout.addWidget(self.search_brave_provider)
            provider_layout.addStretch(1)

            self.search_tavily_api_key = QLineEdit(str(search.get("tavily_api_key", "")), group)
            self.search_tavily_api_key.setEchoMode(_enum_value(QLineEdit, "EchoMode", "Password"))
            self.search_brave_api_key = QLineEdit(str(search.get("brave_api_key", "")), group)
            self.search_brave_api_key.setEchoMode(_enum_value(QLineEdit, "EchoMode", "Password"))
            self.search_max_results = QSpinBox(group)
            self.search_max_results.setRange(1, 20)
            self.search_max_results.setValue(int(search.get("max_results", 5)))
            self.search_timeout = QSpinBox(group)
            self.search_timeout.setRange(1, 3600)
            self.search_timeout.setValue(int(search.get("timeout_seconds", 20)))

            form.addRow(self.search_enabled)
            form.addRow("Providers", provider_row)
            form.addRow("Tavily API key", self.search_tavily_api_key)
            form.addRow("Brave API key", self.search_brave_api_key)
            form.addRow("Max results", self.search_max_results)
            form.addRow("Timeout seconds", self.search_timeout)
            return group

        def _build_batch_group(self) -> Any:
            batch = self._base_config["batch"]
            group = QGroupBox("Batch generation", self)
            form = QFormLayout(group)

            self.batch_enabled = QCheckBox("Combine final generation requests", group)
            self.batch_enabled.setChecked(bool(batch.get("enabled", False)))
            self.batch_max_notes = QSpinBox(group)
            self.batch_max_notes.setRange(1, 100)
            self.batch_max_notes.setValue(int(batch.get("max_notes_per_request", 10)))
            self.batch_max_chars = QSpinBox(group)
            self.batch_max_chars.setRange(1000, 500000)
            self.batch_max_chars.setSingleStep(1000)
            self.batch_max_chars.setValue(int(batch.get("max_chars_per_request", 30000)))
            self.batch_fallback = QCheckBox("Fallback to single-note generation on batch failure", group)
            self.batch_fallback.setChecked(bool(batch.get("fallback_to_single_on_error", True)))

            form.addRow(self.batch_enabled)
            form.addRow("Max notes per request", self.batch_max_notes)
            form.addRow("Max chars per request", self.batch_max_chars)
            form.addRow(self.batch_fallback)
            return group

        def _build_mappings_group(self) -> Any:
            group = QGroupBox("Field mappings", self)
            layout = QVBoxLayout(group)
            layout.addWidget(QLabel("Source fields are comma-separated.", group))

            self.mappings_table = QTableWidget(0, 3, group)
            self.mappings_table.setHorizontalHeaderLabels(["Note type", "Source fields", "Target field"])
            self.mappings_table.horizontalHeader().setSectionResizeMode(_enum_value(QHeaderView, "ResizeMode", "Stretch"))
            layout.addWidget(self.mappings_table)

            for note_type, mapping in self._base_config.get("mappings", {}).items():
                if isinstance(mapping, dict):
                    source_fields = mapping.get("source_fields", [])
                    target_field = mapping.get("target_field", "")
                    self._add_mapping_row(note_type, ", ".join(str(field) for field in source_fields), str(target_field))

            button_row = QWidget(group)
            button_layout = QHBoxLayout(button_row)
            button_layout.setContentsMargins(0, 0, 0, 0)
            add_button = QPushButton("Add", button_row)
            remove_button = QPushButton("Remove selected", button_row)
            add_button.clicked.connect(lambda: self._add_mapping_row("", "", ""))
            remove_button.clicked.connect(self._remove_selected_mapping_rows)
            button_layout.addWidget(add_button)
            button_layout.addWidget(remove_button)
            button_layout.addStretch(1)
            layout.addWidget(button_row)
            return group

        def _build_prompt_group(self) -> Any:
            prompt = self._base_config["prompt"]
            group = QGroupBox("Advanced prompt", self)
            form = QFormLayout(group)

            self.prompt_system = QPlainTextEdit(str(prompt.get("system", "")), group)
            self.prompt_analysis = QPlainTextEdit(str(prompt.get("analysis_instruction", "")), group)
            self.prompt_final = QPlainTextEdit(str(prompt.get("final_instruction", "")), group)
            for editor in (self.prompt_system, self.prompt_analysis, self.prompt_final):
                editor.setMinimumHeight(80)

            form.addRow("System", self.prompt_system)
            form.addRow("Search decision", self.prompt_analysis)
            form.addRow("Final explanation", self.prompt_final)
            return group

        def _add_mapping_row(self, note_type: str, source_fields: str, target_field: str) -> None:
            row = self.mappings_table.rowCount()
            self.mappings_table.insertRow(row)
            self.mappings_table.setItem(row, 0, QTableWidgetItem(note_type))
            self.mappings_table.setItem(row, 1, QTableWidgetItem(source_fields))
            self.mappings_table.setItem(row, 2, QTableWidgetItem(target_field))

        def _remove_selected_mapping_rows(self) -> None:
            rows = {index.row() for index in self.mappings_table.selectedIndexes()}
            if not rows and self.mappings_table.currentRow() >= 0:
                rows.add(self.mappings_table.currentRow())
            for row in sorted(rows, reverse=True):
                self.mappings_table.removeRow(row)

        def _on_accept(self) -> None:
            try:
                config = self._config_from_form()
                validate_config(config)
            except (ConfigError, ValueError) as exc:
                showWarning(f"Configuration error:\n\n{exc}")
                return
            self.result_config = config
            self.accept()

        def _config_from_form(self) -> JsonDict:
            config = deepcopy(self._base_config)
            config["llm"] = {
                **config["llm"],
                "base_url": self.llm_base_url.text().strip(),
                "api_key": self.llm_api_key.text().strip(),
                "model": self.llm_model.text().strip(),
                "temperature": self.llm_temperature.value(),
                "timeout_seconds": self.llm_timeout.value(),
            }

            providers = []
            if self.search_tavily_provider.isChecked():
                providers.append("tavily")
            if self.search_brave_provider.isChecked():
                providers.append("brave")
            config["search"] = {
                **config["search"],
                "enabled": self.search_enabled.isChecked(),
                "providers": providers,
                "tavily_api_key": self.search_tavily_api_key.text().strip(),
                "brave_api_key": self.search_brave_api_key.text().strip(),
                "max_results": self.search_max_results.value(),
                "timeout_seconds": self.search_timeout.value(),
            }

            config["batch"] = {
                **config["batch"],
                "enabled": self.batch_enabled.isChecked(),
                "max_notes_per_request": self.batch_max_notes.value(),
                "max_chars_per_request": self.batch_max_chars.value(),
                "fallback_to_single_on_error": self.batch_fallback.isChecked(),
            }

            config["mappings"] = self._mappings_from_table()
            config["prompt"] = {
                **config["prompt"],
                "system": self.prompt_system.toPlainText().strip(),
                "analysis_instruction": self.prompt_analysis.toPlainText().strip(),
                "final_instruction": self.prompt_final.toPlainText().strip(),
            }
            return config

        def _mappings_from_table(self) -> JsonDict:
            mappings: JsonDict = {}
            for row in range(self.mappings_table.rowCount()):
                note_type = _table_text(self.mappings_table, row, 0)
                source_text = _table_text(self.mappings_table, row, 1)
                target_field = _table_text(self.mappings_table, row, 2)
                if not note_type and not source_text and not target_field:
                    continue
                if note_type in mappings:
                    raise ConfigError(f"duplicate mapping for {note_type!r}")
                mappings[note_type] = {
                    "source_fields": [field.strip() for field in source_text.split(",") if field.strip()],
                    "target_field": target_field,
                }
            return mappings

    config = merged_config(mw.addonManager.getConfig(addon_module_name))
    dialog = ConfigDialog(config, mw)
    if _exec_dialog(dialog) == _accepted_code(QDialog) and dialog.result_config is not None:
        mw.addonManager.writeConfig(addon_module_name, dialog.result_config)
        showInfo("LLM Remark Generator configuration saved.")


def _table_text(table: Any, row: int, column: int) -> str:
    item = table.item(row, column)
    if item is None:
        return ""
    return item.text().strip()


def _exec_dialog(dialog: Any) -> int:
    exec_method = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return _enum_int(exec_method())


def _accepted_code(qdialog: Any) -> int:
    return _enum_int(_enum_value(qdialog, "DialogCode", "Accepted"))


def _standard_button(button_box: Any, name: str) -> Any:
    return _enum_value(button_box, "StandardButton", name)


def _enum_value(owner: Any, enum_name: str, value_name: str) -> Any:
    enum_owner = getattr(owner, enum_name, owner)
    return getattr(enum_owner, value_name)


def _enum_int(value: Any) -> int:
    return int(getattr(value, "value", value))
