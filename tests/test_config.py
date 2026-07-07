from __future__ import annotations

import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_remark_generate.config import ConfigError, merged_config, parse_mappings, validate_config


class ConfigTest(unittest.TestCase):
    def test_parse_mappings_keeps_note_type_and_field_order(self) -> None:
        config = merged_config(
            {
                "mappings": {
                    "Choice": {
                        "source_fields": ["Question", "Options", "Answer"],
                        "target_field": "Remark",
                    }
                }
            }
        )

        mappings = parse_mappings(config)

        self.assertEqual(["Question", "Options", "Answer"], mappings["Choice"].source_fields)
        self.assertEqual("Remark", mappings["Choice"].target_field)

    def test_validate_rejects_enabled_provider_without_key(self) -> None:
        config = merged_config(
            {
                "llm": {"api_key": "llm-key"},
                "search": {"enabled": True, "providers": ["brave"], "brave_api_key": ""},
                "mappings": {
                    "Choice": {
                        "source_fields": ["Question"],
                        "target_field": "Remark",
                    }
                },
            }
        )

        with self.assertRaisesRegex(ConfigError, "brave_api_key"):
            validate_config(config)

    def test_validate_allows_search_disabled_without_search_keys(self) -> None:
        config = merged_config(
            {
                "llm": {"api_key": "llm-key"},
                "search": {"enabled": False, "providers": []},
                "mappings": {
                    "Choice": {
                        "source_fields": ["Question"],
                        "target_field": "Remark",
                    }
                },
            }
        )

        validate_config(config)

    def test_default_batch_config_is_disabled(self) -> None:
        config = merged_config({"llm": {"api_key": "llm-key"}, "search": {"enabled": False}})

        self.assertFalse(config["batch"]["enabled"])
        self.assertEqual(10, config["batch"]["max_notes_per_request"])
        self.assertEqual(30000, config["batch"]["max_chars_per_request"])
        self.assertTrue(config["batch"]["fallback_to_single_on_error"])

    def test_default_documents_config_is_disabled(self) -> None:
        config = merged_config({"llm": {"api_key": "llm-key"}, "search": {"enabled": False}})

        self.assertFalse(config["documents"]["enabled"])
        self.assertEqual("", config["documents"]["directory"])
        self.assertEqual("", config["documents"]["converter_path"])
        self.assertEqual(5, config["documents"]["max_results"])

    def test_validate_requires_document_directory_when_enabled(self) -> None:
        config = merged_config(
            {
                "llm": {"api_key": "llm-key"},
                "search": {"enabled": False},
                "documents": {"enabled": True, "directory": ""},
            }
        )

        with self.assertRaisesRegex(ConfigError, "documents.directory"):
            validate_config(config)

    def test_validate_allows_enabled_documents_without_converter_path(self) -> None:
        config = merged_config(
            {
                "llm": {"api_key": "llm-key"},
                "search": {"enabled": False},
                "documents": {"enabled": True, "directory": "/tmp/docs", "converter_path": ""},
            }
        )

        validate_config(config)

    def test_default_llm_api_type_is_completion(self) -> None:
        config = merged_config({"llm": {"api_key": "llm-key"}, "search": {"enabled": False}})

        self.assertEqual("completion", config["llm"]["api_type"])

    def test_validate_accepts_supported_llm_api_types(self) -> None:
        for api_type in ("completion", "response"):
            with self.subTest(api_type=api_type):
                config = merged_config(
                    {
                        "llm": {"api_key": "llm-key", "api_type": api_type},
                        "search": {"enabled": False},
                    }
                )

                validate_config(config)

    def test_validate_accepts_missing_llm_api_type(self) -> None:
        config = merged_config({"llm": {"api_key": "llm-key"}, "search": {"enabled": False}})
        del config["llm"]["api_type"]

        validate_config(config)

    def test_validate_rejects_invalid_llm_api_type(self) -> None:
        config = merged_config(
            {
                "llm": {"api_key": "llm-key", "api_type": "Response"},
                "search": {"enabled": False},
            }
        )

        with self.assertRaisesRegex(ConfigError, "llm.api_type"):
            validate_config(config)

    def test_validate_rejects_invalid_batch_threshold(self) -> None:
        config = merged_config(
            {
                "llm": {"api_key": "llm-key"},
                "search": {"enabled": False},
                "batch": {"max_notes_per_request": 0},
            }
        )

        with self.assertRaisesRegex(ConfigError, "batch.max_notes_per_request"):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
