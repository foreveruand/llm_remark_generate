from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import JsonDict, SearchResult


SUPPORTED_SOURCE_SUFFIXES = {
    ".doc",
    ".docx",
    ".html",
    ".htm",
    ".md",
    ".pdf",
    ".ppt",
    ".pptx",
    ".txt",
}
TEXT_INDEX_SUFFIXES = {".md", ".txt"}
INDEX_DIR_NAME = ".llm_remark_index"
MANIFEST_NAME = "manifest.json"


class DocumentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentSettings:
    enabled: bool
    directory: Path
    converter_path: Path | None
    index_directory: Path
    max_results: int
    max_result_chars: int
    max_tool_rounds: int


def document_settings(config: JsonDict) -> DocumentSettings:
    raw = config.get("documents", {})
    directory = Path(str(raw.get("directory", ""))).expanduser()
    configured_index = str(raw.get("index_directory", "")).strip()
    index_directory = Path(configured_index).expanduser() if configured_index else directory / INDEX_DIR_NAME
    converter_raw = str(raw.get("converter_path", "")).strip()
    converter_path = Path(converter_raw).expanduser() if converter_raw else None
    return DocumentSettings(
        enabled=bool(raw.get("enabled", False)),
        directory=directory,
        converter_path=converter_path,
        index_directory=index_directory,
        max_results=int(raw.get("max_results", 5)),
        max_result_chars=int(raw.get("max_result_chars", 1200)),
        max_tool_rounds=int(raw.get("max_tool_rounds", 3)),
    )


