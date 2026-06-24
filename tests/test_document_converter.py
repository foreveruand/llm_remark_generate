from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ankiplugin.converter.document_converter import convert_document, extract_pdf_text_fallback


class DocumentConverterTest(unittest.TestCase):
    def test_converts_plain_markdown_with_source_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "guide.md"
            source.write_text("AVC control strategy", encoding="utf-8")

            markdown = convert_document(root, source)

            self.assertIn("# guide.md", markdown)
            self.assertIn("Source: guide.md", markdown)
            self.assertIn("AVC control strategy", markdown)

    def test_converts_docx_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "manual.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                        "<w:body><w:p><w:r><w:t>中文控制策略</w:t></w:r></w:p></w:body>"
                        "</w:document>"
                    ),
                )

            markdown = convert_document(root, source)

            self.assertIn("中文控制策略", markdown)

    def test_pdf_fallback_extracts_literal_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "sample.pdf"
            source.write_bytes(b"stream\nBT (fallback text) Tj ET\nendstream")

            text = extract_pdf_text_fallback(source)

            self.assertIn("fallback text", text)


if __name__ == "__main__":
    unittest.main()
