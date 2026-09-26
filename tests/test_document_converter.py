from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
import struct
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llm_remark_generate.converter.document_converter import (
    convert_document,
    extract_legacy_office_text,
    extract_pdf_text_fallback,
    iter_documents,
)
from llm_remark_generate.converter.legacy_office import OleFile


def ole_file(streams: dict[str, bytes], mini: bool = False) -> bytes:
    """Build the small subset of OLE needed by these extraction tests."""
    sector_size = 512
    allocations: dict[str, tuple[int, int]] = {}
    sectors: list[bytes] = []
    if mini:
        mini_data = b"".join(streams.values())
        mini_count = (len(mini_data) + 63) // 64
        mini_fat_start = 1
        root_start = 2
        directory_start = root_start
        fat_start = directory_start + 8
        sectors.append(mini_data.ljust(512, b"\0"))
        mini_fat = bytearray(512)
        cursor = 0
        for name, value in streams.items():
            count = (len(value) + 63) // 64
            allocations[name] = (cursor, len(value))
            for index in range(count):
                struct.pack_into("<I", mini_fat, (cursor + index) * 4, cursor + index + 1 if index + 1 < count else 0xFFFFFFFE)
            cursor += count
        sectors.append(bytes(mini_fat))
        sectors.extend([b"\0" * 512] * 8)
    else:
        directory_start = 0
        sectors.append(b"\0" * 512)
        cursor = 1
        for name, value in streams.items():
            count = (len(value) + 511) // 512
            allocations[name] = (cursor, len(value))
            sectors.extend([value[i : i + 512].ljust(512, b"\0") for i in range(0, count * 512, 512)])
            cursor += count
        fat_start = cursor
    entries = [("Root Entry", 5, 0 if mini else 0xFFFFFFFE, len(streams) * 64 if mini else 0)]
    entries.extend((name, 2, start, size) for name, (start, size) in allocations.items())
    directory = bytearray(512 * 8)
    for index, (name, kind, start, size) in enumerate(entries):
        offset = index * 128
        encoded = (name + "\0").encode("utf-16le")
        directory[offset : offset + len(encoded)] = encoded
        struct.pack_into("<H", directory, offset + 64, len(encoded))
        directory[offset + 66] = kind
        struct.pack_into("<I", directory, offset + 116, start)
        struct.pack_into("<Q", directory, offset + 120, size)
    if mini:
        sectors[root_start : root_start + 8] = [directory[i : i + 512] for i in range(0, len(directory), 512)]
    else:
        sectors[directory_start] = directory[:512]
    fat = [0xFFFFFFFF] * (fat_start + 1)
    if mini:
        fat[0] = 0xFFFFFFFE
        fat[1] = 0xFFFFFFFE
        for index in range(8):
            fat[directory_start + index] = directory_start + index + 1 if index < 7 else 0xFFFFFFFE
    else:
        fat[directory_start] = 0xFFFFFFFE
        for name, (start, size) in allocations.items():
            count = (size + 511) // 512
            for index in range(count):
                fat[start + index] = start + index + 1 if index + 1 < count else 0xFFFFFFFE
    fat[fat_start] = 0xFFFFFFFD
    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HH", header, 30, 9, 6)
    struct.pack_into("<I", header, 44, 0)
    struct.pack_into("<I", header, 48, directory_start)
    struct.pack_into("<I", header, 60, 1 if mini else 0xFFFFFFFE)
    struct.pack_into("<I", header, 64, 1 if mini else 0)
    struct.pack_into("<I", header, 76, 1)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)
    struct.pack_into("<I", header, 72, 0)
    struct.pack_into("<I", header, 76, 1)
    for offset in range(76, 512, 4):
        struct.pack_into("<I", header, offset, 0xFFFFFFFF)
    struct.pack_into("<I", header, 76, fat_start)
    return bytes(header) + b"".join(sectors) + struct.pack("<%dI" % 128, *(fat + [0xFFFFFFFF] * (128 - len(fat))))


