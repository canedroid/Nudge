"""Mapping between frontmatter mappings and domain documents.

The mapper is the only place that knows the wire format. It is deliberately
tolerant: a missing or malformed optional field becomes a sensible default rather
than an exception, so one bad key in a hand-edited file cannot make a whole vault
unreadable. Only structurally impossible input raises.

Keys Nodify does not recognise are moved into ``extra`` and written back, which
is what keeps foreign metadata intact.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from nodify.adapters.file_index import parse_category_from_path
from nodify.domain.documents import (
    CREATED_AT_KEY,
    DOCUMENT_ID_KEY,
    DOCUMENT_TYPE_KEY,
    TAGS_KEY,
    TITLE_KEY,
    UPDATED_AT_KEY,
    Document,
    DocumentType,
    Note,
    Schedule,
    Task,
    TaskStatus,
    Timer,
    TimerKind,
    TimerStatus,
    parse_optional_iso,
    to_iso,
)
from nodify.domain.ports import DocumentFormatError

TASK_FIELDS = frozenset(
    {"status", "priority", "schedule", "due_at", "completed_at", "completion_history", "notes"}
)
TIMER_FIELDS = frozenset(
    {
        "kind",
        "starts_at",
        "due_at",
        "duration_seconds",
        "status",
        "notified_at",
        "completed_at",
        "notes",
    }
)
NOTE_FIELDS = frozenset({"category"})

#: Every key this application owns. Anything else is preserved as ``extra``.
_KNOWN_KEYS = (
    frozenset(
        {DOCUMENT_ID_KEY, DOCUMENT_TYPE_KEY, TITLE_KEY, CREATED_AT_KEY, UPDATED_AT_KEY, TAGS_KEY}
    )
    | TASK_FIELDS
    | TIMER_FIELDS
    | NOTE_FIELDS
)


def _as_str(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key, default)
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _as_tags(data: dict[str, Any]) -> list[str]:
    value = data.get(TAGS_KEY)
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item).strip()]
    return []


def _enum_or_default(enum_cls: type[Any], value: Any, default: Any) -> Any:
    """Coerce a stored string into a known enum value, falling back to ``default``.

    An unrecognised value is not an error; it degrades to the default so that a
    file written by a newer version still opens.
    """
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value.strip().lower())
        except ValueError:
            return default
    return default


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_iso_list(value: Any) -> list[datetime]:
    """Parse a list of timestamps, skipping entries that cannot be read."""
    if not isinstance(value, list):
        return []
    parsed: list[datetime] = []
    for item in value:
        moment = parse_optional_iso(item)
        if moment is not None:
            parsed.append(moment)
    return parsed


def split_known_keys(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate recognised keys from keys this application does not own."""
    known = {k: v for k, v in data.items() if k in _KNOWN_KEYS}
    extra = {k: v for k, v in data.items() if k not in _KNOWN_KEYS}
    return known, extra


def document_to_frontmatter(document: Document) -> dict[str, Any]:
    """Render a document as a frontmatter mapping.

    When the document was parsed from disk, the original mapping is updated in
    place. That keeps the user's key order and leaves comments attached to keys
    that Nodify does not own exactly where they were. Owned keys are written
    after, so a stale value inside ``extra`` can never shadow an authoritative
    one.

    A document that was constructed in memory has no source mapping, so a fresh
    dict is built from ``extra`` followed by the owned keys.
    """
    if document.frontmatter_source is not None:
        data = document.frontmatter_source
    else:
        data = dict(document.extra)

    data.update(document.frontmatter())

    if isinstance(document, Note):
        if document.category:
            data["category"] = document.category
    elif isinstance(document, Task):
        data["status"] = str(document.status)
        data["priority"] = document.priority
        data["schedule"] = str(document.schedule)
        if document.notes:
            data["notes"] = document.notes
        if document.due_at is not None:
            data["due_at"] = to_iso(document.due_at)
        if document.completed_at is not None:
            data["completed_at"] = to_iso(document.completed_at)
        if document.completion_history:
            data["completion_history"] = [to_iso(m) for m in document.completion_history]
    elif isinstance(document, Timer):
        data["kind"] = str(document.kind)
        data["status"] = str(document.status)
        if document.notes:
            data["notes"] = document.notes
        if document.starts_at is not None:
            data["starts_at"] = to_iso(document.starts_at)
        if document.due_at is not None:
            data["due_at"] = to_iso(document.due_at)
        if document.duration_seconds is not None:
            data["duration_seconds"] = document.duration_seconds
        if document.notified_at is not None:
            data["notified_at"] = to_iso(document.notified_at)
        if document.completed_at is not None:
            data["completed_at"] = to_iso(document.completed_at)

    return data


