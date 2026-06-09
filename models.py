from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldMapping:
    note_type: str
    source_fields: list[str]
    target_field: str


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str
    provider: str
    score: float | None = None


@dataclass
class NoteProcessResult:
    note_id: int
    status: str
    message: str = ""


@dataclass
class BatchResult:
    processed: int = 0
    written: int = 0
    skipped_existing: int = 0
    skipped_unmapped: int = 0
    failed: int = 0
    cancelled: bool = False
    details: list[NoteProcessResult] = field(default_factory=list)

    def add(self, result: NoteProcessResult) -> None:
        self.processed += 1
        self.details.append(result)
        if result.status == "written":
            self.written += 1
        elif result.status == "skipped_existing":
            self.skipped_existing += 1
        elif result.status == "skipped_unmapped":
            self.skipped_unmapped += 1
        else:
            self.failed += 1

    @property
    def changes(self) -> Any:
        return _build_op_changes(note_changed=self.written > 0)

    @property
    def count(self) -> int:
        return self.written


JsonDict = dict[str, Any]


class _FallbackOpChanges:
    def __init__(self, *, note: bool = False) -> None:
        self.note = note

    def __getattr__(self, _name: str) -> bool:
        return False


def _build_op_changes(*, note_changed: bool) -> Any:
    for module_name in ("aqt.operations", "anki.collection"):
        try:
            module = __import__(module_name, fromlist=["OpChanges"])
            op_changes = getattr(module, "OpChanges")
        except Exception:
            continue

        try:
            return op_changes(note=note_changed)
        except Exception:
            try:
                changes = op_changes()
                setattr(changes, "note", note_changed)
                return changes
            except Exception:
                continue

    return _FallbackOpChanges(note=note_changed)
