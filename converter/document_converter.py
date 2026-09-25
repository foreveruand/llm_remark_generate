#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import os
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

try:
    from .legacy_office import extract_legacy_office
except ImportError:  # PyInstaller invokes this file as the script entry point.
    from legacy_office import extract_legacy_office


SUPPORTED_SUFFIXES = {".doc", ".docx", ".html", ".htm", ".md", ".pdf", ".ppt", ".pptx", ".txt"}
TEXT_SUFFIXES = {".md", ".txt"}
OOXML_SUFFIXES = {".docx", ".pptx"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert local documents to Markdown for LLM Remark Generator.")
    parser.add_argument("--input", required=True, help="Document file or directory to scan.")
    parser.add_argument("--output", required=True, help="Directory for converted Markdown/text files.")
    args = parser.parse_args(argv)

    source = Path(args.input).expanduser()
    output_dir = Path(args.output).expanduser()
    if not source.exists():
        print(f"input path does not exist: {source}", file=sys.stderr)
        return 2
    if source.is_file() and source.suffix.lower() not in SUPPORTED_SUFFIXES:
        print(f"unsupported document type: {source}", file=sys.stderr)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)

    root = source.parent if source.is_file() else source
    failures = 0
    for document in iter_documents(source):
        try:
            markdown = convert_document(root, document)
            target = output_dir / output_filename(root, document)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        except Exception as exc:
            failures += 1
            print(f"failed to convert {document}: {exc}", file=sys.stderr)

    return 1 if failures else 0


def iter_documents(source: Path) -> list[Path]:
    if source.is_file():
        return [source] if source.suffix.lower() in SUPPORTED_SUFFIXES else []
    files: list[Path] = []
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if ".llm_remark_index" in path.parts:
            continue
        if path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda item: str(item).lower())


def convert_document(root: Path, source: Path) -> str:
    suffix = source.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text = source.read_text(encoding="utf-8", errors="replace")
    elif suffix in OOXML_SUFFIXES:
        text = extract_ooxml_text(source)
    elif suffix in {".html", ".htm"}:
        text = extract_html_text(source)
    elif suffix == ".pdf":
        text = extract_pdf_text(source)
    elif suffix in {".doc", ".ppt"}:
        text = extract_legacy_office_text(source)
    else:
        text = extract_binary_text(source)

    relative = source_key(root, source)
    body = format_markdown(remove_repeated_short_lines(clean_text(preserve_markdown_tables(text))))
    return "\n".join(
        [
            f"# {source.name}",
            "",
            f"Source: {relative}",
            "",
            body,
            "",
        ]
    )


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def extract_ooxml_text(source: Path) -> str:
    with zipfile.ZipFile(source) as archive:
        names = sorted(name for name in archive.namelist() if name.endswith(".xml"))
        texts: list[str] = []
        document = "word/document.xml"
        if document in names:
            texts.append(_extract_word_body(archive.read(document)))
        slides = [
            name
            for name in names
            if name.startswith(("ppt/slides/", "ppt/notesSlides/"))
        ]
        for name in slides:
            texts.append(_extract_slide_text(archive.read(name)))
    return "\n\n".join(part for part in texts if part.strip())


_WORD_HEADING_STYLES = {
    "1": 1,
    "2": 2,
    "3": 3,
    "Heading1": 1,
    "Heading2": 2,
    "Heading3": 3,
    "标题1": 1,
    "标题2": 2,
    "标题3": 3,
}


def _extract_word_body(data: bytes) -> str:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ""
    body = root.find(f"{{{WORD_NS}}}body")
    if body is None:
        return _joined_text_nodes(root)
    blocks: list[str] = []
    for child in body:
        tag = _local_name(child.tag)
        if tag == "p":
            text = _word_paragraph_markdown(child)
            if text:
                blocks.append(text)
        elif tag == "tbl":
            table = _word_table_markdown(child)
            if table:
                blocks.append(table)
    return "\n\n".join(blocks)


def _word_paragraph_markdown(paragraph: ElementTree.Element) -> str:
    text = _word_paragraph_text(paragraph)
    if not text:
        return ""
    level = _word_heading_level(paragraph)
    if level:
        return f"{'#' * (level + 1)} {text}"
    return text