def _note_category(known: dict[str, Any], relative_path: Path | None) -> str:
    """Resolve a note's category, preferring the folder over frontmatter.

    A note at ``notes/Work/Ideas.md`` is a Work note because of where it lives.
    Frontmatter ``category`` is only a fallback, for a note that has not been
    filed into a category folder yet.
    """
    if relative_path is not None:
        from_folder = parse_category_from_path(relative_path)
        if from_folder:
            return from_folder
    return _as_str(known, "category").strip()


def _base_fields(data: dict[str, Any]) -> tuple[str, DocumentType, str, str]:
    """Extract the fields every document type shares.

    A document with no ``id`` is a hard error: without it a save would produce a
    file that could never be addressed again. Anything else is repaired.
    """
    doc_id = _as_str(data, DOCUMENT_ID_KEY).strip()
    if not doc_id:
        raise DocumentFormatError("document is missing a frontmatter 'id'")

    raw_type = _as_str(data, DOCUMENT_TYPE_KEY).strip().lower()
    try:
        doc_type = DocumentType(raw_type) if raw_type else DocumentType.NOTE
    except ValueError as exc:
        raise DocumentFormatError(f"unknown document type: {raw_type!r}") from exc

    return doc_id, doc_type, _as_str(data, TITLE_KEY).strip(), doc_type


def from_frontmatter(
    data: dict[str, Any], body: str, relative_path: Path | None = None
) -> Document:
    """Build a domain document from a frontmatter mapping and a Markdown body.

    ``relative_path`` is the document's location within the vault, when the caller
    knows it. It is optional so the mapper stays usable without a filesystem, but
    it matters for notes: the category of record is the folder the note lives in,
    not a frontmatter key, because the folder is what the user sees in Obsidian.
    """
    known, extra = split_known_keys(data)
    doc_id, doc_type, title, _ = _base_fields(known)

    created = parse_optional_iso(known.get(CREATED_AT_KEY))
    updated = parse_optional_iso(known.get(UPDATED_AT_KEY))

    if doc_type is DocumentType.NOTE:
        return Note(
            id=doc_id,
            title=title,
            category=_note_category(known, relative_path),
            body=body,
            created_at=created,
            updated_at=updated,
            tags=_as_tags(known),
            extra=extra,
            frontmatter_source=data,
        )

    if doc_type is DocumentType.TASK:
        return Task(
            id=doc_id,
            title=title,
            created_at=created,
            updated_at=updated,
            tags=_as_tags(known),
            body=body,
            extra=extra,
            frontmatter_source=data,
            notes=_as_str(known, "notes"),
            status=_enum_or_default(TaskStatus, known.get("status"), TaskStatus.OPEN),
            priority=_as_str(known, "priority", "normal") or "normal",
            schedule=_enum_or_default(Schedule, known.get("schedule"), Schedule.NOW),
            due_at=parse_optional_iso(known.get("due_at")),
            completed_at=parse_optional_iso(known.get("completed_at")),
            completion_history=_as_iso_list(known.get("completion_history")),
        )

    return Timer(
        id=doc_id,
        title=title,
        created_at=created,
        updated_at=updated,
        tags=_as_tags(known),
        body=body,
        extra=extra,
        frontmatter_source=data,
        notes=_as_str(known, "notes"),
        kind=_enum_or_default(TimerKind, known.get("kind"), TimerKind.COUNTDOWN),
        starts_at=parse_optional_iso(known.get("starts_at")),
        due_at=parse_optional_iso(known.get("due_at")),
        duration_seconds=_as_int(known.get("duration_seconds")),
        status=_enum_or_default(TimerStatus, known.get("status"), TimerStatus.SCHEDULED),
        notified_at=parse_optional_iso(known.get("notified_at")),
        completed_at=parse_optional_iso(known.get("completed_at")),
    )


def note_to_frontmatter(note: Note) -> dict[str, Any]:
    return document_to_frontmatter(note)
