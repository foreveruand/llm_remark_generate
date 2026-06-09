from __future__ import annotations

import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin.config import ConfigError, merged_config, parse_mappings, validate_config


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


if __name__ == "__main__":
    unittest.main()