def _word_heading_level(paragraph: ElementTree.Element) -> int:
    properties = paragraph.find(f"{{{WORD_NS}}}pPr")
    if properties is None:
        return 0
    outline = properties.find(f"{{{WORD_NS}}}outlineLvl")
    if outline is not None:
        raw = outline.attrib.get(f"{{{WORD_NS}}}val", "")
        if raw.isdigit():
            return min(int(raw) + 1, 6)
    style = properties.find(f"{{{WORD_NS}}}pStyle")
    if style is None:
        return 0
    return _WORD_HEADING_STYLES.get(style.attrib.get(f"{{{WORD_NS}}}val", ""), 0)


def _word_paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        tag = _local_name(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append(" ")
        elif tag == "br":
            parts.append("\n")
    return "".join(parts).strip()


def _word_table_markdown(table: ElementTree.Element) -> str:
    rows: list[list[str]] = []
    for row in table.findall(f"{{{WORD_NS}}}tr"):
        cells = [_word_cell_text(cell) for cell in row.findall(f"{{{WORD_NS}}}tc")]
        if any(cells):
            rows.append(cells)
    return markdown_table(rows)


def _word_cell_text(cell: ElementTree.Element) -> str:
    paragraphs = [
        _word_paragraph_text(paragraph)
        for paragraph in cell.findall(f"{{{WORD_NS}}}p")
    ]
    return " ".join(paragraph for paragraph in paragraphs if paragraph)


def _extract_slide_text(data: bytes) -> str:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ""
    table_nodes = list(root.iter(f"{{{DRAWING_NS}}}tbl"))
    if not table_nodes:
        return _joined_text_nodes(root)
    parent_map = {child: parent for parent in root.iter() for child in parent}
    paragraphs = []
    for paragraph in root.iter(f"{{{DRAWING_NS}}}p"):
        if _has_ancestor(paragraph, parent_map, f"{{{DRAWING_NS}}}tc"):
            continue
        text = "".join(node.text or "" for node in paragraph.iter(f"{{{DRAWING_NS}}}t")).strip()
        if text:
            paragraphs.append(text)
    tables = [_slide_table_markdown(table) for table in table_nodes]
    return "\n\n".join(part for part in [*paragraphs, *tables] if part)


def _slide_table_markdown(table: ElementTree.Element) -> str:
    rows: list[list[str]] = []
    for row in table.findall(f"{{{DRAWING_NS}}}tr"):
        cells = []
        for cell in row.findall(f"{{{DRAWING_NS}}}tc"):
            text = "".join(node.text or "" for node in cell.iter(f"{{{DRAWING_NS}}}t")).strip()
            cells.append(re.sub(r"\s+", " ", text))
        if any(cells):
            rows.append(cells)
    return markdown_table(rows)


def _has_ancestor(element: ElementTree.Element, parent_map: dict, tag: str) -> bool:
    current = parent_map.get(element)
    while current is not None:
        if current.tag == tag:
            return True
        current = parent_map.get(current)
    return False


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    escaped = [[_escape_table_cell(cell) for cell in row] for row in normalized]
    header = "| " + " | ".join(escaped[0]) + " |"
    divider = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = ["| " + " | ".join(row) + " |" for row in escaped[1:]]
    return "\n".join([header, divider, *body])


def _escape_table_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def _joined_text_nodes(root: ElementTree.Element) -> str:
    parts = [element.text.strip() for element in root.iter() if element.text and element.text.strip()]
    return "\n".join(parts)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_legacy_office_text(source: Path) -> str:
    """Extract text from the OLE2 Word/PowerPoint formats."""
    try:
        return extract_legacy_office(source)
    except (KeyError, ValueError, IndexError):
        return extract_binary_text(source)


def extract_html_text(source: Path) -> str:
    raw = source.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return html.unescape(raw)


def extract_pdf_text(source: Path) -> str:
    text = extract_pdf_text_with_pymupdf(source)
    if text.strip():
        return text
    return extract_pdf_text_fallback(source)


def extract_pdf_text_with_pymupdf(source: Path) -> str:
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            return ""

    pages: list[list[tuple[float, str]]] = []
    with pymupdf.open(source) as document:
        for page in document:
            pages.append(_pdf_page_lines(page))
    return _join_pdf_pages(pages)


def _pdf_page_text(page: object) -> str:
    return _join_pdf_lines(_pdf_page_lines(page))


def _pdf_page_lines(page: object) -> list[tuple[float, str]]:
    data = page.get_text("dict")  # type: ignore[attr-defined]
    height = float(page.rect.height)  # type: ignore[attr-defined]
    return _pdf_body_lines(data.get("blocks", []), height)


def _pdf_body_lines(blocks: list[dict], page_height: float) -> list[tuple[float, str]]:
    rows: dict[float, list[tuple[float, str]]] = {}
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            direction = line.get("dir") or (1.0, 0.0)
            if not _is_horizontal(direction):
                continue
            spans = line.get("spans", [])
            text = "".join(span.get("text", "") for span in spans).strip()
            if not text or _is_pdf_page_number(text):
                continue
            x0, y0, _, y1 = (float(value) for value in line["bbox"])
            if y0 < page_height * 0.045 or y1 > page_height * 0.95:
                continue
            key = round(y0, 1)
            rows.setdefault(key, []).append((x0, text))
    ordered: list[tuple[float, str]] = []
    for key in sorted(rows):
        pieces = sorted(rows[key])
        ordered.append((pieces[0][0], "".join(text for _, text in pieces)))
    return ordered


def _is_horizontal(direction: tuple[float, float]) -> bool:
    return abs(direction[0] - 1.0) <= 0.05 and abs(direction[1]) <= 0.05


_PAGE_NUMBER_RE = re.compile(r"^[^\w\u4e00-\u9fff]*\d{1,4}[^\w\u4e00-\u9fff]*$")


def _is_pdf_page_number(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return len(compact) <= 12 and bool(_PAGE_NUMBER_RE.fullmatch(compact))


def _join_pdf_pages(pages: list[list[tuple[float, str]]]) -> str:
    paragraphs: list[str] = []
    current = ""
    current_indent: float | None = None
    for lines in pages:
        body_indent = _body_indent(lines)
        for indent, line in lines:
            if not current:
                current, current_indent = line, indent
                continue
            if _starts_new_pdf_paragraph(current, current_indent, line, indent, body_indent):
                paragraphs.append(current)
                current, current_indent = line, indent
            else:
                current = _append_pdf_line(current, line)
                if current_indent is None or indent < current_indent:
                    current_indent = indent
    if current:
        paragraphs.append(current)
    return "\n\n".join(paragraphs)


def _join_pdf_lines(lines: list[tuple[float, str]]) -> str:
    return _join_pdf_pages([lines])


def _body_indent(lines: list[tuple[float, str]]) -> float:
    indents = [indent for indent, _ in lines]
    if not indents:
        return 0.0
    return Counter(round(indent, 0) for indent in indents).most_common(1)[0][0]


def _starts_new_pdf_paragraph(
    current: str,
    current_indent: float | None,
    line: str,
    indent: float,
    body_indent: float,
) -> bool:
    if current.endswith(("-", "—")):
        return False
    if current_indent is None or indent - current_indent < 12:
        return False
    return abs(indent - body_indent) <= 8 or current[-1:] in ".!?。！？；;:"


def _append_pdf_line(current: str, line: str) -> str:
    if current.endswith("-") and line[:1].isascii() and line[:1].isalpha():
        return current[:-1] + line
    if current[-1:].isascii() and current[-1:].isalnum() and line[:1].isascii() and line[:1].isalnum():
        return current + " " + line
    return current + line


def extract_pdf_text_fallback(source: Path) -> str:
    data = source.read_bytes()
    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.DOTALL):
        stream = match.group(1)
        chunks.extend(decode_pdf_literals(stream))
        chunks.extend(decode_pdf_hex_strings(stream))
    if chunks:
        return "\n".join(chunks)
    return extract_binary_text(source)


def decode_pdf_literals(stream: bytes) -> list[str]:
    texts: list[str] = []
    for match in re.finditer(rb"\((.*?)\)", stream, re.DOTALL):
        raw = match.group(1)
        if len(raw) > 5000:
            continue
        text = raw.replace(rb"\\n", b"\n").replace(rb"\\r", b"\n").replace(rb"\\t", b"\t")
        text = text.replace(rb"\\(", b"(").replace(rb"\\)", b")").replace(rb"\\\\", b"\\")
        decoded = decode_bytes(text)
        if decoded.strip():
            texts.append(decoded)
    return texts


def decode_pdf_hex_strings(stream: bytes) -> list[str]:
    texts: list[str] = []
    for match in re.finditer(rb"<([0-9A-Fa-f\s]{4,})>", stream):
        compact = re.sub(rb"\s+", b"", match.group(1))
        if len(compact) % 2:
            compact += b"0"
        try:
            raw = bytes.fromhex(compact.decode("ascii"))
        except ValueError:
            continue
        decoded = decode_bytes(raw)
        if decoded.strip():
            texts.append(decoded)
    return texts


def extract_binary_text(source: Path) -> str:
    data = source.read_bytes()
    strings = re.findall(rb"[\x09\x0a\x0d\x20-\x7e]{4,}", data)
    decoded = [decode_bytes(item) for item in strings]
    utf16 = re.findall(rb"(?:[\x20-\x7e]\x00){4,}", data)
    decoded.extend(item.decode("utf-16le", errors="ignore") for item in utf16)
    return "\n".join(item for item in decoded if item.strip())


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "utf-16-be", "utf-16-le", "gb18030", "latin-1"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _looks_like_text(text):
            return text
    return data.decode("utf-8", errors="replace")


def _looks_like_text(text: str) -> bool:
    if not text:
        return False
    printable = sum(1 for char in text if char.isprintable() or char.isspace())
    return printable / max(len(text), 1) > 0.75


def preserve_markdown_tables(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line.startswith("| ") and line.endswith(" |") and set(line.replace("|", "").replace(" ", "")) <= {"-"}:
            lines.append(line.replace(" ", "\u00a0"))
        else:
            lines.append(line)
    return "\n".join(lines)


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.replace("\u00a0", " ").strip()


def remove_repeated_short_lines(text: str) -> str:
    raw_lines = text.splitlines()
    stripped = [line.strip() for line in raw_lines]
    counts = Counter(line for line in stripped if 3 <= len(line) <= 80 and not _is_markdown_table_line(line))
    filtered = [
        line
        for line in stripped
        if _is_markdown_table_line(line) or not (counts.get(line, 0) >= 3 and len(set(line)) > 1)
    ]
    return "\n".join(filtered).strip()


def _is_markdown_table_line(line: str) -> bool:
    return line.startswith("|") and line.endswith("|")


_TOC_RE = re.compile(r"^(.{1,80}?)(?:[ .．。…]{2,}|…+)\s*(\d{1,4})\s*$")


def format_markdown(text: str) -> str:
    formatted: list[str] = []
    for block in re.split(r"\n{2,}", text):
        stripped = block.strip()
        if not stripped:
            continue
        if _is_markdown_table_line(stripped.splitlines()[0]):
            formatted.append(stripped)
            continue
        formatted.append(_format_prose_block(stripped))
    return "\n\n".join(part for part in formatted if part).strip()


def _format_prose_block(block: str) -> str:
    line = re.sub(r"[ \t]+", " ", " ".join(block.splitlines())).strip()
    if line.startswith("#"):
        return line
    toc = _TOC_RE.match(line)
    if toc and _looks_like_toc_title(toc.group(1)):
        return f"- {toc.group(1).strip()} ({toc.group(2)})"
    return line


def _looks_like_toc_title(title: str) -> bool:
    compact = title.strip(" .．。…")
    return bool(compact) and not compact[-1].isdigit() and len(compact) <= 60


def output_filename(root: Path, source: Path) -> str:
    relative = source_key(root, source)
    safe = relative.replace(os.sep, "__").replace("/", "__")
    return f"{safe}.md"


def source_key(root: Path, source: Path) -> str:
    try:
        return str(source.relative_to(root))
    except ValueError:
        return str(source)


if __name__ == "__main__":
    raise SystemExit(main())