class LocalDocumentProvider:
    name = "local_documents"

    def __init__(self, settings: DocumentSettings) -> None:
        self.settings = settings

    @classmethod
    def from_config(cls, config: JsonDict) -> "LocalDocumentProvider | None":
        settings = document_settings(config)
        if not settings.enabled:
            return None
        return cls(settings)

    def prepare(self) -> None:
        if not self.settings.enabled:
            return
        if not self.settings.directory.is_dir():
            return
        self.settings.index_directory.mkdir(parents=True, exist_ok=True)
        index_plain_text_files(self.settings)

    def extract_documents(self) -> None:
        if not self.settings.directory.is_dir():
            raise DocumentError(f"document directory does not exist: {self.settings.directory}")
        self.settings.index_directory.mkdir(parents=True, exist_ok=True)
        run_converter(self.settings)
        index_plain_text_files(self.settings)

    def list_documents(self, query: str = "", *, max_results: int | None = None) -> list[SearchResult]:
        self.prepare()
        limit = max_results or self.settings.max_results
        return list_index_documents(
            self.settings.index_directory,
            query,
            max_results=limit,
            max_result_chars=self.settings.max_result_chars,
        )

    def search(
        self,
        query: str,
        *,
        document: str | None = None,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        self.prepare()
        limit = max_results or self.settings.max_results
        return search_index(
            self.settings.index_directory,
            query,
            document=document,
            max_results=limit,
            max_result_chars=self.settings.max_result_chars,
        )


def run_converter(settings: DocumentSettings) -> None:
    converter = settings.converter_path
    if converter is None:
        raise DocumentError("document converter path is not configured")
    if not converter.exists():
        raise DocumentError(f"document converter does not exist: {converter}")
    command = [
        str(converter),
        "--input",
        str(settings.directory),
        "--output",
        str(settings.index_directory),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except OSError as exc:
        raise DocumentError(f"failed to run document converter: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DocumentError("document converter timed out") from exc

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        if len(message) > 1000:
            message = f"{message[:1000]}..."
        raise DocumentError(f"document converter failed with exit code {completed.returncode}: {message}")


def index_plain_text_files(settings: DocumentSettings) -> None:
    manifest = _load_manifest(settings.index_directory / MANIFEST_NAME)
    changed = False
    for source in _iter_source_files(settings.directory):
        if _is_relative_to(source, settings.index_directory):
            continue
        if source.suffix.lower() not in TEXT_INDEX_SUFFIXES:
            continue
        key = _source_key(settings.directory, source)
        signature = _source_signature(source)
        if manifest.get(key) == signature:
            continue
        output = settings.index_directory / _index_filename(settings.directory, source, ".txt")
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        manifest[key] = signature
        changed = True
    if changed:
        _write_manifest(settings.index_directory / MANIFEST_NAME, manifest)


def search_index(
    index_directory: Path,
    query: str,
    *,
    document: str | None = None,
    max_results: int,
    max_result_chars: int,
) -> list[SearchResult]:
    if not index_directory.is_dir() or not query.strip():
        return []

    terms = _query_terms(query)
    if not terms:
        return []

    index_files = _iter_index_files(index_directory)
    if document and document.strip():
        matched = _match_index_documents(index_files, document)
        if len(matched) != 1:
            return _document_candidate_results(matched, max_results=max_results)
        index_files = matched

    candidates: list[tuple[int, SearchResult]] = []
    for path in sorted(index_files, key=lambda item: str(item).lower()):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score, snippet = _match_text(text, terms, max_result_chars=max_result_chars)
        if score <= 0:
            continue
        source = _document_name_from_index(path, text)
        candidates.append(
            (
                score,
                SearchResult(
                    title=source,
                    url=str(path),
                    content=snippet,
                    provider="local_documents",
                    score=float(score),
                ),
            )
        )

    candidates.sort(key=lambda item: (-item[0], item[1].title.lower()))
    return [result for _score, result in candidates[:max_results]]


def list_index_documents(
    index_directory: Path,
    query: str = "",
    *,
    max_results: int,
    max_result_chars: int,
) -> list[SearchResult]:
    if not index_directory.is_dir():
        return []

    terms = _query_terms(query)
    candidates: list[tuple[int, SearchResult]] = []
    for path in sorted(_iter_index_files(index_directory), key=lambda item: str(item).lower()):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        document = _document_name_from_index(path, text)
        haystack = f"{document}\n{text[: max_result_chars * 2]}".lower()
        score = 1 if not terms else sum(haystack.count(term) for term in terms)
        if terms and score <= 0:
            continue
        candidates.append(
            (
                score,
                SearchResult(
                    title=document,
                    url=str(path),
                    content=_clean_snippet(text[:max_result_chars]),
                    provider="local_documents",
                    score=float(score),
                ),
            )
        )

    candidates.sort(key=lambda item: (-item[0], item[1].title.lower()))
    return [result for _score, result in candidates[:max_results]]


def _iter_source_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files: list[Path] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        if INDEX_DIR_NAME in path.parts:
            continue
        if path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES:
            files.append(path)
    return sorted(files, key=lambda item: str(item).lower())


def _iter_index_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() in TEXT_INDEX_SUFFIXES:
            files.append(path)
    return files


def _query_terms(query: str) -> list[str]:
    words = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
    seen: set[str] = set()
    terms: list[str] = []
    for word in words:
        if len(word) < 2 or word in seen:
            continue
        seen.add(word)
        terms.append(word)
    return terms[:12]


def _match_text(text: str, terms: list[str], *, max_result_chars: int) -> tuple[int, str]:
    lowered = text.lower()
    matches = [(term, lowered.find(term)) for term in terms]
    found = [(term, index) for term, index in matches if index >= 0]
    if not found:
        return 0, ""
    first_index = min(index for _term, index in found)
    start = max(0, first_index - max_result_chars // 3)
    end = min(len(text), start + max_result_chars)
    snippet = _clean_snippet(text[start:end])
    score = sum(lowered.count(term) for term, _index in found) + len(found) * 5
    return score, snippet


def _clean_snippet(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _source_from_index_text(text: str) -> str | None:
    title: str | None = None
    for line in text.splitlines()[:8]:
        stripped = line.strip()
        if stripped.startswith("Source:"):
            return stripped.removeprefix("Source:").strip()
        if title is None and stripped.startswith("# "):
            title = stripped.removeprefix("# ").strip()
    return title


def _document_name_from_index(path: Path, text: str) -> str:
    source = _source_from_index_text(text)
    if source:
        return Path(source).name
    return path.name.removesuffix(path.suffix)


def _match_index_documents(index_files: list[Path], document: str) -> list[Path]:
    needle = document.strip().lower()
    if not needle:
        return []

    matches: list[Path] = []
    for path in index_files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        document_name = _document_name_from_index(path, text)
        source = _source_from_index_text(text) or document_name
        if needle in document_name.lower() or needle in source.lower() or needle in path.name.lower():
            matches.append(path)
    return sorted(matches, key=lambda item: str(item).lower())


def _document_candidate_results(paths: list[Path], *, max_results: int) -> list[SearchResult]:
    results: list[SearchResult] = []
    for path in paths[:max_results]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        document = _document_name_from_index(path, text)
        results.append(
            SearchResult(
                title=document,
                url=str(path),
                content=f"Multiple documents matched. Candidate document: {document}",
                provider="local_documents",
                score=0.0,
            )
        )
    return results


def _index_filename(root: Path, source: Path, suffix: str) -> str:
    relative = _source_key(root, source)
    safe = relative.replace(os.sep, "__").replace("/", "__")
    return f"{safe}{suffix}"


def _source_key(root: Path, source: Path) -> str:
    try:
        return str(source.relative_to(root))
    except ValueError:
        return str(source)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _source_signature(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
