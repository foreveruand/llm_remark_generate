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


SUPPORTED_SUFFIXES = {".doc", ".docx", ".html", ".htm", ".md", ".pdf", ".ppt", ".pptx", ".txt"}
TEXT_SUFFIXES = {".md", ".txt"}
OOXML_SUFFIXES = {".docx", ".pptx"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert local documents to Markdown for LLM Remark Generator.")
    parser.add_argument("--input", required=True, help="Document directory to scan.")
    parser.add_argument("--output", required=True, help="Directory for converted Markdown/text files.")
    args = parser.parse_args(argv)

    input_dir = Path(args.input).expanduser()
    output_dir = Path(args.output).expanduser()
    if not input_dir.is_dir():
        print(f"input directory does not exist: {input_dir}", file=sys.stderr)
        return 2
    output_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for source in iter_documents(input_dir):
        try:
            markdown = convert_document(input_dir, source)
            target = output_dir / output_filename(input_dir, source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        except Exception as exc:
            failures += 1
            print(f"failed to convert {source}: {exc}", file=sys.stderr)

    return 1 if failures else 0


def iter_documents(input_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in input_dir.rglob("*"):
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
    else:
        text = extract_binary_text(source)

    relative = source_key(root, source)
    body = remove_repeated_short_lines(clean_text(text))
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


def extract_ooxml_text(source: Path) -> str:
    with zipfile.ZipFile(source) as archive:
        names = sorted(name for name in archive.namelist() if name.endswith(".xml"))
        selected = [
            name
            for name in names
            if name.startswith(("word/", "ppt/slides/", "ppt/notesSlides/"))
        ]
        texts: list[str] = []
        for name in selected:
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            parts = [element.text for element in root.iter() if element.text and element.text.strip()]
            if parts:
                texts.append("\n".join(parts))
    return "\n\n".join(texts)


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
        import fitz
    except ImportError:
        return ""

    parts: list[str] = []
    with fitz.open(source) as document:
        for page in document:
            text = page.get_text("text", sort=True)
            if text.strip():
                parts.append(text)
    return "\n\n".join(parts)


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


def clean_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_repeated_short_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    counts = Counter(line for line in lines if 3 <= len(line) <= 80)
    filtered = [line for line in lines if not (counts.get(line, 0) >= 3 and len(set(line)) > 1)]
    return "\n".join(filtered).strip()


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
