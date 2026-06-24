from __future__ import annotations

import stat
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin.config import merged_config
from ankiplugin.documents import LocalDocumentProvider, document_settings, search_index


class DocumentsTest(unittest.TestCase):
    def test_plain_text_files_are_indexed_and_searched(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "guide.txt").write_text("Source fact about AVC control strategy.", encoding="utf-8")
            config = merged_config(
                {
                    "llm": {"api_key": "llm-key"},
                    "search": {"enabled": False},
                    "documents": {"enabled": True, "directory": str(root), "max_result_chars": 80},
                }
            )
            provider = LocalDocumentProvider.from_config(config)
            self.assertIsNotNone(provider)

            assert provider is not None
            provider.prepare()
            results = provider.search("AVC strategy", max_results=3)

            self.assertEqual(1, len(results))
            self.assertEqual("local_documents", results[0].provider)
            self.assertIn("guide.txt", results[0].url)
            self.assertIn("AVC", results[0].content)

    def test_manual_extraction_calls_converter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            index = root / ".llm_remark_index"
            converter = root / "fake_converter.py"
            converter.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env python3",
                        "from pathlib import Path",
                        "import argparse",
                        "parser = argparse.ArgumentParser()",
                        "parser.add_argument('--input')",
                        "parser.add_argument('--output')",
                        "args = parser.parse_args()",
                        "Path(args.output).mkdir(parents=True, exist_ok=True)",
                        "(Path(args.output) / 'converted.md').write_text('# Manual\\n\\nSource: manual.pdf\\n\\nD6.0 keyword', encoding='utf-8')",
                    ]
                ),
                encoding="utf-8",
            )
            converter.chmod(converter.stat().st_mode | stat.S_IXUSR)
            config = merged_config(
                {
                    "llm": {"api_key": "llm-key"},
                    "search": {"enabled": False},
                    "documents": {
                        "enabled": True,
                        "directory": str(root),
                        "converter_path": str(converter),
                    },
                }
            )

            provider = LocalDocumentProvider.from_config(config)
            assert provider is not None
            provider.extract_documents()

            self.assertTrue((index / "converted.md").exists())
            self.assertEqual(1, len(provider.search("D6.0")))

    def test_list_documents_returns_filename_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index = Path(tmpdir)
            (index / "avc.md").write_text(
                "# AVC Manual\n\nSource: manuals/AVC使用手册.pdf\n\n控制策略说明",
                encoding="utf-8",
            )
            (index / "agc.md").write_text(
                "# AGC Manual\n\nSource: manuals/AGC使用手册.pdf\n\n有功控制说明",
                encoding="utf-8",
            )

            results = search_index(index, "控制", max_results=1, max_result_chars=80)
            listed = LocalDocumentProvider(
                document_settings(
                    merged_config(
                        {
                            "llm": {"api_key": "llm-key"},
                            "search": {"enabled": False},
                            "documents": {"enabled": True, "directory": str(index), "index_directory": str(index)},
                        }
                    )
                )
            ).list_documents("AVC", max_results=5)

            self.assertEqual(1, len(results))
            self.assertEqual(["AVC使用手册.pdf"], [result.title for result in listed])

    def test_search_can_be_limited_to_one_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index = Path(tmpdir)
            (index / "avc.md").write_text(
                "# AVC Manual\n\nSource: manuals/AVC使用手册.pdf\n\n控制策略 电压",
                encoding="utf-8",
            )
            (index / "agc.md").write_text(
                "# AGC Manual\n\nSource: manuals/AGC使用手册.pdf\n\n控制策略 有功",
                encoding="utf-8",
            )

            results = search_index(
                index,
                "控制策略",
                document="AVC",
                max_results=5,
                max_result_chars=80,
            )

            self.assertEqual(["AVC使用手册.pdf"], [result.title for result in results])
            self.assertIn("电压", results[0].content)

    def test_ambiguous_document_fragment_returns_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index = Path(tmpdir)
            (index / "avc-a.md").write_text(
                "# AVC A\n\nSource: manuals/AVC用户手册.pdf\n\n控制策略 A",
                encoding="utf-8",
            )
            (index / "avc-b.md").write_text(
                "# AVC B\n\nSource: manuals/AVC培训手册.pdf\n\n控制策略 B",
                encoding="utf-8",
            )

            results = search_index(
                index,
                "控制策略",
                document="AVC",
                max_results=5,
                max_result_chars=80,
            )

            self.assertEqual(["AVC用户手册.pdf", "AVC培训手册.pdf"], [result.title for result in results])
            self.assertTrue(all("Candidate document" in result.content for result in results))

    def test_search_index_limits_result_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index = Path(tmpdir)
            for name in ("a.md", "b.md", "c.md"):
                (index / name).write_text(f"# {name}\n\nSource: {name}\n\nshared keyword", encoding="utf-8")

            results = search_index(index, "shared keyword", max_results=2, max_result_chars=30)

            self.assertEqual(2, len(results))

    def test_missing_document_directory_returns_empty_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing"
            config = merged_config(
                {
                    "llm": {"api_key": "llm-key"},
                    "search": {"enabled": False},
                    "documents": {"enabled": True, "directory": str(missing)},
                }
            )
            provider = LocalDocumentProvider.from_config(config)
            assert provider is not None

            self.assertEqual([], provider.search("AVC", max_results=3))

    def test_document_settings_defaults_index_under_document_directory(self) -> None:
        config = merged_config(
            {
                "llm": {"api_key": "llm-key"},
                "search": {"enabled": False},
                "documents": {"enabled": True, "directory": "/tmp/reference"},
            }
        )

        settings = document_settings(config)

        self.assertEqual(Path("/tmp/reference/.llm_remark_index"), settings.index_directory)


if __name__ == "__main__":
    unittest.main()