class FakeTable:
    def __init__(self, bbox: tuple[float, float, float, float], rows: list[list[str | None]]) -> None:
        self.bbox = bbox
        self._rows = rows

    def extract(self) -> list[list[str | None]]:
        return self._rows


class FakeFinder:
    def __init__(self, tables: list[FakeTable]) -> None:
        self.tables = tables


def text_block(entries: list[tuple[float, float, float, float, str]]) -> dict:
    return {
        "type": 0,
        "lines": [
            {"dir": (1, 0), "bbox": (x0, y0, x1, y1), "spans": [{"text": text}]}
            for x0, y0, x1, y1, text in entries
        ],
    }


def fake_pdf_page(blocks: list[dict], height: float = 842, find_tables=None) -> object:
    class FakePage:
        rect = type("Rect", (), {"height": height})()

        def get_text(self, mode: str) -> dict:
            return {"blocks": blocks}

    page = FakePage()
    if find_tables is not None:
        page.find_tables = find_tables  # type: ignore[attr-defined]
    return page


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

    def test_converts_docx_tables_to_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "report.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                        "<w:body>"
                        "<w:p><w:r><w:t>Status</w:t></w:r></w:p>"
                        "<w:tbl><w:tr>"
                        "<w:tc><w:p><w:r><w:t>ID</w:t></w:r></w:p></w:tc>"
                        "<w:tc><w:p><w:r><w:t>Area</w:t></w:r></w:p></w:tc>"
                        "<w:tc><w:p><w:r><w:t>State</w:t></w:r></w:p></w:tc>"
                        "</w:tr><w:tr>"
                        "<w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>"
                        "<w:tc><w:p><w:r><w:t>North</w:t></w:r></w:p></w:tc>"
                        "<w:tc><w:p><w:r><w:t>Ready</w:t></w:r></w:p></w:tc>"
                        "</w:tr></w:tbl>"
                        "</w:body></w:document>"
                    ),
                )

            markdown = convert_document(root, source)

            self.assertIn("Status", markdown)
            self.assertIn("| ID | Area | State |", markdown)
            self.assertIn("| --- | --- | --- |", markdown)
            self.assertIn("| 1 | North | Ready |", markdown)

    def test_pdf_layout_drops_watermark_and_joins_wrapped_lines(self) -> None:
        blocks = [
            {
                "type": 0,
                "lines": [
                    {
                        "dir": (0.94, -0.34),
                        "bbox": (0, 200, 500, 220),
                        "spans": [{"text": "CONFIDENTIAL SAMPLE 2026-04-24 15:28:20"}],
                    },
                    {
                        "dir": (1, 0),
                        "bbox": (79, 120, 520, 140),
                        "spans": [{"text": "Wrapped body text continues"}],
                    },
                    {
                        "dir": (1, 0),
                        "bbox": (79, 148, 400, 168),
                        "spans": [{"text": "on the next visual line."}],
                    },
                    {
                        "dir": (1, 0),
                        "bbox": (450, 760, 510, 778),
                        "spans": [{"text": "— 1 —"}],
                    },
                ],
            }
        ]

        class FakePage:
            rect = type("Rect", (), {"height": 842})()

            def get_text(self, mode: str):
                self.mode = mode
                return {"blocks": blocks}

        from llm_remark_generate.converter.document_converter import _pdf_page_text

        text = _pdf_page_text(FakePage())

        self.assertEqual("Wrapped body text continues on the next visual line.", text)
        self.assertNotIn("CONFIDENTIAL", text)
        self.assertNotIn("— 1 —", text)

    def test_pdf_keeps_clause_numbers_separate_across_pages(self) -> None:
        from llm_remark_generate.converter.document_converter import _join_pdf_pages

        pages = [
            [(72.0, "Body text is indented and wraps across the page boundary with-")],
            [(40.0, "out losing the trailing word."), (72.0, "The next paragraph starts again.")],
        ]

        text = _join_pdf_pages(pages)

        self.assertIn("without losing the trailing word.", text)
        self.assertIn("\n\nThe next paragraph starts again.", text)

    def test_pdf_fallback_extracts_literal_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "sample.pdf"
            source.write_bytes(b"stream\nBT (fallback text) Tj ET\nendstream")

            text = extract_pdf_text_fallback(source)

            self.assertIn("fallback text", text)

    def test_pdf_page_emits_markdown_table_with_br_cells(self) -> None:
        from llm_remark_generate.converter.document_converter import _pdf_page_text

        blocks = [
            text_block(
                [
                    (72, 100, 400, 118, "Heading above the table"),
                    (80, 200, 300, 218, "cell text inside table"),
                ]
            )
        ]
        finder = FakeFinder(
            [
                FakeTable(
                    (64, 137, 777, 505),
                    [
                        ["Header A", "Header B", "Header C"],
                        ["Row 1", "1.a\n2.b", "3.c"],
                    ],
                )
            ]
        )

        text = _pdf_page_text(fake_pdf_page(blocks, find_tables=lambda: finder))

        self.assertIn("| Header A | Header B | Header C |", text)
        self.assertIn("| --- | --- | --- |", text)
        self.assertIn("| Row 1 | 1.a<br>2.b | 3.c |", text)
        self.assertIn("Heading above the table", text)
        self.assertNotIn("cell text inside table", text)

    def test_pdf_table_cell_normalizer_joins_lines_with_br(self) -> None:
        from llm_remark_generate.converter.document_converter import (
            _pdf_table_cell_text,
            markdown_table,
        )

        self.assertEqual("", _pdf_table_cell_text(None))
        self.assertEqual(
            "1.a<br>2.b<br>3.c",
            _pdf_table_cell_text("1.a\n  2.b\n\n3.c"),
        )
        self.assertIn("a\\|b", markdown_table([["a|b", "plain"]]))

    def test_pdf_page_orders_table_between_prose(self) -> None:
        from llm_remark_generate.converter.document_converter import _pdf_page_text

        blocks = [
            text_block(
                [
                    (72, 90, 400, 108, "Above the table"),
                    (72, 700, 400, 718, "Below the table"),
                ]
            )
        ]
        finder = FakeFinder([FakeTable((64, 300, 777, 500), [["H1", "H2"], ["A", "B"]])])

        text = _pdf_page_text(fake_pdf_page(blocks, find_tables=lambda: finder))

        self.assertLess(text.index("Above the table"), text.index("| H1 | H2 |"))
        self.assertLess(text.index("| H1 | H2 |"), text.index("Below the table"))

    def test_join_pdf_pages_keeps_table_atomic(self) -> None:
        from llm_remark_generate.converter.document_converter import _join_pdf_pages

        table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
        text = _join_pdf_pages(
            [[(72.0, "Intro sentence"), (72.0, table), (72.0, "Outro sentence")]]
        )

        self.assertIn("Intro sentence\n\n| a | b |", text)
        self.assertIn("| 1 | 2 |\n\nOutro sentence", text)

    def test_pdf_page_without_find_tables_falls_back_to_prose(self) -> None:
        from llm_remark_generate.converter.document_converter import _pdf_page_text

        blocks = [text_block([(79, 120, 520, 140, "Wrapped body text continues")])]

        self.assertEqual("Wrapped body text continues", _pdf_page_text(fake_pdf_page(blocks)))

    def test_pdf_page_tolerates_find_tables_error(self) -> None:
        from llm_remark_generate.converter.document_converter import _pdf_page_text

        def boom():
            raise RuntimeError("detector unavailable")

        blocks = [text_block([(79, 120, 520, 140, "Body text survives")])]

        text = _pdf_page_text(fake_pdf_page(blocks, find_tables=boom))

        self.assertEqual("Body text survives", text)

    def test_pdf_page_accepts_iterable_table_finder(self) -> None:
        from llm_remark_generate.converter.document_converter import _pdf_page_text

        class IterableFinder:
            def __iter__(self):
                return iter([FakeTable((10, 10, 200, 60), [["H1", "H2"], ["A", "B"]])])

        blocks = [text_block([(72, 100, 300, 118, "Prose line")])]

        text = _pdf_page_text(fake_pdf_page(blocks, find_tables=lambda: IterableFinder()))

        self.assertIn("| H1 | H2 |", text)
        self.assertIn("Prose line", text)

    def test_pdf_table_drops_none_and_blank_rows(self) -> None:
        from llm_remark_generate.converter.document_converter import _pdf_page_tables

        finder = FakeFinder([FakeTable((0, 0, 100, 100), [["h1", "h2"], [None, None]])])
        page = fake_pdf_page([], find_tables=lambda: finder)

        tables = _pdf_page_tables(page)

        self.assertEqual(1, len(tables))
        self.assertEqual("| h1 | h2 |\n| --- | --- |", tables[0][1])

    def test_converts_pptx_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "slides.pptx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "ppt/slides/slide1.xml",
                    (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                        "<a:t>中文演示文稿</a:t></p:sld>"
                    ),
                )

            markdown = convert_document(root, source)

            self.assertIn("中文演示文稿", markdown)

    def test_iter_documents_recurses_into_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "nested" / "deeper"
            nested.mkdir(parents=True)
            source = nested / "manual.docx"
            source.touch()
            (root / ".llm_remark_index").mkdir()
            (root / ".llm_remark_index" / "stale.pptx").touch()

            self.assertEqual([source], iter_documents(root))

    def test_iter_documents_accepts_a_single_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "manual.docx"
            source.touch()

            self.assertEqual([source], iter_documents(source))

    def test_main_converts_a_single_file(self) -> None:
        from llm_remark_generate.converter.document_converter import main

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "note.txt"
            source.write_text("单文件转换", encoding="utf-8")
            output = root / "out"

            code = main(["--input", str(source), "--output", str(output)])

            self.assertEqual(0, code)
            converted = (output / "note.txt.md").read_text(encoding="utf-8")
            self.assertIn("单文件转换", converted)
            self.assertIn("Source: note.txt", converted)

    def test_formats_headings_clause_labels_and_toc(self) -> None:
        from llm_remark_generate.converter.document_converter import format_markdown

        text = "\n\n".join(
            [
                "Chapter One ................................ 1",
                "Ordinary sentence 2 stays unchanged.",
                "| Item | Value |",
                "| --- | --- |",
                "| A | 1 |",
            ]
        )

        markdown = format_markdown(text)

        self.assertIn("- Chapter One (1)", markdown)
        self.assertIn("Ordinary sentence 2 stays unchanged.", markdown)
        self.assertIn("| --- | --- |", markdown)

    def test_legacy_doc_extracts_word_piece_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "manual.doc"
            word = bytearray(4096)
            word[512 : 512 + 8] = "中文策略".encode("utf-16le")
            table = b"test" + struct.pack("<BI2IHI", 2, 16, 0, 4, 0, 512)
            source.write_bytes(ole_file({"WordDocument": bytes(word), "0Table": table.ljust(4096, b"\0")}))

            self.assertEqual("中文策略", extract_legacy_office_text(source))

    def test_legacy_ppt_extracts_text_chars_atom(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "slides.ppt"
            payload = "中文幻灯片".encode("utf-16le")
            record = struct.pack("<HHI", 0, 4000, len(payload)) + payload
            source.write_bytes(ole_file({"PowerPoint Document": record.ljust(4096, b"\0")}))

            self.assertEqual("中文幻灯片", extract_legacy_office_text(source))

    def test_ole_reads_mini_stream(self) -> None:
        value = b"mini stream text"
        # The reader is covered independently here; the format-specific streams are regular in the tests above.
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "mini.doc"
            source.write_bytes(ole_file({"WordDocument": value}, mini=True))
            self.assertEqual(value, OleFile(source.read_bytes()).read("WordDocument"))


if __name__ == "__main__":
    unittest.main()
