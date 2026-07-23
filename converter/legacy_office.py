"""Small, dependency-free readers for the text portions of OLE Office files."""
from __future__ import annotations

import re
import struct
from pathlib import Path


OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
END_OF_CHAIN = 0xFFFFFFFE
FREE_SECTOR = 0xFFFFFFFF


class OleFile:
    def __init__(self, data: bytes) -> None:
        if len(data) < 512 or data[:8] != OLE_MAGIC:
            raise ValueError("not an OLE compound file")
        self.data = data
        self.sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
        self.mini_sector_size = 1 << struct.unpack_from("<H", data, 32)[0]
        self.mini_stream_cutoff = struct.unpack_from("<I", data, 56)[0]
        self._fat = self._read_fat()
        self._streams: dict[str, tuple[int, int]] = {}
        self._read_directory()

    def _sector(self, number: int) -> bytes:
        start = 512 + number * self.sector_size
        end = start + self.sector_size
        if number < 0 or end > len(self.data):
            raise ValueError("invalid OLE sector")
        return self.data[start:end]

    def _chain(self, start: int, limit: int | None = None) -> list[int]:
        if start in (END_OF_CHAIN, FREE_SECTOR) or start < 0:
            return []
        result: list[int] = []
        seen: set[int] = set()
        while start not in (END_OF_CHAIN, FREE_SECTOR):
            if start in seen or start >= len(self._fat):
                raise ValueError("invalid OLE chain")
            seen.add(start)
            result.append(start)
            if limit is not None and len(result) >= limit:
                break
            start = self._fat[start]
        return result

    def _read_fat(self) -> list[int]:
        difat = list(struct.unpack_from("<109I", self.data, 76))
        first_difat = struct.unpack_from("<I", self.data, 68)[0]
        count = struct.unpack_from("<I", self.data, 72)[0]
        for sector in self._chain_from_header(first_difat, count, difat):
            values = struct.unpack("<%dI" % (self.sector_size // 4), self._sector(sector))
            difat.extend(values[:-1])
        fat_sectors = [n for n in difat if n not in (FREE_SECTOR, END_OF_CHAIN)]
        fat: list[int] = []
        for sector in fat_sectors:
            fat.extend(struct.unpack("<%dI" % (self.sector_size // 4), self._sector(sector)))
        return fat

    def _chain_from_header(self, start: int, count: int, initial: list[int]) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        while count and start not in (END_OF_CHAIN, FREE_SECTOR):
            if start in seen:
                raise ValueError("invalid OLE DIFAT chain")
            seen.add(start)
            result.append(start)
            block = self._sector(start)
            start = struct.unpack_from("<I", block, self.sector_size - 4)[0]
            count -= 1
        return result

    def _read_directory(self) -> None:
        start = struct.unpack_from("<I", self.data, 48)[0]
        raw = b"".join(self._sector(n) for n in self._chain(start))
        entries: list[tuple[str, int, int, int]] = []
        for offset in range(0, len(raw) - 127, 128):
            name_length = struct.unpack_from("<H", raw, offset + 64)[0]
            if name_length < 2 or name_length > 64:
                continue
            name = raw[offset : offset + name_length - 2].decode("utf-16le", errors="ignore")
            kind = raw[offset + 66]
            stream_start = struct.unpack_from("<I", raw, offset + 116)[0]
            stream_size = struct.unpack_from("<Q", raw, offset + 120)[0]
            entries.append((name, kind, stream_start, stream_size))
        self._root = next((entry for entry in entries if entry[1] == 5), ("", 0, END_OF_CHAIN, 0))
        self._streams = {name: (start, size) for name, kind, start, size in entries if kind == 2}

    def read(self, name: str) -> bytes:
        start, size = self._streams[name]
        if size == 0:
            return b""
        if size < self.mini_stream_cutoff:
            mini_stream = self._regular(self._root[2], self._root[3])
            parts = []
            for sector in self._mini_chain(start):
                position = sector * self.mini_sector_size
                parts.append(mini_stream[position : position + self.mini_sector_size])
            return b"".join(parts)[:size]
        return self._regular(start, size)

    def _regular(self, start: int, size: int) -> bytes:
        return b"".join(self._sector(n) for n in self._chain(start))[:size]

    def _mini_chain(self, start: int) -> list[int]:
        mini_fat_start = struct.unpack_from("<I", self.data, 60)[0]
        mini_fat_count = struct.unpack_from("<I", self.data, 64)[0]
        raw = b"".join(self._sector(n) for n in self._chain(mini_fat_start, mini_fat_count))
        mini_fat = list(struct.unpack("<%dI" % (len(raw) // 4), raw))
        result: list[int] = []
        seen: set[int] = set()
        while start not in (END_OF_CHAIN, FREE_SECTOR):
            if start in seen or start >= len(mini_fat):
                raise ValueError("invalid OLE mini stream chain")
            seen.add(start)
            result.append(start)
            start = mini_fat[start]
        return result


def extract_legacy_office(source: Path) -> str:
    ole = OleFile(source.read_bytes())
    if source.suffix.lower() == ".doc":
        return extract_doc(ole)
    if source.suffix.lower() == ".ppt":
        return extract_ppt(ole)
    raise ValueError(f"unsupported legacy Office suffix: {source.suffix}")


def extract_doc(ole: OleFile) -> str:
    word = ole.read("WordDocument")
    table_name = "1Table" if len(word) > 0x0A and struct.unpack_from("<H", word, 0x0A)[0] & 0x0200 else "0Table"
    table = ole.read(table_name) if table_name in ole._streams else b""
    candidates: list[str] = []
    for match in re.finditer(b"\x02", table):
        length = struct.unpack_from("<I", table, match.start() + 1)[0]
        begin = match.start() + 5
        if length < 4 or length > len(table) - begin or (length - 4) % 12:
            continue
        count = (length - 4) // 12
        cps = struct.unpack_from("<%dI" % (count + 1), table, begin)
        if any(cps[i] > cps[i + 1] for i in range(count)) or cps[-1] == 0:
            continue
        pieces: list[str] = []
        for index in range(count):
            pcd = begin + 4 * (count + 1) + index * 8
            fc = struct.unpack_from("<I", table, pcd + 2)[0]
            offset = fc & 0x3FFFFFFF
            chars = cps[index + 1] - cps[index]
            if fc & 0x40000000:
                raw = word[offset : offset + chars]
                pieces.append(raw.decode("cp1252", errors="replace"))
            else:
                raw = word[offset : offset + chars * 2]
                pieces.append(raw.decode("utf-16le", errors="replace"))
        text = "".join(pieces)
        if text.strip() and len(text) > (len(candidates[-1]) if candidates else 0):
            candidates.append(text)
    if candidates:
        return candidates[-1]
    return _text_fallback(word)


def extract_ppt(ole: OleFile) -> str:
    data = ole.read("PowerPoint Document")
    texts: list[str] = []
    offset = 0
    while offset + 8 <= len(data):
        record_type, length = struct.unpack_from("<HI", data, offset + 2)
        end = offset + 8 + length
        if end > len(data):
            break
        payload = data[offset + 8 : end]
        if record_type == 4000:
            texts.append(payload.decode("utf-16le", errors="replace"))
        elif record_type == 4008:
            texts.append(payload.decode("cp1252", errors="replace"))
        offset = end
    return "\n".join(texts)


def _text_fallback(data: bytes) -> str:
    utf16 = [item.decode("utf-16le", errors="replace") for item in re.findall(rb"(?:[\x20-\x7e]\x00){4,}", data)]
    ascii_text = [item.decode("cp1252") for item in re.findall(rb"[\x09\x0a\x0d\x20-\x7e]{4,}", data)]
    return "\n".join(utf16 + ascii_text)
